"""
cyberswarm/audit/backends/memory.py
──────────────────────────────────────────────────────────────────────────────
In-memory :class:`~cyberswarm.audit.backend.AuditBackend` implementation.

Designed for unit tests and REPL experimentation.  No I/O is performed;
entries are stored in a plain Python list and discarded when the process exits.

Usage::

    from cyberswarm.audit.chain    import TamperEvidentAuditLog
    from cyberswarm.audit.backends import InMemoryAuditBackend

    backend = InMemoryAuditBackend()
    audit   = TamperEvidentAuditLog(backend=backend)

    await audit.record("TEST_EVENT", "entity-1", {"foo": "bar"})

    all_entries = await backend.load_all()
    assert len(all_entries) == 1
"""

from __future__ import annotations

from typing import List

from cyberswarm.audit.backend import AuditBackend
from cyberswarm.audit.chain import AuditChainEntry


class InMemoryAuditBackend(AuditBackend):
    """In-memory append-only backend (zero I/O — for tests and local dev)."""

    def __init__(self) -> None:
        self._entries: List[AuditChainEntry] = []

    async def persist(self, entry: AuditChainEntry) -> None:
        """Append a deepcopy-safe reference to the entry list."""
        self._entries.append(entry)

    async def load_all(self) -> List[AuditChainEntry]:
        """Return all stored entries in insertion (seq-ascending) order."""
        return list(self._entries)

    def clear(self) -> None:
        """Reset the store (useful between test cases)."""
        self._entries.clear()
