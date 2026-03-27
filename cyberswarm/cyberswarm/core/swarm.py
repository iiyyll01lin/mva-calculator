"""
cyberswarm/core/swarm.py
──────────────────────────────────────────────────────────────────────────────
Abstract base classes for the CyberSwarm debate engine.

Subclass these three ABCs to build any domain-specific debate swarm:

    class MarketingAgent(BaseDebateAgent):
        name = "Marketing"
        async def propose(self, topic, context=""):  ...
        async def critique(self, topic, proposal, context=""): ...

    class LegalJudge(ConsensusJudge):
        async def synthesize(self, topic, proposal, critique, session_id): ...

    swarm = ConcreteSwarm(
        proposer=MarketingAgent(),
        critic=LegalAgent(),
        judge=LegalJudge(),
    )
    result = await swarm.debate("Q4 Social Media Campaign")

Design notes
────────────
• ``BaseDebateAgent`` is intentionally symmetric: the same agent class can
  be used as proposer *or* critic.  Asymmetric specialisation is achieved via
  the system prompts / logic inside your ``propose`` / ``critique`` methods.
• ``ConsensusJudge`` is kept separate from ``BaseDebateAgent`` so it is clear
  that the judge never proposes — it only synthesizes.
• ``BaseSwarm`` ships a complete default ``debate()`` orchestration that you
  can use as-is or override turn-by-turn.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

from cyberswarm.core.schemas import (
    ConsensusResult,
    Critique,
    DebateSession,
    Proposal,
)

if TYPE_CHECKING:
    from cyberswarm.audit.chain import TamperEvidentAuditLog

logger = logging.getLogger(__name__)

# Hard-coded safety rails — override in subclass if needed.
_MIN_TURNS: int = 3
_MAX_TURNS: int = 10


# ────────────────────────────────────────────────────────────────────────────
# Adversarial agent interface
# ────────────────────────────────────────────────────────────────────────────

class BaseDebateAgent(ABC):
    """
    Abstract base for any agent that participates in an adversarial debate.

    Subclasses must implement ``propose()`` and ``critique()``.  Both methods
    receive the debate ``topic`` string and an optional ``context`` block that
    the swarm engine injects (historical data, tool results, prior turn output).

    Example::

        class CostAgent(BaseDebateAgent):
            name = "CostOptimizer"

            async def propose(self, topic: str, context: str = "") -> Proposal:
                # call your LLM or rule engine here
                return Proposal(summary="Cut headcount by 15%", key_points=["..."])

            async def critique(
                self,
                topic: str,
                proposal: Proposal,
                context: str = "",
            ) -> Critique:
                return Critique(
                    critiqued_id=proposal.proposal_id,
                    critic_name=self.name,
                    weaknesses=["Quality will suffer"],
                    counter_proposal=Proposal(summary="Automate instead", ...),
                )
    """

    # Subclasses may set this as a class attribute for convenience.
    name: str = "UnnamedAgent"

    @abstractmethod
    async def propose(self, topic: str, context: str = "") -> Proposal:
        """
        Generate an initial plan / position on ``topic``.

        Args:
            topic:   Human-readable problem statement or question.
            context: Optional enrichment block — historical data, tool
                     outputs, or prior-turn summaries injected by the swarm.

        Returns:
            :class:`~cyberswarm.core.schemas.Proposal`
        """

    @abstractmethod
    async def critique(
        self,
        topic: str,
        proposal: Proposal,
        context: str = "",
    ) -> Critique:
        """
        Critique an existing :class:`Proposal` and produce a counter-proposal.

        Args:
            topic:    The same topic string passed to :meth:`propose`.
            proposal: The :class:`Proposal` to critique.
            context:  Optional enrichment block.

        Returns:
            :class:`~cyberswarm.core.schemas.Critique` whose
            ``counter_proposal`` field contains the revised plan.
        """


# ────────────────────────────────────────────────────────────────────────────
# Judge interface
# ────────────────────────────────────────────────────────────────────────────

class ConsensusJudge(ABC):
    """
    Abstract synthesis judge that resolves the adversarial debate.

    The judge receives the original proposal *and* the critique, and must
    produce a :class:`~cyberswarm.core.schemas.ConsensusResult` that
    captures the best of both positions.

    Example::

        class GptJudge(ConsensusJudge):
            async def synthesize(self, topic, proposal, critique, session_id):
                # call GPT-4o with both plans in context
                ...
                return ConsensusResult(
                    summary="Automate high-volume tasks; preserve human QC",
                    adopted_from_proposer=["cost reduction targets"],
                    adopted_from_critic=["quality SLA preservation"],
                    confidence_score=0.88,
                )
    """

    @abstractmethod
    async def synthesize(
        self,
        topic: str,
        proposal: Proposal,
        critique: Critique,
        session_id: str,
    ) -> ConsensusResult:
        """
        Synthesize a consensus from the two adversarial positions.

        Args:
            topic:      The debate topic.
            proposal:   Turn-1 :class:`Proposal` from the proposer agent.
            critique:   Turn-2 :class:`Critique` (with counter-proposal).
            session_id: The current :class:`DebateSession` ID — must be
                        stamped onto the returned :class:`ConsensusResult`.

        Returns:
            :class:`~cyberswarm.core.schemas.ConsensusResult`
        """


# ────────────────────────────────────────────────────────────────────────────
# Swarm orchestrator
# ────────────────────────────────────────────────────────────────────────────

class BaseSwarm(ABC):
    """
    Orchestrates a sequential multi-turn adversarial debate.

    Default turn structure (``max_turns=3``)::

        Turn 1  proposer.propose(topic)          → Proposal
        Turn 2  critic.critique(topic, Proposal) → Critique + counter-Proposal
        Turn 3  judge.synthesize(…)              → ConsensusResult

    When ``max_turns > 3`` an additional rebuttal round is inserted before
    the judge::

        Turn 3  proposer.critique(topic, counter_proposal)  → rebuttal Critique
        Turn 4  judge.synthesize(…rebuttal…)                → ConsensusResult

    Override :meth:`_fetch_context` to inject historical data, RAG tool
    output, sensor readings, or any other domain-specific enrichment into
    every agent turn.

    Override :meth:`_on_turn_complete` to react to individual turns (e.g.
    stream partial results to a dashboard).

    Args:
        proposer:  First adversarial agent — generates the initial proposal.
        critic:    Second adversarial agent — generates the critique.
        judge:     Consensus synthesizer.
        audit_log: Optional :class:`~cyberswarm.audit.chain.TamperEvidentAuditLog`
                   instance.  When supplied, every proposal and the final
                   consensus are hash-chained and Ed25519-signed automatically.
        max_turns: Default number of debate rounds (minimum 3, maximum 10).
    """

    def __init__(
        self,
        proposer:  BaseDebateAgent,
        critic:    BaseDebateAgent,
        judge:     ConsensusJudge,
        audit_log: Optional["TamperEvidentAuditLog"] = None,
        max_turns: int = _MIN_TURNS,
    ) -> None:
        if max_turns < _MIN_TURNS:
            raise ValueError(
                f"max_turns must be >= {_MIN_TURNS} "
                "(Turn 1: proposer, Turn 2: critic, Turn 3: judge)."
            )
        self.proposer  = proposer
        self.critic    = critic
        self.judge     = judge
        self.audit_log = audit_log
        self.max_turns = min(max_turns, _MAX_TURNS)

    # ── Extension hooks (override in subclasses) ─────────────────────────────

    async def _fetch_context(self, topic: str, session_id: str) -> str:
        """
        Override to inject domain context (RAG, sensor data, history).

        Called at the start of every agent turn.  Return an empty string
        (default) if no enrichment is needed.
        """
        return ""

    async def _on_turn_complete(
        self,
        turn: int,
        agent_name: str,
        output: Proposal | Critique | ConsensusResult,
        session: DebateSession,
    ) -> None:
        """
        Override to react to turn completions (streaming, dashboards, logging).

        Called synchronously within the debate loop after each turn.
        """

    # ── Private audit helper ─────────────────────────────────────────────────

    async def _audit(self, event_type: str, entity_id: str, payload: dict) -> None:
        """Append a block to the audit chain (no-op when no audit_log is set)."""
        if self.audit_log is not None:
            try:
                await self.audit_log.record(
                    event_type=event_type,
                    entity_id=entity_id,
                    payload=payload,
                )
            except Exception as exc:
                logger.warning("Audit log write failed (%s): %s", event_type, exc)

    # ── Public orchestration API ──────────────────────────────────────────────

    async def debate(
        self,
        topic: str,
        session_id: Optional[str] = None,
    ) -> ConsensusResult:
        """
        Run a full debate session and return the consensus result.

        Args:
            topic:      Human-readable problem statement driving the debate.
            session_id: Optional trace ID.  Auto-generated as UUID4 if omitted.

        Returns:
            :class:`~cyberswarm.core.schemas.ConsensusResult`

        Raises:
            ValueError: propagated from :meth:`ConsensusJudge.synthesize` or
                        either debate agent if they return structurally invalid
                        data.
        """
        session_id = session_id or str(uuid.uuid4())
        session    = DebateSession(session_id=session_id, topic=topic)

        logger.info(
            "Swarm debate started: session=%s topic=%r max_turns=%d",
            session_id, topic, self.max_turns,
        )

        # ── Turn 1: Proposer ──────────────────────────────────────────────────
        context  = await self._fetch_context(topic, session_id)
        proposal = await self.proposer.propose(topic=topic, context=context)

        session.proposals.append(proposal)
        session.turns += 1

        await self._audit(
            "DEBATE_PROPOSAL",
            proposal.proposal_id,
            {"session_id": session_id, "turn": session.turns, **proposal.model_dump()},
        )
        await self._on_turn_complete(session.turns, self.proposer.name, proposal, session)
        logger.info(
            "Turn %d [%s] proposal_id=%s",
            session.turns, self.proposer.name, proposal.proposal_id,
        )

        # ── Turn 2: Critic ────────────────────────────────────────────────────
        context  = await self._fetch_context(topic, session_id)
        critique = await self.critic.critique(
            topic=topic, proposal=proposal, context=context
        )

        session.critiques.append(critique)
        session.turns += 1

        await self._audit(
            "DEBATE_CRITIQUE",
            critique.critique_id,
            {"session_id": session_id, "turn": session.turns, **critique.model_dump()},
        )
        await self._on_turn_complete(session.turns, self.critic.name, critique, session)
        logger.info(
            "Turn %d [%s] critique_id=%s weaknesses=%d",
            session.turns, self.critic.name,
            critique.critique_id, len(critique.weaknesses),
        )

        # ── Optional Turn 3: Proposer rebuttal (when max_turns > 3) ──────────
        active_critique = critique
        if self.max_turns > _MIN_TURNS:
            context  = await self._fetch_context(topic, session_id)
            rebuttal = await self.proposer.critique(
                topic=topic,
                proposal=critique.counter_proposal,
                context=context,
            )
            session.critiques.append(rebuttal)
            session.turns += 1

            await self._audit(
                "DEBATE_REBUTTAL",
                rebuttal.critique_id,
                {
                    "session_id": session_id,
                    "turn": session.turns,
                    **rebuttal.model_dump(),
                },
            )
            await self._on_turn_complete(
                session.turns, self.proposer.name, rebuttal, session
            )
            logger.info(
                "Turn %d [%s rebuttal] critique_id=%s",
                session.turns, self.proposer.name, rebuttal.critique_id,
            )
            # Feed the richer critique context into the judge
            active_critique = rebuttal

        # ── Final Turn: Judge ─────────────────────────────────────────────────
        session.turns += 1
        consensus = await self.judge.synthesize(
            topic=topic,
            proposal=proposal,
            critique=active_critique,
            session_id=session_id,
        )
        # Stamp session metadata the judge doesn't know about
        consensus = consensus.model_copy(
            update={"session_id": session_id, "debate_turns": session.turns}
        )

        session.consensus = consensus
        session.ended_at  = datetime.now(timezone.utc).isoformat()

        await self._audit(
            "DEBATE_CONSENSUS",
            consensus.consensus_id,
            {
                "session_id":   session_id,
                "debate_turns": session.turns,
                **consensus.model_dump(),
            },
        )
        await self._on_turn_complete(
            session.turns, "ConsensusJudge", consensus, session
        )
        logger.info(
            "Turn %d [ConsensusJudge] consensus_id=%s confidence=%.2f session=%s",
            session.turns,
            consensus.consensus_id,
            consensus.confidence_score,
            session_id,
        )

        return consensus
