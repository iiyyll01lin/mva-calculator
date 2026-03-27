"""
cyberswarm/audit/backends/jsonl.py
──────────────────────────────────────────────────────────────────────────────
Append-only JSONL file backend for :class:`~cyberswarm.audit.chain.TamperEvidentAuditLog`.

This is the default recommendation for single-node deployments and edge
devices.  Each block is serialised as a single JSON line, making the file
human-readable and trivially importable by log-shipping agents (Promtail,
Fluentd, Vector, Splunk).

A log-shipping pipeline can tail this file and forward entries to any SIEM
for long-term compliant archival without requiring changes to the library.

Args:
    path: Absolute or relative path to the ``.jsonl`` file.
          Defaults to ``"audit_chain.jsonl"`` in the current working directory.
          The file is created on first write if it does not exist.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import List

from cyberswarm.audit.backend import AuditBackend
from cyberswarm.audit.chain import AuditChainEntry

logger = logging.getLogger(__name__)


class JsonlAuditBackend(AuditBackend):
    """
    Append-only JSONL file backend.

    Thread and asyncio safety
    ─────────────────────────
    File writes are dispatched to the default thread pool executor via
    ``asyncio.get_event_loop().run_in_executor`` to prevent blocking the
    event loop on disk I/O.  The audit engine serialises concurrent
    :meth:`persist` calls via its own lock, so this backend does not need
    internal synchronisation.
    """

    def __init__(self, path: str = "audit_chain.jsonl") -> None:
        self._path = path

    # ── AuditBackend interface ────────────────────────────────────────────────

    async def persist(self, entry: AuditChainEntry) -> None:
        """Append ``entry`` as a single JSON line to the JSONL file."""
        line = entry.to_json() + "\n"
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, self._sync_append, line)
        except OSError as exc:
            logger.warning("JsonlAuditBackend: write failed (%s): %s", self._path, exc)
            raise

    async def load_all(self) -> List[AuditChainEntry]:
        """
        Read and deserialise all blocks from the JSONL file.

        Returns an empty list if the file does not exist.
        """
        if not os.path.exists(self._path):
            return []
        loop = asyncio.get_event_loop()
        try:
            lines = await loop.run_in_executor(None, self._sync_read_lines)
        except OSError as exc:
            logger.warning("JsonlAuditBackend: read failed (%s): %s", self._path, exc)
            return []

        entries: List[AuditChainEntry] = []
        for i, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entries.append(AuditChainEntry.from_dict(data))
            except (json.JSONDecodeError, TypeError) as exc:
                logger.warning(
                    "JsonlAuditBackend: skipping malformed line %d: %s", i, exc
                )
        return entries

    # ── Private sync helpers (run in executor) ────────────────────────────────

    def _sync_append(self, line: str) -> None:
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(line)

    def _sync_read_lines(self) -> List[str]:
        with open(self._path, encoding="utf-8") as fh:
            return fh.readlines()
