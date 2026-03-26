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
    SUPERVISOR = "SupervisorAgent"
    LABOR_TIME = "LaborTimeAgent"
    BOM        = "BomAgent"
    SIMULATION = "SimulationAgent"


class IntentLabel(str, Enum):
    """Canonical intent labels the Supervisor assigns to an inbound query."""
    LABOR_TIME_ANALYSIS  = "labor_time_analysis"
    BOM_LOOKUP           = "bom_lookup"
    SIMULATION_RUN       = "simulation_run"
    MULTI_STEP           = "multi_step"
    UNKNOWN              = "unknown"


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

    input_schema, output_schema, handler = _TOOL_REGISTRY[tool_name]

    # Ensure per-tool retry counter exists in state
    state["retry_counts"].setdefault(tool_name, 0)

    last_error: str = ""

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
            _record_error(state, tool_name, attempt, last_error)
            await _exponential_backoff(attempt)
            continue   # retry after back-off
        except Exception as exc:
            last_error = f"Tool execution error: {type(exc).__name__}: {exc}"
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
        return result

    # ── All retries exhausted ────────────────────────────────────────────────
    _record_error(state, tool_name, MAX_RETRIES_PER_TOOL, last_error)
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

    # Stub: return the original args unchanged (real impl would call the LLM here)
    # In production:
    #   corrected_json = await llm_client.call(messages=state["messages"], ...)
    #   return json.loads(corrected_json)
    await asyncio.sleep(0)
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
    """
    state["active_agent"] = AgentName.LABOR_TIME.value
    logger.info("LaborTimeAgent activated (session=%s).", state["session_id"])

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

    state["messages"].append(Message(role="assistant", content=summary, name=AgentName.LABOR_TIME.value))
    state["updated_at"] = datetime.now(timezone.utc)
    return state


async def bom_agent(state: AgentState) -> AgentState:
    """
    BomAgent — specializes in BOM structure and part validation.

    Extracts part number from the user query and calls lookup_bom_item.
    """
    state["active_agent"] = AgentName.BOM.value
    logger.info("BomAgent activated (session=%s).", state["session_id"])

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

    state["messages"].append(Message(role="assistant", content=summary, name=AgentName.BOM.value))
    state["updated_at"] = datetime.now(timezone.utc)
    return state


async def simulation_agent(state: AgentState) -> AgentState:
    """
    SimulationAgent — runs cycle-time and cost simulations.

    Extracts simulation parameters from the user query and calls run_simulation.
    """
    state["active_agent"] = AgentName.SIMULATION.value
    logger.info("SimulationAgent activated (session=%s).", state["session_id"])

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

    state["messages"].append(Message(role="assistant", content=summary, name=AgentName.SIMULATION.value))
    state["updated_at"] = datetime.now(timezone.utc)
    return state


# ── Agent dispatch map (intent → agent coroutine) ────────────────────────────
_AGENT_MAP: Dict[str, Callable[[AgentState], Coroutine[Any, Any, AgentState]]] = {
    AgentName.LABOR_TIME.value: labor_time_agent,
    AgentName.BOM.value:        bom_agent,
    AgentName.SIMULATION.value: simulation_agent,
}


# ────────────────────────────────────────────────────────────────────────────
# Supervisor Agent — Router + Orchestrator
# ────────────────────────────────────────────────────────────────────────────

# Semaphore limiting concurrent LLM calls initiated by the Supervisor.
_llm_semaphore = asyncio.Semaphore(LLM_CONCURRENCY_LIMIT)


async def _llm_route(user_message: str) -> RouterDecision:
    """
    Stub LLM call that classifies intent and returns a routing decision.

    In production this calls the LLM with a function-calling / structured-output
    request and parses the JSON response into a RouterDecision.

    The stub uses keyword matching to demonstrate the routing logic.
    """
    async with _llm_semaphore:
        # Simulate network latency to the LLM provider
        await asyncio.sleep(0)

        lowered = user_message.lower()

        if any(kw in lowered for kw in ("tmu", "motion", "minimost", "sequence", "labor")):
            return RouterDecision(
                intent          = IntentLabel.LABOR_TIME_ANALYSIS,
                target_agents   = [AgentName.LABOR_TIME],
                reasoning       = "Query contains MiniMOST / labor-time keywords.",
                run_in_parallel = False,
            )
        if any(kw in lowered for kw in ("bom", "part", "component", "revision", "lead time")):
            return RouterDecision(
                intent          = IntentLabel.BOM_LOOKUP,
                target_agents   = [AgentName.BOM],
                reasoning       = "Query relates to bill-of-materials lookup.",
                run_in_parallel = False,
            )
        if any(kw in lowered for kw in ("simulation", "simulate", "cycle time", "throughput", "cost")):
            return RouterDecision(
                intent          = IntentLabel.SIMULATION_RUN,
                target_agents   = [AgentName.SIMULATION],
                reasoning       = "Query requests a simulation scenario.",
                run_in_parallel = False,
            )
        # If the query touches both labor and cost, fan out to both agents in parallel.
        if any(kw in lowered for kw in ("full analysis", "complete report", "breakdown")):
            return RouterDecision(
                intent          = IntentLabel.MULTI_STEP,
                target_agents   = [AgentName.LABOR_TIME, AgentName.BOM, AgentName.SIMULATION],
                reasoning       = "Multi-step query; all three agents contribute independently.",
                run_in_parallel = True,
            )

        return RouterDecision(
            intent          = IntentLabel.UNKNOWN,
            target_agents   = [AgentName.LABOR_TIME],
            reasoning       = "Could not classify intent; defaulting to LaborTimeAgent.",
            run_in_parallel = False,
        )


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

    # ── Step 1: Route ────────────────────────────────────────────────────────
    decision        = await _llm_route(user_message)
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

    logger.info("SupervisorAgent finished (session=%s).", state["session_id"])
    return state


# ────────────────────────────────────────────────────────────────────────────
# Public entrypoint — drop-in for FastAPI endpoint integration
# ────────────────────────────────────────────────────────────────────────────

async def run_agent_workflow(
    user_query: str,
    auth:       AuthContext,
) -> Dict[str, Any]:
    """
    Entrypoint consumed by the FastAPI route handler.

    Example integration in main.py::

        from agent_router_poc import run_agent_workflow, AuthContext

        @app.post("/agent/query")
        async def agent_query(request: AgentQueryRequest, credentials = Depends(security)):
            auth = AuthContext(
                user_id   = current_user["user_id"],
                role      = current_user["role"],
                jwt_token = credentials.credentials,
            )
            return await run_agent_workflow(user_query=request.query, auth=auth)

    Args:
        user_query: Natural-language query string from the authenticated user.
        auth:       JWT-derived credentials forwarded to all tool calls.

    Returns:
        A dict with session_id, final answer, tool results, and error log.
    """
    state   = new_agent_state(user_query, auth)
    state   = await supervisor_agent(state)

    # Extract the final synthesized response (last Supervisor message)
    final_response = next(
        (m.content for m in reversed(state["messages"])
         if m.role == "assistant" and m.name == AgentName.SUPERVISOR.value),
        "No response generated.",
    )

    return {
        "session_id":   state["session_id"],
        "intent":       state["intent"],
        "answer":       final_response,
        "tool_results": [r.model_dump() for r in state["tool_call_results"]],
        "error_log":    [e.model_dump() for e in state["error_log"]],
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
