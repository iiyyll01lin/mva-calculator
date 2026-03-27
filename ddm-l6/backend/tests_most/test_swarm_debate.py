"""
backend/tests_most/test_swarm_debate.py
────────────────────────────────────────────────────────────────────────────────
Unit + integration tests for the Multi-Agent Debate & Consensus Engine.

Test coverage:
  1. Pydantic schema validation for DebatePlan, CritiquePlan, ConsensusResult.
  2. Individual agent coroutines return validated models when given stubbed LLM output.
  3. Invalid LLM output triggers a descriptive ValueError.
  4. Full debate session (max_turns=3) completes and returns a ConsensusResult.
  5. Full debate session output is JSON-serializable with correct field values.
  6. max_turns < 3 raises ValueError.
  7. max_turns=4 triggers the extra rebuttal round (≥4 LLM calls).
  8. Telemetry spans with span_type="debate" are emitted during the session.
  9. Supervisor routing keywords direct queries to the DebateRoom agent.
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
from unittest.mock import AsyncMock, patch

import pytest

# Ensure the backend package root is on the path when running from tests_most/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.debate_room import (
    ConsensusResult,
    CritiquePlan,
    DebatePlan,
    DebateSession,
    run_consensus_judge,
    run_cost_agent,
    run_debate_session,
    run_quality_agent,
)
from llm_client import LlmCallResult, ModelTier

# ────────────────────────────────────────────────────────────────────────────
# Shared test fixtures / helpers
# ────────────────────────────────────────────────────────────────────────────

SAMPLE_QUERY = (
    "Optimize the assembly line for Model-X DIMM module. "
    "Target: 60 UPH with <2% defect rate."
)
SESSION_ID = "test-debate-session-001"


def _cost_json() -> str:
    return json.dumps({
        "plan_id":            "COST-PLAN-A",
        "summary":            "Merge stations 3 and 4; reduce operators to 3.",
        "num_operators":      3,
        "cycle_time_tmu":     420.0,
        "cost_per_unit_usd":  1.85,
        "throughput_uph":     52.4,
        "key_changes":        ["Merge stations 3+4", "Automate barcode scanning"],
        "risk_flags":         ["Fatigue risk at merged station"],
    })


def _quality_json() -> str:
    return json.dumps({
        "critiqued_plan_id": "COST-PLAN-A",
        "critic_agent":      "QualityAndTimeAgent",
        "weaknesses_found":  [
            "Merged station reduces quality inspection coverage",
            "3 operators creates a bottleneck",
        ],
        "counter_plan": {
            "plan_id":            "QUALITY-PLAN-B",
            "summary":            "Add inline AOI and 5th operator for parallel flow.",
            "num_operators":      5,
            "cycle_time_tmu":     380.0,
            "cost_per_unit_usd":  2.40,
            "throughput_uph":     61.8,
            "key_changes":        ["Inline AOI at exit", "Split screw-driving station"],
            "risk_flags":         ["30% higher labor cost"],
        },
    })


def _consensus_json() -> str:
    return json.dumps({
        "consensus_plan_id":  "CONSENSUS-OPTIMAL",
        "summary":            "Balanced: 4 operators, smart merge + lightweight AOI.",
        "num_operators":      4,
        "cycle_time_tmu":     395.0,
        "cost_per_unit_usd":  2.10,
        "throughput_uph":     58.2,
        "adopted_from_cost":    ["Merge stations 3+4", "Automate barcode scanning"],
        "adopted_from_quality": ["Inline AOI at exit"],
        "trade_off_resolution": "Dedicated rework loop deferred to Phase 2.",
        "confidence_score":   0.87,
    })


def _make_llm_result(content: str, tier: ModelTier = ModelTier.LOCAL) -> LlmCallResult:
    return LlmCallResult(
        content=content, prompt_tokens=100, completion_tokens=80,
        model="stub", tier=tier,
    )


def _route_call_llm(messages, agent_name, **kwargs):
    """Route mock LLM calls to the correct stub response by agent name."""
    responses = {
        "CostOptimizationAgent": _cost_json(),
        "QualityAndTimeAgent":   _quality_json(),
        "ConsensusJudgeAgent":   _consensus_json(),
    }
    content = responses.get(agent_name, _consensus_json())
    return asyncio.coroutine(lambda: _make_llm_result(content))()


# ────────────────────────────────────────────────────────────────────────────
# 1. Schema validation: DebatePlan
# ────────────────────────────────────────────────────────────────────────────

class TestDebatePlanSchema:

    def test_valid_construction(self):
        plan = DebatePlan(
            plan_id="TEST-001",
            summary="Test plan",
            num_operators=3,
            cycle_time_tmu=400.0,
            cost_per_unit_usd=1.90,
            throughput_uph=55.0,
        )
        assert plan.plan_id == "TEST-001"
        assert plan.num_operators == 3
        assert plan.key_changes == []
        assert plan.risk_flags == []

    def test_rejects_zero_operators(self):
        with pytest.raises(Exception):
            DebatePlan(
                plan_id="BAD", summary="Bad",
                num_operators=0,          # must be ≥ 1
                cycle_time_tmu=400.0,
                cost_per_unit_usd=1.90,
                throughput_uph=55.0,
            )

    def test_rejects_negative_cycle_time(self):
        with pytest.raises(Exception):
            DebatePlan(
                plan_id="BAD", summary="Bad",
                num_operators=3,
                cycle_time_tmu=-1.0,      # must be > 0
                cost_per_unit_usd=1.90,
                throughput_uph=55.0,
            )

    def test_rejects_excessive_operators(self):
        with pytest.raises(Exception):
            DebatePlan(
                plan_id="BAD", summary="Bad",
                num_operators=201,        # max 200
                cycle_time_tmu=400.0,
                cost_per_unit_usd=1.90,
                throughput_uph=55.0,
            )

    def test_extra_fields_ignored(self):
        """extra='ignore' means unrecognised fields don't raise an error."""
        plan = DebatePlan.model_validate({
            "plan_id": "OK", "summary": "fine",
            "num_operators": 2, "cycle_time_tmu": 300.0,
            "cost_per_unit_usd": 1.0, "throughput_uph": 40.0,
            "unexpected_field": "should be silently dropped",
        })
        assert not hasattr(plan, "unexpected_field")


# ────────────────────────────────────────────────────────────────────────────
# 2. Schema validation: CritiquePlan
# ────────────────────────────────────────────────────────────────────────────

class TestCritiquePlanSchema:

    def test_valid_construction(self):
        counter = DebatePlan(
            plan_id="B", summary="B plan",
            num_operators=4, cycle_time_tmu=380.0,
            cost_per_unit_usd=2.0, throughput_uph=60.0,
        )
        critique = CritiquePlan(
            critiqued_plan_id="COST-PLAN-A",
            critic_agent="QualityAndTimeAgent",
            weaknesses_found=["Too few operators"],
            counter_plan=counter,
        )
        assert critique.critiqued_plan_id == "COST-PLAN-A"
        assert critique.counter_plan.num_operators == 4
        assert critique.critique_id.startswith("CRITIQUE-")

    def test_critique_id_auto_generated(self):
        counter = DebatePlan(
            plan_id="B", summary="B",
            num_operators=2, cycle_time_tmu=300.0,
            cost_per_unit_usd=1.0, throughput_uph=40.0,
        )
        c1 = CritiquePlan(
            critiqued_plan_id="X", critic_agent="Y",
            counter_plan=counter,
        )
        c2 = CritiquePlan(
            critiqued_plan_id="X", critic_agent="Y",
            counter_plan=counter,
        )
        assert c1.critique_id != c2.critique_id


# ────────────────────────────────────────────────────────────────────────────
# 3. Schema validation: ConsensusResult
# ────────────────────────────────────────────────────────────────────────────

class TestConsensusResultSchema:

    def test_confidence_score_above_one_rejected(self):
        with pytest.raises(Exception):
            ConsensusResult(
                summary="bad", num_operators=4,
                cycle_time_tmu=400.0, cost_per_unit_usd=2.0,
                throughput_uph=58.0,
                confidence_score=1.5,   # must be ≤ 1.0
            )

    def test_confidence_score_below_zero_rejected(self):
        with pytest.raises(Exception):
            ConsensusResult(
                summary="bad", num_operators=4,
                cycle_time_tmu=400.0, cost_per_unit_usd=2.0,
                throughput_uph=58.0,
                confidence_score=-0.1,
            )

    def test_valid_consensus_defaults(self):
        c = ConsensusResult(
            summary="Good plan",
            num_operators=4,
            cycle_time_tmu=395.0,
            cost_per_unit_usd=2.10,
            throughput_uph=58.2,
            confidence_score=0.87,
        )
        assert c.debate_turns == 0
        assert c.session_id == ""
        assert c.consensus_plan_id.startswith("CONSENSUS-")


# ────────────────────────────────────────────────────────────────────────────
# 4. Individual agents with mocked LLM
# ────────────────────────────────────────────────────────────────────────────

class TestCostOptimizationAgent:

    @pytest.mark.asyncio
    async def test_returns_valid_plan(self):
        mock_result = _make_llm_result(_cost_json())
        with patch("agents.debate_room.call_llm", new=AsyncMock(return_value=mock_result)):
            plan = await run_cost_agent(SAMPLE_QUERY, SESSION_ID, turn=1)

        assert isinstance(plan, DebatePlan)
        assert plan.plan_id == "COST-PLAN-A"
        assert plan.num_operators == 3
        assert plan.throughput_uph > 0

    @pytest.mark.asyncio
    async def test_invalid_json_raises_value_error(self):
        bad_result = _make_llm_result("not valid json at all !!!")
        with patch("agents.debate_room.call_llm", new=AsyncMock(return_value=bad_result)):
            with pytest.raises(ValueError, match="invalid output"):
                await run_cost_agent(SAMPLE_QUERY, SESSION_ID, turn=1)

    @pytest.mark.asyncio
    async def test_strips_markdown_fences(self):
        fenced = f"```json\n{_cost_json()}\n```"
        mock_result = _make_llm_result(fenced)
        with patch("agents.debate_room.call_llm", new=AsyncMock(return_value=mock_result)):
            plan = await run_cost_agent(SAMPLE_QUERY, SESSION_ID, turn=1)
        assert isinstance(plan, DebatePlan)


class TestQualityAndTimeAgent:

    @pytest.mark.asyncio
    async def test_returns_valid_critique(self):
        plan_a = DebatePlan.model_validate(json.loads(_cost_json()))
        mock_result = _make_llm_result(_quality_json())
        with patch("agents.debate_room.call_llm", new=AsyncMock(return_value=mock_result)):
            result = await run_quality_agent(SAMPLE_QUERY, plan_a, SESSION_ID, turn=2)

        # v3.0: run_quality_agent returns (CritiquePlan, Optional[dict]) tuple
        critique = result[0] if isinstance(result, tuple) else result
        assert isinstance(critique, CritiquePlan)
        assert critique.critiqued_plan_id == "COST-PLAN-A"
        assert len(critique.weaknesses_found) > 0
        assert isinstance(critique.counter_plan, DebatePlan)
        assert critique.counter_plan.plan_id == "QUALITY-PLAN-B"

    @pytest.mark.asyncio
    async def test_counter_plan_higher_throughput(self):
        """Quality agent's counter-plan should beat the cost plan on throughput."""
        plan_a = DebatePlan.model_validate(json.loads(_cost_json()))
        mock_result = _make_llm_result(_quality_json())
        with patch("agents.debate_room.call_llm", new=AsyncMock(return_value=mock_result)):
            result = await run_quality_agent(SAMPLE_QUERY, plan_a, SESSION_ID, turn=2)

        # v3.0: run_quality_agent returns (CritiquePlan, Optional[dict]) tuple
        critique = result[0] if isinstance(result, tuple) else result
        assert critique.counter_plan.throughput_uph > plan_a.throughput_uph


class TestConsensusJudgeAgent:

    @pytest.mark.asyncio
    async def test_returns_valid_consensus(self):
        plan_a   = DebatePlan.model_validate(json.loads(_cost_json()))
        critique = CritiquePlan.model_validate(json.loads(_quality_json()))
        mock_result = _make_llm_result(
            _consensus_json(), tier=ModelTier.CLOUD
        )
        with patch("agents.debate_room.call_llm", new=AsyncMock(return_value=mock_result)):
            result = await run_consensus_judge(SAMPLE_QUERY, plan_a, critique, SESSION_ID, turn=3)

        assert isinstance(result, ConsensusResult)
        assert result.session_id == SESSION_ID
        assert result.debate_turns == 3
        assert 0.0 <= result.confidence_score <= 1.0


# ────────────────────────────────────────────────────────────────────────────
# 5–8. Full debate session pipeline
# ────────────────────────────────────────────────────────────────────────────

class TestRunDebateSession:

    def _mock_call_llm(self, responses: dict):
        """Return an async mock that routes by agent_name."""
        async def _dispatch(messages, agent_name, **kwargs):
            content = responses.get(agent_name, _consensus_json())
            return _make_llm_result(content)
        return _dispatch

    @pytest.mark.asyncio
    async def test_completes_within_max_turns(self):
        """Full session with max_turns=3 returns a valid ConsensusResult."""
        responses = {
            "CostOptimizationAgent": _cost_json(),
            "QualityAndTimeAgent":   _quality_json(),
            "ConsensusJudgeAgent":   _consensus_json(),
        }
        with patch("agents.debate_room.call_llm", new=self._mock_call_llm(responses)):
            result = await run_debate_session(
                query=SAMPLE_QUERY, session_id=SESSION_ID, max_turns=3
            )

        assert isinstance(result, ConsensusResult)
        assert result.debate_turns <= 3
        assert result.num_operators >= 1
        assert result.throughput_uph > 0
        assert result.cost_per_unit_usd > 0
        assert 0.0 <= result.confidence_score <= 1.0

    @pytest.mark.asyncio
    async def test_both_agents_contribute_to_consensus(self):
        """Consensus must reference at least one change from each adversarial agent."""
        responses = {
            "CostOptimizationAgent": _cost_json(),
            "QualityAndTimeAgent":   _quality_json(),
            "ConsensusJudgeAgent":   _consensus_json(),
        }
        with patch("agents.debate_room.call_llm", new=self._mock_call_llm(responses)):
            result = await run_debate_session(SAMPLE_QUERY, SESSION_ID, max_turns=3)

        total_contributions = (
            len(result.adopted_from_cost) + len(result.adopted_from_quality)
        )
        assert total_contributions > 0

    @pytest.mark.asyncio
    async def test_output_is_json_serializable(self):
        """ConsensusResult must round-trip through JSON without data loss."""
        responses = {
            "CostOptimizationAgent": _cost_json(),
            "QualityAndTimeAgent":   _quality_json(),
            "ConsensusJudgeAgent":   _consensus_json(),
        }
        with patch("agents.debate_room.call_llm", new=self._mock_call_llm(responses)):
            result = await run_debate_session(SAMPLE_QUERY, SESSION_ID, max_turns=3)

        serialized = result.model_dump_json()
        parsed     = json.loads(serialized)
        assert parsed["num_operators"]      == result.num_operators
        assert parsed["throughput_uph"]     == result.throughput_uph
        assert parsed["confidence_score"]   == result.confidence_score

    @pytest.mark.asyncio
    async def test_max_turns_below_minimum_raises(self):
        """max_turns < 3 must raise ValueError before any LLM call."""
        with pytest.raises(ValueError, match="max_turns must be >= 3"):
            await run_debate_session(SAMPLE_QUERY, SESSION_ID, max_turns=2)

    @pytest.mark.asyncio
    async def test_extra_rebuttal_round_fires(self):
        """max_turns=4 triggers a rebuttal: CostAgent is called twice (≥4 total calls)."""
        call_count = 0
        responses = {
            "CostOptimizationAgent": _cost_json(),
            "QualityAndTimeAgent":   _quality_json(),
            "ConsensusJudgeAgent":   _consensus_json(),
        }

        async def _counting_mock(messages, agent_name, **kwargs):
            nonlocal call_count
            call_count += 1
            return _make_llm_result(responses.get(agent_name, _consensus_json()))

        with patch("agents.debate_room.call_llm", new=_counting_mock):
            result = await run_debate_session(SAMPLE_QUERY, SESSION_ID, max_turns=4)

        # Turn 1: cost (1) + Turn 2: quality (1) + rebuttal cost (1) + judge (1) = 4
        assert call_count >= 4
        assert isinstance(result, ConsensusResult)

    @pytest.mark.asyncio
    async def test_max_turns_ceiling_enforced(self):
        """max_turns > 10 is silently capped at 10 (no infinite loops)."""
        responses = {
            "CostOptimizationAgent": _cost_json(),
            "QualityAndTimeAgent":   _quality_json(),
            "ConsensusJudgeAgent":   _consensus_json(),
        }
        with patch("agents.debate_room.call_llm", new=self._mock_call_llm(responses)):
            # Should complete without error even if caller passes a very large max_turns
            result = await run_debate_session(SAMPLE_QUERY, SESSION_ID, max_turns=50)

        assert isinstance(result, ConsensusResult)
        assert result.debate_turns <= 10 + 1  # ceiling + judge


# ────────────────────────────────────────────────────────────────────────────
# 9. Telemetry: debate spans are emitted
# ────────────────────────────────────────────────────────────────────────────

class TestDebateTelemetry:

    @pytest.mark.asyncio
    async def test_debate_spans_in_buffer(self):
        """After a full session, the telemetry buffer must contain debate spans."""
        from telemetry import get_trace_spans

        responses = {
            "CostOptimizationAgent": _cost_json(),
            "QualityAndTimeAgent":   _quality_json(),
            "ConsensusJudgeAgent":   _consensus_json(),
        }

        async def _mock(messages, agent_name, **kwargs):
            return _make_llm_result(responses.get(agent_name, _consensus_json()))

        with patch("agents.debate_room.call_llm", new=_mock):
            await run_debate_session(
                query=SAMPLE_QUERY, session_id="telemetry-test-session", max_turns=3
            )

        # Allow fire-and-forget create_task spans to complete in the event loop.
        await asyncio.sleep(0.05)

        spans = get_trace_spans("telemetry-test-session")
        debate_spans = [s for s in spans if s.span_type == "debate"]
        assert len(debate_spans) >= 3, (
            f"Expected ≥3 debate spans (cost+quality+judge), got {len(debate_spans)}"
        )

    @pytest.mark.asyncio
    async def test_debate_event_metadata_present(self):
        """Each debate span must carry a 'debate_event' description."""
        from telemetry import get_trace_spans

        trace_id  = "telemetry-event-test"
        responses = {
            "CostOptimizationAgent": _cost_json(),
            "QualityAndTimeAgent":   _quality_json(),
            "ConsensusJudgeAgent":   _consensus_json(),
        }

        async def _mock(messages, agent_name, **kwargs):
            return _make_llm_result(responses.get(agent_name, _consensus_json()))

        with patch("agents.debate_room.call_llm", new=_mock):
            await run_debate_session(query=SAMPLE_QUERY, session_id=trace_id, max_turns=3)

        # Allow fire-and-forget create_task spans to complete in the event loop.
        await asyncio.sleep(0.05)

        spans = get_trace_spans(trace_id)
        for s in spans:
            if s.span_type == "debate":
                assert "debate_event" in s.metadata, (
                    f"Span {s.span_name} missing 'debate_event' metadata"
                )


# ────────────────────────────────────────────────────────────────────────────
# 10. Supervisor keyword routing
# ────────────────────────────────────────────────────────────────────────────

class TestSupervisorDebateRouting:
    """Verify that the supervisor stub correctly routes debate-triggering queries."""

    @pytest.mark.asyncio
    async def test_assembly_line_routes_to_debate(self):
        from agent_router_poc import _llm_route, IntentLabel
        decision = await _llm_route("Optimize the assembly line for Model-X", trace_id="rt-test")
        assert decision.intent == IntentLabel.DEBATE

    @pytest.mark.asyncio
    async def test_line_balancing_routes_to_debate(self):
        from agent_router_poc import _llm_route, IntentLabel
        decision = await _llm_route("Run a line balancing analysis for the FATP station", trace_id="rt-test-2")
        assert decision.intent == IntentLabel.DEBATE

    @pytest.mark.asyncio
    async def test_supply_chain_routes_to_debate(self):
        from agent_router_poc import _llm_route, IntentLabel
        decision = await _llm_route("Optimize supply chain allocation for Q3", trace_id="rt-test-3")
        assert decision.intent == IntentLabel.DEBATE

    @pytest.mark.asyncio
    async def test_minimost_still_routes_to_labor_agent(self):
        from agent_router_poc import _llm_route, IntentLabel, AgentName
        decision = await _llm_route("Calculate TMU for sequence A1B0G1A1B0P3A0", trace_id="rt-test-4")
        assert decision.intent == IntentLabel.LABOR_TIME_ANALYSIS
        assert AgentName.LABOR_TIME in decision.target_agents
