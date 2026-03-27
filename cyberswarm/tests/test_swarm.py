"""
tests/test_swarm.py — unit tests for BaseSwarm / CyberSwarm orchestration.
"""

import pytest

from cyberswarm import (
    ConsensusJudge,
    ConsensusResult,
    CyberSwarm,
    Critique,
    DebateAgent,
    InMemoryAuditBackend,
    Proposal,
    TamperEvidentAuditLog,
)
from cyberswarm.core.swarm import BaseSwarm


# ── Stub agents ───────────────────────────────────────────────────────────────

class StubProposer(DebateAgent):
    name = "Proposer"

    async def propose(self, topic: str, context: str = "") -> Proposal:
        return Proposal(
            summary=f"Proposer plan: {topic}",
            key_points=["point-a"],
            risks=["risk-a"],
            metadata={"turn": "propose"},
        )

    async def critique(self, topic: str, proposal: Proposal, context: str = "") -> Critique:
        return Critique(
            critiqued_id=proposal.proposal_id,
            critic_name=self.name,
            weaknesses=["rebuttal weakness"],
            counter_proposal=Proposal(summary="Rebuttal plan", key_points=[], risks=[]),
        )


class StubCritic(DebateAgent):
    name = "Critic"

    async def propose(self, topic: str, context: str = "") -> Proposal:
        return Proposal(summary=f"Critic plan: {topic}", key_points=[], risks=[])

    async def critique(self, topic: str, proposal: Proposal, context: str = "") -> Critique:
        return Critique(
            critiqued_id=proposal.proposal_id,
            critic_name=self.name,
            weaknesses=["weakness-1", "weakness-2"],
            counter_proposal=Proposal(
                summary="Counter plan", key_points=["better-point"], risks=[]
            ),
        )


class StubJudge(ConsensusJudge):
    async def synthesize(
        self,
        topic: str,
        proposal: Proposal,
        critique: Critique,
        session_id: str,
    ) -> ConsensusResult:
        return ConsensusResult(
            summary="Balanced consensus",
            adopted_from_proposer=["point-a"],
            adopted_from_critic=["better-point"],
            trade_off_resolution="balanced",
            confidence_score=0.85,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_swarm(max_turns: int = 3, with_audit: bool = False) -> CyberSwarm:
    audit = (
        TamperEvidentAuditLog(InMemoryAuditBackend()) if with_audit else None
    )
    return CyberSwarm(
        proposer  = StubProposer(),
        critic    = StubCritic(),
        judge     = StubJudge(),
        audit_log = audit,
        max_turns = max_turns,
    )


# ── CyberSwarm basic behaviour ────────────────────────────────────────────────

class TestCyberSwarmDebate:
    async def test_returns_consensus_result(self):
        result = await make_swarm().debate("test topic")
        assert isinstance(result, ConsensusResult)

    async def test_summary_not_empty(self):
        result = await make_swarm().debate("test topic")
        assert result.summary

    async def test_session_id_stamped(self):
        result = await make_swarm().debate("test topic")
        assert result.session_id

    async def test_explicit_session_id(self):
        result = await make_swarm().debate("topic", session_id="my-session")
        assert result.session_id == "my-session"

    async def test_debate_turns_stamped(self):
        result = await make_swarm(max_turns=3).debate("topic")
        assert result.debate_turns == 3

    async def test_rebuttal_round_adds_turn(self):
        result = await make_swarm(max_turns=4).debate("topic")
        # Turn 1 = propose, Turn 2 = critique, Turn 3 = rebuttal, Turn 4 = judge
        assert result.debate_turns == 4

    async def test_confidence_score_within_range(self):
        result = await make_swarm().debate("topic")
        assert 0.0 <= result.confidence_score <= 1.0


class TestCyberSwarmValidation:
    def test_max_turns_below_minimum_raises(self):
        with pytest.raises(ValueError, match="max_turns must be >= 3"):
            CyberSwarm(
                proposer=StubProposer(),
                critic=StubCritic(),
                judge=StubJudge(),
                max_turns=2,
            )

    def test_max_turns_capped_at_10(self):
        swarm = CyberSwarm(
            proposer=StubProposer(),
            critic=StubCritic(),
            judge=StubJudge(),
            max_turns=99,
        )
        assert swarm.max_turns == 10


class TestCyberSwarmAudit:
    async def test_audit_log_receives_blocks(self):
        backend = InMemoryAuditBackend()
        audit   = TamperEvidentAuditLog(backend=backend)
        swarm   = CyberSwarm(
            proposer=StubProposer(), critic=StubCritic(),
            judge=StubJudge(), audit_log=audit,
        )
        await swarm.debate("topic")
        # 3 turns → PROPOSAL + CRITIQUE + CONSENSUS = 3 blocks minimum
        entries = await backend.load_all()
        assert len(entries) >= 3

    async def test_chain_integrity_after_debate(self):
        backend = InMemoryAuditBackend()
        audit   = TamperEvidentAuditLog(backend=backend)
        swarm   = CyberSwarm(
            proposer=StubProposer(), critic=StubCritic(),
            judge=StubJudge(), audit_log=audit,
        )
        await swarm.debate("topic")
        assert await audit.verify_chain() is True

    async def test_no_audit_log_does_not_raise(self):
        # audit_log=None is fine
        swarm  = make_swarm(with_audit=False)
        result = await swarm.debate("topic without audit")
        assert isinstance(result, ConsensusResult)


# ── Context hook ─────────────────────────────────────────────────────────────

class TestContextHook:
    async def test_fetch_context_called(self):
        calls: list = []

        class ContextSwarm(CyberSwarm):
            async def _fetch_context(self, topic: str, session_id: str) -> str:
                calls.append((topic, session_id))
                return "injected-context"

        swarm = ContextSwarm(
            proposer=StubProposer(), critic=StubCritic(), judge=StubJudge()
        )
        await swarm.debate("topic", session_id="s1")
        # Called at least twice (once for proposer, once for critic)
        assert len(calls) >= 2
        assert all(sid == "s1" for _, sid in calls)


# ── Turn callback hook ────────────────────────────────────────────────────────

class TestTurnCallbackHook:
    async def test_on_turn_complete_called_per_turn(self):
        completed: list = []

        class TrackingSwarm(CyberSwarm):
            async def _on_turn_complete(self, turn, agent_name, output, session):
                completed.append((turn, agent_name))

        swarm = TrackingSwarm(
            proposer=StubProposer(), critic=StubCritic(), judge=StubJudge()
        )
        await swarm.debate("topic")
        assert len(completed) == 3
        turns = [t for t, _ in completed]
        assert turns == [1, 2, 3]


# ── hello_swarm example smoke test ───────────────────────────────────────────

class TestHelloSwarmExample:
    async def test_example_runs_end_to_end(self):
        """Import and run the hello_swarm example as a smoke test."""
        import importlib.util
        import pathlib

        example_path = (
            pathlib.Path(__file__).parent.parent / "examples" / "hello_swarm.py"
        )
        spec   = importlib.util.spec_from_file_location("hello_swarm", example_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        # Run the async main()
        import asyncio
        await module.main()
