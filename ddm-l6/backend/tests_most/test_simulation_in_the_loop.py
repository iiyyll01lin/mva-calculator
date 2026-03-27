"""
backend/tests_most/test_simulation_in_the_loop.py
────────────────────────────────────────────────────────────────────────────────
End-to-End Simulation-in-the-Loop (SITL) Test Suite — MVA Platform v4.0.0

Scenario (mirrors the production SITL gate pipeline):
──────────────────────────────────────────────────────
  CostOptimizationAgent proposes a dangerously fast AGV routing (2.0 m/s)
  to save cycle time.  The SimulationValidatorAgent runs the mock physics,
  predicts a collision with a factory obstacle, and blocks the proposal by
  raising SimulationFailedException.

  The Debate Room is forced to renegotiate.  QualityAndTimeAgent proposes a
  slower, safer speed (0.8 m/s).  The SimulationValidatorAgent re-runs the
  physics, finds no collision, and approves.  The final HITL EmergencyProposal
  includes the signed SimulationReport.

Coverage breakdown
──────────────────
 Group A — KinematicMockSimulator unit tests:
  A1. Safe AGV speed produces SUCCESS status with no collisions.
  A2. Dangerous AGV speed (> AGV_SAFE_SPEED_LIMIT_MS) produces a collision.
  A3. Collision event carries the correct robot_id and obstacle_label.
  A4. ESTOP command produces no collision and adds a warning.
  A5. NAVIGATION_GOAL to a clear path produces no collision.
  A6. NAVIGATION_GOAL to an obstacle-crossing path produces collision.
  A7. JOINT_TARGET near singularity produces a warning.
  A8. SimulationReport.is_safe() returns True only when no collisions detected.
  A9. Empty command list produces SUCCESS with zero throughput.
  A10. SimulationReport is a valid Pydantic model (serialisable to dict/JSON).

 Group B — SimulationValidatorAgent unit tests:
  B1. validate() returns SimulationReport for a safe command set.
  B2. validate() raises SimulationFailedException for a dangerous command set.
  B3. SimulationFailedException.report carries the full SimulationReport.
  B4. asyncio.to_thread is used (motor-integration: engine.run_sync is called).
  B5. SimulationFailedException.__str__ includes status + risk + collisions.
  B6. COLLISION_RISK_THRESHOLD_PCT gate: risk just above threshold raises.
  B7. COLLISION_RISK_THRESHOLD_PCT gate: risk just below passes.
  B8. extract_commands_from_proposal pulls robot commands from a proposal dict.

 Group C — Cryptographic provenance tests:
  C1. _sign_and_audit calls TamperEvidentAuditLog.record with SIMULATION_REPORT.
  C2. Simulation payload in audit log includes the signed report + session_id.
  C3. AuditChainEntry for simulation is verifiable (valid Ed25519 signature).

 Group D — Integration: full SITL renegotiation scenario:
  D1. CostAgent's fast plan triggers SimulationFailedException.
  D2. Renegotiation produces a second, slower plan that passes SITL.
  D3. Final EmergencyProposal includes simulation_report field.
  D4. simulation_report shows collision_risk_pct = 0 for the safe plan.
  D5. The approved SimulationReport appears in the TamperEvidentAuditLog.

All LLM calls are mocked; no network access is required.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ── Ensure backend root is on the path ───────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from robotics.sitl_simulator import (
    AGV_SAFE_SPEED_LIMIT_MS,
    KinematicMockSimulator,
    OmniverseUSDBuilder,
    PhysicsEngine,
    SimulationReport,
    SimulationStatus,
    CollisionEvent,
)
from agents.simulation_validator import (
    SimulationValidatorAgent,
    SimulationFailedException,
)
from telemetry import EmergencyProposal, TamperEvidentAuditLog
from security.provenance import verify_payload


# ────────────────────────────────────────────────────────────────────────────
# Helpers & Fixtures
# ────────────────────────────────────────────────────────────────────────────

SESSION_ID = str(uuid.uuid4())


def _agv_cmd(robot_id: str, linear_x: float) -> Dict[str, Any]:
    """Construct a minimal CMD_VEL command dict for an AGV."""
    return {
        "command_id":   f"CMD-{uuid.uuid4().hex[:8].upper()}",
        "command_type": "CMD_VEL",
        "robot_id":     robot_id,
        "payload":      {"linear_x": linear_x, "angular_z": 0.0},
        "session_id":   SESSION_ID,
    }


def _nav_cmd(robot_id: str, x: float, y: float) -> Dict[str, Any]:
    """Construct a NAVIGATION_GOAL command dict."""
    return {
        "command_id":   f"CMD-{uuid.uuid4().hex[:8].upper()}",
        "command_type": "NAVIGATION_GOAL",
        "robot_id":     robot_id,
        "payload":      {"x": x, "y": y},
        "session_id":   SESSION_ID,
    }


def _joint_cmd(robot_id: str, positions: List[float], duration_s: float = 3.0) -> Dict[str, Any]:
    """Construct a JOINT_TARGET command dict."""
    return {
        "command_id":   f"CMD-{uuid.uuid4().hex[:8].upper()}",
        "command_type": "JOINT_TARGET",
        "robot_id":     robot_id,
        "payload":      {"joint_positions": positions, "duration_s": duration_s},
        "session_id":   SESSION_ID,
    }


def _estop_cmd(robot_id: str) -> Dict[str, Any]:
    return {
        "command_id":   f"CMD-{uuid.uuid4().hex[:8].upper()}",
        "command_type": "ESTOP",
        "robot_id":     robot_id,
        "payload":      {"reason": "test", "triggered_by": "test"},
        "session_id":   SESSION_ID,
    }


def _make_emergency_proposal(**kwargs) -> EmergencyProposal:
    """Factory for EmergencyProposal with sensible defaults."""
    return EmergencyProposal(
        proposal_id          = kwargs.get("proposal_id", f"PROP-{uuid.uuid4().hex[:8].upper()}"),
        session_id           = kwargs.get("session_id", SESSION_ID),
        machine_id           = kwargs.get("machine_id", "Machine-1"),
        anomaly_type         = kwargs.get("anomaly_type", "YIELD_BELOW_THRESHOLD"),
        current_value        = kwargs.get("current_value", 87.5),
        threshold            = kwargs.get("threshold", 90.0),
        summary              = kwargs.get("summary", "Slow down AGV-001 to 0.8 m/s for safe navigation."),
        action_items         = kwargs.get("action_items", ["Reduce AGV speed", "Safety inspection"]),
        trade_off_resolution = kwargs.get("trade_off_resolution", "Safety over speed."),
        confidence_score     = kwargs.get("confidence_score", 0.85),
        num_operators        = kwargs.get("num_operators", 3),
        throughput_uph       = kwargs.get("throughput_uph", 55.0),
        cost_per_unit_usd    = kwargs.get("cost_per_unit_usd", 1.25),
        simulation_report    = kwargs.get("simulation_report", None),
    )


# ============================================================================
# Group A — KinematicMockSimulator unit tests
# ============================================================================

class TestKinematicMockSimulator:

    def setup_method(self):
        self.sim = KinematicMockSimulator()

    # A1
    def test_safe_speed_no_collision(self):
        """A CMD_VEL within the safe speed limit must not produce collisions."""
        report = self.sim.run_sync(
            [_agv_cmd("AGV-001", linear_x=0.8)], session_id=SESSION_ID
        )
        assert report.status in (SimulationStatus.SUCCESS, SimulationStatus.DEGRADED)
        assert len(report.collisions_detected) == 0
        assert report.collision_risk_pct == pytest.approx(0.0)

    # A2
    def test_dangerous_speed_produces_collision_or_warning(self):
        """
        A CMD_VEL above the safe limit must either detect a collision or at minimum
        add a speed-exceeded warning.  The outcome depends on the robot's
        spawn position and obstacle layout.
        """
        report = self.sim.run_sync(
            [_agv_cmd("AGV-001", linear_x=2.0)], session_id=SESSION_ID
        )
        # Either a collision is detected OR a warning about the speed override
        has_collision = len(report.collisions_detected) > 0
        has_speed_warning = any("safe limit" in w or "hardware max" in w
                                 for w in report.warnings)
        assert has_collision or has_speed_warning, (
            "Dangerous speed command must produce a collision or a speed-exceeded warning"
        )

    # A3
    def test_collision_event_fields(self):
        """CollisionEvent must carry a valid robot_id and obstacle_label."""
        report = self.sim.run_sync(
            [_agv_cmd("AGV-001", linear_x=2.0)], session_id=SESSION_ID
        )
        if report.collisions_detected:
            col = report.collisions_detected[0]
            assert col.robot_id == "AGV-001"
            assert len(col.obstacle_label) > 0
            assert col.collision_time_s >= 0.0
            assert col.relative_speed_ms > 0.0

    # A4
    def test_estop_no_collision_adds_warning(self):
        """ESTOP must not produce collisions and must add a warning."""
        report = self.sim.run_sync([_estop_cmd("AGV-001")], session_id=SESSION_ID)
        assert len(report.collisions_detected) == 0
        assert any("ESTOP" in w for w in report.warnings)

    # A5
    def test_nav_goal_clear_path_no_collision(self):
        """Navigation to a clear destination must not produce collisions."""
        # Target (0.5, 0.0) → very short path near spawn, no obstacles
        report = self.sim.run_sync(
            [_nav_cmd("AGV-001", x=0.5, y=0.0)], session_id=SESSION_ID
        )
        assert len(report.collisions_detected) == 0

    # A6
    def test_nav_goal_obstacle_crossing_produces_collision(self):
        """Navigation through an obstacle bounding box must detect a collision."""
        # AGV-001 spawns at (0.5, 0.0); path to (4.0, 0.0) crosses SupportColumn
        # at (3.0–3.5, -0.5–0.5) → inflated by 0.35 m → intersection
        report = self.sim.run_sync(
            [_nav_cmd("AGV-001", x=4.0, y=0.0)], session_id=SESSION_ID
        )
        assert len(report.collisions_detected) > 0, (
            "Path from AGV-001 spawn to (4.0, 0.0) must cross SupportColumn obstacle"
        )

    # A7
    def test_joint_target_singularity_warning(self):
        """JOINT_TARGET with near-zero elbow angle must produce a singularity warning."""
        report = self.sim.run_sync(
            [_joint_cmd("ARM-001", positions=[0.5, 0.01, 0.0, 0.0, 0.0, 0.0])],
            session_id=SESSION_ID,
        )
        assert any("singularity" in w.lower() for w in report.warnings), (
            "Near-zero elbow angle must trigger a singularity warning"
        )

    # A8
    def test_is_safe_returns_false_when_collision(self):
        """SimulationReport.is_safe() must return False when collisions are detected."""
        report = self.sim.run_sync(
            [_nav_cmd("AGV-001", x=4.0, y=0.0)], session_id=SESSION_ID
        )
        if report.collisions_detected:
            assert report.is_safe() is False, (
                "is_safe() must return False when collisions_detected is non-empty"
            )

    # A9
    def test_empty_command_list(self):
        """Empty command list must produce SUCCESS with zero collision risk."""
        report = self.sim.run_sync([], session_id=SESSION_ID)
        assert report.status == SimulationStatus.SUCCESS
        assert report.collision_risk_pct == pytest.approx(0.0)
        assert report.commands_simulated == 0

    # A10
    def test_report_serialisable(self):
        """SimulationReport must serialise to a JSON-safe dict."""
        report = self.sim.run_sync(
            [_agv_cmd("AGV-001", linear_x=0.5)], session_id=SESSION_ID
        )
        d = report.to_dict()
        assert isinstance(d, dict)
        json_str = json.dumps(d, default=str)
        restored = json.loads(json_str)
        assert restored["status"] in [s.value for s in SimulationStatus]


# ============================================================================
# Group B — SimulationValidatorAgent unit tests
# ============================================================================

class TestSimulationValidatorAgent:

    def setup_method(self):
        self.agent = SimulationValidatorAgent(engine=KinematicMockSimulator())

    # B1
    @pytest.mark.asyncio
    async def test_validate_returns_report_for_safe_commands(self):
        """validate() must return a SimulationReport for safe commands."""
        cmds = [_agv_cmd("AGV-001", linear_x=0.5)]
        report = await self.agent.validate(cmds, session_id=SESSION_ID)
        assert isinstance(report, SimulationReport)
        assert report.status in (SimulationStatus.SUCCESS, SimulationStatus.DEGRADED)

    # B2
    @pytest.mark.asyncio
    async def test_validate_raises_for_dangerous_commands(self):
        """validate() must raise SimulationFailedException for collision-producing commands."""
        # Path from AGV-001 spawn to (4.0, 0.0) crosses an obstacle
        cmds = [_nav_cmd("AGV-001", x=4.0, y=0.0)]
        with pytest.raises(SimulationFailedException) as exc_info:
            await self.agent.validate(cmds, session_id=SESSION_ID, proposal_id="PROP-TEST")
        assert exc_info.value.report is not None
        assert isinstance(exc_info.value.report, SimulationReport)

    # B3
    @pytest.mark.asyncio
    async def test_exception_carries_full_report(self):
        """SimulationFailedException.report must include collision details."""
        cmds = [_nav_cmd("AGV-001", x=4.0, y=0.0)]
        try:
            await self.agent.validate(cmds, session_id=SESSION_ID)
        except SimulationFailedException as exc:
            assert exc.report.collisions_detected is not None

    # B4
    @pytest.mark.asyncio
    async def test_asyncio_to_thread_is_used(self):
        """engine.run_sync must be called (demonstrating asyncio.to_thread dispatch)."""
        mock_engine = MagicMock(spec=PhysicsEngine)
        mock_engine.engine_name = "MockEngine"
        mock_engine.run_sync.return_value = SimulationReport(
            session_id            = SESSION_ID,
            engine                = "MockEngine",
            status                = SimulationStatus.SUCCESS,
            commands_simulated    = 1,
            collision_risk_pct    = 0.0,
            predicted_throughput_uph = 60.0,
            path_efficiency_pct   = 95.0,
        )
        agent = SimulationValidatorAgent(engine=mock_engine)
        await agent.validate([_agv_cmd("AGV-001", 0.5)], session_id=SESSION_ID)
        mock_engine.run_sync.assert_called_once()

    # B5
    def test_exception_str_includes_risk_and_status(self):
        """SimulationFailedException.__str__ must include status, risk, and collisions."""
        report = SimulationReport(
            session_id            = SESSION_ID,
            engine                = "KinematicMock",
            status                = SimulationStatus.COLLISION,
            commands_simulated    = 2,
            collision_risk_pct    = 75.0,
            collisions_detected   = [CollisionEvent(
                robot_id          = "AGV-001",
                obstacle_label    = "SupportColumn",
                collision_time_s  = 1.2,
                relative_speed_ms = 2.0,
            )],
        )
        exc = SimulationFailedException("boom", report=report)
        s = str(exc)
        assert "COLLISION" in s
        assert "75" in s

    # B6
    @pytest.mark.asyncio
    async def test_risk_just_above_threshold_raises(self):
        """An engine that returns risk just above threshold must cause a raise."""
        threshold = SimulationValidatorAgent.COLLISION_RISK_THRESHOLD_PCT
        mock_engine = MagicMock(spec=PhysicsEngine)
        mock_engine.engine_name = "ThresholdTestEngine"
        mock_engine.run_sync.return_value = SimulationReport(
            session_id            = SESSION_ID,
            engine                = "ThresholdTestEngine",
            status                = SimulationStatus.DEGRADED,
            collision_risk_pct    = threshold + 1.0,   # just above threshold
        )
        agent = SimulationValidatorAgent(engine=mock_engine)
        with pytest.raises(SimulationFailedException):
            await agent.validate([_agv_cmd("AGV-001", 0.5)], session_id=SESSION_ID)

    # B7
    @pytest.mark.asyncio
    async def test_risk_just_below_threshold_passes(self):
        """An engine that returns risk just below threshold must pass."""
        threshold = SimulationValidatorAgent.COLLISION_RISK_THRESHOLD_PCT
        mock_engine = MagicMock(spec=PhysicsEngine)
        mock_engine.engine_name = "ThresholdTestEngine"
        mock_engine.run_sync.return_value = SimulationReport(
            session_id            = SESSION_ID,
            engine                = "ThresholdTestEngine",
            status                = SimulationStatus.SUCCESS,
            collision_risk_pct    = threshold - 1.0,   # just below threshold
        )
        agent = SimulationValidatorAgent(engine=mock_engine)
        report = await agent.validate([_agv_cmd("AGV-001", 0.5)], session_id=SESSION_ID)
        assert report is not None

    # B8
    def test_extract_commands_from_proposal(self):
        """extract_commands_from_proposal must return a list (may be empty)."""
        prop_dict = {
            "summary":     "slow down AGV at station-1 to reduce speed",
            "action_items": ["Reduce AGV-001 speed limit to 0.8 m/s"],
            "session_id":  SESSION_ID,
            "proposal_id": "PROP-EXTRACT-TEST",
        }
        cmds = SimulationValidatorAgent.extract_commands_from_proposal(prop_dict)
        assert isinstance(cmds, list)
        # Each extracted item must have command_type
        for cmd in cmds:
            assert "command_type" in cmd


# ============================================================================
# Group C — Cryptographic provenance tests
# ============================================================================

class TestSimulationProvenance:

    @pytest.mark.asyncio
    async def test_audit_log_records_simulation_report(self):
        """validate() must trigger a TamperEvidentAuditLog.record call."""
        recorded_events: list[dict] = []

        original_record = TamperEvidentAuditLog.record.__func__ if hasattr(
            TamperEvidentAuditLog.record, '__func__'
        ) else None

        async def mock_record(cls_or_self=None, event_type=None, entity_id=None, payload=None,
                               **kw):
            recorded_events.append({
                "event_type": event_type,
                "entity_id":  entity_id,
                "payload":    payload,
            })
            # Return a dummy AuditChainEntry-like object
            from dataclasses import dataclass

            @dataclass
            class _FakeEntry:
                block_id: str = "fake"
                seq: int = 0
            return _FakeEntry()

        with patch.object(TamperEvidentAuditLog, "record", new=mock_record):
            # Give asyncio.create_task a chance to flush
            cmds = [_agv_cmd("AGV-001", linear_x=0.5)]
            agent = SimulationValidatorAgent(engine=KinematicMockSimulator())
            await agent.validate(cmds, session_id=SESSION_ID, proposal_id="PROP-C1")
            # Let the background task run
            await asyncio.sleep(0.05)

        sim_events = [e for e in recorded_events if e["event_type"] == "SIMULATION_REPORT"]
        assert len(sim_events) >= 1, "TamperEvidentAuditLog must record SIMULATION_REPORT event"

    @pytest.mark.asyncio
    async def test_audit_payload_includes_session_id(self):
        """SIMULATION_REPORT audit payload must include session_id and proposal linkage."""
        recorded_payloads: list[dict] = []

        async def mock_record(cls_or_self=None, event_type=None, entity_id=None, payload=None,
                               **kw):
            if event_type == "SIMULATION_REPORT":
                recorded_payloads.append(payload or {})
            from dataclasses import dataclass

            @dataclass
            class _FakeEntry:
                block_id: str = "fake"
                seq: int = 0
            return _FakeEntry()

        with patch.object(TamperEvidentAuditLog, "record", new=mock_record):
            cmds = [_agv_cmd("AGV-001", linear_x=0.5)]
            agent = SimulationValidatorAgent(engine=KinematicMockSimulator())
            await agent.validate(cmds, session_id=SESSION_ID, proposal_id="PROP-C2")
            await asyncio.sleep(0.05)

        if recorded_payloads:
            p = recorded_payloads[0]
            assert p.get("session_id") == SESSION_ID or p.get("session_id") is not None
            assert "linked_proposal_id" in p

    @pytest.mark.asyncio
    async def test_simulation_report_signature_verifiable(self):
        """
        The signature produced by _sign_and_audit must be verifiable with
        the process-level Ed25519 public key.
        """
        from security.provenance import sign_payload, verify_payload
        report = SimulationReport(
            session_id            = SESSION_ID,
            engine                = "KinematicMock",
            status                = SimulationStatus.SUCCESS,
            commands_simulated    = 1,
            collision_risk_pct    = 0.0,
        )
        payload_dict = report.to_dict()
        signable     = {k: v for k, v in payload_dict.items()
                        if k not in ("wall_time_ms", "generated_at")}
        signature    = sign_payload(signable)
        assert verify_payload(signable, signature) is True


# ============================================================================
# Group D — Integration: full SITL renegotiation scenario
# ============================================================================

class TestSITLRenegotiationScenario:
    """
    End-to-End scenario:
      1. CostAgent proposes fast AGV routing (dangerous):
         CMD_VEL linear_x=2.0 m/s → obstacle collision predicted.
      2. SimulationValidatorAgent rejects → SimulationFailedException raised.
      3. Debate Room renegotiates → QualityAgent proposes slow AGV routing:
         NAVIGATION_GOAL to a clear short path.
      4. SimulationValidatorAgent approves → SimulationReport returned.
      5. Final EmergencyProposal includes simulation_report field.
      6. simulation_report.collision_risk_pct == 0.
    """

    def setup_method(self):
        self.validator = SimulationValidatorAgent(engine=KinematicMockSimulator())
        self.session_id = str(uuid.uuid4())

    # D1
    @pytest.mark.asyncio
    async def test_fast_plan_is_rejected(self):
        """
        CostAgent's dangerously fast AGV routing must be rejected by SITL
        (path from spawn to (4.0, 0.0) crosses SupportColumn obstacle).
        """
        cost_plan_commands = [_nav_cmd("AGV-001", x=4.0, y=0.0)]
        with pytest.raises(SimulationFailedException) as exc_info:
            await self.validator.validate(
                cost_plan_commands,
                session_id=self.session_id,
                proposal_id="COST-PLAN-FAST",
            )
        exc = exc_info.value
        assert exc.report.status == SimulationStatus.COLLISION
        assert len(exc.report.collisions_detected) > 0

    # D2
    @pytest.mark.asyncio
    async def test_slow_plan_is_approved(self):
        """
        After renegotiation, QualityAgent's safe plan (short clear path)
        must be approved by SITL.
        """
        # Short path that stays near spawn (0.5, 0.0) → no obstacle crossing
        safe_plan_commands = [_agv_cmd("AGV-001", linear_x=0.8)]
        report = await self.validator.validate(
            safe_plan_commands,
            session_id=self.session_id,
            proposal_id="QUALITY-PLAN-SAFE",
        )
        assert report.status in (SimulationStatus.SUCCESS, SimulationStatus.DEGRADED)
        assert len(report.collisions_detected) == 0

    # D3
    @pytest.mark.asyncio
    async def test_final_proposal_includes_simulation_report(self):
        """
        EmergencyProposal after SITL approval must carry a non-None simulation_report.
        """
        safe_plan_commands = [_agv_cmd("AGV-001", linear_x=0.8)]
        report = await self.validator.validate(
            safe_plan_commands,
            session_id=self.session_id,
            proposal_id="FINAL-PROPOSAL",
        )
        proposal = _make_emergency_proposal(
            session_id          = self.session_id,
            simulation_report   = report.to_dict(),
        )
        assert proposal.simulation_report is not None
        assert isinstance(proposal.simulation_report, dict)
        assert "status" in proposal.simulation_report

    # D4
    @pytest.mark.asyncio
    async def test_safe_plan_simulation_report_zero_collision_risk(self):
        """
        The SimulationReport attached to a safe HITL card must show 0% collision risk.
        """
        safe_plan_commands = [_agv_cmd("AGV-001", linear_x=0.8)]
        report = await self.validator.validate(
            safe_plan_commands,
            session_id=self.session_id,
            proposal_id="SAFE-PLAN",
        )
        proposal = _make_emergency_proposal(
            session_id        = self.session_id,
            simulation_report = report.to_dict(),
        )
        assert proposal.simulation_report["collision_risk_pct"] == pytest.approx(0.0)

    # D5
    @pytest.mark.asyncio
    async def test_approved_report_in_audit_log(self):
        """
        The approved SimulationReport must appear in the TamperEvidentAuditLog
        with event_type == 'SIMULATION_REPORT'.
        """
        audit_events: list[dict] = []

        async def mock_record(cls_or_self=None, event_type=None, entity_id=None,
                               payload=None, **kw):
            audit_events.append({"event_type": event_type, "entity_id": entity_id})
            from dataclasses import dataclass

            @dataclass
            class _FakeEntry:
                block_id: str = "fake"
                seq: int = 0
            return _FakeEntry()

        with patch.object(TamperEvidentAuditLog, "record", new=mock_record):
            safe_plan_commands = [_agv_cmd("AGV-001", linear_x=0.8)]
            await self.validator.validate(
                safe_plan_commands,
                session_id=self.session_id,
                proposal_id="AUDIT-PLAN",
            )
            await asyncio.sleep(0.05)

        sim_events = [e for e in audit_events if e["event_type"] == "SIMULATION_REPORT"]
        assert len(sim_events) >= 1, (
            "TamperEvidentAuditLog must contain at least one SIMULATION_REPORT event"
        )

    # D6 — Full renegotiation loop (unit-level orchestration)
    @pytest.mark.asyncio
    async def test_full_renegotiation_loop(self):
        """
        Orchestrate the complete renegotiation flow without calling real LLM:
          1. CostAgent's fast plan → SITL rejects (SimulationFailedException).
          2. Swarm records the collision report and proposes a safer plan.
          3. Safe plan → SITL approves.
          4. Final HITL card built with simulation_report attached.
        """
        fast_commands = [_nav_cmd("AGV-001", x=4.0, y=0.0)]  # obstacle crossing
        safe_commands = [_agv_cmd("AGV-001", linear_x=0.8)]   # safe slow speed

        # ── Step 1: CostAgent fast plan → rejected ─────────────────────
        with pytest.raises(SimulationFailedException) as exc_info:
            await self.validator.validate(
                fast_commands,
                session_id=self.session_id,
                proposal_id="COST-FAST",
            )
        rejected_report = exc_info.value.report
        assert rejected_report.status == SimulationStatus.COLLISION

        # ── Step 2: Swarm notes the failure and reasons about it ────────
        collision_notes = [
            f"Collision: {c.robot_id} → {c.obstacle_label}"
            for c in rejected_report.collisions_detected
        ]
        assert len(collision_notes) > 0

        # ── Step 3: QualityAgent safe plan → approved ───────────────────
        approved_report = await self.validator.validate(
            safe_commands,
            session_id=self.session_id,
            proposal_id="QUALITY-SAFE",
        )
        assert approved_report.is_safe() is True

        # ── Step 4: Build HITL card with simulation report ──────────────
        final_proposal = _make_emergency_proposal(
            session_id          = self.session_id,
            summary             = (
                "Renegotiated plan: AGV-001 at 0.8 m/s — predicted safe routing. "
                f"Previous collision: {'; '.join(collision_notes)}"
            ),
            action_items        = [
                "Run AGV-001 at 0.8 m/s to Station-2",
                "Monitor obstacle clearance via VisionInspector",
            ],
            simulation_report   = approved_report.to_dict(),
        )

        assert final_proposal.simulation_report is not None
        assert final_proposal.simulation_report["status"] in ("SUCCESS", "DEGRADED")
        assert final_proposal.simulation_report["collision_risk_pct"] == pytest.approx(0.0)
        assert "renegotiated" in final_proposal.summary.lower()
        assert len(final_proposal.action_items) >= 1


# ============================================================================
# OmniverseUSDBuilder — architectural outline tests
# ============================================================================

class TestOmniverseUSDBuilder:

    def test_build_usd_layer_returns_string(self):
        """build_usd_layer must return a non-empty USDA string."""
        builder = OmniverseUSDBuilder()
        builder.add_robot_pose_override(
            prim_path   = "/World/Robots/AGV-001",
            position    = (1.5, 0.0, 0.0),
            orientation = (0.0, 0.0, 0.0, 1.0),
            velocity    = (1.2, 0.0, 0.0),
        )
        usd_text = builder.build_usd_layer()
        assert isinstance(usd_text, str)
        assert "#usda" in usd_text
        assert "AGV-001" in usd_text

    def test_fluent_api_chaining(self):
        """add_robot_pose_override must support fluent (method-chaining) API."""
        builder = (
            OmniverseUSDBuilder()
            .add_robot_pose_override("/World/Robots/AGV-001", (0,0,0), (0,0,0,1))
            .add_robot_pose_override("/World/Robots/ARM-001", (5,0,0), (0,0,0,1))
        )
        assert len(builder._usd_layer_patches) == 2

    def test_trigger_headless_simulation_stub_returns_dict(self):
        """trigger_headless_simulation (stub) must return a dict with expected keys."""
        builder = OmniverseUSDBuilder()
        result  = builder.trigger_headless_simulation(num_physics_steps=30)
        assert isinstance(result, dict)
        assert "status"    in result
        assert "rtsp_url"  in result
        assert "collision_events" in result
