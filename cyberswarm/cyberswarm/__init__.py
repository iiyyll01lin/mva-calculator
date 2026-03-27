"""
cyberswarm
══════════════════════════════════════════════════════════════════════════════
Generic multi-agent swarm debate & cryptographic provenance SDK.

Quick-start
───────────
::

    from cyberswarm import CyberSwarm, DebateAgent, ConsensusJudge
    from cyberswarm import Proposal, Critique, ConsensusResult
    from cyberswarm import TamperEvidentAuditLog, JsonlAuditBackend

    class MyProposer(DebateAgent):
        name = "Proposer"
        async def propose(self, topic, context=""):
            return Proposal(summary=f"Initial plan for: {topic}", key_points=["a"])
        async def critique(self, topic, proposal, context=""):
            return Critique(critiqued_id=proposal.proposal_id, critic_name=self.name,
                            weaknesses=["too vague"], counter_proposal=proposal)

    class MyCritic(DebateAgent):
        name = "Critic"
        async def propose(self, topic, context=""):
            return Proposal(summary=f"Counter-plan for: {topic}", key_points=["b"])
        async def critique(self, topic, proposal, context=""):
            return Critique(critiqued_id=proposal.proposal_id, critic_name=self.name,
                            weaknesses=["too aggressive"], counter_proposal=proposal)

    class MyJudge(ConsensusJudge):
        async def synthesize(self, topic, proposal, critique, session_id):
            return ConsensusResult(summary="Best of both worlds", confidence_score=0.9)

    audit = TamperEvidentAuditLog(JsonlAuditBackend("audit.jsonl"))
    swarm = CyberSwarm(proposer=MyProposer(), critic=MyCritic(),
                       judge=MyJudge(), audit_log=audit)
    result = await swarm.debate("Reduce infrastructure costs by Q3")

Public API surface
──────────────────
Core abstractions
  :class:`DebateAgent`      alias for :class:`~cyberswarm.core.swarm.BaseDebateAgent`
  :class:`ConsensusJudge`   alias for :class:`~cyberswarm.core.swarm.ConsensusJudge`
  :class:`CyberSwarm`       concrete orchestrator (subclass of :class:`~cyberswarm.core.swarm.BaseSwarm`)

Data models
  :class:`Proposal`         Turn-1 agent output
  :class:`Critique`         Turn-2 agent output (with counter-proposal)
  :class:`ConsensusResult`  Final judge output

Audit subsystem
  :class:`TamperEvidentAuditLog`   hash-chaining + Ed25519 signing engine
  :class:`AuditBackend`            storage plugin interface (ABC)
  :class:`JsonlAuditBackend`       built-in JSONL file backend
  :class:`InMemoryAuditBackend`    built-in in-memory backend (tests)
  :class:`KeyManager`              Ed25519 key pair singleton
  :func:`sign_payload`             sign a dict
  :func:`verify_payload`           verify a signature
  :func:`hash_payload`             SHA-256 hex digest of a dict
"""

from __future__ import annotations

from typing import Optional

# ── Core abstractions ─────────────────────────────────────────────────────────
from cyberswarm.core.schemas import (
    ConsensusResult,
    Critique,
    DebateSession,
    Proposal,
)
from cyberswarm.core.swarm import (
    BaseDebateAgent,
    BaseSwarm,
    ConsensusJudge,
)

# ── Audit subsystem ───────────────────────────────────────────────────────────
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

# ── Convenience alias ─────────────────────────────────────────────────────────
DebateAgent = BaseDebateAgent
"""Alias for :class:`~cyberswarm.core.swarm.BaseDebateAgent`."""


# ────────────────────────────────────────────────────────────────────────────
# CyberSwarm — batteries-included concrete orchestrator
# ────────────────────────────────────────────────────────────────────────────

class CyberSwarm(BaseSwarm):
    """
    Concrete, ready-to-use swarm debate orchestrator.

    Inherits the complete ``debate()`` orchestration loop from
    :class:`~cyberswarm.core.swarm.BaseSwarm`.  Override
    :meth:`~cyberswarm.core.swarm.BaseSwarm._fetch_context` to inject
    domain enrichment (RAG results, sensor data, historical records) into
    every agent turn, or override
    :meth:`~cyberswarm.core.swarm.BaseSwarm._on_turn_complete` to stream
    partial results to a frontend dashboard.

    Args:
        proposer:  First adversarial :class:`DebateAgent` — generates the
                   opening proposal.
        critic:    Second adversarial :class:`DebateAgent` — generates the
                   critique and counter-proposal.
        judge:     :class:`ConsensusJudge` that synthesizes the final result.
        audit_log: Optional :class:`TamperEvidentAuditLog`.  When supplied,
                   every proposal and consensus is Ed25519-signed and
                   SHA-256 hash-chained before being handed to the backend.
        max_turns: Default 3 (minimum).  Set to 4+ to include a rebuttal
                   round before the consensus judge.

    Example::

        swarm = CyberSwarm(
            proposer  = MarketingAgent(),
            critic    = LegalAgent(),
            judge     = GptJudge(),
            audit_log = TamperEvidentAuditLog(JsonlAuditBackend()),
        )
        result = await swarm.debate("Launch v2 product in APAC next quarter?")
        print(result.summary)
        print(f"Confidence: {result.confidence_score:.0%}")
    """

    # CyberSwarm is a fully concrete class; no new abstract methods.
    # All behaviour comes from BaseSwarm — this class exists so users can
    # ``from cyberswarm import CyberSwarm`` without knowing about BaseSwarm.

    def __init__(
        self,
        proposer:  BaseDebateAgent,
        critic:    BaseDebateAgent,
        judge:     ConsensusJudge,
        audit_log: Optional[TamperEvidentAuditLog] = None,
        max_turns: int = 3,
    ) -> None:
        super().__init__(
            proposer  = proposer,
            critic    = critic,
            judge     = judge,
            audit_log = audit_log,
            max_turns = max_turns,
        )


# ── Public __all__ ────────────────────────────────────────────────────────────

__all__ = [
    # Orchestrator
    "CyberSwarm",
    # ABCs
    "DebateAgent",
    "BaseDebateAgent",
    "ConsensusJudge",
    "BaseSwarm",
    # Schemas
    "Proposal",
    "Critique",
    "ConsensusResult",
    "DebateSession",
    # Audit engine
    "TamperEvidentAuditLog",
    "AuditChainEntry",
    "AuditBackend",
    # Built-in backends
    "JsonlAuditBackend",
    "InMemoryAuditBackend",
    # Crypto primitives
    "KeyManager",
    "sign_payload",
    "verify_payload",
    "hash_payload",
    "GENESIS_HASH",
]

__version__ = "0.1.0"
