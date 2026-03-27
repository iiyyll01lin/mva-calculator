"""
cyberswarm/audit/chain.py
──────────────────────────────────────────────────────────────────────────────
Tamper-Evident Audit Log — blockchain-style SHA-256 hash chaining
with Ed25519 digital signatures.

Architecture
────────────
Each new block records:

  • ``previous_hash``  — SHA-256 of the previous block (links blocks)
  • ``block_hash``     — SHA-256 of this block (excluding block_hash itself)
  • ``signature``      — Ed25519 sig over canonical JSON (excl. both hash fields)

Any retroactive modification to an existing block is detectable by calling
:meth:`TamperEvidentAuditLog.verify_chain`, which recomputes every hash and
signature from scratch.

The engine is **storage-agnostic**: it delegates all I/O to an injected
:class:`~cyberswarm.audit.backend.AuditBackend` plugin.

Usage
─────
    from cyberswarm.audit.chain    import TamperEvidentAuditLog
    from cyberswarm.audit.backends import JsonlAuditBackend

    log = TamperEvidentAuditLog(backend=JsonlAuditBackend("audit.jsonl"))

    await log.record(
        event_type = "DEBATE_CONSENSUS",
        entity_id  = consensus.consensus_id,
        payload    = consensus.model_dump(),
    )

    ok = await log.verify_chain()
    assert ok, "Chain integrity violated!"
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from cyberswarm.audit.backend import AuditBackend
from cyberswarm.audit.crypto import (
    GENESIS_HASH,
    hash_payload,
    sign_payload,
    verify_payload,
)

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Chain block schema
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class AuditChainEntry:
    """
    A single block in the append-only, tamper-evident audit chain.

    Attributes
    ----------
    block_id:       UUID4 identifier for this block.
    seq:            Monotonically increasing block index (0-based).
    event_type:     Application-level event label, e.g. ``"DEBATE_CONSENSUS"``.
    entity_id:      The primary entity this block records (e.g. ``proposal_id``).
    payload:        Arbitrary dict of application data to be preserved.
    timestamp:      ISO-8601 UTC creation time.
    previous_hash:  SHA-256 of the preceding block's canonical JSON.
    block_hash:     SHA-256 of this block (computed; excluded from hash input).
    signature:      Ed25519 signature (computed; excluded from hash & sig input).
    """

    block_id:      str
    seq:           int
    event_type:    str
    entity_id:     str
    payload:       Dict[str, Any]
    timestamp:     str
    previous_hash: str
    block_hash:    str = ""
    signature:     str = ""

    # ── Canonical helpers ─────────────────────────────────────────────────────

    def _hashable_dict(self) -> Dict[str, Any]:
        """Dict used when computing block_hash (excludes block_hash & signature)."""
        d = asdict(self)
        d.pop("block_hash", None)
        d.pop("signature", None)
        return d

    def _signable_dict(self) -> Dict[str, Any]:
        """Dict used when signing (same as _hashable_dict)."""
        return self._hashable_dict()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), default=str)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AuditChainEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ────────────────────────────────────────────────────────────────────────────
# Audit log engine
# ────────────────────────────────────────────────────────────────────────────

class TamperEvidentAuditLog:
    """
    Async-safe, append-only audit log with blockchain-style integrity.

    The engine holds all cryptographic state in memory and delegates
    persistence to the injected :class:`~cyberswarm.audit.backend.AuditBackend`.

    Multiple instances can coexist in the same process if they are wired to
    different backends (e.g. one per tenant in a multi-tenant application).

    Example::

        from cyberswarm.audit.chain    import TamperEvidentAuditLog
        from cyberswarm.audit.backends import JsonlAuditBackend

        audit = TamperEvidentAuditLog(JsonlAuditBackend("/var/log/audit.jsonl"))
        swarm = MySwarm(proposer=..., critic=..., judge=..., audit_log=audit)
    """

    def __init__(self, backend: AuditBackend) -> None:
        self._backend:   AuditBackend         = backend
        self._lock:      asyncio.Lock         = asyncio.Lock()
        self._seq:       int                  = 0
        self._last_hash: str                  = GENESIS_HASH

    # ── Public API ────────────────────────────────────────────────────────────

    async def record(
        self,
        event_type: str,
        entity_id:  str,
        payload:    Dict[str, Any],
    ) -> AuditChainEntry:
        """
        Append a new block to the audit chain.

        The block is signed with the process-level Ed25519 key via
        :func:`~cyberswarm.audit.crypto.sign_payload` and persisted through
        :meth:`~cyberswarm.audit.backend.AuditBackend.persist`.

        Args:
            event_type: Application-level label, e.g. ``"DEBATE_CONSENSUS"``.
            entity_id:  Primary entity involved (consensus_id, proposal_id …).
            payload:    Arbitrary JSON-serialisable application data.

        Returns:
            The committed :class:`AuditChainEntry` (useful for assertions
            in tests).
        """
        async with self._lock:
            entry = AuditChainEntry(
                block_id      = str(uuid.uuid4()),
                seq           = self._seq,
                event_type    = event_type,
                entity_id     = entity_id,
                payload       = payload,
                timestamp     = datetime.now(timezone.utc).isoformat(),
                previous_hash = self._last_hash,
            )

            # 1. Compute block hash (over all fields except block_hash & signature)
            entry.block_hash = hash_payload(entry._hashable_dict())
            # 2. Sign the block (same canonical input as the hash)
            entry.signature  = sign_payload(entry._signable_dict())

            self._last_hash = entry.block_hash
            self._seq       += 1

        logger.debug(
            "AuditChain: block seq=%d type=%s entity=%s hash=%.12s",
            entry.seq, entry.event_type, entry.entity_id, entry.block_hash,
        )

        # Persist outside the lock so I/O does not block other writers.
        try:
            await self._backend.persist(entry)
        except Exception as exc:
            logger.warning("AuditBackend.persist failed for seq=%d: %s", entry.seq, exc)

        return entry

    async def verify_chain(self) -> bool:
        """
        Recompute and validate every hash and signature in the stored chain.

        Loads all blocks through :meth:`~cyberswarm.audit.backend.AuditBackend.load_all`
        and checks:

        1. ``previous_hash`` linkage is unbroken (block N's ``block_hash`` ==
           block N+1's ``previous_hash``).
        2. Each block's ``block_hash`` matches SHA-256 of its canonical content.
        3. Each block's ``signature`` is a valid Ed25519 sig over the block.

        Returns
        -------
        bool
            ``True`` if the entire chain is intact and unmodified.

        Note
        ----
        Signature verification uses the **current** process key.  Blocks
        signed by a previous process key (e.g. after a restart) will fail
        signature checks unless the :class:`~cyberswarm.audit.crypto.KeyManager`
        is loaded with a persistent key.
        """
        entries: List[AuditChainEntry] = await self._backend.load_all()
        if not entries:
            return True

        prev_hash = GENESIS_HASH
        for entry in entries:
            # Check linkage
            if entry.previous_hash != prev_hash:
                logger.error(
                    "Chain broken at seq=%d: expected prev_hash=%.12s got %.12s",
                    entry.seq, prev_hash, entry.previous_hash,
                )
                return False

            # Recompute block hash
            expected_hash = hash_payload(entry._hashable_dict())
            if entry.block_hash != expected_hash:
                logger.error(
                    "Hash mismatch at seq=%d: stored=%.12s computed=%.12s",
                    entry.seq, entry.block_hash, expected_hash,
                )
                return False

            # Verify signature
            if not verify_payload(entry._signable_dict(), entry.signature):
                logger.error(
                    "Signature invalid at seq=%d entity=%s",
                    entry.seq, entry.entity_id,
                )
                return False

            prev_hash = entry.block_hash

        logger.info("AuditChain verified: %d blocks intact.", len(entries))
        return True

    @property
    def current_seq(self) -> int:
        """Number of blocks recorded in this session (not loaded from storage)."""
        return self._seq
