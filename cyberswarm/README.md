# cyberswarm

> **Generic multi-agent swarm debate & cryptographic provenance SDK**  
> *Extracted from the Enterprise MVA Cyber-Physical Agent Platform*

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-green)](LICENSE)
[![Build: Hatch](https://img.shields.io/badge/build-hatch-purple)](https://hatch.pypa.io)

---

## What is this?

`cyberswarm` packages the core cognitive and security architecture of the MVA platform into a
**domain-agnostic, pip-installable Python library** so any developer can build their own
Cyber-Physical Agent system on top of battle-tested engines:

| Engine | What it does |
|--------|-------------|
| **Swarm Debate** | Sequential multi-turn adversarial debate between two agents, resolved by a consensus judge |
| **Cryptographic Provenance** | Ed25519 signing + SHA-256 hash-chaining — makes every decision tamper-evident |
| **Plugin Audit Backend** | Swap storage (JSONL, PostgreSQL, S3, Redis) without touching any debate logic |

---

## Install

```bash
pip install cyberswarm               # core only (pydantic + cryptography)
pip install "cyberswarm[http]"       # + httpx for Langfuse telemetry
pip install "cyberswarm[all]"        # + http + opentelemetry
```

---

## 10-line hello world

```python
import asyncio
from cyberswarm import CyberSwarm, DebateAgent, ConsensusJudge
from cyberswarm import Proposal, Critique, ConsensusResult

class Marketing(DebateAgent):
    name = "Marketing"
    async def propose(self, topic, context=""):
        return Proposal(summary=f"Double ad spend for {topic}", key_points=["Fast growth"])
    async def critique(self, topic, proposal, context=""):
        return Critique(critiqued_id=proposal.proposal_id, critic_name=self.name,
                        weaknesses=["Too costly"], counter_proposal=proposal)

class Legal(DebateAgent):
    name = "Legal"
    async def propose(self, topic, context=""):
        return Proposal(summary=f"Audit all assets for {topic}", key_points=["Compliance"])
    async def critique(self, topic, proposal, context=""):
        return Critique(critiqued_id=proposal.proposal_id, critic_name=self.name,
                        weaknesses=["Blocks GTM"], counter_proposal=proposal)

class Judge(ConsensusJudge):
    async def synthesize(self, topic, proposal, critique, session_id):
        return ConsensusResult(summary="Phased launch with compliance gate",
                               confidence_score=0.91, session_id=session_id)

async def main():
    swarm = CyberSwarm(proposer=Marketing(), critic=Legal(), judge=Judge())
    result = await swarm.debate("Q4 Social Media Campaign")
    print(result.summary, f"({result.confidence_score:.0%} confident)")

asyncio.run(main())
```

---

## Enabling the audit log

```python
from cyberswarm import TamperEvidentAuditLog, JsonlAuditBackend

audit = TamperEvidentAuditLog(JsonlAuditBackend("audit_chain.jsonl"))
swarm = CyberSwarm(proposer=..., critic=..., judge=..., audit_log=audit)

result = await swarm.debate("...")

# Verify every block in the chain is unmodified
assert await audit.verify_chain()
```

---

## Custom storage backend

Implement two methods and inject:

```python
from cyberswarm import AuditBackend, TamperEvidentAuditLog

class S3AuditBackend(AuditBackend):
    async def persist(self, entry):
        # upload entry.to_json() to your S3 bucket
        ...

    async def load_all(self):
        # stream and deserialise all stored entries
        ...

audit = TamperEvidentAuditLog(S3AuditBackend())
```

The library handles **all** cryptographic concerns (signing, hashing, block sequencing).
Your backend only needs to store and retrieve JSON.

---

## Architecture

```
cyberswarm/
├── core/
│   ├── schemas.py    # Proposal · Critique · ConsensusResult (Pydantic v2)
│   └── swarm.py      # BaseDebateAgent · ConsensusJudge · BaseSwarm
├── audit/
│   ├── crypto.py     # KeyManager · sign_payload · verify_payload · hash_payload
│   ├── backend.py    # AuditBackend ABC (plugin interface)
│   ├── chain.py      # TamperEvidentAuditLog · AuditChainEntry
│   └── backends/
│       ├── jsonl.py  # Default append-only JSONL file
│       └── memory.py # In-memory (tests / REPL)
└── __init__.py       # CyberSwarm facade + re-exports
```

---

## Development

```bash
pip install hatch
hatch run test          # pytest
hatch run lint          # ruff
hatch run typecheck     # mypy --strict
```

---

## License

Apache 2.0 — see [LICENSE](LICENSE).
