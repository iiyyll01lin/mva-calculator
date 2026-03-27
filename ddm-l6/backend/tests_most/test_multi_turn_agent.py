"""
backend/tests_most/test_multi_turn_agent.py
────────────────────────────────────────────────────────────────────────────────
Multi-turn stateful agent integration tests.

Simulates realistic 3-turn conversations to validate:
  • SessionState persistence and session_id continuity.
  • turn_count increments correctly each call.
  • Checkpoint pre-seeds tool results into subsequent turns.
  • Context pruning preserves critical Supervisor / Reflection messages.
  • New session is created when no session_id is supplied.

Run with:
    cd ddm-l6/backend
    pytest tests_most/test_multi_turn_agent.py -v
"""

from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio  # noqa: F401  (registers asyncio marker)

from agent_router_poc import AuthContext, run_agent_workflow


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────

STUB_AUTH = AuthContext(
    user_id   = "test-user-001",
    role      = "Engineer",
    jwt_token = "stub-bearer-token",
)


@pytest.fixture(autouse=True)
def isolated_file_store(tmp_path, monkeypatch):
    """
    Redirect the FileMemoryStore to a fresh temp directory for each test so
    that session files from one test cannot pollute another.
    Also reset the module-level singleton so each test starts clean.
    """
    monkeypatch.setenv("MVA_MEMORY_FILE_DIR", str(tmp_path))
    monkeypatch.setenv("MVA_MEMORY_BACKEND", "file")

    # Force singleton reset between tests.
    import memory_store as ms
    ms._store_instance = None
    yield
    ms._store_instance = None


# ────────────────────────────────────────────────────────────────────────────
# Test 1: 3-turn conversation — end-to-end happy path
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_3_turn_conversation_state_persistence():
    """
    Turn 1: Ask for BOM  → BomAgent runs lookup_bom_item.
    Turn 2: Ask for Labor Time based on that BOM → LaborTimeAgent runs.
    Turn 3: Ask to summarize both → Supervisor synthesizes full history.

    Assertions:
    • session_id is identical across all three turns.
    • turn_count increments 1 → 2 → 3.
    • Each turn returns at least one successful tool result.
    • Turn 3 answer is non-empty (Supervisor synthesized a response).
    """
    # ── Turn 1: BOM lookup ────────────────────────────────────────────────────
    result1 = await run_agent_workflow(
        user_query = "Look up the BOM for part SQT-K860G6-BASY revision 1.3",
        auth       = STUB_AUTH,
    )
    assert "session_id" in result1, "session_id missing from turn-1 response"
    session_id = result1["session_id"]
    assert result1["turn_count"] == 1, f"Expected turn_count=1, got {result1['turn_count']}"
    assert len(result1["tool_results"]) > 0, "Turn 1 produced no tool results"
    bom_result = next(
        (r for r in result1["tool_results"] if r["tool_name"] == "lookup_bom_item"),
        None,
    )
    assert bom_result is not None, "lookup_bom_item not called in turn 1"
    assert bom_result["success"] is True, "lookup_bom_item failed in turn 1"

    # ── Turn 2: Labor time for the BOM component ──────────────────────────────
    result2 = await run_agent_workflow(
        user_query = (
            "Now calculate the MiniMOST labor time for assembling that BOM component. "
            "Use sequence A1B0G1A1B0P3A0 with a Proficient operator."
        ),
        auth       = STUB_AUTH,
        session_id = session_id,
    )
    assert result2["session_id"] == session_id, "session_id changed between turns"
    assert result2["turn_count"] == 2, f"Expected turn_count=2, got {result2['turn_count']}"
    assert len(result2["tool_results"]) > 0, "Turn 2 produced no tool results"
    labor_result = next(
        (r for r in result2["tool_results"] if r["tool_name"] == "calc_minimost_tmu"),
        None,
    )
    assert labor_result is not None, "calc_minimost_tmu not called in turn 2"
    assert labor_result["success"] is True, "calc_minimost_tmu failed in turn 2"

    # ── Turn 3: Summarize both ────────────────────────────────────────────────
    result3 = await run_agent_workflow(
        user_query = (
            "Please give me a complete breakdown and summary of the full analysis: "
            "the BOM data retrieved and the labor time calculated."
        ),
        auth       = STUB_AUTH,
        session_id = session_id,
    )
    assert result3["session_id"] == session_id, "session_id changed on turn 3"
    assert result3["turn_count"] == 3, f"Expected turn_count=3, got {result3['turn_count']}"
    assert isinstance(result3["answer"], str) and len(result3["answer"]) > 0, \
        "Turn 3 produced an empty answer"

    # Checkpoint should carry cumulative successful results from all turns.
    assert len(result3["tool_results"]) >= 2, \
        "Turn 3 checkpoint should contain at least 2 cumulative tool results"


# ────────────────────────────────────────────────────────────────────────────
# Test 2: Fresh session per call (no session_id)
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_new_session_per_call_without_session_id():
    """
    Each call without a session_id must produce a unique session_id and
    start at turn_count == 1.
    """
    result_a = await run_agent_workflow(
        user_query = "bom lookup for part ALPHA",
        auth       = STUB_AUTH,
    )
    result_b = await run_agent_workflow(
        user_query = "bom lookup for part BETA",
        auth       = STUB_AUTH,
    )
    assert result_a["session_id"] != result_b["session_id"], \
        "Two calls without session_id must generate distinct sessions"
    assert result_a["turn_count"] == 1
    assert result_b["turn_count"] == 1


# ────────────────────────────────────────────────────────────────────────────
# Test 3: Checkpoint pre-seeds tool results into subsequent turns
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_checkpoint_preseeds_tool_results():
    """
    After turn 1 writes a checkpoint, turn 2 should receive the cumulative
    tool results (turn 1 + turn 2) in its response.
    """
    result1 = await run_agent_workflow(
        user_query = "bom lookup for part SQT",
        auth       = STUB_AUTH,
    )
    session_id       = result1["session_id"]
    turn1_tool_count = len(result1["tool_results"])
    assert turn1_tool_count > 0

    result2 = await run_agent_workflow(
        user_query = "labor time analysis for the component using sequence A1B0G1",
        auth       = STUB_AUTH,
        session_id = session_id,
    )
    assert result2["turn_count"] == 2
    # Cumulative: turn 2 tool_results includes both turns' successful calls.
    assert len(result2["tool_results"]) >= turn1_tool_count, \
        "Turn 2 should carry at least as many tool results as turn 1"


# ────────────────────────────────────────────────────────────────────────────
# Test 4: Unknown session_id starts a fresh session gracefully
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_unknown_session_id_starts_fresh_session():
    """
    Passing a non-existent session_id must not raise; a new session is created.
    """
    result = await run_agent_workflow(
        user_query = "bom lookup",
        auth       = STUB_AUTH,
        session_id = "00000000-0000-0000-0000-000000000000",
    )
    assert result["turn_count"] == 1
    # A new session_id is assigned (different from the fake one we passed in).
    assert result["session_id"] != "00000000-0000-0000-0000-000000000000"


# ────────────────────────────────────────────────────────────────────────────
# Test 5: Context pruning preserves critical Supervisor / Reflection messages
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_context_pruning_preserves_supervisor_and_reflection(monkeypatch):
    """
    With a very low PRUNE_TOKEN_LIMIT, prune_and_summarize must:
    • Evict some messages to bring the window under budget.
    • Never evict Supervisor synthesis messages.
    • Populate system_summary with a non-empty digest.
    """
    from agent_router_poc import AgentName
    import memory_store as ms

    # Lower the token limit to force pruning after a couple of messages.
    monkeypatch.setenv("MVA_PRUNE_TOKEN_LIMIT", "50")
    # Reload the constant in memory_store so the patched value is used.
    ms.PRUNE_TOKEN_LIMIT = 50
    ms._store_instance   = None   # reset singleton

    session = ms.SessionState()
    session.message_history = [
        ms.StoredMessage(
            role    = "user",
            content = "first user turn with a long message " * 8,  # ~64 tokens
        ),
        ms.StoredMessage(
            role    = "assistant",
            content = "Supervisor synthesis result for turn 1",
            name    = AgentName.SUPERVISOR.value,
        ),
        ms.StoredMessage(
            role    = "user",
            content = "second user turn",
        ),
        ms.StoredMessage(
            role    = "assistant",
            content = "LaborTime agent result",
            name    = AgentName.LABOR_TIME.value,
        ),
        ms.StoredMessage(
            role    = "user",
            content = "third user turn (most recent)",
        ),
    ]
    session.turn_count = 3

    pruned = ms.prune_and_summarize(session)

    # Supervisor messages must survive pruning.
    supervisor_msgs = [
        m for m in pruned.message_history
        if m.role == "assistant" and m.name == AgentName.SUPERVISOR.value
    ]
    assert len(supervisor_msgs) >= 1, \
        "Supervisor synthesis message must never be evicted during pruning"

    # System summary must be populated with the evicted content.
    assert pruned.system_summary is not None, \
        "system_summary should be non-None after pruning"
    assert len(pruned.system_summary) > 0

    # Restore
    ms.PRUNE_TOKEN_LIMIT = 4000
    ms._store_instance   = None
