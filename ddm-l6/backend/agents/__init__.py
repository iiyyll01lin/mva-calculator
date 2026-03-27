"""
backend/agents/__init__.py
Multi-Agent module. Exposes the debate room as the primary public interface.
"""
from agents.debate_room import (
    ConsensusResult,
    CritiquePlan,
    DebatePlan,
    run_debate_session,
)

__all__ = [
    "run_debate_session",
    "DebatePlan",
    "CritiquePlan",
    "ConsensusResult",
]
