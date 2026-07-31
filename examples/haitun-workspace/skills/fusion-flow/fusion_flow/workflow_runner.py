"""Compile and execute checked FusionFlow workflows."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from os import PathLike
from os.path import isabs
from typing import Literal, cast

from .core_ir import Assertion, CompoundTerm, Concept, Constant, Operator
from .graph_compiler import WorkflowGraphCompilation, WorkflowGraphCompiler
from .parser import ParseContext, parse_workflow
from .workflow_execution import (
    CheckpointObserver,
    ContextualStepDispatcher,
    DispatchContext,
    ExecutionCheckpoint,
    ResourceAllocator,
    ResourceCapacity,
    execute_plan,
    generate_plan,
)
from .workflow_graph import ProducesEdge, StepNode, WorkflowGraph

type Completion = Callable[[str], Awaitable[object]]
type PathResolver = Callable[[str], Awaitable[str]]
type InstructionResolver = Callable[[str], Awaitable[str]]
type HumanInstructionPreparer = Callable[[str], Awaitable[str]]
type HumanRequester = Callable[[str], Awaitable[object]]
type ExecutorKind = Literal["Agent", "Human", "Program"]


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    """One executable graph plus catalog-derived executor classifications."""

    graph: WorkflowGraph
    executor_kinds: Mapping[str, ExecutorKind]
    program_paths: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CompletionContext:
    """Structured runtime contract for an Agent or Human callback."""

    step_id: str
    executor_id: str
    executor_kind: ExecutorKind
    inputs: Mapping[str, object]
    output_ids: tuple[str, ...]
    dispatch: DispatchContext


@dataclass(frozen=True, slots=True)
class ProgramInvocation:
    """Exact script and artifact contract passed to an injected Program runner."""

    name: str
    argv: tuple[str, ...]
    stdin: str
    cwd: str | PathLike[str] | None
    binding_name: str
    dispatch: DispatchContext
    instruction: str = ""
    inputs: Mapping[str, object] = field(default_factory=dict)
    output_ids: tuple[str, ...] = ()


type ContextualCompletion = Callable[
    [str, CompletionContext],
    Awaitable[object],
]
type ContextualHumanInstructionPreparer = Callable[
    [str, CompletionContext],
    Awaitable[str],
]
type ContextualHumanRequester = Callable[
    [str, CompletionContext],
    Awaitable[object],
]
type ProgramRunner = Callable[[ProgramInvocation], Awaitable[object]]


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
    "Path",
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
    "program_path": (("Program",), "Path"),
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


def _typed_constant(
    value: object,
    concept_name: str,
    context: str,
) -> Constant:
    if not isinstance(value, Constant) or not value.symbol:
        raise ValueError(f"{context} must be a non-empty constant")
    concepts = {concept.name for concept in value.belong_concepts}
    if concept_name not in concepts:
        raise ValueError(f"{context} must belong to {concept_name}")
    return value


def _extract_program_paths(
    assertions: tuple[Assertion, ...],
) -> tuple[dict[str, str], tuple[Assertion, ...]]:
    """Consume catalog-owned Program path declarations from graph residuals."""

    program_paths: dict[str, str] = {}
    residual: list[Assertion] = []
    for assertion in assertions:
        candidates = tuple(
            (term, value)
            for term, value in (
                (assertion.lhs, assertion.rhs),
                (assertion.rhs, assertion.lhs),
            )
            if isinstance(term, CompoundTerm) and term.operator.name == "program_path"
        )
        if not candidates:
            residual.append(assertion)
            continue
        if len(candidates) != 1:
            raise ValueError("one equality cannot configure multiple Program paths")

        call, value = candidates[0]
        if len(call.arguments) != 1:
            raise ValueError(f"program_path expects 1 argument, got {len(call.arguments)}")
        executor = _typed_constant(
            call.arguments[0],
            "Program",
            "program_path argument",
        )
        path = _typed_constant(value, "Path", "program_path value")
        if executor.symbol in program_paths:
            raise ValueError(f"duplicate program_path for {executor.symbol!r}")
        program_paths[executor.symbol] = path.symbol

    return program_paths, tuple(residual)


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
    program_paths, residual_assertions = _extract_program_paths(compilation.residual_assertions)
    if residual_assertions:
        counts = _residual_operator_counts(residual_assertions)
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
        if executor_kinds[step.executor_id] == "Program" and step.executor_id not in program_paths:
            raise ValueError(f"Program executor {step.executor_id!r} has no program_path")

    return CompiledWorkflow(
        graph=compilation.graph,
        executor_kinds=executor_kinds,
        program_paths=program_paths,
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


async def _build_program_paths(
    compiled: CompiledWorkflow,
    resolve_path: PathResolver | None,
) -> dict[str, str]:
    """Resolve only catalog identities; explicit absolute and ``./`` paths pass through."""

    paths: dict[str, str] = {}
    program_ids = {
        step.executor_id for step in compiled.graph.steps if compiled.executor_kinds[step.executor_id] == "Program"
    }
    for program_id in sorted(program_ids):
        path_reference = compiled.program_paths[program_id]
        if isabs(path_reference) or path_reference.startswith("./"):
            executable_path = path_reference
        else:
            if resolve_path is None:
                raise ValueError(f"Program executor {program_id!r} has a path identity but no path resolver")
            executable_path = await resolve_path(path_reference)
            if not isinstance(executable_path, str) or not executable_path.strip():
                raise ValueError(f"program_path for {program_id!r} resolved to no path")
        paths[program_id] = executable_path
    return paths


async def _materialize_instructions(
    compiled: CompiledWorkflow,
    resolve_instruction: InstructionResolver | None,
) -> dict[str, str]:
    """Resolve every instruction path before the execution plan can dispatch."""

    resolved_references: dict[str, str] = {}
    instructions: dict[str, str] = {}
    for step in sorted(compiled.graph.steps, key=lambda item: item.step_id):
        reference = step.instruction_id
        if reference is None:
            raise ValueError(f"step {step.step_id!r} has no step_instruction")
        if reference.startswith("./"):
            if resolve_instruction is None:
                raise ValueError(f"step {step.step_id!r} has an instruction path but no instruction resolver")
            if reference not in resolved_references:
                resolved_references[reference] = await resolve_instruction(reference)
            instruction = resolved_references[reference]
        else:
            instruction = reference
        if not isinstance(instruction, str) or not instruction.strip():
            raise ValueError(f"step {step.step_id!r} instruction resolved to no text")
        instructions[step.step_id] = instruction
    return instructions


def _normalize_program_stdout(
    step_id: str,
    output_ids: tuple[str, ...],
    stdout: str,
) -> dict[str, object]:
    """Keep legacy scalar stdout while extending Program steps to N outputs."""

    if len(output_ids) <= 1:
        result: object = stdout if output_ids or stdout else None
        return _normalize_outputs(
            step_id,
            output_ids,
            result,
            named_mapping_required=False,
        )

    def reject_non_finite_constant(value: str) -> object:
        raise ValueError(f"non-finite JSON constant {value!r}")

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key {key!r}")
            result[key] = value
        return result

    try:
        result = json.loads(
            stdout,
            parse_constant=reject_non_finite_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
        # ``json.loads("1e400")`` produces infinity without invoking
        # ``parse_constant``. Re-encoding with ``allow_nan=False`` validates
        # every nested number and catches that overflow case as well.
        json.dumps(result, allow_nan=False)
    except (json.JSONDecodeError, OverflowError, ValueError) as error:
        raise ValueError(f"Program step {step_id!r} must write a strict JSON object keyed by artifact ID") from error
    return _normalize_outputs(
        step_id,
        output_ids,
        result,
        named_mapping_required=True,
    )


def _build_dispatch(
    compiled: CompiledWorkflow,
    *,
    instructions: Mapping[str, str],
    program_paths: Mapping[str, str],
    work_dir: str | PathLike[str] | None,
    complete: Completion | None,
    contextual_complete: ContextualCompletion | None,
    run_program: ProgramRunner | None,
    prepare_human_instruction: HumanInstructionPreparer | None,
    request_human: HumanRequester | None,
    contextual_prepare_human_instruction: ContextualHumanInstructionPreparer | None,
    contextual_request_human: ContextualHumanRequester | None,
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
        output_ids = tuple(sorted(outputs_by_step[step.step_id]))
        output_contract = _output_contract(output_ids)
        instruction = instructions[step.step_id]
        executor_kind = compiled.executor_kinds[step.executor_id]
        completion_context = CompletionContext(
            step_id=step.step_id,
            executor_id=step.executor_id,
            executor_kind=executor_kind,
            inputs=dict(inputs),
            output_ids=output_ids,
            dispatch=dispatch_context,
        )
        if executor_kind == "Human":
            has_legacy_callbacks = prepare_human_instruction is not None and request_human is not None
            has_contextual_callbacks = (
                contextual_prepare_human_instruction is not None and contextual_request_human is not None
            )
            if not has_legacy_callbacks and not has_contextual_callbacks:
                raise ValueError(
                    f"step {step.step_id!r} requires prepare_human_instruction and request_human callbacks"
                )
            preparation_prompt = (
                "Prepare this workflow step for a human.\n"
                f"Step: {step.step_id}\n"
                f"Instruction:\n{instruction}\n\n"
                f"Inputs: "
                f"{json.dumps(dict(inputs), ensure_ascii=False, sort_keys=True, default=str)}\n"
                f"Output contract: {output_contract}\n"
                "Produce concise, readable guidance. Use available tools only when "
                "needed to inspect supporting resources named by the inputs. Do not ask the human "
                "directly, change resources, or invent inaccessible contents."
            )
            if contextual_prepare_human_instruction is not None:
                prepared_instruction = await contextual_prepare_human_instruction(
                    preparation_prompt,
                    completion_context,
                )
            else:
                if prepare_human_instruction is None:
                    raise AssertionError("Human callback preflight did not select an instruction preparer")
                prepared_instruction = await prepare_human_instruction(preparation_prompt)
            if not prepared_instruction.strip():
                raise ValueError(f"step {step.step_id!r} human instruction preparation returned no text")
            if contextual_request_human is not None:
                human_result = await contextual_request_human(
                    prepared_instruction,
                    completion_context,
                )
            else:
                if request_human is None:
                    raise AssertionError("Human callback preflight did not select a requester")
                human_result = await request_human(prepared_instruction)
            return _normalize_outputs(
                step.step_id,
                output_ids,
                human_result,
                named_mapping_required=False,
            )

        if executor_kind == "Program":
            try:
                payload = json.dumps(
                    {
                        "instruction": instruction,
                        "inputs": dict(inputs),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    allow_nan=False,
                )
            except (TypeError, ValueError) as error:
                raise ValueError(f"Program step {step.step_id!r} inputs must be finite JSON values") from error
            if run_program is None:
                raise AssertionError("Program runner preflight did not select a runner")
            program_result = await run_program(
                ProgramInvocation(
                    name=step.executor_id,
                    argv=(program_paths[step.executor_id],),
                    stdin=f"{payload}\n",
                    cwd=work_dir,
                    binding_name=step.step_id,
                    dispatch=dispatch_context,
                    instruction=instruction,
                    inputs=dict(inputs),
                    output_ids=output_ids,
                )
            )
            if isinstance(program_result, str):
                return _normalize_program_stdout(
                    step.step_id,
                    output_ids,
                    program_result,
                )
            return _normalize_outputs(
                step.step_id,
                output_ids,
                program_result,
                named_mapping_required=True,
            )

        prompt = (
            f"Instruction:\n{instruction}\n\n"
            f"Inputs: "
            f"{json.dumps(dict(inputs), ensure_ascii=False, sort_keys=True, default=str)}\n"
            f"{output_contract}"
        )
        if contextual_complete is not None:
            result = await contextual_complete(
                prompt,
                completion_context,
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
    complete: Completion | None = None,
    contextual_complete: ContextualCompletion | None = None,
    request: str | None = None,
    inputs: Mapping[str, object] | None = None,
    resource_capacities: Mapping[str, ResourceCapacity] | None = None,
    allocator: ResourceAllocator | None = None,
    parse_context: ParseContext | None = None,
    strict_executors: bool = False,
    supported_executor_kinds: Collection[ExecutorKind] | None = None,
    resolve_path: PathResolver | None = None,
    resolve_instruction: InstructionResolver | None = None,
    work_dir: str | PathLike[str] | None = None,
    run_program: ProgramRunner | None = None,
    prepare_human_instruction: HumanInstructionPreparer | None = None,
    request_human: HumanRequester | None = None,
    contextual_prepare_human_instruction: ContextualHumanInstructionPreparer | None = None,
    contextual_request_human: ContextualHumanRequester | None = None,
    checkpoint: ExecutionCheckpoint | None = None,
    checkpoint_observer: CheckpointObserver | None = None,
) -> dict[str, object]:
    """Execute one checked workflow with explicit dispatcher/runtime injection."""

    if complete is not None and contextual_complete is not None:
        raise ValueError("provide exactly one of complete or contextual_complete")
    if prepare_human_instruction is not None and contextual_prepare_human_instruction is not None:
        raise ValueError("provide exactly one of prepare_human_instruction or contextual_prepare_human_instruction")
    if request_human is not None and contextual_request_human is not None:
        raise ValueError("provide exactly one of request_human or contextual_request_human")
    if (prepare_human_instruction is None) != (request_human is None):
        raise ValueError("provide prepare_human_instruction and request_human together")
    if (contextual_prepare_human_instruction is None) != (contextual_request_human is None):
        raise ValueError("provide contextual_prepare_human_instruction and contextual_request_human together")
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
        strict_executors=strict_executors,
    )
    if supported_executor_kinds is not None:
        supported = frozenset(supported_executor_kinds)
        unsupported = sorted(
            (
                step.step_id,
                compiled.executor_kinds[step.executor_id],
            )
            for step in compiled.graph.steps
            if compiled.executor_kinds[step.executor_id] not in supported
        )
        if unsupported:
            details = ", ".join(f"{step_id}={kind}" for step_id, kind in unsupported)
            raise ValueError(f"workflow contains unsupported executors: {details}")

    graph = compiled.graph
    if (
        any(compiled.executor_kinds[step.executor_id] == "Agent" for step in graph.steps)
        and complete is None
        and contextual_complete is None
    ):
        raise ValueError("provide exactly one of complete or contextual_complete")
    instructions = await _materialize_instructions(compiled, resolve_instruction)
    program_paths = await _build_program_paths(compiled, resolve_path)
    if work_dir is None and any(not isabs(path) for path in program_paths.values()):
        raise ValueError("relative program_path requires an explicit work_dir")
    if program_paths and run_program is None:
        raise ValueError("Program workflow requires an injected run_program callback")
    plan = generate_plan(graph)
    dispatch = _build_dispatch(
        compiled,
        instructions=instructions,
        program_paths=program_paths,
        work_dir=work_dir,
        complete=complete,
        contextual_complete=contextual_complete,
        run_program=run_program,
        prepare_human_instruction=prepare_human_instruction,
        request_human=request_human,
        contextual_prepare_human_instruction=contextual_prepare_human_instruction,
        contextual_request_human=contextual_request_human,
    )

    return await execute_plan(
        plan,
        graph,
        inputs=workflow_inputs,
        contextual_dispatch=dispatch,
        resource_capacities=resource_capacities,
        allocator=allocator,
        checkpoint=checkpoint,
        checkpoint_observer=checkpoint_observer,
    )
