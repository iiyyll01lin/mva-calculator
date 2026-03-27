"""
backend/events/iot_watchdog.py
────────────────────────────────────────────────────────────────────────────────
IoT Watchdog — Proactive Event-Driven Swarm Trigger

Architecture
────────────
  simulate_factory_stream()
    ├── emits FactoryStreamTick every STREAM_TICK_INTERVAL_S
    └── AnomalyDetector (rule engine)
           │  yield_rate < YIELD_CRITICAL_PCT for CONSECUTIVE_THRESHOLD ticks
           ▼
       AnomalyEvent fires
           │
           ├── Cooldown lock (per machine_id) — prevents duplicate debates
           │
           └── asyncio.create_task(_handle_anomaly())
                   │
                   └── run_debate_session()  ──►  DebateRoom (Cost vs Quality)
                               │
                               └── broadcast_emergency_proposal()
                                           │
                                           └── SSE push → Mission Control UI

Design principles
─────────────────
• The background task runs in the FastAPI asyncio event loop via
  asyncio.create_task() — it NEVER blocks the event loop.
• Separation of concerns: Watchdog detects, Swarm reasons, HITL guards execution.
• Idempotency: a per-machine cooldown lock prevents firing 50 concurrent debates
  for the same ongoing anomaly episode.
• All LLM work is spawned as a fire-and-forget task so the sensor tick loop
  itself is never stalled by LLM latency.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Configuration — override via module constants or environment variables
# ────────────────────────────────────────────────────────────────────────────

#: Seconds between synthetic sensor ticks.  1 s is fast for demos; raise to
#: 10–30 s for staging environments.
STREAM_TICK_INTERVAL_S: float = 5.0

#: Yield rate (%) below which a tick counts as anomalous.
YIELD_CRITICAL_PCT: float = 90.0

#: How many consecutive below-threshold ticks trigger an AnomalyEvent.
CONSECUTIVE_THRESHOLD: int = 3

#: Seconds after an anomaly fires before the same machine can trigger again.
#: Prevents 50 concurrent DebateRoom sessions for the same ongoing fault.
ANOMALY_COOLDOWN_S: float = 120.0

#: Number of simulated machines in the factory data stream.
NUM_MACHINES: int = 3


# ────────────────────────────────────────────────────────────────────────────
# Data Models
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class FactoryStreamTick:
    """Single telemetry snapshot from one machine at one point in time."""

    machine_id:    str
    timestamp:     str
    cycle_time_ms: float  # nominal target ~480 ms
    temperature_c: float  # nominal 45–55 °C
    yield_rate:    float  # 0–100 %

    @property
    def is_yield_anomalous(self) -> bool:
        return self.yield_rate < YIELD_CRITICAL_PCT


@dataclass
class AnomalyEvent:
    """Fired when a machine exceeds the consecutive below-threshold counter."""

    event_id:      str   = field(default_factory=lambda: str(uuid.uuid4()))
    machine_id:    str   = ""
    anomaly_type:  str   = "YIELD_BELOW_THRESHOLD"
    current_value: float = 0.0
    threshold:     float = YIELD_CRITICAL_PCT
    consecutive:   int   = 0
    detected_at:   str   = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


# ────────────────────────────────────────────────────────────────────────────
# Anomaly Detector — Stateful Rule Engine
# ────────────────────────────────────────────────────────────────────────────

class AnomalyDetector:
    """
    Lightweight stateful rule engine that tracks consecutive anomalous ticks
    per machine.

    Thread-safe within a single asyncio event loop — callers must not mutate
    state from multiple coroutines concurrently.
    """

    def __init__(self) -> None:
        # machine_id → consecutive below-threshold tick count
        self._consecutive: Dict[str, int] = {}

    def process(self, tick: FactoryStreamTick) -> Optional[AnomalyEvent]:
        """
        Ingest one tick.  Returns an AnomalyEvent if the threshold chain is
        breached; returns None otherwise.

        The consecutive counter resets to 0 once an event fires, so the same
        machine must breach the threshold another ``CONSECUTIVE_THRESHOLD``
        times before a second event is emitted (combined with the cooldown
        lock this gives robust debouncing).
        """
        mid = tick.machine_id
        if tick.is_yield_anomalous:
            self._consecutive[mid] = self._consecutive.get(mid, 0) + 1
            if self._consecutive[mid] >= CONSECUTIVE_THRESHOLD:
                # Reset counter so the next anomaly chain has to rebuild.
                self._consecutive[mid] = 0
                return AnomalyEvent(
                    machine_id    = mid,
                    current_value = tick.yield_rate,
                    consecutive   = CONSECUTIVE_THRESHOLD,
                )
        else:
            # Healthy tick — reset the counter for this machine.
            self._consecutive[mid] = 0
        return None

    def get_consecutive_count(self, machine_id: str) -> int:
        """Return current consecutive anomaly count for a machine (for tests)."""
        return self._consecutive.get(machine_id, 0)


# ────────────────────────────────────────────────────────────────────────────
# Synthetic Factory Data Generator
# ────────────────────────────────────────────────────────────────────────────

def _generate_tick(machine_id: str, force_anomaly: bool = False) -> FactoryStreamTick:
    """
    Produce one synthetic telemetry tick for the given machine.

    ``force_anomaly=True`` forces a sub-threshold yield, which is used by
    integration tests to drive the anomaly path deterministically.
    """
    if force_anomaly:
        yield_rate = random.uniform(82.0, 89.4)
    else:
        # Baseline: ~15 % chance of a marginal / anomalous tick
        r = random.random()
        if r < 0.10:
            yield_rate = random.uniform(84.0, 89.9)   # clearly anomalous
        elif r < 0.25:
            yield_rate = random.uniform(89.5, 92.0)   # borderline
        else:
            yield_rate = random.uniform(92.0, 99.5)   # healthy

    return FactoryStreamTick(
        machine_id    = machine_id,
        timestamp     = datetime.now(timezone.utc).isoformat(),
        cycle_time_ms = round(random.uniform(455.0, 525.0), 1),
        temperature_c = round(random.uniform(43.0, 60.0), 1),
        yield_rate    = round(yield_rate, 2),
    )


# ────────────────────────────────────────────────────────────────────────────
# Watchdog Singleton State
# ────────────────────────────────────────────────────────────────────────────

class _WatchdogState:
    """Module-level singleton that holds the watchdog lifecycle state."""

    def __init__(self) -> None:
        self.task:      Optional[asyncio.Task] = None
        self.running:   bool                   = False
        # per-machine cooldown: machine_id → monotonic expiry epoch
        self.cooldowns: Dict[str, float]       = {}


_state = _WatchdogState()


def _is_in_cooldown(machine_id: str) -> bool:
    """Return True if the machine is inside its post-anomaly cooldown window."""
    return time.monotonic() < _state.cooldowns.get(machine_id, 0.0)


def _set_cooldown(machine_id: str) -> None:
    """Arm cooldown for ``machine_id`` so the next anomaly is ignored for a while."""
    _state.cooldowns[machine_id] = time.monotonic() + ANOMALY_COOLDOWN_S
    logger.info(
        "Watchdog: cooldown armed for %s (%.0f s)", machine_id, ANOMALY_COOLDOWN_S
    )


# ────────────────────────────────────────────────────────────────────────────
# Proactive Swarm Orchestration
# ────────────────────────────────────────────────────────────────────────────

async def _handle_anomaly(event: AnomalyEvent) -> None:
    """
    React to a confirmed AnomalyEvent:
      1. Compose an internal system query describing the anomaly.
      2. Route the query to the DebateRoom (adversarial swarm debate).
      3. Package the consensus as an EmergencyProposal and push it via
         the broadcast_emergency_proposal() SSE fan-out.

    This coroutine is always spawned via asyncio.create_task() so it never
    blocks the main sensor-tick loop regardless of LLM round-trip latency.
    """
    from agents.debate_room import run_debate_session
    from telemetry import EmergencyProposal, broadcast_emergency_proposal

    session_id = f"watchdog-{event.event_id[:8]}"

    # Autonomously generated internal system query — no human prompt required.
    query = (
        f"CRITICAL: {event.machine_id} yield dropped to "
        f"{event.current_value:.1f}% (below {event.threshold:.0f}% threshold, "
        f"{event.consecutive} consecutive ticks). "
        "Analyze temporal production data, identify the root cause of the yield "
        "degradation, and propose a concrete mitigation plan including specific "
        "machine parameter adjustments and operator interventions."
    )

    logger.warning(
        "Watchdog: anomaly confirmed on %s (yield=%.1f%%) — "
        "dispatching DebateRoom session=%s",
        event.machine_id, event.current_value, session_id,
    )

    try:
        consensus = await run_debate_session(query=query, session_id=session_id)

        action_items = list(dict.fromkeys(
            consensus.adopted_from_cost + consensus.adopted_from_quality
        ))

        proposal = EmergencyProposal(
            proposal_id          = str(uuid.uuid4()),
            session_id           = session_id,
            machine_id           = event.machine_id,
            anomaly_type         = event.anomaly_type,
            current_value        = event.current_value,
            threshold            = event.threshold,
            summary              = consensus.summary,
            action_items         = action_items,
            trade_off_resolution = consensus.trade_off_resolution,
            confidence_score     = consensus.confidence_score,
            num_operators        = consensus.num_operators,
            throughput_uph       = consensus.throughput_uph,
            cost_per_unit_usd    = consensus.cost_per_unit_usd,
        )

        await broadcast_emergency_proposal(proposal)

        logger.info(
            "Watchdog: EMERGENCY_PROPOSAL broadcast complete "
            "(session=%s confidence=%.2f)",
            session_id, consensus.confidence_score,
        )

    except Exception as exc:
        logger.error(
            "Watchdog: DebateRoom failed for anomaly on %s: %s",
            event.machine_id, exc,
            exc_info=True,
        )


# ────────────────────────────────────────────────────────────────────────────
# Main Background Task
# ────────────────────────────────────────────────────────────────────────────

async def simulate_factory_stream() -> None:
    """
    Background coroutine — the heart of the IoT Watchdog.

    Emits synthetic factory telemetry ticks and runs each through the
    AnomalyDetector.  When an anomaly is confirmed AND the machine is not in
    cooldown, spawns ``_handle_anomaly()`` as a fire-and-forget task.

    The loop runs until ``_state.running`` is set to False by
    ``stop_watchdog()``.  Gracefully handles CancelledError on shutdown.
    """
    detector = AnomalyDetector()
    machines = [f"Machine-{i + 1}" for i in range(NUM_MACHINES)]
    tick_n   = 0

    logger.info(
        "Watchdog: factory stream started (%d machines, tick=%.1fs)",
        NUM_MACHINES, STREAM_TICK_INTERVAL_S,
    )

    while _state.running:
        tick_n += 1
        for mid in machines:
            tick = _generate_tick(mid)
            logger.debug(
                "Watchdog tick #%d — %s yield=%.1f%% temp=%.1f°C cycle=%.1fms",
                tick_n, mid, tick.yield_rate, tick.temperature_c, tick.cycle_time_ms,
            )

            event = detector.process(tick)
            if event is not None and not _is_in_cooldown(mid):
                # Arm cooldown BEFORE spawning so concurrent ticks on the same
                # machine within this loop iteration are also blocked.
                _set_cooldown(mid)
                asyncio.create_task(
                    _handle_anomaly(event),
                    name=f"watchdog_anomaly_{mid}_{event.event_id[:8]}",
                )

        try:
            await asyncio.sleep(STREAM_TICK_INTERVAL_S)
        except asyncio.CancelledError:
            break

    logger.info("Watchdog: factory stream stopped after %d ticks", tick_n)


# ────────────────────────────────────────────────────────────────────────────
# Public Lifecycle API — called by main.py startup / shutdown hooks
# ────────────────────────────────────────────────────────────────────────────

async def start_watchdog() -> None:
    """
    Start the IoT Watchdog as an asyncio background task.

    Idempotent: a second call while the watchdog is already running is a
    silent no-op.  The running task is stored in ``_state.task`` so that
    ``stop_watchdog()`` can cancel it gracefully on application shutdown.
    """
    if _state.running:
        logger.debug("Watchdog: already running; ignoring duplicate start.")
        return

    _state.running = True
    _state.cooldowns.clear()
    _state.task = asyncio.create_task(
        simulate_factory_stream(),
        name="iot_factory_watchdog",
    )
    logger.info("Watchdog: background task created and running.")


async def stop_watchdog() -> None:
    """
    Gracefully shut down the watchdog background task.

    Cancels the stream loop and awaits its termination (up to 5 s) so no
    dangling asyncio tasks remain when the application exits.  Safe to call
    even if the watchdog was never started.
    """
    _state.running = False
    if _state.task and not _state.task.done():
        _state.task.cancel()
        try:
            await asyncio.wait_for(_state.task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
    _state.task = None
    logger.info("Watchdog: background task stopped.")


def get_watchdog_status() -> dict:
    """
    Return a JSON-serialisable summary of the watchdog runtime state.

    Used by the ``GET /api/v1/watchdog/status`` endpoint so operators can
    inspect the watchdog health without tailing server logs.
    """
    return {
        "running":   _state.running,
        "task_alive": (
            _state.task is not None and not _state.task.done()
        ),
        "cooldowns": {
            mid: max(0.0, round(expiry - time.monotonic(), 1))
            for mid, expiry in _state.cooldowns.items()
        },
    }
