"""
backend/agents/temporal_analyst.py
────────────────────────────────────────────────────────────────────────────────
Temporal Analyst Sub-Agent — Enterprise MVA Platform v2.0.0

Receives time-series data from the TemporalVectorStore, performs multi-horizon
Temporal Reasoning, and returns a structured ``TemporalAnalysis`` report that
other agents (DebateRoom participants, Supervisor) can cite as evidence.

Capabilities
────────────
• **Trend detection**: linear regression over yield_rate / cycle_time_sec /
  throughput_uph time-series to classify IMPROVING / DEGRADING / STABLE.
• **Anomaly detection**: IQR-based spike/dip detection on numeric metrics.
• **Event correlation**: flags temporal co-occurrence of anomalies with
  configuration-change events embedded in ``notes`` fields.
• **Natural-language synthesis**: converts the numeric findings into a
  concise, citation-rich paragraph the LLM agents can quote directly.

Design principles
─────────────────
• Fully async — scoring logic offloaded to executor.
• Pydantic v2 output schema so the DebateRoom can validate the context block.
• Every invocation emits ``span_type="temporal_analysis"`` for dashboard visibility.
• Pure-Python statistics — no external numpy/scipy dependency for the PoC.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from tools.temporal_rag import RetrievalResult, TemporalVectorStore, get_temporal_store
from telemetry import LlmUsage, agent_span, estimate_tokens

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Output Schemas
# ────────────────────────────────────────────────────────────────────────────

TrendDirection = Literal["IMPROVING", "DEGRADING", "STABLE", "INSUFFICIENT_DATA"]


class MetricTrend(BaseModel):
    """Trend analysis result for a single numeric metric."""

    metric:     str             = Field(..., description="Metric name, e.g. 'yield_rate'.")
    direction:  TrendDirection  = Field(..., description="Overall direction of the trend.")
    slope:      float           = Field(default=0.0,
                                        description="Least-squares slope (units/day).")
    min_value:  float           = Field(default=0.0)
    max_value:  float           = Field(default=0.0)
    mean_value: float           = Field(default=0.0)
    n_points:   int             = Field(default=0, description="Number of data points used.")


class AnomalyRecord(BaseModel):
    """A detected anomaly (spike or dip) in a time-series metric."""

    anomaly_id:  str      = Field(default_factory=lambda: f"ANO-{uuid.uuid4().hex[:8].upper()}")
    metric:      str
    value:       float
    timestamp:   datetime
    z_score:     float    = Field(description="Deviation in units of IQR from median.")
    anomaly_type: Literal["SPIKE", "DIP"]
    model_id:    str      = ""
    station_id:  str      = ""
    context_note: str     = ""


class TemporalAnalysis(BaseModel):
    """
    Structured Temporal Reasoning report produced by TemporalAnalystAgent.

    Consumed by DebateRoom agents to ground their arguments in verified
    historical evidence.
    """

    analysis_id:   str  = Field(default_factory=lambda: f"TA-{uuid.uuid4().hex[:8].upper()}")
    query:         str  = Field(..., description="The original natural-language query.")
    session_id:    str  = Field(default="")
    model_filter:  Optional[str] = None

    records_analysed: int = 0
    time_window_desc: str = ""

    trends:     List[MetricTrend]   = Field(default_factory=list)
    anomalies:  List[AnomalyRecord] = Field(default_factory=list)

    # Human-readable synthesis paragraph for direct LLM injection
    synthesis:  str = Field(default="",
                             description="Narrative summary of findings.")

    analysed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    model_config = {"arbitrary_types_allowed": True}


# ────────────────────────────────────────────────────────────────────────────
# Pure-Python Statistics Helpers
# ────────────────────────────────────────────────────────────────────────────

def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _variance(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return sum((v - m) ** 2 for v in values) / (len(values) - 1)


def _stdev(values: List[float]) -> float:
    return math.sqrt(_variance(values))


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _iqr(values: List[float]) -> float:
    """Interquartile range."""
    if len(values) < 4:
        return 0.0
    s = sorted(values)
    n = len(s)
    q1 = _median(s[:n // 2])
    q3 = _median(s[(n + 1) // 2:])
    return q3 - q1


def _linear_regression_slope(
    xs: List[float], ys: List[float]
) -> float:
    """Ordinary least-squares slope (Δy / Δx) for parallel lists."""
    n = len(xs)
    if n < 2:
        return 0.0
    x_mean = _mean(xs)
    y_mean = _mean(ys)
    numerator   = sum((xs[i] - x_mean) * (ys[i] - y_mean) for i in range(n))
    denominator = sum((xs[i] - x_mean) ** 2 for i in range(n))
    if denominator == 0:
        return 0.0
    return numerator / denominator


# ────────────────────────────────────────────────────────────────────────────
# Analysis Engine (sync, runs in executor)
# ────────────────────────────────────────────────────────────────────────────

def _analyse_metric(
    metric_name: str,
    values:      List[float],
    timestamps:  List[datetime],
    positive_is_good: bool,
) -> MetricTrend:
    """
    Run trend analysis on a single numeric metric.

    Args:
        positive_is_good: True for yield_rate / throughput (higher = better),
                          False for cycle_time_sec / defect_count (lower = better).
    """
    n = len(values)
    if n < 2:
        return MetricTrend(
            metric    = metric_name,
            direction = "INSUFFICIENT_DATA",
            n_points  = n,
            mean_value = _mean(values) if values else 0.0,
            min_value  = min(values) if values else 0.0,
            max_value  = max(values) if values else 0.0,
        )

    # Convert timestamps to days-since-epoch for regression
    epoch = timestamps[0]
    xs    = [(ts - epoch).total_seconds() / 86_400 for ts in timestamps]
    slope = _linear_regression_slope(xs, values)

    # Classify: slope is meaningful only if > 0.5% of mean per day
    mean_val = _mean(values)
    threshold = 0.005 * abs(mean_val) if mean_val else 0.01

    if abs(slope) < threshold:
        direction: TrendDirection = "STABLE"
    elif slope > 0:
        direction = "IMPROVING" if positive_is_good else "DEGRADING"
    else:
        direction = "DEGRADING" if positive_is_good else "IMPROVING"

    return MetricTrend(
        metric     = metric_name,
        direction  = direction,
        slope      = round(slope, 6),
        min_value  = round(min(values), 4),
        max_value  = round(max(values), 4),
        mean_value = round(mean_val, 4),
        n_points   = n,
    )


def _detect_anomalies(
    metric_name:  str,
    values:       List[float],
    timestamps:   List[datetime],
    records:      List[Any],          # TimeSeriesRecord
    iqr_threshold: float = 2.5,
) -> List[AnomalyRecord]:
    """IQR-based anomaly detection on a numeric metric."""
    anomalies: List[AnomalyRecord] = []
    if len(values) < 4:
        return anomalies

    med       = _median(values)
    iqr_range = _iqr(values)
    if iqr_range == 0:
        return anomalies

    for i, (val, ts, rec) in enumerate(zip(values, timestamps, records)):
        deviation = (val - med) / iqr_range
        if abs(deviation) >= iqr_threshold:
            atype: Literal["SPIKE", "DIP"] = "SPIKE" if deviation > 0 else "DIP"
            anomalies.append(AnomalyRecord(
                metric       = metric_name,
                value        = round(val, 4),
                timestamp    = ts,
                z_score      = round(deviation, 3),
                anomaly_type = atype,
                model_id     = rec.model_id,
                station_id   = rec.station_id,
                context_note = rec.notes[:200] if rec.notes else "",
            ))
    return anomalies


METRIC_CONFIG: Dict[str, bool] = {
    # metric_name: positive_is_good
    "yield_rate":     True,
    "throughput_uph": True,
    "cycle_time_sec": False,
    "defect_count":   False,
}


def _run_analysis_sync(
    query:   str,
    results: List[RetrievalResult],
) -> TemporalAnalysis:
    """
    CPU-bound analysis to be run in a thread-pool executor.
    """
    if not results:
        return TemporalAnalysis(
            query     = query,
            synthesis = "No historical records were found matching the query criteria.",
        )

    records = [r.record for r in results]
    # Sort chronologically for regression
    records.sort(key=lambda rec: rec.timestamp)
    timestamps = [rec.timestamp for rec in records]

    in_window   = [r for r in results if r.in_time_window]
    window_desc = (
        f"{len(in_window)}/{len(results)} records fall within the detected temporal window"
        if any(r.in_time_window for r in results)
        else "No specific temporal window detected; analysing all retrieved records"
    )

    # ── Trend analysis per metric ─────────────────────────────────────────────
    trends: List[MetricTrend] = []
    for metric, pos_good in METRIC_CONFIG.items():
        values = [getattr(rec, metric, 0.0) for rec in records]
        if isinstance(values[0], int):
            values = [float(v) for v in values]
        trend = _analyse_metric(metric, values, timestamps, pos_good)
        trends.append(trend)

    # ── Anomaly detection ─────────────────────────────────────────────────────
    anomalies: List[AnomalyRecord] = []
    for metric in METRIC_CONFIG:
        values = [float(getattr(rec, metric, 0.0)) for rec in records]
        anomalies.extend(_detect_anomalies(metric, values, timestamps, records))

    # ── Narrative synthesis ────────────────────────────────────────────────────
    lines: List[str] = [f"Temporal analysis of {len(records)} records. {window_desc}."]

    for t in trends:
        if t.n_points >= 2:
            sign = "+" if t.slope > 0 else ""
            lines.append(
                f"  • {t.metric}: {t.direction} "
                f"(slope={sign}{t.slope:.4f}/day, mean={t.mean_value:.2f}, "
                f"range=[{t.min_value:.2f}, {t.max_value:.2f}])."
            )

    if anomalies:
        lines.append(f"  ⚠ {len(anomalies)} anomalies detected:")
        for a in anomalies[:5]:  # cap at 5 for synthesis brevity
            lines.append(
                f"    – {a.anomaly_type} in {a.metric} at "
                f"{a.timestamp.strftime('%Y-%m-%d %H:%M')} UTC "
                f"(value={a.value:.2f}, IQR-score={a.z_score:.2f}) "
                f"| model={a.model_id} station={a.station_id}"
                + (f" | note: {a.context_note[:80]}" if a.context_note else "")
            )
    else:
        lines.append("  ✓ No statistical anomalies detected in the retrieved window.")

    return TemporalAnalysis(
        query             = query,
        records_analysed  = len(records),
        time_window_desc  = window_desc,
        trends            = trends,
        anomalies         = anomalies,
        synthesis         = "\n".join(lines),
    )


# ────────────────────────────────────────────────────────────────────────────
# TemporalAnalystAgent — public API
# ────────────────────────────────────────────────────────────────────────────

class TemporalAnalystAgent:
    """
    Specialist sub-agent for Temporal Reasoning over manufacturing time-series data.

    Integration pattern (used by DebateRoom agents)::

        analyst = TemporalAnalystAgent()
        analysis = await analyst.analyse(
            query      = "yield rate trend for Model-X last month",
            session_id = session_id,
        )
        context_block = analysis.synthesis

    The agent pulls records from the shared ``TemporalVectorStore`` singleton,
    so data must be ingested via ``DataNormalizationAgent`` → ``store.add_records``
    before queries are meaningful.
    """

    AGENT_NAME = "TemporalAnalystAgent"

    def __init__(self, store: Optional[TemporalVectorStore] = None) -> None:
        self._store = store if store is not None else get_temporal_store()

    async def analyse(
        self,
        query:        str,
        session_id:   str = "",
        top_k:        int = 10,
        model_filter: Optional[str] = None,
        now:          Optional[datetime] = None,
    ) -> TemporalAnalysis:
        """
        Retrieve relevant time-series records and perform Temporal Reasoning.

        Args:
            query:        Natural-language question (may embed temporal expressions).
            session_id:   Trace ID for telemetry correlation.
            top_k:        Maximum records to retrieve from the vector store.
            model_filter: If set, restricts retrieval to this exact model_id.
            now:          Reference datetime for temporal window parsing
                          (defaults to ``datetime.now(timezone.utc)``).

        Returns:
            :class:`TemporalAnalysis` with trend, anomaly, and synthesis fields.
        """
        async with agent_span(
            span_name  = "temporal_analyst/analyse",
            span_type  = "temporal_analysis",
            agent_name = self.AGENT_NAME,
            trace_id   = session_id,
        ) as span:
            span.prompt = query
            span.metadata["top_k"]        = top_k
            span.metadata["model_filter"] = model_filter or "(none)"

            # Step 1: Retrieve temporally-relevant records
            retrieval_results = await self._store.query(
                query_text   = query,
                top_k        = top_k,
                session_id   = session_id,
                model_filter = model_filter,
                now          = now,
            )

            # Step 2: Run analysis directly (fast for typical datasets)
            analysis = _run_analysis_sync(query, retrieval_results)
            analysis = analysis.model_copy(update={
                "session_id":   session_id,
                "model_filter": model_filter,
            })

            span.raw_output = analysis.synthesis
            span.token_usage = LlmUsage.from_counts(
                prompt_tokens     = estimate_tokens(query),
                completion_tokens = estimate_tokens(analysis.synthesis),
            )
            span.metadata["records_analysed"] = analysis.records_analysed
            span.metadata["anomalies_found"]  = len(analysis.anomalies)
            span.metadata["trends_computed"]  = len(analysis.trends)

            logger.info(
                "TemporalAnalystAgent: analysed %d records, "
                "%d anomalies, %d trends (session=%s)",
                analysis.records_analysed,
                len(analysis.anomalies),
                len(analysis.trends),
                session_id,
            )
            return analysis
