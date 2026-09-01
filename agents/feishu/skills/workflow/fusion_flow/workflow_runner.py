"""Compile and execute checked FusionFlow workflows."""

from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Awaitable, Callable, Collection, Mapping
from dataclasses import dataclass, field
from os import PathLike
from os.path import isabs
from typing import Literal, cast

from .checker import check_workflow, collect_core_ir_diagnostics
from .contracts import Diagnostic
from .core_ir import Assertion, CompoundTerm, Concept, Constant, Operator
from .execution.model import AgentConfig
from .graph_compiler import WorkflowGraphCompilation, WorkflowGraphCompiler
from .parser import ParseContext, parse_workflow
from .step_timing import StepTiming, StepTimingMetadata
from .workflow_execution import (
    CheckpointObserver,
    DispatchContext,
    ExecutionCheckpoint,
    ResourceAllocator,
    ResourceCapacity,
    StepDispatcher,
    execute_plan,
    generate_plan,
)
from .workflow_graph import ForeachEdge, ProducesEdge, StepNode, WorkflowGraph

type PathResolver = Callable[[str], Awaitable[str]]
type InstructionResolver = Callable[[str], Awaitable[str]]
type ExecutorKind = Literal["Agent", "Human", "Program"]


@dataclass(frozen=True, slots=True)
class CompiledAgentConfig:
    """G4 Agent settings before the workspace adds its fixed safety prompt.

    ``system_prompt`` is an optional specialization overlay, never the complete
    system prompt.  This keeps workspace policy out of the language compiler
    while still making every declarative setting available to the runtime.
    """

    name: str
    system_prompt: str | None = None
    model: str | None = None
    engine: str | None = None
    api_base: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None
    tools: tuple[str, ...] = ()
    max_turns: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "tools", tuple(self.tools))

    def to_agent_config(self, base_system_prompt: str) -> AgentConfig:
        """Finalize this overlay against a caller-owned fixed system prompt."""

        if not isinstance(base_system_prompt, str) or not base_system_prompt.strip():
            raise ValueError("base_system_prompt must be a non-empty string")
        system_prompt = base_system_prompt.rstrip()
        if self.system_prompt is not None:
            system_prompt = f"{system_prompt}\n\n# Workflow agent specialization\n{self.system_prompt.strip()}"
        return AgentConfig(
            name=self.name,
            system_prompt=system_prompt,
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            engine=self.engine,
            tools=self.tools,
            max_turns=self.max_turns,
            api_base=self.api_base,
            reasoning_effort=self.reasoning_effort,
        )


@dataclass(frozen=True, slots=True)
class CompiledWorkflow:
    """One executable graph plus classifications and non-fatal diagnostics."""

    graph: WorkflowGraph
    executor_kinds: Mapping[str, ExecutorKind]
    program_paths: Mapping[str, str] = field(default_factory=dict)
    agent_configs: Mapping[str, CompiledAgentConfig] = field(default_factory=dict)
    diagnostics: tuple[Diagnostic, ...] = ()


@dataclass(frozen=True, slots=True)
class CompletionContext:
    """Structured runtime contract for an Agent or Human callback."""

    step_id: str
    executor_id: str
    executor_kind: ExecutorKind
    inputs: Mapping[str, object]
    output_ids: tuple[str, ...]
    dispatch: DispatchContext
    agent_config: CompiledAgentConfig | None = None
    terminal: bool = False


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
    terminal: bool = False


type Completion = Callable[
    [str, CompletionContext],
    Awaitable[object],
]
type HumanInstructionPreparer = Callable[
    [str, CompletionContext],
    Awaitable[str],
]
type HumanRequester = Callable[
    [str, CompletionContext],
    Awaitable[object],
]
type ProgramRunner = Callable[[ProgramInvocation], Awaitable[object]]


_CONCEPT_NAMES = (
    "Agent",
    "ApiBase",
    "Artifact",
    "Bool",
    "BoolArtifact",
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
    "TerminalStep",
    "Tool",
    "Workflow",
)
_CONCEPT_SUPERTYPES: Mapping[str, tuple[str, ...]] = {
    "BoolArtifact": ("Artifact",),
    "TerminalStep": ("Step",),
}
# This is an explicit catalog, not a source-code name discovery mechanism.
# ``step_executor`` deliberately has no output concept because the minimal
# parser does not model Agent/Human/Program as sub-concepts of Executor.
_OPERATOR_SIGNATURES: Mapping[
    str,
    tuple[tuple[str, ...], str | None],
] = {
    "agent_config": (("Agent", "Model", "Engine", "ApiBase"), "Bool"),
    "agent_system_prompt": (("Agent",), "Instruction"),
    "allowed_tool": (("Agent", "Tool"), "Bool"),
    "comparison_gt_op": ((), None),
    "comparison_gte_op": ((), None),
    "comparison_lt_op": ((), None),
    "comparison_lte_op": ((), None),
    "consumes": (("Step",), "List"),
    "depends_on": (("Step", "Step"), "Bool"),
    "foreach_item": (("Step", "Artifact"), "Artifact"),
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

_AGENT_OPERATOR_NAMES = frozenset(
    {
        "agent_config",
        "agent_system_prompt",
        "allowed_tool",
        "max_output_tokens",
        "max_turns",
        "reasoning_effort",
        "temperature",
    }
)


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
    return ParseContext(
        concepts=concepts,
        operators=operators,
        concept_supertypes=_CONCEPT_SUPERTYPES,
    )


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


@dataclass(slots=True)
class _AgentConfigDraft:
    """Mutable accumulator used while consuming order-independent assertions."""

    system_prompt: str | None = None
    model: str | None = None
    engine: str | None = None
    api_base: str | None = None
    max_tokens: int | None = None
    temperature: float | None = None
    reasoning_effort: str | None = None
    tools: list[str] = field(default_factory=list)
    max_turns: int | None = None
    declarations: set[str] = field(default_factory=set)


def _agent_owner(call: CompoundTerm, *, arity: int) -> Constant:
    operator_name = call.operator.name
    if len(call.arguments) != arity:
        raise ValueError(f"{operator_name} expects {arity} arguments, got {len(call.arguments)}")
    agent = _typed_constant(
        call.arguments[0],
        "Agent",
        f"{operator_name} owner",
    )
    executor_concepts = {
        concept.name for concept in agent.belong_concepts if concept.name in {"Agent", "Human", "Program"}
    }
    if executor_concepts != {"Agent"}:
        raise ValueError(f"{operator_name} owner must belong to Agent only")
    return agent


def _assert_true(value: object, operator_name: str) -> None:
    predicate = _typed_constant(value, "Bool", f"{operator_name} value")
    if predicate.symbol != "True":
        raise ValueError(f"{operator_name} must be asserted true")


def _positive_integer(value: object, operator_name: str) -> int:
    constant = value if isinstance(value, Constant) else None
    symbol = None if constant is None else constant.symbol
    if symbol is None or not symbol.isascii() or not symbol.isdecimal():
        raise ValueError(f"{operator_name} value must be a positive integer constant")
    try:
        parsed = int(symbol)
    except ValueError as error:
        raise ValueError(f"{operator_name} value must be a positive integer constant") from error
    if parsed < 1:
        raise ValueError(f"{operator_name} value must be a positive integer constant")
    return parsed


def _finite_temperature(value: object) -> float:
    constant = value if isinstance(value, Constant) else None
    if constant is None or "ComplexNumber" not in {concept.name for concept in constant.belong_concepts}:
        raise ValueError("temperature value must be a finite numeric constant")
    try:
        parsed = float(constant.symbol)
    except ValueError as error:
        raise ValueError("temperature value must be a finite numeric constant") from error
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("temperature value must be a finite non-negative numeric constant")
    return parsed


def _extract_agent_configs(
    assertions: tuple[Assertion, ...],
) -> tuple[dict[str, CompiledAgentConfig], tuple[Assertion, ...]]:
    """Consume every operator in the closed G4 Agent configuration vocabulary."""

    drafts: dict[str, _AgentConfigDraft] = {}
    residual: list[Assertion] = []
    for assertion in assertions:
        candidates = tuple(
            (term, value)
            for term, value in (
                (assertion.lhs, assertion.rhs),
                (assertion.rhs, assertion.lhs),
            )
            if isinstance(term, CompoundTerm) and term.operator.name in _AGENT_OPERATOR_NAMES
        )
        if not candidates:
            residual.append(assertion)
            continue
        if len(candidates) != 1:
            raise ValueError("one equality cannot configure multiple Agent settings")

        call, value = candidates[0]
        operator_name = call.operator.name
        expected_arity = 4 if operator_name == "agent_config" else 2 if operator_name == "allowed_tool" else 1
        agent = _agent_owner(call, arity=expected_arity)
        draft = drafts.setdefault(agent.symbol, _AgentConfigDraft())

        if operator_name == "allowed_tool":
            _assert_true(value, operator_name)
            tool = _typed_constant(
                call.arguments[1],
                "Tool",
                "allowed_tool tool",
            )
            if tool.symbol in draft.tools:
                raise ValueError(f"duplicate allowed_tool {tool.symbol!r} for {agent.symbol!r}")
            draft.tools.append(tool.symbol)
            continue

        if operator_name in draft.declarations:
            raise ValueError(f"duplicate {operator_name} for {agent.symbol!r}")
        draft.declarations.add(operator_name)

        if operator_name == "agent_config":
            _assert_true(value, operator_name)
            draft.model = _typed_constant(
                call.arguments[1],
                "Model",
                "agent_config model",
            ).symbol
            draft.engine = _typed_constant(
                call.arguments[2],
                "Engine",
                "agent_config engine",
            ).symbol
            draft.api_base = _typed_constant(
                call.arguments[3],
                "ApiBase",
                "agent_config API base",
            ).symbol
        elif operator_name == "agent_system_prompt":
            prompt = _typed_constant(
                value,
                "Instruction",
                "agent_system_prompt value",
            ).symbol
            if not prompt.strip():
                raise ValueError("agent_system_prompt value must not be blank")
            draft.system_prompt = prompt
        elif operator_name == "max_output_tokens":
            draft.max_tokens = _positive_integer(value, operator_name)
        elif operator_name == "temperature":
            draft.temperature = _finite_temperature(value)
        elif operator_name == "reasoning_effort":
            draft.reasoning_effort = _typed_constant(
                value,
                "ReasoningEffort",
                "reasoning_effort value",
            ).symbol
        elif operator_name == "max_turns":
            draft.max_turns = _positive_integer(value, operator_name)
        else:
            raise AssertionError(f"unhandled Agent operator {operator_name!r}")

    configs = {
        name: CompiledAgentConfig(
            name=name,
            system_prompt=draft.system_prompt,
            model=draft.model,
            engine=draft.engine,
            api_base=draft.api_base,
            max_tokens=draft.max_tokens,
            temperature=draft.temperature,
            reasoning_effort=draft.reasoning_effort,
            tools=tuple(draft.tools),
            max_turns=draft.max_turns,
        )
        for name, draft in drafts.items()
    }
    return configs, tuple(residual)


def compile_workflow(
    source: str,
    *,
    context: ParseContext | None = None,
    diagnostic_callback: Callable[[Diagnostic], None] | None = None,
) -> CompiledWorkflow:
    """Parse and compile one strictly typed workflow through a closed catalog.

    ``diagnostic_callback`` receives non-fatal warning diagnostics before this
    function returns or raises. Fatal diagnostics are reported through
    ``ValueError``. Successful results also retain their warnings.
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

    try:
        compiled = WorkflowGraphCompiler().compile(parsed.core_ir)
    except (TypeError, ValueError) as error:
        core_ir_diagnostics = collect_core_ir_diagnostics(parsed.core_ir)
        if diagnostic_callback is not None:
            for diagnostic in core_ir_diagnostics:
                if diagnostic.severity == "warning":
                    diagnostic_callback(diagnostic)
        error_messages = [diagnostic.message for diagnostic in core_ir_diagnostics if diagnostic.severity == "error"]
        error_messages.append(str(error))
        unique_error_messages = tuple(dict.fromkeys(error_messages))
        raise ValueError(f"workflow check failed: {'; '.join(unique_error_messages)}") from error
    if not isinstance(compiled, tuple):
        raise TypeError("workflow graph compiler returned an unexpected result")
    compilations = cast(tuple[WorkflowGraphCompilation, ...], compiled)

    checked = check_workflow(
        parsed.core_ir,
        graph_compilations=compilations,
        consumed_residual_operators=_AGENT_OPERATOR_NAMES,
    )
    check_errors = [diagnostic.message for diagnostic in checked.diagnostics if diagnostic.severity == "error"]
    check_warnings = tuple(diagnostic for diagnostic in checked.diagnostics if diagnostic.severity == "warning")
    # Warnings must escape before any fatal checker or strict-runner error:
    # a failed compilation has no CompiledWorkflow result to carry them.
    if diagnostic_callback is not None:
        for diagnostic in check_warnings:
            diagnostic_callback(diagnostic)
    if check_errors:
        raise ValueError(f"workflow check failed: {'; '.join(check_errors)}")

    if len(compilations) != 1:
        raise ValueError("workflow runner expects exactly one workflow")
    compilation = compilations[0]
    program_paths, residual_assertions = _extract_program_paths(compilation.residual_assertions)
    configured_agents, residual_assertions = _extract_agent_configs(residual_assertions)
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
        if len(matches) != 1:
            raise ValueError(
                f"executor {step.executor_id!r} for step {step.step_id!r} "
                "must be declared as exactly one of Agent, Human, or Program"
            )
        executor_kinds[step.executor_id] = cast(ExecutorKind, matches.pop())
        if executor_kinds[step.executor_id] == "Program" and step.executor_id not in program_paths:
            raise ValueError(f"Program executor {step.executor_id!r} has no program_path")

    used_agent_executors = {
        executor_id for executor_id, executor_kind in executor_kinds.items() if executor_kind == "Agent"
    }
    unused_configured_agents = sorted(configured_agents.keys() - used_agent_executors)
    if unused_configured_agents:
        raise ValueError(
            "every configured Agent must execute at least one Step; "
            f"unused configured Agents: {unused_configured_agents}"
        )

    agent_configs = dict(configured_agents)
    for executor_id, executor_kind in executor_kinds.items():
        if executor_kind == "Agent":
            agent_configs.setdefault(
                executor_id,
                CompiledAgentConfig(name=executor_id),
            )

    return CompiledWorkflow(
        graph=compilation.graph,
        executor_kinds=executor_kinds,
        program_paths=program_paths,
        agent_configs=agent_configs,
        diagnostics=check_warnings,
    )


def _normalize_outputs(
    step_id: str,
    output_ids: tuple[str, ...],
    result: object,
    *,
    named_mapping_required: bool,
) -> dict[str, object]:
    """Normalize scalar single outputs while keeping N-output calls explicit."""

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


def _normalize_terminal_output(
    step_id: str,
    output_ids: tuple[str, ...],
    result: object,
) -> dict[str, object]:
    """Normalize the closed TerminalStep result without leaking internal IDs."""

    if len(output_ids) != 1:
        raise ValueError(f"TerminalStep {step_id!r} must have exactly one output")
    if isinstance(result, Mapping):
        outputs = _normalize_outputs(
            step_id,
            output_ids,
            result,
            named_mapping_required=True,
        )
        value = outputs[output_ids[0]]
    else:
        value = result
    if type(value) is not bool:
        raise ValueError(
            f"TerminalStep {step_id!r} must return strict Boolean true or false, got {type(value).__name__}"
        )
    return {output_ids[0]: value}


def _output_contract(output_ids: tuple[str, ...], *, terminal: bool = False) -> str:
    if terminal:
        return "Return exactly one Boolean value: true or false."
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
    *,
    terminal: bool = False,
) -> dict[str, object]:
    """Map scalar Program stdout to one output and require mappings for many."""

    if terminal:
        try:
            result = json.loads(stdout)
        except json.JSONDecodeError as error:
            raise ValueError(f"TerminalStep {step_id!r} must write JSON true or false") from error
        return _normalize_terminal_output(step_id, output_ids, result)

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
    run_program: ProgramRunner | None,
    prepare_human_instruction: HumanInstructionPreparer | None,
    request_human: HumanRequester | None,
) -> StepDispatcher:
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
        terminal = step.step_type == "TerminalStep"
        output_contract = _output_contract(output_ids, terminal=terminal)
        instruction = instructions[step.step_id]
        executor_kind = compiled.executor_kinds[step.executor_id]
        completion_context = CompletionContext(
            step_id=step.step_id,
            executor_id=step.executor_id,
            executor_kind=executor_kind,
            inputs=dict(inputs),
            output_ids=output_ids,
            dispatch=dispatch_context,
            agent_config=(compiled.agent_configs[step.executor_id] if executor_kind == "Agent" else None),
            terminal=terminal,
        )
        if executor_kind == "Human":
            if prepare_human_instruction is None or request_human is None:
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
            prepared_instruction = await prepare_human_instruction(
                preparation_prompt,
                completion_context,
            )
            if not prepared_instruction.strip():
                raise ValueError(f"step {step.step_id!r} human instruction preparation returned no text")
            human_result = await request_human(
                prepared_instruction,
                completion_context,
            )
            if terminal:
                return _normalize_terminal_output(step.step_id, output_ids, human_result)
            return _normalize_outputs(step.step_id, output_ids, human_result, named_mapping_required=False)

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
                    terminal=terminal,
                )
            )
            if isinstance(program_result, str):
                return _normalize_program_stdout(
                    step.step_id,
                    output_ids,
                    program_result,
                    terminal=terminal,
                )
            if terminal:
                return _normalize_terminal_output(step.step_id, output_ids, program_result)
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
        if complete is None:
            raise AssertionError("completion preflight did not select a completion")
        result = await complete(
            prompt,
            completion_context,
        )
        if terminal:
            return _normalize_terminal_output(step.step_id, output_ids, result)
        return _normalize_outputs(
            step.step_id,
            output_ids,
            result,
            named_mapping_required=True,
        )

    return dispatch


async def execute_workflow(
    source: str,
    *,
    inputs: Mapping[str, object],
    complete: Completion | None = None,
    resource_capacities: Mapping[str, ResourceCapacity] | None = None,
    allocator: ResourceAllocator | None = None,
    parse_context: ParseContext | None = None,
    supported_executor_kinds: Collection[ExecutorKind] | None = None,
    resolve_path: PathResolver | None = None,
    resolve_instruction: InstructionResolver | None = None,
    work_dir: str | PathLike[str] | None = None,
    run_program: ProgramRunner | None = None,
    prepare_human_instruction: HumanInstructionPreparer | None = None,
    request_human: HumanRequester | None = None,
    checkpoint: ExecutionCheckpoint | None = None,
    checkpoint_observer: CheckpointObserver | None = None,
    timing_recorder: Callable[[StepTiming], None] | None = None,
    max_loop_epochs: int | None = None,
) -> dict[str, object]:
    """Execute one checked workflow with explicit dispatcher/runtime injection."""

    if (prepare_human_instruction is None) != (request_human is None):
        raise ValueError("provide prepare_human_instruction and request_human together")
    workflow_inputs: Mapping[str, object] = dict(inputs)

    compiled = compile_workflow(
        source,
        context=parse_context,
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
    foreach_step_ids = {edge.step_id for edge in graph.edges if isinstance(edge, ForeachEdge)}
    human_foreach_steps = sorted(
        step.step_id
        for step in graph.steps
        if (step.step_id in foreach_step_ids and compiled.executor_kinds[step.executor_id] == "Human")
    )
    if human_foreach_steps:
        raise ValueError(
            "Human executors are not supported for foreach steps because "
            "resumable requests have no iteration identity: "
            f"{human_foreach_steps}"
        )
    if any(compiled.executor_kinds[step.executor_id] == "Agent" for step in graph.steps) and complete is None:
        raise ValueError("Agent workflow requires a complete callback")
    instructions = await _materialize_instructions(compiled, resolve_instruction)
    program_paths = await _build_program_paths(compiled, resolve_path)
    if work_dir is None and any(not isabs(path) for path in program_paths.values()):
        raise ValueError("relative program_path requires an explicit work_dir")
    if program_paths and run_program is None:
        raise ValueError("Program workflow requires an injected run_program callback")
    plan = generate_plan(graph)
    loop_step_ids = {step_id for loop in plan.loops for step_id in loop.step_ids}
    human_loop_steps = sorted(
        step.step_id
        for step in graph.steps
        if step.step_id in loop_step_ids and compiled.executor_kinds[step.executor_id] == "Human"
    )
    if human_loop_steps:
        raise ValueError(
            "Human executors are not supported in feedback loops because "
            f"resumable requests have no epoch identity: {human_loop_steps}"
        )
    dispatch = _build_dispatch(
        compiled,
        instructions=instructions,
        program_paths=program_paths,
        work_dir=work_dir,
        complete=complete,
        run_program=run_program,
        prepare_human_instruction=prepare_human_instruction,
        request_human=request_human,
    )
    timing_metadata = (
        None
        if timing_recorder is None
        else {
            step.step_id: StepTimingMetadata(
                step_name=step.name_id,
                executor_id=step.executor_id,
                executor_kind=cast(Literal["Agent", "Program"], compiled.executor_kinds[step.executor_id]),
            )
            for step in graph.steps
            if compiled.executor_kinds[step.executor_id] != "Human"
        }
    )

    return await execute_plan(
        plan,
        graph,
        inputs=workflow_inputs,
        dispatch=dispatch,
        resource_capacities=resource_capacities,
        allocator=allocator,
        checkpoint=checkpoint,
        checkpoint_observer=checkpoint_observer,
        timing_recorder=timing_recorder,
        timing_metadata=timing_metadata,
        max_loop_epochs=max_loop_epochs,
    )
