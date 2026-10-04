# Project brief verification

**Project:** Amazon DocumentDB for a Document-Oriented Application (24CC3014-P070)
**Run on:** 4 October 2026 against the live deployment in AWS `us-east-1`
**Database:** Amazon DocumentDB 5.0, cluster `inspectdb` (1 × `db.t3.medium`)
**Result:** 14 of 14 checks passed

The checks are automated in [`scripts/verify_documentdb.py`](scripts/verify_documentdb.py):

- **Database checks** run on the API server against DocumentDB. They use a temporary collection that is dropped afterwards, so no application data is read or changed.
- **Cost checks** read AWS with the same read-only calls as the Cost Monitoring page.

To re-run them, see [DEPLOYMENT.md](DEPLOYMENT.md#verifying-the-project-brief).

## Use case 1: Store variable-schema inspection reports

| Check | Result | Evidence |
|---|---|---|
| Documents with different shapes are stored and read back unchanged | ✅ Pass | 3 documents with 3 distinct `dynamic_attributes` shapes (electrical telemetry, fire-safety arrays, HVAC metrics plus custom fields); the round trip was identical |

## Use case 2: Query nested document structures

| Check | Result | Evidence |
|---|---|---|
| Dot-notation query four levels deep | ✅ Pass | `dynamic_attributes.electrical_telemetry.phases.phase_a.voltage_kv > 13` → 1 match (expected 1) |
| `$elemMatch` keeps both conditions on the same array element | ✅ Pass | With `$elemMatch`: 1 match (correct). Without it: 2 matches, a false match where the conditions hit different findings |
| Query an array inside an array | ✅ Pass | `findings.issues.status = 'open'` → 2 matches (expected 2) |
| Aggregation over nested findings | ✅ Pass | `$unwind` + `$group` → `{critical: 2, high: 1, low: 1}` |
| A multikey index on a nested array field serves the query | ✅ Pass | The index on `findings.severity` produces an `IXSCAN`. On 3 documents the planner prefers a collection scan, which is expected |

## Bottleneck 1: MongoDB feature compatibility gaps break the driver

| Check | Result | Evidence |
|---|---|---|
| Stock TLS settings fail; the Amazon CA bundle connects | ✅ Pass | Without `global-bundle.pem`: `certificate verify failed`. With it: ping ok |
| `$where` (JavaScript) is caught before reaching the database | ✅ Pass | Analyzer: `INCOMPATIBLE`. DocumentDB itself rejects it: *Field '$where' is currently not supported* |
| `$elemMatch` inside `$all` is rewritten automatically | ✅ Pass | DocumentDB rejects the original (*Feature not supported: $elemMatch in $all*). The analyzer's `$and` rewrite runs and returns 1 match (expected 1) |
| Concurrent index builds are retried instead of failing | ✅ Pass | Parallel builds hit DocumentDB's one-build-at-a-time limit (error 40333). The app's `ensure_indexes` retried and created every index |

Note: DocumentDB 5.0 accepts retryable writes. Versions 3.6 and 4.0 do not, so the app keeps `retryWrites=false`, which works on every engine version.

## Bottleneck 2: Cluster cost is high for a small workload

| Check | Result | Evidence |
|---|---|---|
| Cluster stops automatically outside working hours | ✅ Pass | EventBridge Scheduler: Mon–Fri 09:00–21:00 Asia/Kolkata → 260.9 of 730 hours a month (64% fewer instance hours) |
| Smallest practical deployment | ✅ Pass | 1 × `db.t3.medium` (burstable), Standard storage, 1-day backup retention, no replicas |
| Monthly cost with the schedule vs always on | ✅ Pass | $21.44/mo modelled vs $59.98/mo always on, saving $38.54/mo. Prices from the AWS Price List API; usage from CloudWatch |
| Actual spend is tracked | ✅ Pass | Cost Explorer this month: $0.0537 usage, −$0.0538 credits, $0.00 net (data through 3 Oct 2026) |
