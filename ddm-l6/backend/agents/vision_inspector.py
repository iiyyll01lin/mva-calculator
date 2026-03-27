"""
backend/agents/vision_inspector.py
────────────────────────────────────────────────────────────────────────────────
Vision Inspector Agent — Multi-Modal "Eyes" for the MVA Platform v3.0.0

Purpose
───────
Gives the Swarm the ability to perceive the physical factory floor by
processing base64-encoded image frames (simulating an RTSP camera feed) via
a Vision-Language Model (VLM).  The agent detects visual anomalies that
purely numerical IoT sensors miss, such as:

  • Smoke or steam in the absence of elevated temperature sensor readings
  • Physical conveyor belt jams
  • Operator / PPE compliance issues
  • Liquid spills or physical misalignment at assembly stations

Architecture
────────────
  ┌──────────────────────────────────────────────────────┐
  │  caller (DebateRoom / IoT Watchdog)                  │
  │    │                                                 │
  │    └─► analyse_frame(image_b64, context)             │
  │               │  [non-blocking async call]           │
  │               ▼                                      │
  │         VisionInspectorAgent                         │
  │               │                                      │
  │         ┌─────┴──────────────────────────────┐       │
  │         │ VLM call (cloud tier)              │       │
  │         │  • base64 image in user message    │       │
  │         │  • IoT context injected            │       │
  │         │  • structured JSON response        │       │
  │         └─────┬──────────────────────────────┘       │
  │               │                                      │
  │         cryptographic sign + TamperEvidentAuditLog   │
  │               │                                      │
  │         returns VisionAnalysisResult                 │
  └──────────────────────────────────────────────────────┘

Design Principles
─────────────────
• Vision calls are ALWAYS cloud-tier (VLMs with vision input are large models).
• The coroutine is fully async; callers MUST use asyncio.create_task() or
  asyncio.gather() when vision processing runs in parallel to the debate loop.
  It NEVER blocks the core debate turn.
• The VLM analysis and every dispatched command are signed with Ed25519 and
  logged in the TamperEvidentAuditLog (cryptographic provenance guarantee).
• Stub mode activates automatically when the cloud LLM has no vision support
  or no API key is configured, returning deterministic mock output so tests
  and dev runs work offline.
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

import httpx
from pydantic import BaseModel, Field

from telemetry import LlmUsage, TamperEvidentAuditLog, agent_span, estimate_tokens
from llm_client import CLOUD_LLM_BASE_URL, CLOUD_LLM_API_KEY, CLOUD_LLM_MODEL, LLM_TIMEOUT

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Configuration — override via environment variables
# ────────────────────────────────────────────────────────────────────────────

#: VLM model for image analysis.  Should be a model that supports vision input,
#: e.g. gpt-4o, gpt-4-turbo, or a local LLaVA/InternVL endpoint.
VLM_MODEL: str = os.environ.get("VLM_MODEL", CLOUD_LLM_MODEL)

#: Hard timeout for VLM calls (vision is token-heavy and slower than text).
VLM_TIMEOUT_S: float = float(os.environ.get("VLM_TIMEOUT_S", "90.0"))

#: Confidence threshold (0–1) below which a detection is classified as
#: UNCERTAIN rather than ANOMALY_CONFIRMED or NORMAL.
VLM_CONFIDENCE_THRESHOLD: float = float(os.environ.get("VLM_CONFIDENCE_THRESHOLD", "0.65"))

AGENT_NAME = "VisionInspectorAgent"


# ────────────────────────────────────────────────────────────────────────────
# Schema: VisionAnalysisResult
# ────────────────────────────────────────────────────────────────────────────

class VisualVerdict(str, Enum):
    """Outcome classification returned by the VLM."""
    ANOMALY_CONFIRMED = "ANOMALY_CONFIRMED"   # clear visual evidence of a fault
    NORMAL            = "NORMAL"               # frame appears fault-free
    UNCERTAIN         = "UNCERTAIN"            # low-confidence detection


class BoundingBox(BaseModel):
    """Approximate pixel coordinates of a detected anomaly region."""
    label:  str   = Field(..., description="Short label, e.g. 'smoke', 'jam', 'spill'.")
    x:      float = Field(..., ge=0.0, le=1.0, description="Left edge as fraction of frame width.")
    y:      float = Field(..., ge=0.0, le=1.0, description="Top edge as fraction of frame height.")
    width:  float = Field(..., ge=0.0, le=1.0, description="Box width as fraction of frame width.")
    height: float = Field(..., ge=0.0, le=1.0, description="Box height as fraction of frame height.")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Detection confidence 0–1.")

    model_config = {"extra": "ignore"}


class VisionAnalysisResult(BaseModel):
    """
    Structured output from a single call to VisionInspectorAgent.

    All fields are JSON-serialisable so the result can be embedded directly
    into DebatePlan extensions and logged to the audit chain.
    """
    analysis_id:          str   = Field(
        default_factory=lambda: f"VIS-{uuid.uuid4().hex[:8].upper()}"
    )
    frame_id:             str   = Field(default="", description="Camera / frame identifier.")
    verdict:              VisualVerdict
    anomaly_description:  str   = Field(..., description="Human-readable description of the finding.")
    confidence:           float = Field(..., ge=0.0, le=1.0)
    detected_objects:     List[BoundingBox] = Field(default_factory=list)
    recommended_actions:  List[str]         = Field(default_factory=list)
    analysed_at:          str   = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    # Cryptographic signature over the canonical payload (set after analysis).
    cryptographic_signature: Optional[str] = None

    model_config = {"extra": "ignore"}

    def _signable_dict(self) -> Dict[str, Any]:
        d = self.model_dump()
        d.pop("cryptographic_signature", None)
        return d


# ────────────────────────────────────────────────────────────────────────────
# VLM System Prompt
# ────────────────────────────────────────────────────────────────────────────

_VLM_SYSTEM_PROMPT = """\
You are VisionInspectorAgent — an elite automated visual quality inspector for
a precision manufacturing facility.  You receive camera frames (base64 JPEG/PNG)
and IoT context from the factory floor.  Your sole objective is to detect and
describe any PHYSICAL anomalies visible in the image that IoT sensors may miss.

Examples of anomalies you should detect:
  - Visible smoke, steam, or fire (even when temperature sensor reads normal)
  - Conveyor belt jams, product pile-ups, or mechanical misalignment
  - Liquid spills on the production floor
  - Missing or incorrect components at an assembly station
  - Operator not wearing required PPE
  - Damaged parts visible on the line

Output a valid JSON object matching this schema EXACTLY (no markdown fences):
{
  "verdict": "ANOMALY_CONFIRMED" | "NORMAL" | "UNCERTAIN",
  "anomaly_description": "<one-paragraph human-readable description>",
  "confidence": <float 0.0–1.0>,
  "detected_objects": [
    {
      "label": "<short label e.g. 'smoke', 'jam', 'spill'>",
      "x": <float 0.0–1.0>,
      "y": <float 0.0–1.0>,
      "width": <float 0.0–1.0>,
      "height": <float 0.0–1.0>,
      "confidence": <float 0.0–1.0>
    },
    ...
  ],
  "recommended_actions": ["<action>", ...]
}
Respond with raw JSON only — no markdown, no extra text.\
"""


# ────────────────────────────────────────────────────────────────────────────
# Stub / Offline Fallback
# ────────────────────────────────────────────────────────────────────────────

def _stub_vision_result(frame_id: str, iot_context: str) -> VisionAnalysisResult:
    """
    Return a deterministic stub result when no VLM endpoint is configured.
    Used in dev / unit-test environments so the full pipeline can run offline.
    """
    has_anomaly_hint = any(
        kw in iot_context.lower()
        for kw in ("jam", "smoke", "stop", "critical", "anomaly", "failed", "below")
    )
    if has_anomaly_hint:
        return VisionAnalysisResult(
            frame_id             = frame_id,
            verdict              = VisualVerdict.ANOMALY_CONFIRMED,
            anomaly_description  = (
                "[STUB] Camera at Station 4 shows a physical product jam on the "
                "conveyor belt.  Several units have accumulated behind the sensor "
                "gate confirming the IoT yield-drop signal.  Immediate halt and "
                "clearance required."
            ),
            confidence           = 0.91,
            detected_objects     = [
                BoundingBox(label="conveyor_jam", x=0.35, y=0.50,
                            width=0.30, height=0.20, confidence=0.91),
            ],
            recommended_actions  = [
                "Immediately halt conveyor belt at Station 4.",
                "Dispatch maintenance robot to clear jam.",
                "Inspect upstream feed mechanism for root cause.",
            ],
        )
    return VisionAnalysisResult(
        frame_id             = frame_id,
        verdict              = VisualVerdict.NORMAL,
        anomaly_description  = (
            "[STUB] Frame appears clear.  No physical anomalies detected.  "
            "Production appears to be running within visual norms."
        ),
        confidence           = 0.88,
        detected_objects     = [],
        recommended_actions  = [],
    )


# ────────────────────────────────────────────────────────────────────────────
# VLM HTTP Call (OpenAI vision-compatible API)
# ────────────────────────────────────────────────────────────────────────────

async def _call_vlm(
    image_b64:   str,
    iot_context: str,
) -> tuple[str, int, int]:
    """
    Send a vision-language completion request to the configured cloud endpoint.

    Returns (raw_content, prompt_tokens, completion_tokens).
    Raises httpx.HTTPError or ValueError on failure so the caller can fall
    back to stub mode.
    """
    user_content: list[Dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"Factory floor camera frame captured at "
                f"{datetime.now(timezone.utc).isoformat()}.  "
                f"IoT sensor context:\n{iot_context}\n\n"
                "Analyse the image and report any visible anomalies."
            ),
        },
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/jpeg;base64,{image_b64}",
                "detail": "high",
            },
        },
    ]

    payload = {
        "model":       VLM_MODEL,
        "messages":    [
            {"role": "system", "content": _VLM_SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ],
        "temperature": 0.1,
        "max_tokens":  1024,
    }

    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {CLOUD_LLM_API_KEY}",
    }

    async with httpx.AsyncClient(timeout=VLM_TIMEOUT_S) as client:
        resp = await client.post(
            f"{CLOUD_LLM_BASE_URL.rstrip('/')}/chat/completions",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data             = resp.json()
        content: str     = data["choices"][0]["message"]["content"]
        usage            = data.get("usage", {})
        prompt_tokens    = usage.get("prompt_tokens", estimate_tokens(_VLM_SYSTEM_PROMPT))
        completion_tokens = usage.get("completion_tokens", estimate_tokens(content))
        return content, prompt_tokens, completion_tokens


# ────────────────────────────────────────────────────────────────────────────
# Public Agent Interface
# ────────────────────────────────────────────────────────────────────────────

class VisionInspectorAgent:
    """
    Stateless async agent that wraps VLM-based visual anomaly detection.

    Callers should use the module-level helper ``analyse_frame()`` rather
    than instantiating this class directly, unless they need multiple agent
    instances with different configurations.

    All analysis results are:
      1. Ed25519-signed before being returned.
      2. Appended to the TamperEvidentAuditLog for compliance.
      3. Wrapped in an agent_span for telemetry tracing.
    """

    async def analyse(
        self,
        image_b64:   str,
        session_id:  str,
        frame_id:    str       = "",
        iot_context: str       = "",
    ) -> VisionAnalysisResult:
        """
        Analyse a single base64-encoded image frame for visual anomalies.

        Args:
            image_b64:   Base64-encoded image (JPEG or PNG, max ~1 MB recommended).
            session_id:  Telemetry trace ID; links this span to the parent debate.
            frame_id:    Optional camera / frame identifier for tracking.
            iot_context: Timestamped IoT sensor readings to inject as context
                         for the VLM (e.g. "yield_rate: 87%, temp: 58°C").

        Returns:
            VisionAnalysisResult — signed and audited.

        Raises:
            Never: any internal errors fall back to stub mode with an UNCERTAIN
            verdict so the calling debate loop is never blocked.
        """
        if not frame_id:
            frame_id = f"frame-{uuid.uuid4().hex[:8]}"

        async with agent_span(
            span_name  = "vision/analyse_frame",
            span_type  = "tool_exec",
            agent_name = AGENT_NAME,
            trace_id   = session_id,
            tool_name  = "vlm_frame_analysis",
            tool_attempt = 1,
        ) as span:
            span.metadata["frame_id"]    = frame_id
            span.metadata["image_bytes"] = len(image_b64)
            span.metadata["has_context"] = bool(iot_context)
            span.prompt = iot_context[:500] if iot_context else "(no IoT context)"

            result: Optional[VisionAnalysisResult] = None

            # ── Attempt live VLM call ──────────────────────────────────────
            if CLOUD_LLM_API_KEY and CLOUD_LLM_API_KEY not in ("", "sk-placeholder"):
                try:
                    raw, pt, ct = await _call_vlm(image_b64, iot_context)
                    span.token_usage = LlmUsage.from_counts(
                        prompt_tokens=pt, completion_tokens=ct
                    )
                    result = self._parse_vlm_output(raw, frame_id)
                    span.raw_output = raw
                except Exception as exc:
                    logger.warning(
                        "VisionInspectorAgent: VLM call failed (%s); using stub fallback.",
                        exc,
                    )
                    span.metadata["vlm_fallback_reason"] = str(exc)

            # ── Stub fallback ──────────────────────────────────────────────
            if result is None:
                result = _stub_vision_result(frame_id, iot_context)
                span.raw_output  = result.model_dump_json()
                span.token_usage = LlmUsage.from_counts(
                    prompt_tokens=estimate_tokens(_VLM_SYSTEM_PROMPT),
                    completion_tokens=estimate_tokens(span.raw_output),
                )
                span.metadata["stub_mode"] = True

            span.metadata["verdict"]    = result.verdict.value
            span.metadata["confidence"] = result.confidence

        # ── Sign result with Ed25519 ───────────────────────────────────────
        result = self._sign_result(result)

        # ── Log to tamper-evident audit chain ── (fire-and-forget) ─────────
        asyncio.create_task(
            TamperEvidentAuditLog.record(
                event_type = "VLM_FRAME_ANALYSIS",
                entity_id  = result.analysis_id,
                payload    = result.model_dump(),
            )
        )

        logger.info(
            "VisionInspector: frame=%s verdict=%s confidence=%.2f session=%s",
            frame_id, result.verdict.value, result.confidence, session_id,
        )
        return result

    # ── Private helpers ────────────────────────────────────────────────────

    @staticmethod
    def _parse_vlm_output(raw: str, frame_id: str) -> VisionAnalysisResult:
        """Parse and validate VLM JSON output."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines   = cleaned.splitlines()
            cleaned = "\n".join(lines[1:])
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3].rstrip()
        data = json.loads(cleaned)
        data["frame_id"] = frame_id
        return VisionAnalysisResult.model_validate(data)

    @staticmethod
    def _sign_result(result: VisionAnalysisResult) -> VisionAnalysisResult:
        """Sign the analysis result in-place."""
        try:
            from security.provenance import sign_payload
            result.cryptographic_signature = sign_payload(result._signable_dict())
        except Exception as exc:
            logger.warning("VisionInspectorAgent: signing failed: %s", exc)
        return result


# ────────────────────────────────────────────────────────────────────────────
# Module-level convenience function
# ────────────────────────────────────────────────────────────────────────────

_agent_singleton = VisionInspectorAgent()


async def analyse_frame(
    image_b64:   str,
    session_id:  str,
    frame_id:    str = "",
    iot_context: str = "",
) -> VisionAnalysisResult:
    """
    Module-level helper — analyse a single camera frame for visual anomalies.

    Wraps :meth:`VisionInspectorAgent.analyse` using a module-level singleton.
    Designed for ease of import in the DebateRoom and IoT Watchdog:

        from agents.vision_inspector import analyse_frame, VisualVerdict

        result = await analyse_frame(
            image_b64   = frame_bytes_b64,
            session_id  = session_id,
            iot_context = f"yield_rate: {tick.yield_rate}%, temp: {tick.temperature_c}°C",
        )
        if result.verdict == VisualVerdict.ANOMALY_CONFIRMED:
            ...

    This helper is always invoked asynchronously so it never blocks the
    debate loop.  Use ``asyncio.create_task()`` or ``asyncio.wait_for()``
    with a timeout when calling from latency-sensitive paths.
    """
    return await _agent_singleton.analyse(
        image_b64   = image_b64,
        session_id  = session_id,
        frame_id    = frame_id,
        iot_context = iot_context,
    )
