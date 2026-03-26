# Developer Guide

**Workspace:** `/data/yy/mva`  
**Last updated:** 2026-03-26

---

## Table of Contents

1. [Repository Overview](#1-repository-overview)
2. [Prerequisites](#2-prerequisites)
3. [Project: mva-v2 — MVA Workbook SPA](#3-project-mva-v2--mva-workbook-spa)
   - 3.1 [Local Development](#31-local-development)
   - 3.2 [Production Build](#32-production-build)
   - 3.3 [Docker Deployment](#33-docker-deployment)
4. [Project: ddm-l6 — DDM IE Platform](#4-project-ddm-l6--ddm-ie-platform)
   - 4.1 [Static Frontend — Local](#41-static-frontend--local)
   - 4.2 [Static Frontend — Docker](#42-static-frontend--docker)
   - 4.3 [Backend API — Local (Host)](#43-backend-api--local-host)
   - 4.4 [Backend API — Docker](#44-backend-api--docker)
5. [Testing](#5-testing)
   - 5.1 [mva-v2 Tests](#51-mva-v2-tests)
   - 5.2 [ddm-l6 Tests](#52-ddm-l6-tests)
6. [Dockerized CLI Tools & Tasks](#6-dockerized-cli-tools--tasks)
   - 6.1 [Setup: docker-compose.tools.yml](#61-setup-docker-composetoolsyml)
   - 6.2 [ddm-l6 Tools](#62-ddm-l6-tools)
   - 6.3 [mva-v2 Tools](#63-mva-v2-tools)
   - 6.4 [Quick Reference Table](#64-quick-reference-table)
7. [Data & Persistence](#7-data--persistence)
8. [API Reference](#8-api-reference)
9. [Proxy Configuration (Corporate Networks)](#9-proxy-configuration-corporate-networks)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Repository Overview

```
mva/
├── DEVELOPER_GUIDE.md          ← this file
├── ddm-l6/                     ← DDM Phase 1: Industrial Engineering Platform
│   ├── backend/                    FastAPI service (Python 3.11)
│   │   ├── main.py                 API server + MOST calculation engine
│   │   ├── requirements.txt
│   │   └── tests_most/             pytest unit tests
│   ├── data/                       Reference data and JSON persistence DB
│   │   ├── db_persistent.json      File-based database (survives restarts)
│   │   └── ddm_structure.csv       MOST catalog (loaded at boot)
│   ├── backend/
│   │   ├── Dockerfile              FastAPI image (python:3.11-slim, non-root)
│   │   ├── main.py                 API server + MOST calculation engine
│   │   ├── requirements.txt
│   │   └── tests_most/             pytest unit tests
│   ├── data/                       Reference data and JSON persistence DB
│   │   ├── db_persistent.json      File-based database (survives restarts)
│   │   └── ddm_structure.csv       MOST catalog (loaded at boot)
│   ├── Dockerfile                  Nginx static image (front-end HTML)
│   ├── docker-compose.yml          ddm-demo + ddm-backend on ddm-net
│   ├── docker-compose.tools.yml    Ephemeral tools: ddm-pytest, ddm-api-test
│   ├── nginx.conf                  Serves static HTML + proxies /api/ → backend
│   ├── test_api.sh                 curl-based API integration smoke test
│   └── docs/
│       ├── MiniMOST_Pipeline_Design_Spec_EN.md
│       └── MiniMOST_Pipeline_Design_Spec_ZH.md
│
└── mva-v2/                     ← MVA Workbook SPA (React + TypeScript)
    ├── src/                        Application source
    ├── tests/                      Vitest unit / functional / regression / e2e
    ├── Dockerfile                  Multi-stage: Node builder → Nginx runner
    ├── Dockerfile.tools            CI image: Node 22 + Python 3 (for smoke test)
    ├── docker-compose.yml          mva-app service (port 8090)
    ├── docker-compose.tools.yml    Ephemeral tools: mva-test, mva-smoke
    ├── nginx.conf
    └── docs/
        ├── smoke-test-plan.md
        └── ai-agent-deep-dive.zh-TW.md
```

### Service Port Map

| Service | Container | Host port |
|---------|-----------|-----------|
| mva-v2 SPA (nginx) | `mva-app` | **8090** |
| ddm-l6 static (nginx) | `ddm-demo` | **9080** |
| ddm-l6 FastAPI (uvicorn) | `ddm-backend` | **8000** |

---

## 2. Prerequisites

### Required

| Tool | Minimum version | Install |
|------|----------------|---------|
| Docker Engine | 24.x | <https://docs.docker.com/engine/install/> |
| Docker Compose v2 | bundled with Docker Desktop / Engine 24 | `docker compose version` |

### For local (host) development only

| Tool | Version | Notes |
|------|---------|-------|
| Node.js | 22.x LTS | For mva-v2 dev server |
| pnpm | latest | `npm install -g pnpm` or `corepack enable` |
| Python | 3.11+ | For ddm-l6 backend |
| pip / venv | — | Isolate per project |

> **Zero-host-dependency goal:** all build, test, and run tasks can be performed through Docker once the `docker-compose.tools.yml` services are in place (see §6).

---

## 3. Project: mva-v2 — MVA Workbook SPA

### 3.1 Local Development

```bash
cd mva-v2

# Install dependencies (first time or after package.json changes)
corepack pnpm install

# Start Vite dev server with HMR
corepack pnpm run dev
# → http://127.0.0.1:5173
```

### 3.2 Production Build

```bash
cd mva-v2

corepack pnpm run build
# Output written to dist/

# Preview the production bundle locally
corepack pnpm run preview
# → http://127.0.0.1:4173
```

### 3.3 Docker Deployment

```bash
cd mva-v2

# Build image and start nginx container
docker compose up -d --build

# Tail logs
docker compose logs -f mva-app

# Stop
docker compose down
```

The container serves the compiled SPA on **host port 8090** → container port 80.  
The image uses a multi-stage build: Node 22 builder compiles with `pnpm run build`, then the static `dist/` folder is copied into `nginx:1.27-alpine`.

---

## 4. Project: ddm-l6 — DDM IE Platform

The project has **two distinct runtimes**:

| Component | Description |
|-----------|-------------|
| **Static frontend** | Nginx serving `l6-ddm-demo.html` and related HTML/JS assets |
| **FastAPI backend** | `backend/main.py` — REST API, MOST engine, SOP, line-balance simulation |

### 4.1 Static Frontend — Local

The static HTML files have no build step. For quick local preview without Docker:

```bash
cd ddm-l6
python3 -m http.server 9080
# → http://localhost:9080/l6-ddm-demo.html
```

### 4.2 Static Frontend — Docker

```bash
cd ddm-l6
docker compose up -d --build
# → http://localhost:9080
```

The `Dockerfile` copies `l6-ddm-demo.html`, `ddm_p*.html`, and the `mva/` directory into `nginx:1.25-alpine`.

### 4.3 Backend API — Local (Host)

> **Note:** Running the backend directly on the host requires the Python virtual environment to be active. The recommended path is to Dockerize the backend (§4.4).

```bash
cd ddm-l6/backend

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Start the API server
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# → http://localhost:8000
# → Swagger UI: http://localhost:8000/docs
```

The server reads `../data/db_persistent.json` on startup (if present) and auto-saves every write back to that file.

### 4.4 Backend API — Docker

The backend is fully containerized. `ddm-l6/` contains:

| File | Role |
|------|------|
| [ddm-l6/backend/Dockerfile](ddm-l6/backend/Dockerfile) | `python:3.11-slim`, non-root `appuser`, layer-cache-optimised |
| [ddm-l6/docker-compose.yml](ddm-l6/docker-compose.yml) | `ddm-backend` service + `data/` bind-mount + health check |
| [ddm-l6/nginx.conf](ddm-l6/nginx.conf) | Serves static HTML; proxies `/api/*` → `ddm-backend:8000` |

**Architecture:**

```
┌─────────────────────┐      ┌─────────────────────────┐
│  ddm-demo  :9080    │      │  ddm-backend  :8000      │
│  nginx static       │─────▶│  uvicorn FastAPI          │
│  /api/* → proxy     │      │  data/ bind-mounted       │
└─────────────────────┘      └─────────────────────────┘
         ↕ ddm-net (bridge, name: ddm-net)
```

```bash
cd ddm-l6

# Build both images and start the full stack
docker compose up -d --build

# Monitor startup (backend health check takes up to 20s on first boot)
docker compose ps
docker compose logs -f ddm-backend

# Verify the API
curl http://localhost:8000/api/v1/health
# → {"status":"healthy","version":"1.0.0", ...}

# Verify the Nginx proxy (round-trip through ddm-demo → ddm-backend)
curl http://localhost:9080/api/v1/health
# → same JSON response

# Stop
docker compose down
```

> **Data persistence:** `ddm-l6/data/` is bind-mounted at `/app/data` inside the container. The JSON database (`db_persistent.json`) and CSV catalogs survive container restarts and image rebuilds without requiring an image rebuild.

---

## 5. Testing

### 5.1 mva-v2 Tests

All test suites use **Vitest**. Run from the `mva-v2/` directory.

```bash
# Full matrix (unit + functional + regression + performance + e2e smoke)
corepack pnpm run test:all

# Individual suites
corepack pnpm run test:unit          # tests/unit/
corepack pnpm run test:functional    # tests/functional/
corepack pnpm run test:regression    # tests/regression/
corepack pnpm run test:perf          # tests/performance/
corepack pnpm run test:e2e           # tests/e2e/smoke.sh
```

The **e2e smoke test** (`tests/e2e/smoke.sh`) builds the project, starts a `python3 -m http.server` process on port 4173, and validates that the SPA delivers the expected HTML and `StreamWeaver` bundle. It requires `pnpm` and `python3` on the host (or the Dockerized alternative, see §6.3).

### 5.2 ddm-l6 Tests

**Unit tests** for the MOST calculation engine live in `backend/tests_most/` and use **pytest**.

```bash
cd ddm-l6/backend

# Activate venv first (or use the Docker path in §6.2)
source .venv/bin/activate

pytest tests_most/ -v
```

**Integration smoke test** exercises all major API endpoints via `curl`:

```bash
cd ddm-l6

# Requires: the backend running on localhost:8000, plus curl and jq
bash test_api.sh
```

---

## 6. Dockerized CLI Tools & Tasks

This section documents how to run **all** ad-hoc scripts and test tools without installing anything on the host — following the Zero Host Dependency principle.

### 6.1 Setup: docker-compose.tools.yml

Each sub-project ships a `docker-compose.tools.yml` that defines ad-hoc service definitions using [Compose profiles](https://docs.docker.com/compose/how-tos/profiles/). Services in the `tools` profile are **never started** by `docker compose up -d`; they must be invoked explicitly with `docker compose run`.

```
ddm-l6/
└── docker-compose.tools.yml   ← ddm-pytest, ddm-api-test

mva-v2/
├── Dockerfile.tools            ← Node 22 + Python 3 CI image
└── docker-compose.tools.yml   ← mva-test, mva-smoke
```

All tool containers:
- Use `--rm` to self-destruct after execution (no dangling containers)
- Mount source code as read-only volumes where possible
- Share the same Docker network as the running services when they need to call the API

### 6.2 ddm-l6 Tools

#### Run the Python unit test suite

```bash
cd ddm-l6

docker compose -f docker-compose.tools.yml run --rm ddm-pytest
```

This starts a `python:3.11-slim` container, installs `requirements.txt`, and runs `pytest tests_most/ -v`. No Python on the host required.

To run a specific test file:

```bash
docker compose -f docker-compose.tools.yml run --rm ddm-pytest \
  pytest tests_most/test_spec_cases.py -v -k "test_m_max_rule"
```

#### Run the API integration smoke test

The backend must be running before executing this test.

```bash
cd ddm-l6

# 1. Ensure the backend service is up
docker compose up -d ddm-backend

# 2. Run the curl-based smoke test inside the Docker network
docker compose -f docker-compose.tools.yml run --rm ddm-api-test
```

The `ddm-api-test` service uses `alpine/curl` + `jq` and targets `http://ddm-backend:8000` (Docker internal DNS) rather than `localhost`, so no port exposure is needed.

### 6.3 mva-v2 Tools

#### Run the full Vitest test matrix

```bash
cd mva-v2

docker compose -f docker-compose.tools.yml run --rm mva-test
```

This reuses the `builder` stage of the multi-stage [Dockerfile](mva-v2/Dockerfile) (Node 22 + pnpm) and runs `pnpm run test:all` inside the container. Exit code mirrors the test result, making it CI-friendly.

To run a single suite:

```bash
docker compose -f docker-compose.tools.yml run --rm mva-test \
  pnpm run test:unit
```

#### Run the E2E smoke test

```bash
cd mva-v2

docker compose -f docker-compose.tools.yml run --rm mva-smoke
```

This builds the production bundle, spins up a `python3 -m http.server` on port 4173, and validates the SPA output — entirely self-contained inside the container. No host pnpm, Node, or Python required.

### 6.4 Quick Reference Table

| Task | Command | Requires services running? |
|------|---------|---------------------------|
| DDM unit tests (pytest) | `docker compose -f ddm-l6/docker-compose.tools.yml run --rm ddm-pytest` | No |
| DDM API integration test | `docker compose -f ddm-l6/docker-compose.tools.yml run --rm ddm-api-test` | Yes — `ddm-backend` |
| MVA-v2 Vitest suite | `docker compose -f mva-v2/docker-compose.tools.yml run --rm mva-test` | No |
| MVA-v2 E2E smoke | `docker compose -f mva-v2/docker-compose.tools.yml run --rm mva-smoke` | No |
| MVA-v2 single suite | `… run --rm mva-test pnpm run test:functional` | No |
| MVA-v2 dev server | `docker compose -f mva-v2/docker-compose.tools.yml run --rm --service-ports mva-dev` | No |

> **Migrating from host scripts:** Replace every occurrence of `python …` / `pytest …` / `bash test_api.sh` / `pnpm run test:…` in CI pipelines, `Makefile` targets, or runbooks with the corresponding `docker compose -f … run --rm <service>` command above.

---

## 7. Data & Persistence

### ddm-l6 Backend

The FastAPI server uses a **file-based JSON database** (`data/db_persistent.json`). This file is:

- Read on server startup (`load_db_from_json()`)
- Written after every mutating request (`auto_save()` / `save_db_to_json()`)
- Excluded from the Docker image — it **must** be bind-mounted at runtime

When running via Docker, ensure the `data/` directory is mounted as a volume:

```yaml
# docker-compose.yml (excerpt)
volumes:
  - ./data:/app/data
```

This guarantees data persists across container restarts and upgrades.

**Database management endpoints** (Manager role required):

| Endpoint | Method | Action |
|----------|--------|--------|
| `/api/v1/db/status` | GET | Check file size, modification time, collection counts |
| `/api/v1/db/save` | POST | Force-save current in-memory state |
| `/api/v1/db/load` | POST | Reload from file (discards in-memory changes) |
| `/api/v1/db/export` | GET | Download full DB as JSON |
| `/api/v1/db/reset` | DELETE | Delete persistent file (requires server restart to reset) |

### ddm-l6 CSV Catalogs

The MOST action catalog (`data/ddm_structure.csv`) is loaded once at process startup by `load_ddm_structure_catalogs()`. Editing the CSV requires an API server restart (or container restart with `docker compose restart ddm-backend`).

CSV files used at runtime:

| File | Purpose |
|------|---------|
| `data/ddm_structure.csv` | MOST catalog: objects, gloves, precautions, MI naming rules |
| `data/MiniMOST1128.csv` | TMU reference data |
| `data/MiniMOST1128_completed.csv` | Completed TMU reference |
| `data/mb_SQT-K860G6-BASY 1.3.csv` | Motherboard config for K860G6 |
| `data/dimm_SQT-K860G6-BASY 1.3.csv` | DIMM config |

### mva-v2 SPA

All state is stored in **browser localStorage**. There is no backend. To reset application state, open DevTools → Application → Storage → Clear localStorage.

---

## 8. API Reference

The DDM backend exposes a [FastAPI](https://fastapi.tiangolo.com/) application with interactive docs:

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

### Authentication

All endpoints (except `/api/v1/health`) require a JWT Bearer token.

```bash
# Login
curl -s -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"engineer1","password":"eng123"}' | jq .access_token

export TOKEN="<token from above>"
```

**Default credentials (development only — change in production):**

| Username | Password | Role |
|----------|----------|------|
| `admin` | `admin123` | Manager |
| `engineer1` | `eng123` | Engineer |
| `operator1` | `op123` | Operator |

> ⚠️ Hardcoded credentials exist for development convenience only. Before any production deployment, externalize credentials via environment variables or a secrets manager.

### Key Endpoint Groups

| Prefix | Description |
|--------|-------------|
| `POST /api/v1/auth/login` | Obtain JWT |
| `GET /api/v1/master/objects` | MiniMOST object catalog |
| `GET /api/v1/master/glove-rules` | Glove requirement rules |
| `POST /api/v1/most/calculate` | MOST TMU calculation engine |
| `GET /api/v1/sop/versions` | SOP version list |
| `POST /api/v1/simulation/line-balance` | Line balance simulation |
| `GET /api/v1/level-system/{project_id}` | Level system entries |
| `GET /api/v1/audit/logs` | Audit trail |

---

## 9. Proxy Configuration (Corporate Networks)

Both projects support build-time and runtime proxy injection via environment variables. Create a `.env` file or export the variables before running Docker commands:

```bash
export HTTP_PROXY=http://10.6.254.210:3128
export HTTPS_PROXY=http://10.6.254.210:3128
export NO_PROXY=localhost,127.0.0.1
export http_proxy=$HTTP_PROXY
export https_proxy=$HTTPS_PROXY
export no_proxy=$NO_PROXY
```

Docker Compose will pass these through automatically because both `docker-compose.yml` files declare the `args` and `environment` blocks that forward the host variables.

If the Docker **daemon** also needs proxy access (to pull base images), configure it at the service level:

```bash
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/proxy.conf <<'EOF'
[Service]
Environment="HTTP_PROXY=http://10.6.254.210:3128"
Environment="HTTPS_PROXY=http://10.6.254.210:3128"
Environment="NO_PROXY=localhost,127.0.0.1"
EOF

sudo systemctl daemon-reload && sudo systemctl restart docker
```

---

## 10. Troubleshooting

### `docker compose up` fails to pull base images

The Docker daemon needs daemon-level proxy configuration (see §9). Run `docker pull nginx:1.25-alpine` after restarting the daemon to verify connectivity.

### Port already in use

```bash
# Find and kill the conflicting process
lsof -i :8000      # ddm backend
lsof -i :8090      # mva-v2
lsof -i :9080      # ddm nginx
```

### Backend returns 500 on startup

Check if `data/db_persistent.json` is malformed. The server logs the parse error and falls back to defaults automatically. To force a clean slate:

```bash
# Remove the corrupted file; the server will rebuild from in-memory defaults
rm ddm-l6/data/db_persistent.json
docker compose restart ddm-backend
```

### `ddm_structure.csv` catalog not loading

Verify the file path resolves correctly from within the container. The backend computes:

```python
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
```

When running via Docker with the `data/` bind-mount, this resolves to `/app/data/ddm_structure.csv`. Confirm with:

```bash
docker compose exec ddm-backend ls /app/data/
```

### mva-v2 E2E smoke test fails with "StreamWeaver not found"

The bundle name comes from the Vite build fingerprint. Rebuild the project cleanly and re-run:

```bash
cd mva-v2
rm -rf dist
corepack pnpm run build
bash tests/e2e/smoke.sh
```

### pytest import error: `ModuleNotFoundError: No module named 'main'`

The test suite must be run from the `backend/` directory, where `main.py` lives:

```bash
cd ddm-l6/backend
pytest tests_most/ -v

# Or using the Dockerized runner:
docker compose -f ../docker-compose.tools.yml run --rm ddm-pytest
```
