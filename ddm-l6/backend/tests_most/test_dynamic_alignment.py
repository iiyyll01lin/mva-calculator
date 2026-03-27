"""
backend/tests_most/test_dynamic_alignment.py
────────────────────────────────────────────────────────────────────────────────
Step-5 Alignment Effectiveness Tests — Enterprise MVA Platform v2.0.0

Verifies the full Self-Alignment loop end-to-end:

  1. test_baseline_prompt_has_no_directives
       Fresh AlignmentStore → build_system_prompt returns the base prompt
       unchanged; no "CRITICAL OPERATIONAL RULES" section present.

  2. test_directive_appended_to_target_agent
       Add a CorrectionDirective for "CostOptimizationAgent".
       Assert that build_system_prompt("CostOptimizationAgent", base) now
       embeds the directive text in a CRITICAL OPERATIONAL RULES block.

  3. test_global_directive_injected_into_all_agents
       Add a GLOBAL directive.  Assert all three debate-room agents'
       prompts include it after injection.

  4. test_cache_invalidated_on_new_directive
       Assert that _CACHE_VERSION bumps when a directive is added and that
       a previously cached prompt is NOT returned for the new version.

  5. test_deactivated_directive_excluded
       Deactivate an existing directive; assert it disappears from future
       build_system_prompt results.

  6. test_non_matching_agent_not_affected
       A directive targeting "QualityAndTimeAgent" must NOT appear in
       "CostOptimizationAgent"'s prompt.

  7. test_cost_agent_prompt_changes_output (simulation mock)
       Simulate the full loop: mock call_llm, inject a directive, assert
       the prompt forwarded to the LLM now contains the directive — proving
       that the agent swarm would "change its output" based on the rule.

Run with:
    cd /data/yy/mva/ddm-l6/backend
    pytest tests_most/test_dynamic_alignment.py -v
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

# ── PYTHONPATH bootstrap ─────────────────────────────────────────────────────
_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ── Imports under test ───────────────────────────────────────────────────────
from memory.alignment_store import (
    AlignmentStore,
    CorrectionDirective,
    _cached_build,
    build_system_prompt,
    get_alignment_store,
    get_cache_version,
    _bump_cache_version,
)


# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def _isolated_store(tmp_path):
    """
    Each test gets a completely isolated AlignmentStore backed by a
    temporary directory.  The singleton and LRU cache are reset so tests
    are fully independent.
    """
    import memory.alignment_store as _mod

    # Point the store file to a temp location
    store_path = str(tmp_path / "directives.json")
    _mod.ALIGNMENT_STORE_FILE = store_path

    # Reset the singleton, cache version, and LRU cache
    AlignmentStore._instance   = None
    AlignmentStore._init_lock  = None
    AlignmentStore._write_lock = None
    _mod._CACHE_VERSION = 0
    _cached_build.cache_clear()

    yield

    # Post-test cleanup
    AlignmentStore._instance   = None
    AlignmentStore._init_lock  = None
    AlignmentStore._write_lock = None
    _mod._CACHE_VERSION = 0
    _cached_build.cache_clear()


BASE_COST    = "You are CostOptimizationAgent — minimize cost."
BASE_QUALITY = "You are QualityAndTimeAgent — maximize throughput."
BASE_JUDGE   = "You are ConsensusJudgeAgent — synthesize optimally."

COST_AGENT    = "CostOptimizationAgent"
QUALITY_AGENT = "QualityAndTimeAgent"
JUDGE_AGENT   = "ConsensusJudgeAgent"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Baseline: no directives → prompt is unchanged
# ═══════════════════════════════════════════════════════════════════════════════

def test_baseline_prompt_has_no_directives():
    """
    A freshly constructed AlignmentStore with no directives must return the
    base prompt verbatim from build_system_prompt().
    """
    result = build_system_prompt(COST_AGENT, BASE_COST)
    assert result == BASE_COST, (
        "build_system_prompt must return base_prompt unchanged when no directives exist."
    )
    assert "CRITICAL OPERATIONAL RULES" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Directive appended to targeted agent
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_directive_appended_to_target_agent():
    """
    After adding a CorrectionDirective for CostOptimizationAgent, the
    next call to build_system_prompt() must embed the directive text.
    """
    store = get_alignment_store()
    # Store needs not be initialized from disk for the in-memory path
    store._initialized = True

    directive = CorrectionDirective(
        agent_target   = COST_AGENT,
        directive_text = "Never reduce headcount below 3 operators on Line 7.",
        author_id      = "manager_yy",
    )
    await store.add_directive(directive)

    result = build_system_prompt(COST_AGENT, BASE_COST)

    assert "CRITICAL OPERATIONAL RULES" in result, \
        "Rules section header must be present after directive injection."
    assert directive.directive_text in result, \
        "Directive text must be verbatim in the generated prompt."
    assert BASE_COST in result, \
        "Base prompt must still be present."

    # Safety: QualityAgent should NOT see this directive
    quality_result = build_system_prompt(QUALITY_AGENT, BASE_QUALITY)
    assert directive.directive_text not in quality_result, \
        "Agent-specific directive must not bleed into other agents."


# ═══════════════════════════════════════════════════════════════════════════════
# 3. GLOBAL directive injected into all debate agents
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_global_directive_injected_into_all_agents():
    """
    A GLOBAL directive must appear in every agent's system prompt.
    """
    store = get_alignment_store()
    store._initialized = True

    global_rule = CorrectionDirective(
        agent_target   = "GLOBAL",
        directive_text = "All recommendations MUST comply with ISO 9001 quality standards.",
        author_id      = "quality_lead",
    )
    await store.add_directive(global_rule)

    for agent_name, base in [
        (COST_AGENT,    BASE_COST),
        (QUALITY_AGENT, BASE_QUALITY),
        (JUDGE_AGENT,   BASE_JUDGE),
    ]:
        result = build_system_prompt(agent_name, base)
        assert global_rule.directive_text in result, \
            f"GLOBAL directive must be injected into {agent_name}'s prompt."


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Cache invalidation on new directive
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cache_invalidated_on_new_directive():
    """
    _CACHE_VERSION must increment after adding a directive, so the LRU
    cached result from the previous version is no longer served.
    """
    store = get_alignment_store()
    store._initialized = True

    # Prime the cache at version 0
    v0 = get_cache_version()
    result_before = build_system_prompt(COST_AGENT, BASE_COST)
    assert "CRITICAL OPERATIONAL RULES" not in result_before

    directive = CorrectionDirective(
        agent_target   = COST_AGENT,
        directive_text = "Prioritise energy saving on night shifts.",
        author_id      = "ops_manager",
    )
    await store.add_directive(directive)

    v1 = get_cache_version()
    assert v1 == v0 + 1, "Cache version must increment by exactly 1 after add_directive()."

    result_after = build_system_prompt(COST_AGENT, BASE_COST)
    assert directive.directive_text in result_after, \
        "Prompt must reflect the new directive after cache invalidation."


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Deactivated directive excluded from prompt
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_deactivated_directive_excluded():
    """
    A directive deactivated via AlignmentStore.deactivate() must no longer
    appear in future build_system_prompt() results.
    """
    store = get_alignment_store()
    store._initialized = True

    directive = CorrectionDirective(
        agent_target   = COST_AGENT,
        directive_text = "Avoid overtime for workers on weekends.",
        author_id      = "hr_manager",
    )
    await store.add_directive(directive)

    # Confirm it's present
    result_active = build_system_prompt(COST_AGENT, BASE_COST)
    assert directive.directive_text in result_active

    # Deactivate
    await store.deactivate(directive.id)

    result_deactivated = build_system_prompt(COST_AGENT, BASE_COST)
    assert directive.directive_text not in result_deactivated, \
        "Deactivated directive must be excluded from prompt."


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Directive for Agent B does NOT appear in Agent A's prompt
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_non_matching_agent_not_affected():
    """
    A directive targeting QualityAndTimeAgent must NOT appear in
    CostOptimizationAgent's prompt.
    """
    store = get_alignment_store()
    store._initialized = True

    quality_rule = CorrectionDirective(
        agent_target   = QUALITY_AGENT,
        directive_text = "Always flag if defect rate exceeds 3%.",
        author_id      = "qc_lead",
    )
    await store.add_directive(quality_rule)

    cost_prompt = build_system_prompt(COST_AGENT, BASE_COST)
    assert quality_rule.directive_text not in cost_prompt, \
        "Agent-specific directive must not appear in a different agent's prompt."

    quality_prompt = build_system_prompt(QUALITY_AGENT, BASE_QUALITY)
    assert quality_rule.directive_text in quality_prompt


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Full loop simulation — prompt forwarded to LLM contains directive
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_cost_agent_prompt_changes_output():
    """
    Full Self-Alignment loop simulation:

      Step A — Baseline: mock call_llm captures the prompt; no directives exist.
      Step B — Inject a CorrectionDirective forbidding a specific action.
      Step C — Re-run and assert the prompt sent to the LLM now includes the
               directive, proving the agent swarm "changes its output" by
               operating under the new rule.

    This test mocks the LLM to avoid real API calls while still exercising
    the complete prompt-assembly pipeline.
    """
    from agents.debate_room import run_cost_agent, _COST_BASE_PROMPT, DEBATE_AGENT_COST

    captured_prompts: list[str] = []

    class _FakeLlmResult:
        content           = '{"plan_id":"COST-PLAN-A","summary":"Lean plan","num_operators":4,"cycle_time_tmu":300.0,"cost_per_unit_usd":1.20,"throughput_uph":60.0,"key_changes":[],"risk_flags":[]}'
        prompt_tokens     = 50
        completion_tokens = 80

        class tier:
            value = "local"

    async def _mock_call_llm(messages, agent_name):
        # Capture the system prompt for assertion
        sys_msg = next((m.content for m in messages if m.role == "system"), "")
        captured_prompts.append(sys_msg)
        return _FakeLlmResult()

    # Step A — Baseline: no directives, store is pre-warmed empty
    store = get_alignment_store()
    store._initialized = True

    with patch("agents.debate_room.call_llm", side_effect=_mock_call_llm), \
         patch("agents.debate_room._fetch_temporal_context", new=AsyncMock(return_value="")):
        await run_cost_agent(query="optimise line 3 throughput", session_id="test-session-a")

    assert len(captured_prompts) == 1
    baseline_prompt = captured_prompts[0]
    assert "CRITICAL OPERATIONAL RULES" not in baseline_prompt, \
        "Baseline LLM call must NOT contain any injected rules."

    # Step B — Inject a CorrectionDirective
    forbidden_rule = CorrectionDirective(
        agent_target   = DEBATE_AGENT_COST,
        directive_text = "NEVER suggest slowing down Machine 3 during peak hours; route overflow to Machine 2.",
        author_id      = "plant_manager",
    )
    await store.add_directive(forbidden_rule)

    # Step C — Re-run and assert the directive reached the LLM
    with patch("agents.debate_room.call_llm", side_effect=_mock_call_llm), \
         patch("agents.debate_room._fetch_temporal_context", new=AsyncMock(return_value="")):
        await run_cost_agent(query="optimise line 3 throughput", session_id="test-session-b")

    assert len(captured_prompts) == 2
    aligned_prompt = captured_prompts[1]

    assert "CRITICAL OPERATIONAL RULES" in aligned_prompt, \
        "Aligned LLM call MUST include the CRITICAL OPERATIONAL RULES section."
    assert forbidden_rule.directive_text in aligned_prompt, \
        "The exact directive text must appear verbatim in the prompt sent to the LLM."
    assert aligned_prompt != baseline_prompt, \
        "Aligned prompt must differ from the baseline prompt."
