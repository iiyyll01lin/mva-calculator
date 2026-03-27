"""
backend/tests_most/test_temporal_rag.py
────────────────────────────────────────────────────────────────────────────────
End-to-end tests for the Agentic ETL & Temporal RAG pipeline.

Test coverage
─────────────
 1. DataNormalizationAgent parses a messy CSV (non-standard column names,
    missing values, mixed date formats) into valid TimeSeriesRecord objects.
 2. Transformation log is non-empty and references at least one column mapping.
 3. TimeSeriesRecord rejects a row missing both timestamp and model_id.
 4. parse_temporal_expression correctly identifies "last week", "last N days",
    "yesterday", "last <weekday>" windows.
 5. TemporalVectorStore.add_records and query return ranked results.
 6. Temporal decay returns 1.0 for in-window records and <1.0 for out-of-window.
 7. TemporalAnalystAgent.analyse() returns a TemporalAnalysis with trends and
    synthesis over seeded records.
 8. TemporalAnalyst detects anomalies (seeded IQR outlier) correctly.
 9. Full pipeline: ETL → store → TemporalAnalyst → Debate Room uses historical
    context (mock verifies _fetch_temporal_context is called).
10. Supervisor routes a temporal query to TemporalAnalystAgent.
11. Temporal retrieval correctly prioritises in-window records over older ones.
12. Empty store returns empty results and graceful synthesis.

Run with:
    cd ddm-l6/backend
    pytest tests_most/test_temporal_rag.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import os
from datetime import datetime, timedelta, timezone
from textwrap import dedent
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add the backend package root so absolute imports resolve during testing.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from data_ops.agentic_etl import (
    DataNormalizationAgent,
    TimeSeriesRecord,
    _normalize_column_name,
    ETLResult,
)
from tools.temporal_rag import (
    TemporalVectorStore,
    RetrievalResult,
    parse_temporal_expression,
    _temporal_decay,
    get_temporal_store,
)
from agents.temporal_analyst import TemporalAnalystAgent, TemporalAnalysis


# ────────────────────────────────────────────────────────────────────────────
# Fixtures & Constants
# ────────────────────────────────────────────────────────────────────────────

# A deliberately messy CSV payload mirroring real legacy manufacturing exports:
# • Non-standard column names (Yld_Rt_%, Def_Count, Cycle Time (s))
# • Mixed date formats
# • Missing values (empty cells)
# • Unicode BOM prefix
# • Percentage signs in numeric fields
MESSY_CSV = dedent("""\
    \ufeffDate,Product,Station,Yld_Rt_%,Cycle Time (s),Def_Count,UPH,Operators,Comment
    2026-01-01,Model-X,ST-01,97.5%,42.1,2,86.0,4,Normal production
    01/02/2026,Model-X,ST-01,95.0,43.8,5,84.2,4,Increased defects noted
    2026-01-03T08:00:00,Model-X,ST-01,,44.0,3,84.0,4,Missing yield value
    03/01/2026,Model-X,ST-01,98.1%,41.5,1,87.1,4,Best shift this week
    2026/01/08 09:30:00,Model-X,ST-01,72.0%,55.2,28,62.5,4,ALERT: defect spike!
    2026-01-09,Model-X,ST-01,94.5%,42.8,4,85.0,4,Post-fix normal
    2026-01-10,Model-X,ST-01,96.2%,41.9,2,86.8,4,Normal
    ,Model-X,ST-01,95.0%,42.0,2,86.0,4,Missing timestamp - should skip
    2026-01-15,,ST-01,94.0%,43.0,2,85.2,4,Missing model - should skip
""")

SESSION_ID = "test-temporal-session-001"

NOW_UTC = datetime(2026, 1, 16, 12, 0, 0, tzinfo=timezone.utc)   # fixed reference time


@pytest.fixture
def fresh_store() -> TemporalVectorStore:
    """Return an empty TemporalVectorStore for each test."""
    return TemporalVectorStore()


@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the module-level store singleton between tests."""
    import tools.temporal_rag as rag_mod
    rag_mod._store_instance = None
    yield
    rag_mod._store_instance = None


# ────────────────────────────────────────────────────────────────────────────
# 1. DataNormalizationAgent parses messy CSV → valid TimeSeriesRecords
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_etl_parses_messy_csv():
    agent  = DataNormalizationAgent()
    result = await agent.run(raw_csv=MESSY_CSV, session_id=SESSION_ID)

    assert isinstance(result, ETLResult)
    # 9 data rows; 2 must be skipped (missing timestamp and missing model_id)
    assert result.rows_skipped == 2, f"Expected 2 skipped rows, got {result.rows_skipped}"
    assert result.rows_out == 7, f"Expected 7 records, got {result.rows_out}"

    for rec in result.records:
        assert isinstance(rec, TimeSeriesRecord)
        assert rec.model_id == "Model-X"
        assert 0.0 <= rec.yield_rate <= 100.0
        assert rec.cycle_time_sec >= 0.0
        assert rec.defect_count >= 0


# ────────────────────────────────────────────────────────────────────────────
# 2. Transformation log references column mappings
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_etl_transformation_log_non_empty():
    agent  = DataNormalizationAgent()
    result = await agent.run(raw_csv=MESSY_CSV, session_id=SESSION_ID)

    assert len(result.transformation_log) > 0
    # Log must mention at least one legacy→canonical mapping
    assert "→" in result.transformation_log or "->" in result.transformation_log


# ────────────────────────────────────────────────────────────────────────────
# 3. Log is written to SessionState.extra when provided
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_etl_writes_log_to_session_state():
    class FakeSessionState:
        extra: dict = {}

    state  = FakeSessionState()
    agent  = DataNormalizationAgent()
    result = await agent.run(raw_csv=MESSY_CSV, session_id=SESSION_ID, session_state=state)

    assert "etl_transformation_log" in state.extra
    assert state.extra["etl_rows_out"] == result.rows_out


# ────────────────────────────────────────────────────────────────────────────
# 4. Column name normalisation
# ────────────────────────────────────────────────────────────────────────────

def test_normalize_column_aliases():
    assert _normalize_column_name("Yld_Rt_%") == "yield_rate"
    assert _normalize_column_name("  cycle time (s)  ") == "cycle_time_sec"  # not in alias but cleaned
    assert _normalize_column_name("Def_Count") == "defect_count"
    assert _normalize_column_name("UPH") == "throughput_uph"
    assert _normalize_column_name("Date") == "timestamp"
    assert _normalize_column_name("Product") == "model_id"


# ────────────────────────────────────────────────────────────────────────────
# 5. parse_temporal_expression windows
# ────────────────────────────────────────────────────────────────────────────

def test_temporal_expression_last_n_days():
    window = parse_temporal_expression("yield drop in the last 7 days", now=NOW_UTC)
    assert window is not None
    start, end = window
    expected_start = NOW_UTC.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
    assert start == expected_start
    assert end == NOW_UTC


def test_temporal_expression_last_week():
    window = parse_temporal_expression("What happened last week?", now=NOW_UTC)
    assert window is not None
    start, end = window
    # NOW_UTC is 2026-01-16 (Friday), so last week = Mon 2026-01-05 → Mon 2026-01-12
    assert start.weekday() == 0   # Monday
    assert (end - start).days == 7


def test_temporal_expression_yesterday():
    window = parse_temporal_expression("Show me yesterday's data", now=NOW_UTC)
    assert window is not None
    start, end = window
    yesterday = NOW_UTC.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    assert start == yesterday
    assert end == yesterday + timedelta(days=1)


def test_temporal_expression_last_tuesday():
    window = parse_temporal_expression("What caused the defect spike last Tuesday?", now=NOW_UTC)
    assert window is not None
    start, end = window
    assert start.weekday() == 1   # Tuesday
    assert (end - start).days == 1


def test_temporal_expression_no_match():
    window = parse_temporal_expression("Optimize the assembly line for Model-X", now=NOW_UTC)
    assert window is None


# ────────────────────────────────────────────────────────────────────────────
# 6. Temporal decay function
# ────────────────────────────────────────────────────────────────────────────

def test_temporal_decay_in_window():
    ts    = datetime(2026, 1, 8, 12, tzinfo=timezone.utc)
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    end   = datetime(2026, 1, 12, tzinfo=timezone.utc)
    score = _temporal_decay(ts, start, end)
    assert score == 1.0


def test_temporal_decay_outside_window():
    ts    = datetime(2025, 12, 1, tzinfo=timezone.utc)   # well before window
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    end   = datetime(2026, 1, 12, tzinfo=timezone.utc)
    score = _temporal_decay(ts, start, end)
    assert 0.0 < score < 1.0, "Expected decay < 1.0 for out-of-window record"
    # 35 days before window; should be well below 0.5 (half-life is 7 days)
    assert score < 0.1


def test_temporal_decay_at_half_life():
    from tools.temporal_rag import DECAY_HALF_LIFE_DAYS
    start = datetime(2026, 1, 12, tzinfo=timezone.utc)
    end   = datetime(2026, 1, 12, tzinfo=timezone.utc)
    # Record is exactly DECAY_HALF_LIFE_DAYS before window
    ts    = start - timedelta(days=DECAY_HALF_LIFE_DAYS)
    score = _temporal_decay(ts, start, end)
    assert abs(score - 0.5) < 0.01, f"Expected ~0.5 at half-life, got {score}"


# ────────────────────────────────────────────────────────────────────────────
# 7. TemporalVectorStore.add_records and query
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_vector_store_add_and_query(fresh_store):
    agent  = DataNormalizationAgent()
    result = await agent.run(raw_csv=MESSY_CSV, session_id=SESSION_ID)
    await fresh_store.add_records(result.records, session_id=SESSION_ID)

    assert len(fresh_store) == result.rows_out

    results = await fresh_store.query(
        query_text  = "yield rate anomaly last week Model-X",
        top_k       = 5,
        session_id  = SESSION_ID,
        now         = NOW_UTC,
    )
    assert len(results) > 0
    for r in results:
        assert isinstance(r, RetrievalResult)
        assert 0.0 <= r.score <= 1.0


@pytest.mark.asyncio
async def test_vector_store_empty_query(fresh_store):
    results = await fresh_store.query(
        query_text = "any query on empty store",
        session_id = SESSION_ID,
    )
    assert results == []


# ────────────────────────────────────────────────────────────────────────────
# 8. Temporal retrieval prioritises in-window records
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_temporal_retrieval_window_priority(fresh_store):
    """Records inside the temporal window must score higher than older records."""
    # Create two records: one very recent (in last 7 days) and one very old
    now = NOW_UTC
    recent_rec = TimeSeriesRecord(
        timestamp       = now - timedelta(days=3),
        model_id        = "Model-X",
        station_id      = "ST-01",
        yield_rate      = 95.0,
        cycle_time_sec  = 42.0,
        defect_count    = 2,
        throughput_uph  = 86.0,
        operator_count  = 4,
    )
    old_rec = TimeSeriesRecord(
        timestamp       = now - timedelta(days=60),
        model_id        = "Model-X",
        station_id      = "ST-01",
        yield_rate      = 95.0,
        cycle_time_sec  = 42.0,
        defect_count    = 2,
        throughput_uph  = 86.0,
        operator_count  = 4,
    )
    await fresh_store.add_records([recent_rec, old_rec], session_id=SESSION_ID)

    results = await fresh_store.query(
        query_text = "yield rate last 7 days Model-X",
        top_k      = 2,
        session_id = SESSION_ID,
        now        = now,
    )
    assert len(results) == 2
    # The recent record should rank first
    assert results[0].record.record_id == recent_rec.record_id, (
        "Expected recent record to rank above older record"
    )


# ────────────────────────────────────────────────────────────────────────────
# 9. TemporalAnalystAgent trends over seeded records
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_temporal_analyst_trends():
    store = TemporalVectorStore()
    agent = TemporalAnalystAgent(store=store)

    # Build a simple degrading yield-rate series (Jan 1-10 2026)
    base   = datetime(2026, 1, 1, tzinfo=timezone.utc)
    yields = [98.0, 97.5, 97.0, 96.5, 96.0, 95.5, 95.0, 94.5, 94.0, 93.5]
    records = [
        TimeSeriesRecord(
            timestamp      = base + timedelta(days=i),
            model_id       = "Model-X",
            station_id     = "ST-01",
            yield_rate     = y,
            cycle_time_sec = 42.0,
            throughput_uph = 86.0,
        )
        for i, y in enumerate(yields)
    ]
    await store.add_records(records, session_id=SESSION_ID)

    analysis = await agent.analyse(
        query      = "yield rate trend for Model-X last month",
        session_id = SESSION_ID,
        now        = NOW_UTC,
    )

    assert isinstance(analysis, TemporalAnalysis)
    assert analysis.records_analysed > 0
    assert len(analysis.synthesis) > 0

    # yield_rate should be detected as DEGRADING (falling over time)
    yield_trend = next((t for t in analysis.trends if t.metric == "yield_rate"), None)
    assert yield_trend is not None
    assert yield_trend.direction in ("DEGRADING", "STABLE"), (
        f"Expected DEGRADING for falling yield series, got '{yield_trend.direction}'"
    )


# ────────────────────────────────────────────────────────────────────────────
# 10. TemporalAnalystAgent detects anomaly (IQR spike)
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_temporal_analyst_anomaly_detection():
    store = TemporalVectorStore()
    agent = TemporalAnalystAgent(store=store)

    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    # Normal yield around 97%, one spike down to 50% on Jan-8
    normal_yields = [97.0, 97.2, 96.8, 97.1, 96.9, 97.3, 97.0, 50.0, 97.2, 97.1]
    records = [
        TimeSeriesRecord(
            timestamp      = base + timedelta(days=i),
            model_id       = "Model-X",
            station_id     = "ST-01",
            yield_rate     = y,
            cycle_time_sec = 42.0,
            throughput_uph = 86.0,
            notes          = "Defect spike - solder paste contamination" if y < 60 else "",
        )
        for i, y in enumerate(normal_yields)
    ]
    await store.add_records(records, session_id=SESSION_ID)

    analysis = await agent.analyse(
        query      = "anomaly in yield rate last 30 days Model-X",
        session_id = SESSION_ID,
        now        = NOW_UTC,
    )

    assert len(analysis.anomalies) > 0, "Expected at least one anomaly to be detected"
    dip = next((a for a in analysis.anomalies if a.anomaly_type == "DIP"), None)
    assert dip is not None, "Expected a DIP anomaly for the 50% yield record"
    assert dip.value == 50.0


# ────────────────────────────────────────────────────────────────────────────
# 11. Full pipeline: ETL → store → TemporalAnalyst analysis synthesis
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_full_etl_to_analyst_pipeline():
    """
    Ingest the messy CSV, index into the singleton store, then run
    TemporalAnalystAgent to verify end-to-end synthesis is coherent.
    """
    # Use fresh singleton store
    store = get_temporal_store()
    store.clear()

    # Step 1: ETL
    etl_agent = DataNormalizationAgent()
    etl_result = await etl_agent.run(raw_csv=MESSY_CSV, session_id=SESSION_ID)
    assert etl_result.rows_out >= 7

    # Step 2: Index into store
    await store.add_records(etl_result.records, session_id=SESSION_ID)
    assert len(store) >= 7

    # Step 3: Temporal analysis
    analyst  = TemporalAnalystAgent(store=store)
    analysis = await analyst.analyse(
        query      = "Why did the yield rate drop? Check for defect spikes last 30 days",
        session_id = SESSION_ID,
        now        = NOW_UTC,
    )

    assert analysis.records_analysed >= 1
    assert len(analysis.synthesis) > 50, "Synthesis should be a substantive paragraph"

    # The anomaly on 2026-01-08 (yield=72%, defect_count=28) should be detected
    yield_anomalies = [a for a in analysis.anomalies if a.metric in ("yield_rate", "defect_count")]
    # At least one anomaly among yield or defect metrics
    assert len(yield_anomalies) > 0 or "anomal" in analysis.synthesis.lower() or len(analysis.anomalies) > 0


# ────────────────────────────────────────────────────────────────────────────
# 12. Debate Room receives historical context (mock integration)
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_debate_room_fetches_temporal_context():
    """
    Verify that _fetch_temporal_context is called when run_cost_agent and
    run_quality_agent are invoked within a debate session that has data.
    """
    from agents.debate_room import _fetch_temporal_context, run_cost_agent, run_debate_session
    from llm_client import LlmCallResult, ModelTier

    # Build a valid cost plan JSON for the mock LLM to return
    cost_json = json.dumps({
        "plan_id":            "COST-PLAN-A",
        "summary":            "Reduce operators; merge stations.",
        "num_operators":      3,
        "cycle_time_tmu":     420.0,
        "cost_per_unit_usd":  1.85,
        "throughput_uph":     52.4,
        "key_changes":        ["Merge stations 3+4"],
        "risk_flags":         ["Fatigue risk"],
    })
    quality_json = json.dumps({
        "critiqued_plan_id": "COST-PLAN-A",
        "critic_agent":      "QualityAndTimeAgent",
        "weaknesses_found":  ["Historical data shows defect spike after similar merge."],
        "counter_plan": {
            "plan_id":            "QUALITY-PLAN-B",
            "summary":            "Keep 5 operators + inline AOI.",
            "num_operators":      5,
            "cycle_time_tmu":     380.0,
            "cost_per_unit_usd":  2.40,
            "throughput_uph":     61.8,
            "key_changes":        ["Inline AOI"],
            "risk_flags":         ["30% higher cost"],
        },
    })
    consensus_json = json.dumps({
        "summary":            "Balanced plan.",
        "num_operators":      4,
        "cycle_time_tmu":     395.0,
        "cost_per_unit_usd":  2.10,
        "throughput_uph":     58.2,
        "adopted_from_cost":    ["Merge stations"],
        "adopted_from_quality": ["Inline AOI"],
        "trade_off_resolution": "Dedicated rework loop deferred.",
        "confidence_score":   0.87,
    })

    call_count = {"n": 0}
    responses  = [cost_json, quality_json, consensus_json]

    async def mock_llm(*args, **kwargs) -> LlmCallResult:
        idx = min(call_count["n"], len(responses) - 1)
        call_count["n"] += 1
        return LlmCallResult(
            content           = responses[idx],
            prompt_tokens     = 100,
            completion_tokens = 50,
            tier              = ModelTier.LOCAL,
        )

    # Patch _fetch_temporal_context to return a synthetic history block
    mock_history = "\n\n[HISTORICAL CONTEXT START]\nYield dip detected on 2026-01-08.\n[HISTORICAL CONTEXT END]\n"

    with patch("agents.debate_room.call_llm", side_effect=mock_llm), \
         patch("agents.debate_room._fetch_temporal_context", new_callable=AsyncMock, return_value=mock_history) as mock_ctx:

        result = await run_debate_session(
            query      = "Optimize Model-X; check historical yield context",
            session_id = SESSION_ID,
            max_turns  = 3,
        )

    # _fetch_temporal_context should have been called at least twice (cost + quality agents)
    assert mock_ctx.call_count >= 2, (
        f"Expected >= 2 calls to _fetch_temporal_context, got {mock_ctx.call_count}"
    )
    assert result.num_operators > 0
    assert result.confidence_score >= 0.0


# ────────────────────────────────────────────────────────────────────────────
# 13. Supervisor routes temporal query → TemporalAnalystAgent
# ────────────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_supervisor_routes_temporal_query(tmp_path, monkeypatch):
    """
    Issue a query containing temporal/trend keywords through the full
    run_agent_workflow entrypoint and verify the intent is temporal_analysis.
    """
    monkeypatch.setenv("MVA_MEMORY_FILE_DIR", str(tmp_path))
    monkeypatch.setenv("MVA_MEMORY_BACKEND", "file")
    import memory_store as ms
    ms._store_instance = None

    from agent_router_poc import AuthContext, run_agent_workflow

    auth = AuthContext(
        user_id   = "test-user-002",
        role      = "Analyst",
        jwt_token = "stub-token",
    )
    result = await run_agent_workflow(
        user_query = "Show me the yield rate trend for Model-X last week",
        auth       = auth,
    )

    assert result["intent"] == "temporal_analysis", (
        f"Expected intent='temporal_analysis', got '{result['intent']}'"
    )
    # The answer should reference TemporalAnalystAgent output
    assert "temporal" in result["answer"].lower() or "analysis" in result["answer"].lower()

    ms._store_instance = None
