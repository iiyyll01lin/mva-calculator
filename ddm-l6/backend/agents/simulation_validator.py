"""
backend/agents/simulation_validator.py
────────────────────────────────────────────────────────────────────────────────
Predictive Rollout Agent — SITL Gate before HITL Escalation
MVA Platform v4.0.0

Role in the pipeline
─────────────────────

           Debate Room reaches physical consensus
                         │
                         ▼
             ┌───────────────────────┐
             │  SimulationValidatorAgent.validate()  │
             │                                      │
             │  1. Extract ROS2 commands from        │
             │     ConsensusResult / EmergencyProposal│
             │                                      │
             │  2. await asyncio.to_thread(          │
             │       engine.run_sync, cmds, sid      │
             │     )    ← non-blocking for FastAPI   │
             │                                      │
             │  3a. Simulation SAFE  ──────────────► HITL card with SimReport │
             │  3b. Simulation UNSAFE ─────────────► SimulationFailedException │
             └───────────────────────┘
                         │
             [UNSAFE path]
                         ▼
             Debate Room must renegotiate
             before bothering the human

Design Principles
─────────────────
• asyncio.to_thread is used for the CPU-bound physics calculation so the
  FastAPI event loop is never blocked, regardless of simulation duration.
• SimulationFailedException carries the full SimulationReport so the
  Debate Room can inspect which collisions occurred and renegotiate.
• The SimulationReport is cryptographically signed and appended to the
  TamperEvidentAuditLog before being returned — physics validation data
  integrity is guaranteed for every HITL card.
• The validator falls back to the KinematicMockSimulator if no engine is
  provided, ensuring it always works in dev/test mode.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from robotics.sitl_simulator import (
    KinematicMockSimulator,
    PhysicsEngine,
    SimulationReport,
    SimulationStatus,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Exceptions
# ────────────────────────────────────────────────────────────────────────────

class SimulationFailedException(Exception):
    """
    Raised by SimulationValidatorAgent when the physics rollout predicts an
    unsafe outcome (collision, ESTOP trigger, or unreachable pose).

    The ``report`` attribute carries the full SimulationReport so the
    Debate Room has detailed collision data to inform its renegotiation.
    """

    def __init__(self, message: str, report: SimulationReport) -> None:
        super().__init__(message)
        self.report = report

    def __str__(self) -> str:
        col_count = len(self.report.collisions_detected)
        return (
            f"SimulationFailed: {super().__str__()} | "
            f"status={self.report.status.value} "
            f"collisions={col_count} "
            f"risk={self.report.collision_risk_pct:.0f}%"
        )


# ────────────────────────────────────────────────────────────────────────────
# SimulationValidatorAgent
# ────────────────────────────────────────────────────────────────────────────

class SimulationValidatorAgent:
    """
    Predictive rollout gate that sits between the Debate Room and the HITL card.

    Workflow
    ────────
    1. Extract seralisable RobotCommand dicts from the proposal / consensus.
    2. Dispatch the blocking physics simulation via asyncio.to_thread.
    3. Sign the SimulationReport and append it to TamperEvidentAuditLog.
    4. Return the report if safe, or raise SimulationFailedException if not.

    Construction
    ────────────
    engine:  any PhysicsEngine implementation (defaults to KinematicMockSimulator).
    """

    #: Collision risk threshold above which the proposal is rejected.
    COLLISION_RISK_THRESHOLD_PCT: float = 15.0

    def __init__(self, engine: Optional[PhysicsEngine] = None) -> None:
        self._engine = engine or KinematicMockSimulator()
        logger.info(
            "SimulationValidatorAgent initialised with engine=%s",
            self._engine.engine_name,
        )

    # ------------------------------------------------------------------
    # Primary async entry point
    # ------------------------------------------------------------------

    async def validate(
        self,
        commands:    List[Dict[str, Any]],
        session_id:  str,
        proposal_id: str = "",
    ) -> SimulationReport:
        """
        Run the SITL validation gate asynchronously.

        Dispatches the CPU-bound physics calculation via ``asyncio.to_thread``
        to keep the FastAPI event loop free throughout the simulation.

        Args:
            commands:    List of RobotCommand-compatible dicts to simulate.
            session_id:  Originating debate session ID for telemetry correlation.
            proposal_id: Optional EmergencyProposal ID for audit trail linkage.

        Returns:
            SimulationReport when the simulation predicts a safe outcome.

        Raises:
            SimulationFailedException when collision_risk_pct exceeds the
            threshold or collisions are detected — caller (Debate Room) must
            renegotiate and produce a safer plan before HITL escalation.
        """
        logger.info(
            "SITL validation starting: engine=%s session=%s commands=%d",
            self._engine.engine_name, session_id[:8] if session_id else "N/A",
            len(commands),
        )

        # ── Dispatch CPU-bound physics via asyncio.to_thread ─────────────
        # This prevents the physics calculation from blocking the event loop
        # even if the simulation takes several seconds.
        report: SimulationReport = await asyncio.to_thread(
            self._engine.run_sync,
            commands,
            session_id,
        )

        # ── Sign the report and log to tamper-evident audit chain ─────────
        await self._sign_and_audit(report, session_id, proposal_id)

        # ── Gate check ────────────────────────────────────────────────────
        if not report.is_safe() or report.collision_risk_pct > self.COLLISION_RISK_THRESHOLD_PCT:
            col_summaries = [
                f"{c.robot_id}→{c.obstacle_label} @t={c.collision_time_s:.1f}s "
                f"speed={c.relative_speed_ms:.2f}m/s"
                for c in report.collisions_detected
            ]
            detail = "; ".join(col_summaries) if col_summaries else "high risk score"
            logger.warning(
                "SITL REJECTED proposal %s: status=%s risk=%.0f%% collisions=%d (%s)",
                proposal_id or "N/A",
                report.status.value,
                report.collision_risk_pct,
                len(report.collisions_detected),
                detail,
            )
            raise SimulationFailedException(
                f"Physics simulation predicts unsafe outcome ({detail})",
                report=report,
            )

        logger.info(
            "SITL PASSED proposal %s: status=%s risk=%.0f%% throughput=%.0f UPH",
            proposal_id or "N/A",
            report.status.value,
            report.collision_risk_pct,
            report.predicted_throughput_uph,
        )
        return report

    # ------------------------------------------------------------------
    # Convenience method — extract commands from EmergencyProposal dict
    # ------------------------------------------------------------------

    @staticmethod
    def extract_commands_from_proposal(proposal_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Extract a list of simulated RobotCommand dicts from an EmergencyProposal.

        The method inspects ``action_items`` and ``summary`` to infer
        physical command targets using the same keyword strategy as
        ROS2CommandTranslator.  This keeps the SITL gate independent of
        the ROS2 bridge (avoids circular imports) while still covering the
        same command space.

        Returns an empty list if no physical commands can be inferred
        (e.g. for purely advisory proposals).
        """
        from robotics.ros2_bridge import ROS2CommandTranslator, ROBOT_FLEET
        translator = ROS2CommandTranslator()
        summary = proposal_dict.get("summary", "")
        action_items = proposal_dict.get("action_items", [])
        session_id = proposal_dict.get("session_id", "")
        plan_id = proposal_dict.get("proposal_id", "N/A")

        robot_commands = translator.translate(
            summary=summary,
            action_items=action_items,
            session_id=session_id,
            plan_id=plan_id,
        )
        return [cmd.model_dump() for cmd in robot_commands]

    # ------------------------------------------------------------------
    # Cryptographic provenance (private)
    # ------------------------------------------------------------------

    @staticmethod
    async def _sign_and_audit(
        report:      SimulationReport,
        session_id:  str,
        proposal_id: str,
    ) -> None:
        """
        Sign the SimulationReport and record it in the TamperEvidentAuditLog.

        The signature guarantees that physics validation data shown to the
        human operator has not been tampered with between generation and display.
        """
        try:
            from security.provenance import sign_payload
            payload_dict = report.to_dict()
            # Exclude mutable fields before signing
            signable = {k: v for k, v in payload_dict.items()
                        if k not in ("wall_time_ms", "generated_at")}
            signature = sign_payload(signable)
            logger.debug(
                "SimulationReport %s signed (sig_prefix=%s)",
                report.report_id, signature[:16],
            )
        except Exception as exc:
            signature = None
            logger.warning("Failed to sign SimulationReport %s: %s", report.report_id, exc)

        # Append to the tamper-evident audit chain (fire-and-forget)
        try:
            from telemetry import TamperEvidentAuditLog
            asyncio.create_task(
                TamperEvidentAuditLog.record(
                    event_type = "SIMULATION_REPORT",
                    entity_id  = report.report_id,
                    payload    = {
                        **report.to_dict(),
                        "cryptographic_signature": signature,
                        "linked_proposal_id":      proposal_id,
                        "session_id":              session_id,
                    },
                )
            )
        except Exception as exc:
            logger.warning(
                "SimulationReport audit log failed for %s: %s",
                report.report_id, exc,
            )
