"""
backend/agent_router_poc.py
────────────────────────────────────────────────────────────────────────────────
Enterprise MVA Platform v2.0.0 — Agentic Workflow Proof of Concept
Supervisor / Router pattern with Reflection & Self-Correction.

Architecture:
  SupervisorAgent
    ├── LaborTimeAgent   (MiniMOST / TMU calculations)
    ├── BomAgent         (BOM structure lookups)
    └── SimulationAgent  (cycle-time & cost simulation)

Design principles:
  • Fully async (asyncio) — zero blocking calls, event-loop-safe.
  • All LLM tool-call schemas defined as strict Pydantic v2 models.
  • Stateful routing via an AgentState TypedDict (LangGraph-compatible shape).
  • Reflection loop: on tool failure the LLM re-reasons with error context;
    bounded by MAX_RETRIES_PER_TOOL to prevent runaway loops.
  • Exponential back-off on transient errors.
  • Per-request asyncio.Semaphore to throttle concurrent LLM calls.
  • Every tool is validated, executed, and logged via a single ToolExecutor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Literal, Optional, TypedDict

from pydantic import BaseModel, Field, ValidationError

from telemetry import LlmUsage, SecurityAuditLog, agent_span, estimate_tokens
from memory_store import (
    Checkpoint,
    PendingAction,
    PendingActionStatus,
    SessionState,
    StoredMessage,
    get_memory_store,
    prune_and_summarize,
    register_protected_agent_names,
)
from security.tool_sandbox import ToolGuard

# ────────────────────────────────────────────────────────────────────────────
# Logging
# ────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────

MAX_RETRIES_PER_TOOL: int = 3       # Hard ceiling; prevents infinite reflection loops
BACKOFF_BASE_SECONDS: float = 0.5   # Exponential back-off base: 0.5 × 2^attempt
LLM_CONCURRENCY_LIMIT: int = 4      # Max simultaneous LLM calls per request
TOOL_CALL_TIMEOUT_SECONDS: float = 30.0  # asyncio.wait_for timeout per tool

# ────────────────────────────────────────────────────────────────────────────
# Domain Enums
# ────────────────────────────────────────────────────────────────────────────

class AgentName(str, Enum):
    SUPERVISOR        = "SupervisorAgent"
    LABOR_TIME        = "LaborTimeAgent"
    BOM               = "BomAgent"
    SIMULATION        = "SimulationAgent"
    DEBATE_ROOM       = "DebateRoom"
    TEMPORAL_ANALYST  = "TemporalAnalystAgent"


# Register all agent names as protected from context pruning immediately after
# AgentName is defined.  This ensures prune_and_summarize() never evicts
# Supervisor synthesis or Reflection outputs from any previous turn.
register_protected_agent_names(frozenset({a.value for a in AgentName}))


class IntentLabel(str, Enum):
    """Canonical intent labels the Supervisor assigns to an inbound query."""
    LABOR_TIME_ANALYSIS  = "labor_time_analysis"
    BOM_LOOKUP           = "bom_lookup"
    SIMULATION_RUN       = "simulation_run"
    MULTI_STEP           = "multi_step"
    DEBATE               = "debate"           # Multi-agent adversarial debate session
    TEMPORAL_ANALYSIS    = "temporal_analysis"  # Time-series trend / anomaly queries
    UNKNOWN              = "unknown"


class ToolCategory(str, Enum):
    """
    Security classification for tool execution policy.

    ``READ_ONLY`` tools are executed immediately by the agent.
    ``SENSITIVE`` tools are intercepted: a PendingAction is created,
    persisted to SessionState, and the agent returns a ``PENDING_APPROVAL``
    response until an authorised user calls the /approve endpoint.
    """
    READ_ONLY = "read_only"
    SENSITIVE = "sensitive"


#: Tools whose execution requires explicit human approval.
#: Extend this set as new high-stakes operations are registered.
SENSITIVE_TOOLS: frozenset[str] = frozenset({
    "run_simulation",
    # Future additions: "update_labor_standard", "update_bom_cost", ...
})


def get_tool_category(tool_name: str) -> ToolCategory:
    """Return the security category for a given tool name."""
    return ToolCategory.SENSITIVE if tool_name in SENSITIVE_TOOLS else ToolCategory.READ_ONLY


# ────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas — State Models
# ────────────────────────────────────────────────────────────────────────────

class Message(BaseModel):
    """A single turn in the LLM conversation history."""
    role:    Literal["system", "user", "assistant", "tool"]
    content: str
    name:    Optional[str] = None    # tool name, for role=="tool" turns


class ToolCallResult(BaseModel):
    """Outcome of a single tool execution recorded in AgentState."""
    tool_name:   str
    attempt:     int
    success:     bool
    output:      Optional[Any] = None
    error:       Optional[str] = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ErrorRecord(BaseModel):
    """Immutable audit entry written on every tool failure."""
    tool_name:  str
    attempt:    int
    error:      str
    timestamp:  datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class AuthContext(BaseModel):
    """JWT-derived security context forwarded to every tool call."""
    user_id:   str
    role:      str
    jwt_token: str   # Raw token; used by tool HTTP clients as Bearer header


# ────────────────────────────────────────────────────────────────────────────
# AgentState — LangGraph-compatible TypedDict
# ────────────────────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    """
    Central state dictionary threaded through every node in the agent graph.
    Shape mirrors LangGraph's StateGraph pattern so the PoC can be lifted
    into LangGraph with minimal refactoring.
    """
    session_id:        str
    messages:          List[Message]
    active_agent:      str
    intent:            str
    tool_call_results: List[ToolCallResult]
    retry_counts:      Dict[str, int]    # key: tool_name, value: attempt count
    error_log:         List[ErrorRecord]
    auth_context:      AuthContext
    created_at:        datetime
    updated_at:        datetime
    pending_action:    Optional[Any]     # Optional[PendingAction] — set on HITL interrupt


def new_agent_state(user_query: str, auth: AuthContext) -> AgentState:
    """Factory that constructs a fresh AgentState for a new request."""
    now = datetime.now(timezone.utc)
    return AgentState(
        session_id        = str(uuid.uuid4()),
        messages          = [Message(role="user", content=user_query)],
        active_agent      = AgentName.SUPERVISOR,
        intent            = IntentLabel.UNKNOWN,
        tool_call_results = [],
        retry_counts      = {},
        error_log         = [],
        auth_context      = auth,
        created_at        = now,
        updated_at        = now,
        pending_action    = None,
    )


# ────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas — Tool Call Inputs & Outputs
# ────────────────────────────────────────────────────────────────────────────

# ── LaborTime tools ──────────────────────────────────────────────────────────

class CalcMiniMostTmuInput(BaseModel):
    """Input schema for the calc_minimost_tmu tool."""
    sequence_code: str = Field(
        ...,
        description="MiniMOST sequence string, e.g. 'A1B0G1A1B0P3A0'.",
        pattern=r"^[A-Za-z0-9]+$",
    )
    skill_level: Literal["Novice", "Proficient", "Expert"] = Field(
        default="Proficient",
        description="Operator skill level; adjusts efficiency multiplier.",
    )


class CalcMiniMostTmuOutput(BaseModel):
    """Output schema returned by calc_minimost_tmu."""
    sequence_code: str
    total_tmu:     float
    time_seconds:  float
    skill_factor:  float


# ── BOM tools ────────────────────────────────────────────────────────────────

class LookupBomItemInput(BaseModel):
    """Input schema for the lookup_bom_item tool."""
    part_number: str = Field(
        ...,
        description="Part number to look up in the BOM database.",
        min_length=1,
        max_length=64,
    )
    revision: Optional[str] = Field(
        default=None,
        description="Optional revision code (e.g. 'A', '1.3'). Defaults to latest.",
    )


class LookupBomItemOutput(BaseModel):
    """Output schema returned by lookup_bom_item."""
    part_number:  str
    description:  str
    unit_cost:    float
    lead_time_d:  int      # lead time in calendar days
    revision:     str


# ── Simulation tools ─────────────────────────────────────────────────────────

class RunSimulationInput(BaseModel):
    """Input schema for the run_simulation tool."""
    model_id:         str   = Field(..., description="Process model identifier.")
    cycle_time_tmu:   float = Field(..., gt=0, description="Gross cycle time in TMU.")
    num_operators:    int   = Field(..., ge=1, le=100, description="Headcount.")
    machine_rate_usd: float = Field(..., ge=0, description="Burdened machine rate (USD/hr).")


class RunSimulationOutput(BaseModel):
    """Output schema returned by run_simulation."""
    model_id:          str
    cost_per_unit_usd: float
    throughput_uph:    float  # units per hour
    utilization_pct:   float


# ── Supervisor routing ────────────────────────────────────────────────────────

class RouterDecision(BaseModel):
    """Structured output the Supervisor's LLM call must produce."""
    intent:           IntentLabel
    target_agents:    List[AgentName] = Field(
        ...,
        description="Ordered list of sub-agents to invoke.",
        min_length=1,
    )
    reasoning:        str = Field(
        ...,
        description="One-sentence rationale for the routing decision.",
    )
    run_in_parallel:  bool = Field(
        default=False,
        description="True when target_agents are independent and safe to fan-out.",
    )


# ────────────────────────────────────────────────────────────────────────────
# Tool Registry — maps tool names to their (input_schema, async handler)
# ────────────────────────────────────────────────────────────────────────────

# Type alias for a tool handler coroutine
ToolHandler = Callable[..., Coroutine[Any, Any, BaseModel]]

# Registry entry
class ToolDefinition(BaseModel):
    """Static descriptor stored in the tool registry."""
    name:         str
    description:  str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    # handler is stored separately to avoid Pydantic trying to validate a callable
    model_config  = {"arbitrary_types_allowed": True}


# ────────────────────────────────────────────────────────────────────────────
# Stub Backend Tool Implementations
# (In production these call the FastAPI backend via httpx.AsyncClient,
#  or call internal service functions directly when co-located.)
# ────────────────────────────────────────────────────────────────────────────

SKILL_EFFICIENCY: Dict[str, float] = {
    "Novice": 0.8,
    "Proficient": 1.0,
    "Expert": 1.2,
}

TMU_TO_SECONDS: float = 0.036  # 1 TMU = 0.036 seconds (per MOST standard)


async def _tool_calc_minimost_tmu(data: CalcMiniMostTmuInput) -> CalcMiniMostTmuOutput:
    """
    Stub: dispatches to the internal MiniMOST TMU calculation engine.
    In production this calls main.py's calculation logic directly or via
    the FastAPI endpoint POST /minimost/calculate.
    """
    # Simulate async I/O (e.g. reading TMU index tables from cache)
    await asyncio.sleep(0)

    # Simplified TMU calculation: sum of numeric index values in the sequence
    raw_tmu = sum(
        int(ch) for ch in data.sequence_code if ch.isdigit()
    )
    skill_factor  = SKILL_EFFICIENCY.get(data.skill_level, 1.0)
    adjusted_tmu  = raw_tmu / skill_factor   # higher skill → fewer effective TMU
    time_seconds  = adjusted_tmu * TMU_TO_SECONDS

    return CalcMiniMostTmuOutput(
        sequence_code = data.sequence_code,
        total_tmu     = round(adjusted_tmu, 2),
        time_seconds  = round(time_seconds,  4),
        skill_factor  = skill_factor,
    )


async def _tool_lookup_bom_item(data: LookupBomItemInput) -> LookupBomItemOutput:
    """
    Stub: queries the BOM database for a part number.
    In production calls GET /bom/{part_number}?revision={revision}.
    """
    await asyncio.sleep(0)
    return LookupBomItemOutput(
        part_number  = data.part_number,
        description  = f"STUB — {data.part_number} component",
        unit_cost    = 12.50,
        lead_time_d  = 14,
        revision     = data.revision or "latest",
    )


async def _tool_run_simulation(data: RunSimulationInput) -> RunSimulationOutput:
    """
    Stub: runs a cycle-time / cost simulation.
    In production calls POST /simulation/run with a full SimulationRequest body.
    """
    await asyncio.sleep(0)
    cost_per_unit = (data.machine_rate_usd / 3600) * (data.cycle_time_tmu * TMU_TO_SECONDS)
    throughput    = (3600 / (data.cycle_time_tmu * TMU_TO_SECONDS)) * data.num_operators
    return RunSimulationOutput(
        model_id          = data.model_id,
        cost_per_unit_usd = round(cost_per_unit,  4),
        throughput_uph    = round(throughput,      2),
        utilization_pct   = 87.5,  # stub value
    )


# ────────────────────────────────────────────────────────────────────────────
# ToolExecutor — validates, calls, reflects, retries
# ────────────────────────────────────────────────────────────────────────────

# Map: tool name → (input model, output model, async handler)
_TOOL_REGISTRY: Dict[str, tuple[type[BaseModel], type[BaseModel], ToolHandler]] = {
    "calc_minimost_tmu": (CalcMiniMostTmuInput,  CalcMiniMostTmuOutput, _tool_calc_minimost_tmu),
    "lookup_bom_item":   (LookupBomItemInput,    LookupBomItemOutput,   _tool_lookup_bom_item),
    "run_simulation":    (RunSimulationInput,     RunSimulationOutput,   _tool_run_simulation),
}


class ToolExecutionFailure(Exception):
    """Raised when a tool exhausts all retries without success."""
    def __init__(self, tool_name: str, last_error: str):
        self.tool_name  = tool_name
        self.last_error = last_error
        super().__init__(f"Tool '{tool_name}' failed after {MAX_RETRIES_PER_TOOL} retries: {last_error}")


class HITLPendingApproval(BaseException):
    """
    Control-flow signal raised when a SENSITIVE tool requires human approval.

    Inherits from ``BaseException`` (not ``Exception``) so that it bypasses
    ``except Exception`` clauses in :func:`telemetry.agent_span` and other
    middleware, propagating directly to :func:`run_agent_workflow`'s explicit
    handler without being mistaken for an error.

    The caller guarantees that a :class:`~memory_store.PendingAction` has
    already been written into ``state["pending_action"]`` before this is raised.
    """
    def __init__(self, action_id: str, tool_name: str) -> None:
        self.action_id = action_id
        self.tool_name = tool_name
        super().__init__(
            f"Tool '{tool_name}' requires human approval — action_id={action_id}."
        )


async def execute_tool_with_reflection(
    tool_name:  str,
    raw_args:   Dict[str, Any],
    state:      AgentState,
) -> ToolCallResult:
    """
    Central tool execution engine that implements the Reflection loop.

    Flow:
      1. Validate raw_args against the tool's Pydantic input schema.
      2. Call the async tool handler inside asyncio.wait_for (timeout guard).
      3. On success  → append ToolCallResult(success=True) to state and return.
      4. On failure  → log the error, build a reflection prompt, ask the LLM to
                       correct the arguments, then retry up to MAX_RETRIES_PER_TOOL.
      5. On max retries → raise ToolExecutionFailure (supervisor handles graceful degradation).

    Args:
        tool_name: Key in _TOOL_REGISTRY.
        raw_args:  Raw argument dict generated by the LLM.
        state:     Mutable AgentState; updated in-place with results/errors.

    Returns:
        ToolCallResult describing the successful execution.

    Raises:
        ToolExecutionFailure if all retries are exhausted.
    """
    if tool_name not in _TOOL_REGISTRY:
        raise ToolExecutionFailure(tool_name, f"Unknown tool '{tool_name}'.")

    # ── HITL intercept: SENSITIVE tools require explicit human approval ───────
    if get_tool_category(tool_name) == ToolCategory.SENSITIVE:
        pending = PendingAction(
            tool_name  = tool_name,
            raw_args   = dict(raw_args),
            agent_name = state["active_agent"],
        )
        state["pending_action"] = pending
        state["updated_at"]     = datetime.now(timezone.utc)
        logger.info(
            "HITL interrupt: tool '%s' classified as SENSITIVE — awaiting approval "
            "(action_id=%s, session=%s).",
            tool_name, pending.action_id, state["session_id"],
        )
        # Audit log: fire-and-forget, never blocks tool path
        asyncio.ensure_future(SecurityAuditLog.record(
            event_type = "SENSITIVE_TOOL_REQUEST",
            tool_name  = tool_name,
            action_id  = pending.action_id,
            session_id = state["session_id"],
            user_id    = state["auth_context"].user_id,
            metadata   = {"raw_args": raw_args, "agent": state["active_agent"]},
        ))
        raise HITLPendingApproval(pending.action_id, tool_name)
    # ── End HITL intercept ───────────────────────────────────────────────────

    input_schema, output_schema, handler = _TOOL_REGISTRY[tool_name]

    # Ensure per-tool retry counter exists in state
    state["retry_counts"].setdefault(tool_name, 0)

    last_error: str = ""

    # Outer telemetry span covers all retry attempts; records final args/output.
    async with agent_span(
        span_name  = f"execute_tool/{tool_name}",
        span_type  = "tool_exec",
        agent_name = state["active_agent"],
        trace_id   = state["session_id"],
        tool_name  = tool_name,
    ) as exec_span:
        exec_span.prompt = json.dumps(raw_args, default=str)

        for attempt in range(1, MAX_RETRIES_PER_TOOL + 1):
            state["retry_counts"][tool_name] = attempt

            # ── Step 1: Schema validation ────────────────────────────────────────
            try:
                validated_input = input_schema.model_validate(raw_args)
            except ValidationError as exc:
                # Schema error — inject reflection prompt so the LLM can correct arguments
                last_error = f"Schema validation error: {exc}"
                raw_args   = await _reflect_and_correct(
                    tool_name    = tool_name,
                    raw_args     = raw_args,
                    error        = last_error,
                    input_schema = input_schema,
                    state        = state,
                    attempt      = attempt,
                )
                continue   # retry with corrected args

            # ── Step 2: Execute the tool (with timeout guard) ────────────────────
            try:
                output = await asyncio.wait_for(
                    handler(validated_input),
                    timeout=TOOL_CALL_TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError:
                last_error = f"Tool timed out after {TOOL_CALL_TIMEOUT_SECONDS}s."
                exec_span.error = last_error
                _record_error(state, tool_name, attempt, last_error)
                await _exponential_backoff(attempt)
                continue   # retry after back-off
            except Exception as exc:
                last_error = f"Tool execution error: {type(exc).__name__}: {exc}"
                exec_span.error = last_error
                raw_args   = await _reflect_and_correct(
                    tool_name    = tool_name,
                    raw_args     = raw_args,
                    error        = last_error,
                    input_schema = input_schema,
                    state        = state,
                    attempt      = attempt,
                )
                continue   # retry with corrected args

            # ── Step 3: Record success ───────────────────────────────────────────
            result = ToolCallResult(
                tool_name = tool_name,
                attempt   = attempt,
                success   = True,
                output    = output.model_dump(),
            )
            state["tool_call_results"].append(result)
            state["updated_at"] = datetime.now(timezone.utc)
            logger.info(
                "Tool '%s' succeeded on attempt %d (session=%s).",
                tool_name, attempt, state["session_id"],
            )
            # Annotate span with final output and attempt number for dashboards.
            exec_span.raw_output = json.dumps(result.output, default=str)
            exec_span.metadata["final_attempt"] = attempt
            exec_span.error = None   # clear transient error if last attempt succeeded
            return result

        # ── All retries exhausted ────────────────────────────────────────────
        _record_error(state, tool_name, MAX_RETRIES_PER_TOOL, last_error)
        # ToolExecutionFailure propagates through agent_span's except clause,
        # which sets exec_span.error automatically before the span is emitted.
        raise ToolExecutionFailure(tool_name, last_error)


async def _reflect_and_correct(
    tool_name:    str,
    raw_args:     Dict[str, Any],
    error:        str,
    input_schema: type[BaseModel],
    state:        AgentState,
    attempt:      int,
) -> Dict[str, Any]:
    """
    Reflection step: appends an error context message to the conversation,
    then calls the (stub) LLM to produce corrected arguments.

    In production this calls the real LLM API (e.g. OpenAI chat completions
    with tool_choice="required") and parses the corrected JSON arguments.
    """
    _record_error(state, tool_name, attempt, error)

    # Build the reflection prompt (injected into message history)
    schema_hint = json.dumps(input_schema.model_json_schema(), indent=2)
    reflection_prompt = (
        f"Your previous call to `{tool_name}` (attempt {attempt}) failed.\n"
        f"Error: {error}\n\n"
        f"Tool schema:\n{schema_hint}\n\n"
        "Review the error and schema carefully, then provide corrected arguments "
        "as a JSON object that strictly matches the schema."
    )
    state["messages"].append(Message(role="assistant", content=reflection_prompt))
    state["updated_at"] = datetime.now(timezone.utc)

    logger.warning(
        "Reflection triggered for tool '%s' attempt %d (session=%s): %s",
        tool_name, attempt, state["session_id"], error,
    )

    # Reflection span: records the full prompt sent to the self-correction LLM
    # and the corrected arguments it returns.  Token usage is tracked here so
    # reflection overhead is visible separately from the primary tool call cost.
    async with agent_span(
        span_name    = f"reflect/{tool_name}",
        span_type    = "reflection",
        agent_name   = state["active_agent"],
        trace_id     = state["session_id"],
        tool_name    = tool_name,
        tool_attempt = attempt,
    ) as span:
        span.prompt = reflection_prompt
        span.metadata["original_error"] = error

        # Stub: return the original args unchanged (real impl would call the LLM here)
        # In production:
        #   corrected_json = await llm_client.call(messages=state["messages"], ...)
        #   corrected_args = json.loads(corrected_json)
        #   span.raw_output  = corrected_json
        #   span.token_usage = LlmUsage.from_counts(
        #       prompt_tokens     = estimate_tokens(reflection_prompt),
        #       completion_tokens = estimate_tokens(corrected_json),
        #   )
        #   return corrected_args
        await asyncio.sleep(0)
        span.raw_output  = json.dumps(raw_args, default=str)   # stub: args unchanged
        span.token_usage = LlmUsage.from_counts(
            prompt_tokens     = estimate_tokens(reflection_prompt),
            completion_tokens = estimate_tokens(span.raw_output),
        )
        return raw_args


def _record_error(state: AgentState, tool_name: str, attempt: int, error: str) -> None:
    """Appends an ErrorRecord to the audit log in state."""
    state["error_log"].append(
        ErrorRecord(tool_name=tool_name, attempt=attempt, error=error)
    )


async def _exponential_backoff(attempt: int) -> None:
    """Async sleep with exponential back-off: BACKOFF_BASE × 2^(attempt-1)."""
    delay = BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
    await asyncio.sleep(delay)


# ────────────────────────────────────────────────────────────────────────────
# Specialist Sub-Agents
# ────────────────────────────────────────────────────────────────────────────

async def labor_time_agent(state: AgentState) -> AgentState:
    """
    LaborTimeAgent — specializes in MiniMOST motion analysis.

    Reads the latest user message, extracts sequence parameters, and calls
    calc_minimost_tmu via the ToolExecutor.
    Instrumented with :func:`telemetry.agent_span` (span_type="agent").
    """
    state["active_agent"] = AgentName.LABOR_TIME.value
    logger.info("LaborTimeAgent activated (session=%s).", state["session_id"])

    async with agent_span(
        span_name  = "labor_time_agent",
        span_type  = "agent",
        agent_name = AgentName.LABOR_TIME.value,
        trace_id   = state["session_id"],
    ) as span:
        # Record the user query this agent is acting on.
        span.prompt = state["messages"][-1].content

        # In production: call LLM to extract structured args from the user message.
        # Stub: use a fixed example derived from the DDM sequence format.
        tool_args: Dict[str, Any] = {
            "sequence_code": "A1B0G1A1B0P3A0",
            "skill_level":   "Proficient",
        }

        try:
            result = await execute_tool_with_reflection(
                tool_name = "calc_minimost_tmu",
                raw_args  = tool_args,
                state     = state,
            )
            summary = (
                f"MiniMOST analysis complete. "
                f"Sequence '{result.output['sequence_code']}': "
                f"{result.output['total_tmu']} TMU = {result.output['time_seconds']}s "
                f"(skill factor {result.output['skill_factor']})."
            )
        except ToolExecutionFailure as exc:
            summary = (
                f"LaborTimeAgent could not complete the calculation: {exc.last_error}. "
                "Please verify the sequence code and try again."
            )

        span.raw_output = summary
        state["messages"].append(Message(role="assistant", content=summary, name=AgentName.LABOR_TIME.value))
        state["updated_at"] = datetime.now(timezone.utc)
        return state


async def bom_agent(state: AgentState) -> AgentState:
    """
    BomAgent — specializes in BOM structure and part validation.

    Extracts part number from the user query and calls lookup_bom_item.
    Instrumented with :func:`telemetry.agent_span` (span_type="agent").
    """
    state["active_agent"] = AgentName.BOM.value
    logger.info("BomAgent activated (session=%s).", state["session_id"])

    async with agent_span(
        span_name  = "bom_agent",
        span_type  = "agent",
        agent_name = AgentName.BOM.value,
        trace_id   = state["session_id"],
    ) as span:
        span.prompt = state["messages"][-1].content

        # Stub args — production LLM call would extract from message history.
        tool_args: Dict[str, Any] = {
            "part_number": "SQT-K860G6-BASY",
            "revision":    "1.3",
        }

        try:
            result = await execute_tool_with_reflection(
                tool_name = "lookup_bom_item",
                raw_args  = tool_args,
                state     = state,
            )
            out     = result.output
            summary = (
                f"BOM lookup for '{out['part_number']}' (rev {out['revision']}): "
                f"{out['description']}, unit cost ${out['unit_cost']:.2f}, "
                f"lead time {out['lead_time_d']} days."
            )
        except ToolExecutionFailure as exc:
            summary = f"BomAgent could not retrieve BOM data: {exc.last_error}."

        span.raw_output = summary
        state["messages"].append(Message(role="assistant", content=summary, name=AgentName.BOM.value))
        state["updated_at"] = datetime.now(timezone.utc)
        return state


async def simulation_agent(state: AgentState) -> AgentState:
    """
    SimulationAgent — runs cycle-time and cost simulations.

    Extracts simulation parameters from the user query and calls run_simulation.
    Instrumented with :func:`telemetry.agent_span` (span_type="agent").
    """
    state["active_agent"] = AgentName.SIMULATION.value
    logger.info("SimulationAgent activated (session=%s).", state["session_id"])

    async with agent_span(
        span_name  = "simulation_agent",
        span_type  = "agent",
        agent_name = AgentName.SIMULATION.value,
        trace_id   = state["session_id"],
    ) as span:
        span.prompt = state["messages"][-1].content

        # Stub args — production LLM call would extract from message history.
        tool_args: Dict[str, Any] = {
            "model_id":         "MODEL-001",
            "cycle_time_tmu":   50.0,
            "num_operators":    2,
            "machine_rate_usd": 45.0,
        }

        try:
            result = await execute_tool_with_reflection(
                tool_name = "run_simulation",
                raw_args  = tool_args,
                state     = state,
            )
            out     = result.output
            summary = (
                f"Simulation for model '{out['model_id']}': "
                f"cost/unit ${out['cost_per_unit_usd']:.4f}, "
                f"throughput {out['throughput_uph']:.1f} UPH, "
                f"utilization {out['utilization_pct']}%."
            )
        except ToolExecutionFailure as exc:
            summary = f"SimulationAgent encountered an error: {exc.last_error}."

        span.raw_output = summary
        state["messages"].append(Message(role="assistant", content=summary, name=AgentName.SIMULATION.value))
        state["updated_at"] = datetime.now(timezone.utc)
        return state


async def temporal_analyst_agent(state: AgentState) -> AgentState:
    """
    TemporalAnalystAgent — routes trend / anomaly / historical queries through
    the TemporalAnalystAgent which queries the TemporalVectorStore and performs
    time-series reasoning (trend detection, anomaly detection, event correlation).

    Instrumented with ``span_type="temporal_analysis"`` for Mission Control
    dashboard visibility under the "Temporal" span lane.
    """
    from agents.temporal_analyst import TemporalAnalystAgent

    state["active_agent"] = AgentName.TEMPORAL_ANALYST.value
    user_message = state["messages"][-1].content
    logger.info("TemporalAnalystAgent activated (session=%s).", state["session_id"])

    async with agent_span(
        span_name  = "temporal_analyst_agent",
        span_type  = "temporal_analysis",
        agent_name = AgentName.TEMPORAL_ANALYST.value,
        trace_id   = state["session_id"],
    ) as span:
        span.prompt = user_message

        try:
            analyst  = TemporalAnalystAgent()
            analysis = await analyst.analyse(
                query      = user_message,
                session_id = state["session_id"],
            )
            summary = (
                f"Temporal Analysis [{analysis.analysis_id}]\n"
                f"{analysis.time_window_desc}\n\n"
                f"{analysis.synthesis}\n\n"
                f"Records analysed: {analysis.records_analysed} | "
                f"Anomalies: {len(analysis.anomalies)} | "
                f"Trends computed: {len(analysis.trends)}"
            )
        except Exception as exc:
            summary = (
                f"TemporalAnalystAgent encountered an error: "
                f"{type(exc).__name__}: {exc}."
            )
            logger.exception("TemporalAnalystAgent error (session=%s)", state["session_id"])

        span.raw_output = summary
        state["messages"].append(
            Message(role="assistant", content=summary, name=AgentName.TEMPORAL_ANALYST.value)
        )
        state["updated_at"] = datetime.now(timezone.utc)
        return state


async def debate_room_agent(state: AgentState) -> AgentState:
    """
    DebateRoom — routes complex optimisation queries through the Multi-Agent
    Debate & Consensus engine.

    Invokes :func:`agents.debate_room.run_debate_session` which orchestrates
    CostOptimizationAgent → QualityAndTimeAgent → ConsensusJudgeAgent in
    sequential debate turns, each instrumented with ``span_type="debate"``.

    The consolidated ConsensusResult is appended as an assistant message so
    the Supervisor can synthesize it with any sibling agent outputs.
    """
    from agents.debate_room import run_debate_session, ConsensusResult

    state["active_agent"] = AgentName.DEBATE_ROOM.value
    user_message = state["messages"][-1].content
    logger.info("DebateRoom activated (session=%s).", state["session_id"])

    async with agent_span(
        span_name  = "debate_room_agent",
        span_type  = "debate",
        agent_name = AgentName.DEBATE_ROOM.value,
        trace_id   = state["session_id"],
    ) as span:
        span.prompt = user_message
        span.metadata["debate_event"] = "Supervisor routing query to DebateRoom…"

        try:
            consensus: ConsensusResult = await run_debate_session(
                query      = user_message,
                session_id = state["session_id"],
                max_turns  = 3,
            )
            summary = (
                f"Debate & Consensus complete ({consensus.debate_turns} turns, "
                f"confidence={consensus.confidence_score:.2f}).\n\n"
                f"Optimal Plan [{consensus.consensus_plan_id}]:\n"
                f"{consensus.summary}\n\n"
                f"• Operators : {consensus.num_operators}\n"
                f"• Cycle Time: {consensus.cycle_time_tmu} TMU\n"
                f"• Cost/Unit : ${consensus.cost_per_unit_usd:.4f}\n"
                f"• Throughput: {consensus.throughput_uph:.1f} UPH\n"
                f"• Trade-off : {consensus.trade_off_resolution}"
            )
        except Exception as exc:
            summary = (
                f"DebateRoom encountered an error: {type(exc).__name__}: {exc}. "
                "Falling back to standard supervisor routing."
            )
            logger.exception("DebateRoom error (session=%s)", state["session_id"])

        span.raw_output = summary
        state["messages"].append(
            Message(role="assistant", content=summary, name=AgentName.DEBATE_ROOM.value)
        )
        state["updated_at"] = datetime.now(timezone.utc)
        return state


# ── Agent dispatch map (intent → agent coroutine) ────────────────────────────
_AGENT_MAP: Dict[str, Callable[[AgentState], Coroutine[Any, Any, AgentState]]] = {
    AgentName.LABOR_TIME.value:       labor_time_agent,
    AgentName.BOM.value:              bom_agent,
    AgentName.SIMULATION.value:       simulation_agent,
    AgentName.DEBATE_ROOM.value:      debate_room_agent,
    AgentName.TEMPORAL_ANALYST.value: temporal_analyst_agent,
}


# ────────────────────────────────────────────────────────────────────────────
# Supervisor Agent — Router + Orchestrator
# ────────────────────────────────────────────────────────────────────────────

# Semaphore limiting concurrent LLM calls initiated by the Supervisor.
_llm_semaphore = asyncio.Semaphore(LLM_CONCURRENCY_LIMIT)


async def _llm_route(user_message: str, trace_id: str = "") -> RouterDecision:
    """
    Stub LLM call that classifies intent and returns a routing decision.

    In production this calls the LLM with a function-calling / structured-output
    request and parses the JSON response into a RouterDecision.

    The stub uses keyword matching to demonstrate the routing logic.
    Instrumented with :func:`telemetry.agent_span` (span_type="routing").
    """
    async with _llm_semaphore:
        async with agent_span(
            span_name  = "_llm_route",
            span_type  = "routing",
            agent_name = AgentName.SUPERVISOR.value,
            trace_id   = trace_id,
        ) as span:
            # Record the exact prompt sent to the routing LLM.
            span.prompt = user_message

            # Simulate network latency to the LLM provider
            await asyncio.sleep(0)

            lowered = user_message.lower()

            # ── Temporal analysis: trend / history / anomaly queries → TemporalAnalystAgent
            if any(kw in lowered for kw in (
                "trend", "anomaly", "anomalies", "historical", "history",
                "last week", "last month", "last tuesday", "last wednesday",
                "last thursday", "last friday", "last monday",
                "past 7 days", "past 30 days", "past week", "past month",
                "yield drop", "yield rate drop", "defect spike", "cycle time spike",
                "time series", "time-series", "temporal", "trend analysis",
                "compare historical", "compare to average", "compared to average",
            )):
                decision = RouterDecision(
                    intent          = IntentLabel.TEMPORAL_ANALYSIS,
                    target_agents   = [AgentName.TEMPORAL_ANALYST],
                    reasoning       = "Temporal/trend query detected; routing to TemporalAnalystAgent.",
                    run_in_parallel = False,
                )
            # ── Debate: complex optimisation queries → DebateRoom ────────────────────
            elif any(kw in lowered for kw in (
                "optimize", "optimise", "line balanc", "line-balanc",
                "supply chain", "resource alloc", "station assign",
                "assembly line", "throughput vs cost", "debate",
            )):
                decision = RouterDecision(
                    intent          = IntentLabel.DEBATE,
                    target_agents   = [AgentName.DEBATE_ROOM],
                    reasoning       = "Complex optimisation query; routing to multi-agent debate engine.",
                    run_in_parallel = False,
                )
            elif any(kw in lowered for kw in ("tmu", "motion", "minimost", "sequence", "labor")):
                # ── fall-through to original routing below ────────────────────────
                decision = RouterDecision(
                    intent          = IntentLabel.LABOR_TIME_ANALYSIS,
                    target_agents   = [AgentName.LABOR_TIME],
                    reasoning       = "Query contains MiniMOST / labor-time keywords.",
                    run_in_parallel = False,
                )
            elif any(kw in lowered for kw in ("bom", "part", "component", "revision", "lead time")):
                decision = RouterDecision(
                    intent          = IntentLabel.BOM_LOOKUP,
                    target_agents   = [AgentName.BOM],
                    reasoning       = "Query relates to bill-of-materials lookup.",
                    run_in_parallel = False,
                )
            elif any(kw in lowered for kw in ("simulation", "simulate", "cycle time", "throughput", "cost")):
                decision = RouterDecision(
                    intent          = IntentLabel.SIMULATION_RUN,
                    target_agents   = [AgentName.SIMULATION],
                    reasoning       = "Query requests a simulation scenario.",
                    run_in_parallel = False,
                )
            # If the query touches both labor and cost, fan out to all agents in parallel.
            elif any(kw in lowered for kw in ("full analysis", "complete report", "breakdown")):
                decision = RouterDecision(
                    intent          = IntentLabel.MULTI_STEP,
                    target_agents   = [AgentName.LABOR_TIME, AgentName.BOM, AgentName.SIMULATION],
                    reasoning       = "Multi-step query; all three agents contribute independently.",
                    run_in_parallel = True,
                )
            else:
                decision = RouterDecision(
                    intent          = IntentLabel.UNKNOWN,
                    target_agents   = [AgentName.LABOR_TIME],
                    reasoning       = "Could not classify intent; defaulting to LaborTimeAgent.",
                    run_in_parallel = False,
                )

            # Record raw LLM output and token usage for cost / latency dashboards.
            span.raw_output  = decision.model_dump_json()
            span.token_usage = LlmUsage.from_counts(
                prompt_tokens     = estimate_tokens(user_message),
                completion_tokens = estimate_tokens(span.raw_output),
            )
            return decision


async def supervisor_agent(state: AgentState) -> AgentState:
    """
    SupervisorAgent — the single entry point for all agentic requests.

    Responsibilities:
      1. Classify user intent via an LLM routing call.
      2. Dispatch to one or more specialist sub-agents (sequential or parallel).
      3. Synthesize sub-agent outputs into a final user-facing response.
      4. Return the fully updated AgentState for downstream logging / storage.
    """
    state["active_agent"] = AgentName.SUPERVISOR.value
    user_message = state["messages"][-1].content

    logger.info(
        "SupervisorAgent received query (session=%s): %s",
        state["session_id"], user_message[:80],
    )

    async with agent_span(
        span_name  = "supervisor_agent",
        span_type  = "agent",
        agent_name = AgentName.SUPERVISOR.value,
        trace_id   = state["session_id"],
    ) as sup_span:
        sup_span.prompt = user_message

        # ── Step 1: Route ────────────────────────────────────────────────────────
        decision        = await _llm_route(user_message, trace_id=state["session_id"])
        state["intent"] = decision.intent.value
        logger.info(
            "Routing decision: intent=%s agents=%s parallel=%s — %s",
            decision.intent, decision.target_agents, decision.run_in_parallel, decision.reasoning,
        )

        # ── Step 2: Dispatch ─────────────────────────────────────────────────────
        agent_coros = [
            _AGENT_MAP[agent.value](state)
            for agent in decision.target_agents
            if agent.value in _AGENT_MAP
        ]

        if decision.run_in_parallel and len(agent_coros) > 1:
            # Fan-out: all specialist agents run concurrently, each updating state.
            # Note: concurrent mutation of state is safe here because each agent
            # only *appends* to list fields — Python list.append() is thread-safe
            # within a single-threaded asyncio event loop.
            await asyncio.gather(*agent_coros)
        else:
            # Sequential execution preserves inter-agent context propagation.
            for coro in agent_coros:
                state = await coro

        # ── Step 3: Synthesize ───────────────────────────────────────────────────
        agent_outputs = [
            msg.content
            for msg in state["messages"]
            if msg.role == "assistant" and msg.name is not None
        ]
        synthesis = (
            "Agent workflow complete.\n\n"
            + "\n".join(f"• {out}" for out in agent_outputs)
        )
        state["messages"].append(Message(role="assistant", content=synthesis, name=AgentName.SUPERVISOR.value))
        state["active_agent"] = AgentName.SUPERVISOR.value
        state["updated_at"]   = datetime.now(timezone.utc)
        sup_span.raw_output   = synthesis
        sup_span.metadata["intent"]       = decision.intent.value
        sup_span.metadata["target_agents"] = [a.value for a in decision.target_agents]
        sup_span.metadata["parallel"]      = decision.run_in_parallel

        logger.info("SupervisorAgent finished (session=%s).", state["session_id"])
        return state


# ────────────────────────────────────────────────────────────────────────────
# Public entrypoint — drop-in for FastAPI endpoint integration
# ────────────────────────────────────────────────────────────────────────────

async def run_agent_workflow(
    user_query:  str,
    auth:        AuthContext,
    session_id:  Optional[str] = None,
) -> Dict[str, Any]:
    """
    Stateful entrypoint for the agent workflow.

    Lifecycle per call
    ------------------
    1. **Load** — fetch existing :class:`~memory_store.SessionState` from the
       configured backend (or create a fresh one for new sessions).
    2. **Hydrate** — inject the compressed ``system_summary`` (if any) as a
       leading ``role="system"`` message so the LLM has long-range context,
       then replay the pruned ``message_history`` followed by the new user turn.
    3. **Pre-seed** — if a :class:`~memory_store.Checkpoint` exists, load its
       tool_call_results into the new ``AgentState`` so downstream agents can
       reference previous tool outputs without re-executing.
    4. **Run** — invoke :func:`supervisor_agent` with the hydrated state.
    5. **Checkpoint** — capture successful tool results + intent into a new
       :class:`~memory_store.Checkpoint` for the next turn's resume-from-failure.
    6. **Prune** — call :func:`~memory_store.prune_and_summarize` if the
       message_history has grown past the token budget; compresses old turns
       into ``system_summary`` while preserving all Reflection outputs.
    7. **Save** — persist the updated ``SessionState`` (async, non-blocking).

    Args:
        user_query:  Natural-language query string from the authenticated user.
        auth:        JWT-derived credentials forwarded to all tool calls.
        session_id:  Optional existing session UUID.  Pass ``None`` (or omit)
                     to start a fresh session; the new ``session_id`` is
                     returned in the response so clients can continue the
                     conversation on subsequent calls.

    Returns:
        A dict with ``session_id``, ``intent``, ``answer``, ``tool_results``,
        ``error_log``, and ``turn_count``.

    Example FastAPI integration (main.py)::

        from agent_router_poc import run_agent_workflow, AuthContext

        @app.post("/api/v1/agent/query")
        async def agent_query(request: AgentQueryRequest, credentials = Depends(security)):
            auth = AuthContext(
                user_id   = current_user["user_id"],
                role      = current_user["role"],
                jwt_token = credentials.credentials,
            )
            return await run_agent_workflow(
                user_query = request.query,
                auth       = auth,
                session_id = request.session_id,
            )
    """
    store = get_memory_store()

    # ── Step 1: Load or create session ───────────────────────────────────────
    session: Optional[SessionState] = None
    if session_id:
        session = await store.load(session_id)
        if session is None:
            logger.warning(
                "Session '%s' not found in store; starting fresh session.",
                session_id,
            )

    if session is None:
        session = SessionState(
            user_id   = auth.user_id,
            user_role = auth.role,
        )

    # ── Step 2: Hydrate AgentState messages from session ─────────────────────
    # Inject compressed history digest as a leading system message so the LLM
    # retains long-range context even after pruning evicted earlier turns.
    initial_messages: List[Message] = []
    if session.system_summary:
        initial_messages.append(
            Message(
                role    = "system",
                content = (
                    "Previous conversation context (auto-compressed summary):\n"
                    + session.system_summary
                ),
            )
        )

    # Replay the pruned message window (StoredMessage → Message; same fields).
    for sm in session.message_history:
        initial_messages.append(Message.model_validate(sm.model_dump()))

    # Append the current user turn.
    initial_messages.append(Message(role="user", content=user_query))

    # ── Step 3: Build AgentState (with checkpoint pre-seed) ──────────────────
    now = datetime.now(timezone.utc)
    state: AgentState = AgentState(
        session_id        = session.session_id,
        messages          = initial_messages,
        active_agent      = AgentName.SUPERVISOR.value,
        intent            = IntentLabel.UNKNOWN.value,
        tool_call_results = [],
        retry_counts      = {},
        error_log         = [],
        auth_context      = auth,
        created_at        = session.created_at,
        updated_at        = now,
        pending_action    = None,
    )

    # Pre-seed the AgentState with tool results from the last checkpoint so
    # downstream agents in this turn can reference previous outputs.
    if session.checkpoint:
        state["tool_call_results"] = [
            ToolCallResult.model_validate(r)
            for r in session.checkpoint.tool_call_results
        ]
        logger.info(
            "Pre-seeded %d tool results from checkpoint (session=%s, turn=%d).",
            len(state["tool_call_results"]),
            session.session_id,
            session.turn_count,
        )

    # ── Step 4: Execute the agent workflow ────────────────────────────────────
    async with agent_span(
        span_name  = "run_agent_workflow",
        span_type  = "agent",
        agent_name = AgentName.SUPERVISOR.value,
        trace_id   = session.session_id,
    ) as workflow_span:
        workflow_span.prompt   = user_query
        workflow_span.metadata["session_id"] = session.session_id
        workflow_span.metadata["turn"]       = session.turn_count + 1

        try:
            state = await supervisor_agent(state)
        except HITLPendingApproval as hitl:
            # ── HITL interrupt: persist pending action and return early ─────────────
            # agent_span's finally block still fires (emitting the span without
            # an error flag) because HITLPendingApproval bypasses except Exception.
            pending: Optional[Any] = state["pending_action"]
            if pending is not None:
                session.pending_action = pending
            # Preserve message history up to the interrupt point
            session.message_history = [
                StoredMessage.model_validate(m.model_dump())
                for m in state["messages"]
                if not (
                    m.role == "system"
                    and m.content.startswith("Previous conversation context")
                )
            ]
            session.turn_count  += 1
            session.updated_at   = datetime.now(timezone.utc)
            session              = prune_and_summarize(session)
            await store.save(session)
            workflow_span.metadata["hitl_interrupt"] = True
            workflow_span.metadata["action_id"]      = hitl.action_id
            workflow_span.metadata["pending_tool"]   = hitl.tool_name
            return {
                "session_id":   session.session_id,
                "intent":       state["intent"],
                "status":       "PENDING_APPROVAL",
                "action_id":    hitl.action_id,
                "tool_name":    hitl.tool_name,
                "answer": (
                    f"This action requires human approval before execution. "
                    f"Please call POST /api/v1/agent/approve/{session.session_id} "
                    f"with action_id='{hitl.action_id}' to authorise the "
                    f"'{hitl.tool_name}' operation."
                ),
                "tool_results": [],
                "error_log":    [e.model_dump() for e in state["error_log"]],
                "turn_count":   session.turn_count,
            }
        # ── Normal completion path ────────────────────────────────────────────────────────
        final_response = next(
            (
                m.content for m in reversed(state["messages"])
                if m.role == "assistant" and m.name == AgentName.SUPERVISOR.value
            ),
            "No response generated.",
        )
        workflow_span.raw_output = final_response

    # ── Step 5: Checkpoint — capture successful tool results ──────────────────
    # Include ONLY the results produced in this turn (not pre-seeded ones from
    # prior turns) so the checkpoint reflects the latest execution state.
    this_turn_results = [
        r for r in state["tool_call_results"]
        if r.success and r not in (session.checkpoint.tool_call_results if session.checkpoint else [])
    ]
    # Merge: checkpoint carries cumulative successful results across all turns.
    cumulative_results: List[ToolCallResult] = []
    if session.checkpoint:
        cumulative_results = [
            ToolCallResult.model_validate(r) for r in session.checkpoint.tool_call_results
        ]
    cumulative_results.extend([r for r in state["tool_call_results"] if r.success])

    session.checkpoint = Checkpoint(
        turn_index        = session.turn_count + 1,
        last_intent       = state["intent"],
        tool_call_results = [r.model_dump() for r in cumulative_results],
    )

    # ── Step 6: Update session message_history ────────────────────────────────
    # Store only the non-system-summary messages so the digest is not duplicated
    # on the next turn (it is re-injected from session.system_summary instead).
    session.message_history = [
        StoredMessage.model_validate(m.model_dump())
        for m in state["messages"]
        if not (
            m.role == "system"
            and m.content.startswith("Previous conversation context (auto-compressed summary):")
        )
    ]
    session.turn_count        += 1
    session.total_tokens_used += sum(
        estimate_tokens(m.content) for m in state["messages"]
    )
    session.updated_at = datetime.now(timezone.utc)

    # ── Step 7: Prune & Save ─────────────────────────────────────────────────
    session = prune_and_summarize(session)
    await store.save(session)

    logger.info(
        "run_agent_workflow complete: session=%s turn=%d intent=%s.",
        session.session_id, session.turn_count, state["intent"],
    )

    return {
        "session_id":   session.session_id,
        "intent":       state["intent"],
        "answer":       final_response,
        "tool_results": [r.model_dump() for r in state["tool_call_results"]],
        "error_log":    [e.model_dump() for e in state["error_log"]],
        "turn_count":   session.turn_count,
    }


# ────────────────────────────────────────────────────────────────────────────
# HITL Resume — continue workflow after human approval
# ────────────────────────────────────────────────────────────────────────────

async def resume_after_approval(
    session_id:  str,
    action_id:   str,
    approver_id: str,
) -> Dict[str, Any]:
    """
    Resume an interrupted agent workflow after a human has approved a pending action.

    Flow
    ----
    1. Load ``SessionState`` from the persistent store.
    2. Validate that the ``action_id`` matches the stored ``PendingAction``
       and that it is still in ``PENDING`` status.
    3. Run :class:`~security.tool_sandbox.ToolGuard` pre-flight checks on the
       saved arguments (a second security gate beyond Pydantic schema validation).
    4. Execute the approved tool inside the standard timeout guard.
    5. Persist the result into the session, update checkpoint, save.
    6. Emit a ``TOOL_APPROVED`` security audit event.

    Args:
        session_id:  Agent session UUID (from the PENDING_APPROVAL response).
        action_id:   UUID from :class:`~memory_store.PendingAction` (must match).
        approver_id: ``user_id`` of the human who clicked "Approve".

    Returns:
        A dict with ``status="APPROVED"``, the tool result, and the session context.

    Raises:
        ValueError: On session-not-found, action_id mismatch, duplicate approval,
                    ToolGuard rejection, or tool execution failure.
    """
    store   = get_memory_store()
    session = await store.load(session_id)
    if session is None:
        raise ValueError(f"Session '{session_id}' not found.")

    pending = session.pending_action
    if pending is None:
        raise ValueError("No pending action found for this session.")
    if pending.action_id != action_id:
        raise ValueError(
            f"action_id mismatch: stored={pending.action_id!r}, received={action_id!r}."
        )
    if pending.status != PendingActionStatus.PENDING:
        raise ValueError(
            f"Action '{action_id}' has already been resolved "
            f"(status='{pending.status.value}')."
        )
    if pending.tool_name not in _TOOL_REGISTRY:
        raise ValueError(f"Unknown tool '{pending.tool_name}' in pending action.")

    # ── Pre-flight security checks ───────────────────────────────────────────
    try:
        sanitized_args = ToolGuard.pre_flight_check(pending.tool_name, pending.raw_args)
    except ValueError as exc:
        pending.status      = PendingActionStatus.REJECTED
        pending.resolved_at = datetime.now(timezone.utc)
        pending.resolved_by = approver_id
        session.pending_action = pending
        await store.save(session)
        asyncio.ensure_future(SecurityAuditLog.record(
            event_type = "TOOL_REJECTED",
            tool_name  = pending.tool_name,
            action_id  = action_id,
            session_id = session_id,
            user_id    = approver_id,
            metadata   = {"reason": f"ToolGuard rejection: {exc}"},
        ))
        raise ValueError(f"Pre-flight validation rejected the action: {exc}") from exc

    # ── Execute the approved tool ────────────────────────────────────────────
    input_schema, _, handler = _TOOL_REGISTRY[pending.tool_name]
    try:
        validated_input = input_schema.model_validate(sanitized_args)
        output = await asyncio.wait_for(
            handler(validated_input),
            timeout=TOOL_CALL_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        asyncio.ensure_future(SecurityAuditLog.record(
            event_type = "TOOL_REJECTED",
            tool_name  = pending.tool_name,
            action_id  = action_id,
            session_id = session_id,
            user_id    = approver_id,
            metadata   = {"error": f"{type(exc).__name__}: {exc}"},
        ))
        raise ValueError(
            f"Tool execution failed after approval: {type(exc).__name__}: {exc}"
        ) from exc

    result_payload = output.model_dump()

    # ── Build human-readable summary (mirrors simulation_agent output) ────────
    result_summary = (
        f"[APPROVED by {approver_id}] "
        f"Simulation for model '{result_payload.get('model_id', '-')}': "
        f"cost/unit ${result_payload.get('cost_per_unit_usd', 0):.4f}, "
        f"throughput {result_payload.get('throughput_uph', 0):.1f} UPH, "
        f"utilization {result_payload.get('utilization_pct', 0)}%."
    )

    # ── Mark approved, update session ────────────────────────────────────────
    pending.status      = PendingActionStatus.APPROVED
    pending.resolved_at = datetime.now(timezone.utc)
    pending.resolved_by = approver_id
    session.pending_action = pending

    # Append the approved tool result as a conversation message so the LLM
    # has full context in subsequent turns (resumption guarantee).
    session.message_history.append(
        StoredMessage(
            role    = "assistant",
            content = result_summary,
            name    = pending.agent_name,
        )
    )

    # Update checkpoint with the approved result so future turns can
    # reference it without re-running the (now-approved) simulation.
    tool_result = ToolCallResult(
        tool_name = pending.tool_name,
        attempt   = 1,
        success   = True,
        output    = result_payload,
    )
    if session.checkpoint:
        session.checkpoint.tool_call_results.append(tool_result.model_dump())
    else:
        session.checkpoint = Checkpoint(
            turn_index        = session.turn_count,
            last_intent       = IntentLabel.SIMULATION_RUN.value,
            tool_call_results = [tool_result.model_dump()],
        )

    session.turn_count += 1
    session.updated_at  = datetime.now(timezone.utc)
    await store.save(session)

    # ── Audit log ────────────────────────────────────────────────────────────
    asyncio.ensure_future(SecurityAuditLog.record(
        event_type = "TOOL_APPROVED",
        tool_name  = pending.tool_name,
        action_id  = action_id,
        session_id = session_id,
        user_id    = approver_id,
        metadata   = {"result_summary": result_summary},
    ))

    logger.info(
        "HITL resume complete: session=%s action_id=%s tool=%s approver=%s.",
        session_id, action_id, pending.tool_name, approver_id,
    )

    return {
        "session_id": session_id,
        "action_id":  action_id,
        "status":     "APPROVED",
        "tool_name":  pending.tool_name,
        "result":     result_payload,
        "summary":    result_summary,
        "turn_count": session.turn_count,
    }


# ────────────────────────────────────────────────────────────────────────────
# CLI smoke-test (run with: python agent_router_poc.py)
# ────────────────────────────────────────────────────────────────────────────

async def _smoke_test() -> None:
    """Runs three representative queries through the full agent pipeline."""
    auth = AuthContext(user_id="eng-001", role="Engineer", jwt_token="stub-jwt")

    queries = [
        "Calculate the MiniMOST TMU for sequence A1B0G1A1B0P3A0 with a proficient operator.",
        "Look up BOM part SQT-K860G6-BASY revision 1.3.",
        "Run a full analysis and cost breakdown for model MODEL-001 with 2 operators.",
    ]

    for query in queries:
        print(f"\n{'─' * 72}")
        print(f"Query: {query}")
        result = await run_agent_workflow(user_query=query, auth=auth)
        print(f"Session : {result['session_id']}")
        print(f"Intent  : {result['intent']}")
        print(f"Answer  :\n{result['answer']}")
        if result["error_log"]:
            print(f"Errors  : {result['error_log']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s  %(message)s")
    asyncio.run(_smoke_test())
