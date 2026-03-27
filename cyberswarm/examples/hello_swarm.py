"""
examples/hello_swarm.py
──────────────────────────────────────────────────────────────────────────────
Minimal 10-line "Hello, Swarm!" demonstration.

Shows how to:
  1. Define two adversarial agents by subclassing DebateAgent
  2. Define a consensus judge by subclassing ConsensusJudge
  3. Wire them into a CyberSwarm with a tamper-evident audit log
  4. Trigger a secure, signed debate with one awaitable call

Run:
    cd examples
    python hello_swarm.py

No API keys required — agents use stub logic here.  Swap the return values
inside propose/critique/synthesize with real LLM calls to go production-ready.
"""

import asyncio

from cyberswarm import (
    CyberSwarm,
    ConsensusJudge,
    ConsensusResult,
    Critique,
    DebateAgent,
    InMemoryAuditBackend,
    Proposal,
    TamperEvidentAuditLog,
)


# ── Step 1: Define your adversarial agents ────────────────────────────────────

class MarketingAgent(DebateAgent):
    name = "Marketing"

    async def propose(self, topic: str, context: str = "") -> Proposal:
        return Proposal(
            summary=f"Launch an aggressive paid-social campaign for: {topic}",
            key_points=["Target 18-35 demographics", "Double ad spend Q4"],
            risks=["High CPM cost", "Brand dilution risk"],
        )

    async def critique(self, topic: str, proposal: Proposal, context: str = "") -> Critique:
        return Critique(
            critiqued_id=proposal.proposal_id,
            critic_name=self.name,
            weaknesses=["Counter-proposal ignores growth KPIs"],
            counter_proposal=Proposal(
                summary="Hybrid: controlled spend + influencer micro-targeting",
                key_points=["CAC within budget", "A/B test messaging"],
                risks=["Longer ramp-up"],
            ),
        )


class LegalAgent(DebateAgent):
    name = "Legal & Compliance"

    async def propose(self, topic: str, context: str = "") -> Proposal:
        return Proposal(
            summary=f"Audit all creative assets before launch: {topic}",
            key_points=["Regulatory sign-off required", "Escrow ad budget pending review"],
            risks=["Delayed GTM", "Opportunity cost"],
        )

    async def critique(self, topic: str, proposal: Proposal, context: str = "") -> Critique:
        return Critique(
            critiqued_id=proposal.proposal_id,
            critic_name=self.name,
            weaknesses=["Aggressive spend may breach FTC disclosure rules"],
            counter_proposal=Proposal(
                summary="Phased launch: compliance gate at $50K spend threshold",
                key_points=["Built-in legal checkpoints", "Risk-tiered budget release"],
                risks=["Slightly slower growth trajectory"],
            ),
        )


# ── Step 2: Define the consensus judge ───────────────────────────────────────

class BalancedJudge(ConsensusJudge):
    async def synthesize(
        self,
        topic: str,
        proposal: Proposal,
        critique: Critique,
        session_id: str,
    ) -> ConsensusResult:
        return ConsensusResult(
            summary=(
                "Approve a phased paid-social launch: release 40% of the budget "
                "immediately for A/B testing, gate the remaining 60% behind a "
                "compliance review at the $50K spend threshold.  All creative "
                "assets must carry FTC-compliant disclosures from day one."
            ),
            adopted_from_proposer=["aggressive demographic targeting", "Q4 urgency"],
            adopted_from_critic=["compliance gate", "phased budget release"],
            trade_off_resolution=(
                "Growth velocity is preserved while regulatory exposure is "
                "bounded by a hard spend threshold and mandatory legal review."
            ),
            confidence_score=0.91,
            session_id=session_id,
        )


# ── Step 3: Wire up and run ───────────────────────────────────────────────────

async def main() -> None:
    audit = TamperEvidentAuditLog(InMemoryAuditBackend())

    swarm = CyberSwarm(
        proposer  = MarketingAgent(),
        critic    = LegalAgent(),
        judge     = BalancedJudge(),
        audit_log = audit,
    )

    result = await swarm.debate("Q4 Social Media Campaign Strategy")

    print("═" * 60)
    print(f"Consensus  : {result.consensus_id}")
    print(f"Session    : {result.session_id}")
    print(f"Confidence : {result.confidence_score:.0%}")
    print(f"Turns      : {result.debate_turns}")
    print()
    print("Summary:")
    print(result.summary)
    print()
    print("Adopted from Marketing :", result.adopted_from_proposer)
    print("Adopted from Legal     :", result.adopted_from_critic)
    print("Trade-off resolution   :", result.trade_off_resolution)
    print()
    print(f"Audit chain blocks     : {audit.current_seq}")
    print(f"Chain integrity OK     : {await audit.verify_chain()}")
    print("═" * 60)


if __name__ == "__main__":
    asyncio.run(main())
