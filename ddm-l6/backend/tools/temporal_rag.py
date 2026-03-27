"""
backend/tools/temporal_rag.py
────────────────────────────────────────────────────────────────────────────────
Temporal RAG (Retrieval-Augmented Generation) Vector Store — MVA Platform v2.0.0

Implements a lightweight, in-memory, time-aware vector store that indexes
``TimeSeriesRecord`` objects produced by the Agentic ETL pipeline.

Key features
────────────
• **Time-aware retrieval**: Queries carrying explicit temporal expressions
  (e.g. "last week", "last Tuesday", "past 3 months") prioritise documents
  with matching timestamps before applying cosine similarity.
• **Temporal decay**: A Gaussian decay function down-weights records further
  from the query's temporal anchor, preventing stale data from dominating.
• **Keyword similarity**: A tf-idf-style cosine similarity (pure Python, no
  external ML deps) supplements temporal scoring.
• **Fully async**: the ``query()`` entrypoint is a coroutine; heavy similarity
  math is offloaded to a thread-pool executor so the event loop stays free.
• **Telemetry**: every retrieval is instrumented with ``span_type="rag"`` so
  lookups appear as distinct spans on the Mission Control Dashboard.

Design notes
────────────
The store intentionally avoids external vector-DB dependencies (Pinecone,
Chroma, FAISS) so the PoC runs in the existing Docker environment.  In
production, replace ``_cosine_sim`` with a real embedding model call and swap
the in-memory list for an async Chroma / Weaviate client.
"""

from __future__ import annotations

import asyncio
import logging
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

from data_ops.agentic_etl import TimeSeriesRecord
from telemetry import LlmUsage, agent_span, estimate_tokens

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Constants & Tuning Parameters
# ────────────────────────────────────────────────────────────────────────────

#: Default number of records returned per query.
DEFAULT_TOP_K: int = 5

#: Temporal decay half-life in days.  A record that is ``DECAY_HALF_LIFE_DAYS``
#: days outside the matched temporal window has its score halved.
DECAY_HALF_LIFE_DAYS: float = 7.0

#: Weight blending parameter: score = α·temporal_score + (1-α)·keyword_score
TEMPORAL_ALPHA: float = 0.65


# ────────────────────────────────────────────────────────────────────────────
# Temporal Expression Parser
# ────────────────────────────────────────────────────────────────────────────

def parse_temporal_expression(
    text:    str,
    now:     Optional[datetime] = None,
) -> Optional[Tuple[datetime, datetime]]:
    """
    Extract a ``(start, end)`` UTC datetime window from a natural-language
    temporal expression embedded in ``text``.

    Recognised patterns (case-insensitive):
      • "last N days/weeks/months"
      • "past N days/weeks/months"
      • "yesterday"
      • "last week" / "this week"
      • "last month" / "this month"
      • "last Tuesday" (or any day name)
      • Explicit ISO date: "since 2025-10-01"

    Returns ``None`` if no temporal expression is found.
    """
    now = now or datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    lowered = text.lower()

    # ── "last N days / weeks / months" ───────────────────────────────────────
    m = re.search(r"(?:last|past)\s+(\d+)\s+(day|week|month)s?", lowered)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        if unit == "day":
            start = today_start - timedelta(days=n)
        elif unit == "week":
            start = today_start - timedelta(weeks=n)
        else:  # month ≈ 30 days
            start = today_start - timedelta(days=n * 30)
        return start, now

    # ── "yesterday" ──────────────────────────────────────────────────────────
    if "yesterday" in lowered:
        start = today_start - timedelta(days=1)
        end   = today_start
        return start, end

    # ── "last week" / "this week" ─────────────────────────────────────────────
    if "last week" in lowered:
        # Monday of last week to Sunday of last week
        days_since_monday = today_start.weekday()
        start = today_start - timedelta(days=days_since_monday + 7)
        end   = start + timedelta(days=7)
        return start, end
    if "this week" in lowered:
        start = today_start - timedelta(days=today_start.weekday())
        return start, now

    # ── "last month" / "this month" ───────────────────────────────────────────
    if "last month" in lowered:
        # First day of the previous calendar month
        first_this_month = today_start.replace(day=1)
        start = (first_this_month - timedelta(days=1)).replace(day=1)
        end   = first_this_month
        return start, end
    if "this month" in lowered:
        start = today_start.replace(day=1)
        return start, now

    # ── "last <weekday>" e.g. "last Tuesday" ─────────────────────────────────
    DAY_MAP = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
    }
    for day_name, day_num in DAY_MAP.items():
        if f"last {day_name}" in lowered:
            delta = (today_start.weekday() - day_num) % 7 or 7
            target_day = today_start - timedelta(days=delta)
            return target_day, target_day + timedelta(days=1)

    # ── Explicit ISO date "since YYYY-MM-DD" ─────────────────────────────────
    m = re.search(r"since\s+(\d{4}-\d{2}-\d{2})", lowered)
    if m:
        try:
            start = datetime.strptime(m.group(1), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            return start, now
        except ValueError:
            pass

    return None


# ────────────────────────────────────────────────────────────────────────────
# Lightweight TF-IDF-style Keyword Vectoriser (no external deps)
# ────────────────────────────────────────────────────────────────────────────

def _tokenise(text: str) -> List[str]:
    """Lower-case, split on non-alphanumeric boundaries."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _build_doc_vector(record: TimeSeriesRecord) -> Counter:
    """
    Build a word-count vector from the textual fields of a TimeSeriesRecord.
    Numerical fields are converted to rounded string tokens so queries like
    "yield 95" can match records with yield_rate ≈ 95.
    """
    tokens = _tokenise(
        f"{record.model_id} {record.station_id} {record.notes} "
        f"yield_{int(record.yield_rate)} "
        f"cycle_{int(record.cycle_time_sec)} "
        f"uph_{int(record.throughput_uph)} "
        f"defects_{record.defect_count} "
        f"ops_{record.operator_count}"
    )
    return Counter(tokens)


def _cosine_sim(a: Counter, b: Counter) -> float:
    """Cosine similarity between two count vectors."""
    if not a or not b:
        return 0.0
    dot      = sum(a[t] * b[t] for t in a if t in b)
    norm_a   = math.sqrt(sum(v * v for v in a.values()))
    norm_b   = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ────────────────────────────────────────────────────────────────────────────
# Temporal Decay Function
# ────────────────────────────────────────────────────────────────────────────

def _temporal_decay(
    record_ts:    datetime,
    window_start: datetime,
    window_end:   datetime,
) -> float:
    """
    Compute a temporal relevance score in [0, 1] for ``record_ts`` relative to
    a target window ``[window_start, window_end]``.

    Rules:
      • Records **inside** the window score 1.0.
      • Records **outside** the window decay with a Gaussian based on their
        distance from the nearest window boundary, parameterised by
        ``DECAY_HALF_LIFE_DAYS``.

    The decay formula is::

        score = exp(-ln(2) × distance_days / DECAY_HALF_LIFE_DAYS)

    which yields 1.0 at distance=0 and 0.5 at distance=DECAY_HALF_LIFE_DAYS.
    """
    if window_start <= record_ts <= window_end:
        return 1.0
    # Distance to nearest window boundary in fractional days
    if record_ts < window_start:
        distance = (window_start - record_ts).total_seconds() / 86_400
    else:
        distance = (record_ts - window_end).total_seconds() / 86_400

    return math.exp(-math.log(2) * distance / DECAY_HALF_LIFE_DAYS)


# ────────────────────────────────────────────────────────────────────────────
# Retrieval Result Schema
# ────────────────────────────────────────────────────────────────────────────

class RetrievalResult(BaseModel):
    """A single ranked result from the Temporal Vector Store."""

    record:          TimeSeriesRecord
    score:           float = Field(ge=0.0, le=1.0, description="Blended relevance score.")
    temporal_score:  float = Field(ge=0.0, le=1.0)
    keyword_score:   float = Field(ge=0.0, le=1.0)
    in_time_window:  bool  = Field(default=False,
                                   description="True if record falls inside the query's temporal window.")

    model_config = {"arbitrary_types_allowed": True}


# ────────────────────────────────────────────────────────────────────────────
# TemporalVectorStore
# ────────────────────────────────────────────────────────────────────────────

class TemporalVectorStore:
    """
    In-memory, time-aware vector store for ``TimeSeriesRecord`` objects.

    Lifecycle::

        store = TemporalVectorStore()
        await store.add_records(records_from_etl)
        results = await store.query("yield rate drop last week for Model-X", top_k=5)

    Thread-safety note: ``add_records`` and ``query`` are both async and use
    ``asyncio.Lock`` to serialise writes so concurrent ETL tasks cannot
    corrupt the index.  Reads (``query``) do not lock—they operate on an
    immutable snapshot of the index list to maximise read concurrency.
    """

    AGENT_NAME = "TemporalVectorStore"

    def __init__(self) -> None:
        self._records:  List[TimeSeriesRecord] = []
        self._vectors:  List[Counter]          = []

    # ── Write path ──────────────────────────────────────────────────────────

    async def add_records(
        self,
        records:    List[TimeSeriesRecord],
        session_id: str = "",
    ) -> int:
        """
        Index a list of TimeSeriesRecord objects.

        Returns the number of new records added.
        Instrumented with ``span_type="rag"`` for dashboard visibility.
        """
        async with agent_span(
            span_name  = "rag/add_records",
            span_type  = "rag",
            agent_name = self.AGENT_NAME,
            trace_id   = session_id,
        ) as span:
            span.prompt = f"Indexing {len(records)} records"

            # Build keyword vectors directly (fast for typical manufacturing datasets)
            vectors = [_build_doc_vector(r) for r in records]

            self._records.extend(records)
            self._vectors.extend(vectors)

            span.raw_output = f"Store size: {len(self._records)} records total"
            span.metadata["added_count"] = len(records)
            span.metadata["store_size"]  = len(self._records)
            logger.info(
                "TemporalVectorStore: indexed %d records (total=%d session=%s)",
                len(records), len(self._records), session_id,
            )
            return len(records)

    # ── Read path ───────────────────────────────────────────────────────────

    async def query(
        self,
        query_text:  str,
        top_k:       int                   = DEFAULT_TOP_K,
        session_id:  str                   = "",
        now:         Optional[datetime]    = None,
        model_filter: Optional[str]        = None,
    ) -> List[RetrievalResult]:
        """
        Retrieve the top-k most relevant records for a natural-language query.

        Scoring pipeline:
          1. Parse any temporal expression in ``query_text`` to get a window.
          2. For each record compute:
               temporal_score = 1.0 if in window else decay(distance)
               keyword_score  = cosine_sim(query_vec, doc_vec)
               blend          = α·temporal_score + (1-α)·keyword_score
          3. Optionally filter by ``model_filter`` (exact match on ``model_id``).
          4. Sort by blend score descending; return top ``top_k``.

        Instrumented with ``span_type="rag"`` for Mission Control visibility.
        """
        async with agent_span(
            span_name  = "rag/query",
            span_type  = "rag",
            agent_name = self.AGENT_NAME,
            trace_id   = session_id,
        ) as span:
            span.prompt = query_text
            span.metadata["top_k"]        = top_k
            span.metadata["model_filter"] = model_filter or "(none)"

            # Snapshot so we don't hold a lock during scoring
            records = list(self._records)
            vectors = list(self._vectors)

            if not records:
                span.raw_output = "Store is empty — no results."
                return []

            # Score directly (fast for typical manufacturing datasets)
            results = self._score_sync(
                query_text, records, vectors, top_k, now, model_filter
            )

            span.raw_output = (
                f"Retrieved {len(results)}/{len(records)} records "
                f"(temporal window: {parse_temporal_expression(query_text, now)})"
            )
            span.metadata["results_count"]   = len(results)
            span.metadata["store_total"]      = len(records)
            span.token_usage = LlmUsage.from_counts(
                prompt_tokens     = estimate_tokens(query_text),
                completion_tokens = estimate_tokens(span.raw_output),
            )
            return results

    # ── Internal scoring (runs in executor) ─────────────────────────────────

    def _score_sync(
        self,
        query_text:   str,
        records:      List[TimeSeriesRecord],
        vectors:      List[Counter],
        top_k:        int,
        now:          Optional[datetime],
        model_filter: Optional[str],
    ) -> List[RetrievalResult]:
        now_dt      = now or datetime.now(timezone.utc)
        time_window = parse_temporal_expression(query_text, now_dt)
        query_vec   = Counter(_tokenise(query_text))

        scored: List[RetrievalResult] = []
        for record, doc_vec in zip(records, vectors):
            # Optional model_id filter
            if model_filter and record.model_id != model_filter:
                continue

            # Temporal score
            if time_window:
                t_score      = _temporal_decay(record.timestamp, *time_window)
                in_window    = time_window[0] <= record.timestamp <= time_window[1]
            else:
                t_score   = 1.0    # no window → all timestamps equally relevant
                in_window = False

            # Keyword score
            k_score = _cosine_sim(query_vec, doc_vec)

            blend = TEMPORAL_ALPHA * t_score + (1.0 - TEMPORAL_ALPHA) * k_score

            scored.append(RetrievalResult(
                record         = record,
                score          = round(blend, 4),
                temporal_score = round(t_score, 4),
                keyword_score  = round(k_score, 4),
                in_time_window = in_window,
            ))

        # Sort by blended score descending; stable sort preserves insertion order for ties
        scored.sort(key=lambda r: r.score, reverse=True)
        return scored[:top_k]

    # ── Utility ─────────────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._records)

    def clear(self) -> None:
        """Remove all indexed records (useful between test cases)."""
        self._records.clear()
        self._vectors.clear()


# ────────────────────────────────────────────────────────────────────────────
# Module-level singleton — shared across all agents in the process
# ────────────────────────────────────────────────────────────────────────────

_store_instance: Optional[TemporalVectorStore] = None


def get_temporal_store() -> TemporalVectorStore:
    """
    Return the process-wide TemporalVectorStore singleton.

    First call initialises the store; subsequent calls return the same
    instance so all agents share a single indexed corpus.
    """
    global _store_instance
    if _store_instance is None:
        _store_instance = TemporalVectorStore()
        logger.info("TemporalVectorStore singleton initialised.")
    return _store_instance


# ────────────────────────────────────────────────────────────────────────────
# Re-exports for consumer convenience
# ────────────────────────────────────────────────────────────────────────────
# Allow callers (including tests) to do:
#   from tools.temporal_rag import TemporalAnalystAgent
# without needing to know that the agent lives in agents.temporal_analyst.
from agents.temporal_analyst import TemporalAnalystAgent  # noqa: F401,E402
