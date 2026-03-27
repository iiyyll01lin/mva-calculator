"""
backend/eval
────────────────────────────────────────────────────────────────────────────────
Continuous Evaluation (Eval) Framework — Enterprise MVA Platform v2.0.0

Sub-modules:
  judge     — LLM-as-a-Judge pipeline; grades agent outputs on four axes.
  red_team  — Adversarial test-case generator; produces jailbreak / edge-case
              queries targeting ToolGuard, HITL, BOM hallucination, and
              infinite-reflection triggers.

Design contract (isolation):
  • This package NEVER imports from the production FastAPI server (main.py).
  • It imports from agent_router_poc, telemetry, and security.tool_sandbox ONLY
    as consumers — no monkey-patching, no state mutation outside test scope.
  • All LLM calls inside the judge are routed through the same async patterns
    as the production agent so the semaphore logic is identical.
"""
