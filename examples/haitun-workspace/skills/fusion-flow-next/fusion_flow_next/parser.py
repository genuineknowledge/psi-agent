"""Parse FusionFlow source into target-neutral Workflow Core IR."""

from __future__ import annotations

from .contracts import ParseResult


def parse_workflow(source: str) -> ParseResult:
    """Parse syntax and lower it without performing static workflow checks.

    Syntax failures are returned as parser diagnostics. Successful lowering
    preserves assertion equality (``==``) separately from formula comparison
    equality (``=``). Compilation and workflow execution are outside this
    boundary.

    The current stub raises only because parsing is not implemented.
    """

    del source
    raise NotImplementedError("FusionFlow Next parser is not implemented.")
