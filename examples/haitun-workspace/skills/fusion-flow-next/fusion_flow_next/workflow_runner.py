"""Compile and execute checked FusionFlow workflows."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, cast

from psi_agent.workflow_execution import (
    ContextualStepDispatcher,
    DispatchContext,
    ResourceAllocator,
    ResourceCapacity,
    execute_plan,
    generate_plan,
)
from psi_agent.workflow_graph import ProducesEdge, StepNode, WorkflowGraph, WorkflowPolicy

from .core_ir import Assertion, CompoundTerm, Concept, Operator
from .graph_compiler import WorkflowGraphCompilation, WorkflowGraphCompiler
from .parser import ParseContext, parse_workflow

type Completion = Callable[[str], Awaitable[object]]
type ExecutorKind = Literal["Agent", "Human", "Program"]


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    """One executable graph plus catalog-derived executor classifications."""

    graph: WorkflowGraph
    executor_kinds: Mapping[str, ExecutorKind]


@dataclass(frozen=True, slots=True)
class CompletionContext:
    """Structured runtime contract for an Agent completion."""

    step_id: str
    executor_id: str
    executor_kind: ExecutorKind
    inputs: Mapping[str, object]
    output_ids: tuple[str, ...]
    dispatch: DispatchContext


type ContextualCompletion = Callable[
    [str, CompletionContext],
    Awaitable[object],
]

MAX_STEPS = 32
MAX_CONCURRENCY = 8
MAX_WORKFLOW_TIMEOUT_SECONDS = 15 * 60
MAX_INSTRUCTION_BODY_BYTES = 16 * 1024
MAX_INSTRUCTION_BODIES_BYTES = 128 * 1024


_CONCEPT_NAMES = (
    "Agent",
    "ApiBase",
    "Artifact",
    "Bool",
    "ComplexNumber",
    "Engine",
    "Executor",
    "Human",
    "Instruction",
    "Integer",
    "List",
    "Model",
    "Program",
    "ReasoningEffort",
    "Resource",
    "Step",
    "StepName",
    "Workflow",
)
# This is an explicit catalog, not a source-code name discovery mechanism.
# ``step_executor`` deliberately has no output concept because the minimal
# parser does not model Agent/Human/Program as sub-concepts of Executor.
_OPERATOR_SIGNATURES: Mapping[
    str,
    tuple[tuple[str, ...], str | None],
] = {
    "agent_config": (("Agent", "Model", "Engine", "ApiBase"), "Bool"),
    "comparison_gt_op": ((), None),
    "comparison_gte_op": ((), None),
    "comparison_lt_op": ((), None),
    "comparison_lte_op": ((), None),
    "consumes": (("Step",), "List"),
    "depends_on": (("Step", "Step"), "Bool"),
    "foreach_item": (("Step", "List"), "Artifact"),
    "independent": (("Step",), "Bool"),
    "input_workflow": (("Workflow",), "List"),
    "max_attempts": (("Step",), "Integer"),
    "max_concurrency": (("Workflow",), "Integer"),
    "max_output_tokens": (("Agent",), "Integer"),
    "max_turns": (("Agent",), "Integer"),
    "output_workflow": (("Workflow",), "List"),
    "produces": (("Step",), "List"),
    "reasoning_effort": (("Agent",), "ReasoningEffort"),
    "resource_requirement": (("Step", "Resource"), "Integer"),
    "step_executor": (("Step",), None),
    "step_instruction": (("Step",), "Instruction"),
    "step_name": (("Step",), "StepName"),
    "step_timeout": (("Step",), "Integer"),
    "temperature": (("Agent",), "ComplexNumber"),
    "workflow_timeout": (("Workflow",), "Integer"),
}


def _default_parse_context() -> ParseContext:
    """Build the runner's closed, typed operator catalog."""

    concepts = {name: Concept(name) for name in _CONCEPT_NAMES}
    operators = {
        name: Operator(
            name=name,
            input_concepts=tuple(concepts[concept_name] for concept_name in inputs),
            output_concept=None if output is None else concepts[output],
        )
        for name, (inputs, output) in _OPERATOR_SIGNATURES.items()
    }
    return ParseContext(concepts=concepts, operators=operators)


def _residual_operator_counts(
    assertions: tuple[Assertion, ...],
) -> Counter[str]:
    """Name every unconsumed assertion without dropping ordinary equalities."""

    counts: Counter[str] = Counter()
    for assertion in assertions:
        calls = [term.operator.name for term in (assertion.lhs, assertion.rhs) if isinstance(term, CompoundTerm)]
        counts.update(calls or ("<equality>",))
    return counts


def compile_workflow(
    source: str,
    *,
    context: ParseContext | None = None,
    strict_executors: bool = False,
) -> CompiledWorkflow:
    """Parse and compile one workflow through a closed catalog by default.

    Existing workflows that predate typed executor declarations keep their
    historical Agent default.  New entry points can opt into strict executor
    typing so catalog mistakes fail before dispatch.
    """

    parsed = parse_workflow(
        source,
        context=context if context is not None else _default_parse_context(),
    )
    if parsed.core_ir is None:
        details = "; ".join(
            (
                diagnostic.message
                if diagnostic.span is None
                else (f"{diagnostic.span.start.line}:{diagnostic.span.start.column}: {diagnostic.message}")
            )
            for diagnostic in parsed.diagnostics
        )
        raise ValueError(f"workflow parse failed: {details}")

    compiled = WorkflowGraphCompiler().compile(parsed.core_ir)
    if not isinstance(compiled, tuple):
        raise TypeError("workflow graph compiler returned an unexpected result")
    compilations = cast(tuple[WorkflowGraphCompilation, ...], compiled)
    if len(compilations) != 1:
        raise ValueError("workflow runner expects exactly one workflow")
    compilation = compilations[0]
    if compilation.residual_assertions:
        counts = _residual_operator_counts(compilation.residual_assertions)
        details = ", ".join(f"{operator_name}={count}" for operator_name, count in sorted(counts.items()))
        raise ValueError(f"workflow contains unconsumed assertions: {details}")

    constants_by_symbol = {constant.symbol: constant for constant in parsed.core_ir.constants}
    executor_kinds: dict[str, ExecutorKind] = {}
    for step in compilation.graph.steps:
        executor = constants_by_symbol.get(step.executor_id)
        matches = (
            set()
            if executor is None
            else {concept.name for concept in executor.belong_concepts if concept.name in {"Agent", "Human", "Program"}}
        )
        if not matches and not strict_executors:
            executor_kinds[step.executor_id] = "Agent"
            continue
        if len(matches) != 1:
            raise ValueError(
                f"executor {step.executor_id!r} for step {step.step_id!r} "
                "must be declared as exactly one of Agent, Human, or Program"
            )
        executor_kinds[step.executor_id] = cast(ExecutorKind, matches.pop())

    return CompiledWorkflow(
        graph=compilation.graph,
        executor_kinds=executor_kinds,
    )


def _normalize_outputs(
    step_id: str,
    output_ids: tuple[str, ...],
    result: object,
    *,
    named_mapping_required: bool,
) -> dict[str, object]:
    """Normalize legacy scalar results while keeping N-output calls explicit."""

    if not output_ids:
        if result is None or (isinstance(result, Mapping) and not result):
            return {}
        raise ValueError(f"step {step_id!r} produces no artifacts")

    if len(output_ids) == 1 and not named_mapping_required:
        return {output_ids[0]: result}

    if not isinstance(result, Mapping) or not all(isinstance(artifact_id, str) for artifact_id in result):
        raise ValueError(f"step {step_id!r} must return a mapping keyed by artifact ID")
    outputs = dict(result)
    expected_outputs = set(output_ids)
    actual_outputs = set(outputs)
    if actual_outputs != expected_outputs:
        raise ValueError(
            f"outputs for {step_id!r} must match exactly: "
            f"expected {sorted(expected_outputs)}, got {sorted(actual_outputs)}"
        )
    return outputs


def _output_contract(output_ids: tuple[str, ...]) -> str:
    if not output_ids:
        return "Return no artifact value for this step."
    if len(output_ids) == 1:
        return f"Return the value for output artifact {output_ids[0]!r}."
    return f"Return a mapping keyed exactly by these output artifact IDs: {json.dumps(output_ids, ensure_ascii=False)}."


def validate_instruction_bodies(
    compiled: CompiledWorkflow,
    instruction_bodies: Mapping[str, str],
) -> dict[str, str]:
    """Validate the caller-supplied bodies before any step completion starts."""

    if not isinstance(instruction_bodies, Mapping):
        raise ValueError("instruction_bodies must be a mapping")
    if not all(isinstance(key, str) and isinstance(value, str) for key, value in instruction_bodies.items()):
        raise ValueError("instruction_bodies must map string instruction IDs to string bodies")

    steps_without_instructions = sorted(step.step_id for step in compiled.graph.steps if step.instruction_id is None)
    if steps_without_instructions:
        raise ValueError(f"every step must declare step_instruction before dispatch: {steps_without_instructions}")

    expected = {cast(str, step.instruction_id) for step in compiled.graph.steps}
    actual = set(instruction_bodies)
    if actual != expected:
        raise ValueError(
            "instruction_bodies must match workflow instruction IDs exactly: "
            f"expected {sorted(expected)}, got {sorted(actual)}"
        )

    normalized: dict[str, str] = {}
    total_bytes = 0
    for instruction_id in sorted(expected):
        body = instruction_bodies[instruction_id].strip()
        if not body or body == instruction_id:
            raise ValueError(f"instruction body for {instruction_id!r} must contain the actual instruction")
        body_bytes = len(body.encode("utf-8"))
        if body_bytes > MAX_INSTRUCTION_BODY_BYTES:
            raise ValueError(
                f"instruction body for {instruction_id!r} exceeds {MAX_INSTRUCTION_BODY_BYTES} UTF-8 bytes"
            )
        total_bytes += body_bytes
        normalized[instruction_id] = body
    if total_bytes > MAX_INSTRUCTION_BODIES_BYTES:
        raise ValueError(f"instruction bodies exceed {MAX_INSTRUCTION_BODIES_BYTES} UTF-8 bytes in total")
    return normalized


def _build_dispatch(
    compiled: CompiledWorkflow,
    *,
    complete: Completion | None,
    contextual_complete: ContextualCompletion | None,
    instruction_bodies: Mapping[str, str],
) -> ContextualStepDispatcher:
    graph = compiled.graph
    outputs_by_step: dict[str, list[str]] = {step.step_id: [] for step in graph.steps}
    for edge in graph.edges:
        if isinstance(edge, ProducesEdge):
            outputs_by_step[edge.step_id].append(edge.artifact_id)

    async def dispatch(
        step: StepNode,
        inputs: Mapping[str, object],
        dispatch_context: DispatchContext,
    ) -> Mapping[str, object]:
        if step.instruction_id is None:
            raise ValueError(f"step {step.step_id!r} has no step_instruction")

        output_ids = tuple(sorted(outputs_by_step[step.step_id]))
        output_contract = _output_contract(output_ids)
        instruction_id = step.instruction_id
        instruction_body = instruction_bodies[instruction_id]
        executor_kind = compiled.executor_kinds[step.executor_id]

        prompt = (
            f"Instruction ID: {instruction_id}\n"
            f"Instruction body:\n{instruction_body}\n"
            f"Inputs: "
            f"{json.dumps(dict(inputs), ensure_ascii=False, sort_keys=True, default=str, allow_nan=False)}\n"
            f"{output_contract}"
        )
        if contextual_complete is not None:
            result = await contextual_complete(
                prompt,
                CompletionContext(
                    step_id=step.step_id,
                    executor_id=step.executor_id,
                    executor_kind=executor_kind,
                    inputs=dict(inputs),
                    output_ids=output_ids,
                    dispatch=dispatch_context,
                ),
            )
            return _normalize_outputs(
                step.step_id,
                output_ids,
                result,
                named_mapping_required=True,
            )

        if complete is None:
            raise AssertionError("completion preflight did not select a completion")
        return _normalize_outputs(
            step.step_id,
            output_ids,
            await complete(prompt),
            named_mapping_required=False,
        )

    return dispatch


async def execute_workflow(
    source: str,
    *,
    instruction_bodies: Mapping[str, str],
    complete: Completion | None = None,
    contextual_complete: ContextualCompletion | None = None,
    request: str | None = None,
    inputs: Mapping[str, object] | None = None,
    resource_capacities: Mapping[str, ResourceCapacity] | None = None,
    allocator: ResourceAllocator | None = None,
    parse_context: ParseContext | None = None,
) -> dict[str, object]:
    """Execute the bounded, one-shot Agent subset of one checked workflow."""

    if (complete is None) == (contextual_complete is None):
        raise ValueError("provide exactly one of complete or contextual_complete")
    if inputs is None:
        if request is None:
            raise ValueError("either request or inputs must be provided")
        workflow_inputs: Mapping[str, object] = {"request": request}
    else:
        if request is not None:
            raise ValueError("request and inputs are mutually exclusive")
        workflow_inputs = dict(inputs)

    compiled = compile_workflow(
        source,
        context=parse_context,
        strict_executors=True,
    )
    unsupported = sorted(
        (
            step.step_id,
            compiled.executor_kinds[step.executor_id],
        )
        for step in compiled.graph.steps
        if compiled.executor_kinds[step.executor_id] != "Agent"
    )
    if unsupported:
        details = ", ".join(f"{step_id}={kind}" for step_id, kind in unsupported)
        raise ValueError(f"fusion-flow-next currently supports Agent executors only: {details}")

    graph = compiled.graph
    if len(graph.steps) > MAX_STEPS:
        raise ValueError(f"workflow has {len(graph.steps)} steps; maximum is {MAX_STEPS}")
    declared_concurrency = graph.policy.max_concurrency
    if declared_concurrency is not None and declared_concurrency > MAX_CONCURRENCY:
        raise ValueError(f"workflow max_concurrency exceeds {MAX_CONCURRENCY}")
    declared_timeout = graph.policy.timeout_seconds
    if declared_timeout is not None and declared_timeout > MAX_WORKFLOW_TIMEOUT_SECONDS:
        raise ValueError(f"workflow timeout exceeds {MAX_WORKFLOW_TIMEOUT_SECONDS} seconds")

    instruction_bodies = validate_instruction_bodies(compiled, instruction_bodies)
    graph = replace(
        graph,
        policy=WorkflowPolicy(
            max_concurrency=declared_concurrency or MAX_CONCURRENCY,
            timeout_seconds=declared_timeout or MAX_WORKFLOW_TIMEOUT_SECONDS,
        ),
    )
    compiled = replace(compiled, graph=graph)
    plan = generate_plan(graph)
    return await execute_plan(
        plan,
        graph,
        inputs=workflow_inputs,
        contextual_dispatch=_build_dispatch(
            compiled,
            complete=complete,
            contextual_complete=contextual_complete,
            instruction_bodies=instruction_bodies,
        ),
        resource_capacities=resource_capacities,
        allocator=allocator,
    )
