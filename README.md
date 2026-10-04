# InspectDB: Inspection Report Management System

> **Repositories:** this repository contains the FastAPI backend, infrastructure (`deploy/`), and full project documentation. The React frontend lives in [suchir7/InspectDB-Frontend](https://github.com/suchir7/InspectDB-Frontend) and is hosted on Vercel.
> 
### Amazon DocumentDB Architecture & Neon PostgreSQL Authentication (AWS Project)

InspectDB is a full-stack, enterprise-grade inspection report management system architected for variable-schema documents, nested JSON structures, and secure user management. It is designed to demonstrate modern multi-database cloud architectures:

1. **Neon PostgreSQL:** Persistent relational database for user accounts, credentials (bcrypt), authentication state, and future user-specific relational entities.
2. **Amazon DocumentDB (MongoDB-Compatible):** Document-oriented database for variable-schema inspection reports, polymorphic findings, and deeply nested equipment telemetry.
3. **Google Gemini 2.5 Flash:** AI-powered query assistant, cost optimizer, and deployment advisor.

---

## 🏛 Multi-Database Architecture & Clear Separation of Concerns

```text
React Frontend (Vite + TypeScript)
        │
        ▼ (JWT Bearer Token / REST API)
FastAPI Application Layer (Python 3.11+)
        ├── Authentication & User Persistence
        │       │
        │       ▼ (SQLAlchemy / Psycopg2 / SSL)
        │   Neon PostgreSQL (Cloud Serverless Postgres)
        │       • Users table (UUID, Name, Normalized Email, Bcrypt Hash, Role, Timestamps)
        │       • User preferences & future relational data
        │
        └── Inspection Document Repository
                │
                ▼ (PyMongo / BSON / Document Protocol)
            Phase 1: In-Memory / Simulated Local Repository
            Phase 2: Amazon DocumentDB Cluster (db.t3.medium)
                • inspection_reports collection (Polymorphic JSON, dynamic telemetry, arrays)
```

> **Important Architectural Separation:**
> - **Neon PostgreSQL** stores application/user/account data and relational entities.
> - **Amazon DocumentDB** (or the local Phase 1 in-memory repository) stores variable-schema inspection documents.
> - Inspection documents are **NOT** stored in PostgreSQL, preserving native document-store characteristics without rigid DDL migrations.

---

## 🔐 Authentication & Security Architecture

### 1. Database Schema (`users` table)
- `id`: UUID Primary Key (`String(36)`)
- `name`: Inspector full name
- `email`: Normalized lowercase unique email index
- `password_hash`: Bcrypt hashed with salted 12-round complexity
- `role`: Role-based access (`USER` by default; registration never allows privilege escalation to `ADMIN`)
- `created_at`: UTC timestamp with timezone
- `updated_at`: Automatically maintained update timestamp

### 2. Authentication API Endpoints

| Method | Endpoint | Description | Auth Required |
|---|---|---|---|
| `POST` | `/api/auth/register` | Register new user account; validates email & strong password | No |
| `POST` | `/api/auth/login` | Authenticate credentials & generate signed JWT token | No |
| `GET` | `/api/auth/me` | Fetch authenticated user profile | Yes (Bearer JWT) |
| `POST` | `/api/auth/logout` | Invalidate current session | Yes (Bearer JWT) |

### 3. Security Principles Enforced
- **Zero Plaintext Passwords:** Passwords are never stored, logged, or serialized.
- **Safe Responses:** API responses never return `password_hash`, database credentials, or `JWT_SECRET`.
- **JWT Protection:** Signed with HMAC-SHA256 containing `sub` (User ID), email, role, and expiration timestamp.
- **Route Guarding:** Unauthenticated frontend navigation to Dashboard, Reports, AI Assistant, Cost Optimizer, Cost Monitoring, or Query Explorer automatically redirects to `/login`.

---

## 💰 AI Cost Optimizer & Deployment Advisor

### Overview
Amazon DocumentDB charges for compute instances (per second), cluster storage (per GB-month replicated 6-ways across 3 Availability Zones), I/O requests (per 1M operations), and backup retention.

The **AI Cost Optimizer & Deployment Advisor** module combines a **deterministic AWS pricing engine** with **Gemini 2.5 Flash** to provide actionable architectural advice.

### Key Capabilities
1. **Interactive Workload Sizing & Presets:**
   - Sliders for daily requests, read/write ratio, cluster storage (GB), and backup retention days.
   - Quick uptime presets: `24/7 Production` (730 hrs), `8h/Weekday Dev` (160 hrs), `4h/Weekday Demo` (80 hrs), and `Local Dev` (0 hrs).
   - Regional pricing multipliers across 6 AWS regions (US East, US West, Ireland, Frankfurt, Mumbai, Singapore).
2. **Deterministic Multi-Tier Rate Card Modeling:**
   - Compares **5 deployment architectures**:
     - **Local Dev (Docker/Mock):** $0.00/mo.
     - **Scheduled Dev (db.t3.medium @ 160h/mo):** ~$13.98/mo (Compute + Storage + I/O).
     - **24/7 Single-AZ (1x db.t3.medium @ 730h/mo):** ~$58.44/mo.
     - **Multi-AZ HA (2x db.t3.medium @ 730h/mo):** ~$115.38/mo.
     - **Serverless Elastic Cluster:** Scales compute on demand based on vCPUs.
3. **Gemini AI Optimization Advisor:**
   - Identifies primary cost drivers and idle capacity waste.
   - Generates structured recommendations categorized by *Compute*, *Scheduling*, *Storage*, *Architecture*, and *Monitoring*.
   - Outlines estimated monthly dollar savings, trade-offs/risks, and implementation steps.

---

## 📈 AWS DocumentDB Cost Monitoring & Optimization

The **Cost Monitoring** module (`/cost-monitoring`) tracks estimated database spending over time, identifies structural cost drivers, flags anomalies, and enables continuous optimization under **Academic Simulation Mode**.

### Key Capabilities
1. **Deterministic Summary Cards:**
   - Real-time calculations for *Current Estimated Cost ($/mo)*, *Estimated Daily Cost ($/day)*, *Projected Monthly Cost*, *Potential Optimization %*, *Threshold ($/mo)*, and *Budget Status* (`Within` / `Near` / `Exceeded`).
2. **Interactive Cost Trajectory Chart:**
   - 7-day, 30-day, and 90-day timeframes with daily and cumulative cost projection curves.
3. **Component Breakdown & Driver Ranking:**
   - Deconstructs spending into Compute, Storage (6-way replicated across 3 AZs), Request I/O, and Backup Snapshots.
4. **Cost Anomaly Detection:**
   - Compares current metrics with baseline snapshots to detect cost spikes (+15%), runtime surges, or storage growth.
5. **Configurable Budget Thresholds & Simulated Alerts:**
   - Configurable monthly threshold progress bar with local persistence.
6. **Gemini Cost Analyst & "Why Did Cost Change?":**
   - On-demand Gemini 2.5 Flash architectural analysis (with deterministic fallback).

---

## 🔬 Amazon DocumentDB Nested Query Laboratory

The **Nested Document Query Explorer** (`/nested-query`) is an interactive laboratory designed to build, validate, understand, and evaluate MongoDB-compatible queries across nested, variable-schema inspection documents.

### Key Capabilities
1. **Dynamic Schema Explorer:**
   - Automatically crawls the dataset to discover all nested paths, polymorphic attributes (`electrical_telemetry`, `fire_safety_data`, `hvac_diagnostics`), and arrays.
2. **Type-Aware Query Builder:**
   - Inferred and validated operators per data type (`categorical`, `string`, `number`, `boolean`, `date`, `array`, `object`).
   - Logic groups supporting `ALL (AND)`, `ANY (OR)`, and `NONE (NOT)` matching.
3. **Array & `$elemMatch` Multi-Condition Grouping:**
   - Automatically detects multiple conditions targeting the same subdocument array and constructs an atomic `{ findings: { $elemMatch: { ... } } }` query.
4. **12 Rich Presets & Saved Queries:**
   - High Severity Findings, Critical Findings, Open Electrical Issues, Failed Safety Checks, Telemetry Queries, Variable-Schema Polymorphic Fields, and more.

---

## 🔍 MongoDB → Amazon DocumentDB Compatibility Analyzer

### Overview & Problem Statement
Amazon DocumentDB (with MongoDB compatibility) is a purpose-built document database service designed for scale and high performance. While Amazon DocumentDB emulates the MongoDB wire protocol and supports the vast majority of query APIs, **MongoDB API compatibility does NOT mean 100% MongoDB feature parity**.

Certain MongoDB operations (e.g., arbitrary JavaScript execution via `$where`, MapReduce, custom accumulators, or specific compound constructs such as `$elemMatch` nested inside `$all`) are either unsupported or exhibit functional and behavioral differences in Amazon DocumentDB.

The **InspectDB AI Query Assistant** incorporates a production-grade, deterministic **Compatibility Rules Engine** that evaluates every AI-generated query *before* marking it as validated or executing it.

### Official AWS Reference Documentation
- [AWS DocumentDB Supported MongoDB APIs, Operations, and Data Types](https://docs.aws.amazon.com/documentdb/latest/devguide/mongo-apis.html)
- [AWS DocumentDB Functional Differences with MongoDB](https://docs.aws.amazon.com/documentdb/latest/devguide/functional-differences.html)
- [AWS DocumentDB MongoDB Compatibility Matrix](https://docs.aws.amazon.com/documentdb/latest/devguide/compatibility.html)

### 6-Step Verification & Query Lifecycle
```text
1. User Natural Language Input ("Find HVAC reports with critical issues")
        │
        ▼
2. Gemini 2.5 Flash Query Generation (or Deterministic Rule-Based Fallback)
        │
        ▼
3. AST Safety Validation (No mutations, strict read-only find/aggregate/count)
        │
        ▼
4. Deterministic DocumentDB Compatibility Analyzer
        ├── Target Version Registry Check (3.6, 4.0, 5.0, 8.0)
        ├── Unsupported Operator Detection ($where, $accumulator, $function, $natural)
        ├── Compound Rule Analysis ($elemMatch nested inside $all)
        └── Behavioral Difference Scan (PCRE regex flags, collation, null byte constraints)
        │
        ▼
5. Automated Alternative Generator (Transform unsupported queries into DocumentDB equivalents)
        │
        ▼
6. UI Presentation & Safe Execution (Compatibility status badge, issue matrix, AWS docs link, 1-click alternative copy)
```

### Compatibility Statuses Distinguishable in UI & Backend
1. **`COMPATIBLE`** (Green): Query syntax, operators, and array expressions are 100% supported in the selected DocumentDB engine version.
2. **`PARTIALLY_COMPATIBLE`** (Amber): Supported with specific version requirements or minor constraints.
3. **`INCOMPATIBLE`** (Red): Contains unsupported MongoDB operations (e.g., `$where`, `$accumulator`, or `$elemMatch` within `$all`). Marked `is_validated = false`. An automated DocumentDB-compatible alternative is generated and validated.
4. **`BEHAVIOR_DIFFERENCE`** (Blue): The query will execute in DocumentDB, but with documented behavioral variations (e.g., case-insensitive regex index usage, collation sorting, or null byte string handling).
5. **`UNKNOWN`** (Gray): Query contains custom or undocumented operators that cannot be deterministically verified.

### Key Documented Compatibility Scenarios Handled
- **`$elemMatch` inside `$all`:** MongoDB permits `{ "findings": { "$all": [{ "$elemMatch": { "severity": "CRITICAL" } }, { "$elemMatch": { "remediation_status": "OPEN" } }] } }`. Amazon DocumentDB does **not** support `$elemMatch` inside `$all`. The engine flags this incompatibility and automatically transforms the query into an equivalent `$and` construct:
  ```json
  {
    "$and": [
      { "findings": { "$elemMatch": { "severity": "CRITICAL" } } },
      { "findings": { "$elemMatch": { "remediation_status": "OPEN" } } }
    ]
  }
  ```
- **JavaScript & Code Execution:** Operators such as `$where`, `$accumulator`, and `$function` are flagged as incompatible with direct references to AWS documentation explaining DocumentDB's engine design.
- **Regex Compatibility Differences:** Flags case-insensitive `$options: "i"` queries with guidance on Amazon DocumentDB index optimization considerations.

---

## 🛠 Technology Stack

### Frontend
- **Framework:** React 19 + TypeScript + Vite
- **Routing & State:** React Router v7 + React Context API (`AuthContext`)
- **Styling:** Vanilla CSS with custom design system tokens & CSS variables
- **Icons:** Lucide React

### Backend
- **Framework:** Python 3.11+ / FastAPI
- **Relational Storage:** Neon PostgreSQL + SQLAlchemy ORM + Psycopg2
- **Password Hashing:** Bcrypt (12-round salted hashing)
- **Token Security:** Python-Jose (JWT HMAC-SHA256)
- **AI Integration:** Google GenAI SDK (`google-genai`) / Gemini API (`gemini-2.5-flash`)
- **Data Modeling & Validation:** Pydantic v2
- **Testing:** PyTest (43 automated tests across Auth, AI Assistant, Cost Optimizer, Cost Monitoring, and Nested Queries)

---

## 📁 Repository Structure

```text
AWS-PROJECT/
├── backend/
│   ├── app/
│   │   ├── api/
│   │   │   ├── deps.py              # JWT authentication dependency (get_current_user)
│   │   │   └── routes/
│   │   │       ├── auth.py          # /api/auth/register, /login, /me, /logout
│   │   │       ├── health.py        # GET /api/health
│   │   │       ├── reports.py       # CRUD /api/reports
│   │   │       ├── query.py         # POST /api/query
│   │   │       ├── stats.py         # GET /api/stats
│   │   │       ├── ai.py            # POST /api/ai/generate-query & GET /api/ai/status
│   │   │       └── cost.py          # Cost Estimator & Monitoring endpoints
│   │   ├── config/
│   │   │   └── settings.py          # App settings, Neon DATABASE_URL, JWT & Gemini config
│   │   ├── core/
│   │   │   └── security.py          # Bcrypt hashing & JWT token encode/decode
│   │   ├── db/
│   │   │   ├── base.py              # Declarative base
│   │   │   └── session.py           # SQLAlchemy engine & Neon session manager
│   │   ├── models/
│   │   │   └── user.py              # SQLAlchemy User table definition
│   │   ├── repositories/
│   │   │   ├── user_repository.py   # PostgreSQL User CRUD operations
│   │   │   ├── base.py              # Abstract inspection repository interface
│   │   │   ├── memory_repository.py # In-memory nested query repository
│   │   │   └── sample_data.py       # 6 rich variable-schema sample reports
│   │   ├── schemas/
│   │   │   ├── auth.py              # Pydantic schemas for registration & login
│   │   │   ├── report.py            # Pydantic schemas for reports & query AST
│   │   │   ├── ai.py                # Pydantic schemas for AI Assistant
│   │   │   └── cost.py              # Pydantic schemas for Cost Optimizer
│   │   ├── services/
│   │   │   ├── auth_service.py      # User registration & verification logic
│   │   │   ├── inspection_service.py# Inspection business logic service
│   │   │   ├── query_validator.py   # Strict safety & AST validator
│   │   │   ├── ai_service.py        # Gemini AI query generator
│   │   │   ├── cost_calculator.py   # Deterministic AWS pricing formulas
│   │   │   └── cost_optimizer_service.py # Gemini cost advisor & caching
│   │   └── main.py                  # FastAPI application entry point & lifespan init
│   ├── tests/
│   │   ├── test_auth.py             # 12 unit tests for Neon PostgreSQL auth & JWT
│   │   ├── test_ai_service.py       # 12 unit tests for AI query generation & safety
│   │   ├── test_cost_optimizer.py   # 6 unit tests for pricing math & Gemini advisor
│   │   ├── test_cost_monitoring.py  # 7 unit tests for cost trends & anomaly detection
│   │   └── test_nested_query.py     # 6 unit tests for MongoDB AST & nested filters
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── auth/                # ProtectedRoute guard
│   │   │   ├── common/              # Badges, Modals, JSON Viewer, Demo Banner
│   │   │   └── layout/              # Collapsible Sidebar & Top Navbar with user profile
│   │   ├── context/
│   │   │   └── AuthContext.tsx      # AuthProvider, token management & session state
│   │   ├── pages/
│   │   │   ├── LoginPage.tsx        # Professional Sign In page
│   │   │   ├── RegisterPage.tsx     # Inspector Registration page
│   │   │   ├── DashboardPage.tsx    # Operational metrics & status distribution
│   │   │   ├── ReportsListPage.tsx  # Search, filters, sort, pagination, actions
│   │   │   ├── CreateReportPage.tsx # Dynamic variable schema form builder
│   │   │   ├── EditReportPage.tsx   # Report editor
│   │   │   ├── ReportDetailsPage.tsx# Deep nested document inspector
│   │   │   ├── AiQueryAssistantPage.tsx # AI Chatbot for DocumentDB queries
│   │   │   ├── CostOptimizerPage.tsx# AI Cost Optimizer & Deployment Advisor
│   │   │   ├── CostMonitoringPage.tsx # AWS DocumentDB Cost Monitoring laboratory
│   │   │   ├── NestedQueryExplorerPage.tsx # Visual query builder & AST translator
│   │   │   ├── DatabaseOverviewPage.tsx    # DocumentDB specs & compatibility
│   │   │   └── SettingsPage.tsx     # API diagnostics & theme settings
│   │   ├── services/
│   │   │   └── api.ts               # Typed REST client with auto JWT bearer injection
│   │   ├── types/
│   │   │   └── index.ts             # TypeScript definitions
│   │   ├── App.tsx                  # Root router with protected route guards
│   │   └── main.tsx
│   ├── package.json
│   └── vite.config.ts
└── README.md
```

---

## 🚀 Getting Started

### 1. Backend Setup (FastAPI + Neon PostgreSQL)

```bash
cd backend

# Install dependencies
pip install -r requirements.txt

# Configure environment variables in .env
cp .env.example .env
# Edit .env and supply your DATABASE_URL and optional GEMINI_API_KEY:
# DATABASE_URL=postgresql://neondb_owner:password@ep-xyz-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require
# JWT_SECRET=your_jwt_secret_key_here
# GEMINI_API_KEY=your_gemini_api_key_here

# Run the FastAPI server (starts on http://localhost:8000)
python -m uvicorn app.main:app --reload --port 8000
```

- **Swagger UI Interactive Docs:** `http://localhost:8000/api/docs`
- **Health Check Endpoint:** `http://localhost:8000/api/health`
- **Registration Endpoint:** `http://localhost:8000/api/auth/register`
- **Login Endpoint:** `http://localhost:8000/api/auth/login`

### 2. Run Automated Unit Tests

```bash
cd backend
python -m pytest tests/ -v
```

All **43 automated unit tests** (Authentication, JWT validation, AST safety validator, pricing calculations, regional multipliers, caching, anomaly detection, and schema discovery) run against in-memory fixtures with 100% pass rate.

### 3. Frontend Setup (React 19 + Vite)

```bash
cd frontend

# Install dependencies
npm install

# Start Vite dev server (starts on http://localhost:5173)
npm run dev
```

1. Navigate to `http://localhost:5173`.
2. Unauthenticated visits automatically route to `/login`.
3. Click **"Create an account"** (`/register`) to register your inspector credentials.
4. Your account is stored in Neon PostgreSQL and your session is authenticated via JWT!

---

## 🧪 Local MongoDB Testing & Amazon DocumentDB Compatibility Workflow

InspectDB supports testing generated queries directly against a locally running MongoDB instance in development before deploying to Amazon DocumentDB.

> [!IMPORTANT]
> **MongoDB API Compatibility ≠ Complete Feature Parity with Amazon DocumentDB**
> While Amazon DocumentDB emulates MongoDB 3.6, 4.0, and 5.0 wire protocols, certain MongoDB features (such as arbitrary JavaScript execution via `$where`, map-reduce, custom `$accumulator`, or `$elemMatch` inside `$all`) are unsupported or have functional differences. InspectDB validates every query against both engines.

### 1. Start MongoDB Locally
Start your local MongoDB daemon or launch via Docker:
```bash
# Option A: Local MongoDB Service
mongod --dbpath <data_directory>

# Option B: Docker Container
docker run -d -p 27017:27017 --name inspectdb-mongo mongo:latest
```

### 2. Configure Environment (`backend/.env`)
Ensure your `backend/.env` has the local MongoDB parameters:
```env
STORAGE_MODE=memory                   # Set to "mongodb" to use local MongoDB as primary store
MONGODB_URI=mongodb://localhost:27017
MONGODB_DATABASE=inspectdb
MONGODB_COLLECTION=inspection_reports
MAX_QUERY_RESULTS=20
MONGODB_TIMEOUT_MS=2000
DOCUMENTDB_TARGET_VERSION=5.0
```

### 3. Seed Realistic Variable-Schema Inspection Reports
Populate your local MongoDB instance with realistic polymorphic inspection reports containing nested telemetry, custom metrics, and arrays:
```bash
cd backend
python scripts/seed_mongodb.py
```
Output:
```text
✓ Connected successfully to local MongoDB instance.
✓ Seeding Complete!
  - Newly inserted documents: 6
  - Total collection count:    6
✓ Created performance indexes on (id, category, status, overall_severity, findings.severity).
```

### 4. Test Queries via AI Query Assistant
1. Open the **AI Query Assistant** in your browser (`http://localhost:5173/ai-assistant`).
2. Ask any natural-language question (e.g. *"Find all reports with high-severity findings"* or *"Find reports with findings in Main Switchgear Enclosure SG-02"*).
3. Review the **4 Clear Output Sections**:
   - **User Request:** The natural language prompt.
   - **Generated MongoDB Query:** Formatted syntax-highlighted read-only query.
   - **Local MongoDB Test:** Real-time local execution results (documents matched, execution time in ms, and sample returned documents capped at `MAX_QUERY_RESULTS`).
   - **Amazon DocumentDB Compatibility:** AWS Rules Engine analysis (`COMPATIBLE`, `PARTIALLY_COMPATIBLE`, `INCOMPATIBLE`, `BEHAVIOR_DIFFERENCE`), comparison matrix, detected issues with AWS docs links, and verified DocumentDB-compatible alternatives.
4. Access previous tests anytime via the **Query History** drawer.

---

## 🔮 Phase 2: Live Amazon DocumentDB & Production Deployment

Set `STORAGE_MODE=documentdb` and the backend stores inspection reports in an Amazon DocumentDB cluster:

- Connects with TLS using the Amazon CA bundle (`global-bundle.pem`, baked into the Docker image), which stock drivers do not trust, and `retryWrites=false`, which DocumentDB 3.6/4.0 require.
- Creates the required indexes on startup (unique report `id`, per-user lookups, severity/category filters).
- Allocates report IDs from an atomic counter, so they never collide across users or after deletes.
- Refuses to start with a clear error if `DOCUMENTDB_URI` or the CA bundle is missing, and never silently falls back to in-memory storage.
- `GET /api/health` reports live DocumentDB connectivity, and the dashboard and settings pages show the active engine.

DocumentDB has no public endpoint, so the API runs on an EC2 server in the same VPC. **See [DEPLOYMENT.md](DEPLOYMENT.md)** for the full deployment: a CloudFormation template for DocumentDB plus EC2 (with a start/stop schedule to cut costs), Docker Compose with Caddy for automatic HTTPS, GitHub Actions for automatic deploys, and the frontend on Vercel.

