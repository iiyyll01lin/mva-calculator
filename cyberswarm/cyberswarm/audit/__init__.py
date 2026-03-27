"""cyberswarm.audit — cryptographic provenance and tamper-evident audit log."""

from cyberswarm.audit.backend import AuditBackend
from cyberswarm.audit.backends.jsonl import JsonlAuditBackend
from cyberswarm.audit.backends.memory import InMemoryAuditBackend
from cyberswarm.audit.chain import AuditChainEntry, TamperEvidentAuditLog
from cyberswarm.audit.crypto import (
    GENESIS_HASH,
    KeyManager,
    hash_payload,
    sign_payload,
    verify_payload,
)

__all__ = [
    # Plugin interface
    "AuditBackend",
    # Built-in backends
    "JsonlAuditBackend",
    "InMemoryAuditBackend",
    # Engine
    "TamperEvidentAuditLog",
    "AuditChainEntry",
    # Primitives
    "KeyManager",
    "sign_payload",
    "verify_payload",
    "hash_payload",
    "GENESIS_HASH",
]
