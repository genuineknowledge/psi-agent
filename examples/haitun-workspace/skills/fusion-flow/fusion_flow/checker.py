"""Static workflow checks over successfully parsed Core IR.

The executable graph compiler remains the authority for graph semantics. The
checker converts its failures into diagnostics and adds runner preflight rules
that otherwise fail only after execution has started.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection

from .contracts import CheckResult, Diagnostic
from .core_ir import Assertion, CompoundTerm, Constant, WorkflowFile
from .graph_compiler import WorkflowGraphCompilation, WorkflowGraphCompiler

_EXECUTOR_KINDS = {"Agent", "Human", "Program"}


def _diagnostic(message: str, *, warning: bool = False) -> Diagnostic:
    return Diagnostic(severity="warning" if warning else "error", message=message)


def _functional_call(assertion: Assertion) -> tuple[CompoundTerm, Constant] | None:
    if isinstance(assertion.lhs, CompoundTerm) and isinstance(assertion.rhs, Constant):
        return assertion.lhs, assertion.rhs
    if isinstance(assertion.rhs, CompoundTerm) and isinstance(assertion.lhs, Constant):
        return assertion.rhs, assertion.lhs
    return None


def _untyped_executor_warning(*, executor_id: str, step_id: str) -> Diagnostic:
    return _diagnostic(
        f"executor {executor_id!r} for step {step_id!r} has no explicit "
        "Agent, Human, or Program type; legacy execution defaults it to Agent",
        warning=True,
    )


def collect_core_ir_diagnostics(core_ir: WorkflowFile) -> tuple[Diagnostic, ...]:
    """Collect diagnostics that do not require a successfully lowered graph."""

    diagnostics: list[Diagnostic] = []
    workflow_names = [workflow.name for workflow in core_ir.workflows]
    if len(workflow_names) != 1:
        diagnostics.append(_diagnostic(f"workflow file must contain exactly one workflow, got {len(workflow_names)}"))
    duplicates = sorted(name for name, count in Counter(workflow_names).items() if count > 1)
    if duplicates:
        diagnostics.append(_diagnostic(f"duplicate workflow names: {duplicates}"))

    constants = {constant.symbol: constant for constant in core_ir.constants}
    warned: set[tuple[str, str]] = set()
    for workflow in core_ir.workflows:
        for assertion in workflow.assertions:
            pair = _functional_call(assertion)
            if pair is None or pair[0].operator.name != "step_executor":
                continue
            call, executor_value = pair
            if len(call.arguments) != 1 or not isinstance(call.arguments[0], Constant):
                continue
            step = call.arguments[0]
            executor = constants.get(executor_value.symbol, executor_value)
            if not step.symbol or not executor.symbol or executor.belong_concepts:
                continue
            warning_key = (step.symbol, executor.symbol)
            if warning_key in warned:
                continue
            warned.add(warning_key)
            diagnostics.append(
                _untyped_executor_warning(
                    executor_id=executor.symbol,
                    step_id=step.symbol,
                )
            )
    return tuple(diagnostics)


def _program_paths(
    residual: tuple[Assertion, ...],
) -> tuple[dict[str, str], tuple[Assertion, ...], tuple[Diagnostic, ...]]:
    paths: dict[str, str] = {}
    unsupported: list[Assertion] = []
    diagnostics: list[Diagnostic] = []
    for assertion in residual:
        pair = _functional_call(assertion)
        if pair is None or pair[0].operator.name != "program_path":
            unsupported.append(assertion)
            continue
        call, value = pair
        if len(call.arguments) != 1 or not isinstance(call.arguments[0], Constant):
            diagnostics.append(_diagnostic("program_path expects one Program identity"))
            continue
        executor = call.arguments[0]
        executor_concepts = {concept.name for concept in executor.belong_concepts}
        value_concepts = {concept.name for concept in value.belong_concepts}
        if "Program" not in executor_concepts:
            diagnostics.append(_diagnostic(f"program_path owner {executor.symbol!r} must be a Program"))
            continue
        if "Path" not in value_concepts:
            diagnostics.append(_diagnostic(f"program_path for {executor.symbol!r} must be a Path"))
            continue
        if executor.symbol in paths:
            diagnostics.append(_diagnostic(f"duplicate program_path for {executor.symbol!r}"))
            continue
        paths[executor.symbol] = value.symbol
    return paths, tuple(unsupported), tuple(diagnostics)


def check_workflow(
    core_ir: WorkflowFile,
    *,
    graph_compilations: tuple[WorkflowGraphCompilation, ...] | None = None,
    consumed_residual_operators: Collection[str] = (),
) -> CheckResult:
    """Validate one parsed workflow file without executing or rewriting it.

    Diagnostics have no source spans because Core IR intentionally does not
    retain tokens. Parse diagnostics continue to own exact source locations.

    ``graph_compilations`` lets the runner reuse its authoritative compilation
    instead of traversing the same Core IR twice. Standalone checker callers
    omit it and receive the same validation through an internal compilation.

    ``consumed_residual_operators`` declares the closed set of residual
    operators that the calling backend will validate and consume after this
    generic check. Assertions containing any other residual operator remain
    errors; passing a name here never makes an unknown assertion executable.
    """

    diagnostics = list(collect_core_ir_diagnostics(core_ir))

    compiled: object = graph_compilations
    if compiled is None:
        # Graph semantics stay centralized in WorkflowGraphCompiler; the
        # checker translates its fail-fast exceptions into ordinary diagnostics.
        try:
            compiled = WorkflowGraphCompiler().compile(core_ir)
        except (TypeError, ValueError) as error:
            diagnostics.append(_diagnostic(str(error)))
            return CheckResult(core_ir=core_ir, diagnostics=tuple(diagnostics))

    if not isinstance(compiled, tuple):
        diagnostics.append(_diagnostic("workflow graph compiler returned an unexpected result"))
        return CheckResult(core_ir=core_ir, diagnostics=tuple(diagnostics))

    constants = {constant.symbol: constant for constant in core_ir.constants}
    for compilation in compiled:
        if not isinstance(compilation, WorkflowGraphCompilation):
            diagnostics.append(_diagnostic("workflow graph compiler returned an unexpected compilation"))
            continue
        # Program paths are runner-owned residual assertions. Consume exactly
        # that catalog extension before reporting every other residual as an
        # unsupported workflow contract.
        program_paths, unsupported, path_diagnostics = _program_paths(compilation.residual_assertions)
        diagnostics.extend(path_diagnostics)
        backend_operators = frozenset(consumed_residual_operators)
        unconsumed: list[Assertion] = []
        for assertion in unsupported:
            operator_names = {
                term.operator.name for term in (assertion.lhs, assertion.rhs) if isinstance(term, CompoundTerm)
            }
            if operator_names and operator_names <= backend_operators:
                continue
            unconsumed.append(assertion)
        unsupported = tuple(unconsumed)
        if unsupported:
            counts: Counter[str] = Counter()
            for assertion in unsupported:
                pair = _functional_call(assertion)
                counts[pair[0].operator.name if pair is not None else "<equality>"] += 1
            details = ", ".join(f"{name}={count}" for name, count in sorted(counts.items()))
            diagnostics.append(_diagnostic(f"workflow contains unsupported assertions: {details}"))

        for artifact in compilation.graph.artifacts:
            declaration = constants.get(artifact.artifact_id)
            concepts = set() if declaration is None else {concept.name for concept in declaration.belong_concepts}
            # Graph relations establish the Artifact role. Untyped constants
            # remain valid; reject only an explicit, incompatible declaration.
            if concepts and "Artifact" not in concepts:
                diagnostics.append(
                    _diagnostic(
                        f"graph value {artifact.artifact_id!r} must be untyped or belong to Artifact, "
                        f"got {sorted(concepts)}"
                    )
                )

        for step in compilation.graph.steps:
            if step.instruction_id is None or not step.instruction_id.strip():
                diagnostics.append(_diagnostic(f"step {step.step_id!r} has no step_instruction"))

            executor = constants.get(step.executor_id)
            kinds = (
                set()
                if executor is None
                else {concept.name for concept in executor.belong_concepts if concept.name in _EXECUTOR_KINDS}
            )
            if executor is None or not executor.belong_concepts:
                warning = _untyped_executor_warning(
                    executor_id=step.executor_id,
                    step_id=step.step_id,
                )
                if warning not in diagnostics:
                    diagnostics.append(warning)
            elif len(kinds) != 1:
                diagnostics.append(
                    _diagnostic(
                        f"executor {step.executor_id!r} for step {step.step_id!r} "
                        "must be declared as exactly one of Agent, Human, or Program"
                    )
                )
            elif "Program" in kinds and step.executor_id not in program_paths:
                diagnostics.append(_diagnostic(f"Program executor {step.executor_id!r} has no program_path"))

    return CheckResult(core_ir=core_ir, diagnostics=tuple(diagnostics))
