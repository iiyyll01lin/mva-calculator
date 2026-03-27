"""
tests/test_audit.py — unit tests for the TamperEvidentAuditLog engine
and the built-in backends.
"""

import pytest

from cyberswarm.audit.backends.memory import InMemoryAuditBackend
from cyberswarm.audit.backends.jsonl import JsonlAuditBackend
from cyberswarm.audit.chain import AuditChainEntry, TamperEvidentAuditLog
from cyberswarm.audit.crypto import GENESIS_HASH


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def backend():
    return InMemoryAuditBackend()


@pytest.fixture
def audit(backend):
    return TamperEvidentAuditLog(backend=backend)


# ── TamperEvidentAuditLog ─────────────────────────────────────────────────────

class TestAuditLogRecord:
    async def test_returns_entry(self, audit):
        entry = await audit.record("TEST", "entity-1", {"foo": "bar"})
        assert isinstance(entry, AuditChainEntry)

    async def test_seq_increments(self, audit):
        e1 = await audit.record("EVT", "e1", {})
        e2 = await audit.record("EVT", "e2", {})
        e3 = await audit.record("EVT", "e3", {})
        assert e1.seq == 0
        assert e2.seq == 1
        assert e3.seq == 2

    async def test_genesis_block_previous_hash(self, audit):
        entry = await audit.record("FIRST", "e1", {})
        assert entry.previous_hash == GENESIS_HASH

    async def test_chaining(self, audit):
        e1 = await audit.record("E1", "id1", {"a": 1})
        e2 = await audit.record("E2", "id2", {"b": 2})
        assert e2.previous_hash == e1.block_hash

    async def test_block_hash_not_empty(self, audit):
        entry = await audit.record("EVT", "x", {"data": 123})
        assert entry.block_hash
        assert len(entry.block_hash) == 64

    async def test_signature_not_empty(self, audit):
        entry = await audit.record("EVT", "x", {"data": 123})
        assert entry.signature
        assert len(entry.signature) > 0

    async def test_current_seq_tracks_writes(self, audit):
        assert audit.current_seq == 0
        await audit.record("EVT", "a", {})
        await audit.record("EVT", "b", {})
        assert audit.current_seq == 2


class TestVerifyChain:
    async def test_empty_chain_valid(self, audit):
        assert await audit.verify_chain() is True

    async def test_single_block_valid(self, audit, backend):
        await audit.record("TEST", "e1", {"v": 1})
        assert await audit.verify_chain() is True

    async def test_multiple_blocks_valid(self, audit, backend):
        for i in range(5):
            await audit.record("EVT", f"e{i}", {"i": i})
        assert await audit.verify_chain() is True

    async def test_tampered_block_detected(self, audit, backend):
        await audit.record("ORIG", "e1", {"value": 42})
        # Tamper: overwrite the stored entry's payload
        stored = backend._entries[0]
        stored.payload["value"] = 999  # mutate stored entry
        assert await audit.verify_chain() is False

    async def test_broken_chain_link_detected(self, audit, backend):
        await audit.record("A", "e1", {})
        await audit.record("B", "e2", {})
        # Break the hash link
        backend._entries[1].previous_hash = "deadbeef" * 8
        assert await audit.verify_chain() is False


# ── InMemoryAuditBackend ──────────────────────────────────────────────────────

class TestInMemoryBackend:
    async def test_persist_and_load(self, backend):
        entry = AuditChainEntry(
            block_id="bid", seq=0, event_type="T", entity_id="e",
            payload={}, timestamp="2026-01-01T00:00:00+00:00",
            previous_hash=GENESIS_HASH, block_hash="abc", signature="sig",
        )
        await backend.persist(entry)
        loaded = await backend.load_all()
        assert len(loaded) == 1
        assert loaded[0].block_id == "bid"

    async def test_clear(self, backend):
        entry = AuditChainEntry(
            block_id="b", seq=0, event_type="T", entity_id="e",
            payload={}, timestamp="2026-01-01T00:00:00+00:00",
            previous_hash=GENESIS_HASH,
        )
        await backend.persist(entry)
        backend.clear()
        assert await backend.load_all() == []

    async def test_load_returns_copy(self, backend):
        entry = AuditChainEntry(
            block_id="b", seq=0, event_type="T", entity_id="e",
            payload={}, timestamp="ts", previous_hash=GENESIS_HASH,
        )
        await backend.persist(entry)
        a = await backend.load_all()
        b = await backend.load_all()
        assert a is not b  # different list objects


# ── JsonlAuditBackend ─────────────────────────────────────────────────────────

class TestJsonlBackend:
    async def test_roundtrip(self, tmp_path):
        path    = str(tmp_path / "audit.jsonl")
        backend = JsonlAuditBackend(path=path)
        entry   = AuditChainEntry(
            block_id="jb1", seq=0, event_type="JSONL_TEST", entity_id="e1",
            payload={"hello": "world"}, timestamp="2026-01-01T00:00:00+00:00",
            previous_hash=GENESIS_HASH, block_hash="aaa", signature="bbb",
        )
        await backend.persist(entry)
        loaded = await backend.load_all()
        assert len(loaded) == 1
        assert loaded[0].block_id == "jb1"
        assert loaded[0].payload == {"hello": "world"}

    async def test_empty_file_returns_empty(self, tmp_path):
        backend = JsonlAuditBackend(path=str(tmp_path / "new.jsonl"))
        assert await backend.load_all() == []

    async def test_missing_file_returns_empty(self, tmp_path):
        backend = JsonlAuditBackend(path=str(tmp_path / "nonexistent.jsonl"))
        assert await backend.load_all() == []

    async def test_full_integration_verify(self, tmp_path):
        """Full write cycle + chain verification via the JsonlAuditBackend."""
        path    = str(tmp_path / "chain.jsonl")
        backend = JsonlAuditBackend(path=path)
        log     = TamperEvidentAuditLog(backend=backend)

        for i in range(3):
            await log.record("STEP", f"entity-{i}", {"step": i})

        # New log instance reads from same file (simulates process restart)
        log2 = TamperEvidentAuditLog(backend=JsonlAuditBackend(path=path))
        assert await log2.verify_chain() is True
