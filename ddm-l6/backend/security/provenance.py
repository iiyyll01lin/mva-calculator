"""
Cryptographic Provenance Engine
================================
Ephemeral Ed25519 key pair generated at process start (PoC).
Provides:
  - sign_payload(payload: dict) -> str        # base64-encoded Ed25519 signature
  - verify_payload(payload, signature) -> bool
  - hash_payload(payload: dict) -> str         # SHA-256 hex digest (for chain)
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Optional

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.exceptions import InvalidSignature


class KeyManager:
    """
    Singleton that holds an ephemeral Ed25519 key pair for the lifetime of the
    process.  In production this would load from a secrets manager or HSM; for
    the PoC we generate fresh keys at import time.
    """

    _instance: Optional["KeyManager"] = None

    def __init__(self) -> None:
        self._private_key: Ed25519PrivateKey = Ed25519PrivateKey.generate()
        self._public_key: Ed25519PublicKey = self._private_key.public_key()

    @classmethod
    def get_instance(cls) -> "KeyManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def private_key(self) -> Ed25519PrivateKey:
        return self._private_key

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._public_key


def _canonical_bytes(payload: dict) -> bytes:
    """
    Produce a deterministic, canonical UTF-8 encoding of *payload*.
    Keys are sorted and no extra whitespace is emitted, so the same logical
    dict always produces the same byte string regardless of insertion order.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def sign_payload(payload: dict) -> str:
    """
    Sign *payload* with the process-level Ed25519 private key.

    Returns
    -------
    str
        Base64-encoded (URL-safe, no padding) Ed25519 signature.
    """
    km = KeyManager.get_instance()
    raw_sig: bytes = km.private_key.sign(_canonical_bytes(payload))
    return base64.urlsafe_b64encode(raw_sig).decode("ascii")


def verify_payload(payload: dict, signature_b64: str) -> bool:
    """
    Verify that *signature_b64* is a valid Ed25519 signature over *payload*.

    Returns
    -------
    bool
        ``True`` if the signature is valid; ``False`` otherwise.
    """
    km = KeyManager.get_instance()
    try:
        raw_sig = base64.urlsafe_b64decode(signature_b64 + "==")  # re-pad safely
        km.public_key.verify(raw_sig, _canonical_bytes(payload))
        return True
    except (InvalidSignature, Exception):
        return False


def hash_payload(payload: dict) -> str:
    """
    Compute a deterministic SHA-256 hex digest over *payload*.
    Used for the blockchain-style audit-chain hash linking.
    """
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()


# ---------------------------------------------------------------------------
# Genesis sentinel — the hash that precedes the very first chain block
# ---------------------------------------------------------------------------
GENESIS_HASH: str = "0" * 64
