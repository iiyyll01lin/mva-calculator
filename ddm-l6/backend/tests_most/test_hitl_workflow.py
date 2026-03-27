"""
backend/tests_most/test_hitl_workflow.py
────────────────────────────────────────────────────────────────────────────────
Human-in-the-Loop (HITL) Approval Workflow — Integration Tests

Validates the full governed-agent lifecycle for SENSITIVE tools:

  Scenario A — Happy path:
    1. User query triggers a SENSITIVE tool (run_simulation).
    2. System returns ``status="PENDING_APPROVAL"`` with an action_id.
    3. Authorised user calls the /approve logic (resume_after_approval).
    4. System executes the tool, returns the final result, updates session.

  Scenario B — Duplicate approval is rejected:
    Calling approve twice on the same action_id returns a ValueError.

  Scenario C — action_id mismatch is rejected:
    A tampered/incorrect action_id cannot be used to approve an action.

  Scenario D — ToolGuard pre-flight blocks unsafe arguments:
    A pending action with out-of-range TMU is rejected by ToolGuard
    before the tool handler is ever called.

  Scenario E — READ_ONLY tools bypass HITL:
    calc_minimost_tmu and lookup_bom_item execute immediately without
    producing a PENDING_APPROVAL response.

  Scenario F — Session state is correct after full HITL round-trip:
    After approval, the session's turn_count advances, pending_action
    is marked APPROVED, and the result message appears in message_history.

Run with:
    cd ddm-l6/backend
    pytest tests_most/test_hitl_workflow.py -v
"""

from __future__ import annotations

import os
import pytest
import pytest_asyncio  # noqa: F401

from agent_router_poc import (
    AuthContext,
    run_agent_workflow,
    resume_after_approval,
)
from memory_store import PendingActionStatus, get_memory_store
import memory_store as ms


# ────────────────────────────────────────────────────────────────────────────
# Fixtures
# ────────────────────────────────────────────────────────────────────────────

STUB_AUTH = AuthContext(
    user_id   = "test-eng-001",
    role      = "Engineer",
    jwt_token = "stub-bearer-token",
)

STUB_APPROVER_ID = "test-mgr-001"


@pytest.fixture(autouse=True)
def isolated_file_store(tmp_path, monkeypatch):
    """
    Redirect the FileMemoryStore to a fresh temp directory for each test and
    reset the module-level singletons so tests cannot pollute each other.
    """
    monkeypatch.setenv("MVA_MEMORY_FILE_DIR", str(tmp_path))
    monkeypatch.setenv("MVA_MEMORY_BACKEND",  "file")
    ms._store_instance = None
    yield
    ms._store_instance = None


# ────────────────────────────────────────────────────────────────────────────
# Scenario A: Full HITL happy path
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hitl_full_happy_path():
    """
    End-to-end HITL flow:
      Turn 1 → PENDING_APPROVAL
      Approve → APPROVED with simulation result
    """
    # ── Turn 1: query triggers run_simulation ────────────────────────────────
    result1 = await run_agent_workflow(
        user_query = "Run a simulation for model MODEL-001 with 2 operators "
                     "and a machine rate of $45/hr.",
        auth       = STUB_AUTH,
    )

    assert result1["status"] == "PENDING_APPROVAL", (
        f"Expected PENDING_APPROVAL, got: {result1}"
    )
    session_id = result1["session_id"]
    action_id  = result1["action_id"]
    assert action_id, "action_id must be non-empty"
    assert result1["tool_name"] == "run_simulation"
    assert session_id in result1["answer"], "Answer should reference the session_id"

    # ── Verify PendingAction is persisted in the session ─────────────────────
    store   = get_memory_store()
    session = await store.load(session_id)
    assert session is not None, "Session must be persisted after HITL interrupt"
    assert session.pending_action is not None, "pending_action must be stored"
    assert session.pending_action.action_id == action_id
    assert session.pending_action.status    == PendingActionStatus.PENDING
    assert session.pending_action.tool_name == "run_simulation"

    # ── Approve: /approve endpoint logic ─────────────────────────────────────
    result2 = await resume_after_approval(
        session_id  = session_id,
        action_id   = action_id,
        approver_id = STUB_APPROVER_ID,
    )

    assert result2["status"]    == "APPROVED"
    assert result2["session_id"] == session_id
    assert result2["action_id"]  == action_id
    assert result2["tool_name"]  == "run_simulation"
    assert "model_id"           in result2["result"]
    assert result2["result"]["throughput_uph"] > 0
    assert result2["result"]["cost_per_unit_usd"] >= 0
    assert STUB_APPROVER_ID in result2["summary"]

    # ── Verify session is updated after approval ──────────────────────────────
    session_after = await store.load(session_id)
    assert session_after is not None
    assert session_after.pending_action.status     == PendingActionStatus.APPROVED
    assert session_after.pending_action.resolved_by == STUB_APPROVER_ID
    assert session_after.pending_action.resolved_at is not None

    # result message must appear in message_history
    history_contents = [m.content for m in session_after.message_history]
    assert any("APPROVED" in c for c in history_contents), (
        "Approved result message not found in message_history"
    )


# ────────────────────────────────────────────────────────────────────────────
# Scenario B: Duplicate approval is rejected
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_duplicate_approval_rejected():
    """Approving the same action_id twice raises a ValueError."""
    result1 = await run_agent_workflow(
        user_query = "Simulate throughput for model M-002 with 3 operators "
                     "and a machine rate of $60/hr.",
        auth       = STUB_AUTH,
    )
    assert result1["status"] == "PENDING_APPROVAL"

    session_id = result1["session_id"]
    action_id  = result1["action_id"]

    # First approval succeeds
    await resume_after_approval(
        session_id  = session_id,
        action_id   = action_id,
        approver_id = STUB_APPROVER_ID,
    )

    # Second approval must be rejected
    with pytest.raises(ValueError, match="already been resolved"):
        await resume_after_approval(
            session_id  = session_id,
            action_id   = action_id,
            approver_id = STUB_APPROVER_ID,
        )


# ────────────────────────────────────────────────────────────────────────────
# Scenario C: action_id mismatch is rejected
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_action_id_mismatch_rejected():
    """A tampered action_id cannot be used to trigger approval."""
    result1 = await run_agent_workflow(
        user_query = "Simulate cost for model M-TEST with 1 operator "
                     "and a burdened machine rate.",
        auth       = STUB_AUTH,
    )
    assert result1["status"] == "PENDING_APPROVAL"

    session_id = result1["session_id"]

    with pytest.raises(ValueError, match="action_id mismatch"):
        await resume_after_approval(
            session_id  = session_id,
            action_id   = "00000000-0000-0000-0000-000000000000",
            approver_id = STUB_APPROVER_ID,
        )


# ────────────────────────────────────────────────────────────────────────────
# Scenario D: ToolGuard pre-flight blocks unsafe arguments
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_toolguard_blocks_unsafe_args():
    """
    A PendingAction with out-of-range cycle_time_tmu is rejected by ToolGuard
    during resume_after_approval, before the actual tool handler is called.
    """
    from memory_store import PendingAction, PendingActionStatus, SessionState

    store = get_memory_store()

    # Manually craft a SessionState with a PendingAction whose args are unsafe
    unsafe_action = PendingAction(
        tool_name  = "run_simulation",
        raw_args   = {
            "model_id":         "MODEL-UNSAFE",
            "cycle_time_tmu":   99_999.0,   # violates 0 < x < 10 000 guard
            "num_operators":    2,
            "machine_rate_usd": 45.0,
        },
        agent_name = "SimulationAgent",
    )
    session = SessionState(
        user_id        = STUB_AUTH.user_id,
        user_role      = STUB_AUTH.role,
        pending_action = unsafe_action,
    )
    await store.save(session)

    with pytest.raises(ValueError, match="Pre-flight validation rejected"):
        await resume_after_approval(
            session_id  = session.session_id,
            action_id   = unsafe_action.action_id,
            approver_id = STUB_APPROVER_ID,
        )

    # The PendingAction should be marked REJECTED in the store
    session_after = await store.load(session.session_id)
    assert session_after is not None
    assert session_after.pending_action.status == PendingActionStatus.REJECTED


# ────────────────────────────────────────────────────────────────────────────
# Scenario E: READ_ONLY tools bypass HITL
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_only_tools_bypass_hitl():
    """
    calc_minimost_tmu and lookup_bom_item are READ_ONLY tools and must
    execute immediately — their responses must NOT carry PENDING_APPROVAL.
    """
    labor_result = await run_agent_workflow(
        user_query = "Calculate the MiniMOST TMU for sequence A1B0G1A1B0P3A0.",
        auth       = STUB_AUTH,
    )
    assert labor_result.get("status") != "PENDING_APPROVAL", (
        "calc_minimost_tmu is READ_ONLY and must not trigger HITL"
    )
    assert len(labor_result["tool_results"]) > 0
    assert labor_result["tool_results"][0]["tool_name"] == "calc_minimost_tmu"
    assert labor_result["tool_results"][0]["success"] is True

    bom_result = await run_agent_workflow(
        user_query = "Look up BOM part SQT-K860G6-BASY revision 1.3.",
        auth       = STUB_AUTH,
    )
    assert bom_result.get("status") != "PENDING_APPROVAL", (
        "lookup_bom_item is READ_ONLY and must not trigger HITL"
    )
    assert any(
        r["tool_name"] == "lookup_bom_item"
        for r in bom_result["tool_results"]
    )


# ────────────────────────────────────────────────────────────────────────────
# Scenario F: Session turn_count advances correctly through full round-trip
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_session_turn_count_advances():
    """
    After a full HITL round-trip the session turn_count must reflect both
    the interrupted turn (PENDING) and the approval turn (+1 each = 2 total
    after approval, starting from 0).
    """
    result1 = await run_agent_workflow(
        user_query = "Simulate throughput for model M-TURN with 1 operator "
                     "and a machine rate of $30/hr.",
        auth       = STUB_AUTH,
    )
    assert result1["status"]     == "PENDING_APPROVAL"
    assert result1["turn_count"] == 1      # interrupted turn counts as turn 1

    result2 = await resume_after_approval(
        session_id  = result1["session_id"],
        action_id   = result1["action_id"],
        approver_id = STUB_APPROVER_ID,
    )
    assert result2["turn_count"] == 2      # approval advances to turn 2


# ────────────────────────────────────────────────────────────────────────────
# Scenario G: Missing session returns clear error
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_approve_nonexistent_session():
    """Attempting to approve a non-existent session raises a clear ValueError."""
    with pytest.raises(ValueError, match="not found"):
        await resume_after_approval(
            session_id  = "no-such-session-id",
            action_id   = "00000000-0000-0000-0000-000000000000",
            approver_id = STUB_APPROVER_ID,
        )
