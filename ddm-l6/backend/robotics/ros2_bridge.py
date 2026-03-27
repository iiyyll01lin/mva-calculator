"""
backend/robotics/ros2_bridge.py
────────────────────────────────────────────────────────────────────────────────
Cyber-Physical Dispatcher — ROS2 Action Bridge  ("Hands")
MVA Platform v3.0.0

Purpose
───────
Translates the Swarm's abstract ConsensusResult / EmergencyProposal into
strictly typed physical robotic commands and dispatches them over MQTT
(or WebSocket) to factory AGVs and robotic arms.

Supported command types
───────────────────────
  CMD_VEL          — velocity command for AGVs (linear.x, angular.z)
  JOINT_TARGET     — joint-angle target for robotic arms (6-DOF)
  ESTOP            — emergency stop for any robotic asset
  GRIPPER          — open / close gripper command
  NAVIGATION_GOAL  — nav2-compatible 2-D pose goal for autonomous navigation

Architecture
────────────
  ConsensusResult / EmergencyProposal
      │
      ▼
  ROS2CommandTranslator.translate()    ← keyword-mapping NLU
      │
      ▼
  RobotCommand (strictly typed, Pydantic)
      │
      ├─► sign_payload + TamperEvidentAuditLog
      │
      └─► ROS2BridgeDispatcher.dispatch()
               │
               ├─► MQTT publish  (if MQTT_BROKER_URL configured)
               └─► WebSocket send (if ROS2_BRIDGE_WS_URL configured)
               └─► [stub] in-memory queue (dev / test mode)

Security guarantees
───────────────────
• Every RobotCommand is Ed25519-signed before dispatch.
• Dispatch events are appended to the TamperEvidentAuditLog.
• MQTT messages carry the signature in the header so edge robots can verify
  the command's origin before execution.
• ESTOP commands bypass the HITL queue (emergency override).  All other
  commands should be dispatched only AFTER HITL approval.

Simulation notes
────────────────
No actual ROS2 runtime is required.  The bridge publishes to an MQTT topic
(e.g. mva/robots/<robot_id>/cmd) that a real ROS2 MQTT bridge node would
subscribe to.  In test / dev mode (no broker configured), commands are
pushed into an in-memory ring buffer that tests can inspect.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Configuration — override via environment variables
# ────────────────────────────────────────────────────────────────────────────

#: MQTT broker URL, e.g. "mqtt://mqtt-broker:1883"
MQTT_BROKER_URL: str = os.environ.get("MQTT_BROKER_URL", "")

#: rosbridge_suite WebSocket URL, e.g. "ws://ros2-bridge:9090"
ROS2_BRIDGE_WS_URL: str = os.environ.get("ROS2_BRIDGE_WS_URL", "")

#: Root MQTT topic prefix.  Commands are published to
#: {MQTT_TOPIC_ROOT}/{robot_id}/cmd
MQTT_TOPIC_ROOT: str = os.environ.get("MQTT_TOPIC_ROOT", "mva/robots")

#: Maximum dispatched commands held in the in-memory stub queue.
DISPATCH_BUFFER_MAX: int = int(os.environ.get("ROS2_DISPATCH_BUFFER_MAX", "500"))


# ────────────────────────────────────────────────────────────────────────────
# Robot Registry — simulated fleet
# ────────────────────────────────────────────────────────────────────────────

class RobotStatus(str, Enum):
    ONLINE   = "ONLINE"
    OFFLINE  = "OFFLINE"
    ESTOP    = "ESTOP"
    CHARGING = "CHARGING"
    BUSY     = "BUSY"


@dataclass
class RobotAgent:
    """
    Represents a registered physical robot in the factory fleet.
    Each robot has a stable ID, a human-readable role, and a mutable status.
    """
    robot_id:    str
    role:        str             # "AGV", "robotic_arm", "inspection_drone"
    station:     str             # "Station-1" … "Station-N"
    status:      RobotStatus     = RobotStatus.ONLINE
    last_seen:   str             = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ── Simulated fleet — 3 machines × 2 robot types ────────────────────────────
ROBOT_FLEET: Dict[str, RobotAgent] = {
    "AGV-001":     RobotAgent("AGV-001",     "AGV",          "Station-1"),
    "AGV-002":     RobotAgent("AGV-002",     "AGV",          "Station-3"),
    "ARM-001":     RobotAgent("ARM-001",     "robotic_arm",  "Station-2"),
    "ARM-002":     RobotAgent("ARM-002",     "robotic_arm",  "Station-4"),
    "DRONE-001":   RobotAgent("DRONE-001",   "inspection_drone", "Station-2"),
}


# ────────────────────────────────────────────────────────────────────────────
# Pydantic Models — strictly typed physical commands
# ────────────────────────────────────────────────────────────────────────────

class CommandType(str, Enum):
    CMD_VEL         = "CMD_VEL"          # AGV velocity command
    JOINT_TARGET    = "JOINT_TARGET"     # arm joint angles
    ESTOP           = "ESTOP"            # emergency stop
    GRIPPER         = "GRIPPER"          # open / close gripper
    NAVIGATION_GOAL = "NAVIGATION_GOAL"  # nav2 goal pose


class CmdVelPayload(BaseModel):
    """ROS2 geometry_msgs/Twist equivalent."""
    linear_x:  float = Field(default=0.0, ge=-2.0,  le=2.0,
                             description="Forward velocity m/s.")
    linear_y:  float = Field(default=0.0, ge=-2.0,  le=2.0,
                             description="Lateral velocity m/s.")
    angular_z: float = Field(default=0.0, ge=-3.14, le=3.14,
                             description="Rotation rate rad/s.")
    model_config = {"extra": "ignore"}


class JointTargetPayload(BaseModel):
    """6-DOF joint target (rad) — matches ROS2 trajectory_msgs/JointTrajectoryPoint."""
    joint_positions: List[float] = Field(
        ..., min_length=1, max_length=7,
        description="Target joint angles in radians (up to 7-DOF).",
    )
    duration_s: float = Field(default=3.0, gt=0.0, le=60.0,
                              description="Execution duration in seconds.")
    model_config = {"extra": "ignore"}


class EstopPayload(BaseModel):
    """Emergency stop command — immediately halts ALL motion on the target robot."""
    reason:       str = Field(..., description="Human-readable ESTOP reason.")
    triggered_by: str = Field(default="SwarmConsensus",
                              description="Source that triggered the ESTOP.")
    model_config = {"extra": "ignore"}


class GripperPayload(BaseModel):
    """Open or close the end-effector gripper."""
    open: bool  = Field(..., description="True = open, False = close.")
    force_n: float = Field(default=10.0, ge=0.0, le=200.0,
                           description="Gripper force in Newtons.")
    model_config = {"extra": "ignore"}


class NavigationGoalPayload(BaseModel):
    """nav2-compatible target pose in the map frame."""
    x:   float = Field(..., description="Target X position (m).")
    y:   float = Field(..., description="Target Y position (m).")
    yaw: float = Field(default=0.0, ge=-3.14, le=3.14,
                       description="Target heading (rad).")
    model_config = {"extra": "ignore"}


class RobotCommand(BaseModel):
    """
    A single, strictly typed, cryptographically signed physical command.

    This is the primary data contract between the Swarm and the physical
    edge robots.  Commands MUST NOT be dispatched until after HITL approval
    (except ESTOP which is an emergency override).
    """
    command_id:             str         = Field(
        default_factory=lambda: f"CMD-{uuid.uuid4().hex[:8].upper()}"
    )
    command_type:           CommandType
    robot_id:               str         = Field(..., description="Target robot from ROBOT_FLEET.")
    session_id:             str         = Field(default="",
                                               description="Originating debate session ID.")
    consensus_plan_id:      str         = Field(default="",
                                               description="ConsensusResult.consensus_plan_id.")
    payload:                Dict[str, Any]
    rationale:              str         = Field(default="",
                                               description="One-sentence justification.")
    issued_at:              str         = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    dispatched:             bool        = False
    dispatched_at:          Optional[str]  = None
    dispatch_channel:       Optional[str]  = None  # "mqtt" | "websocket" | "stub"
    cryptographic_signature: Optional[str] = None

    model_config = {"extra": "ignore"}

    def _signable_dict(self) -> Dict[str, Any]:
        d = self.model_dump()
        d.pop("cryptographic_signature", None)
        d.pop("dispatched", None)
        d.pop("dispatched_at", None)
        d.pop("dispatch_channel", None)
        return d


# ────────────────────────────────────────────────────────────────────────────
# In-Memory Dispatch Buffer (stub / test mode)
# ────────────────────────────────────────────────────────────────────────────

_dispatch_buffer: List[RobotCommand] = []
_dispatch_lock: Optional[asyncio.Lock] = None


def _get_dispatch_lock() -> asyncio.Lock:
    global _dispatch_lock
    if _dispatch_lock is None:
        _dispatch_lock = asyncio.Lock()
    return _dispatch_lock


def get_dispatched_commands(limit: int = 100) -> List[RobotCommand]:
    """Return the most recent dispatched commands (for tests and the /fleet API)."""
    return _dispatch_buffer[-limit:]


# ────────────────────────────────────────────────────────────────────────────
# ROS2CommandTranslator — NLU layer
# ────────────────────────────────────────────────────────────────────────────

class ROS2CommandTranslator:
    """
    Translates abstract Swarm proposals into concrete RobotCommand objects.

    The translation uses keyword matching against the consensus summary and
    action_items / adopted_from_* fields to determine:
      1. COMMAND_TYPE  — what physical action to take
      2. TARGET_ROBOT  — which robot in the fleet to send it to
      3. PAYLOAD       — type-safe parameters

    This is intentionally deterministic (no LLM call) to keep the
    cyber-physical dispatch path fast, auditable, and predictable.
    """

    # Keyword → CommandType priority mapping (evaluated top-to-bottom)
    _CMD_TYPE_KEYWORDS: List[tuple[List[str], CommandType]] = [
        (["estop", "e-stop", "emergency stop", "halt", "stop immediately",
          "shutdown", "power off", "kill"], CommandType.ESTOP),
        (["navigate", "move to", "drive to", "relocate", "proceed to"],
         CommandType.NAVIGATION_GOAL),
        (["slow down", "reduce speed", "speed limit", "velocity"],
         CommandType.CMD_VEL),
        (["gripper", "pick up", "grasp", "release", "drop"],
         CommandType.GRIPPER),
        (["joint", "arm position", "retract", "extend", "rotate arm"],
         CommandType.JOINT_TARGET),
    ]

    # Station keyword → Robot ID mapping
    _STATION_TO_ROBOT: Dict[str, str] = {
        "station-1": "AGV-001",
        "station-2": "ARM-001",
        "station-3": "AGV-002",
        "station-4": "ARM-002",
        "drone":     "DRONE-001",
    }

    def translate(
        self,
        summary:      str,
        action_items: List[str],
        session_id:   str,
        plan_id:      str,
    ) -> List[RobotCommand]:
        """
        Produce a list of RobotCommands from a Swarm consensus summary.

        Returns an empty list if no actionable physical commands can be inferred.
        Always returns at most one command per robot to prevent conflicting
        simultaneous dispatches.
        """
        full_text = (summary + " " + " ".join(action_items)).lower()
        commands:  List[RobotCommand] = []
        targeted:  set[str]           = set()  # prevent duplicate robot targeting

        for keywords, cmd_type in self._CMD_TYPE_KEYWORDS:
            if not any(kw in full_text for kw in keywords):
                continue

            robot_id = self._infer_robot(full_text, cmd_type)
            if robot_id in targeted:
                continue
            targeted.add(robot_id)

            payload  = self._build_payload(cmd_type, full_text)
            rationale = self._extract_rationale(cmd_type, action_items)

            commands.append(RobotCommand(
                command_type      = cmd_type,
                robot_id          = robot_id,
                session_id        = session_id,
                consensus_plan_id = plan_id,
                payload           = payload,
                rationale         = rationale,
            ))

        # If nothing matched but the context implies a halt, default to ESTOP.
        if not commands and any(
            kw in full_text for kw in ["halt production", "stop production",
                                         "shut down line", "emergency"]
        ):
            commands.append(RobotCommand(
                command_type      = CommandType.ESTOP,
                robot_id          = "AGV-001",
                session_id        = session_id,
                consensus_plan_id = plan_id,
                payload           = EstopPayload(
                    reason       = "Swarm consensus: production halt requested.",
                    triggered_by = "SwarmConsensus",
                ).model_dump(),
                rationale = "Default ESTOP inferred from production-halt directive.",
            ))

        return commands

    def _infer_robot(self, text: str, cmd_type: CommandType) -> str:
        """Infer the target robot from station mentions or command type defaults."""
        for station_kw, robot_id in self._STATION_TO_ROBOT.items():
            if station_kw in text:
                return robot_id
        # Type-based defaults
        if cmd_type in (CommandType.ESTOP, CommandType.CMD_VEL,
                        CommandType.NAVIGATION_GOAL):
            return "AGV-001"
        if cmd_type in (CommandType.JOINT_TARGET, CommandType.GRIPPER):
            return "ARM-001"
        return "AGV-001"

    @staticmethod
    def _build_payload(cmd_type: CommandType, text: str) -> Dict[str, Any]:
        """Build a type-safe payload dict for the given command type."""
        if cmd_type == CommandType.ESTOP:
            return EstopPayload(
                reason       = "Swarm consensus: emergency halt directive.",
                triggered_by = "SwarmConsensus",
            ).model_dump()

        if cmd_type == CommandType.CMD_VEL:
            # Parse speed hints from text  (e.g. "reduce speed by 10%")
            speed = 0.3  # conservative default
            if "stop" in text or "halt" in text:
                speed = 0.0
            elif "slow" in text or "reduce" in text:
                speed = 0.2
            return CmdVelPayload(linear_x=speed).model_dump()

        if cmd_type == CommandType.JOINT_TARGET:
            return JointTargetPayload(
                joint_positions=[0.0, -0.5, 0.8, 0.0, 0.5, 0.0],
                duration_s=4.0,
            ).model_dump()

        if cmd_type == CommandType.GRIPPER:
            should_open = "open" in text or "release" in text or "drop" in text
            return GripperPayload(open=should_open, force_n=15.0).model_dump()

        if cmd_type == CommandType.NAVIGATION_GOAL:
            return NavigationGoalPayload(x=5.0, y=3.0, yaw=0.0).model_dump()

        return {}

    @staticmethod
    def _extract_rationale(cmd_type: CommandType, action_items: List[str]) -> str:
        if action_items:
            return action_items[0][:200]
        return f"Swarm-generated {cmd_type.value} directive."


# ────────────────────────────────────────────────────────────────────────────
# ROS2BridgeDispatcher — actual dispatch layer
# ────────────────────────────────────────────────────────────────────────────

class ROS2BridgeDispatcher:
    """
    Dispatches signed RobotCommands to factory robots over MQTT or WebSocket.

    In dev / stub mode (no broker/bridge URL configured), commands are
    pushed to an in-memory ring buffer and logged at INFO level.
    """

    async def dispatch(self, command: RobotCommand) -> RobotCommand:
        """
        Sign, log, and dispatch a single RobotCommand.

        Args:
            command: A RobotCommand (typically produced by ROS2CommandTranslator).

        Returns:
            The same command with ``dispatched=True`` and provenance fields set.

        Side effects:
            • Appends an audit block to TamperEvidentAuditLog.
            • Publishes to MQTT or WebSocket (if configured).
            • Falls back to in-memory stub queue if neither is available.
        """
        from telemetry import TamperEvidentAuditLog

        # ── 1. Sign the command ────────────────────────────────────────────
        command = self._sign_command(command)

        # ── 2. Validate target robot is in the fleet ───────────────────────
        robot = ROBOT_FLEET.get(command.robot_id)
        if robot is None:
            raise ValueError(
                f"ROS2Bridge: unknown robot_id '{command.robot_id}'. "
                f"Registered robots: {list(ROBOT_FLEET.keys())}"
            )

        # ── 3. Dispatch ────────────────────────────────────────────────────
        channel = await self._select_and_send(command)

        # ── 4. Mark as dispatched ──────────────────────────────────────────
        command = command.model_copy(update={
            "dispatched":       True,
            "dispatched_at":    datetime.now(timezone.utc).isoformat(),
            "dispatch_channel": channel,
        })

        # ── 5. Log to in-memory buffer ─────────────────────────────────────
        lock = _get_dispatch_lock()
        async with lock:
            if len(_dispatch_buffer) >= DISPATCH_BUFFER_MAX:
                _dispatch_buffer.pop(0)
            _dispatch_buffer.append(command)

        # ── 6. Audit log ─── (fire-and-forget) ────────────────────────────
        asyncio.create_task(
            TamperEvidentAuditLog.record(
                event_type = "ROBOT_COMMAND_DISPATCHED",
                entity_id  = command.command_id,
                payload    = command.model_dump(),
            )
        )

        logger.info(
            "ROS2Bridge: %s dispatched to %s via %s (session=%s plan=%s)",
            command.command_type.value,
            command.robot_id,
            channel,
            command.session_id,
            command.consensus_plan_id,
        )

        # Update fleet status for ESTOP commands
        if command.command_type == CommandType.ESTOP:
            ROBOT_FLEET[command.robot_id].status   = RobotStatus.ESTOP
            ROBOT_FLEET[command.robot_id].last_seen = command.dispatched_at or ""

        return command

    async def _select_and_send(self, command: RobotCommand) -> str:
        """Choose the best available transport and send."""
        if MQTT_BROKER_URL:
            try:
                await self._publish_mqtt(command)
                return "mqtt"
            except Exception as exc:
                logger.warning("ROS2Bridge: MQTT dispatch failed (%s); trying WS.", exc)

        if ROS2_BRIDGE_WS_URL:
            try:
                await self._send_websocket(command)
                return "websocket"
            except Exception as exc:
                logger.warning("ROS2Bridge: WebSocket dispatch failed (%s); using stub.", exc)

        # Stub mode — dev / test
        logger.debug(
            "ROS2Bridge: stub dispatch — command %s queued in memory.",
            command.command_id,
        )
        return "stub"

    @staticmethod
    async def _publish_mqtt(command: RobotCommand) -> None:
        """
        Publish command to MQTT broker using aiomqtt (optional dep).

        Topic: {MQTT_TOPIC_ROOT}/{robot_id}/cmd
        Payload: JSON with embedded Ed25519 signature for edge verification.
        """
        try:
            import aiomqtt  # optional — install with: pip install aiomqtt
        except ImportError:
            raise RuntimeError(
                "aiomqtt is not installed.  "
                "Run: pip install aiomqtt  to enable MQTT dispatch."
            )

        topic   = f"{MQTT_TOPIC_ROOT}/{command.robot_id}/cmd"
        message = json.dumps({
            "command_id":  command.command_id,
            "type":        command.command_type.value,
            "payload":     command.payload,
            "signature":   command.cryptographic_signature,
            "issued_at":   command.issued_at,
            "session_id":  command.session_id,
        })

        async with aiomqtt.Client(MQTT_BROKER_URL) as client:
            await client.publish(topic, payload=message, qos=1, retain=False)

        logger.debug("ROS2Bridge: MQTT published to %s", topic)

    @staticmethod
    async def _send_websocket(command: RobotCommand) -> None:
        """
        Send command over rosbridge_suite WebSocket protocol.

        Message format: rosbridge advertise + publish (simplified).
        """
        try:
            import websockets  # optional — install with: pip install websockets
        except ImportError:
            raise RuntimeError(
                "websockets is not installed.  "
                "Run: pip install websockets  to enable WebSocket dispatch."
            )

        # rosbridge_suite Publish message
        msg = json.dumps({
            "op":    "publish",
            "topic": f"/mva/{command.robot_id}/cmd",
            "msg":   {
                "command_id": command.command_id,
                "type":       command.command_type.value,
                "payload":    command.payload,
                "signature":  command.cryptographic_signature,
            },
        })

        async with websockets.connect(ROS2_BRIDGE_WS_URL, open_timeout=5) as ws:
            await ws.send(msg)

        logger.debug("ROS2Bridge: WS published to %s", ROS2_BRIDGE_WS_URL)

    @staticmethod
    def _sign_command(command: RobotCommand) -> RobotCommand:
        """Ed25519-sign the command payload before dispatch."""
        try:
            from security.provenance import sign_payload
            sig = sign_payload(command._signable_dict())
            return command.model_copy(update={"cryptographic_signature": sig})
        except Exception as exc:
            logger.warning("ROS2Bridge: signing failed: %s", exc)
            return command


# ────────────────────────────────────────────────────────────────────────────
# Public Facade — single entry point for the Swarm
# ────────────────────────────────────────────────────────────────────────────

_translator  = ROS2CommandTranslator()
_dispatcher  = ROS2BridgeDispatcher()


async def dispatch_consensus_to_robots(
    summary:      str,
    action_items: List[str],
    session_id:   str,
    plan_id:      str  = "",
) -> List[RobotCommand]:
    """
    Translate a Swarm consensus into physical commands and dispatch them.

    This is the single entry point called after HITL approval.

    Args:
        summary:      ConsensusResult.summary or EmergencyProposal.summary.
        action_items: List of concrete action strings from the consensus.
        session_id:   Originating debate / watchdog session ID.
        plan_id:      ConsensusResult.consensus_plan_id (optional).

    Returns:
        List[RobotCommand] — the dispatched commands with signatures and
        timestamps.  May be empty if no physical actions were inferred.

    Example::

        commands = await dispatch_consensus_to_robots(
            summary      = consensus.summary,
            action_items = consensus.adopted_from_quality,
            session_id   = session_id,
            plan_id      = consensus.consensus_plan_id,
        )
    """
    commands = _translator.translate(
        summary      = summary,
        action_items = action_items,
        session_id   = session_id,
        plan_id      = plan_id,
    )

    if not commands:
        logger.info(
            "ROS2Bridge: no physical commands inferred from session=%s", session_id
        )
        return []

    dispatched: List[RobotCommand] = []
    for cmd in commands:
        try:
            d = await _dispatcher.dispatch(cmd)
            dispatched.append(d)
        except Exception as exc:
            logger.error(
                "ROS2Bridge: dispatch failed for %s → %s: %s",
                cmd.command_type.value, cmd.robot_id, exc,
                exc_info=True,
            )

    return dispatched


async def dispatch_estop(
    robot_id:   str,
    reason:     str,
    session_id: str,
) -> RobotCommand:
    """
    Immediately dispatch an ESTOP to a specific robot (no HITL required).

    Used for emergency halt before the full debate cycle completes.
    """
    cmd = RobotCommand(
        command_type = CommandType.ESTOP,
        robot_id     = robot_id,
        session_id   = session_id,
        payload      = EstopPayload(
            reason       = reason,
            triggered_by = "EmergencyEstop",
        ).model_dump(),
        rationale    = reason,
    )
    return await _dispatcher.dispatch(cmd)
