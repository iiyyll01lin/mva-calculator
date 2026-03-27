"""
cyberswarm/audit/crypto.py
──────────────────────────────────────────────────────────────────────────────
Cryptographic primitives extracted from the Enterprise MVA provenance engine.

Provides:
  • :class:`KeyManager`   — singleton Ed25519 key pair (ephemeral or loaded)
  • :func:`sign_payload`  — deterministic Ed25519 signing over canonical JSON
  • :func:`verify_payload` — Ed25519 signature verification
  • :func:`hash_payload`  — SHA-256 hex digest for blockchain-style hash chaining
  • :data:`GENESIS_HASH`  — 64-zero sentinel for the first chain block

Production key management
──────────────────────────
The default :class:`KeyManager` generates an **ephemeral** key pair at
process start (suitable for PoC/dev).  For production, override
:meth:`KeyManager.get_instance` or subclass :class:`KeyManager` to load
keys from a secrets manager (HashiCorp Vault, AWS KMS, GCP Secret Manager).

    class HsmKeyManager(KeyManager):
        def __init__(self):
            self._private_key = load_from_hsm(key_id=os.getenv("HSM_KEY_ID"))
            self._public_key  = self._private_key.public_key()

    KeyManager._instance = HsmKeyManager()   # inject before first use
"""

from __future__ import annotations

import base64
import hashlib
import json
from typing import Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

# ────────────────────────────────────────────────────────────────────────────

GENESIS_HASH: str = "0" * 64
"""SHA-256 zero-hash sentinel preceding the very first audit chain block."""


# ────────────────────────────────────────────────────────────────────────────
# Key manager
# ────────────────────────────────────────────────────────────────────────────

class KeyManager:
    """
    Singleton holding an Ed25519 key pair for the lifetime of the process.

    Instantiation is lazy and thread-safe (CPython GIL).  Subclass and
    override :meth:`get_instance` to inject HSM or secrets-manager keys
    without touching call sites.

    Attributes
    ----------
    private_key: Ed25519PrivateKey
    public_key:  Ed25519PublicKey
    """

    _instance: Optional["KeyManager"] = None

    def __init__(self) -> None:
        self._private_key: Ed25519PrivateKey = Ed25519PrivateKey.generate()
        self._public_key:  Ed25519PublicKey  = self._private_key.public_key()

    @classmethod
    def get_instance(cls) -> "KeyManager":
        """Return (or create) the process-level singleton."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def private_key(self) -> Ed25519PrivateKey:
        return self._private_key

    @property
    def public_key(self) -> Ed25519PublicKey:
        return self._public_key


# ────────────────────────────────────────────────────────────────────────────
# Canonical serialisation
# ────────────────────────────────────────────────────────────────────────────

def _canonical_bytes(payload: dict) -> bytes:
    """
    Produce a deterministic, canonical UTF-8 encoding of *payload*.

    Keys are sorted recursively and no extra whitespace is emitted, so the
    same logical dict always produces the same byte string regardless of
    insertion order or Python version.
    """
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


# ────────────────────────────────────────────────────────────────────────────
# Public API
# ────────────────────────────────────────────────────────────────────────────

def sign_payload(payload: dict) -> str:
    """
    Sign ``payload`` with the process-level Ed25519 private key.

    Returns
    -------
    str
        URL-safe base64-encoded (no padding) Ed25519 signature.
    """
    km      = KeyManager.get_instance()
    raw_sig = km.private_key.sign(_canonical_bytes(payload))
    return base64.urlsafe_b64encode(raw_sig).decode("ascii")


def verify_payload(payload: dict, signature_b64: str) -> bool:
    """
    Verify that ``signature_b64`` is a valid Ed25519 signature over ``payload``.

    Returns
    -------
    bool
        ``True`` if valid; ``False`` on any verification failure.
    """
    km = KeyManager.get_instance()
    try:
        # Re-add stripped padding safely
        raw_sig = base64.urlsafe_b64decode(signature_b64 + "==")
        km.public_key.verify(raw_sig, _canonical_bytes(payload))
        return True
    except (InvalidSignature, Exception):
        return False


def hash_payload(payload: dict) -> str:
    """
    Compute a deterministic SHA-256 hex digest over ``payload``.

    Used for the blockchain-style hash chaining between audit blocks.
    """
    return hashlib.sha256(_canonical_bytes(payload)).hexdigest()
