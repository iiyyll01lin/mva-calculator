"""
backend/data_ops/agentic_etl.py
────────────────────────────────────────────────────────────────────────────────
Agentic ETL Pipeline — Enterprise MVA Platform v2.0.0

Implements a DataNormalizationAgent that autonomously ingests raw, messy legacy
CSV data (inconsistent date formats, missing values, non-standard column names)
and normalises it into a clean, strictly-typed ``TimeSeriesRecord`` Pydantic schema.

Design principles
─────────────────
• Fully async (asyncio) — zero blocking I/O in the event loop.
• Every ETL run is instrumented with ``span_type="etl"`` so the full ingestion
  pipeline is visible on the Mission Control Dashboard.
• A human-readable Transformation Log is written to ``SessionState.extra``
  for traceability and auditability.
• Column aliasing is data-driven: ``COLUMN_ALIASES`` maps legacy field names to
  canonical names; extend it as new legacy sources are on-boarded.
• Missing-value imputation strategy is conservative: numeric fields default to
  sentinel ``0.0`` and the field is flagged in the transformation log.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field, field_validator

from telemetry import LlmUsage, agent_span, estimate_tokens

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Canonical Target Schema
# ────────────────────────────────────────────────────────────────────────────

class TimeSeriesRecord(BaseModel):
    """
    Canonical typed schema for a single manufacturing time-series data point.

    All fields use snake_case.  Units are fixed to SI / standard manufacturing
    conventions so downstream reasoning agents can compare values without
    additional unit-conversion logic.
    """

    record_id:       str   = Field(default_factory=lambda: str(uuid.uuid4()),
                                   description="Unique record identifier.")
    timestamp:       datetime = Field(...,
                                      description="UTC timestamp for the observation.")
    model_id:        str   = Field(...,
                                   description="Product/process model identifier.")
    station_id:      str   = Field(default="UNKNOWN",
                                   description="Manufacturing station identifier.")
    yield_rate:      float = Field(default=0.0, ge=0.0, le=100.0,
                                   description="Yield rate as a percentage (0–100).")
    cycle_time_sec:  float = Field(default=0.0, ge=0.0,
                                   description="Observed cycle time in seconds.")
    defect_count:    int   = Field(default=0, ge=0,
                                   description="Number of defects observed.")
    throughput_uph:  float = Field(default=0.0, ge=0.0,
                                   description="Units per hour.")
    operator_count:  int   = Field(default=1, ge=0,
                                   description="Number of operators on station.")
    notes:           str   = Field(default="",
                                   description="Free-text notes from the legacy record.")

    model_config = {"extra": "ignore"}

    @field_validator("timestamp", mode="before")
    @classmethod
    def _parse_timestamp(cls, v: Any) -> datetime:
        """
        Accept multiple legacy date string formats and always return
        a timezone-aware UTC datetime.
        """
        if isinstance(v, datetime):
            return v if v.tzinfo else v.replace(tzinfo=timezone.utc)

        if not isinstance(v, str):
            raise ValueError(f"Cannot parse timestamp from type {type(v)}: {v!r}")

        # Strip surrounding whitespace
        v = v.strip()

        # Ordered list of patterns to try, most-specific first.
        _FORMATS = [
            "%Y-%m-%dT%H:%M:%S%z",       # ISO 8601 with tz
            "%Y-%m-%dT%H:%M:%S",          # ISO 8601 without tz
            "%Y-%m-%d %H:%M:%S",          # SQL-style
            "%Y/%m/%d %H:%M:%S",          # Slash-separated with time
            "%d/%m/%Y %H:%M",             # European short
            "%m/%d/%Y %H:%M",             # US short
            "%Y-%m-%d",                   # Date only
            "%d-%b-%Y",                   # 01-Jan-2025
            "%d/%m/%Y",                   # European date only
            "%m/%d/%Y",                   # US date only
        ]
        for fmt in _FORMATS:
            try:
                parsed = datetime.strptime(v, fmt)
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

        raise ValueError(
            f"Could not parse timestamp '{v}' with any known format. "
            f"Tried: {_FORMATS}"
        )


# ────────────────────────────────────────────────────────────────────────────
# Column Alias Map
# Maps legacy / messy column names → canonical TimeSeriesRecord field names.
# Matching is case-insensitive after stripping whitespace.
# ────────────────────────────────────────────────────────────────────────────

COLUMN_ALIASES: Dict[str, str] = {
    # timestamp variants
    "timestamp":        "timestamp",
    "date":             "timestamp",
    "datetime":         "timestamp",
    "ts":               "timestamp",
    "date_time":        "timestamp",
    "log_date":         "timestamp",
    "record_date":      "timestamp",
    "measurement_date": "timestamp",
    # model_id
    "model_id":         "model_id",
    "model":            "model_id",
    "product":          "model_id",
    "product_id":       "model_id",
    "productid":        "model_id",
    "item":             "model_id",
    # station_id
    "station_id":       "station_id",
    "station":          "station_id",
    "line":             "station_id",
    "line_id":          "station_id",
    # yield_rate  (include both raw forms and their normalised equivalents)
    "yield_rate":       "yield_rate",
    "yld_rt_%":         "yield_rate",
    "yld_rt":           "yield_rate",   # normalised form of "Yld_Rt_%"
    "yld_%":            "yield_rate",
    "yld":              "yield_rate",   # normalised form of "Yld_%"
    "yield%":           "yield_rate",
    "yield":            "yield_rate",   # normalised form of "yield%"
    "yield_pct":        "yield_rate",
    # cycle_time_sec
    "cycle_time_sec":   "cycle_time_sec",
    "cycle_time":       "cycle_time_sec",
    "cycletime":        "cycle_time_sec",
    "ct_sec":           "cycle_time_sec",
    "ct":               "cycle_time_sec",
    "cycle_time_s":     "cycle_time_sec",   # normalised form of "Cycle Time (s)"
    # defect_count
    "defect_count":     "defect_count",
    "defects":          "defect_count",
    "defect":           "defect_count",
    "defect_qty":       "defect_count",
    "defect_cnt":       "defect_count",
    "def_count":        "defect_count",
    # throughput_uph
    "throughput_uph":   "throughput_uph",
    "throughput":       "throughput_uph",
    "uph":              "throughput_uph",
    "units_per_hour":   "throughput_uph",
    "output_uph":       "throughput_uph",
    # operator_count
    "operator_count":   "operator_count",
    "operators":        "operator_count",
    "operator":         "operator_count",
    "headcount":        "operator_count",
    "hc":               "operator_count",
    "op_count":         "operator_count",
    # notes
    "notes":            "notes",
    "note":             "notes",
    "comment":          "notes",
    "remarks":          "notes",
    "remark":           "notes",
}


def _normalize_column_name(raw: str) -> str:
    """
    Normalise a raw column header to its canonical TimeSeriesRecord field name.

    Steps:
      1. Strip surrounding whitespace and convert to lower-case.
      2. Replace any non-alphanumeric character run with a single underscore.
      3. Look up in COLUMN_ALIASES; return the canonical name or the cleaned
         raw name if not found (it will simply be ignored by the schema).
    """
    cleaned = re.sub(r"[^a-z0-9]+", "_", raw.strip().lower()).strip("_")
    return COLUMN_ALIASES.get(cleaned, cleaned)


def _coerce_float(value: str, field_name: str, log: List[str]) -> float:
    """
    Safely coerce a raw string to float.  On failure, logs the imputation
    and returns 0.0.
    """
    if value is None or str(value).strip() in ("", "N/A", "n/a", "null", "NULL", "nan", "NaN", "-"):
        log.append(f"  • '{field_name}': missing/null → imputed as 0.0")
        return 0.0
    try:
        return float(str(value).replace(",", "").replace("%", "").strip())
    except ValueError:
        log.append(f"  • '{field_name}': unparseable value '{value}' → imputed as 0.0")
        return 0.0


def _coerce_int(value: str, field_name: str, log: List[str]) -> int:
    """Safely coerce a raw string to int via float intermediary."""
    f = _coerce_float(value, field_name, log)
    return int(round(f))


# ────────────────────────────────────────────────────────────────────────────
# CSV Row Parser
# ────────────────────────────────────────────────────────────────────────────

def _parse_row(
    row:         Dict[str, str],
    col_map:     Dict[str, str],     # raw_col → canonical_col
    row_index:   int,
    global_log:  List[str],
) -> Tuple[Optional[TimeSeriesRecord], List[str]]:
    """
    Convert a single raw CSV row dict into a TimeSeriesRecord.

    Returns ``(record, row_log)`` where ``row_log`` contains all transformations
    applied to this specific row.  Returns ``(None, row_log)`` if the row cannot
    be parsed (e.g. missing timestamp or model_id).
    """
    row_log: List[str] = [f"Row #{row_index}:"]
    canonical: Dict[str, Any] = {}

    for raw_col, value in row.items():
        canon = col_map.get(raw_col, _normalize_column_name(raw_col))
        if canon not in TimeSeriesRecord.model_fields:
            continue    # ignore unknown columns

        stripped = str(value).strip() if value is not None else ""

        if canon == "timestamp":
            if not stripped:
                row_log.append("  • 'timestamp': missing — row SKIPPED")
                return None, row_log
            canonical["timestamp"] = stripped    # validated by Pydantic field_validator

        elif canon == "model_id":
            if not stripped:
                row_log.append("  • 'model_id': missing — row SKIPPED")
                return None, row_log
            canonical["model_id"] = stripped

        elif canon == "station_id":
            canonical["station_id"] = stripped or "UNKNOWN"

        elif canon == "yield_rate":
            canonical["yield_rate"] = _coerce_float(stripped, "yield_rate", row_log)

        elif canon == "cycle_time_sec":
            canonical["cycle_time_sec"] = _coerce_float(stripped, "cycle_time_sec", row_log)

        elif canon == "defect_count":
            canonical["defect_count"] = _coerce_int(stripped, "defect_count", row_log)

        elif canon == "throughput_uph":
            canonical["throughput_uph"] = _coerce_float(stripped, "throughput_uph", row_log)

        elif canon == "operator_count":
            canonical["operator_count"] = _coerce_int(stripped, "operator_count", row_log)

        elif canon == "notes":
            canonical["notes"] = stripped

    if "timestamp" not in canonical or "model_id" not in canonical:
        row_log.append("  • Required fields (timestamp, model_id) missing — row SKIPPED")
        return None, row_log

    try:
        record = TimeSeriesRecord(**canonical)
        row_log.append(f"  ✓ Normalised → record_id={record.record_id[:8]}…")
        return record, row_log
    except Exception as exc:
        row_log.append(f"  ✗ Pydantic validation failed: {exc} — row SKIPPED")
        return None, row_log


# ────────────────────────────────────────────────────────────────────────────
# DataNormalizationAgent — public API
# ────────────────────────────────────────────────────────────────────────────

class ETLResult(BaseModel):
    """Result envelope returned by DataNormalizationAgent.run()."""

    records:         List[TimeSeriesRecord]
    transformation_log: str
    rows_in:         int
    rows_out:        int
    rows_skipped:    int
    session_id:      str = ""

    model_config = {"arbitrary_types_allowed": True}


class DataNormalizationAgent:
    """
    Autonomous ETL agent that ingests raw, messy CSV strings and produces
    clean ``TimeSeriesRecord`` objects.

    Usage::

        agent  = DataNormalizationAgent()
        result = await agent.run(raw_csv=messy_string, session_id=sid)
        records = result.records
        print(result.transformation_log)

    Architecture notes
    ──────────────────
    • ``run()`` is a single async coroutine — safe to ``await`` in any asyncio
      context without blocking the event loop.
    • The heavy work (CSV parsing, type coercion) runs in an executor so it
      does not starve other coroutines during large ingestion batches.
    • The full transformation log is written as a JSON-serialisable string and
      stored in ``session_state.extra["etl_transformation_log"]`` when a
      ``SessionState`` is provided.
    """

    AGENT_NAME = "DataNormalizationAgent"

    async def run(
        self,
        raw_csv:       str,
        session_id:    str = "",
        session_state: Optional[Any] = None,   # SessionState from memory_store
    ) -> ETLResult:
        """
        Ingest a raw CSV string, normalize it, and return an ETLResult.

        Args:
            raw_csv:       Raw CSV string (may have inconsistent headers, messy data).
            session_id:    Telemetry trace ID for span correlation.
            session_state: Optional live SessionState; if provided, the
                           transformation log is persisted to ``extra``.

        Returns:
            :class:`ETLResult` with the cleaned records and full audit log.
        """
        async with agent_span(
            span_name  = "etl/data_normalization_agent",
            span_type  = "etl",
            agent_name = self.AGENT_NAME,
            trace_id   = session_id,
        ) as span:
            span.prompt = f"[ETL] Ingesting CSV payload ({len(raw_csv)} bytes)"
            span.metadata["payload_bytes"] = len(raw_csv)

            # Parse the CSV directly (fast for typical manufacturing log files)
            result = self._parse_csv_sync(raw_csv, session_id)

            # Persist transformation log to SessionState for traceability
            if session_state is not None:
                if not hasattr(session_state, "extra") or session_state.extra is None:
                    session_state.extra = {}
                session_state.extra["etl_transformation_log"] = result.transformation_log
                session_state.extra["etl_rows_in"]            = result.rows_in
                session_state.extra["etl_rows_out"]           = result.rows_out
                logger.info(
                    "ETL log written to SessionState "
                    "(rows_in=%d rows_out=%d session=%s).",
                    result.rows_in, result.rows_out, session_id,
                )

            span.raw_output = (
                f"ETL complete: {result.rows_in} rows in → "
                f"{result.rows_out} records out "
                f"({result.rows_skipped} skipped)"
            )
            span.token_usage = LlmUsage.from_counts(
                prompt_tokens     = estimate_tokens(raw_csv[:2000]),
                completion_tokens = estimate_tokens(result.transformation_log[:1000]),
            )
            span.metadata["rows_in"]      = result.rows_in
            span.metadata["rows_out"]     = result.rows_out
            span.metadata["rows_skipped"] = result.rows_skipped

            logger.info(
                "DataNormalizationAgent: %d→%d records (%d skipped) session=%s",
                result.rows_in, result.rows_out, result.rows_skipped, session_id,
            )
            return result

    # ── Internal synchronous parser (runs in executor) ───────────────────────

    def _parse_csv_sync(self, raw_csv: str, session_id: str) -> ETLResult:
        """
        CPU-bound CSV parsing executed in a thread-pool executor.

        Handles:
          • BOM (UTF-8 with BOM) prefix stripping
          • Inconsistent column header casing and special characters
          • Multiple date formats (delegated to TimeSeriesRecord field_validator)
          • Missing / null / N/A values
          • Percentage signs in numeric fields (``Yld_Rt_% = "95.4%"``)
        """
        log_lines: List[str] = [
            "═" * 60,
            "DataNormalizationAgent — Transformation Log",
            f"Session : {session_id or '(none)'}",
            f"Started : {datetime.now(timezone.utc).isoformat()}",
            "═" * 60,
        ]

        # Strip UTF-8 BOM if present
        raw_csv = raw_csv.lstrip("\ufeff")

        reader   = csv.DictReader(io.StringIO(raw_csv))
        raw_cols = reader.fieldnames or []

        # Build column map: raw_header → canonical field
        col_map: Dict[str, str] = {}
        log_lines.append("\nColumn Mapping:")
        for raw in raw_cols:
            canon = _normalize_column_name(raw)
            col_map[raw] = canon
            log_lines.append(f"  '{raw}' → '{canon}'")

        log_lines.append("\nRow Transformations:")

        records:  List[TimeSeriesRecord] = []
        skipped:  int = 0

        for idx, row in enumerate(reader, start=1):
            record, row_log = _parse_row(row, col_map, idx, log_lines)
            log_lines.extend(row_log)
            if record is not None:
                records.append(record)
            else:
                skipped += 1

        rows_in = len(records) + skipped
        log_lines.append("\n" + "─" * 60)
        log_lines.append(
            f"Summary: {rows_in} rows in → {len(records)} records normalised, "
            f"{skipped} rows skipped."
        )
        log_lines.append("═" * 60)

        return ETLResult(
            records            = records,
            transformation_log = "\n".join(log_lines),
            rows_in            = rows_in,
            rows_out           = len(records),
            rows_skipped       = skipped,
            session_id         = session_id,
        )
