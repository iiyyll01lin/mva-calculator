"""
backend/telemetry.py
────────────────────────────────────────────────────────────────────────────────
LLMOps Telemetry Layer — Enterprise MVA Platform v2.0.0

Instruments the agentic workflow with structured, async-safe observability:
  • Timing  : wall-clock start/end and duration_ms per span
  • Prompts : exact prompt sent to the LLM, raw completion output
  • Tokens  : prompt_tokens, completion_tokens, total_tokens, estimated_cost_usd
  • Graph   : span_type tags the step as "routing" | "tool_exec" | "reflection" | "agent"

Emission backends (all fire-and-forget — never block the asyncio event loop):
  1. Structured JSON log line via Python logger  (always active)
  2. JSONL append to MVA_TELEMETRY_LOG file      (set env var to enable)
  3. Langfuse /api/public/ingestion              (set LANGFUSE_* env vars)
  4. OpenTelemetry OTLP spans                    (set OTEL_EXPORTER_OTLP_ENDPOINT +
                                                  install opentelemetry packages)

Usage
-----
    from telemetry import agent_span, LlmUsage, estimate_tokens

    async with agent_span(
        span_name  = "_llm_route",
        span_type  = "routing",
        agent_name = "SupervisorAgent",
        trace_id   = state["session_id"],
    ) as span:
        span.prompt      = serialized_prompt
        decision         = await real_llm_call(prompt)
        span.raw_output  = decision.model_dump_json()
        span.token_usage = LlmUsage.from_counts(
            prompt_tokens     = estimate_tokens(serialized_prompt),
            completion_tokens = estimate_tokens(span.raw_output),
        )
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, List, Optional

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Configuration — override via environment variables
# ────────────────────────────────────────────────────────────────────────────

#: Absolute path to a JSONL file where every span is appended.
#: Left empty to disable file logging.
TELEMETRY_LOG_FILE: str = os.environ.get("MVA_TELEMETRY_LOG", "")

#: Langfuse host, e.g. "https://cloud.langfuse.com" or self-hosted URL.
LANGFUSE_HOST: str       = os.environ.get("LANGFUSE_HOST", "")
LANGFUSE_PUBLIC_KEY: str = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY: str = os.environ.get("LANGFUSE_SECRET_KEY", "")

#: OpenTelemetry OTLP collector endpoint, e.g. "http://otel-collector:4317".
OTEL_ENDPOINT: str = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "")

#: USD cost per 1 000 tokens — adjust to match your LLM contract pricing.
LLM_COST_PER_1K_PROMPT: float     = float(os.environ.get("LLM_COST_PER_1K_PROMPT",     "0.001"))
LLM_COST_PER_1K_COMPLETION: float = float(os.environ.get("LLM_COST_PER_1K_COMPLETION", "0.002"))

#: Maximum spans held in the in-memory ring buffer (for /metrics scraping).
SPAN_BUFFER_MAX: int = int(os.environ.get("MVA_TELEMETRY_BUFFER", "1000"))


# ────────────────────────────────────────────────────────────────────────────
# Data Models
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class LlmUsage:
    """Token consumption and estimated cost for a single LLM call."""
    prompt_tokens:      int   = 0
    completion_tokens:  int   = 0
    total_tokens:       int   = 0
    estimated_cost_usd: float = 0.0

    @classmethod
    def from_counts(cls, prompt_tokens: int, completion_tokens: int) -> "LlmUsage":
        """Construct from raw token counts; computes cost automatically."""
        total = prompt_tokens + completion_tokens
        cost = (
            (prompt_tokens     / 1_000) * LLM_COST_PER_1K_PROMPT
            + (completion_tokens / 1_000) * LLM_COST_PER_1K_COMPLETION
        )
        return cls(
            prompt_tokens      = prompt_tokens,
            completion_tokens  = completion_tokens,
            total_tokens       = total,
            estimated_cost_usd = round(cost, 8),
        )


class SpanContext:
    """
    Mutable annotation object yielded by :func:`agent_span`.

    The instrumented coroutine writes to these attributes during execution;
    they are captured when the context manager exits and baked into the
    immutable ``AgentSpanRecord`` that gets emitted.
    """

    __slots__ = ("prompt", "raw_output", "token_usage", "error", "metadata")

    def __init__(self) -> None:
        self.prompt:      Optional[str]      = None
        self.raw_output:  Optional[str]      = None
        self.token_usage: Optional[LlmUsage] = None
        self.error:       Optional[str]      = None
        self.metadata:    Dict[str, Any]     = {}


@dataclass
class AgentSpanRecord:
    """
    Immutable, fully-serialisable record of one instrumented execution step.

    A *span* maps to a single logical operation:
      - one LLM router call
      - one tool execution attempt
      - one reflection/self-correction cycle
      - one sub-agent invocation

    The ``trace_id`` == ``session_id`` from ``AgentState``, so all spans
    belonging to one user request share the same trace and can be stitched
    together into an execution graph in any observability dashboard.
    """
    span_id:      str
    trace_id:     str   # == AgentState["session_id"]
    span_name:    str
    span_type:    str   # "routing" | "tool_exec" | "reflection" | "agent"
    agent_name:   str
    started_at:   str   # ISO-8601 UTC
    ended_at:     str   # ISO-8601 UTC
    duration_ms:  float

    prompt:       Optional[str]            = None
    raw_output:   Optional[str]            = None
    token_usage:  Optional[Dict[str, Any]] = None  # LlmUsage as dict

    tool_name:    Optional[str]  = None
    tool_attempt: Optional[int]  = None
    tool_success: Optional[bool] = None

    error:        Optional[str]        = None
    metadata:     Dict[str, Any]       = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ────────────────────────────────────────────────────────────────────────────
# In-Memory Ring Buffer  (for /metrics or debug endpoints)
# ────────────────────────────────────────────────────────────────────────────

# The buffer and its lock are module-level singletons.  The lock is created
# lazily so it is always bound to the running event loop.
_span_buffer: list[AgentSpanRecord] = []
_buffer_lock: Optional[asyncio.Lock] = None


def _get_buffer_lock() -> asyncio.Lock:
    global _buffer_lock
    if _buffer_lock is None:
        _buffer_lock = asyncio.Lock()
    return _buffer_lock


async def _buffer_span(span: AgentSpanRecord) -> None:
    """Append span to ring buffer and fan-out to any SSE subscribers."""
    lock = _get_buffer_lock()
    async with lock:
        if len(_span_buffer) >= SPAN_BUFFER_MAX:
            _span_buffer.pop(0)
        _span_buffer.append(span)

    # Push to SSE subscriber queues — non-blocking; slow consumers drop spans.
    sse_lock = _get_sse_lock()
    async with sse_lock:
        queues = list(_sse_queues.get(span.trace_id, []))
    for q in queues:
        try:
            q.put_nowait(span)
        except asyncio.QueueFull:
            # Slow consumer: silently drop rather than block the event loop.
            logger.debug("SSE: queue full for trace %s; span dropped.", span.trace_id)


def get_buffered_spans(limit: int = 100) -> list[AgentSpanRecord]:
    """
    Return the most-recent ``limit`` spans from the in-memory buffer.

    Safe to call from a synchronous FastAPI route or health-check endpoint.
    """
    return _span_buffer[-limit:]


def get_trace_spans(trace_id: str) -> list[AgentSpanRecord]:
    """
    Return all buffered spans belonging to ``trace_id`` in chronological order.

    Used by the /replay endpoint to reconstruct an agent reasoning trace.
    """
    return [s for s in _span_buffer if s.trace_id == trace_id]


# ────────────────────────────────────────────────────────────────────────────
# SSE Pub/Sub — Real-time span fan-out to dashboard subscribers
# ────────────────────────────────────────────────────────────────────────────

# Mapping: trace_id → list of asyncio.Queue[AgentSpanRecord | None]
# Each connected SSE client gets its own queue; None is the sentinel for shutdown.
_sse_queues: Dict[str, List[asyncio.Queue]] = {}
_sse_lock: Optional[asyncio.Lock] = None


def _get_sse_lock() -> asyncio.Lock:
    global _sse_lock
    if _sse_lock is None:
        _sse_lock = asyncio.Lock()
    return _sse_lock


async def subscribe_to_trace(trace_id: str) -> "asyncio.Queue[Optional[AgentSpanRecord]]":
    """
    Register a new SSE subscriber queue for ``trace_id``.

    The caller should ``await queue.get()`` in a loop and yield SSE-formatted
    data to the HTTP response.  Call :func:`unsubscribe_from_trace` on
    disconnect to prevent memory leaks.

    Returns:
        An ``asyncio.Queue`` that receives :class:`AgentSpanRecord` objects
        as they are emitted.  No ordering guarantees beyond FIFO.
    """
    lock = _get_sse_lock()
    q: asyncio.Queue = asyncio.Queue(maxsize=256)
    async with lock:
        _sse_queues.setdefault(trace_id, []).append(q)
    logger.debug("SSE: subscriber added for trace %s (total=%d)", trace_id, len(_sse_queues.get(trace_id, [])))
    return q


async def unsubscribe_from_trace(trace_id: str, queue: "asyncio.Queue") -> None:
    """Remove ``queue`` from the subscriber list for ``trace_id``."""
    lock = _get_sse_lock()
    async with lock:
        if trace_id in _sse_queues:
            try:
                _sse_queues[trace_id].remove(queue)
            except ValueError:
                pass
            if not _sse_queues[trace_id]:
                del _sse_queues[trace_id]
    logger.debug("SSE: subscriber removed for trace %s", trace_id)


# ────────────────────────────────────────────────────────────────────────────
# Emission Backends
# ────────────────────────────────────────────────────────────────────────────

def _sync_jsonl_append(path: str, line: str) -> None:
    """Blocking file append — always called via run_in_executor."""
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line)


async def _emit_to_jsonl(span: AgentSpanRecord) -> None:
    """
    Append one JSON line to ``MVA_TELEMETRY_LOG``.

    Uses ``run_in_executor`` to avoid blocking the event loop on disk I/O.
    A log-shipping agent (Promtail, Fluentd, Vector) can tail this file and
    forward lines to Loki/Elasticsearch for Grafana dashboards.
    """
    if not TELEMETRY_LOG_FILE:
        return
    line = span.to_json() + "\n"
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, _sync_jsonl_append, TELEMETRY_LOG_FILE, line)
    except OSError as exc:
        logger.debug("Telemetry JSONL write failed (%s): %s", TELEMETRY_LOG_FILE, exc)


async def _emit_to_langfuse(span: AgentSpanRecord) -> None:
    """
    POST span to the Langfuse ingestion endpoint using the SDK-compatible
    ``/api/public/ingestion`` batch format.

    Requires ``httpx`` (add to requirements.txt).  Silently skips when
    LANGFUSE_HOST / keys are not configured or when httpx is unavailable.
    """
    if not all([LANGFUSE_HOST, LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY]):
        return
    try:
        import httpx  # optional dep — graceful degradation if missing
    except ImportError:
        logger.debug("httpx not installed; Langfuse emission skipped.")
        return

    # Basic-auth header: base64(publicKey:secretKey)
    creds_b64 = base64.b64encode(
        f"{LANGFUSE_PUBLIC_KEY}:{LANGFUSE_SECRET_KEY}".encode()
    ).decode()

    payload = {
        "batch": [
            {
                "id":        span.span_id,
                "type":      "span-create",
                "timestamp": span.started_at,
                "body": {
                    "traceId":   span.trace_id,
                    "name":      span.span_name,
                    "startTime": span.started_at,
                    "endTime":   span.ended_at,
                    "input":     span.prompt,
                    "output":    span.raw_output,
                    "usage":     span.token_usage,
                    "metadata":  {
                        "span_type":    span.span_type,
                        "agent_name":   span.agent_name,
                        "tool_name":    span.tool_name,
                        "tool_attempt": span.tool_attempt,
                        "tool_success": span.tool_success,
                        "duration_ms":  span.duration_ms,
                        **span.metadata,
                    },
                    "level":     "ERROR" if span.error else "DEFAULT",
                    "statusMessage": span.error,
                },
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{LANGFUSE_HOST.rstrip('/')}/api/public/ingestion",
                json=payload,
                headers={"Authorization": f"Basic {creds_b64}"},
            )
            if resp.status_code >= 400:
                logger.debug(
                    "Langfuse ingestion returned %d: %s",
                    resp.status_code, resp.text[:200],
                )
    except Exception as exc:
        logger.debug("Langfuse emission failed: %s", exc)


# Optional OpenTelemetry tracer — initialised once at import time.
_otel_tracer: Any = None


def _init_otel() -> None:
    """
    Bootstrap the OpenTelemetry SDK and OTLP exporter.

    Called once when this module is imported.  All packages are imported
    lazily so missing them does not raise an ImportError.
    """
    global _otel_tracer
    if not OTEL_ENDPOINT:
        return
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider()
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=OTEL_ENDPOINT, insecure=True))
        )
        otel_trace.set_tracer_provider(provider)
        _otel_tracer = otel_trace.get_tracer("mva.agent", schema_url="https://opentelemetry.io/schemas/1.11.0")
        logger.info("OpenTelemetry tracing initialised → %s", OTEL_ENDPOINT)
    except ImportError:
        logger.debug(
            "opentelemetry packages not installed; OTLP tracing disabled. "
            "Install opentelemetry-sdk + opentelemetry-exporter-otlp-proto-grpc."
        )


_init_otel()


async def _emit_to_otel(span: AgentSpanRecord) -> None:
    """
    Record the span as an OpenTelemetry span.

    Because the OTel SDK is synchronous, this runs in an executor to stay
    non-blocking.  The BatchSpanProcessor handles the async export.
    """
    if _otel_tracer is None:
        return
    loop = asyncio.get_event_loop()

    def _sync_record() -> None:
        from opentelemetry import trace as otel_trace
        from opentelemetry.trace import SpanKind, StatusCode

        ctx = otel_trace.set_span_in_context(otel_trace.NonRecordingSpan(
            otel_trace.SpanContext(
                trace_id=int(span.trace_id.replace("-", ""), 16) & ((1 << 128) - 1),
                span_id=int(span.span_id.replace("-", ""), 16) & ((1 << 64) - 1),
                is_remote=False,
            )
        ))
        with _otel_tracer.start_as_current_span(
            span.span_name,
            kind=SpanKind.INTERNAL,
            context=ctx,
            attributes={
                "mva.span_type":         span.span_type,
                "mva.agent_name":        span.agent_name,
                "mva.trace_id":          span.trace_id,
                "mva.duration_ms":       str(span.duration_ms),
                "mva.tool_name":         span.tool_name or "",
                "mva.tool_attempt":      str(span.tool_attempt or 0),
                "mva.tool_success":      str(span.tool_success or ""),
                "mva.prompt_tokens":     str((span.token_usage or {}).get("prompt_tokens", 0)),
                "mva.completion_tokens": str((span.token_usage or {}).get("completion_tokens", 0)),
                "mva.cost_usd":          str((span.token_usage or {}).get("estimated_cost_usd", 0.0)),
                "error.message":         span.error or "",
            },
        ) as otel_span:
            if span.error:
                otel_span.set_status(StatusCode.ERROR, span.error)

    try:
        await loop.run_in_executor(None, _sync_record)
    except Exception as exc:
        logger.debug("OpenTelemetry span recording failed: %s", exc)


async def _fan_out_backends(span: AgentSpanRecord) -> None:
    """Coroutine that concurrently dispatches a span to all I/O backends."""
    await asyncio.gather(
        _buffer_span(span),
        _emit_to_jsonl(span),
        _emit_to_langfuse(span),
        _emit_to_otel(span),
        return_exceptions=True,
    )


async def _emit_span(span: AgentSpanRecord) -> None:
    """
    Fan-out: emit a completed span to all configured backends concurrently.

    Always writes a structured JSON log line so the span is never silently
    dropped even when external backends are unavailable.
    """
    # Inline structured log (zero I/O — always fast)
    logger.info(
        "SPAN span_id=%s trace_id=%s name=%s type=%s agent=%s "
        "duration_ms=%.1f prompt_tokens=%s completion_tokens=%s "
        "cost_usd=%s tool=%s attempt=%s success=%s error=%s",
        span.span_id, span.trace_id, span.span_name, span.span_type,
        span.agent_name, span.duration_ms,
        (span.token_usage or {}).get("prompt_tokens"),
        (span.token_usage or {}).get("completion_tokens"),
        (span.token_usage or {}).get("estimated_cost_usd"),
        span.tool_name, span.tool_attempt, span.tool_success,
        span.error,
    )

    # Non-blocking fan-out via create_task (must pass a coroutine, not a Future)
    asyncio.create_task(_fan_out_backends(span))


# ────────────────────────────────────────────────────────────────────────────
# Primary Instrumentation API
# ────────────────────────────────────────────────────────────────────────────

@asynccontextmanager
async def agent_span(
    span_name:    str,
    span_type:    str,
    agent_name:   str,
    trace_id:     str,
    tool_name:    Optional[str] = None,
    tool_attempt: Optional[int] = None,
) -> AsyncGenerator[SpanContext, None]:
    """
    Async context manager that bookends an execution step with telemetry.

    Captures wall-clock timing regardless of whether the body succeeds or
    raises.  The caller annotates the yielded :class:`SpanContext` with
    LLM-specific data (prompt, output, token counts) during execution.
    The completed :class:`AgentSpanRecord` is emitted via
    :func:`_emit_span` (non-blocking ``create_task``) after the body exits.

    Args:
        span_name:    Human-readable label, e.g. ``"_llm_route"`` or
                      ``"execute_tool/calc_minimost_tmu"``.
        span_type:    Tag for the execution graph layer:
                      ``"routing"`` | ``"tool_exec"`` | ``"reflection"`` | ``"agent"``.
        agent_name:   e.g. ``"SupervisorAgent"``, ``"LaborTimeAgent"``.
        trace_id:     The ``session_id`` from ``AgentState`` — links all spans
                      for one user request.
        tool_name:    Set for ``"tool_exec"`` spans.
        tool_attempt: 1-based retry counter for tool execution spans.

    Example::

        async with agent_span("_llm_route", "routing", "SupervisorAgent",
                              trace_id=state["session_id"]) as span:
            span.prompt     = user_message
            decision        = await _actual_llm_call(user_message)
            span.raw_output = decision.model_dump_json()
            span.token_usage = LlmUsage.from_counts(
                prompt_tokens=estimate_tokens(user_message),
                completion_tokens=estimate_tokens(span.raw_output),
            )
    """
    ctx        = SpanContext()
    span_id    = str(uuid.uuid4())
    started_at = datetime.now(timezone.utc)
    t0         = time.monotonic()

    try:
        yield ctx
    except Exception as exc:
        # Record the exception on the context; re-raise so the caller handles it
        ctx.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        duration_ms = (time.monotonic() - t0) * 1_000
        ended_at    = datetime.now(timezone.utc)

        record = AgentSpanRecord(
            span_id      = span_id,
            trace_id     = trace_id,
            span_name    = span_name,
            span_type    = span_type,
            agent_name   = agent_name,
            started_at   = started_at.isoformat(),
            ended_at     = ended_at.isoformat(),
            duration_ms  = round(duration_ms, 2),
            prompt       = ctx.prompt,
            raw_output   = ctx.raw_output,
            token_usage  = asdict(ctx.token_usage) if ctx.token_usage else None,
            tool_name    = tool_name,
            tool_attempt = tool_attempt,
            tool_success = (ctx.error is None) if tool_name is not None else None,
            error        = ctx.error,
            metadata     = ctx.metadata,
        )
        # Emit asynchronously — never blocks the caller's hot path
        await _emit_span(record)


# ────────────────────────────────────────────────────────────────────────────
# Security Audit Log  (compliance-grade event trail)
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class SecurityAuditEvent:
    """
    Immutable, fully-serialisable security audit record.

    Every SENSITIVE tool request, human approval, and rejection is recorded
    as a SecurityAuditEvent with a UTC timestamp and the authorising user_id.
    The record is emitted to the structured log and (optionally) to the
    JSONL telemetry file so it can be shipped to a SIEM.

    Event types
    -----------
    ``SENSITIVE_TOOL_REQUEST``  — agent attempted to invoke a SENSITIVE tool.
    ``TOOL_APPROVED``           — an authorised user approved the pending action.
    ``TOOL_REJECTED``           — a pre-flight check or explicit rejection blocked execution.
    """
    event_id:   str
    event_type: str           # SENSITIVE_TOOL_REQUEST | TOOL_APPROVED | TOOL_REJECTED
    tool_name:  str
    action_id:  str
    session_id: str
    user_id:    str
    timestamp:  str           # ISO-8601 UTC
    metadata:   Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class SecurityAuditLog:
    """
    Async-safe, fire-and-forget security audit logger.

    Usage::

        await SecurityAuditLog.record(
            event_type = "SENSITIVE_TOOL_REQUEST",
            tool_name  = "run_simulation",
            action_id  = pending.action_id,
            session_id = state["session_id"],
            user_id    = state["auth_context"].user_id,
            metadata   = {"raw_args": raw_args},
        )
    """

    _log_lock: Optional[asyncio.Lock] = None

    @classmethod
    def _get_lock(cls) -> asyncio.Lock:
        if cls._log_lock is None:
            cls._log_lock = asyncio.Lock()
        return cls._log_lock

    @classmethod
    async def record(
        cls,
        event_type: str,
        tool_name:  str,
        action_id:  str,
        session_id: str,
        user_id:    str,
        metadata:   Optional[Dict[str, Any]] = None,
    ) -> "SecurityAuditEvent":
        """
        Emit a security audit event.

        Always writes a structured log line.  If ``MVA_TELEMETRY_LOG`` is
        configured the event is also appended to the JSONL file so a
        log-shipping agent can forward it to a SIEM.

        Args:
            event_type: One of ``SENSITIVE_TOOL_REQUEST``, ``TOOL_APPROVED``,
                        ``TOOL_REJECTED``.
            tool_name:  The tool involved in the event.
            action_id:  UUID of the :class:`~memory_store.PendingAction`.
            session_id: Agent session identifier (== trace_id).
            user_id:    ``auth_context.user_id`` of the requesting or approving user.
            metadata:   Optional dict of additional context (never include secrets).

        Returns:
            The emitted :class:`SecurityAuditEvent` (useful for tests).
        """
        event = SecurityAuditEvent(
            event_id   = str(uuid.uuid4()),
            event_type = event_type,
            tool_name  = tool_name,
            action_id  = action_id,
            session_id = session_id,
            user_id    = user_id,
            timestamp  = datetime.now(timezone.utc).isoformat(),
            metadata   = metadata or {},
        )

        # Structured log — always active, zero I/O latency
        logger.info(
            "SECURITY_AUDIT event_id=%s type=%s tool=%s action_id=%s "
            "session=%s user=%s",
            event.event_id, event.event_type, event.tool_name,
            event.action_id, event.session_id, event.user_id,
        )

        # JSONL append — fire-and-forget via asyncio.create_task
        if TELEMETRY_LOG_FILE:
            line = event.to_json() + "\n"
            loop = asyncio.get_event_loop()
            async with cls._get_lock():
                try:
                    await loop.run_in_executor(
                        None, _sync_jsonl_append, TELEMETRY_LOG_FILE, line
                    )
                except OSError as exc:
                    logger.debug("SecurityAuditLog JSONL write failed: %s", exc)

        return event


# ────────────────────────────────────────────────────────────────────────────
# Emergency Proposal — Proactive HITL Push from the IoT Watchdog
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class EmergencyProposal:
    """
    A high-priority autonomous recommendation produced by the Watchdog Swarm.

    Broadcast to all connected Mission Control clients via SSE so the dashboard
    can surface an immediate PENDING_APPROVAL overlay without waiting for a
    human query.

    All fields are JSON-serialisable; ``to_dict()`` / ``to_json()`` match the
    shape expected by the frontend ``EMERGENCY_PROPOSAL`` SSE handler.
    """

    proposal_id:          str
    session_id:           str
    machine_id:           str
    anomaly_type:         str
    current_value:        float             # measured metric (e.g. yield %)
    threshold:            float             # violated threshold
    summary:              str               # Swarm consensus one-paragraph summary
    action_items:         List[str]         # concrete operator actions
    trade_off_resolution: str               # Judge's trade-off rationale
    confidence_score:     float             # Swarm confidence 0–1
    num_operators:        int               # recommended headcount
    throughput_uph:       float             # projected UPH after mitigation
    cost_per_unit_usd:    float             # projected cost after mitigation
    status:               str               = "PENDING_APPROVAL"
    created_at:           str               = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


# ── Emergency broadcast channel ──────────────────────────────────────────────
# A list of asyncio.Queue objects — one per connected SSE subscriber.
# Unlike the per-trace span queues, emergency proposals are broadcast to ALL
# connected Mission Control clients simultaneously.

_emergency_queues: List[asyncio.Queue] = []
_emergency_lock: Optional[asyncio.Lock] = None


def _get_emergency_lock() -> asyncio.Lock:
    global _emergency_lock
    if _emergency_lock is None:
        _emergency_lock = asyncio.Lock()
    return _emergency_lock


async def subscribe_to_emergency() -> "asyncio.Queue[Optional[EmergencyProposal]]":
    """
    Register a new SSE subscriber for emergency proposals.

    Returns an ``asyncio.Queue`` that receives :class:`EmergencyProposal`
    objects as they are broadcast.  Call :func:`unsubscribe_from_emergency`
    on client disconnect to avoid memory leaks.
    """
    lock = _get_emergency_lock()
    q: asyncio.Queue = asyncio.Queue(maxsize=32)
    async with lock:
        _emergency_queues.append(q)
    logger.debug(
        "Emergency SSE: subscriber added (total=%d)", len(_emergency_queues)
    )
    return q


async def unsubscribe_from_emergency(queue: "asyncio.Queue") -> None:
    """Remove ``queue`` from the emergency broadcast list."""
    lock = _get_emergency_lock()
    async with lock:
        try:
            _emergency_queues.remove(queue)
        except ValueError:
            pass
    logger.debug(
        "Emergency SSE: subscriber removed (total=%d)", len(_emergency_queues)
    )


async def broadcast_emergency_proposal(proposal: EmergencyProposal) -> None:
    """
    Fan-out an :class:`EmergencyProposal` to every connected SSE subscriber.

    Non-blocking: slow / full queues are silently skipped so a single lagging
    client cannot stall the Watchdog.  The proposal is also appended to the
    in-memory ring buffer (using the watchdog session_id as trace_id) so it
    appears in the telemetry replay feed.
    """
    lock = _get_emergency_lock()
    async with lock:
        queues = list(_emergency_queues)

    delivered = 0
    for q in queues:
        try:
            q.put_nowait(proposal)
            delivered += 1
        except asyncio.QueueFull:
            logger.debug(
                "Emergency SSE: queue full for one subscriber; proposal dropped for that client."
            )

    logger.info(
        "Emergency SSE: proposal %s broadcast to %d/%d subscribers",
        proposal.proposal_id[:8], delivered, len(queues),
    )

    # Also buffer a synthetic span so the proposal appears in telemetry replays.
    import time as _time
    now_iso = datetime.now(timezone.utc).isoformat()
    synthetic_span = AgentSpanRecord(
        span_id     = str(uuid.uuid4()),
        trace_id    = proposal.session_id,
        span_name   = "watchdog/emergency_proposal",
        span_type   = "agent",
        agent_name  = "IoTWatchdog",
        started_at  = now_iso,
        ended_at    = now_iso,
        duration_ms = 0.0,
        raw_output  = proposal.to_json(),
        metadata    = {
            "proposal_id":  proposal.proposal_id,
            "machine_id":   proposal.machine_id,
            "anomaly_type": proposal.anomaly_type,
            "status":       proposal.status,
        },
    )
    await _buffer_span(synthetic_span)


# ────────────────────────────────────────────────────────────────────────────
# Utility Helpers
# ────────────────────────────────────────────────────────────────────────────

def estimate_tokens(text: str) -> int:
    """
    Approximate a token count using the common 4-chars-per-token heuristic.

    Replace with ``tiktoken`` or the LLM provider's tokeniser for production
    accuracy.  Never returns zero (minimum of 1).

    Args:
        text: Any string (prompt, completion, schema hint, etc.).

    Returns:
        Estimated integer token count.
    """
    if not text:
        return 1
    return max(1, len(text) // 4)
