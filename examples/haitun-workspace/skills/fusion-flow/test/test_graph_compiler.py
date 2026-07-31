from __future__ import annotations

from typing import cast

import fusion_flow
import pytest
from fusion_flow.compiler import CoreIRCompiler
from fusion_flow.core_ir import (
    Assertion,
    CompoundTerm,
    Concept,
    ConnectiveFormula,
    Constant,
    IfTerm,
    ListTerm,
    Operator,
    Term,
    Workflow,
    WorkflowFile,
)
from fusion_flow.graph_compiler import (
    WorkflowGraphCompilation,
    WorkflowGraphCompilationError,
    WorkflowGraphCompiler,
)
from fusion_flow.workflow_graph.model import (
    ArtifactOperand,
    ComparisonCondition,
    ComparisonOperator,
    LiteralOperand,
    LogicalCondition,
    ResourceRequirement,
    SelectNode,
    WorkflowGraphError,
)


def _assertion(
    operator_name: str,
    arguments: tuple[Term, ...],
    rhs: Term,
) -> Assertion:
    return Assertion(
        lhs=CompoundTerm(
            operator=Operator(operator_name),
            arguments=arguments,
        ),
        rhs=rhs,
    )


def _step_declarations(step_id: str = "step") -> tuple[Assertion, ...]:
    step = Constant(step_id)
    return (
        _assertion("step_name", (step,), Constant(f"{step_id}-name")),
        _assertion(
            "step_executor",
            (step,),
            Constant(f"{step_id}-executor", (Concept("Agent"),)),
        ),
    )


def _compile(
    assertions: tuple[Assertion, ...],
    *,
    workflow_id: str = "workflow",
) -> WorkflowGraphCompilation:
    compiled = WorkflowGraphCompiler().compile(
        WorkflowFile(
            constants=(),
            workflows=(Workflow(name=workflow_id, assertions=assertions),),
        )
    )
    assert isinstance(compiled, tuple)
    return cast(tuple[WorkflowGraphCompilation, ...], compiled)[0]


def test_supported_operators_include_depends_on() -> None:
    assert "depends_on" in WorkflowGraphCompiler.SUPPORTED_OPERATORS


def test_compiles_all_graph_operators() -> None:
    workflow = Constant("workflow")
    step = Constant("step")
    input_artifact = Constant("input")
    output_artifact = Constant("output")
    compilation = _compile(
        (
            _assertion("input_workflow", (workflow,), ListTerm((input_artifact,))),
            _assertion("output_workflow", (workflow,), ListTerm((output_artifact,))),
            _assertion("step_name", (step,), Constant("name")),
            _assertion("step_instruction", (step,), Constant("instruction")),
            _assertion(
                "step_executor",
                (step,),
                Constant("executor", (Concept("Agent"),)),
            ),
            _assertion("consumes", (step,), ListTerm((input_artifact,))),
            _assertion("produces", (step,), ListTerm((output_artifact,))),
            _assertion("foreach_item", (step, input_artifact), Constant("item")),
            _assertion("step_timeout", (step,), Constant("30")),
            _assertion("max_attempts", (step,), Constant("2")),
            _assertion(
                "resource_requirement",
                (step, Constant("gpu")),
                Constant("3"),
            ),
            _assertion("max_concurrency", (workflow,), Constant("4")),
            _assertion("workflow_timeout", (workflow,), Constant("120")),
        )
    )

    assert compilation.residual_assertions == ()
    assert compilation.graph.to_dict() == {
        "workflow_id": "workflow",
        "steps": [
            {
                "step_id": "step",
                "name_id": "name",
                "executor_id": "executor",
                "instruction_id": "instruction",
                "timeout_seconds": 30,
                "max_attempts": 2,
                "resources": [
                    {
                        "resource_id": "gpu",
                        "amount": 3,
                    }
                ],
            }
        ],
        "artifacts": [
            {
                "artifact_id": "input",
                "is_input": True,
                "is_output": False,
                "binding_step_id": None,
            },
            {
                "artifact_id": "item",
                "is_input": False,
                "is_output": False,
                "binding_step_id": "step",
            },
            {
                "artifact_id": "output",
                "is_input": False,
                "is_output": True,
                "binding_step_id": None,
            },
        ],
        "edges": [
            {"kind": "consumes", "artifact_id": "input", "step_id": "step"},
            {
                "kind": "foreach",
                "artifact_id": "input",
                "step_id": "step",
                "item_binding_id": "item",
            },
            {"kind": "produces", "step_id": "step", "artifact_id": "output"},
        ],
        "policy": {"max_concurrency": 4, "timeout_seconds": 120},
        "selectors": [],
    }


def test_compiles_scheduling_policies_order_independently() -> None:
    step = Constant("step", (Concept("Step"),))
    predecessor = Constant("predecessor", (Concept("Step"),))
    gpu = Constant("gpu", (Concept("Resource"),))
    true = Constant("True", (Concept("Bool"),))
    assertions = (
        *_step_declarations("step"),
        *_step_declarations("predecessor"),
        _assertion("independent", (step,), true),
        _assertion("depends_on", (step, predecessor), true),
        _assertion("resource_requirement", (step, gpu), Constant("1")),
    )

    left = _compile(assertions)
    right = _compile(tuple(reversed(assertions)))

    assert left.residual_assertions == right.residual_assertions == ()
    assert left.graph.to_dict() == right.graph.to_dict()
    compiled_step = next(item for item in left.graph.steps if item.step_id == "step")
    assert compiled_step.independent is True
    assert compiled_step.depends_on == ("predecessor",)
    assert compiled_step.resources == (ResourceRequirement("gpu", 1),)
    compiled_payload = next(item for item in left.graph.to_dict()["steps"] if item["step_id"] == "step")
    assert compiled_payload["depends_on"] == ["predecessor"]


@pytest.mark.parametrize(
    ("operator_name", "arguments"),
    [
        ("independent", (Constant("step", (Concept("Step"),)),)),
        (
            "depends_on",
            (
                Constant("step", (Concept("Step"),)),
                Constant("predecessor", (Concept("Step"),)),
            ),
        ),
    ],
)
def test_catalog_bool_relations_accept_only_true(
    operator_name: str,
    arguments: tuple[Term, ...],
) -> None:
    with pytest.raises(
        WorkflowGraphCompilationError,
        match="RHS must be the Boolean constant True",
    ):
        _compile(
            (
                *_step_declarations(),
                *_step_declarations("predecessor"),
                _assertion(
                    operator_name,
                    arguments,
                    Constant("False", (Concept("Bool"),)),
                ),
            )
        )


def test_depends_on_supports_multiple_predecessors() -> None:
    true = Constant("True", (Concept("Bool"),))
    step = Constant("step", (Concept("Step"),))
    first = Constant("first", (Concept("Step"),))
    second = Constant("second", (Concept("Step"),))

    compilation = _compile(
        (
            _assertion("depends_on", (step, second), true),
            *_step_declarations("first"),
            *_step_declarations("step"),
            _assertion("depends_on", (step, first), true),
            *_step_declarations("second"),
        )
    )

    compiled_step = next(item for item in compilation.graph.steps if item.step_id == "step")
    assert compiled_step.depends_on == ("first", "second")


def test_depends_on_rejects_duplicate_pair() -> None:
    true = Constant("True", (Concept("Bool"),))
    step = Constant("step", (Concept("Step"),))
    predecessor = Constant("predecessor", (Concept("Step"),))

    with pytest.raises(
        WorkflowGraphCompilationError,
        match=r"duplicate depends_on: \('step', 'predecessor'\)",
    ):
        _compile(
            (
                *_step_declarations(),
                *_step_declarations("predecessor"),
                _assertion(
                    "depends_on",
                    (step, predecessor),
                    true,
                ),
                _assertion("depends_on", (step, predecessor), true),
            )
        )


def test_depends_on_rejects_unknown_predecessor() -> None:
    with pytest.raises(
        WorkflowGraphCompilationError,
        match="depends_on predecessor 'missing' is not a fully declared step",
    ):
        _compile(
            (
                *_step_declarations(),
                _assertion(
                    "depends_on",
                    (
                        Constant("step", (Concept("Step"),)),
                        Constant("missing", (Concept("Step"),)),
                    ),
                    Constant("True", (Concept("Bool"),)),
                ),
            )
        )


def test_depends_on_rejects_unknown_target() -> None:
    with pytest.raises(
        WorkflowGraphCompilationError,
        match="depends_on target 'missing' is not a fully declared step",
    ):
        _compile(
            (
                *_step_declarations("predecessor"),
                _assertion(
                    "depends_on",
                    (
                        Constant("missing", (Concept("Step"),)),
                        Constant("predecessor", (Concept("Step"),)),
                    ),
                    Constant("True", (Concept("Bool"),)),
                ),
            )
        )


def test_depends_on_self_cycle_is_preserved_for_the_planner() -> None:
    step = Constant("step", (Concept("Step"),))
    compilation = _compile(
        (
            *_step_declarations(),
            _assertion(
                "depends_on",
                (step, step),
                Constant("True", (Concept("Bool"),)),
            ),
        )
    )

    assert compilation.graph.steps[0].depends_on == ("step",)


def test_graph_dataflow_operators_read_multiple_list_term_items() -> None:
    workflow = Constant("workflow")
    step = Constant("step")
    inputs = (Constant("in-b"), Constant("in-a"))
    outputs = (Constant("out-b"), Constant("out-a"))
    compilation = _compile(
        (
            *_step_declarations(),
            _assertion("input_workflow", (workflow,), ListTerm(inputs)),
            _assertion("output_workflow", (workflow,), ListTerm(outputs)),
            _assertion("consumes", (step,), ListTerm(inputs)),
            _assertion("produces", (step,), ListTerm(outputs)),
        )
    )

    assert {
        (artifact.artifact_id, artifact.is_input, artifact.is_output) for artifact in compilation.graph.artifacts
    } == {
        ("in-a", True, False),
        ("in-b", True, False),
        ("out-a", False, True),
        ("out-b", False, True),
    }
    assert {(edge.kind, edge.artifact_id, edge.step_id) for edge in compilation.graph.edges} == {
        ("consumes", "in-a", "step"),
        ("consumes", "in-b", "step"),
        ("produces", "out-a", "step"),
        ("produces", "out-b", "step"),
    }


def test_shared_compiler_contract_builds_every_workflow() -> None:
    compiler = WorkflowGraphCompiler()

    compiled = compiler.compile(
        WorkflowFile(
            constants=(),
            workflows=(
                Workflow(name="first", assertions=()),
                Workflow(name="second", assertions=()),
            ),
        )
    )
    assert isinstance(compiled, tuple)
    compilations = cast(tuple[WorkflowGraphCompilation, ...], compiled)

    assert isinstance(compiler, CoreIRCompiler)
    assert tuple(compilation.graph.workflow_id for compilation in compilations) == (
        "first",
        "second",
    )


def test_unknown_assertions_remain_residual() -> None:
    unknown = _assertion(
        "value_assignment",
        (Constant("files"),),
        ListTerm((Constant("a"), Constant("b"))),
    )
    reversed_unknown = Assertion(
        lhs=Constant("value"),
        rhs=CompoundTerm(
            operator=Operator("value_assignment"),
            arguments=(Constant("files"),),
        ),
    )
    ordinary_equality = Assertion(lhs=Constant("left"), rhs=Constant("right"))

    compilation = _compile((unknown, reversed_unknown, ordinary_equality))

    assert compilation.graph.steps == ()
    assert compilation.graph.artifacts == ()
    assert compilation.residual_assertions == (
        unknown,
        reversed_unknown,
        ordinary_equality,
    )


def test_unknown_operator_with_if_value_remains_residual() -> None:
    artifact = Concept("Artifact")
    assertion = Assertion(
        lhs=CompoundTerm(
            operator=Operator("unknown_operator"),
            arguments=(Constant("output", (artifact,)),),
        ),
        rhs=IfTerm(
            condition=Assertion(
                lhs=Constant("condition", (artifact,)),
                rhs=Constant("True", (Concept("Bool"),)),
            ),
            when_true=Constant("primary", (artifact,)),
            when_false=Constant("fallback", (artifact,)),
        ),
    )

    compilation = _compile((assertion,))

    assert compilation.graph.selectors == ()
    assert compilation.residual_assertions == (assertion,)


def test_executor_configuration_remains_residual() -> None:
    program_path = _assertion(
        "program_path",
        (Constant("program"),),
        Constant("program-source"),
    )
    agent_system_prompt = _assertion(
        "agent_system_prompt",
        (Constant("agent"),),
        Constant("system-prompt"),
    )

    compilation = _compile((program_path, agent_system_prompt))

    assert compilation.residual_assertions == (
        program_path,
        agent_system_prompt,
    )


def test_supported_graph_operator_on_rhs_is_lowered_as_equality() -> None:
    reversed_input = Assertion(
        lhs=ListTerm((Constant("artifact2"),)),
        rhs=CompoundTerm(
            operator=Operator("input_workflow"),
            arguments=(Constant("workflow1"),),
        ),
    )

    compilation = _compile((reversed_input,), workflow_id="workflow1")

    assert compilation.residual_assertions == ()
    assert tuple(
        (artifact.artifact_id, artifact.is_input, artifact.is_output) for artifact in compilation.graph.artifacts
    ) == (("artifact2", True, False),)


def test_one_equality_cannot_declare_multiple_graph_facts() -> None:
    assertion = Assertion(
        lhs=CompoundTerm(
            operator=Operator("input_workflow"),
            arguments=(Constant("workflow"),),
        ),
        rhs=CompoundTerm(
            operator=Operator("output_workflow"),
            arguments=(Constant("workflow"),),
        ),
    )

    with pytest.raises(
        WorkflowGraphCompilationError,
        match="one equality cannot declare multiple graph facts",
    ):
        _compile((assertion,))


def test_supported_operator_with_unsupported_term_fails_closed() -> None:
    conditional = IfTerm(
        condition=Assertion(lhs=Constant("condition"), rhs=Constant("True")),
        when_true=Constant("yes"),
        when_false=Constant("no"),
    )

    with pytest.raises(ValueError, match="unsupported if term"):
        _compile((_assertion("step_name", (Constant("step"),), conditional),))


def test_named_artifact_if_lowers_to_select_without_residual() -> None:
    artifact = Concept("Artifact")
    boolean = Concept("Bool")
    workflow = Constant("workflow")
    flag = Constant("flag", (artifact,))
    status = Constant("status", (artifact,))
    primary = Constant("primary", (artifact,))
    fallback = Constant("fallback", (artifact,))
    selected = Constant("selected", (artifact,))
    condition = ConnectiveFormula(
        formula_left=Assertion(
            lhs=flag,
            rhs=Constant("True", (boolean,)),
        ),
        connective="AND",
        formula_right=ConnectiveFormula(
            formula_left=Assertion(lhs=status, rhs=Constant("ready")),
            connective="NOT",
        ),
    )

    compilation = _compile(
        (
            _assertion(
                "input_workflow",
                (workflow,),
                ListTerm((flag, status, primary, fallback)),
            ),
            _assertion("output_workflow", (workflow,), ListTerm((selected,))),
            Assertion(
                lhs=selected,
                rhs=IfTerm(
                    condition=condition,
                    when_true=primary,
                    when_false=fallback,
                ),
            ),
        )
    )

    assert compilation.residual_assertions == ()
    assert compilation.graph.selectors == (
        SelectNode(
            output_artifact_id="selected",
            when_true_artifact_id="primary",
            when_false_artifact_id="fallback",
            condition=LogicalCondition(
                operator="and",
                conditions=(
                    ComparisonCondition(
                        operator="eq",
                        left=ArtifactOperand("flag"),
                        right=LiteralOperand(True),
                    ),
                    LogicalCondition(
                        operator="not",
                        conditions=(
                            ComparisonCondition(
                                operator="eq",
                                left=ArtifactOperand("status"),
                                right=LiteralOperand("ready"),
                            ),
                        ),
                    ),
                ),
            ),
        ),
    )
    assert tuple(artifact.artifact_id for artifact in compilation.graph.artifacts) == (
        "fallback",
        "flag",
        "primary",
        "selected",
        "status",
    )


@pytest.mark.parametrize(
    ("operator_name", "graph_operator"),
    [
        ("comparison_lt_op", "lt"),
        ("comparison_lte_op", "lte"),
        ("comparison_gt_op", "gt"),
        ("comparison_gte_op", "gte"),
    ],
)
def test_named_artifact_if_lowers_ordered_comparison(
    operator_name: str,
    graph_operator: ComparisonOperator,
) -> None:
    artifact = Concept("Artifact")
    boolean = Concept("Bool")
    number = Concept("ComplexNumber")
    workflow = Constant("workflow")
    score = Constant("score", (artifact,))
    primary = Constant("primary", (artifact,))
    fallback = Constant("fallback", (artifact,))
    selected = Constant("selected", (artifact,))

    compilation = _compile(
        (
            _assertion(
                "input_workflow",
                (workflow,),
                ListTerm((score, primary, fallback)),
            ),
            _assertion("output_workflow", (workflow,), ListTerm((selected,))),
            Assertion(
                lhs=selected,
                rhs=IfTerm(
                    condition=Assertion(
                        lhs=CompoundTerm(
                            operator=Operator(operator_name),
                            arguments=(score, Constant("10.5", (number,))),
                        ),
                        rhs=Constant("True", (boolean,)),
                    ),
                    when_true=primary,
                    when_false=fallback,
                ),
            ),
        )
    )

    assert compilation.graph.selectors[0].condition == ComparisonCondition(
        operator=graph_operator,
        left=ArtifactOperand("score"),
        right=LiteralOperand(10.5),
    )


def test_named_select_chains_compile_deterministically() -> None:
    artifact = Concept("Artifact")
    boolean = Concept("Bool")
    workflow = Constant("workflow")
    condition_one = Constant("condition_one", (artifact,))
    condition_two = Constant("condition_two", (artifact,))
    primary = Constant("primary", (artifact,))
    review = Constant("review", (artifact,))
    fallback = Constant("fallback", (artifact,))
    review_or_fallback = Constant("review_or_fallback", (artifact,))
    selected = Constant("selected", (artifact,))
    true = Constant("True", (boolean,))
    assertions = (
        _assertion(
            "input_workflow",
            (workflow,),
            ListTerm((condition_one, condition_two, primary, review, fallback)),
        ),
        _assertion("output_workflow", (workflow,), ListTerm((selected,))),
        Assertion(
            lhs=review_or_fallback,
            rhs=IfTerm(
                condition=Assertion(condition_two, true),
                when_true=review,
                when_false=fallback,
            ),
        ),
        Assertion(
            lhs=selected,
            rhs=IfTerm(
                condition=Assertion(condition_one, true),
                when_true=primary,
                when_false=review_or_fallback,
            ),
        ),
    )

    left = _compile(assertions)
    right = _compile(tuple(reversed(assertions)))

    assert left.graph.to_dict() == right.graph.to_dict()
    assert tuple(selector.output_artifact_id for selector in left.graph.selectors) == (
        "review_or_fallback",
        "selected",
    )
    assert left.residual_assertions == ()


def test_inline_if_in_consumes_fails_closed() -> None:
    conditional = IfTerm(
        condition=Assertion(lhs=Constant("condition"), rhs=Constant("True")),
        when_true=Constant("yes"),
        when_false=Constant("no"),
    )

    with pytest.raises(WorkflowGraphCompilationError, match="unsupported if term"):
        _compile(
            (
                _assertion(
                    "consumes",
                    (Constant("step"),),
                    ListTerm((conditional,)),
                ),
            )
        )


@pytest.mark.parametrize(
    ("selected", "when_true", "message"),
    [
        (
            Constant("selected"),
            Constant("primary", (Concept("Artifact"),)),
            "selected if output must be an Artifact constant",
        ),
        (
            Constant("selected", (Concept("Artifact"),)),
            Constant("primary"),
            "if branches must be Artifact constants",
        ),
        (
            Constant("selected", (Concept("Artifact"),)),
            ListTerm((Constant("primary", (Concept("Artifact"),)),)),
            "if branches must be Artifact constants",
        ),
        (
            Constant("selected", (Concept("Artifact"),)),
            CompoundTerm(Operator("identity"), (Constant("primary"),)),
            "if branches must be Artifact constants",
        ),
        (
            Constant("selected", (Concept("Artifact"),)),
            IfTerm(
                condition=Assertion(Constant("nested"), Constant("True")),
                when_true=Constant("a"),
                when_false=Constant("b"),
            ),
            "nested if branches are unsupported",
        ),
    ],
)
def test_named_if_rejects_non_artifact_and_compound_branches(
    selected: Constant,
    when_true: Term,
    message: str,
) -> None:
    artifact = Concept("Artifact")

    with pytest.raises(WorkflowGraphCompilationError, match=message):
        _compile(
            (
                Assertion(
                    lhs=selected,
                    rhs=IfTerm(
                        condition=Assertion(
                            Constant("condition", (artifact,)),
                            Constant("True", (Concept("Bool"),)),
                        ),
                        when_true=when_true,
                        when_false=Constant("fallback", (artifact,)),
                    ),
                ),
            )
        )


@pytest.mark.parametrize(
    "operand",
    [
        ListTerm((Constant("condition"),)),
        CompoundTerm(Operator("+"), (Constant("condition"), Constant("1"))),
        IfTerm(
            condition=Assertion(Constant("inner"), Constant("True")),
            when_true=Constant("yes"),
            when_false=Constant("no"),
        ),
    ],
)
def test_named_if_rejects_unsupported_condition_operands(operand: Term) -> None:
    artifact = Concept("Artifact")

    with pytest.raises(
        WorkflowGraphCompilationError,
        match="condition operands must be constants",
    ):
        _compile(
            (
                Assertion(
                    lhs=Constant("selected", (artifact,)),
                    rhs=IfTerm(
                        condition=Assertion(
                            lhs=operand,
                            rhs=Constant("True", (Concept("Bool"),)),
                        ),
                        when_true=Constant("primary", (artifact,)),
                        when_false=Constant("fallback", (artifact,)),
                    ),
                ),
            )
        )


def test_step_and_select_duplicate_producer_is_public_and_chained() -> None:
    artifact = Concept("Artifact")
    workflow = Constant("workflow")
    condition = Constant("condition", (artifact,))
    primary = Constant("primary", (artifact,))
    fallback = Constant("fallback", (artifact,))
    selected = Constant("selected", (artifact,))

    with pytest.raises(
        WorkflowGraphCompilationError,
        match="artifact has multiple producers",
    ) as caught:
        _compile(
            (
                *_step_declarations(),
                _assertion(
                    "input_workflow",
                    (workflow,),
                    ListTerm((condition, primary, fallback)),
                ),
                _assertion("output_workflow", (workflow,), ListTerm((selected,))),
                _assertion(
                    "produces",
                    (Constant("step"),),
                    ListTerm((selected,)),
                ),
                Assertion(
                    lhs=selected,
                    rhs=IfTerm(
                        condition=Assertion(condition, Constant("True", (Concept("Bool"),))),
                        when_true=primary,
                        when_false=fallback,
                    ),
                ),
            )
        )

    assert isinstance(caught.value.__cause__, WorkflowGraphError)


def test_duplicate_select_output_is_public_and_chained() -> None:
    artifact = Concept("Artifact")
    workflow = Constant("workflow")
    condition = Constant("condition", (artifact,))
    primary = Constant("primary", (artifact,))
    fallback = Constant("fallback", (artifact,))
    selected = Constant("selected", (artifact,))
    select = IfTerm(
        condition=Assertion(condition, Constant("True", (Concept("Bool"),))),
        when_true=primary,
        when_false=fallback,
    )

    with pytest.raises(
        WorkflowGraphCompilationError,
        match="artifact has multiple producers",
    ) as caught:
        _compile(
            (
                _assertion(
                    "input_workflow",
                    (workflow,),
                    ListTerm((condition, primary, fallback)),
                ),
                _assertion("output_workflow", (workflow,), ListTerm((selected,))),
                Assertion(lhs=selected, rhs=select),
                Assertion(lhs=selected, rhs=select),
            )
        )

    assert isinstance(caught.value.__cause__, WorkflowGraphError)


def test_oversized_integer_is_reported_as_a_graph_compilation_error() -> None:
    with pytest.raises(
        WorkflowGraphCompilationError,
        match="step_timeout RHS must be a positive integer constant",
    ) as caught:
        _compile(
            (
                *_step_declarations(),
                _assertion(
                    "step_timeout",
                    (Constant("step"),),
                    Constant("9" * 5000),
                ),
            )
        )

    assert isinstance(caught.value.__cause__, ValueError)


def test_explicit_one_does_not_hide_duplicate_max_attempts() -> None:
    with pytest.raises(WorkflowGraphCompilationError, match="duplicate max_attempts"):
        _compile(
            (
                *_step_declarations(),
                _assertion("max_attempts", (Constant("step"),), Constant("1")),
                _assertion("max_attempts", (Constant("step"),), Constant("2")),
            )
        )


def test_cycle_compilation_is_order_independent() -> None:
    assertions = (
        *_step_declarations("a"),
        *_step_declarations("b"),
        _assertion(
            "consumes",
            (Constant("a"),),
            ListTerm((Constant("from-b"),)),
        ),
        _assertion(
            "produces",
            (Constant("a"),),
            ListTerm((Constant("from-a"),)),
        ),
        _assertion(
            "consumes",
            (Constant("b"),),
            ListTerm((Constant("from-a"),)),
        ),
        _assertion(
            "produces",
            (Constant("b"),),
            ListTerm((Constant("from-b"),)),
        ),
    )

    left = _compile(assertions)
    right = _compile(tuple(reversed(assertions)))

    assert left.graph.to_dict() == right.graph.to_dict()
    assert len(left.graph.edges) == 4


def test_graph_validation_errors_are_public_and_chained() -> None:
    with pytest.raises(
        WorkflowGraphCompilationError,
        match="input or producer-backed",
    ) as caught:
        _compile(
            (
                _assertion(
                    "output_workflow",
                    (Constant("workflow"),),
                    ListTerm((Constant("missing"),)),
                ),
            )
        )

    assert isinstance(caught.value.__cause__, WorkflowGraphError)


def test_graph_backend_is_exported_from_package() -> None:
    assert fusion_flow.WorkflowGraphCompiler is WorkflowGraphCompiler
    assert fusion_flow.WorkflowGraphCompilation is WorkflowGraphCompilation
    assert fusion_flow.WorkflowGraphCompilationError is WorkflowGraphCompilationError
