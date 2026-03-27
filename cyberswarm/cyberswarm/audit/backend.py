"""
cyberswarm/audit/backend.py
──────────────────────────────────────────────────────────────────────────────
Plugin interface for audit log storage backends.

The :class:`TamperEvidentAuditLog` engine handles the *cryptographic* layer
(Ed25519 signing + SHA-256 hash chaining) independently of storage.  It
delegates all I/O to an :class:`AuditBackend` implementation that you inject
at construction time.

Built-in backends (``cyberswarm.audit.backends``)
──────────────────────────────────────────────────
• :class:`~cyberswarm.audit.backends.jsonl.JsonlAuditBackend`  — append-only
  JSONL file (default, zero extra dependencies)
• :class:`~cyberswarm.audit.backends.memory.InMemoryAuditBackend`  — in-memory
  list (useful in unit tests)

Custom backend example (PostgreSQL)
────────────────────────────────────
    import asyncpg
    from cyberswarm.audit.backend import AuditBackend
    from cyberswarm.audit.chain  import AuditChainEntry

    class PostgresAuditBackend(AuditBackend):
        def __init__(self, dsn: str):
            self._dsn = dsn
            self._pool = None

        async def _get_pool(self):
            if self._pool is None:
                self._pool = await asyncpg.create_pool(self._dsn)
            return self._pool

        async def persist(self, entry: AuditChainEntry) -> None:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    \"\"\"INSERT INTO audit_chain
                           (block_id, seq, event_type, entity_id,
                            payload, timestamp, previous_hash,
                            block_hash, signature)
                       VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)\"\"\",
                    entry.block_id, entry.seq, entry.event_type,
                    entry.entity_id, entry.to_json(), entry.timestamp,
                    entry.previous_hash, entry.block_hash, entry.signature,
                )

        async def load_all(self) -> list[AuditChainEntry]:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT payload FROM audit_chain ORDER BY seq"
                )
            import json
            return [AuditChainEntry(**json.loads(r["payload"])) for r in rows]
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from cyberswarm.audit.chain import AuditChainEntry


class AuditBackend(ABC):
    """
    Abstract storage plugin for :class:`~cyberswarm.audit.chain.TamperEvidentAuditLog`.

    Implement :meth:`persist` and :meth:`load_all`; the audit engine handles
    all cryptographic concerns (signing, hashing, chain sequencing).

    Thread / asyncio safety
    ───────────────────────
    The audit engine serialises calls to ``persist`` via an ``asyncio.Lock``.
    Backend implementations are therefore guaranteed to receive sequential,
    non-concurrent calls; you do not need internal locking.
    """

    @abstractmethod
    async def persist(self, entry: "AuditChainEntry") -> None:
        """
        Durably store a single :class:`~cyberswarm.audit.chain.AuditChainEntry`.

        Called by the audit engine after signing and hashing.
        Implementations should raise on unrecoverable storage failures so
        the engine can log the error (entries are never silently dropped
        by the engine itself).

        Args:
            entry: Fully populated, signed, and hashed chain block.
        """

    @abstractmethod
    async def load_all(self) -> List["AuditChainEntry"]:
        """
        Return all persisted chain entries in ascending ``seq`` order.

        Used by :meth:`~cyberswarm.audit.chain.TamperEvidentAuditLog.verify_chain`
        to reconstruct and validate the full chain.

        Returns:
            List of :class:`~cyberswarm.audit.chain.AuditChainEntry` objects,
            sorted by ``seq`` ascending.
        """
