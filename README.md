# Enterprise MVA Platform

> **Bridging Large Language Models with Industry 5.0** — an open-source, production-ready AI orchestration platform for intelligent manufacturing value analysis.

[![Build Status](https://img.shields.io/github/actions/workflow/status/iiyyll01lin/mva-calculator/ci.yml?branch=main&style=flat-square&label=build)](https://github.com/iiyyll01lin/mva-calculator/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg?style=flat-square)](LICENSE)
[![Coverage](https://img.shields.io/badge/coverage-87%25-brightgreen?style=flat-square)](#testing)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue?style=flat-square)](https://www.python.org/)
[![Pydantic v2](https://img.shields.io/badge/pydantic-v2-e92063?style=flat-square)](https://docs.pydantic.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104-009688?style=flat-square)](https://fastapi.tiangolo.com/)
[![React](https://img.shields.io/badge/React-18-61dafb?style=flat-square)](https://react.dev/)

---

## Executive Summary

Manufacturing enterprises face a widening gap: **Industry 5.0** demands real-time, human-in-the-loop decisions driven by AI, yet most platforms still run batch workloads with brittle rule engines disconnected from physical equipment.

The **Enterprise MVA Platform** solves this by unifying:

- A **Multi-Agent Swarm** that conducts structured debates between cost, quality, and temporal analysis agents before reaching a consensus.
- A **Cyber-Physical ROS2 Bridge** that translates swarm consensus into cryptographically signed robotic commands dispatched to real factory assets.
- **Agentic ETL** that autonomously ingests messy legacy CSV data, normalizes it in real time, and feeds it into a **Temporal RAG** vector store.
- **End-to-End Cryptographic Provenance** — every VLM analysis, robot command, and audit log entry is Ed25519-signed and chained in a tamper-evident ledger.
- A **Mission Control Dashboard** that streams live agent debates, IoT anomalies, and HITL approval queues over Server-Sent Events.

> "This is not another LLM wrapper. It is a full-stack AI operating system for the factory floor."

---

## Core Features Matrix

| Capability | Module | Highlights |
|---|---|---|
| 🤝 **Multi-Agent Swarm Debate** | `agents/debate_room.py` | Cost vs. Quality agents argue; ConsensusJudge synthesizes; bounded by `MAX_TURNS_CEILING` |
| 🕒 **Temporal RAG** | `agents/temporal_analyst.py` + `tools/temporal_rag.py` | IQR anomaly detection, linear regression trend classification (IMPROVING/DEGRADING/STABLE) |
| 👁️ **Vision Inspector (VLM)** | `agents/vision_inspector.py` | Detects smoke, jams, PPE violations from RTSP frames; Ed25519-signed results |
| 🌊 **Agentic ETL** | `data_ops/agentic_etl.py` | LLM-driven column aliasing, missing-value imputation, `TimeSeriesRecord` Pydantic schema |
| 🔐 **Cryptographic Provenance** | `security/provenance.py` | Ed25519 ephemeral key pair; SHA-256 hash chain; tamper-evident JSONL audit log |
| 🤖 **ROS2 Cyber-Physical Bridge** | `robotics/ros2_bridge.py` | CMD_VEL, JOINT_TARGET, ESTOP, NAVIGATION_GOAL over MQTT/WebSocket; HITL-gated dispatch |
| 🚁 **SITL Simulator** | `robotics/sitl_simulator.py` | Software-in-the-loop test harness; no physical hardware required for CI |
| 🔭 **IoT Watchdog** | `events/iot_watchdog.py` | Async sensor tick loop; per-machine cooldown lock; fires DebateRoom on yield anomaly |
| 🧪 **LLM-as-a-Judge Eval** | `eval/judge.py` | Faithfulness, Relevancy, Security Adherence, Latency Score |
| 🔴 **Red Team Generator** | `eval/red_team.py` | TOOL_GUARD_BYPASS, BOM_HALLUCINATION, HITL_BYPASS adversarial suites |
| 📡 **LLMOps Telemetry** | `telemetry.py` | Structured JSON spans → Langfuse / OpenTelemetry OTLP / JSONL file |
| 🧠 **Stateful Agent Memory** | `memory_store.py` | Short-term in-process + long-term Redis/File backend; context pruning with summary compression |
| 🛡️ **Tool Sandbox** | `security/tool_sandbox.py` | ToolGuard validates every tool call; blocks injection and HITL bypass attempts |
| ⚛️ **MVA Workbook SPA** | `mva-v2/` | React 18 + TypeScript; Web Worker MVA engine; TanStack Virtual; XLSX multi-sheet export |

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                        Mission Control UI (SSE)                       │
│                   React SPA  /  Nginx Ingress  / K8s                  │
└────────────────────────────┬─────────────────────────────────────────┘
                             │  HTTP / SSE
┌────────────────────────────▼─────────────────────────────────────────┐
│                  FastAPI Agentic Backend  (Python 3.11)               │
│                                                                       │
│  IoT Watchdog ──► Anomaly Detector ──► Swarm Debate Room             │
│       │                                      │                        │
│  Temporal RAG ◄── Agentic ETL               │                        │
│       │                                      ▼                        │
│  Vision Inspector (VLM) ──────────► ConsensusJudge Agent             │
│                                              │                        │
│  HITL Approval Queue ◄───────────────────────┘                        │
│       │                                                               │
│  Cryptographic Provenance (Ed25519 + SHA-256 chain)                   │
│       │                                                               │
│  ROS2 Bridge ──► MQTT / WebSocket ──► Factory Robots / AGVs          │
└──────────────────────────────────────────────────────────────────────┘
```

For detailed Mermaid diagrams of the data flow and Kubernetes infrastructure, see [docs/architecture.md](docs/architecture.md).

---

## Quick Start

Get the Mission Control dashboard running in **3 commands**:

```bash
# 1. Clone the repository
git clone https://github.com/iiyyll01lin/mva-calculator.git && cd mva-calculator

# 2. Build and start all services (backend API + Nginx frontend)
make run-dev

# 3. Open Mission Control
open http://localhost:9080/mission-control.html
```

> **Requirements:** Docker 24+ and Docker Compose v2. No Python or Node.js installation needed on the host.

### Environment Variables (Optional)

Copy the example env file and configure your LLM keys:

```bash
cp ddm-l6/.env.example ddm-l6/.env
# Edit .env — set OPENAI_API_KEY or ANTHROPIC_API_KEY, LANGFUSE_* for telemetry
```

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | *(unset)* | Enables cloud-tier LLM calls |
| `ANTHROPIC_API_KEY` | *(unset)* | Anthropic Claude as cloud-tier alternative |
| `MVA_PRUNE_TOKEN_LIMIT` | `4000` | Context window before pruning fires |
| `DEBATE_VISION_TIMEOUT_S` | `30.0` | VLM vision-proof timeout per debate turn |
| `LANGFUSE_PUBLIC_KEY` | *(unset)* | Enables Langfuse telemetry emission |
| `LANGFUSE_SECRET_KEY` | *(unset)* | Langfuse secret key |
| `MQTT_BROKER_URL` | *(unset)* | Enables real MQTT dispatch to robots |
| `ROS2_BRIDGE_WS_URL` | *(unset)* | Enables WebSocket ROS2 bridge |

---

## Repository Structure

```
mva-calculator/
├── ddm-l6/                   ← DDM Phase 1 — AI-Augmented IE Platform
│   ├── backend/              FastAPI agentic backend (Python 3.11)
│   │   ├── agents/           Multi-Agent Swarm (DebateRoom, TemporalAnalyst, VisionInspector)
│   │   ├── data_ops/         Agentic ETL pipeline
│   │   ├── eval/             LLM-as-a-Judge + Red Team adversarial suite
│   │   ├── events/           IoT Watchdog (async SSE event loop)
│   │   ├── memory/           Alignment store + context compression
│   │   ├── robotics/         ROS2 bridge + SITL simulator
│   │   ├── security/         Cryptographic provenance + ToolGuard sandbox
│   │   ├── tools/            Temporal RAG vector store
│   │   ├── main.py           FastAPI app entrypoint
│   │   ├── agent_router_poc.py  Supervisor/Router with Reflection loop
│   │   └── telemetry.py      LLMOps observability layer
│   ├── data/                 Persistent DB, MOST catalog CSVs
│   ├── docker-compose.yml    Service mesh (backend + demo frontend)
│   └── mission-control.html  Live Mission Control dashboard
├── mva-v2/                   ← MVA Workbook SPA (React 18 + TypeScript)
│   ├── src/
│   │   ├── components/       UI component library
│   │   ├── workers/          Web Worker MVA computation engine
│   │   └── features/         Domain feature modules
│   └── tests/                Unit, functional, e2e, regression, performance
├── deploy/k8s/               Kubernetes manifests
├── docs/                     Architecture diagrams, design specs, runbooks
├── Makefile                  Developer workflow automation
├── CONTRIBUTING.md           Contribution guidelines
└── CHANGELOG.md              Full version history
```

---

## Testing

### Backend (Python)

```bash
# Run all backend tests with coverage
make test-backend

# Run adversarial red-team suite
make test-red-team
```

### Frontend (TypeScript)

```bash
# Run all frontend tests
make test-frontend

# Full test suite (backend + frontend)
make test-all
```

### Verify Cryptographic Audit Log

```bash
# Verify the tamper-evident JSONL audit chain
make verify-audit
```

---

## Kubernetes Deployment

Production manifests are in `deploy/k8s/`. See [docs/k8s-production-runbook.md](ddm-l6/docs/k8s-production-runbook.md) for the full runbook.

```bash
# Apply all K8s manifests
make k8s-apply

# Check rollout status
kubectl rollout status deployment/mva-backend -n mva-platform
```

---

## Contributing

We welcome contributions from the global developer community. Please read [CONTRIBUTING.md](CONTRIBUTING.md) for:

- Local environment setup
- Strict typing and Pydantic v2 rules
- How to propose and implement a new Swarm Agent
- PR review checklist

---

## Documentation

| Document | Description |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Mermaid.js data-flow and K8s infrastructure diagrams |
| [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) | Full local setup, Docker, and testing guide |
| [CHANGELOG.md](CHANGELOG.md) | Semantic versioning history |
| [ddm-l6/docs/agentic-architecture-design.md](ddm-l6/docs/agentic-architecture-design.md) | Deep-dive agentic workflow design |
| [ddm-l6/docs/llmops-telemetry-guide.md](ddm-l6/docs/llmops-telemetry-guide.md) | Telemetry integration guide (Langfuse, OTEL) |
| [ddm-l6/docs/k8s-production-runbook.md](ddm-l6/docs/k8s-production-runbook.md) | Kubernetes production runbook |

---

## License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.

---

<div align="center">

**Built for Industry 5.0. Open to the world.**

[⭐ Star this repo](https://github.com/iiyyll01lin/mva-calculator) · [🐛 Report a Bug](.github/ISSUE_TEMPLATE/bug_report.md) · [💡 Propose a New Agent](.github/ISSUE_TEMPLATE/new_agent_proposal.md) · [🔒 Report a Vulnerability](.github/ISSUE_TEMPLATE/security_vulnerability.md)

</div>
