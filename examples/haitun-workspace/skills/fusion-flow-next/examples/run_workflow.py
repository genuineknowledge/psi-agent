from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Literal, cast

from fusion_flow_next import (
    Concept,
    Operator,
    ParseContext,
    WorkflowGraphCompilation,
    WorkflowGraphCompiler,
    parse_workflow,
)

from psi_agent.workflow_execution import StepDispatcher, execute_plan, generate_plan
from psi_agent.workflow_graph import ProducesEdge, StepNode, WorkflowGraph

type Completion = Callable[[str], Awaitable[str]]
type HumanInstructionPreparer = Callable[[str], Awaitable[str]]
type HumanRequester = Callable[[str], Awaitable[object]]
type ExecutorKind = Literal["Agent", "Human", "Program"]


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    graph: WorkflowGraph
    executor_kinds: Mapping[str, ExecutorKind]


_CONCEPT_NAMES = (
    "Agent",
    "Artifact",
    "Bool",
    "ComplexNumber",
    "Human",
    "Instruction",
    "Program",
    "Step",
    "StepName",
    "Workflow",
)
_OPERATOR_NAMES = (
    "comparison_gt_op",
    "comparison_gte_op",
    "comparison_lt_op",
    "comparison_lte_op",
    "consumes",
    "input_workflow",
    "max_concurrency",
    "output_workflow",
    "produces",
    "step_executor",
    "step_instruction",
    "step_name",
)


def compile_workflow(source: str) -> CompiledWorkflow:
    """Parse and compile one workflow into an executable graph."""

    concepts = {name: Concept(name) for name in _CONCEPT_NAMES}
    operators = {name: Operator(name) for name in _OPERATOR_NAMES}
    operators["step_instruction"] = Operator(
        name="step_instruction",
        input_concepts=(concepts["Step"],),
        output_concept=concepts["Instruction"],
    )
    context = ParseContext(
        concepts=concepts,
        operators=operators,
    )
    parsed = parse_workflow(source, context=context)
    if parsed.core_ir is None:
        details = "; ".join(
            (
                diagnostic.message
                if diagnostic.span is None
                else f"{diagnostic.span.start.line}:{diagnostic.span.start.column}: {diagnostic.message}"
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
        raise ValueError("workflow contains assertions that the graph compiler cannot execute")
    executor_kinds: dict[str, ExecutorKind] = {}
    for constant in parsed.core_ir.constants:
        matches = {concept.name for concept in constant.belong_concepts} & {"Agent", "Human", "Program"}
        if len(matches) == 1:
            executor_kinds[constant.symbol] = cast(ExecutorKind, matches.pop())
    for step in compilation.graph.steps:
        executor_kinds.setdefault(step.executor_id, "Agent")
    return CompiledWorkflow(graph=compilation.graph, executor_kinds=executor_kinds)


def _build_dispatch(
    compiled: CompiledWorkflow,
    complete: Completion,
    prepare_human_instruction: HumanInstructionPreparer | None,
    request_human: HumanRequester | None,
) -> StepDispatcher:
    graph = compiled.graph
    outputs_by_step: dict[str, list[str]] = {step.step_id: [] for step in graph.steps}
    for edge in graph.edges:
        if isinstance(edge, ProducesEdge):
            outputs_by_step[edge.step_id].append(edge.artifact_id)

    async def dispatch(step: StepNode, inputs: Mapping[str, object]) -> Mapping[str, object]:
        if step.instruction_id is None:
            raise ValueError(f"step {step.step_id!r} has no step_instruction")
        output_ids = outputs_by_step[step.step_id]
        if len(output_ids) != 1:
            raise ValueError(f"step {step.step_id!r} must produce exactly one artifact")
        instruction = step.instruction_id
        if compiled.executor_kinds[step.executor_id] == "Human":
            if prepare_human_instruction is None or request_human is None:
                raise ValueError(
                    f"step {step.step_id!r} requires prepare_human_instruction and request_human callbacks"
                )
            preparation_prompt = (
                "Prepare this workflow step for a human.\n"
                f"Step: {step.step_id}\n"
                f"Instruction or reference: {instruction}\n"
                f"Inputs: {json.dumps(dict(inputs), ensure_ascii=False, sort_keys=True, default=str)}\n"
                "Produce concise, readable guidance. Decide whether referenced resources need inspection "
                "using your available tools and normal approval flow. Do not invent inaccessible contents."
            )
            prepared_instruction = await prepare_human_instruction(preparation_prompt)
            if not prepared_instruction.strip():
                raise ValueError(f"step {step.step_id!r} human instruction preparation returned no text")
            return {output_ids[0]: await request_human(prepared_instruction)}
        prompt = (
            f"Instruction: {instruction}\n"
            f"Inputs: {json.dumps(dict(inputs), ensure_ascii=False, sort_keys=True, default=str)}\n"
            "Return only the result for this workflow step."
        )
        return {output_ids[0]: await complete(prompt)}

    return dispatch


async def execute_workflow(
    source: str,
    *,
    request: str,
    complete: Completion,
    prepare_human_instruction: HumanInstructionPreparer | None = None,
    request_human: HumanRequester | None = None,
) -> dict[str, object]:
    """Execute one workflow with injected Agent completion and Human request boundaries."""

    compiled = compile_workflow(source)
    graph = compiled.graph
    return await execute_plan(
        generate_plan(graph),
        graph,
        inputs={"request": request},
        dispatch=_build_dispatch(compiled, complete, prepare_human_instruction, request_human),
    )
