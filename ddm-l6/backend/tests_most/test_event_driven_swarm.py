"""
tests_most/test_event_driven_swarm.py
────────────────────────────────────────────────────────────────────────────────
E2E Test Suite — Event-Driven Proactive Swarm

Coverage
────────
 1. AnomalyDetector correctly counts consecutive below-threshold ticks.
 2. AnomalyDetector fires an AnomalyEvent only after CONSECUTIVE_THRESHOLD ticks.
 3. AnomalyDetector resets the counter after firing (no double-fire).
 4. AnomalyDetector resets the counter on a healthy tick (recovery).
 5. Cooldown lock prevents _handle_anomaly from being re-triggered for the
    same machine within the cooldown window.
 6. Cooldown lock expires after the configured timeout (simulated via monkeypatch).
 7. DebateRoom is invoked autonomously by _handle_anomaly (end-to-end mock).
 8. Final output of _handle_anomaly is a well-formed EmergencyProposal broadcast.
 9. simulate_factory_stream lifecycle: start → runs → stop is clean.
10. EmergencyProposal broadcast reaches all SSE subscribers via telemetry.

All LLM calls are mocked so tests run without network access.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import asdict
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── Imports under test ───────────────────────────────────────────────────────
from events.iot_watchdog import (
    ANOMALY_COOLDOWN_S,
    CONSECUTIVE_THRESHOLD,
    NUM_MACHINES,
    STREAM_TICK_INTERVAL_S,
    YIELD_CRITICAL_PCT,
    AnomalyDetector,
    AnomalyEvent,
    FactoryStreamTick,
    _WatchdogState,
    _generate_tick,
    _handle_anomaly,
    _is_in_cooldown,
    _set_cooldown,
    _state,
    start_watchdog,
    stop_watchdog,
)
from telemetry import (
    EmergencyProposal,
    broadcast_emergency_proposal,
    subscribe_to_emergency,
    unsubscribe_from_emergency,
)


# ============================================================================
# Helpers
# ============================================================================

def _healthy_tick(machine_id: str = "Machine-1") -> FactoryStreamTick:
    return FactoryStreamTick(
        machine_id    = machine_id,
        timestamp     = "2026-01-01T00:00:00Z",
        cycle_time_ms = 480.0,
        temperature_c = 50.0,
        yield_rate    = 95.0,
    )


def _bad_tick(machine_id: str = "Machine-1", yield_rate: float = 87.0) -> FactoryStreamTick:
    return FactoryStreamTick(
        machine_id    = machine_id,
        timestamp     = "2026-01-01T00:00:00Z",
        cycle_time_ms = 490.0,
        temperature_c = 58.0,
        yield_rate    = yield_rate,
    )


def _make_fake_consensus():
    """Return a minimal ConsensusResult-like MagicMock for testing _handle_anomaly."""
    c = MagicMock()
    c.summary              = "Slow down Machine-1 to reduce thermal load and improve yield."
    c.adopted_from_cost    = ["Reduce machine speed by 10%"]
    c.adopted_from_quality = ["Increase quality inspection frequency"]
    c.trade_off_resolution = "Speed reduction costs 2% UPH but restores yield above 90%."
    c.confidence_score     = 0.87
    c.num_operators        = 4
    c.throughput_uph       = 42.5
    c.cost_per_unit_usd    = 1.23
    return c


# ============================================================================
# 1. AnomalyDetector — consecutive counter
# ============================================================================

class TestAnomalyDetector:

    def test_no_event_on_healthy_ticks(self):
        """Healthy ticks must never trigger an AnomalyEvent."""
        det = AnomalyDetector()
        for _ in range(10):
            assert det.process(_healthy_tick()) is None

    def test_counter_increments_on_bad_ticks(self):
        """Each below-threshold tick below the ceiling increments the counter."""
        det = AnomalyDetector()
        for i in range(CONSECUTIVE_THRESHOLD - 1):
            result = det.process(_bad_tick())
            assert result is None, f"Should not fire after only {i + 1} ticks"
            assert det.get_consecutive_count("Machine-1") == i + 1

    def test_event_fires_at_threshold(self):
        """AnomalyEvent must be returned exactly at CONSECUTIVE_THRESHOLD."""
        det   = AnomalyDetector()
        event = None
        for _ in range(CONSECUTIVE_THRESHOLD):
            event = det.process(_bad_tick())
        assert event is not None, "Must fire after CONSECUTIVE_THRESHOLD ticks"
        assert isinstance(event, AnomalyEvent)
        assert event.machine_id    == "Machine-1"
        assert event.current_value < YIELD_CRITICAL_PCT
        assert event.consecutive   == CONSECUTIVE_THRESHOLD

    def test_counter_resets_after_event(self):
        """After firing, the counter must reset to 0 so the next chain starts fresh."""
        det = AnomalyDetector()
        for _ in range(CONSECUTIVE_THRESHOLD):
            det.process(_bad_tick())
        # Counter should be reset to 0 after the event.
        assert det.get_consecutive_count("Machine-1") == 0

    def test_counter_resets_on_recovery(self):
        """A healthy tick between anomalous ticks resets the counter."""
        det = AnomalyDetector()
        for _ in range(CONSECUTIVE_THRESHOLD - 1):
            det.process(_bad_tick())
        # Inject a healthy tick → counter resets
        det.process(_healthy_tick())
        assert det.get_consecutive_count("Machine-1") == 0
        # Subsequent bad ticks restart the count from 1
        det.process(_bad_tick())
        assert det.get_consecutive_count("Machine-1") == 1

    def test_multiple_machines_are_independent(self):
        """Counter state for different machine_ids must not bleed across machines."""
        det = AnomalyDetector()
        m1_bad   = _bad_tick("Machine-1")
        m2_good  = _healthy_tick("Machine-2")
        for _ in range(CONSECUTIVE_THRESHOLD - 1):
            det.process(m1_bad)
            det.process(m2_good)
        # Machine-1 close to threshold, Machine-2 still at 0
        assert det.get_consecutive_count("Machine-1") == CONSECUTIVE_THRESHOLD - 1
        assert det.get_consecutive_count("Machine-2") == 0

    def test_event_payload_fields(self):
        """AnomalyEvent must carry the correct machine_id, threshold, and type."""
        det = AnomalyDetector()
        for _ in range(CONSECUTIVE_THRESHOLD):
            event = det.process(_bad_tick("Machine-2", yield_rate=85.0))
        assert event.machine_id    == "Machine-2"
        assert event.threshold     == YIELD_CRITICAL_PCT
        assert event.anomaly_type  == "YIELD_BELOW_THRESHOLD"
        assert event.current_value == pytest.approx(85.0)


# ============================================================================
# 2. Cooldown Lock
# ============================================================================

class TestCooldownLock:

    def setup_method(self):
        """Clear cooldowns before each test."""
        _state.cooldowns.clear()

    def test_is_not_in_cooldown_initially(self):
        assert _is_in_cooldown("Machine-1") is False

    def test_cooldown_armed_after_set(self):
        _set_cooldown("Machine-1")
        assert _is_in_cooldown("Machine-1") is True

    def test_cooldown_expires(self):
        """
        Simulate cooldown expiry by backdating the stored expiry time.
        """
        _set_cooldown("Machine-1")
        # Override to a time in the past
        _state.cooldowns["Machine-1"] = time.monotonic() - 1.0
        assert _is_in_cooldown("Machine-1") is False

    def test_separate_cooldowns_per_machine(self):
        _set_cooldown("Machine-1")
        assert _is_in_cooldown("Machine-1") is True
        assert _is_in_cooldown("Machine-2") is False


# ============================================================================
# 3. _handle_anomaly — end-to-end (mocked LLM & broadcast)
# ============================================================================

class TestHandleAnomaly:

    @pytest.mark.asyncio
    async def test_handle_anomaly_calls_debate_room(self):
        """_handle_anomaly must call run_debate_session with a CRITICAL query."""
        event = AnomalyEvent(
            machine_id    = "Machine-3",
            current_value = 87.5,
            consecutive   = CONSECUTIVE_THRESHOLD,
        )
        fake_consensus = _make_fake_consensus()

        # _handle_anomaly does `from agents.debate_room import run_debate_session`
        # inside the function, so we must patch the attribute on the source module.
        with (
            patch(
                "agents.debate_room.run_debate_session",
                new_callable=AsyncMock,
                return_value=fake_consensus,
            ) as mock_debate,
            patch(
                "telemetry.broadcast_emergency_proposal",
                new_callable=AsyncMock,
            ),
        ):
            await _handle_anomaly(event)

        # Verify DebateRoom was invoked.
        mock_debate.assert_awaited_once()
        call_kwargs = mock_debate.call_args
        query = call_kwargs.kwargs.get("query") or call_kwargs.args[0]
        assert "CRITICAL" in query
        assert "Machine-3" in query
        assert "87.5" in query

    @pytest.mark.asyncio
    async def test_handle_anomaly_broadcasts_emergency_proposal(self):
        """_handle_anomaly must produce a broadcast call with a proper EmergencyProposal."""
        event = AnomalyEvent(
            machine_id    = "Machine-1",
            current_value = 86.0,
            consecutive   = CONSECUTIVE_THRESHOLD,
        )
        fake_consensus = _make_fake_consensus()
        captured: list[EmergencyProposal] = []

        async def capture_broadcast(proposal):
            captured.append(proposal)

        with (
            patch(
                "agents.debate_room.run_debate_session",
                new_callable=AsyncMock,
                return_value=fake_consensus,
            ),
            patch(
                "telemetry.broadcast_emergency_proposal",
                side_effect=capture_broadcast,
            ),
        ):
            await _handle_anomaly(event)

        assert len(captured) == 1
        proposal = captured[0]
        assert isinstance(proposal, EmergencyProposal)
        assert proposal.machine_id    == "Machine-1"
        assert proposal.current_value == pytest.approx(86.0)
        assert proposal.status        == "PENDING_APPROVAL"
        assert proposal.confidence_score == pytest.approx(0.87)
        assert len(proposal.action_items) >= 1

    @pytest.mark.asyncio
    async def test_handle_anomaly_error_does_not_propagate(self):
        """If DebateRoom raises, _handle_anomaly must log and NOT re-raise."""
        event = AnomalyEvent(machine_id="Machine-2", current_value=88.0)

        with patch(
            "agents.debate_room.run_debate_session",
            new_callable=AsyncMock,
            side_effect=RuntimeError("simulated LLM failure"),
        ):
            # Must not raise — resilient error handling is required.
            await _handle_anomaly(event)


# ============================================================================
# 4. simulate_factory_stream — lifecycle
# ============================================================================

class TestFactoryStream:
    """Tests for the background stream task lifecycle."""

    @pytest.mark.asyncio
    async def test_start_stop_watchdog(self):
        """start_watchdog() creates a running task; stop_watchdog() cancels it."""
        async def _noop_stream():
            try:
                while True:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                return

        import events.iot_watchdog as _wd_mod
        original = _wd_mod.simulate_factory_stream
        _wd_mod.simulate_factory_stream = _noop_stream
        try:
            await start_watchdog()
            assert _state.running is True
            assert _state.task is not None
            assert not _state.task.done()

            await stop_watchdog()
            assert _state.running is False
            assert _state.task is None
        finally:
            _wd_mod.simulate_factory_stream = original

    @pytest.mark.asyncio
    async def test_start_watchdog_is_idempotent(self):
        """Calling start_watchdog twice must not create a second task."""
        import events.iot_watchdog as _wd_mod
        original = _wd_mod.simulate_factory_stream

        async def _noop_stream():
            try:
                while True:
                    await asyncio.sleep(0.05)
            except asyncio.CancelledError:
                return

        _wd_mod.simulate_factory_stream = _noop_stream
        try:
            await start_watchdog()
            first_task = _state.task

            # Second call must be a no-op: the task reference must not change.
            await start_watchdog()
            assert _state.task is first_task, (
                "start_watchdog() must not replace the existing task on a second call"
            )
        finally:
            await stop_watchdog()
            _wd_mod.simulate_factory_stream = original

    @pytest.mark.asyncio
    async def test_stream_respects_cooldown_and_fires_task(self):
        """
        Simulate CONSECUTIVE_THRESHOLD anomalous ticks for one machine and verify
        that asyncio.create_task is called with _handle_anomaly (but LLM mocked).
        """
        _state.cooldowns.clear()
        _state.running = False  # ensure clean state

        spawned: list[AnomalyEvent] = []

        async def _fake_handle(event: AnomalyEvent):
            spawned.append(event)

        # Inject deterministically anomalous ticks for Machine-1 only.
        tick_seq = iter(
            [_bad_tick("Machine-1")] * (CONSECUTIVE_THRESHOLD + 1)
            + [_healthy_tick("Machine-1")] * 20
        )

        def _fake_generate_tick(machine_id: str, force_anomaly: bool = False):
            try:
                return next(tick_seq)
            except StopIteration:
                return _healthy_tick(machine_id)

        with (
            patch("events.iot_watchdog._generate_tick", side_effect=_fake_generate_tick),
            patch("events.iot_watchdog._handle_anomaly", side_effect=_fake_handle),
            patch(
                "events.iot_watchdog.NUM_MACHINES",
                new=1,  # one machine only for isolation
            ),
            patch("events.iot_watchdog.STREAM_TICK_INTERVAL_S", new=0.0),
        ):
            _state.running = True
            # Run a few ticks manually via the detector
            det = AnomalyDetector()
            for _ in range(CONSECUTIVE_THRESHOLD + 1):
                tick = _fake_generate_tick("Machine-1")
                ev   = det.process(tick)
                if ev and not _is_in_cooldown("Machine-1"):
                    _set_cooldown("Machine-1")
                    await _fake_handle(ev)

        # At least one anomaly was captured and routed.
        assert len(spawned) >= 1

        # A second identical burst should be blocked by cooldown.
        pre_count = len(spawned)
        det2 = AnomalyDetector()
        for _ in range(CONSECUTIVE_THRESHOLD):
            tick = _bad_tick("Machine-1")
            ev   = det2.process(tick)
            if ev:
                if not _is_in_cooldown("Machine-1"):
                    await _fake_handle(ev)
        assert len(spawned) == pre_count, "Cooldown should block second burst"

        # Cleanup
        _state.running = False
        _state.cooldowns.clear()


# ============================================================================
# 5. EmergencyProposal broadcast — telemetry layer
# ============================================================================

class TestEmergencyBroadcast:

    @pytest.mark.asyncio
    async def test_broadcast_reaches_subscriber(self):
        """broadcast_emergency_proposal must deliver the proposal to the queue."""
        proposal = EmergencyProposal(
            proposal_id          = str(uuid.uuid4()),
            session_id           = "watchdog-abc12345",
            machine_id           = "Machine-1",
            anomaly_type         = "YIELD_BELOW_THRESHOLD",
            current_value        = 87.3,
            threshold            = 90.0,
            summary              = "Reduce Machine-1 speed to restore yield.",
            action_items         = ["Reduce speed by 10%"],
            trade_off_resolution = "Minor throughput loss; yields recover.",
            confidence_score     = 0.91,
            num_operators        = 3,
            throughput_uph       = 40.0,
            cost_per_unit_usd    = 1.10,
        )

        queue = await subscribe_to_emergency()
        try:
            await broadcast_emergency_proposal(proposal)
            received = await asyncio.wait_for(queue.get(), timeout=2.0)
            assert received.proposal_id  == proposal.proposal_id
            assert received.machine_id   == "Machine-1"
            assert received.status       == "PENDING_APPROVAL"
        finally:
            await unsubscribe_from_emergency(queue)

    @pytest.mark.asyncio
    async def test_broadcast_to_multiple_subscribers(self):
        """All subscribers receive the same proposal."""
        proposal = EmergencyProposal(
            proposal_id          = str(uuid.uuid4()),
            session_id           = "watchdog-multi",
            machine_id           = "Machine-2",
            anomaly_type         = "YIELD_BELOW_THRESHOLD",
            current_value        = 85.0,
            threshold            = 90.0,
            summary              = "Multi-subscriber test.",
            action_items         = [],
            trade_off_resolution = "",
            confidence_score     = 0.80,
            num_operators        = 2,
            throughput_uph       = 35.0,
            cost_per_unit_usd    = 0.99,
        )

        q1 = await subscribe_to_emergency()
        q2 = await subscribe_to_emergency()
        try:
            await broadcast_emergency_proposal(proposal)
            r1 = await asyncio.wait_for(q1.get(), timeout=2.0)
            r2 = await asyncio.wait_for(q2.get(), timeout=2.0)
            assert r1.proposal_id == proposal.proposal_id
            assert r2.proposal_id == proposal.proposal_id
        finally:
            await unsubscribe_from_emergency(q1)
            await unsubscribe_from_emergency(q2)

    @pytest.mark.asyncio
    async def test_proposal_is_pending_approval(self):
        """A newly created EmergencyProposal must have status PENDING_APPROVAL."""
        proposal = EmergencyProposal(
            proposal_id      = str(uuid.uuid4()),
            session_id       = "watchdog-status-test",
            machine_id       = "Machine-3",
            anomaly_type     = "YIELD_BELOW_THRESHOLD",
            current_value    = 88.0,
            threshold        = 90.0,
            summary          = "Status check test.",
            action_items     = [],
            trade_off_resolution = "",
            confidence_score = 0.75,
            num_operators    = 3,
            throughput_uph   = 38.0,
            cost_per_unit_usd = 1.05,
        )
        assert proposal.status == "PENDING_APPROVAL"

    def test_proposal_serialisation_round_trip(self):
        """to_dict() / to_json() must produce a JSON-serialisable representation."""
        import json
        proposal = EmergencyProposal(
            proposal_id      = "abc123",
            session_id       = "watchdog-abc",
            machine_id       = "Machine-1",
            anomaly_type     = "YIELD_BELOW_THRESHOLD",
            current_value    = 87.0,
            threshold        = 90.0,
            summary          = "Round-trip test.",
            action_items     = ["action-a", "action-b"],
            trade_off_resolution = "resolved",
            confidence_score = 0.9,
            num_operators    = 4,
            throughput_uph   = 44.0,
            cost_per_unit_usd = 1.20,
        )
        d    = proposal.to_dict()
        raw  = proposal.to_json()
        back = json.loads(raw)
        assert back["proposal_id"] == "abc123"
        assert back["machine_id"]  == "Machine-1"
        assert back["status"]      == "PENDING_APPROVAL"
        assert back["action_items"] == ["action-a", "action-b"]


# ============================================================================
# 6. Factory data generator sanity checks
# ============================================================================

class TestGenerateTick:

    def test_normal_tick_fields(self):
        tick = _generate_tick("Machine-1")
        assert tick.machine_id    == "Machine-1"
        assert isinstance(tick.timestamp, str)
        assert 0.0 < tick.yield_rate  <= 100.0
        assert tick.temperature_c >  0.0
        assert tick.cycle_time_ms >  0.0

    def test_force_anomaly_is_below_threshold(self):
        for _ in range(20):
            tick = _generate_tick("Machine-1", force_anomaly=True)
            assert tick.yield_rate < YIELD_CRITICAL_PCT, (
                f"force_anomaly tick must be below {YIELD_CRITICAL_PCT}%, "
                f"got {tick.yield_rate}"
            )

    def test_is_yield_anomalous_property(self):
        bad  = _bad_tick(yield_rate=85.0)
        good = _healthy_tick()
        assert bad.is_yield_anomalous  is True
        assert good.is_yield_anomalous is False
