---
name: "🤖 New Agent Proposal"
about: "Propose adding a new specialized Agent to the Multi-Agent Swarm"
title: "feat(agents): propose [AgentName]"
labels: ["enhancement", "new-agent", "needs-design-review"]
assignees: []
---

## New Agent Proposal

The Enterprise MVA Platform's Multi-Agent Swarm is designed for extensibility. Use this template to propose a new specialized Agent that contributes unique reasoning capabilities to the Swarm.

Please read [CONTRIBUTING.md — Section 5 (How to Add a New Swarm Agent)](../../CONTRIBUTING.md#5-how-to-add-a-new-swarm-agent) before submitting.

---

### Agent Name & Module Path

```
Agent class name:    MyNewAgent
Module path:         ddm-l6/backend/agents/my_new_agent.py
AgentName enum key:  MY_NEW_AGENT = "MyNewAgent"
```

---

### Problem Statement

<!-- What gap does this agent fill? What can the Swarm NOT currently do without it? -->

---

### Proposed Agent Role

> Which role(s) will this agent play in the Swarm?

- [ ] **Debate Participant** — Argues a position in `run_debate_session()` alongside CostOptimizationAgent and QualityAndTimeAgent
- [ ] **Sub-Agent Evidence Provider** — Called asynchronously via `asyncio.gather()` to supply evidence to the ConsensusJudge (like TemporalAnalyst or VisionInspector)
- [ ] **Standalone Routed Agent** — Handles direct user queries routed by the SupervisorAgent
- [ ] **Background Monitor** — Runs as an async background task (like IoT Watchdog)
- [ ] **Other:** _______________

---

### Capabilities

#### What the agent reasons about

<!-- Describe the domain knowledge this agent specialises in. -->

#### Input Schema (Pydantic v2)

```python
class MyNewAgentInput(BaseModel):
    model_config = {"strict": True}
    # Define input fields here
```

#### Output Schema (Pydantic v2)

```python
class MyNewAgentOutput(BaseModel):
    model_config = {"strict": True}
    # Define output fields here
```

---

### LLM Tier

> Which LLM tier should this agent use? (See `llm_client.AGENT_MODEL_MAP`)

- [ ] **Local tier** — Fast, cost-optimised model for high-frequency reasoning
- [ ] **Cloud tier** — Frontier model for complex synthesis or vision tasks
- [ ] **Configurable** — Tier determined by environment variable at runtime

---

### Tool Calls Required

<!-- List any tools this agent needs to invoke. Confirm they are allowlisted in ToolGuard. -->

| Tool Name | Purpose | ToolGuard Status |
|---|---|---|
| | | ✅ Already allowlisted / ⚠️ Needs allowlist entry |

---

### Security Analysis

<!-- Complete this section for every new agent. -->

- [ ] **HITL required?** Does this agent propose sensitive actions (robot commands, data mutations, external API calls)?
- [ ] **All outputs schema-validated** before downstream use?
- [ ] **No raw user input passed to shell or eval?**
- [ ] **Adversarial test case** to be added to `eval/red_team.py`?

Describe any security considerations specific to this agent:

---

### Integration Points

<!-- Which existing modules does this agent interact with? -->

- [ ] `debate_room.py` — new debate turn
- [ ] `agent_router_poc.py` — new routing case in SupervisorAgent
- [ ] `telemetry.py` — `agent_span` instrumentation
- [ ] `security/provenance.py` — Ed25519 sign outputs
- [ ] `memory_store.py` — reads/writes session state
- [ ] Other: _______________

---

### Test Plan

```python
# Describe or sketch the key test cases

@pytest.mark.asyncio
async def test_my_new_agent_happy_path():
    ...

@pytest.mark.asyncio
async def test_my_new_agent_handles_missing_data():
    ...
```

---

### Architecture Diagram Update

> Does this agent require an update to `docs/architecture.md`?

- [ ] Yes — I will update the relevant Mermaid diagram in my PR
- [ ] No — it fits within the existing flow without changes

---

### Alternatives Considered

<!-- What other approaches were considered? Why is a new agent the right solution? -->

---

### Additional Context

<!-- Relevant papers, prior art, benchmarks, or reference implementations. -->

---

- [ ] I have read [CONTRIBUTING.md](../../CONTRIBUTING.md).
- [ ] I have searched existing issues and this agent has not been proposed before.
- [ ] I understand this proposal requires a design review before implementation begins.
