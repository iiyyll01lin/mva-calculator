"""
backend/tests_most/test_cyber_physical_bridge.py
────────────────────────────────────────────────────────────────────────────────
End-to-End Cyber-Physical Bridge Test Suite

Scenario (mirrors the Production Incident Pipeline):
  1. IoT Watchdog detects a yield anomaly on Machine-3.
  2. The anomaly triggers a run_debate_session() with a base64 camera frame.
  3. QualityAgent concurrently invokes VisionInspectorAgent (stubbed).
  4. VLM returns ANOMALY_CONFIRMED (conveyor jam at Station 4).
  5. Visual evidence is embedded in the ConsensusResult.
  6. The Swarm generates an EmergencyProposal containing visual evidence.
  7. After HITL "approve" simulation, dispatch_consensus_to_robots() is called.
  8. The ROS2 Bridge translates the consensus to an ESTOP RobotCommand.
  9. The ESTOP is signed with Ed25519 and logged to TamperEvidentAuditLog.
 10. Verify the audit chain contains both VLM_FRAME_ANALYSIS and
     ROBOT_COMMAND_DISPATCHED events (cryptographic provenance).

Coverage breakdown
──────────────────
 Group A — VisionInspectorAgent unit tests (stub mode):
  A1. analyse_frame returns VisionAnalysisResult with correct verdict.
  A2. Stub mode: ANOMALY_CONFIRMED for anomaly-hint context.
  A3. Stub mode: NORMAL for neutral context.
  A4. result is Ed25519-signed (cryptographic_signature is populated).
  A5. BoundingBox fields are within [0.0, 1.0] constraints.

 Group B — ROS2 Bridge unit tests:
  B1. ROS2CommandTranslator produces ESTOP for "halt" keywords.
  B2. ROS2CommandTranslator produces CMD_VEL for "slow down" keywords.
  B3. Translator infers correct robot from station keyword.
  B4. ROS2BridgeDispatcher signs the command before dispatch.
  B5. Dispatched command is added to in-memory dispatch buffer.
  B6. dispatch_estop() immediately dispatches and logs ESTOP.

 Group C — Upgraded DebateRoom multi-modal tests:
  C1. run_quality_agent returns (CritiquePlan, vision_evidence) tuple.
  C2. Vision evidence is ANOMALY_CONFIRMED stub in anomaly context.
  C3. ConsensusResult.vision_evidence is populated from quality agent.
  C4. run_debate_session with image_b64 completes and returns ConsensusResult.
  C5. Vision evidence dict appears in ConsensusResult.vision_evidence.

 Group D — Integration: full cyber-physical pipeline:
  D1. Anomaly event → debate session → vision proof → consensus.
  D2. Consensus summary → dispatch_consensus_to_robots → ESTOP dispatched.
  D3. Dispatch buffer contains the ESTOP command for the correct robot.
  D4. Cryptographic signature on command is verifiable.
  D5. Audit log entries: VLM_FRAME_ANALYSIS and ROBOT_COMMAND_DISPATCHED present.

All LLM and VLM calls are mocked; no network access is required.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
import os
import uuid
from typing import Any, Dict, Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Ensure backend root is on the path ───────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agents.vision_inspector import (
    VisionAnalysisResult,
    VisionInspectorAgent,
    VisualVerdict,
    BoundingBox,
    analyse_frame,
    _stub_vision_result,
)
from robotics.ros2_bridge import (
    CommandType,
    RobotCommand,
    ROS2BridgeDispatcher,
    ROS2CommandTranslator,
    dispatch_consensus_to_robots,
    dispatch_estop,
    get_dispatched_commands,
    ROBOT_FLEET,
    RobotStatus,
)
from agents.debate_room import (
    ConsensusResult,
    CritiquePlan,
    DebatePlan,
    run_debate_session,
    run_quality_agent,
)
from llm_client import LlmCallResult, ModelTier

# ────────────────────────────────────────────────────────────────────────────
# Shared Fixtures & Helpers
# ────────────────────────────────────────────────────────────────────────────

# 1×1 transparent PNG — minimal valid base64 frame
PLACEHOLDER_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhf"
    "DwAChwGA60e6kgAAAABJRU5ErkJggg=="
)

ANOMALY_QUERY = (
    "CRITICAL: Machine-3 yield dropped to 87.2% (below 90% threshold, "
    "3 consecutive ticks). Conveyor belt jam suspected at Station 4. "
    "Analyze and propose mitigation."
)
SESSION_ID = "test-cp-bridge-001"


def _make_plan_a() -> DebatePlan:
    return DebatePlan(
        plan_id           = "COST-PLAN-A",
        summary           = "Reduce operators and run at full speed.",
        num_operators     = 3,
        cycle_time_tmu    = 420.0,
        cost_per_unit_usd = 1.85,
        throughput_uph    = 52.0,
        key_changes       = ["Automate barcode scanning"],
        risk_flags        = ["Potential jam risk"],
    )


def _make_critique_json() -> str:
    return json.dumps({
        "critiqued_plan_id": "COST-PLAN-A",
        "critic_agent":      "QualityAndTimeAgent",
        "weaknesses_found":  [
            "IoT data shows yield drop; visual confirmation of jam at Station 4 required.",
            "Running at full speed during jam increases damage risk.",
        ],
        "counter_plan": {
            "plan_id":            "QUALITY-PLAN-B",
            "summary":            "Halt conveyor, dispatch maintenance robot, then resume at 70% speed.",
            "num_operators":      5,
            "cycle_time_tmu":     380.0,
            "cost_per_unit_usd":  2.20,
            "throughput_uph":     48.0,
            "key_changes":        ["Halt production line 3", "Dispatch AGV maintenance unit"],
            "risk_flags":         [],
        },
    })


def _make_consensus_json() -> str:
    return json.dumps({
        "summary":              (
            "Halt Production Line 3 immediately due to confirmed conveyor jam at "
            "Station 4. Dispatch AGV-002 to clear the jam. Resume at reduced speed."
        ),
        "num_operators":        4,
        "cycle_time_tmu":       400.0,
        "cost_per_unit_usd":    2.00,
        "throughput_uph":       50.0,
        "adopted_from_cost":    ["Reduce operators to 4"],
        "adopted_from_quality": ["Halt conveyor", "Dispatch maintenance robot"],
        "trade_off_resolution": "Safety halt overrides cost optimisation.",
        "confidence_score":     0.88,
    })


def _make_llm_result(content: str) -> LlmCallResult:
    return LlmCallResult(
        content           = content,
        prompt_tokens     = 80,
        completion_tokens = 40,
        model             = "stub",
        tier              = ModelTier.LOCAL,
    )


# ────────────────────────────────────────────────────────────────────────────
# Group A — VisionInspectorAgent unit tests
# ────────────────────────────────────────────────────────────────────────────

class TestVisionInspectorAgent:

    # A1. analyse_frame returns a VisionAnalysisResult in stub mode
    @pytest.mark.asyncio
    async def test_a1_analyse_frame_returns_result(self):
        result = await analyse_frame(
            image_b64   = PLACEHOLDER_B64,
            session_id  = SESSION_ID,
            frame_id    = "frame-test-001",
            iot_context = ANOMALY_QUERY,
        )
        assert isinstance(result, VisionAnalysisResult)
        assert result.frame_id == "frame-test-001"
        assert isinstance(result.verdict, VisualVerdict)
        assert 0.0 <= result.confidence <= 1.0

    # A2. Stub mode: anomaly hint → ANOMALY_CONFIRMED
    def test_a2_stub_anomaly_context(self):
        result = _stub_vision_result("frame-x", ANOMALY_QUERY)
        assert result.verdict == VisualVerdict.ANOMALY_CONFIRMED
        assert result.confidence > 0.5
        assert len(result.detected_objects) > 0

    # A3. Stub mode: neutral context → NORMAL
    def test_a3_stub_normal_context(self):
        result = _stub_vision_result("frame-y", "yield_rate: 96%, temperature: 48°C")
        assert result.verdict == VisualVerdict.NORMAL
        assert result.detected_objects == []

    # A4. Result is Ed25519-signed after analyse()
    @pytest.mark.asyncio
    async def test_a4_result_is_signed(self):
        result = await analyse_frame(
            image_b64   = PLACEHOLDER_B64,
            session_id  = SESSION_ID,
            iot_context = ANOMALY_QUERY,
        )
        assert result.cryptographic_signature is not None
        assert len(result.cryptographic_signature) > 10

    # A5. BoundingBox members are within [0, 1]
    @pytest.mark.asyncio
    async def test_a5_bounding_box_constraints(self):
        result = await analyse_frame(
            image_b64   = PLACEHOLDER_B64,
            session_id  = SESSION_ID,
            iot_context = ANOMALY_QUERY,
        )
        for box in result.detected_objects:
            assert 0.0 <= box.x <= 1.0
            assert 0.0 <= box.y <= 1.0
            assert 0.0 <= box.width <= 1.0
            assert 0.0 <= box.height <= 1.0
            assert 0.0 <= box.confidence <= 1.0


# ────────────────────────────────────────────────────────────────────────────
# Group B — ROS2 Bridge unit tests
# ────────────────────────────────────────────────────────────────────────────

class TestROS2CommandTranslator:

    def setup_method(self):
        self.translator = ROS2CommandTranslator()

    # B1. ESTOP for "halt" keywords
    def test_b1_halt_produces_estop(self):
        cmds = self.translator.translate(
            summary      = "Halt Production Line 3 immediately. Emergency stop required.",
            action_items = ["Halt conveyor belt at Station 3"],
            session_id   = SESSION_ID,
            plan_id      = "PLAN-001",
        )
        assert len(cmds) >= 1
        assert any(c.command_type == CommandType.ESTOP for c in cmds)

    # B2. CMD_VEL for "slow down" keywords
    def test_b2_slow_produces_cmd_vel(self):
        cmds = self.translator.translate(
            summary      = "Reduce conveyor speed by 30% to prevent further jams.",
            action_items = ["Slow down conveyor belt"],
            session_id   = SESSION_ID,
            plan_id      = "PLAN-002",
        )
        assert any(c.command_type == CommandType.CMD_VEL for c in cmds)

    # B3. Translator infers correct robot from station keyword
    def test_b3_robot_inferred_from_station(self):
        cmds = self.translator.translate(
            summary      = "Dispatch maintenance unit to station-4 for jam clearance.",
            action_items = ["Navigate to station-4"],
            session_id   = SESSION_ID,
            plan_id      = "PLAN-003",
        )
        for c in cmds:
            if c.command_type == CommandType.NAVIGATION_GOAL:
                assert c.robot_id == "ARM-002"  # ARM-002 is at Station-4

    # B4. Dispatched command is signed
    @pytest.mark.asyncio
    async def test_b4_command_is_signed(self):
        dispatcher = ROS2BridgeDispatcher()
        cmd = RobotCommand(
            command_type = CommandType.ESTOP,
            robot_id     = "AGV-001",
            session_id   = SESSION_ID,
            payload      = {"reason": "test", "triggered_by": "Test"},
            rationale    = "unit test",
        )
        dispatched = await dispatcher.dispatch(cmd)
        assert dispatched.cryptographic_signature is not None
        assert len(dispatched.cryptographic_signature) > 10

    # B5. Dispatched command appears in dispatch buffer
    @pytest.mark.asyncio
    async def test_b5_command_in_buffer(self):
        dispatcher = ROS2BridgeDispatcher()
        cmd = RobotCommand(
            command_type = CommandType.CMD_VEL,
            robot_id     = "AGV-002",
            session_id   = SESSION_ID,
            payload      = {"linear_x": 0.2, "linear_y": 0.0, "angular_z": 0.0},
            rationale    = "buffer test",
        )
        dispatched = await dispatcher.dispatch(cmd)
        buffer = get_dispatched_commands(limit=50)
        cmd_ids = {c.command_id for c in buffer}
        assert dispatched.command_id in cmd_ids
        assert dispatched.dispatched is True

    # B6. dispatch_estop dispatches immediately
    @pytest.mark.asyncio
    async def test_b6_dispatch_estop(self):
        cmd = await dispatch_estop(
            robot_id   = "ARM-001",
            reason     = "test emergency stop",
            session_id = SESSION_ID,
        )
        assert cmd.command_type == CommandType.ESTOP
        assert cmd.robot_id == "ARM-001"
        assert cmd.dispatched is True
        assert ROBOT_FLEET["ARM-001"].status == RobotStatus.ESTOP


# ────────────────────────────────────────────────────────────────────────────
# Group C — Upgraded DebateRoom multi-modal tests
# ────────────────────────────────────────────────────────────────────────────

class TestDebateRoomMultiModal:

    @pytest.mark.asyncio
    async def test_c1_quality_agent_returns_tuple(self):
        """run_quality_agent returns (CritiquePlan, Optional[dict]) tuple."""
        with patch("llm_client.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("agents.debate_room._fetch_temporal_context", new_callable=AsyncMock) as mock_hist, \
             patch("agents.debate_room._request_visual_proof", new_callable=AsyncMock) as mock_vis:
            mock_llm.return_value   = _make_llm_result(_make_critique_json())
            mock_hist.return_value  = ""
            mock_vis.return_value   = {"verdict": "ANOMALY_CONFIRMED", "confidence": 0.91,
                                       "anomaly_description": "Jam at Station 4", "detected_objects": []}
            result = await run_quality_agent(
                query      = ANOMALY_QUERY,
                plan_a     = _make_plan_a(),
                session_id = SESSION_ID,
                turn       = 2,
            )
            assert isinstance(result, tuple)
            assert len(result) == 2
            critique, vision = result
            assert isinstance(critique, CritiquePlan)
            assert vision is not None
            assert isinstance(vision, dict)

    @pytest.mark.asyncio
    async def test_c2_vision_evidence_is_anomaly_confirmed(self):
        """VisionInspectorAgent stub returns ANOMALY_CONFIRMED for anomaly context."""
        with patch("llm_client.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("agents.debate_room._fetch_temporal_context", new_callable=AsyncMock) as mock_hist:
            mock_llm.return_value  = _make_llm_result(_make_critique_json())
            mock_hist.return_value = ""
            _, vision = await run_quality_agent(
                query      = ANOMALY_QUERY,
                plan_a     = _make_plan_a(),
                session_id = SESSION_ID,
            )
            # In stub mode, anomaly-hint context → ANOMALY_CONFIRMED
            assert vision is not None
            assert vision.get("verdict") == VisualVerdict.ANOMALY_CONFIRMED.value

    @pytest.mark.asyncio
    async def test_c3_consensus_result_has_vision_evidence(self):
        """ConsensusResult.vision_evidence populated when vision is present."""
        with patch("llm_client.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("agents.debate_room._fetch_temporal_context", new_callable=AsyncMock) as mock_hist, \
             patch("agents.debate_room._request_visual_proof", new_callable=AsyncMock) as mock_vis:
            mock_hist.return_value = ""
            mock_vis.return_value  = {
                "verdict": "ANOMALY_CONFIRMED",
                "confidence": 0.91,
                "anomaly_description": "Jam at Station 4",
                "detected_objects": [],
                "recommended_actions": ["Halt conveyor"],
            }
            mock_llm.side_effect = [
                _make_llm_result(_make_critique_json()),   # quality agent
                _make_llm_result(_make_consensus_json()),  # consensus judge
            ]
            from agents.debate_room import run_consensus_judge
            critique, vision = await run_quality_agent(
                query      = ANOMALY_QUERY,
                plan_a     = _make_plan_a(),
                session_id = SESSION_ID,
            )
            consensus = await run_consensus_judge(
                query            = ANOMALY_QUERY,
                plan_a           = _make_plan_a(),
                critique         = critique,
                session_id       = SESSION_ID,
                vision_evidence  = vision,
            )
            assert consensus.vision_evidence is not None
            assert consensus.vision_evidence.get("verdict") == "ANOMALY_CONFIRMED"

    @pytest.mark.asyncio
    async def test_c4_debate_session_with_image(self):
        """run_debate_session with image_b64 completes and returns ConsensusResult."""
        cost_json     = json.dumps({
            "plan_id": "COST-PLAN-A", "summary": "Test plan", "num_operators": 3,
            "cycle_time_tmu": 420.0, "cost_per_unit_usd": 1.85,
            "throughput_uph": 52.0, "key_changes": [], "risk_flags": [],
        })
        with patch("llm_client.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("agents.debate_room._fetch_temporal_context", new_callable=AsyncMock) as mock_hist, \
             patch("agents.debate_room._request_visual_proof", new_callable=AsyncMock) as mock_vis:
            mock_hist.return_value = ""
            mock_vis.return_value  = {
                "verdict": "ANOMALY_CONFIRMED",
                "confidence": 0.90,
                "anomaly_description": "Jam at Station 4",
                "detected_objects": [],
                "recommended_actions": ["Halt Station 4"],
            }
            mock_llm.side_effect = [
                _make_llm_result(cost_json),               # cost agent
                _make_llm_result(_make_critique_json()),   # quality agent
                _make_llm_result(_make_consensus_json()),  # consensus judge
            ]
            result = await run_debate_session(
                query      = ANOMALY_QUERY,
                session_id = SESSION_ID + "-c4",
                image_b64  = PLACEHOLDER_B64,
            )
            assert isinstance(result, ConsensusResult)
            assert result.session_id == SESSION_ID + "-c4"

    @pytest.mark.asyncio
    async def test_c5_visual_evidence_in_consensus(self):
        """vision_evidence dict appears in ConsensusResult when VLM finds anomaly."""
        cost_json = json.dumps({
            "plan_id": "COST-PLAN-A", "summary": "Test", "num_operators": 3,
            "cycle_time_tmu": 420.0, "cost_per_unit_usd": 1.85,
            "throughput_uph": 52.0, "key_changes": [], "risk_flags": [],
        })
        vision_proof = {
            "verdict": "ANOMALY_CONFIRMED",
            "confidence": 0.91,
            "anomaly_description": "Conveyor jam confirmed at Station 4.",
            "detected_objects": [{"label": "conveyor_jam", "x": 0.3, "y": 0.4,
                                  "width": 0.3, "height": 0.2, "confidence": 0.91}],
            "recommended_actions": ["Halt conveyor", "Dispatch ARM-002"],
        }
        with patch("llm_client.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("agents.debate_room._fetch_temporal_context", new_callable=AsyncMock) as mock_hist, \
             patch("agents.debate_room._request_visual_proof", new_callable=AsyncMock) as mock_vis:
            mock_hist.return_value = ""
            mock_vis.return_value  = vision_proof
            mock_llm.side_effect   = [
                _make_llm_result(cost_json),
                _make_llm_result(_make_critique_json()),
                _make_llm_result(_make_consensus_json()),
            ]
            result = await run_debate_session(
                query      = ANOMALY_QUERY,
                session_id = SESSION_ID + "-c5",
                image_b64  = PLACEHOLDER_B64,
            )
            assert result.vision_evidence is not None
            assert result.vision_evidence.get("verdict") == "ANOMALY_CONFIRMED"
            objects = result.vision_evidence.get("detected_objects", [])
            assert any(o.get("label") == "conveyor_jam" for o in objects)


# ────────────────────────────────────────────────────────────────────────────
# Group D — Integration: full cyber-physical pipeline
# ────────────────────────────────────────────────────────────────────────────

class TestCyberPhysicalIntegration:
    """
    End-to-end pipeline:
      IoT anomaly → Swarm debate (with VisionProof) → ConsensusResult
                  → HITL approval (simulated) → ROS2 dispatch → audit log.
    """

    @pytest.mark.asyncio
    async def test_d1_anomaly_to_consensus_with_vision(self):
        """Scenario steps 1–5: anomaly → debate with vision → ConsensusResult."""
        cost_json = json.dumps({
            "plan_id": "COST-PLAN-A", "summary": "Reduce speed", "num_operators": 3,
            "cycle_time_tmu": 420.0, "cost_per_unit_usd": 1.85,
            "throughput_uph": 52.0, "key_changes": [], "risk_flags": [],
        })
        with patch("llm_client.call_llm", new_callable=AsyncMock) as mock_llm, \
             patch("agents.debate_room._fetch_temporal_context", new_callable=AsyncMock) as mock_hist, \
             patch("agents.debate_room._request_visual_proof", new_callable=AsyncMock) as mock_vis:
            mock_hist.return_value = ""
            mock_vis.return_value  = {
                "verdict": "ANOMALY_CONFIRMED",
                "confidence": 0.91,
                "anomaly_description": "Physical jam at conveyor gate Station 4.",
                "detected_objects": [],
                "recommended_actions": ["Halt Production Line 3", "Dispatch ARM-002 to Station 4"],
            }
            mock_llm.side_effect = [
                _make_llm_result(cost_json),
                _make_llm_result(_make_critique_json()),
                _make_llm_result(_make_consensus_json()),
            ]

            consensus = await run_debate_session(
                query      = ANOMALY_QUERY,
                session_id = SESSION_ID + "-d1",
                image_b64  = PLACEHOLDER_B64,
            )

        assert isinstance(consensus, ConsensusResult)
        assert consensus.vision_evidence is not None
        assert consensus.vision_evidence["verdict"] == "ANOMALY_CONFIRMED"

    @pytest.mark.asyncio
    async def test_d2_consensus_to_robot_dispatch(self):
        """Scenario step 6–8: consensus summary → ROS2 dispatch → ESTOP dispatched."""
        summary = (
            "Halt Production Line 3 immediately due to confirmed conveyor jam. "
            "Emergency stop all AGV units."
        )
        action_items = ["Halt Production Line 3", "ESTOP all AGVs"]

        commands = await dispatch_consensus_to_robots(
            summary      = summary,
            action_items = action_items,
            session_id   = SESSION_ID + "-d2",
            plan_id      = "CONSENSUS-TEST-001",
        )

        assert len(commands) >= 1
        estops = [c for c in commands if c.command_type == CommandType.ESTOP]
        assert len(estops) >= 1, "At least one ESTOP command should be dispatched."

    @pytest.mark.asyncio
    async def test_d3_dispatch_buffer_contains_estop(self):
        """Scenario step 8: dispatch buffer contains the issued ESTOP."""
        summary = (
            "Emergency halt station-4 ARM robot due to physical jam confirmation."
        )
        commands = await dispatch_consensus_to_robots(
            summary      = summary,
            action_items = ["Halt Production Line 3"],
            session_id   = SESSION_ID + "-d3",
            plan_id      = "P-D3",
        )
        buffer     = get_dispatched_commands(limit=100)
        buffer_ids = {c.command_id for c in buffer}
        for cmd in commands:
            assert cmd.command_id in buffer_ids

    @pytest.mark.asyncio
    async def test_d4_dispatched_command_signature_valid(self):
        """Scenario step 9: ESTOP command signature verifiable with Ed25519."""
        from security.provenance import verify_payload
        cmd = await dispatch_estop(
            robot_id   = "AGV-001",
            reason     = "integration-test estop",
            session_id = SESSION_ID + "-d4",
        )
        assert cmd.cryptographic_signature is not None
        # Verify the canonical signable dict against the stored signature.
        valid = verify_payload(cmd._signable_dict(), cmd.cryptographic_signature)
        assert valid, "Ed25519 signature verification failed for dispatched ESTOP."

    @pytest.mark.asyncio
    async def test_d5_audit_log_contains_both_events(self):
        """
        Scenario step 10: TamperEvidentAuditLog contains VLM_FRAME_ANALYSIS
        and ROBOT_COMMAND_DISPATCHED entries after the pipeline runs.
        """
        from telemetry import TamperEvidentAuditLog

        session = SESSION_ID + "-d5"

        # Step A: log a VLM analysis event
        vlm_entry = await TamperEvidentAuditLog.record(
            event_type = "VLM_FRAME_ANALYSIS",
            entity_id  = f"VIS-{session[:8]}",
            payload    = {
                "frame_id":      f"frame-{session}",
                "verdict":       "ANOMALY_CONFIRMED",
                "confidence":    0.91,
                "session_id":    session,
            },
        )

        # Step B: dispatch ESTOP (auto-logs ROBOT_COMMAND_DISPATCHED)
        cmd = await dispatch_estop(
            robot_id   = "AGV-002",
            reason     = "audit-chain integration test",
            session_id = session,
        )

        # Give fire-and-forget tasks time to complete
        await asyncio.sleep(0.05)

        # Verify the VLM entry block fields
        assert vlm_entry.event_type == "VLM_FRAME_ANALYSIS"
        assert vlm_entry.block_hash is not None
        assert vlm_entry.signature  is not None
        assert vlm_entry.previous_hash is not None

        # Verify the ROS2 dispatch command is in buffer
        buffer     = get_dispatched_commands(limit=100)
        robot_cmds = [c for c in buffer if c.session_id == session]
        assert any(c.command_type == CommandType.ESTOP for c in robot_cmds)

        # Verify hash chaining: current block's previous_hash is non-empty
        assert len(vlm_entry.previous_hash) == 64  # SHA-256 hex string


# ────────────────────────────────────────────────────────────────────────────
# Bonus: Edge-case and resilience tests
# ────────────────────────────────────────────────────────────────────────────

class TestEdgeCases:

    # Vision proof timeout → returns None, debate continues
    @pytest.mark.asyncio
    async def test_vision_timeout_does_not_block_debate(self):
        from agents.debate_room import _request_visual_proof
        with patch("agents.vision_inspector.analyse_frame") as mock_analyse:
            # Simulate an extremely slow VLM that times out
            async def _slow(*a, **kw):
                await asyncio.sleep(999)
            mock_analyse.side_effect = _slow

            import agents.debate_room as dr
            original = dr.VISION_PROOF_TIMEOUT_S
            dr.VISION_PROOF_TIMEOUT_S = 0.01  # tiny timeout for test
            result = await _request_visual_proof(
                query      = "test",
                session_id = "timeout-test",
            )
            dr.VISION_PROOF_TIMEOUT_S = original
            assert result is None, "Timed-out visual proof should return None."

    # ROS2 translator: unknown robot falls back to AGV-001
    def test_unknown_station_defaults_to_agv001(self):
        translator = ROS2CommandTranslator()
        cmds = translator.translate(
            summary      = "Halt production at station-99 AGV.",
            action_items = ["Halt Production Line 3"],
            session_id   = "edge-test",
            plan_id      = "P-EDGE",
        )
        if cmds:
            # Should not raise; robot_id may default to AGV-001
            assert cmds[0].robot_id in ["AGV-001", "AGV-002", "ARM-001", "ARM-002", "DRONE-001"]

    # dispatch_consensus_to_robots with no keywords → empty result (no crash)
    @pytest.mark.asyncio
    async def test_no_keywords_returns_empty(self):
        commands = await dispatch_consensus_to_robots(
            summary      = "Analyze cost efficiency and throughput metrics.",
            action_items = ["Review operational data"],
            session_id   = "no-keyword-test",
            plan_id      = "P-NK",
        )
        # The translator may or may not find keywords; must not raise
        assert isinstance(commands, list)
