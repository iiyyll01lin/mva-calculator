"""
backend/llm_client.py
────────────────────────────────────────────────────────────────────────────────
LLM Client Abstraction Layer — Enterprise MVA Platform v2.0.0

Unified async interface for all LLM calls, with per-agent tier routing:

  Cloud tier (strong model, higher cost)
    → ConsensusJudgeAgent, SupervisorAgent
    → Set OPENAI_API_BASE + OPENAI_API_KEY (or any OpenAI-compatible endpoint)

  Local tier (fast model, near-zero cost)
    → CostOptimizationAgent, QualityAndTimeAgent, specialist sub-agents
    → Set LOCAL_LLM_BASE_URL + LOCAL_LLM_MODEL
      (e.g. vLLM on AMD ROCm: LOCAL_LLM_BASE_URL=http://localhost:8000/v1)

Routing is driven by AGENT_MODEL_MAP; override per-agent without code changes
by setting AGENT_MODEL_MAP_JSON as a JSON object, e.g.:
    {"ConsensusJudgeAgent": "cloud", "CostOptimizationAgent": "local"}

Stub mode activates automatically when no real API key is configured,
returning deterministic JSON so unit tests and dev runs work offline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from enum import Enum
from typing import Any, Dict, List, Optional

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Configuration — override via environment variables
# ────────────────────────────────────────────────────────────────────────────

#: Cloud LLM (OpenAI-compatible endpoint)
CLOUD_LLM_BASE_URL: str = os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
CLOUD_LLM_API_KEY:  str = os.environ.get("OPENAI_API_KEY", "")
CLOUD_LLM_MODEL:    str = os.environ.get("CLOUD_LLM_MODEL", "gpt-4o")

#: Local LLM (vLLM / ROCm / CUDA — OpenAI-compatible)
LOCAL_LLM_BASE_URL: str = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:8000/v1")
LOCAL_LLM_API_KEY:  str = os.environ.get("LOCAL_LLM_API_KEY", "token-local")
LOCAL_LLM_MODEL:    str = os.environ.get("LOCAL_LLM_MODEL", "meta-llama/Meta-Llama-3-8B-Instruct")

#: Request timeout in seconds for all LLM HTTP calls
LLM_TIMEOUT: float = float(os.environ.get("LLM_TIMEOUT", "60.0"))


# ────────────────────────────────────────────────────────────────────────────
# Model Tier
# ────────────────────────────────────────────────────────────────────────────

class ModelTier(str, Enum):
    """Routing tier assignment for an LLM call."""
    CLOUD = "cloud"   # Strong cloud model — best reasoning, higher cost/latency
    LOCAL = "local"   # Fast local model   — lower cost, hardware-accelerated


# ────────────────────────────────────────────────────────────────────────────
# Agent → Tier Routing Map
# ────────────────────────────────────────────────────────────────────────────

#: Default routing: forensics (debate) agents go local; supervisors go cloud.
_DEFAULT_AGENT_MODEL_MAP: Dict[str, ModelTier] = {
    "SupervisorAgent":       ModelTier.CLOUD,
    "ConsensusJudgeAgent":   ModelTier.CLOUD,
    "CostOptimizationAgent": ModelTier.LOCAL,
    "QualityAndTimeAgent":   ModelTier.LOCAL,
    "LaborTimeAgent":        ModelTier.LOCAL,
    "BomAgent":              ModelTier.LOCAL,
    "SimulationAgent":       ModelTier.LOCAL,
    "DebateRoom":            ModelTier.LOCAL,
}


def _load_agent_model_map() -> Dict[str, ModelTier]:
    """Load routing map from env var overrides merged with defaults."""
    raw = os.environ.get("AGENT_MODEL_MAP_JSON", "")
    if not raw:
        return dict(_DEFAULT_AGENT_MODEL_MAP)
    try:
        overrides = json.loads(raw)
        merged = dict(_DEFAULT_AGENT_MODEL_MAP)
        for agent, tier_str in overrides.items():
            merged[agent] = ModelTier(tier_str)
        return merged
    except (json.JSONDecodeError, ValueError) as exc:
        logger.warning("AGENT_MODEL_MAP_JSON parse error — using defaults: %s", exc)
        return dict(_DEFAULT_AGENT_MODEL_MAP)


AGENT_MODEL_MAP: Dict[str, ModelTier] = _load_agent_model_map()


# ────────────────────────────────────────────────────────────────────────────
# Request / Response Models
# ────────────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    """A single message in an LLM conversation."""
    role:    str
    content: str
    name:    Optional[str] = None


class LlmCallResult(BaseModel):
    """Result of a single LLM completion call."""
    content:           str
    prompt_tokens:     int       = 0
    completion_tokens: int       = 0
    model:             str       = ""
    tier:              ModelTier = ModelTier.CLOUD


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────

async def call_llm(
    messages:        List[ChatMessage],
    agent_name:      str,
    response_format: Optional[Dict[str, Any]] = None,
    temperature:     float = 0.3,
    max_tokens:      int   = 2048,
) -> LlmCallResult:
    """
    Route a chat completion request to the appropriate LLM endpoint based on
    the calling agent's tier assignment in ``AGENT_MODEL_MAP``.

    In stub/offline mode (no real API key set), returns a deterministic
    placeholder so the full pipeline runs without real LLM API access.

    Args:
        messages:        Conversation history to send to the model.
        agent_name:      Caller identifier; maps to ModelTier via AGENT_MODEL_MAP.
        response_format: Optional structured-output format (e.g. JSON schema).
        temperature:     Sampling temperature; lower → more deterministic.
        max_tokens:      Max completion tokens.

    Returns:
        LlmCallResult with completion content and token usage.
    """
    tier = AGENT_MODEL_MAP.get(agent_name, ModelTier.CLOUD)

    if tier == ModelTier.LOCAL:
        base_url = LOCAL_LLM_BASE_URL
        api_key  = LOCAL_LLM_API_KEY
        model    = LOCAL_LLM_MODEL
    else:
        base_url = CLOUD_LLM_BASE_URL
        api_key  = CLOUD_LLM_API_KEY
        model    = CLOUD_LLM_MODEL

    # Stub mode: no real API key — fall back to deterministic offline responses.
    if not api_key or api_key.startswith("token-"):
        return await _stub_llm_call(messages, agent_name, model, tier)

    payload: Dict[str, Any] = {
        "model":       model,
        "messages":    [m.model_dump(exclude_none=True) for m in messages],
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    if response_format:
        payload["response_format"] = response_format

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }

    async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as client:
        resp = await client.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        data = resp.json()

    choice = data["choices"][0]["message"]["content"]
    usage  = data.get("usage", {})
    return LlmCallResult(
        content           = choice,
        prompt_tokens     = usage.get("prompt_tokens",     0),
        completion_tokens = usage.get("completion_tokens", 0),
        model             = model,
        tier              = tier,
    )


# ────────────────────────────────────────────────────────────────────────────
# Offline stub — deterministic responses per agent name
# ────────────────────────────────────────────────────────────────────────────

_STUB_RESPONSES: Dict[str, str] = {
    "CostOptimizationAgent": json.dumps({
        "plan_id":            "COST-PLAN-A",
        "summary":            (
            "Merge stations 3 and 4 (compatible skill sets); reduce operators "
            "to 3; automate barcode scanning at station 1. Projected unit cost "
            "reduction: 12%."
        ),
        "num_operators":      3,
        "cycle_time_tmu":     420.0,
        "cost_per_unit_usd":  1.85,
        "throughput_uph":     52.4,
        "key_changes": [
            "Merge stations 3+4",
            "Automate barcode scanning",
            "Reduce buffer inventory by 20%",
        ],
        "risk_flags": [
            "Operator fatigue at merged station may degrade quality",
        ],
    }),
    "QualityAndTimeAgent": json.dumps({
        "critiqued_plan_id": "COST-PLAN-A",
        "critic_agent":      "QualityAndTimeAgent",
        "weaknesses_found": [
            "Merged station reduces inline quality inspection coverage",
            "3 operators creates a bottleneck at peak demand",
        ],
        "counter_plan": {
            "plan_id":            "QUALITY-PLAN-B",
            "summary":            (
                "Add dedicated inline AOI station; increase to 5 operators "
                "for a parallel rework loop. Throughput +18%, defect rate -35%."
            ),
            "num_operators":      5,
            "cycle_time_tmu":     380.0,
            "cost_per_unit_usd":  2.40,
            "throughput_uph":     61.8,
            "key_changes": [
                "Add inline AOI (Automated Optical Inspection) at exit",
                "Split screw-driving station for parallel flow",
                "Implement poka-yoke jig at cable-routing",
            ],
            "risk_flags": [
                "Higher headcount increases labor cost by ~30%",
            ],
        },
    }),
    "ConsensusJudgeAgent": json.dumps({
        "consensus_plan_id":  "CONSENSUS-OPTIMAL",
        "summary":            (
            "Balanced plan: 4 operators with smart station merge and "
            "lightweight inline AOI. Achieves 85% of cost savings with "
            "75% of throughput gain."
        ),
        "num_operators":      4,
        "cycle_time_tmu":     395.0,
        "cost_per_unit_usd":  2.10,
        "throughput_uph":     58.2,
        "adopted_from_cost":    ["Merge stations 3+4", "Automate barcode scanning"],
        "adopted_from_quality": ["Inline AOI at exit", "Poka-yoke jig at cable-routing"],
        "trade_off_resolution": (
            "Merging stations accepted; dedicated rework loop deferred to "
            "Phase 2 capex cycle."
        ),
        "confidence_score": 0.87,
    }),
}


async def _stub_llm_call(
    messages:   List[ChatMessage],
    agent_name: str,
    model:      str,
    tier:       ModelTier,
) -> LlmCallResult:
    """Return a deterministic offline response for dev/test usage."""
    await asyncio.sleep(0)  # yield control to event loop

    content = _STUB_RESPONSES.get(
        agent_name,
        json.dumps({
            "summary": f"Offline stub response from {agent_name}",
            "content": (messages[-1].content[:100] if messages else ""),
        }),
    )

    prompt_tokens     = sum(len(m.content) for m in messages) // 4
    completion_tokens = len(content) // 4

    return LlmCallResult(
        content           = content,
        prompt_tokens     = prompt_tokens,
        completion_tokens = completion_tokens,
        model             = model,
        tier              = tier,
    )
