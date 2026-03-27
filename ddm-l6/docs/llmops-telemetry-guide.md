# LLMOps Telemetry Guide — MVA Platform Agentic Workflow

**Target audience:** DevOps / SRE / MLOps engineers responsible for monitoring
the MVA Platform's autonomous agent backend in production.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Span Data Model](#2-span-data-model)
3. [Environment Configuration](#3-environment-configuration)
4. [Emission Backends](#4-emission-backends)
   - 4.1 [Structured Logger (always active)](#41-structured-logger-always-active)
   - 4.2 [JSONL File + Loki/Promtail → Grafana](#42-jsonl-file--lokipromtail--grafana)
   - 4.3 [Langfuse (dedicated LLM observability)](#43-langfuse-dedicated-llm-observability)
   - 4.4 [OpenTelemetry OTLP (Tempo / Jaeger)](#44-opentelemetry-otlp-tempo--jaeger)
5. [Grafana Dashboard Setup](#5-grafana-dashboard-setup)
6. [Key Metrics & Alerts](#6-key-metrics--alerts)
7. [Hallucination Rate Monitoring](#7-hallucination-rate-monitoring)
8. [Cost Tracking](#8-cost-tracking)
9. [Connecting to Langfuse Cloud](#9-connecting-to-langfuse-cloud)
10. [Connecting to a Self-Hosted Stack](#10-connecting-to-a-self-hosted-stack)
11. [Production Hardening Checklist](#11-production-hardening-checklist)

---

## 1. Architecture Overview

Every LLM call and tool execution in the agent workflow is wrapped by the
`telemetry.agent_span()` context manager in `backend/telemetry.py`.
Each completed step emits an **`AgentSpanRecord`** — a fully-serialisable
dataclass with timing, token usage, and the exact prompt/output payloads.

```
User Request
    │
    ▼
supervisor_agent          ← agent span  (span_type = "agent")
    │
    ├── _llm_route        ← agent span  (span_type = "routing")
    │
    ├── labor_time_agent  ← agent span  (span_type = "agent")
    │       └── execute_tool/calc_minimost_tmu
    │                     ← agent span  (span_type = "tool_exec")
    │               └── reflect/calc_minimost_tmu   [on failure]
    │                     ← agent span  (span_type = "reflection")
    │
    ├── bom_agent         ← agent span  (span_type = "agent")
    │       └── execute_tool/lookup_bom_item
    │
    └── simulation_agent  ← agent span  (span_type = "agent")
            └── execute_tool/run_simulation
```

All spans sharing the same `trace_id` (= `session_id`) belong to one user
request and can be stitched into a complete execution graph.

Emission is **fire-and-forget** — `asyncio.create_task()` schedules each
backend write in the background so the hot path is never blocked.

---

## 2. Span Data Model

Each emitted span is an `AgentSpanRecord` with these fields:

| Field | Type | Description |
|---|---|---|
| `span_id` | UUID | Unique span identifier |
| `trace_id` | str | `session_id` — groups all spans for one request |
| `span_name` | str | e.g. `_llm_route`, `execute_tool/calc_minimost_tmu` |
| `span_type` | enum | `routing` \| `tool_exec` \| `reflection` \| `agent` |
| `agent_name` | str | e.g. `SupervisorAgent`, `LaborTimeAgent` |
| `started_at` | ISO-8601 | UTC wall-clock start |
| `ended_at` | ISO-8601 | UTC wall-clock end |
| `duration_ms` | float | Elapsed milliseconds |
| `prompt` | str \| null | **Full prompt / args sent to LLM or tool** |
| `raw_output` | str \| null | **Full raw LLM completion or tool result** |
| `token_usage.prompt_tokens` | int | Tokens in the prompt |
| `token_usage.completion_tokens` | int | Tokens in the completion |
| `token_usage.total_tokens` | int | Sum |
| `token_usage.estimated_cost_usd` | float | USD cost at configured rates |
| `tool_name` | str \| null | Tool name for `tool_exec`/`reflection` spans |
| `tool_attempt` | int \| null | 1-based retry counter |
| `tool_success` | bool \| null | Whether the tool call succeeded |
| `error` | str \| null | Exception message if the step failed |
| `metadata` | dict | Free-form extra context (intent, target_agents, …) |

---

## 3. Environment Configuration

Set these environment variables before starting the backend container.
All are optional; the telemetry layer degrades gracefully when unset.

```bash
# ── JSONL file logging ────────────────────────────────────────────────────────
# Absolute path inside the container; mount a host volume for persistence.
MVA_TELEMETRY_LOG=/var/log/mva/telemetry.jsonl

# ── Langfuse ──────────────────────────────────────────────────────────────────
LANGFUSE_HOST=https://cloud.langfuse.com     # or your self-hosted URL
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...

# ── OpenTelemetry OTLP ────────────────────────────────────────────────────────
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317

# ── Cost model (USD per 1 000 tokens) ─────────────────────────────────────────
LLM_COST_PER_1K_PROMPT=0.001
LLM_COST_PER_1K_COMPLETION=0.002

# ── In-memory buffer size ─────────────────────────────────────────────────────
MVA_TELEMETRY_BUFFER=1000
```

In `docker-compose.yml`:

```yaml
services:
  backend:
    environment:
      MVA_TELEMETRY_LOG: /var/log/mva/telemetry.jsonl
      LANGFUSE_HOST:      ${LANGFUSE_HOST}
      LANGFUSE_PUBLIC_KEY: ${LANGFUSE_PUBLIC_KEY}
      LANGFUSE_SECRET_KEY: ${LANGFUSE_SECRET_KEY}
    volumes:
      - telemetry_logs:/var/log/mva

volumes:
  telemetry_logs:
```

---

## 4. Emission Backends

### 4.1 Structured Logger (always active)

Every span writes one `INFO`-level log line in this machine-readable format:

```
INFO  backend.telemetry  SPAN span_id=<uuid> trace_id=<uuid> name=_llm_route
      type=routing agent=SupervisorAgent duration_ms=23.4
      prompt_tokens=18 completion_tokens=12 cost_usd=0.000042
      tool=None attempt=None success=None error=None
```

Configure `uvicorn` to emit JSON logs (`--log-config log_config.json`) and
ship them to your existing log aggregator (CloudWatch, Stackdriver, Datadog).

### 4.2 JSONL File + Loki/Promtail → Grafana

**Step 1 — Mount a host volume** (see §3 docker-compose snippet).

**Step 2 — Configure Promtail** to tail the file:

```yaml
# promtail-config.yaml
scrape_configs:
  - job_name: mva_telemetry
    static_configs:
      - targets: [localhost]
        labels:
          job:        mva_agent
          __path__:   /var/log/mva/*.jsonl

    pipeline_stages:
      - json:
          expressions:
            span_type:   span_type
            agent_name:  agent_name
            duration_ms: duration_ms
            total_tokens: token_usage.total_tokens
            cost_usd:    token_usage.estimated_cost_usd
            error:       error
            trace_id:    trace_id
      - labels:
          span_type:
          agent_name:
          error:
      - timestamp:
          source:  started_at
          format:  RFC3339Nano
```

**Step 3 — Add Loki as a Grafana data source** (URL: `http://loki:3100`).

**Step 4 — Import the panel queries** from §5.

### 4.3 Langfuse (dedicated LLM observability)

Langfuse is the recommended platform for LLM-specific insights (prompt
versioning, human feedback, hallucination tagging). The telemetry layer POSTs
to `/api/public/ingestion` using Langfuse's batch span-create API.

Each span is mapped as:

| `AgentSpanRecord` field | Langfuse field |
|---|---|
| `trace_id` | `traceId` |
| `span_name` | `name` |
| `started_at / ended_at` | `startTime / endTime` |
| `prompt` | `input` |
| `raw_output` | `output` |
| `token_usage` | `usage` (prompt / completion / total) |
| `metadata` | `metadata` |
| `error` | `statusMessage` + level `ERROR` |

After connecting (§9), navigate to **Traces** to see full request trees,
and to **Generations** to compare prompt quality across iterations.

### 4.4 OpenTelemetry OTLP (Tempo / Jaeger)

Install the optional packages:

```bash
pip install opentelemetry-api opentelemetry-sdk \
            opentelemetry-exporter-otlp-proto-grpc
```

Uncomment the three lines in `requirements.txt` and set
`OTEL_EXPORTER_OTLP_ENDPOINT`. The backend will automatically initialise
a `BatchSpanProcessor` and export spans via gRPC.

In Grafana: add **Tempo** as a data source and use **Explore → TraceQL**:

```
{ .mva.span_type = "reflection" }
```

---

## 5. Grafana Dashboard Setup

### Recommended panels (Loki data source)

#### P50/P95 LLM Routing Latency

```logql
quantile_over_time(0.95,
  {job="mva_agent"} | json | span_name="_llm_route"
    | unwrap duration_ms [5m]
) by (agent_name)
```

#### Tool Execution Success Rate

```logql
sum by (tool_name) (
  rate({job="mva_agent"} | json | span_type="tool_exec"
       | tool_success="True" [5m])
)
/
sum by (tool_name) (
  rate({job="mva_agent"} | json | span_type="tool_exec" [5m])
)
```

#### Reflection Loop Rate (proxy for LLM argument errors)

```logql
sum(rate({job="mva_agent"} | json | span_type="reflection" [5m]))
/
sum(rate({job="mva_agent"} | json | span_type="tool_exec"  [5m]))
```

> **Interpretation:** A value above 0.2 means >20 % of tool calls require
> at least one self-correction — investigate your prompt templates or schema
> definitions.

#### Estimated Daily LLM Cost (USD)

```logql
sum(
  sum_over_time(
    {job="mva_agent"} | json
      | unwrap cost_usd [1d]
  )
)
```

#### Error Rate by Agent

```logql
sum by (agent_name) (
  rate({job="mva_agent"} | json | error != "" [5m])
)
```

---

## 6. Key Metrics & Alerts

| Alert | Condition | Severity |
|---|---|---|
| High routing latency | `_llm_route` P95 > 3 000 ms | Warning |
| LLM routing latency critical | `_llm_route` P95 > 8 000 ms | Critical |
| Elevated reflection rate | reflection/tool_exec ratio > 0.3 | Warning |
| Tool failure rate | tool_success=False rate > 5 % | Warning |
| All-retries exhausted | `ToolExecutionFailure` rate > 1 % | Critical |
| Hourly cost spike | `estimated_cost_usd` > $X in 1 h | Warning |
| Zero spans emitted | No spans for 5 min (dead-man switch) | Critical |

Configure Grafana alerts via **Alerting → Alert rules**, using the LogQL
queries above as conditions, and route to PagerDuty / Slack via
**Contact points**.

---

## 7. Hallucination Rate Monitoring

The telemetry layer stores the **full verbatim prompt and raw LLM output** in
every `routing` and `reflection` span (`prompt` and `raw_output` fields).
This enables two complementary monitoring approaches:

### 7.1 Structural Hallucination (schema violations)

A schema validation failure in `execute_tool_with_reflection` —
`ValidationError` on the Pydantic input model — means the LLM generated
arguments that do not match the required schema.  This is logged as a
`reflection` span with `error` set.

**Metric:** reflection rate per tool (§5 panel above).
**Dashboard annotation:** label each reflection span's `metadata.original_error`
field to distinguish schema errors from runtime errors.

### 7.2 Semantic Hallucination (LLM-as-Judge pipeline)

For deeper hallucination detection, post every `routing` span to a secondary
LLM judge:

```
┌─────────────┐      ┌──────────────────────┐      ┌──────────────┐
│  JSONL log  │─────▶│  Vector/Fluentd      │─────▶│  LLM Judge   │
│  (prompts + │      │  tail + filter       │      │  GPT-4 /     │
│  outputs)   │      │  span_type=routing   │      │  Claude      │
└─────────────┘      └──────────────────────┘      └──────┬───────┘
                                                          │
                                              ┌───────────▼───────────┐
                                              │  Score (0–1)          │
                                              │  POST to Langfuse     │
                                              │  as "score" event     │
                                              └───────────────────────┘
```

The judge prompt:

```
You are an audit agent. Given the INPUT query and the ROUTING DECISION,
rate whether the decision is correct (1.0) or hallucinated (0.0).

INPUT: {span.prompt}
ROUTING DECISION: {span.raw_output}

Respond with JSON: {"score": <float 0-1>, "reason": "<one sentence>"}
```

Post results back to Langfuse via the Scores API
(`POST /api/public/scores`), keyed on `trace_id`.  Langfuse will display
rolling hallucination scores on the **Scores** tab and allows setting a
threshold alert.

---

## 8. Cost Tracking

Each span with `token_usage` set contributes to cost tracking.
The `estimated_cost_usd` field uses:

```
cost = (prompt_tokens / 1000) × LLM_COST_PER_1K_PROMPT
     + (completion_tokens / 1000) × LLM_COST_PER_1K_COMPLETION
```

Override the rates per LLM model via environment variables (§3).

> **Note:** The current stub uses a 4-chars-per-token heuristic.
> For accurate billing, replace `estimate_tokens()` in `telemetry.py` with
> `tiktoken.encoding_for_model("gpt-4o").encode(text)` or the appropriate
> tokeniser for your LLM provider.

### Cost breakdown by span type

| Span type | What it measures |
|---|---|
| `routing` | Supervisor classification calls |
| `reflection` | Self-correction LLM calls (avoidable cost) |
| `agent` | Not an LLM call; no token usage expected |
| `tool_exec` | Not an LLM call; no token usage expected |

**Grafana panel — weekly token spend by type:**

```logql
sum by (span_type) (
  sum_over_time(
    {job="mva_agent"} | json | span_type =~ "routing|reflection"
      | unwrap total_tokens [7d]
  )
)
```

---

## 9. Connecting to Langfuse Cloud

1. Sign in to [cloud.langfuse.com](https://cloud.langfuse.com) and create a
   project named `mva-agent`.
2. Go to **Settings → API Keys** and copy your Public and Secret keys.
3. Set the env vars in your `.env` file (never commit secrets to git):

   ```env
   LANGFUSE_HOST=https://cloud.langfuse.com
   LANGFUSE_PUBLIC_KEY=pk-lf-xxxxxxxxxxxxxxxx
   LANGFUSE_SECRET_KEY=sk-lf-xxxxxxxxxxxxxxxx
   ```

4. Rebuild and restart the backend container:

   ```bash
   docker compose up -d --build backend
   ```

5. Send a test request to `/agent/query`.  Within seconds, the trace appears
   in Langfuse under **Traces** with all spans nested under the `session_id`.

6. In Langfuse **Dashboards**, pin:
   - **P95 latency** by span name
   - **Token usage** by model
   - **Error rate** timeline
   - **Reflection rate** (custom metric via Scores API)

---

## 10. Connecting to a Self-Hosted Stack

The recommended self-hosted observability stack for this platform:

```yaml
# docker-compose.observability.yml
services:

  loki:
    image: grafana/loki:2.9.0
    ports: ["3100:3100"]
    volumes: [loki_data:/loki]
    command: -config.file=/etc/loki/local-config.yaml

  promtail:
    image: grafana/promtail:2.9.0
    volumes:
      - telemetry_logs:/var/log/mva:ro
      - ./promtail-config.yaml:/etc/promtail/config.yml
    command: -config.file=/etc/promtail/config.yml

  tempo:
    image: grafana/tempo:2.3.0
    ports: ["4317:4317", "3200:3200"]  # OTLP gRPC + HTTP query
    volumes: [tempo_data:/tmp/tempo]

  grafana:
    image: grafana/grafana:10.2.0
    ports: ["3000:3000"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: ${GRAFANA_PASSWORD}
    volumes:
      - grafana_data:/var/lib/grafana
      - ./grafana-provisioning:/etc/grafana/provisioning

  langfuse:
    image: langfuse/langfuse:2
    ports: ["3001:3000"]
    environment:
      DATABASE_URL:    postgresql://langfuse:${PG_PASSWORD}@postgres:5432/langfuse
      NEXTAUTH_SECRET: ${NEXTAUTH_SECRET}
      SALT:            ${LANGFUSE_SALT}
    depends_on: [postgres]

  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_DB:       langfuse
      POSTGRES_USER:     langfuse
      POSTGRES_PASSWORD: ${PG_PASSWORD}
    volumes: [pg_data:/var/lib/postgresql/data]

volumes:
  loki_data:
  tempo_data:
  grafana_data:
  telemetry_logs:
  pg_data:
```

Start the stack:

```bash
docker compose -f docker-compose.yml \
               -f docker-compose.observability.yml \
               up -d
```

Configure Grafana data sources under
`grafana-provisioning/datasources/datasources.yaml`:

```yaml
apiVersion: 1
datasources:
  - name: Loki
    type: loki
    url:  http://loki:3100
    isDefault: true
  - name: Tempo
    type: tempo
    url:  http://tempo:3200
    jsonData:
      tracesToLogsV2:
        datasourceUid: loki
        filterByTraceID: true
```

---

## 11. Production Hardening Checklist

- [ ] **Redact PII from prompts** — before the span is emitted, apply a regex
  to strip personal data from `span.prompt` and `span.raw_output` if the agent
  processes user-supplied content.  Add a `_redact()` helper in `telemetry.py`.
- [ ] **Replace `estimate_tokens()`** with an accurate tokeniser (`tiktoken`
  or provider SDK) for correct cost billing.
- [ ] **Replace the stub LLM calls** in `_llm_route` and `_reflect_and_correct`
  with real API calls; populate `span.token_usage` from the provider's response
  `usage` object (never estimate when real counts are available).
- [ ] **Rotate secrets** — `LANGFUSE_SECRET_KEY` and `OTEL_ENDPOINT` must live
  in a secrets manager (Vault, AWS Secrets Manager, Kubernetes Secrets), not
  plain env vars or `.env` files in production.
- [ ] **Cap payload size** — add a `MAX_PAYLOAD_BYTES = 65536` guard in
  `_emit_to_langfuse()` and truncate `span.prompt` / `span.raw_output` before
  transmission to avoid exceeding Langfuse's ingestion limits.
- [ ] **Dead-man alert** — set a Grafana alert that fires when no spans have
  been received for 5 minutes; this catches silent failures in the emission
  pipeline.
- [ ] **Dashboard version-lock** — export Grafana dashboards as JSON and commit
  them to `docs/grafana-dashboards/` so the observability setup is reproducible.
- [ ] **Sampling for high-traffic** — for > 100 req/s, configure
  `opentelemetry-sdk`'s `TraceIdRatioBased(0.1)` sampler to keep 10 % of traces
  and reduce Tempo storage costs; keep 100 % of error traces.
