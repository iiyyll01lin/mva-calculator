"""
backend/memory/alignment_store.py
────────────────────────────────────────────────────────────────────────────────
Alignment Knowledge Base — Enterprise MVA Platform v2.0.0

Stores CorrectionDirective objects that domain experts inject via Mission
Control.  All active directives are appended to Agent system prompts via
build_system_prompt(), ensuring the AI permanently remembers lessons learned.

Architecture:
  • Directives are stored in a JSON file (atomic-replace pattern, same as
    FileMemoryStore) for PoC durability without extra dependencies.
  • An in-memory dict enables O(1) synchronous reads during prompt building,
    so build_system_prompt() never blocks on disk I/O in the hot path.
  • A global _CACHE_VERSION counter is bumped on every directive write,
    invalidating functools.lru_cache keyed by (agent_name, version) so stale
    prompts are never served after a new rule is added.

Performance constraint satisfied:
  build_system_prompt() → _cached_build(agent_name, version) is O(1) when
  the version has not changed (cache hit).  Disk I/O only fires on the first
  call per version, never blocking the Swarm Debate hot-path.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Mapping, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Configuration — override via environment variables
# ────────────────────────────────────────────────────────────────────────────

ALIGNMENT_STORE_FILE: str = os.environ.get(
    "MVA_ALIGNMENT_STORE", "/tmp/mva_alignment/directives.json"
)

# Agent names recognised by the debate room.  Used to validate agent_target.
KNOWN_AGENT_TARGETS: frozenset[str] = frozenset({
    "CostOptimizationAgent",
    "QualityAndTimeAgent",
    "ConsensusJudgeAgent",
    "SupervisorAgent",
    "LaborTimeAgent",
    "BomAgent",
    "SimulationAgent",
    "GLOBAL",
})


# ────────────────────────────────────────────────────────────────────────────
# Data Model
# ────────────────────────────────────────────────────────────────────────────

class CorrectionDirective(BaseModel):
    """
    A single human-sourced alignment rule for the AI agent swarm.

    Experts create these via Mission Control when they reject an agent proposal
    and want to prevent the same mistake from recurring.  The directive text is
    injected verbatim into the agent's system prompt under
    "CRITICAL OPERATIONAL RULES".
    """

    id:             str  = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_target:   str  = Field(
        ...,
        description=(
            "Agent this rule targets: 'CostOptimizationAgent', "
            "'QualityAndTimeAgent', 'ConsensusJudgeAgent', or 'GLOBAL'."
        ),
    )
    directive_text: str  = Field(..., min_length=5)
    author_id:      str  = Field(..., description="user_id of the expert author.")
    timestamp:      str  = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    is_active:      bool = Field(default=True)

    model_config = {"extra": "ignore"}


# ────────────────────────────────────────────────────────────────────────────
# Cache Version Counter
# ────────────────────────────────────────────────────────────────────────────

_CACHE_VERSION: int = 0


def _bump_cache_version() -> None:
    """Increment the global version, invalidating all cached system prompts."""
    global _CACHE_VERSION
    _CACHE_VERSION += 1


def get_cache_version() -> int:
    """Return the current prompt-cache version (readable by tests)."""
    return _CACHE_VERSION


# ────────────────────────────────────────────────────────────────────────────
# AlignmentStore — Singleton persistence layer
# ────────────────────────────────────────────────────────────────────────────

class AlignmentStore:
    """
    Singleton alignment knowledge base.

    Maintains an in-memory dict of directives for zero-latency synchronous
    reads in build_system_prompt(), while persisting to disk asynchronously
    for durability across restarts.
    """

    _instance:    Optional["AlignmentStore"] = None
    _init_lock:   Optional[asyncio.Lock]     = None
    _write_lock:  Optional[asyncio.Lock]     = None

    def __init__(self) -> None:
        self._directives:   Dict[str, CorrectionDirective] = {}
        self._initialized:  bool = False

    # ── Singleton helpers ──────────────────────────────────────────────────

    @classmethod
    def get_instance(cls) -> "AlignmentStore":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def _get_init_lock(cls) -> asyncio.Lock:
        if cls._init_lock is None:
            cls._init_lock = asyncio.Lock()
        return cls._init_lock

    @classmethod
    def _get_write_lock(cls) -> asyncio.Lock:
        if cls._write_lock is None:
            cls._write_lock = asyncio.Lock()
        return cls._write_lock

    # ── Init ──────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """
        Load persisted directives from disk into the in-memory dict.

        Idempotent: subsequent calls are no-ops.  Call once from the FastAPI
        lifespan hook so the store is warm before the first request arrives.
        """
        if self._initialized:
            return
        async with self._get_init_lock():
            if self._initialized:
                return
            loop = asyncio.get_event_loop()
            try:
                raw: list = await loop.run_in_executor(None, self._sync_load)
                for item in raw:
                    d = CorrectionDirective.model_validate(item)
                    self._directives[d.id] = d
                logger.info(
                    "AlignmentStore: loaded %d directive(s) from %s",
                    len(self._directives), ALIGNMENT_STORE_FILE,
                )
            except FileNotFoundError:
                logger.info("AlignmentStore: no store file found — starting fresh.")
            except Exception as exc:
                logger.warning("AlignmentStore: load error (%s) — starting empty.", exc)
            self._initialized = True

    # ── Sync I/O helpers (run in executor) ────────────────────────────────

    def _sync_load(self) -> list:
        with open(ALIGNMENT_STORE_FILE, "r", encoding="utf-8") as fh:
            return json.load(fh)

    def _sync_save(self, snapshot: list) -> None:
        Path(ALIGNMENT_STORE_FILE).parent.mkdir(parents=True, exist_ok=True)
        tmp = ALIGNMENT_STORE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(snapshot, fh, indent=2, default=str)
        os.replace(tmp, ALIGNMENT_STORE_FILE)  # atomic rename

    # ── Writes ────────────────────────────────────────────────────────────

    async def add_directive(self, directive: CorrectionDirective) -> CorrectionDirective:
        """
        Persist *directive* and invalidate the system-prompt LRU cache.

        Protected by _write_lock so concurrent requests cannot produce a
        torn write to the JSON snapshot.

        Returns
        -------
        CorrectionDirective
            The stored directive (id / timestamp are already populated by
            the Pydantic default factories).
        """
        async with self._get_write_lock():
            self._directives[directive.id] = directive
            snapshot = [d.model_dump() for d in self._directives.values()]

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_save, snapshot)

        # Bump version → next build_system_prompt() call misses the LRU cache.
        _bump_cache_version()

        logger.info(
            "AlignmentStore: directive id=%s target='%s' added by author=%s",
            directive.id[:8], directive.agent_target, directive.author_id,
        )
        return directive

    async def deactivate(self, directive_id: str) -> Optional[CorrectionDirective]:
        """
        Soft-delete a directive by setting is_active=False, then persist.

        Returns the updated directive, or None if not found.
        """
        async with self._get_write_lock():
            d = self._directives.get(directive_id)
            if d is None:
                return None
            d.is_active = False
            snapshot = [dd.model_dump() for dd in self._directives.values()]

        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_save, snapshot)
        _bump_cache_version()
        return d

    # ── Reads (synchronous — called from the hot-path prompt builder) ──────

    def list_active(self, agent_target: str) -> List[CorrectionDirective]:
        """
        Return active directives targeting *agent_target* or 'GLOBAL'.

        Synchronous and lock-free — reads from the in-memory dict which is
        only mutated under _write_lock by add_directive / deactivate.
        O(n) on the number of total directives, which is bounded by the
        number of human corrections ever made (typically < 1 000).
        """
        return [
            d for d in self._directives.values()
            if d.is_active and (
                d.agent_target == agent_target or d.agent_target == "GLOBAL"
            )
        ]

    def list_all(self) -> List[CorrectionDirective]:
        """Return all directives regardless of is_active (for admin UI)."""
        return list(self._directives.values())


# ────────────────────────────────────────────────────────────────────────────
# Module-level singleton accessor
# ────────────────────────────────────────────────────────────────────────────

def get_alignment_store() -> AlignmentStore:
    """Return (and lazily create) the process-singleton AlignmentStore."""
    return AlignmentStore.get_instance()


# ────────────────────────────────────────────────────────────────────────────
# Dynamic System Prompt Builder — with LRU cache
# ────────────────────────────────────────────────────────────────────────────

# Base prompts are imported lazily inside _cached_build to avoid a circular
# import: debate_room → alignment_store → debate_room.
# Instead we pass the base prompt string as a parameter so lru_cache can key
# on it (strings are hashable).  The caller passes
# _BASE_PROMPTS[agent_name] from debate_room.
#
# Public API: build_system_prompt(agent_name, base_prompt) — called once per
# request; hits the LRU cache unless a new directive was added.

@functools.lru_cache(maxsize=64)
def _cached_build(agent_name: str, base_prompt: str, _v: int) -> str:
    """
    Inner cached builder keyed on (agent_name, base_prompt, cache_version).

    _v == get_cache_version() at call time; bumping _CACHE_VERSION in
    add_directive() forces a cache miss for every agent_name on the next call.

    Never call this directly — use build_system_prompt() instead.
    """
    store      = get_alignment_store()
    directives = store.list_active(agent_name)

    if not directives:
        return base_prompt

    rules = "\n".join(f"  - {d.directive_text}" for d in directives)
    return (
        base_prompt
        + "\n\n--- CRITICAL OPERATIONAL RULES (Administrator Mandated) ---\n"
        + "The following rules OVERRIDE your default reasoning. You MUST comply:\n"
        + rules
        + "\n--- END OF CRITICAL OPERATIONAL RULES ---"
    )


def build_system_prompt(agent_name: str, base_prompt: str) -> str:
    """
    Build the dynamic system prompt for *agent_name*.

    Queries the AlignmentStore for active CorrectionDirectives targeting
    *agent_name* or 'GLOBAL', and appends them as an immutable
    "CRITICAL OPERATIONAL RULES" section after *base_prompt*.

    The result is LRU-cached keyed on (agent_name, base_prompt, version).
    Adding any new directive via AlignmentStore.add_directive() increments
    the version, causing a cache miss on the very next call — so the agent
    swarm always operates under the most-current set of rules without any
    manual cache invalidation by the caller.

    Args:
        agent_name:  Canonical agent name string, e.g. "CostOptimizationAgent".
        base_prompt: The static base system prompt string for this agent.

    Returns:
        Complete system prompt string, with directives appended if any exist.
    """
    return _cached_build(agent_name, base_prompt, get_cache_version())
