"""
tests/test_cryptographic_provenance.py
───────────────────────────────────────────────────────────────────────────────
Unit + Red-Team tests for the Cryptographic Provenance Engine.

Covers:
  1. sign_and_verify_proposal  — happy path, signature validates successfully.
  2. red_team_tampered_payload — mutating a field after signing must invalidate sig.
  3. audit_chain_integrity     — 3 blocks chained; verify_chain returns SECURE.
  4. audit_chain_tampering     — manually mutate a JSONL block; detects COMPROMISED.

Run with:
    pytest backend/tests_most/test_cryptographic_provenance.py -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

# ── Make sure the backend package root is on PYTHONPATH ──────────────────────
_BACKEND = os.path.join(os.path.dirname(__file__), "..")
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

# ── Imports under test ───────────────────────────────────────────────────────
from security.provenance import (
    KeyManager,
    sign_payload,
    verify_payload,
    hash_payload,
    GENESIS_HASH,
)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Happy-path: sign → verify
# ═══════════════════════════════════════════════════════════════════════════════

def test_sign_and_verify_proposal():
    """
    A fresh signature over a simulated EmergencyProposal dict must verify
    correctly with the same process-level public key.
    """
    proposal = {
        "proposal_id":       "prop-test-001",
        "session_id":        "session-abc",
        "machine_id":        "MB-LINE-7",
        "anomaly_type":      "yield_drop",
        "current_value":     87.3,
        "threshold":         90.0,
        "summary":           "Yield dropped below threshold after PCB rework.",
        "action_items":      ["Pause Line 7", "Notify QC lead"],
        "trade_off_resolution": "Stop now to avoid scrap accumulation.",
        "confidence_score":  0.94,
        "num_operators":     2,
        "throughput_uph":    42.5,
        "cost_per_unit_usd": 1.82,
        "status":            "PENDING_APPROVAL",
    }

    sig = sign_payload(proposal)

    assert isinstance(sig, str), "Signature should be a base64 string"
    assert len(sig) > 0, "Signature must not be empty"

    result = verify_payload(proposal, sig)
    assert result is True, "Signature must verify against the original payload"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Red-Team: tamper detection
# ═══════════════════════════════════════════════════════════════════════════════

def test_red_team_tampered_payload():
    """
    After signing, an adversary modifies confidence_score.
    The signature must no longer validate (integrity is broken).
    """
    original = {
        "proposal_id":       "prop-redteam-007",
        "machine_id":        "MB-ATTACK",
        "anomaly_type":      "cost_spike",
        "current_value":     5.10,
        "threshold":         4.00,
        "confidence_score":  0.85,
        "cost_per_unit_usd": 5.10,
    }
    sig = sign_payload(original)

    # Adversary tampers — inflates cost to hide a financial fraud
    tampered = dict(original)
    tampered["cost_per_unit_usd"] = 0.01   # attacker back-dates cost

    result = verify_payload(tampered, sig)
    assert result is False, (
        "Signature MUST fail for tampered payload — cryptographic integrity broken."
    )


def test_verify_payload_with_wrong_signature():
    """Completely wrong (random) signature string must return False."""
    payload = {"key": "value", "num": 42}
    bad_sig = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    assert verify_payload(payload, bad_sig) is False


def test_sign_is_deterministic_over_key_order():
    """
    Payloads with the same logical keys but different insertion order must
    produce the same signature (canonical JSON serialisation is key-sorted).
    """
    p1 = {"b": 2, "a": 1}
    p2 = {"a": 1, "b": 2}
    assert sign_payload(p1) == sign_payload(p2), (
        "Signatures must be identical for semantically equivalent dicts."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Audit chain integrity — SECURE path
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_audit_chain_integrity():
    """
    Log 3 blocks with TamperEvidentAuditLog, then verify_chain → SECURE.
    """
    from telemetry import TamperEvidentAuditLog, AuditChainEntry

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as tmp:
        tmp_path = tmp.name

    # Reset class-level state for a clean slate
    TamperEvidentAuditLog._seq = 0
    TamperEvidentAuditLog._last_hash = ""

    try:
        for i in range(3):
            await TamperEvidentAuditLog.record(
                event_type=f"TEST_EVENT_{i}",
                entity_id=f"entity-{i}",
                payload={"index": i, "data": f"block-{i}"},
                # Override log file to use temp file
            )

        # Patch log file path for this test
        import telemetry as tel
        original_path = tel.AUDIT_CHAIN_LOG_FILE
        tel.AUDIT_CHAIN_LOG_FILE = tmp_path

        # Re-record to the temp file
        TamperEvidentAuditLog._seq = 0
        TamperEvidentAuditLog._last_hash = ""

        for i in range(3):
            await TamperEvidentAuditLog.record(
                event_type=f"TEST_EVENT_{i}",
                entity_id=f"entity-{i}",
                payload={"index": i, "data": f"block-{i}"},
            )

        result = await TamperEvidentAuditLog.verify_chain(log_file=tmp_path)

        assert result["status"] == "SECURE", (
            f"Expected SECURE but got {result}"
        )
        assert result["total_blocks"] == 3
        assert result["tampered_blocks"] == []
        assert result["verified_signatures"] == 3

    finally:
        tel.AUDIT_CHAIN_LOG_FILE = original_path
        TamperEvidentAuditLog._seq = 0
        TamperEvidentAuditLog._last_hash = ""
        os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Red-Team: tampering with JSONL → COMPROMISED
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_audit_chain_tampering_detected():
    """
    Write 3 valid blocks, then mutate the second block's payload in the JSONL
    file.  verify_chain must detect the compromise.
    """
    import telemetry as tel

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    ) as tmp:
        tmp_path = tmp.name

    original_path = tel.AUDIT_CHAIN_LOG_FILE
    tel.AUDIT_CHAIN_LOG_FILE = tmp_path
    tel.TamperEvidentAuditLog._seq = 0
    tel.TamperEvidentAuditLog._last_hash = ""

    try:
        for i in range(3):
            await tel.TamperEvidentAuditLog.record(
                event_type="SIGNED_EVENT",
                entity_id=f"ent-{i}",
                payload={"idx": i, "value": float(i) * 10.0},
            )

        # Tamper: read the JSONL, mutate block[1].payload, write back
        with open(tmp_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        assert len(lines) == 3, "Expected 3 lines in JSONL"

        block1 = json.loads(lines[1])
        block1["payload"]["value"] = 9999.0   # adversarial edit
        lines[1] = json.dumps(block1) + "\n"

        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)

        result = await tel.TamperEvidentAuditLog.verify_chain(log_file=tmp_path)

        assert result["status"] == "COMPROMISED", (
            f"Expected COMPROMISED after tampering, got: {result}"
        )
        assert len(result["tampered_blocks"]) > 0, (
            "At least one tampered block must be reported."
        )

    finally:
        tel.AUDIT_CHAIN_LOG_FILE = original_path
        tel.TamperEvidentAuditLog._seq = 0
        tel.TamperEvidentAuditLog._last_hash = ""
        os.unlink(tmp_path)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Key-Manager singleton — same instance returned
# ═══════════════════════════════════════════════════════════════════════════════

def test_key_manager_singleton():
    """KeyManager.get_instance() must always return the same object."""
    km1 = KeyManager.get_instance()
    km2 = KeyManager.get_instance()
    assert km1 is km2, "KeyManager must be a singleton"


def test_hash_payload_deterministic():
    """hash_payload must return a 64-char hex SHA-256 string, deterministically."""
    data = {"x": 1, "y": "hello"}
    h1 = hash_payload(data)
    h2 = hash_payload(data)
    assert h1 == h2
    assert len(h1) == 64
    assert all(c in "0123456789abcdef" for c in h1)
