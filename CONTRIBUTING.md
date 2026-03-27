# Contributing to the Enterprise MVA Platform

Thank you for your interest in contributing. This document covers everything you need to get from zero to a merged pull request — including local environment setup, our strict typing and Pydantic v2 rules, and the process for adding a new Swarm Agent.

---

## Table of Contents

1. [Code of Conduct](#1-code-of-conduct)
2. [Local Environment Setup](#2-local-environment-setup)
3. [Project Layout Quick Reference](#3-project-layout-quick-reference)
4. [Coding Standards](#4-coding-standards)
   - 4.1 [Python — Typing & Pydantic v2](#41-python--typing--pydantic-v2)
   - 4.2 [TypeScript — React Frontend](#42-typescript--react-frontend)
   - 4.3 [Async Rules](#43-async-rules)
   - 4.4 [Security Requirements](#44-security-requirements)
5. [How to Add a New Swarm Agent](#5-how-to-add-a-new-swarm-agent)
6. [Testing Requirements](#6-testing-requirements)
7. [Pull Request Process](#7-pull-request-process)
8. [Commit Message Convention](#8-commit-message-convention)

---

## 1. Code of Conduct

This project follows the [Contributor Covenant v2.1](https://www.contributor-covenant.org/version/2/1/code_of_conduct/). By participating, you agree to uphold a welcoming, harassment-free environment for everyone, regardless of background, identity, or experience level.

---

## 2. Local Environment Setup

### Prerequisites

| Tool | Minimum Version | Notes |
|---|---|---|
| Docker | 24.x | Required for all services |
| Docker Compose | v2.x | Included with Docker Desktop |
| Make | 3.81+ | GNU Make — use `brew install make` on macOS |
| Git | 2.40+ | |
| Python | 3.11+ | _Only_ needed for host-side IDE typing support; all execution is containerised |
| Node.js | 20 LTS | _Only_ needed for `mva-v2` IDE support; all builds are containerised |

### First-time Setup

```bash
# 1. Fork and clone
git clone https://github.com/YOUR_USERNAME/mva-calculator.git
cd mva-calculator

# 2. Create your feature branch
git checkout -b feat/your-feature-name

# 3. Build all Docker images
make install

# 4. Start the full stack locally
make run-dev
# → Mission Control: http://localhost:9080/mission-control.html
# → Backend API docs: http://localhost:8010/docs
```

### Configure Environment Variables (Optional)

Copy the example env file for the DDM-L6 backend:

```bash
cp ddm-l6/.env.example ddm-l6/.env
```

Then edit `ddm-l6/.env` to add your LLM API key (required for non-stub agent runs):

```dotenv
# LLM — at least one required for real inference
OPENAI_API_KEY=sk-...
ANTHROPIC_API_KEY=...

# Telemetry — optional but recommended
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...

# Cyber-Physical — optional, enables real robot dispatch
MQTT_BROKER_URL=mqtt://localhost:1883
ROS2_BRIDGE_WS_URL=ws://ros2-bridge:9090
```

> **Offline / test mode:** All agents have stub fallbacks. With no API key configured, agents return deterministic mock output and all tests pass without any external dependency.

---

## 3. Project Layout Quick Reference

```
ddm-l6/backend/
├── agent_router_poc.py      ← Supervisor/Router + Reflection loop
├── main.py                  ← FastAPI app entrypoint and routes
├── telemetry.py             ← LLMOps observability (NEVER import from agents)
├── memory_store.py          ← Stateful memory (NEVER import from main.py)
├── llm_client.py            ← LLM abstraction (local/cloud tier routing)
├── agents/                  ← ★ This is where new Swarm Agents live
│   ├── debate_room.py       Multi-Agent Debate & Consensus Engine
│   ├── temporal_analyst.py  Temporal RAG analysis sub-agent
│   ├── vision_inspector.py  VLM visual anomaly detection
│   └── simulation_validator.py
├── data_ops/                ← Agentic ETL pipeline
├── eval/                    ← LLM-as-a-Judge + Red Team suite
├── events/                  ← IoT Watchdog async event loop
├── memory/                  ← Alignment store + context compression
├── robotics/                ← ROS2 bridge + SITL simulator
├── security/                ← Cryptographic provenance + ToolGuard sandbox
└── tools/                   ← Shared tools: Temporal RAG vector store
```

---

## 4. Coding Standards

### 4.1 Python — Typing & Pydantic v2

**All Python code must pass `mypy --strict`.** There are zero exceptions.

#### Mandatory rules

```python
# ✅ DO — fully annotated, Pydantic v2 model
from pydantic import BaseModel, Field

class MyAgentOutput(BaseModel):
    model_config = {"strict": True}   # ← always set this on agent output models

    result_summary: str = Field(..., description="Human-readable synthesis.")
    confidence:     float = Field(..., ge=0.0, le=1.0)
    trace_id:       str   = Field(default_factory=lambda: str(uuid.uuid4()))

# ❌ DON'T — untyped dict, no schema validation
def run_agent(state):           # missing type annotations
    return {"result": "..."}    # naked dict with no validation
```

#### Pydantic v2 specifics

| Rule | Example |
|---|---|
| Use `model_config = {"strict": True}` on all output schemas | Prevents silent type coercion |
| Use `Field(..., description=...)` on every model field | Required for auto-generated API docs |
| Use `model_validate()` for deserialization, never `**dict` unpacking | Triggers validators correctly |
| Prefer `field_validator` / `model_validator` over raw `__init__` overrides | Maintains serialization parity |

#### Import discipline

The module dependency graph is strictly enforced:

```
main.py / routes
    └── agent_router_poc.py
            ├── agents/*.py
            │       └── tools/*, memory/*, llm_client.py
            ├── data_ops/*.py
            ├── telemetry.py   ← leaf node, no upstream imports
            └── memory_store.py ← imports only telemetry.py
```

> **Rule:** Never create circular imports. `telemetry.py` and `memory_store.py` are leaf nodes. If you need to import a heavy module lazily, use the pattern already established in `debate_room.py`:
> ```python
> # Inside a coroutine, not at module top-level:
> from agents.temporal_analyst import TemporalAnalystAgent
> ```

### 4.2 TypeScript — React Frontend

- **TypeScript `strict` mode** is enabled in `mva-v2/tsconfig.json`. No `any` casts without an explanatory comment.
- All component props must have explicit interface or type definitions.
- Use `useDeferredValue` + Web Workers for any computation > 5 ms to keep the UI thread responsive.
- New UI state must go through the established state management pattern in `mva-v2/src/state/`.

### 4.3 Async Rules

All Python I/O **must** be async. The FastAPI event loop is never blocked.

```python
# ✅ DO — async, non-blocking
async def fetch_data(path: str) -> str:
    async with aiofiles.open(path) as f:
        return await f.read()

# ❌ DON'T — blocking I/O in async context
async def fetch_data(path: str) -> str:
    return open(path).read()    # blocks the event loop
```

- Use `asyncio.get_event_loop().run_in_executor(None, ...)` for unavoidable blocking calls.
- Use `asyncio.gather()` for fan-out concurrency within a single request.
- Use `asyncio.create_task()` for fire-and-forget background work (e.g., IoT Watchdog → DebateRoom).
- Never use `asyncio.sleep(0)` as a yield-control hack; use `await asyncio.to_thread(...)` instead.

### 4.4 Security Requirements

Every contribution that touches agent I/O, tool dispatch, or user-facing routes **must** follow these rules:

| Requirement | Implementation |
|---|---|
| **All tool arguments validated** | Use `ToolGuard` from `security/tool_sandbox.py` |
| **All LLM outputs schema-validated** | `model_validate()` before trusting any field |
| **Sensitive actions gate on HITL** | Use `PendingAction` + approval queue — never self-approve |
| **Robot commands are Ed25519-signed** | Use `sign_payload()` from `security/provenance.py` |
| **No secrets in code or logs** | Use environment variables; use `SecretStr` in Pydantic models |
| **No `eval()`, `exec()`, or `subprocess` in agent code** | Use the ToolGuard allowlist |
| **Input from LLMs is always untrusted** | Validate and sanitize before any downstream call |

> **If you find a security vulnerability,** please do **not** open a public issue. Use the [Security Vulnerability template](.github/ISSUE_TEMPLATE/security_vulnerability.md) or email the maintainers directly.

---

## 5. How to Add a New Swarm Agent

Follow these six steps to add a new specialized Agent to the Multi-Agent Swarm.

### Step 1 — Create the agent module

Create `ddm-l6/backend/agents/my_new_agent.py`. Use the following skeleton:

```python
"""
backend/agents/my_new_agent.py
────────────────────────────────────────────────────────────────────────────────
MyNewAgent — One-line description of what this agent does.

Design principles
─────────────────
• Fully async.
• Pydantic v2 output schema — strict mode enabled.
• Instrumented with agent_span for Mission Control visibility.
• Ed25519-signed output appended to TamperEvidentAuditLog.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from telemetry import agent_span, LlmUsage, estimate_tokens
from llm_client import call_llm, ChatMessage


class MyNewAgentOutput(BaseModel):
    model_config = {"strict": True}

    summary:  str   = Field(..., description="Agent synthesis output.")
    trace_id: str   = Field(default_factory=lambda: str(uuid.uuid4()))


async def run_my_new_agent(context: dict, trace_id: str) -> MyNewAgentOutput:
    async with agent_span(
        span_name="my_new_agent",
        span_type="agent",
        agent_name="MyNewAgent",
        trace_id=trace_id,
    ) as span:
        messages = [
            ChatMessage(role="system", content="You are MyNewAgent. ..."),
            ChatMessage(role="user",   content=str(context)),
        ]
        result = await call_llm(messages, tier="local")
        span.raw_output = result.content
        span.token_usage = LlmUsage.from_counts(
            prompt_tokens=estimate_tokens(str(messages)),
            completion_tokens=estimate_tokens(result.content),
        )
        return MyNewAgentOutput(summary=result.content, trace_id=trace_id)
```

### Step 2 — Register the agent name

Add your agent to the `AgentName` enum in `agent_router_poc.py`:

```python
class AgentName(str, Enum):
    # ... existing entries ...
    MY_NEW_AGENT = "MyNewAgent"
```

### Step 3 — Wire into the DebateRoom (if applicable)

If your agent participates in the Swarm Debate, add a debate turn in `agents/debate_room.py`:

```python
async def run_my_new_agent_turn(context: DebateContext) -> DebateTurn:
    from agents.my_new_agent import run_my_new_agent
    output = await run_my_new_agent(context.to_dict(), trace_id=context.trace_id)
    return DebateTurn(agent_name="MyNewAgent", content=output.summary)
```

### Step 4 — Add routing logic (if standalone)

If your agent handles direct user queries (not via DebateRoom), add a routing case in `agent_router_poc.py`'s `SupervisorAgent._route()` method.

### Step 5 — Write tests

Create `ddm-l6/backend/tests_most/test_my_new_agent.py`:

```python
import pytest
from agents.my_new_agent import run_my_new_agent

@pytest.mark.asyncio
async def test_my_new_agent_returns_output():
    result = await run_my_new_agent(
        context={"machine_id": "M001", "yield_rate": 85.0},
        trace_id="test-trace-001",
    )
    assert result.summary
    assert result.trace_id == "test-trace-001"
```

Run: `make test-backend`

### Step 6 — Document your agent

Add a row to the **Core Features Matrix** table in `README.md` and update `docs/architecture.md` if the data-flow diagram changes.

---

## 6. Testing Requirements

All PRs **must** pass the full test suite before review:

```bash
make test-all        # Backend pytest + frontend vitest
make verify-audit    # Cryptographic audit chain integrity
```

**Coverage thresholds** (enforced in CI):
- Backend: ≥ 80% line coverage
- Frontend: ≥ 75% statement coverage

**New features** must include:
- At least one happy-path unit test
- At least one error/edge-case test
- If touching security code: at least one adversarial test case added to `eval/red_team.py`

---

## 7. Pull Request Process

1. **Ensure your branch is up to date** with `main` before opening a PR:
   ```bash
   git fetch origin && git rebase origin/main
   ```

2. **All CI checks must pass.** We do not merge PRs with failing tests or type errors.

3. **PR title must follow the [Commit Message Convention](#8-commit-message-convention).**

4. **Fill out the PR template completely.** Incomplete PRs will be closed without review.

5. **One approval** from a maintainer is required to merge.

6. **Squash-merge** is preferred for feature branches. Maintainers will squash on merge.

---

## 8. Commit Message Convention

We follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

```
<type>(<scope>): <short description>

[optional body]

[optional footer: BREAKING CHANGE, Closes #issue]
```

| Type | When to use |
|---|---|
| `feat` | A new feature or capability |
| `fix` | A bug fix |
| `perf` | A performance improvement |
| `refactor` | Code change that neither adds a feature nor fixes a bug |
| `test` | Adding or updating tests |
| `docs` | Documentation only changes |
| `chore` | Build process, dependency updates, tooling |
| `security` | Security fix or hardening |

**Scope examples:** `agents`, `debate`, `etl`, `ros2`, `telemetry`, `frontend`, `k8s`, `ci`

```bash
# Good examples
feat(agents): add PredictiveMaintenanceAgent to swarm
fix(debate): prevent ConsensusJudge from exceeding MAX_TURNS_CEILING
security(provenance): rotate Ed25519 key from env-supplied seed
docs(architecture): update K8s diagram for Redis Cluster

# Bad examples
fixed stuff
update
WIP
```
