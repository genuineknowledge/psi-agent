"""Static workflow checks over successfully parsed Core IR."""

from __future__ import annotations

from .contracts import CheckResult
from .core_ir import WorkflowFile


def check_workflow(core_ir: WorkflowFile) -> CheckResult:
    """Validate workflow semantics without parsing, compiling, or executing.

    The checker owns operator contracts and arity, duplicates, reference
    integrity, and other static workflow rules. For example, S01 requires the
    first argument of ``input_workflow`` and ``output_workflow`` to equal the
    enclosing workflow name. Ordinary workflow failures belong in the returned
    diagnostics; this stub raises only because checking is not implemented.

    This phase validates Core IR but does not build or rewrite backend shapes.
    """

    del core_ir
    raise NotImplementedError("FusionFlow checker is not implemented.")
