---
name: "🐛 Bug Report"
about: "Report a reproducible defect in the platform"
title: "fix: [short description of the bug]"
labels: ["bug", "needs-triage"]
assignees: []
---

## Bug Report

**Thank you for helping improve the Enterprise MVA Platform.** Please fill in all sections so we can reproduce and fix the issue promptly.

---

### Affected Component

> Select the component(s) where the bug occurs.

- [ ] Backend API (`ddm-l6/backend/main.py`, routes)
- [ ] Agentic Router / Supervisor (`agent_router_poc.py`)
- [ ] Swarm Debate Room (`agents/debate_room.py`)
- [ ] Temporal Analyst (`agents/temporal_analyst.py`)
- [ ] Vision Inspector (`agents/vision_inspector.py`)
- [ ] IoT Watchdog (`events/iot_watchdog.py`)
- [ ] Agentic ETL (`data_ops/agentic_etl.py`)
- [ ] Cryptographic Provenance (`security/provenance.py`)
- [ ] ROS2 Bridge / SITL (`robotics/`)
- [ ] LLMOps Telemetry (`telemetry.py`)
- [ ] Memory Store (`memory_store.py`)
- [ ] MVA Workbook SPA (`mva-v2/`)
- [ ] Mission Control Dashboard (`mission-control.html`)
- [ ] Docker / Docker Compose
- [ ] Kubernetes manifests (`deploy/k8s/`)
- [ ] Other: _______________

---

### Platform Version

```
# Run: git log --oneline -1
Commit SHA:
```

| Item | Version |
|---|---|
| Docker | |
| Docker Compose | |
| OS / Kernel | |
| Browser (if UI bug) | |
| LLM Provider / Model | |

---

### Description

<!-- A clear and concise description of what the bug is. -->

---

### Steps to Reproduce

1. Start the stack with `make run-dev`
2. ...
3. ...
4. Observe the error

---

### Expected Behavior

<!-- What you expected to happen. -->

---

### Actual Behavior

<!-- What actually happened. Include error messages, stack traces, or screenshots. -->

```
# Paste relevant logs here (get them with: make logs-backend)
```

---

### Minimal Reproducible Example

<!-- If applicable, provide a minimal code snippet or curl command that reproduces the issue. -->

```python
# or bash / curl
```

---

### Audit Log Entry (if applicable)

> If the bug involves agent decisions or robot dispatch, paste the relevant entry from `audit_chain.jsonl`.

```json
```

---

### Additional Context

<!-- Anything else that might help: environment variables set, proxy configuration, etc. -->

- [ ] I have searched existing issues and this is not a duplicate.
- [ ] I have read [CONTRIBUTING.md](../../CONTRIBUTING.md).
