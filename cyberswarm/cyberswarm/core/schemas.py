"""
cyberswarm/core/schemas.py
──────────────────────────────────────────────────────────────────────────────
Generic Pydantic v2 data models for swarm debate sessions.

All manufacturing-specific fields (TMU, UPH, cost_per_unit) have been removed.
Domain-specific data belongs in the ``metadata`` dict so the library stays
agnostic about the problem space.

Examples of valid domain use-cases
───────────────────────────────────
• Marketing vs. Legal campaign review
• Security vs. Performance infrastructure trade-off analysis
• Buyer vs. Seller price negotiation simulation
• Risk vs. Opportunity investment committee debate
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ────────────────────────────────────────────────────────────────────────────
# Turn 1 — Proposer output
# ────────────────────────────────────────────────────────────────────────────

class Proposal(BaseModel):
    """
    A concrete plan or position proposed by the first adversarial agent.

    Attributes
    ----------
    proposal_id:
        Auto-generated UUID4 hex label, e.g. ``"PROPOSAL-4A3F…"``.
    summary:
        One-paragraph human-readable description of the proposal.
    key_points:
        Ordered list of supporting arguments or planned actions.
    risks:
        Known trade-offs or failure modes the proposer acknowledges.
    metadata:
        Arbitrary domain-specific data (scores, KPIs, structured plans).
        The library never inspects this field, but it is signed and
        hash-chained when the proposal is logged to the audit chain.
    """

    proposal_id: str = Field(
        default_factory=lambda: f"PROPOSAL-{uuid.uuid4().hex[:8].upper()}",
        description="Unique proposal identifier.",
    )
    summary:    str            = Field(..., description="One-paragraph description.")
    key_points: List[str]      = Field(default_factory=list)
    risks:      List[str]      = Field(default_factory=list)
    metadata:   Dict[str, Any] = Field(
        default_factory=dict,
        description="Domain-specific numeric KPIs or structured data.",
    )

    model_config = {"extra": "ignore"}


# ────────────────────────────────────────────────────────────────────────────
# Turn 2 — Critic output
# ────────────────────────────────────────────────────────────────────────────

class Critique(BaseModel):
    """
    A critique produced by the second adversarial agent, with a counter-proposal.

    Attributes
    ----------
    critique_id:
        Auto-generated UUID4 hex label.
    critiqued_id:
        ``proposal_id`` of the :class:`Proposal` being rebutted.
    critic_name:
        Display name of the agent producing this critique.
    weaknesses:
        Ordered list of weaknesses identified in the original proposal.
    counter_proposal:
        A revised :class:`Proposal` that addresses the weaknesses.
    metadata:
        Additional evidence or scoring data gathered during the critique.
    """

    critique_id:       str = Field(
        default_factory=lambda: f"CRITIQUE-{uuid.uuid4().hex[:8].upper()}",
    )
    critiqued_id:      str = Field(..., description="proposal_id of the critiqued Proposal.")
    critic_name:       str = Field(..., description="Name of the critiquing agent.")
    weaknesses:        List[str]      = Field(default_factory=list)
    counter_proposal:  Proposal
    metadata:          Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


# ────────────────────────────────────────────────────────────────────────────
# Final turn — Judge output
# ────────────────────────────────────────────────────────────────────────────

class ConsensusResult(BaseModel):
    """
    Final synthesized plan produced by :class:`~cyberswarm.core.swarm.ConsensusJudge`.

    Attributes
    ----------
    consensus_id:
        Auto-generated UUID4 hex label.
    summary:
        One-paragraph synthesis of the optimal balanced plan.
    adopted_from_proposer:
        Key points adopted from the first agent's proposal.
    adopted_from_critic:
        Key points adopted from the critic's counter-proposal.
    trade_off_resolution:
        Narrative explanation of how contradictions were resolved.
    confidence_score:
        Judge's self-declared confidence in the synthesis (0.0 – 1.0).
    debate_turns:
        Total number of turns completed in this session.
    session_id:
        The parent :class:`DebateSession` ID.
    metadata:
        Domain-specific KPIs or structured judge data.
    """

    consensus_id:           str = Field(
        default_factory=lambda: f"CONSENSUS-{uuid.uuid4().hex[:8].upper()}",
    )
    summary:                str
    adopted_from_proposer:  List[str]      = Field(default_factory=list)
    adopted_from_critic:    List[str]      = Field(default_factory=list)
    trade_off_resolution:   str            = Field(default="")
    confidence_score:       float          = Field(default=0.0, ge=0.0, le=1.0)
    debate_turns:           int            = Field(default=0)
    session_id:             str            = Field(default="")
    metadata:               Dict[str, Any] = Field(default_factory=dict)

    model_config = {"extra": "ignore"}


# ────────────────────────────────────────────────────────────────────────────
# Internal session tracker
# ────────────────────────────────────────────────────────────────────────────

class DebateSession(BaseModel):
    """Internal record tracking the full state of one debate session."""

    session_id:  str
    topic:       str
    turns:       int                            = 0
    proposals:   List[Proposal]                = Field(default_factory=list)
    critiques:   List[Critique]                = Field(default_factory=list)
    consensus:   Optional[ConsensusResult]     = None
    started_at:  str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    ended_at:    Optional[str] = None

    model_config = {"extra": "ignore"}
