# Agentic Architecture Design — Enterprise MVA Platform v2.0.0

> **Status:** PoC / RFC  
> **Author:** Principal AI Architect  
> **Date:** 2026-03-27  
> **Baseline:** `extreme-perf` feature branch (merged into `main`)

---

## 1. Executive Summary

This document specifies the transition of the DDM-L6 backend from a conventional request/response API model to a **Supervisor-Routed Agentic Workflow**. The new architecture enables the LLM to autonomously select, invoke, and self-correct tool calls against existing backend services while preserving the zero-blocking, high-concurrency guarantees introduced in the `extreme-perf` update.

---

## 2. Architectural Overview

```
                          ┌──────────────────────────────────────┐
                          │          CLIENT (Frontend / API)      │
                          └────────────────┬─────────────────────┘
                                           │  POST /agent/query
                                           ▼
                          ┌──────────────────────────────────────┐
                          │         SUPERVISOR AGENT              │
                          │  • Intent classification              │
                          │  • Sub-agent routing                  │
                          │  • State aggregation                  │
                          │  • Final response synthesis           │
                          └──┬──────────┬─────────┬──────────────┘
                             │          │         │
              ┌──────────────┘  ┌───────┘  ┌─────┘
              ▼                 ▼           ▼
  ┌──────────────────┐  ┌──────────────┐  ┌──────────────────────┐
  │  LaborTime Agent │  │  BOM Agent   │  │  Simulation Agent    │
  │ (MiniMOST / TMU) │  │ (BOM Mapping)│  │ (Cost / Cycle Time)  │
  └────────┬─────────┘  └──────┬───────┘  └──────────┬───────────┘
           │                   │                      │
           └───────────────────┴──────────────────────┘
                                       │
                          ┌────────────▼────────────┐
                          │   TOOL EXECUTOR LAYER    │
                          │  (async HTTP / in-proc)  │
                          │  • calc_minimost_tmu()   │
                          │  • lookup_bom_item()     │
                          │  • run_simulation()      │
                          │  • get_labor_standards() │
                          └─────────────────────────┘
                                       │
                          ┌────────────▼────────────┐
                          │  EXISTING FastAPI CORE   │
                          │  (DDM Phase 1 Backend)   │
                          └─────────────────────────┘
```

---

## 3. Core Design Patterns

### 3.1 Supervisor / Router Pattern

The **Supervisor Agent** is the single entry point for all agentic requests. It:

1. Receives a natural-language query plus an optional structured context payload.
2. Classifies intent via an LLM call using a **structured output schema** (`RouterDecision`).
3. Dispatches to the appropriate **Specialist Sub-Agent** (or a sequence of them for multi-step tasks).
4. Aggregates sub-agent results and synthesizes a final, coherent response.
5. Writes the full execution trace to the immutable `AgentState` so the session can be resumed or audited.

**Routing is stateful.** The `AgentState` dictionary carries:
- `messages`: full conversation history (system + user + assistant turns)
- `active_agent`: which specialist is currently executing
- `tool_call_results`: accumulated tool call outcomes
- `retry_counts`: per-tool retry counter
- `error_log`: timestamped error records for observability

### 3.2 Specialist Sub-Agents

| Agent | Responsibility | Primary Tools |
|---|---|---|
| `LaborTimeAgent` | MiniMOST motion analysis, TMU → time conversion | `calc_minimost_tmu`, `get_labor_standards` |
| `BomAgent` | BOM item lookup, family/revision validation | `lookup_bom_item`, `get_bom_structure` |
| `SimulationAgent` | Cycle-time simulation, cost sensitivity analysis | `run_simulation`, `get_machine_rates` |

Each sub-agent is a pure `async` function with a typed `AgentState` input/output contract — making them trivially composable and individually testable.

### 3.3 Tool Calling / Function Calling (LLM ↔ Backend)

All interactions between the LLM and backend services use **structured Tool Calling**:

```
LLM generates:           { "name": "calc_minimost_tmu",
                           "arguments": { "sequence_code": "A1B0G1A1B0P3A0",
                                          "skill_level": "Proficient" } }

ToolExecutor validates:  Pydantic model CalcMiniMostTmuInput(...)
                           → raises ValueError on schema mismatch (triggers Reflection)

ToolExecutor calls:      await backend.calc_minimost_tmu(input)  ← async, non-blocking

Result returned to LLM:  CalcMiniMostTmuOutput(tmu=50, time_sec=1.8, ...)
```

**Security controls on Tool Calling:**
- Every tool input is validated against a **Pydantic v2 model** before execution — no raw string interpolation reaches the backend.
- Tool functions operate under the **authenticated user's JWT scope** (passed as a Bearer token in the `AgentState.auth_context`). A tool call cannot escalate privileges.
- The set of tools available to each sub-agent is **statically declared** — the LLM cannot invoke tools outside its allow-list.
- All tool call arguments and responses are **logged** (sanitized of PII) for audit and anomaly detection.

---

## 4. Reflection & Self-Correction Strategy

### 4.1 Motivation

LLM tool calls can fail due to:
- **Schema errors** — LLM hallucinates an argument name or value type.
- **Business logic errors** — upstream service returns 422 / 400 with a structured error body.
- **Transient errors** — network timeout, ephemeral service unavailability.

Without a correction loop the agent silently fails. With an **unbounded** correction loop it can run indefinitely.

### 4.2 Reflection Loop Design

```
                    ┌──────────────────────────┐
                    │  LLM generates Tool Call  │
                    └──────────┬───────────────┘
                               │
                    ┌──────────▼───────────────┐
                    │  ToolExecutor.run(call)   │◄──────────────────────┐
                    └──────────┬───────────────┘                        │
                    success?   │   failure?                              │
              ┌────────────────┘    │                                    │
              ▼                     ▼                        retry < max │
  ┌──────────────────┐  ┌──────────────────────────┐                    │
  │ Append result to │  │  Reflection Step          │                    │
  │ AgentState and   │  │  1. Capture error context  │                   │
  │ continue         │  │  2. Build correction prompt│                   │
  └──────────────────┘  │  3. LLM re-reasons         │                   │
                        │  4. Increment retry_count  ├───────────────────┘
                        └──────────────┬─────────────┘
                                       │  retry >= max_retries
                                       ▼
                        ┌──────────────────────────┐
                        │  Mark tool FAILED in      │
                        │  AgentState.error_log     │
                        │  Return graceful error    │
                        │  response to user         │
                        └──────────────────────────┘
```

**Key design decisions:**

| Parameter | Value | Rationale |
|---|---|---|
| `max_retries` per tool call | **3** | Balances correction opportunity vs. runaway cost |
| Error context injection | Full error message + tool schema | Gives LLM maximum signal for correction |
| Retry back-off | Exponential (`0.5s × 2^n`) | Respects transient upstream rate limits |
| Reflection prompt template | "Your previous call to `{tool}` failed with: `{error}`. Review the schema and try again." | Minimal, focused context |

### 4.3 Error Escalation

If all retries are exhausted, the Supervisor receives a `ToolExecutionFailure` record and may either:
1. **Re-route** to a different sub-agent that can satisfy the request via an alternative tool.
2. **Gracefully degrade** and return a partial result to the user with an explicit explanation of what failed and why.

---

## 5. Concurrency & Performance

The `extreme-perf` baseline demands that no agent operation blocks the event loop.

| Concern | Approach |
|---|---|
| LLM API calls | `asyncio` + `httpx.AsyncClient` with connection pooling |
| Parallel tool calls | `asyncio.gather()` for independent tool invocations within one agent turn |
| Stateful routing | Pure in-memory `AgentState` dict — no synchronous DB reads on the hot path |
| Back-pressure | `asyncio.Semaphore` limits concurrent LLM calls per request |
| Timeout | `asyncio.wait_for(coroutine, timeout=30)` wraps every tool call |

**Fan-out pattern (parallel tool calls):**
When the Supervisor determines that two sub-agents can run independently (e.g., fetch BOM data while simultaneously computing TMU), it fans out:

```python
bom_result, tmu_result = await asyncio.gather(
    bom_agent.run(state),
    labor_agent.run(state),
)
```

---

## 6. State Schema (Reference)

```python
class AgentState(TypedDict):
    session_id:        str          # UUID per user session
    messages:          List[Message] # Full LLM conversation history
    active_agent:      str          # Current specialist agent name
    intent:            str          # Classified intent label
    tool_call_results: List[ToolCallResult]  # Accumulated results
    retry_counts:      Dict[str, int]        # tool_name → attempt count
    error_log:         List[ErrorRecord]     # Timestamped failures
    auth_context:      AuthContext           # JWT scope for tool security
    created_at:        datetime
    updated_at:        datetime
```

---

## 7. Extension Points

- **New Specialist Agent**: add a new async function + tool definitions + update the router's intent-to-agent map.
- **Persistent State**: swap the in-memory `AgentState` for a Redis-backed store without changing agent logic.
- **Multi-step Planning**: extend the Supervisor with a planning step (ReAct / Plan-and-Execute) before dispatching.
- **Human-in-the-Loop**: insert an approval gate in the `ToolExecutor` for high-impact actions (e.g., publishing SOPs).

---

## 8. Files Produced

| File | Purpose |
|---|---|
| `docs/agentic-architecture-design.md` | This document |
| `backend/agent_router_poc.py` | Proof-of-Concept implementation |
