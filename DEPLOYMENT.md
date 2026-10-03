# Deploying InspectDB Live

InspectDB runs on free or credit-covered services. Amazon DocumentDB is the only significant AWS cost.

## Architecture

```text
Browser ──HTTPS──▶ Vercel (free Hobby plan)               ← InspectDB-Frontend repo, auto-deploys on push
                     ├── React static build (global CDN)
                     └── /api/*  ──HTTPS proxy──▶ EC2 API server (Elastic IP)   ← this repo, GitHub Actions deploy
                                                   ├── caddy:   HTTPS (Let's Encrypt via sslip.io)
                                                   └── backend: FastAPI
                                                         ├──TLS:27017──▶ Amazon DocumentDB 5.0 (private, same VPC)
                                                         ├─────────────▶ Neon PostgreSQL (users / auth, free tier)
                                                         └─────────────▶ Google Gemini API (optional)
```

**Why the API is on EC2:** DocumentDB has no public endpoint. It only accepts connections from inside its own VPC, so the API must run on a server in that VPC. Free hosts like Render or Vercel can't reach it. The EC2 server is a micro instance, paid from AWS credits.

## Current live deployment

The live deployment reuses a DocumentDB cluster and server that were created in the AWS console, rather than the full CloudFormation stack described below.

| Item | Value |
|---|---|
| Region | `us-east-1` (N. Virginia) |
| DocumentDB | cluster `inspectdb`, engine 5.0, 1 × `db.t3.medium`, admin user `dbadmin` |
| API server | EC2 `inspectdb-backend` (`t3.micro`, Amazon Linux 2023), Elastic IP `34.206.218.109` |
| API URL | `https://34-206-218-109.sslip.io/api` (health: `/api/health`, docs: `/api/docs`) |
| Running schedule | stack `inspectdb-docdb-schedule` ([`deploy/docdb-schedule.yaml`](deploy/docdb-schedule.yaml)): start 9:00, stop 21:00, Mon–Fri, Asia/Kolkata |
| Deploys | GitHub Actions on every push to `main` (secrets `EC2_HOST`, `EC2_SSH_KEY`, `ENV_PRODUCTION`) |

## Cost (approximate)

| Resource | Monthly cost |
|---|---|
| DocumentDB `db.t3.medium`, weekdays 9 AM–9 PM IST only (~264 h) | ~$21 (first 30 days free on the AWS Paid plan) |
| DocumentDB storage and I/O (small project) | < $1 |
| EC2 `t3.micro` (or `t4g.micro` with the full template) + disk | ~$6–8 |
| Elastic IP (public IPv4) | ~$3.65 |
| Vercel, GitHub Actions (public repo), Neon free tier, EventBridge Scheduler | $0 |

DocumentDB is only available on the AWS **Paid plan**. The Free plan doesn't include it. Credits are still applied first after upgrading.

## Deployment steps

The steps below create everything from scratch with [`deploy/cloudformation.yaml`](deploy/cloudformation.yaml).

To **reuse an existing DocumentDB cluster** (as the live deployment does), skip step 1 and do the following instead:
- Allow port 27017 to the cluster from the API server's security group.
- Open ports 80, 443 and 22 on the API server.
- Attach an Elastic IP to the API server.
- Add the start/stop schedule:

```bash
aws cloudformation deploy --region <region> --stack-name inspectdb-docdb-schedule   --template-file deploy/docdb-schedule.yaml --capabilities CAPABILITY_IAM   --parameter-overrides ClusterIdentifier=<cluster-id>
```

### 1. Create the AWS resources (CloudFormation)

Using the AWS CLI (`aws login` first):

```bash
aws ec2 create-key-pair --region ap-south-1 --key-name inspectdb-key --key-type ed25519 \
  --query KeyMaterial --output text > ~/.ssh/inspectdb-key.pem

aws cloudformation deploy --region ap-south-1 --stack-name inspectdb \
  --template-file deploy/cloudformation.yaml --capabilities CAPABILITY_IAM \
  --parameter-overrides VpcId=<default-vpc-id> SubnetIds=<subnet-a>,<subnet-b> \
      KeyName=inspectdb-key DocDBPassword=<letters-and-digits-only>

aws cloudformation describe-stacks --region ap-south-1 --stack-name inspectdb --query "Stacks[0].Outputs"
```

You can also use the console: **CloudFormation → Create stack → Upload `deploy/cloudformation.yaml`**. Tick the IAM acknowledgement box, because the stack creates a small role that lets the scheduler start and stop DocumentDB.

The stack creates the following resources:
- the DocumentDB cluster and instance
- the EC2 API server (`t4g.micro`, Amazon Linux 2023, 10 GB disk) with an Elastic IP
- security groups: DocumentDB accepts port 27017 from the API server only
- two EventBridge Scheduler schedules: start at 9:00 and stop at 21:00, Mon–Fri, Asia/Kolkata

### 2. Fill in `.env.production`

Copy `.env.production.example` to `.env.production` (git-ignored) and set the following values from the stack outputs:

- `SITE_ADDRESS` = `HttpsSiteAddress`, for example `13-200-1-2.sslip.io`
- `DOCUMENTDB_URI` = `DocumentDBUri`
- `DOCUMENTDB_PASSWORD`, `DATABASE_URL` (Neon), `JWT_SECRET`, and optionally `GEMINI_API_KEY`

### 3. Add the GitHub Actions secrets in this repository

Go to **Settings → Secrets and variables → Actions**, or use the `gh` CLI:

```bash
gh secret set EC2_HOST --body "<AppPublicIP>"
gh secret set EC2_SSH_KEY < ~/.ssh/inspectdb-key.pem
gh secret set ENV_PRODUCTION < .env.production
```

Every push to `main` now runs the tests and then deploys to EC2. The workflow is [`.github/workflows/deploy.yml`](.github/workflows/deploy.yml). On the first run it installs Docker and adds swap, then builds the image and starts it. You can also start a run by hand from **Actions → Test and deploy API → Run workflow**.

Check that it worked: open `https://<HttpsSiteAddress>/api/health`. It should show `"type": "Amazon DocumentDB", "status": "connected"`.

### 4. Deploy the frontend on Vercel

1. In the [InspectDB-Frontend](https://github.com/suchir7/InspectDB-Frontend) repo, set the `/api` rewrite in `vercel.json` to `https://<HttpsSiteAddress>/api/:path*`, then push.
2. On https://vercel.com, sign in with GitHub, choose **Add New → Project**, import `InspectDB-Frontend`, and click **Deploy**. Vercel detects Vite automatically, and no environment variables are needed.

### 5. Optional: load sample reports into your account

Register in the app first, then run:

```bash
ssh -i ~/.ssh/inspectdb-key.pem ec2-user@<AppPublicIP> \
  "cd ~/inspectdb && sudo docker compose --env-file .env.production exec -T backend python scripts/seed_mongodb.py --user-email you@example.com"
```

## Day-to-day operations

| Task | How |
|---|---|
| Deploy a backend change | Push to `main` |
| Deploy a frontend change | Push to `main` in the frontend repo |
| Change secrets/config | Edit `.env.production`, run `gh secret set ENV_PRODUCTION < .env.production`, then re-run the workflow |
| Use DocumentDB outside its hours | **DocumentDB → Clusters → Actions → Start**. The scheduled stop still runs at 21:00. |
| Change the schedule | Update the stack with new `DocDBStartSchedule` / `DocDBStopSchedule` parameters, or set `EnableDocDBSchedule=false` |
| View API logs | `ssh -i ~/.ssh/inspectdb-key.pem ec2-user@<AppPublicIP> "cd ~/inspectdb && sudo docker compose --env-file .env.production logs --tail 100"` |
| Remove everything | `aws cloudformation delete-stack --region ap-south-1 --stack-name inspectdb`. A final DocumentDB snapshot is kept; delete it under **DocumentDB → Snapshots** if not needed. |

## Troubleshooting

| Symptom | Fix |
|---|---|
| App says "DocumentDB Paused / Offline" | Expected outside weekdays 9 AM–9 PM IST. Start the cluster manually if needed. |
| GitHub Actions deploy fails at SSH | Check the `EC2_HOST`/`EC2_SSH_KEY` secrets and that the security group allows port 22. |
| Health shows `"status": "unreachable"` during running hours | The cluster is still starting (it takes ~5 min), or `DOCUMENTDB_URI`/`DOCUMENTDB_PASSWORD` is wrong. Fix it, update the `ENV_PRODUCTION` secret, and re-run the workflow. |
| HTTPS / Vercel `/api` calls fail | `SITE_ADDRESS` must match `HttpsSiteAddress`, and ports 80 and 443 must be open. Let's Encrypt may rate-limit shared sslip.io names; using your own domain avoids this. |
| Login or registration fails | `DATABASE_URL` (Neon) is missing or wrong in `ENV_PRODUCTION`. |
