"""
tests/test_crypto.py — unit tests for the cryptographic primitives.
"""

import pytest

from cyberswarm.audit.crypto import (
    GENESIS_HASH,
    KeyManager,
    hash_payload,
    sign_payload,
    verify_payload,
)


class TestGenesisHash:
    def test_is_64_zeros(self):
        assert GENESIS_HASH == "0" * 64

    def test_is_string(self):
        assert isinstance(GENESIS_HASH, str)


class TestKeyManager:
    def test_singleton(self):
        km1 = KeyManager.get_instance()
        km2 = KeyManager.get_instance()
        assert km1 is km2

    def test_has_keys(self):
        km = KeyManager.get_instance()
        assert km.private_key is not None
        assert km.public_key is not None


class TestHashPayload:
    def test_deterministic(self):
        p = {"a": 1, "b": "hello"}
        assert hash_payload(p) == hash_payload(p)

    def test_key_order_invariant(self):
        p1 = {"b": 2, "a": 1}
        p2 = {"a": 1, "b": 2}
        assert hash_payload(p1) == hash_payload(p2)

    def test_returns_64_char_hex(self):
        result = hash_payload({"test": True})
        assert len(result) == 64
        int(result, 16)  # raises if not valid hex

    def test_different_payloads_differ(self):
        assert hash_payload({"v": 1}) != hash_payload({"v": 2})


class TestSignAndVerify:
    def test_valid_signature_verifies(self):
        payload = {"event": "test", "value": 42}
        sig = sign_payload(payload)
        assert verify_payload(payload, sig) is True

    def test_tampered_payload_fails(self):
        payload = {"event": "test", "value": 42}
        sig = sign_payload(payload)
        payload["value"] = 99  # tamper
        assert verify_payload(payload, sig) is False

    def test_wrong_signature_fails(self):
        p1 = {"x": 1}
        p2 = {"x": 2}
        sig = sign_payload(p1)
        assert verify_payload(p2, sig) is False

    def test_garbage_signature_false(self):
        assert verify_payload({"x": 1}, "not-base64!!") is False

    def test_signature_is_string(self):
        sig = sign_payload({"hello": "world"})
        assert isinstance(sig, str)
        assert len(sig) > 0
