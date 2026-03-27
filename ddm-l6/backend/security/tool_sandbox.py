"""
backend/security/tool_sandbox.py
────────────────────────────────────────────────────────────────────────────────
Secure Tool Execution Sandbox — Enterprise MVA Platform v2.0.0

Implements a ToolGuard class that performs "pre-flight" security checks on
all SENSITIVE tool arguments before the tool handler is ever invoked.

Design principles:
  • Each SENSITIVE tool has a dedicated Pydantic guard model with
    @field_validator rules that go beyond the basic tool input schema:
      - Numeric bounds enforcement (e.g. TMU must be 0 < x < 10 000)
      - Pattern/length checks on free-text fields to block injection
      - Cross-field invariants via @model_validator
  • ToolGuard.pre_flight_check() is the single entry point; callers receive
    sanitized args as a plain dict on success, or a ValueError on rejection.
  • Adding a new SENSITIVE tool only requires a guard model + one registry entry.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional, Type

from pydantic import BaseModel, Field, field_validator, model_validator


# ────────────────────────────────────────────────────────────────────────────
# Per-tool Guard Schemas
# ────────────────────────────────────────────────────────────────────────────

class RunSimulationGuard(BaseModel):
    """
    Pre-flight guard for the ``run_simulation`` tool.

    Validates that all numeric parameters are within safe operating envelopes
    and that the model_id contains only safe identifier characters to prevent
    injection into any downstream service that may embed it in a query.
    """

    model_id:         str   = Field(..., description="Process model identifier.")
    cycle_time_tmu:   float = Field(..., description="Gross cycle time in TMU.")
    num_operators:    int   = Field(..., description="Headcount.")
    machine_rate_usd: float = Field(..., description="Burdened machine rate (USD/hr).")

    @field_validator("model_id")
    @classmethod
    def validate_model_id(cls, v: str) -> str:
        """Block injection: only alphanumeric characters, hyphens, and underscores."""
        if not re.fullmatch(r"[A-Za-z0-9\-_]{1,64}", v):
            raise ValueError(
                f"model_id must be 1-64 characters [A-Za-z0-9\\-_], got: {v!r}"
            )
        return v

    @field_validator("cycle_time_tmu")
    @classmethod
    def validate_cycle_time_tmu(cls, v: float) -> float:
        """
        TMU (Time Measurement Unit) must be in a physically meaningful range.
        0 TMU would imply instantaneous work; 10 000 TMU ≈ 6 minutes per unit —
        any higher value likely indicates a data-entry error.
        """
        if not (0.0 < v < 10_000.0):
            raise ValueError(
                f"cycle_time_tmu must satisfy 0 < x < 10 000, got {v}. "
                "Values outside this range indicate a likely data-entry error."
            )
        return v

    @field_validator("num_operators")
    @classmethod
    def validate_num_operators(cls, v: int) -> int:
        """Headcount must be at least 1 and no more than 100 per simulation run."""
        if not (1 <= v <= 100):
            raise ValueError(
                f"num_operators must be 1–100, got {v}."
            )
        return v

    @field_validator("machine_rate_usd")
    @classmethod
    def validate_machine_rate(cls, v: float) -> float:
        """
        Machine rate must be non-negative and below a reasonable ceiling.
        Rates above $100 000/hr are indicative of unit errors (e.g. annual
        cost passed as hourly rate).
        """
        if not (0.0 <= v < 100_000.0):
            raise ValueError(
                f"machine_rate_usd must satisfy 0 ≤ x < 100 000, got {v}. "
                "Check that the value is an hourly rate, not an annual cost."
            )
        return v

    @model_validator(mode="after")
    def validate_cost_sanity(self) -> "RunSimulationGuard":
        """
        Cross-field check: estimated cost per unit must not exceed $10 000.
        A lower guard here surfaces implausible combinations early, before
        the simulation engine runs.
        """
        tmu_to_seconds = 0.036
        estimated_cost = (
            (self.machine_rate_usd / 3_600)
            * (self.cycle_time_tmu * tmu_to_seconds)
            * self.num_operators
        )
        if estimated_cost > 10_000.0:
            raise ValueError(
                f"Implausible parameter combination: estimated cost/unit would be "
                f"${estimated_cost:,.2f}, which exceeds the $10 000 ceiling. "
                "Verify cycle_time_tmu, num_operators, and machine_rate_usd."
            )
        return self


# ────────────────────────────────────────────────────────────────────────────
# ToolGuard — single entry point for all pre-flight checks
# ────────────────────────────────────────────────────────────────────────────

# Registry: tool_name → guard schema class
_GUARD_REGISTRY: Dict[str, Type[BaseModel]] = {
    "run_simulation": RunSimulationGuard,
}


class ToolGuard:
    """
    Stateless pre-flight security validator for SENSITIVE tools.

    Usage::

        from security.tool_sandbox import ToolGuard

        try:
            sanitized_args = ToolGuard.pre_flight_check("run_simulation", raw_args)
        except ValueError as exc:
            # Reject the action and log the reason
            ...

    ``pre_flight_check`` returns a sanitized ``dict`` on success.  The dict is
    produced by the guard model's ``model_dump()``, which guarantees that all
    field types match what the downstream tool handler expects.

    To add a new SENSITIVE tool:
    1. Create a guard model class above with the relevant ``@field_validator``
       and ``@model_validator`` rules.
    2. Add an entry to ``_GUARD_REGISTRY``.
    """

    @classmethod
    def pre_flight_check(
        cls,
        tool_name: str,
        raw_args: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Run all registered pre-flight checks for ``tool_name``.

        Args:
            tool_name: Key in the tool registry (e.g. ``"run_simulation"``).
            raw_args:  Raw argument dict from the LLM / PendingAction.

        Returns:
            Sanitized argument dict ready for the tool handler.

        Raises:
            ValueError: If any guard validator rejects the arguments.
                        The error message is human-readable and safe to surface
                        to the approving user.
        """
        guard_cls = _GUARD_REGISTRY.get(tool_name)
        if guard_cls is None:
            # No guard registered — pass through unchanged.
            # Note: this branch should never be hit for SENSITIVE_TOOLS since
            # every SENSITIVE tool must have a guard.  A warning is logged so
            # unguarded additions are visible in the audit trail.
            import logging
            logging.getLogger(__name__).warning(
                "ToolGuard: no guard schema registered for SENSITIVE tool '%s'."
                " Allowing execution without pre-flight checks.",
                tool_name,
            )
            return dict(raw_args)

        try:
            validated = guard_cls.model_validate(raw_args)
        except Exception as exc:
            # Re-raise as ValueError with a clean message (strips Pydantic internals)
            raise ValueError(str(exc)) from exc

        return validated.model_dump()
