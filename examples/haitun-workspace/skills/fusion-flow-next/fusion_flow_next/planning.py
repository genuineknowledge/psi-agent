"""Report missing DSL syntax for planned steps before workflow authoring.

Each ``PlannedStep`` maps to one catalog ``Step`` identity. Workflow authoring
expands it into that typed constant and the assertions that describe it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import Diagnostic


@dataclass(frozen=True, slots=True)
class PlannedSyntax:
    """DSL syntax required by a planned step.

    ``name=None`` means no matching syntax was found; callers must not invent
    one. Non-null names must be non-empty after trimming.
    """

    description: str
    name: str | None


@dataclass(frozen=True, slots=True)
class PlannedStep:
    """One planned Step with its required syntax mappings."""

    id: str
    description: str
    syntax: tuple[PlannedSyntax, ...]


@dataclass(frozen=True, slots=True)
class PlanningCheckResult:
    """Whether declared steps can be authored, independent of later phases."""

    can_author_workflow: bool
    diagnostics: tuple[Diagnostic, ...]


def check_planned_steps(
    steps: tuple[PlannedStep, ...],
    available_syntax_names: tuple[str, ...],
) -> PlanningCheckResult:
    """Check planned steps after planning and before authoring the DSL.

    The caller supplies syntax names that actually exist; a non-empty mapping
    is not assumed to be available. Missing, blank, unavailable, or empty
    mappings are normal diagnostics and make ``can_author_workflow`` false.
    This phase checks declared items only and cannot prove the planner listed
    every required step. It does not imply parse, compile, or execution
    success.

    The current stub raises only because this phase is not implemented.
    """

    del steps, available_syntax_names
    raise NotImplementedError("FusionFlow Next planning check is not implemented.")
