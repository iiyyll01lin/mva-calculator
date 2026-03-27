"""
backend/agents/debate_room.py
────────────────────────────────────────────────────────────────────────────────
Multi-Agent Debate & Consensus Engine — Enterprise MVA Platform v2.0.0

Architecture
────────────
                     ┌────────────────────────────────────┐
  User Query ──────► │        run_debate_session()         │
                     │                                    │
                     │  Turn 1:                           │
                     │    CostOptimizationAgent  ─► Plan A│
                     │                                    │
                     │  Turn 2:                           │
                     │    QualityAndTimeAgent    ─► Plan B│
                     │    (critiques Plan A)              │
                     │                                    │
                     │  Optional extra turns              │
                     │    (CostAgent rebuts Plan B)       │
                     │                                    │
                     │  Final turn:                       │
                     │    ConsensusJudgeAgent    ─► Synth │
                     └────────────────────────────────────┘

Design principles
─────────────────
• Debate turns are sequential — prevents race conditions on shared context.
• Tool calls *within* a single agent turn may be parallelised via asyncio.gather.
• All turns are instrumented with span_type="debate" so Mission Control
  renders them as a distinct "internal argument" thread in the dashboard.
• Pydantic v2 models enforce strict output schemas at every turn boundary.
• The LLM client abstraction routes cost/quality agents to the local tier
  and ConsensusJudge to the cloud tier (see llm_client.AGENT_MODEL_MAP).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from telemetry import LlmUsage, agent_span, estimate_tokens
from llm_client import ChatMessage, LlmCallResult, call_llm

# TemporalAnalyst is imported lazily inside coroutines to avoid circular imports
# (agents/temporal_analyst.py → tools/temporal_rag.py → data_ops/agentic_etl.py)

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────

DEFAULT_MAX_TURNS: int = 3
MAX_TURNS_CEILING: int = 10

DEBATE_AGENT_COST    = "CostOptimizationAgent"
DEBATE_AGENT_QUALITY = "QualityAndTimeAgent"
DEBATE_AGENT_JUDGE   = "ConsensusJudgeAgent"


# ────────────────────────────────────────────────────────────────────────────
# Pydantic Schemas — enforced at every turn boundary
# ────────────────────────────────────────────────────────────────────────────

class DebatePlan(BaseModel):
    """A concrete optimization plan proposed by one of the adversarial agents."""

    plan_id:           str   = Field(..., description="Unique plan identifier, e.g. 'COST-PLAN-A'.")
    summary:           str   = Field(..., description="One-paragraph description of the plan.")
    num_operators:     int   = Field(..., ge=1,  le=200,
                                    description="Total operators required.")
    cycle_time_tmu:    float = Field(..., gt=0,
                                    description="Gross cycle time in TMU.")
    cost_per_unit_usd: float = Field(..., ge=0,
                                    description="Estimated manufacturing cost per unit.")
    throughput_uph:    float = Field(..., ge=0,
                                    description="Units produced per hour.")
    key_changes:       List[str] = Field(default_factory=list,
                                         description="Bullet list of proposed changes.")
    risk_flags:        List[str] = Field(default_factory=list,
                                         description="Known risks or trade-off warnings.")

    model_config = {"extra": "ignore"}


class CritiquePlan(BaseModel):
    """
    A critique produced by one agent reviewing another agent's DebatePlan.
    Embeds an updated counter-proposal in ``counter_plan``.
    """

    critique_id:       str = Field(
        default_factory=lambda: f"CRITIQUE-{uuid.uuid4().hex[:8].upper()}",
    )
    critiqued_plan_id: str = Field(..., description="plan_id of the plan being critiqued.")
    critic_agent:      str = Field(..., description="Name of the agent producing this critique.")
    weaknesses_found:  List[str] = Field(default_factory=list)
    counter_plan:      DebatePlan

    model_config = {"extra": "ignore"}


class ConsensusResult(BaseModel):
    """
    Final synthesized execution plan produced by ConsensusJudgeAgent.
    Resolves trade-offs and traces contributions from both adversarial plans.
    """

    consensus_plan_id:    str = Field(
        default_factory=lambda: f"CONSENSUS-{uuid.uuid4().hex[:8].upper()}",
    )
    summary:              str

    num_operators:        int   = Field(..., ge=1, le=200)
    cycle_time_tmu:       float = Field(..., gt=0)
    cost_per_unit_usd:    float = Field(..., ge=0)
    throughput_uph:       float = Field(..., ge=0)

    adopted_from_cost:    List[str] = Field(default_factory=list,
                                            description="Changes adopted from CostOptimizationAgent.")
    adopted_from_quality: List[str] = Field(default_factory=list,
                                            description="Changes adopted from QualityAndTimeAgent.")
    trade_off_resolution: str  = Field(default="",
                                       description="Explanation of resolved trade-offs.")
    confidence_score:     float = Field(default=0.0, ge=0.0, le=1.0,
                                        description="Judge's confidence in the synthesis (0–1).")
    debate_turns:         int   = Field(default=0,
                                        description="Total debate turns completed.")
    session_id:           str   = Field(default="")

    model_config = {"extra": "ignore"}


class DebateSession(BaseModel):
    """Internal record tracking the full state of a debate session."""

    session_id: str
    query:      str
    turns:      int                        = 0
    plans:      List[DebatePlan]           = Field(default_factory=list)
    critiques:  List[CritiquePlan]         = Field(default_factory=list)
    consensus:  Optional[ConsensusResult]  = None
    started_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ended_at:   Optional[str]             = None


# ────────────────────────────────────────────────────────────────────────────
# System Prompts
# ────────────────────────────────────────────────────────────────────────────

_COST_SYSTEM_PROMPT = """\
You are CostOptimizationAgent — a ruthlessly cost-focused industrial engineering analyst.
Your sole objective is to minimize total manufacturing cost per unit and headcount.
You do NOT consider throughput or quality unless they directly affect unit cost.

When HISTORICAL CONTEXT is provided (between [HISTORICAL CONTEXT START] /
[HISTORICAL CONTEXT END] markers), you MUST reference it in your risk_flags
if there is evidence that similar cost-cutting measures have historically caused
defect spikes or yield deterioration.

Always output a valid JSON object matching this schema exactly:
{
  "plan_id": "COST-PLAN-A",
  "summary": "<one paragraph>",
  "num_operators": <int ≥1>,
  "cycle_time_tmu": <float >0>,
  "cost_per_unit_usd": <float ≥0>,
  "throughput_uph": <float ≥0>,
  "key_changes": ["<change>", ...],
  "risk_flags": ["<risk>", ...]
}
Respond with raw JSON only — no markdown fences, no extra text.\
"""

_QUALITY_SYSTEM_PROMPT = """\
You are QualityAndTimeAgent — a throughput and quality champion.
Your sole objective is to maximize UPH and minimize defect rate / cycle time.
You do NOT heavily weigh cost unless it creates a hard budget constraint.

When HISTORICAL CONTEXT is provided (between [HISTORICAL CONTEXT START] /
[HISTORICAL CONTEXT END] markers), you MUST cite historical anomalies or
defect spikes as weaknesses_found evidence against the CostAgent's plan, and
your counter_plan MUST incorporate lessons from that historical data.

Given Plan A from CostAgent, produce a critique AND an improved counter-plan.
Output a valid JSON object matching this schema exactly:
{
  "critiqued_plan_id": "<plan_id of Plan A>",
  "critic_agent": "QualityAndTimeAgent",
  "weaknesses_found": ["<weakness>", ...],
  "counter_plan": {
    "plan_id": "QUALITY-PLAN-B",
    "summary": "<one paragraph>",
    "num_operators": <int ≥1>,
    "cycle_time_tmu": <float >0>,
    "cost_per_unit_usd": <float ≥0>,
    "throughput_uph": <float ≥0>,
    "key_changes": ["<change>", ...],
    "risk_flags": ["<risk>", ...]
  }
}
Respond with raw JSON only — no markdown fences, no extra text.\
"""

_JUDGE_SYSTEM_PROMPT = """\
You are ConsensusJudgeAgent — an impartial industrial optimization architect.
You receive Plan A (cost-optimized) and Plan B (quality/throughput-optimized).
Your objective: synthesize a globally optimal plan that captures the best of both.
Accept cost savings that do not degrade quality below target; accept quality
improvements that do not price out the product against budget constraints.

Output a valid JSON object matching this schema exactly:
{
  "summary": "<one paragraph>",
  "num_operators": <int ≥1>,
  "cycle_time_tmu": <float >0>,
  "cost_per_unit_usd": <float ≥0>,
  "throughput_uph": <float ≥0>,
  "adopted_from_cost": ["<change adopted>", ...],
  "adopted_from_quality": ["<change adopted>", ...],
  "trade_off_resolution": "<explanation of how conflicts were resolved>",
  "confidence_score": <float 0.0–1.0>
}
Respond with raw JSON only — no markdown fences, no extra text.\
"""


# ────────────────────────────────────────────────────────────────────────────
# Helper: safe JSON parse → Pydantic validation
# ────────────────────────────────────────────────────────────────────────────

def _parse_or_raise(raw: str, schema: type[BaseModel], agent_name: str) -> BaseModel:
    """
    Parse raw LLM output as JSON and validate against ``schema``.

    Strips accidental markdown code fences before parsing.
    Raises ``ValueError`` with a descriptive message on failure so callers
    can surface the error to telemetry without a bare exception.
    """
    cleaned = raw.strip()
    # Strip ```json ... ``` or ``` ... ``` fences
    if cleaned.startswith("```"):
        lines   = cleaned.splitlines()
        cleaned = "\n".join(lines[1:])          # drop opening fence line
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3].rstrip()
    try:
        data = json.loads(cleaned)
        return schema.model_validate(data)
    except (json.JSONDecodeError, Exception) as exc:
        raise ValueError(
            f"{agent_name} produced invalid output: {exc}\n"
            f"Raw (first 400 chars): {raw[:400]}"
        ) from exc


# ────────────────────────────────────────────────────────────────────────────
# Adversarial Agent: CostOptimizationAgent
# ────────────────────────────────────────────────────────────────────────────

async def _fetch_temporal_context(
    query:      str,
    session_id: str,
) -> str:
    """
    Helper: invoke TemporalAnalystAgent to retrieve historical context.

    Returns a formatted context block for injection into agent system prompts.
    Returns an empty string if the store is empty or the analyst errors out,
    so the debate can continue without blocking on missing historical data.
    """
    try:
        from agents.temporal_analyst import TemporalAnalystAgent
        analyst  = TemporalAnalystAgent()
        analysis = await analyst.analyse(query=query, session_id=session_id)
        if analysis.records_analysed == 0:
            return ""
        return (
            "\n\n[HISTORICAL CONTEXT START]\n"
            f"{analysis.synthesis}\n"
            "[HISTORICAL CONTEXT END]\n"
        )
    except Exception as exc:
        logger.warning(
            "TemporalAnalyst tool call failed in DebateRoom (%s): %s",
            session_id, exc,
        )
        return ""


async def run_cost_agent(
    query:      str,
    session_id: str,
    turn:       int = 1,
) -> DebatePlan:
    """
    Generate Plan A: a cost-minimisation proposal for the given query.

    Before proposing, fetches historical context from TemporalAnalystAgent so
    it can flag risks grounded in real manufacturing history.

    Instrumented with ``span_type="debate"`` so Mission Control renders it
    distinctly from normal agent spans.  The ``debate_event`` metadata key
    carries a human-readable status string for the live thought stream.
    """
    # Fetch historical context as a tool call (non-blocking; empty str if no data)
    hist_context = await _fetch_temporal_context(query, session_id)

    async with agent_span(
        span_name  = f"debate/cost_agent/turn_{turn}",
        span_type  = "debate",
        agent_name = DEBATE_AGENT_COST,
        trace_id   = session_id,
    ) as span:
        span.metadata["debate_turn"]          = turn
        span.metadata["debate_role"]          = "proposer"
        span.metadata["has_temporal_context"] = bool(hist_context)
        span.metadata["debate_event"] = (
            f"[Turn {turn}] CostOptimizationAgent is proposing Plan A"
            + (" (with historical context)" if hist_context else "") + "…"
        )

        enriched_query = query + hist_context
        messages = [
            ChatMessage(role="system", content=_COST_SYSTEM_PROMPT),
            ChatMessage(role="user",   content=f"Manufacturing optimization query:\n{enriched_query}"),
        ]
        span.prompt = messages[-1].content

        result: LlmCallResult = await call_llm(
            messages   = messages,
            agent_name = DEBATE_AGENT_COST,
        )

        span.raw_output  = result.content
        span.token_usage = LlmUsage.from_counts(
            prompt_tokens     = result.prompt_tokens,
            completion_tokens = result.completion_tokens,
        )
        span.metadata["model_tier"] = result.tier.value

        plan = _parse_or_raise(result.content, DebatePlan, DEBATE_AGENT_COST)
        logger.info(
            "CostAgent Plan A: %d operators, %.2f UPH, $%.4f/unit (session=%s)",
            plan.num_operators, plan.throughput_uph, plan.cost_per_unit_usd, session_id,
        )
        return plan


# ────────────────────────────────────────────────────────────────────────────
# Adversarial Agent: QualityAndTimeAgent
# ────────────────────────────────────────────────────────────────────────────

async def run_quality_agent(
    query:      str,
    plan_a:     DebatePlan,
    session_id: str,
    turn:       int = 2,
) -> CritiquePlan:
    """
    Critique Plan A and propose Plan B (quality/throughput-maximising counter-plan).

    Fetches historical context from TemporalAnalystAgent so it can cite
    evidence of past defect spikes as argument against the CostAgent's proposal.

    Instrumented with ``span_type="debate"``.
    """
    # Fetch historical context — QualityAgent uses it to argue against risky changes
    hist_context = await _fetch_temporal_context(query, session_id)

    async with agent_span(
        span_name  = f"debate/quality_agent/turn_{turn}",
        span_type  = "debate",
        agent_name = DEBATE_AGENT_QUALITY,
        trace_id   = session_id,
    ) as span:
        span.metadata["debate_turn"]          = turn
        span.metadata["debate_role"]          = "critic"
        span.metadata["has_temporal_context"] = bool(hist_context)
        span.metadata["debate_event"] = (
            f"[Turn {turn}] QualityAndTimeAgent is critiquing Plan A"
            + (" (with historical context)" if hist_context else "") + "…"
        )

        plan_a_json   = plan_a.model_dump_json(indent=2)
        context_block = f"Original query:\n{query}{hist_context}"
        messages = [
            ChatMessage(role="system",    content=_QUALITY_SYSTEM_PROMPT),
            ChatMessage(role="user",      content=context_block),
            ChatMessage(role="assistant", content=f"Plan A (CostAgent):\n{plan_a_json}"),
            ChatMessage(role="user",      content="Critique Plan A and propose your counter-plan (Plan B)."),
        ]
        span.prompt = plan_a_json

        result: LlmCallResult = await call_llm(
            messages   = messages,
            agent_name = DEBATE_AGENT_QUALITY,
        )

        span.raw_output  = result.content
        span.token_usage = LlmUsage.from_counts(
            prompt_tokens     = result.prompt_tokens,
            completion_tokens = result.completion_tokens,
        )
        span.metadata["model_tier"] = result.tier.value

        critique = _parse_or_raise(result.content, CritiquePlan, DEBATE_AGENT_QUALITY)
        logger.info(
            "QualityAgent Plan B: %d operators, %.2f UPH, $%.4f/unit (session=%s)",
            critique.counter_plan.num_operators,
            critique.counter_plan.throughput_uph,
            critique.counter_plan.cost_per_unit_usd,
            session_id,
        )
        return critique


# ────────────────────────────────────────────────────────────────────────────
# Consensus Judge
# ────────────────────────────────────────────────────────────────────────────

async def run_consensus_judge(
    query:      str,
    plan_a:     DebatePlan,
    critique:   CritiquePlan,
    session_id: str,
    turn:       int = 3,
) -> ConsensusResult:
    """
    Evaluate Plan A and Plan B; synthesize the globally optimal consensus plan.

    Routes to the cloud-tier model (configured in ``llm_client.AGENT_MODEL_MAP``)
    for the highest available reasoning quality.

    Instrumented with ``span_type="debate"``.
    """
    async with agent_span(
        span_name  = f"debate/consensus_judge/turn_{turn}",
        span_type  = "debate",
        agent_name = DEBATE_AGENT_JUDGE,
        trace_id   = session_id,
    ) as span:
        span.metadata["debate_turn"]  = turn
        span.metadata["debate_role"]  = "judge"
        span.metadata["debate_event"] = (
            f"[Turn {turn}] ConsensusJudge is synthesizing the final optimal plan…"
        )

        plan_b   = critique.counter_plan
        context  = (
            f"Original query:\n{query}\n\n"
            f"Plan A (Cost-Optimized):\n{plan_a.model_dump_json(indent=2)}\n\n"
            f"Critique by QualityAgent:\n"
            f"  Weaknesses: {json.dumps(critique.weaknesses_found)}\n\n"
            f"Plan B (Quality-Optimized):\n{plan_b.model_dump_json(indent=2)}"
        )
        messages = [
            ChatMessage(role="system", content=_JUDGE_SYSTEM_PROMPT),
            ChatMessage(role="user",   content=context),
        ]
        span.prompt = context

        result: LlmCallResult = await call_llm(
            messages   = messages,
            agent_name = DEBATE_AGENT_JUDGE,
        )

        span.raw_output  = result.content
        span.token_usage = LlmUsage.from_counts(
            prompt_tokens     = result.prompt_tokens,
            completion_tokens = result.completion_tokens,
        )
        span.metadata["model_tier"] = result.tier.value

        # Use _parse_or_raise for consistent fence-stripping + validation
        raw_obj = _parse_or_raise(result.content, ConsensusResult, DEBATE_AGENT_JUDGE)
        # Stamp session metadata that can't come from the LLM
        consensus = raw_obj.model_copy(update={
            "session_id":   session_id,
            "debate_turns": turn,
        })
        logger.info(
            "Consensus plan: %d operators, %.2f UPH, $%.4f/unit, confidence=%.2f (session=%s)",
            consensus.num_operators,
            consensus.throughput_uph,
            consensus.cost_per_unit_usd,
            consensus.confidence_score,
            session_id,
        )
        return consensus


# ────────────────────────────────────────────────────────────────────────────
# Public API: run_debate_session
# ────────────────────────────────────────────────────────────────────────────

async def run_debate_session(
    query:      str,
    session_id: str,
    max_turns:  int = DEFAULT_MAX_TURNS,
) -> ConsensusResult:
    """
    Orchestrate a full multi-agent debate session for a complex optimisation query.

    Turn structure (default ``max_turns=3``):
      Turn 1 — CostOptimizationAgent  → Plan A
      Turn 2 — QualityAndTimeAgent    → Critique A + Plan B
      Turn 3 — ConsensusJudgeAgent    → Synthesis

    When ``max_turns > 3``, an additional rebuttal round is inserted:
      Turn 3 — CostOptimizationAgent  → Rebuttal of Plan B
      Turn 4 — ConsensusJudgeAgent    → Synthesis (with rebuttal context)

    Debate turns are **sequential** — each turn receives the full context of
    all prior turns.  Tool calls *within* a single turn may be parallelised
    by the individual agent coroutines using ``asyncio.gather``.

    The root ``debate_session`` span is emitted with ``span_type="debate"``
    and ``debate_event`` metadata so Mission Control can render a dedicated
    "Debate Room" view in the dashboard.

    Args:
        query:      Natural-language manufacturing optimisation query.
        session_id: Telemetry trace ID (pass the parent AgentState session_id).
        max_turns:  Hard cap on debate rounds. Must be ≥ 3; capped at 10.

    Returns:
        :class:`ConsensusResult` — the final synthesized execution plan.

    Raises:
        ValueError: if ``max_turns < 3``.
    """
    if max_turns < 3:
        raise ValueError(
            "max_turns must be >= 3 "
            "(turn 1: cost proposer, turn 2: quality critic, turn 3: consensus judge)."
        )
    max_turns = min(max_turns, MAX_TURNS_CEILING)

    async with agent_span(
        span_name  = "debate_session",
        span_type  = "debate",
        agent_name = "DebateRoom",
        trace_id   = session_id,
    ) as root_span:
        root_span.prompt = query
        root_span.metadata["max_turns"]    = max_turns
        root_span.metadata["debate_event"] = (
            "Debate session initiated — adversarial agents assembling…"
        )

        db_session = DebateSession(session_id=session_id, query=query)

        # ── Turn 1: CostOptimizationAgent proposes Plan A ───────────────────
        plan_a = await run_cost_agent(query=query, session_id=session_id, turn=1)
        db_session.plans.append(plan_a)
        db_session.turns = 1

        # ── Turn 2: QualityAndTimeAgent critiques Plan A + proposes Plan B ──
        critique = await run_quality_agent(
            query      = query,
            plan_a     = plan_a,
            session_id = session_id,
            turn       = 2,
        )
        db_session.critiques.append(critique)
        db_session.plans.append(critique.counter_plan)
        db_session.turns = 2

        # ── Optional extra turns: CostAgent rebuts Quality plan ─────────────
        current_turn  = 3
        active_plan_a = plan_a      # may be superseded by rebuttal below

        if max_turns > 3:
            rebuttal_query = (
                f"{query}\n\n"
                f"[Rebuttal context: respond to the quality counter-plan below]\n"
                f"{critique.counter_plan.model_dump_json(indent=2)}"
            )
            rebuttal = await run_cost_agent(
                query      = rebuttal_query,
                session_id = session_id,
                turn       = current_turn,
            )
            db_session.plans.append(rebuttal)
            active_plan_a = rebuttal       # judge sees the updated cost position
            current_turn += 1
            db_session.turns = current_turn - 1

        # ── Final turn: ConsensusJudgeAgent synthesizes ─────────────────────
        consensus = await run_consensus_judge(
            query      = query,
            plan_a     = active_plan_a,
            critique   = critique,
            session_id = session_id,
            turn       = current_turn,
        )
        consensus = consensus.model_copy(update={
            "debate_turns": current_turn,
            "session_id":   session_id,
        })

        db_session.consensus = consensus
        db_session.ended_at  = datetime.now(timezone.utc).isoformat()
        db_session.turns     = current_turn

        root_span.raw_output = consensus.model_dump_json()
        root_span.metadata["debate_turns_completed"] = current_turn
        root_span.metadata["confidence_score"]       = consensus.confidence_score

        logger.info(
            "Debate session complete: %d turns, confidence=%.2f (session=%s)",
            current_turn, consensus.confidence_score, session_id,
        )
        return consensus
