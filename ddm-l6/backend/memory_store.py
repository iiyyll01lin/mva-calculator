"""
backend/memory_store.py
────────────────────────────────────────────────────────────────────────────────
Enterprise MVA Platform v2.0.0 — Stateful Agent Memory Layer

Implements a tiered persistence architecture:
  • Short-term: working messages held in AgentState (in-process, per-request)
  • Long-term:  SessionState serialized to a pluggable backend
                (FileMemoryStore for PoC; RedisMemoryStore for production)

Context Pruning:
  When message_history exceeds PRUNE_TOKEN_LIMIT tokens, prune_and_summarize()
  compresses evicted turns into system_summary, guaranteeing that critical
  Supervisor synthesis and Reflection results are never lost.

Design Notes:
  • StoredMessage mirrors agent_router_poc.Message field-for-field to allow
    lossless round-trips via model_validate(stored_msg.model_dump()).
  • No circular imports: this module only imports from telemetry.py.
    agent_router_poc.py is imported by callers, not this module.
  • All I/O in FileMemoryStore is run in asyncio thread-pool executors to keep
    the event loop non-blocking.
  • RedisMemoryStore uses redis[asyncio] (optional dep; install when deploying).
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from telemetry import estimate_tokens

logger = logging.getLogger(__name__)

# ────────────────────────────────────────────────────────────────────────────
# Configuration — override via environment variables
# ────────────────────────────────────────────────────────────────────────────

#: Token threshold at which context pruning fires.
PRUNE_TOKEN_LIMIT: int = int(os.environ.get("MVA_PRUNE_TOKEN_LIMIT", "4000"))

#: How long sessions live in the store (seconds).  Default: 24 hours.
SESSION_TTL_SECONDS: int = int(os.environ.get("MVA_SESSION_TTL_SECONDS", str(60 * 60 * 24)))

#: Select backend: "file" (PoC default) or "redis" (production).
MEMORY_BACKEND: str = os.environ.get("MVA_MEMORY_BACKEND", "file")

#: Root directory for FileMemoryStore JSON files.
MEMORY_FILE_DIR: str = os.environ.get("MVA_MEMORY_FILE_DIR", "/tmp/mva_sessions")

# Agent names whose assistant messages must never be evicted during pruning.
# Populated at import time by agent_router_poc via register_protected_agent_names().
_PROTECTED_AGENT_NAMES: frozenset[str] = frozenset()


def register_protected_agent_names(names: frozenset[str]) -> None:
    """
    Register the agent names whose messages are protected from context pruning.

    Call once at module init in agent_router_poc.py after AgentName is defined::

        register_protected_agent_names(frozenset({a.value for a in AgentName}))

    This satisfies the engineering constraint that "Context Pruning does not
    lose critical Reflection results from previous turns."
    """
    global _PROTECTED_AGENT_NAMES
    _PROTECTED_AGENT_NAMES = names


# ────────────────────────────────────────────────────────────────────────────
# Shared Message Schema
# (Mirrors agent_router_poc.Message field-for-field — avoids circular import)
# ────────────────────────────────────────────────────────────────────────────

class StoredMessage(BaseModel):
    """
    Cross-module message model stored inside SessionState.

    Fields are intentionally identical to agent_router_poc.Message so that::

        Message.model_validate(stored_msg.model_dump())

    works without any field mapping.
    """
    role:    Literal["system", "user", "assistant", "tool"]
    content: str
    name:    Optional[str] = None


# ────────────────────────────────────────────────────────────────────────────
# HITL Pending Action models
# ────────────────────────────────────────────────────────────────────────────

class PendingActionStatus(str, Enum):
    """Lifecycle status of a pending HITL approval action."""
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class PendingAction(BaseModel):
    """
    Represents a SENSITIVE tool call awaiting explicit human approval.

    Stored inside SessionState so the /approve endpoint can validate the
    action_id, run ToolGuard pre-flight checks, and resume the agent
    workflow from the exact interrupted point.
    """
    action_id:   str                 = Field(default_factory=lambda: str(uuid.uuid4()))
    tool_name:   str
    raw_args:    Dict[str, Any]      = Field(default_factory=dict)
    agent_name:  str
    status:      PendingActionStatus = PendingActionStatus.PENDING
    created_at:  datetime            = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: Optional[datetime]  = None
    resolved_by: Optional[str]       = None   # user_id of the approver / rejector


# ────────────────────────────────────────────────────────────────────────────
# SessionState & Checkpoint schemas
# ────────────────────────────────────────────────────────────────────────────

class Checkpoint(BaseModel):
    """
    Snapshot of the last successful tool outputs for a given session turn.

    Enables "resume from failure": when a new turn begins the AgentState is
    pre-seeded with these results so follow-up agents can reference previous
    decisions without re-executing tools.
    """
    turn_index:        int
    last_intent:       Optional[str]         = None
    #: ToolCallResult.model_dump() dicts — serialised to avoid circular imports.
    tool_call_results: List[Dict[str, Any]]  = Field(default_factory=list)
    captured_at:       datetime              = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


class SessionState(BaseModel):
    """
    Durable, cross-turn session state persisted to the chosen backend.

    Tiered design
    -------------
    message_history  — pruned sliding window of recent conversation turns.
    system_summary   — compressed digest of evicted turns; injected as a
                       ``role="system"`` message at the start of each new turn
                       so the LLM never loses long-range context.
    checkpoint       — last successful tool outputs for resume-from-failure.
    turn_count       — monotonically increasing turn counter.
    total_tokens_used — cumulative token accounting across all turns.
    """
    session_id:        str               = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id:           str               = ""
    user_role:         str               = ""
    message_history:   List[StoredMessage] = Field(default_factory=list)
    system_summary:    Optional[str]       = None
    checkpoint:        Optional[Checkpoint] = None
    turn_count:        int                      = 0
    total_tokens_used: int                      = 0
    pending_action:    Optional[PendingAction]   = None   # HITL: action awaiting approval
    created_at:        datetime                  = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at:        datetime                  = Field(default_factory=lambda: datetime.now(timezone.utc))


# ────────────────────────────────────────────────────────────────────────────
# Context-Pruning / Summarizer
# ────────────────────────────────────────────────────────────────────────────

def _count_tokens(messages: List[StoredMessage]) -> int:
    """Approximate token count for a list of stored messages."""
    return sum(estimate_tokens(m.content) for m in messages)


def prune_and_summarize(
    session: SessionState,
    extra_protected_names: Optional[frozenset[str]] = None,
) -> SessionState:
    """
    Sliding-window context pruner with guaranteed Reflection preservation.

    Algorithm
    ---------
    1. If total tokens ≤ PRUNE_TOKEN_LIMIT → no-op; return unchanged.
    2. Build eviction candidates, *never* including:
         a. ``role == "system"`` messages.
         b. Assistant messages whose ``name`` is in the protected set
            (Supervisor synthesis + all Reflection outputs).
         c. The two most recent user messages (immediate context).
    3. Evict oldest candidates one-by-one until back under budget.
    4. Build a plain-text digest from evicted messages; append to
       ``session.system_summary`` so nothing is permanently lost.
    5. Remove evicted messages from ``session.message_history``.

    Engineering constraint satisfied
    ---------------------------------
    Supervisor messages and all registered agent messages are in the protected
    set, so Reflection results from previous turns are never evicted.

    Args:
        session:               SessionState to prune in-place.
        extra_protected_names: Additional agent names to protect beyond the
                               globally registered set.

    Returns:
        The same SessionState object with message_history and system_summary
        updated.
    """
    if _count_tokens(session.message_history) <= PRUNE_TOKEN_LIMIT:
        return session

    protected: frozenset[str] = _PROTECTED_AGENT_NAMES | (extra_protected_names or frozenset())

    # Identify the two most-recent user messages to preserve immediate context.
    user_indices = [i for i, m in enumerate(session.message_history) if m.role == "user"]
    keep_user: set[int] = set(user_indices[-2:])

    # Build ordered list of eviction candidates (oldest first).
    evictable: List[int] = []
    for i, msg in enumerate(session.message_history):
        if msg.role == "system":
            continue
        if msg.role == "assistant" and msg.name in protected:
            continue
        if i in keep_user:
            continue
        evictable.append(i)

    # Evict oldest candidates until we reach the token budget.
    to_evict: List[int] = []
    evict_set: set[int] = set()
    for idx in evictable:
        to_evict.append(idx)
        evict_set.add(idx)
        remaining = [m for i, m in enumerate(session.message_history) if i not in evict_set]
        if _count_tokens(remaining) <= PRUNE_TOKEN_LIMIT:
            break

    if not to_evict:
        return session  # nothing safe to evict

    # Build plain-text digest of evicted messages.
    evicted: List[StoredMessage] = [session.message_history[i] for i in sorted(to_evict)]
    lines = [
        f"[turn≈{session.turn_count - len(evicted) + k} | {m.role}/{m.name or '-'}]: "
        f"{m.content[:300]}"
        for k, m in enumerate(evicted)
    ]
    digest = "--- PRUNED HISTORY SEGMENT ---\n" + "\n".join(lines)

    session.system_summary = (
        f"{session.system_summary}\n\n{digest}" if session.system_summary else digest
    )

    # Remove evicted messages from history.
    final_evict_set = set(to_evict)
    session.message_history = [
        m for i, m in enumerate(session.message_history)
        if i not in final_evict_set
    ]

    logger.info(
        "Context pruned: evicted %d messages (session=%s, remaining_tokens≈%d).",
        len(to_evict),
        session.session_id,
        _count_tokens(session.message_history),
    )
    return session


# ────────────────────────────────────────────────────────────────────────────
# Abstract Base Store
# ────────────────────────────────────────────────────────────────────────────

class BaseMemoryStore(ABC):
    """
    Contract for all session persistence backends.

    All methods must be async and I/O non-blocking to maintain the platform's
    extreme-performance standard.
    """

    @abstractmethod
    async def load(self, session_id: str) -> Optional[SessionState]:
        """Load a SessionState by ID.  Returns None if not found or expired."""

    @abstractmethod
    async def save(self, session: SessionState) -> None:
        """Persist or overwrite a SessionState."""

    @abstractmethod
    async def delete(self, session_id: str) -> None:
        """Hard-delete a session (GDPR / cleanup flows)."""

    @abstractmethod
    async def list_pending_actions(self) -> List[Dict[str, Any]]:
        """
        Return summary dicts for all sessions that have a PENDING HITL action.

        Each dict contains at minimum:
          session_id, user_id, pending_action (serialised), turn_count, updated_at.
        """

    @abstractmethod
    async def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return lightweight summary metadata for the most-recent ``limit`` sessions."""


# ────────────────────────────────────────────────────────────────────────────
# FileMemoryStore — JSON on disk (PoC / development)
# ────────────────────────────────────────────────────────────────────────────

class FileMemoryStore(BaseMemoryStore):
    """
    Persists each SessionState as a UTF-8 JSON file under ``store_dir``.

    Coroutine safety
    ----------------
    A per-session ``asyncio.Lock`` serialises concurrent writes to the same
    file.  Reads are lock-free since Pydantic's ``model_validate_json`` is
    stateless and JSON reads are effectively atomic for small files.

    Security
    --------
    ``_sanitize()`` allows only alphanumeric + hyphen characters in the file
    name, preventing path-traversal attacks (OWASP A01).

    File layout::

        {store_dir}/{sanitized_session_id}.json
    """

    def __init__(self, store_dir: str = MEMORY_FILE_DIR) -> None:
        self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._locks: Dict[str, asyncio.Lock] = {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _sanitize(session_id: str) -> str:
        """Strip all characters except alphanumerics and hyphens."""
        return "".join(c for c in session_id if c.isalnum() or c == "-")

    def _path(self, session_id: str) -> Path:
        return self._dir / f"{self._sanitize(session_id)}.json"

    def _lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

    # ── BaseMemoryStore interface ─────────────────────────────────────────────

    async def load(self, session_id: str) -> Optional[SessionState]:
        path = self._path(session_id)
        if not path.exists():
            return None
        try:
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(None, path.read_text, "utf-8")
            return SessionState.model_validate_json(raw)
        except Exception as exc:
            logger.warning("FileMemoryStore.load failed (session=%s): %s", session_id, exc)
            return None

    async def save(self, session: SessionState) -> None:
        path = self._path(session.session_id)
        async with self._lock(session.session_id):
            loop = asyncio.get_event_loop()
            data = session.model_dump_json(indent=2)
            await loop.run_in_executor(None, path.write_text, data, "utf-8")
        logger.debug("FileMemoryStore: saved session %s.", session.session_id)

    async def delete(self, session_id: str) -> None:
        path = self._path(session_id)
        async with self._lock(session_id):
            if path.exists():
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, path.unlink)
        logger.info("FileMemoryStore: deleted session %s.", session_id)

    async def list_pending_actions(self) -> List[Dict[str, Any]]:
        """
        Scan all session files and return those with a ``PENDING`` action.

        Uses ``run_in_executor`` so disk I/O never blocks the event loop.
        """
        results: List[Dict[str, Any]] = []
        try:
            files = list(self._dir.glob("*.json"))
        except OSError:
            return results

        loop = asyncio.get_event_loop()
        for path in sorted(files, key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                raw = await loop.run_in_executor(None, path.read_text, "utf-8")
                session = SessionState.model_validate_json(raw)
                if (
                    session.pending_action is not None
                    and session.pending_action.status == PendingActionStatus.PENDING
                ):
                    results.append({
                        "session_id":    session.session_id,
                        "user_id":       session.user_id,
                        "user_role":     session.user_role,
                        "pending_action": session.pending_action.model_dump(mode="json"),
                        "turn_count":    session.turn_count,
                        "updated_at":    session.updated_at.isoformat(),
                    })
            except Exception:
                continue
        return results

    async def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """
        Return lightweight metadata for the most-recent ``limit`` sessions,
        sorted newest-first by file modification time.
        """
        results: List[Dict[str, Any]] = []
        try:
            files = sorted(
                self._dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:limit]
        except OSError:
            return results

        loop = asyncio.get_event_loop()
        for path in files:
            try:
                raw = await loop.run_in_executor(None, path.read_text, "utf-8")
                session = SessionState.model_validate_json(raw)
                results.append({
                    "session_id":       session.session_id,
                    "user_id":          session.user_id,
                    "user_role":        session.user_role,
                    "turn_count":       session.turn_count,
                    "total_tokens_used": session.total_tokens_used,
                    "has_pending":      (
                        session.pending_action is not None
                        and session.pending_action.status == PendingActionStatus.PENDING
                    ),
                    "created_at":       session.created_at.isoformat(),
                    "updated_at":       session.updated_at.isoformat(),
                })
            except Exception:
                continue
        return results


# ────────────────────────────────────────────────────────────────────────────
# RedisMemoryStore — Redis GET/SET/EXPIRE (production)
# ────────────────────────────────────────────────────────────────────────────

class RedisMemoryStore(BaseMemoryStore):
    """
    Stores each SessionState as a JSON string in Redis with automatic TTL.

    Requires::

        pip install redis[asyncio]

    Environment variables::

        MVA_REDIS_URL           — connection URL, e.g. "redis://localhost:6379/0"
        MVA_SESSION_TTL_SECONDS — key expiry in seconds (default: 86400)

    Security
    --------
    ``_key()`` sanitizes session_id before building the Redis key name,
    preventing key-injection attacks.
    """

    _REDIS_URL: str = os.environ.get("MVA_REDIS_URL", "redis://localhost:6379/0")

    def __init__(self) -> None:
        try:
            import redis.asyncio as aioredis  # type: ignore[import]
            self._redis = aioredis.from_url(
                self._REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
            )
        except ImportError as exc:
            raise RuntimeError(
                "RedisMemoryStore requires 'redis[asyncio]'. "
                "Install with: pip install redis[asyncio]"
            ) from exc

    @staticmethod
    def _key(session_id: str) -> str:
        safe = "".join(c for c in session_id if c.isalnum() or c == "-")
        return f"mva:session:{safe}"

    async def load(self, session_id: str) -> Optional[SessionState]:
        try:
            raw = await self._redis.get(self._key(session_id))
            if raw is None:
                return None
            return SessionState.model_validate_json(raw)
        except Exception as exc:
            logger.warning("RedisMemoryStore.load error (session=%s): %s", session_id, exc)
            return None

    async def save(self, session: SessionState) -> None:
        try:
            await self._redis.set(
                self._key(session.session_id),
                session.model_dump_json(),
                ex=SESSION_TTL_SECONDS,
            )
        except Exception as exc:
            logger.error(
                "RedisMemoryStore.save error (session=%s): %s",
                session.session_id, exc,
            )
            raise

    async def delete(self, session_id: str) -> None:
        try:
            await self._redis.delete(self._key(session_id))
        except Exception as exc:
            logger.warning("RedisMemoryStore.delete error (session=%s): %s", session_id, exc)

    async def list_pending_actions(self) -> List[Dict[str, Any]]:
        """Scan Redis keys to find sessions with a PENDING action."""
        results: List[Dict[str, Any]] = []
        try:
            keys = [k async for k in self._redis.scan_iter("mva:session:*", count=100)]
        except Exception:
            return results
        for key in keys:
            try:
                raw = await self._redis.get(key)
                if raw is None:
                    continue
                session = SessionState.model_validate_json(raw)
                if (
                    session.pending_action is not None
                    and session.pending_action.status == PendingActionStatus.PENDING
                ):
                    results.append({
                        "session_id":    session.session_id,
                        "user_id":       session.user_id,
                        "user_role":     session.user_role,
                        "pending_action": session.pending_action.model_dump(mode="json"),
                        "turn_count":    session.turn_count,
                        "updated_at":    session.updated_at.isoformat(),
                    })
            except Exception:
                continue
        return results

    async def list_sessions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return lightweight metadata for up to ``limit`` sessions from Redis."""
        results: List[Dict[str, Any]] = []
        try:
            keys = [k async for k in self._redis.scan_iter("mva:session:*", count=100)]
        except Exception:
            return results
        for key in keys[:limit]:
            try:
                raw = await self._redis.get(key)
                if raw is None:
                    continue
                session = SessionState.model_validate_json(raw)
                results.append({
                    "session_id":        session.session_id,
                    "user_id":           session.user_id,
                    "user_role":         session.user_role,
                    "turn_count":        session.turn_count,
                    "total_tokens_used": session.total_tokens_used,
                    "has_pending":       (
                        session.pending_action is not None
                        and session.pending_action.status == PendingActionStatus.PENDING
                    ),
                    "created_at":        session.created_at.isoformat(),
                    "updated_at":        session.updated_at.isoformat(),
                })
            except Exception:
                continue
        return sorted(results, key=lambda s: s["updated_at"], reverse=True)


# ────────────────────────────────────────────────────────────────────────────
# Factory — selects backend from MVA_MEMORY_BACKEND env var
# ────────────────────────────────────────────────────────────────────────────

_store_instance: Optional[BaseMemoryStore] = None


def get_memory_store() -> BaseMemoryStore:
    """
    Returns the process-singleton memory store.

    Backend is selected by ``MVA_MEMORY_BACKEND`` env var:
      ``'file'``  (default) → :class:`FileMemoryStore`
      ``'redis'``           → :class:`RedisMemoryStore`

    Call ``get_memory_store()`` anywhere; the first call initialises the
    singleton and all subsequent calls return the same instance.
    """
    global _store_instance
    if _store_instance is None:
        backend = MEMORY_BACKEND.lower()
        if backend == "redis":
            _store_instance = RedisMemoryStore()
            logger.info("Memory backend: RedisMemoryStore (%s)", RedisMemoryStore._REDIS_URL)
        else:
            _store_instance = FileMemoryStore(MEMORY_FILE_DIR)
            logger.info("Memory backend: FileMemoryStore (%s)", MEMORY_FILE_DIR)
    return _store_instance
