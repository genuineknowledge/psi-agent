from __future__ import annotations

import pytest
from fusion_flow_next.core_ir import (
    Assertion,
    CompoundTerm,
    Concept,
    ConnectiveFormula,
    Constant,
    IfTerm,
    ListTerm,
    Operator,
    WorkflowFile,
)
from fusion_flow_next.parser import ParseContext, parse_workflow


def _context() -> ParseContext:
    concepts = {
        name: Concept(name=name)
        for name in (
            "Agent",
            "Artifact",
            "Bool",
            "Comparable",
            "ComplexNumber",
            "Count",
            "First",
            "Name",
            "Second",
            "Step",
        )
    }
    operators = {
        name: Operator(name=name)
        for name in (
            "+",
            "-",
            "*",
            "^",
            "compare",
            "comparison_gt_op",
            "comparison_gte_op",
            "comparison_lt_op",
            "comparison_lte_op",
            "custom",
            "max_attempts",
            "max_turns",
            "step_executor",
        )
    }
    operators["typed"] = Operator(
        name="typed",
        input_concepts=(concepts["Artifact"], concepts["Artifact"]),
    )
    operators["typed_mixed"] = Operator(
        name="typed_mixed",
        input_concepts=(concepts["Artifact"], concepts["Agent"]),
    )
    return ParseContext(concepts=concepts, operators=operators)


def test_parse_workflow_lowers_complete_surface_to_core_ir() -> None:
    result = parse_workflow(
        """
        const review: Step, Agent;
        const "draft": Artifact;
        const backup: Agent;
        const agent: Agent;
        const writer: Agent;
        const human: Agent;

        workflow first {
          custom(agent, review) == TRUE;
          custom(review) == False;
        }

        workflow second {
          step_executor(review) == if(!(max_attempts(review) != 2) AND max_turns(agent) = 8, writer, human);
          custom(1 + 2 * 3, [review, "draft"], -(4 ^ 2 ^ 3)) == true;
        }
        """,
        context=_context(),
    )

    assert result.diagnostics == ()
    assert isinstance(result.core_ir, WorkflowFile)
    workflow_file = result.core_ir
    assert [constant.symbol for constant in workflow_file.constants] == [
        "review",
        "draft",
        "backup",
        "agent",
        "writer",
        "human",
    ]
    assert [workflow.name for workflow in workflow_file.workflows] == ["first", "second"]

    review, draft, backup, agent, writer, human = workflow_file.constants
    assert [concept.name for concept in review.belong_concepts] == ["Step", "Agent"]
    assert [concept.name for concept in draft.belong_concepts] == ["Artifact"]
    assert review.belong_concepts[1] is backup.belong_concepts[0]

    first_workflow, second_workflow = workflow_file.workflows
    first_custom_assertion, second_custom_assertion = first_workflow.assertions
    conditional_assertion, arithmetic_assertion = second_workflow.assertions

    assert isinstance(first_custom_assertion.lhs, CompoundTerm)
    assert isinstance(second_custom_assertion.lhs, CompoundTerm)
    assert isinstance(arithmetic_assertion.lhs, CompoundTerm)
    first_custom = first_custom_assertion.lhs
    second_custom = second_custom_assertion.lhs
    arithmetic_call = arithmetic_assertion.lhs
    assert first_custom.operator.name == "custom"
    assert first_custom.operator is second_custom.operator
    assert first_custom.operator is arithmetic_call.operator
    assert first_custom.arguments[0] is agent
    assert first_custom.arguments[1] is review
    assert second_custom.arguments[0] is review

    assert isinstance(first_custom_assertion.rhs, Constant)
    assert isinstance(second_custom_assertion.rhs, Constant)
    assert first_custom_assertion.rhs.symbol == "True"
    assert second_custom_assertion.rhs.symbol == "False"
    assert [concept.name for concept in first_custom_assertion.rhs.belong_concepts] == ["Bool"]
    assert [concept.name for concept in second_custom_assertion.rhs.belong_concepts] == ["Bool"]
    assert arithmetic_assertion.rhs is first_custom_assertion.rhs

    assert isinstance(conditional_assertion.lhs, CompoundTerm)
    assert conditional_assertion.lhs.operator.name == "step_executor"
    assert conditional_assertion.lhs.arguments[0] is review
    assert isinstance(conditional_assertion.rhs, IfTerm)
    conditional = conditional_assertion.rhs
    assert isinstance(conditional.condition, ConnectiveFormula)
    assert conditional.condition.connective == "AND"
    assert isinstance(conditional.condition.formula_left, ConnectiveFormula)
    negated = conditional.condition.formula_left
    assert negated.connective == "NOT"
    assert negated.formula_right is None
    assert isinstance(negated.formula_left, ConnectiveFormula)
    not_equals = negated.formula_left
    assert not_equals.connective == "NOT"
    assert not_equals.formula_right is None
    assert isinstance(not_equals.formula_left, Assertion)
    equality = not_equals.formula_left
    assert isinstance(equality.lhs, CompoundTerm)
    assert equality.lhs.operator.name == "max_attempts"
    assert equality.lhs.arguments[0] is review
    assert isinstance(equality.rhs, Constant)
    assert equality.rhs.symbol == "2"
    assert [concept.name for concept in equality.rhs.belong_concepts] == ["ComplexNumber"]

    assert isinstance(conditional.condition.formula_right, Assertion)
    numeric_equals = conditional.condition.formula_right
    assert isinstance(numeric_equals.lhs, CompoundTerm)
    assert numeric_equals.lhs.operator.name == "max_turns"
    assert numeric_equals.lhs.arguments[0] is first_custom.arguments[0]
    assert isinstance(numeric_equals.rhs, Constant)
    assert numeric_equals.rhs.symbol == "8"
    assert [concept.name for concept in numeric_equals.rhs.belong_concepts] == ["ComplexNumber"]
    assert isinstance(conditional.when_true, Constant)
    assert isinstance(conditional.when_false, Constant)
    assert conditional.when_true is writer
    assert conditional.when_false is human

    sum_term, list_term, negation = arithmetic_call.arguments
    assert isinstance(sum_term, CompoundTerm)
    assert sum_term.operator.name == "+"
    assert isinstance(sum_term.arguments[0], Constant)
    assert sum_term.arguments[0].symbol == "1"
    assert isinstance(sum_term.arguments[1], CompoundTerm)
    multiplication = sum_term.arguments[1]
    assert multiplication.operator.name == "*"
    assert [argument.symbol for argument in multiplication.arguments if isinstance(argument, Constant)] == ["2", "3"]

    assert isinstance(list_term, ListTerm)
    assert list_term.items == (review, draft)
    assert isinstance(negation, CompoundTerm)
    assert negation.operator.name == "-"
    assert len(negation.arguments) == 1
    assert isinstance(negation.arguments[0], CompoundTerm)
    power = negation.arguments[0]
    assert power.operator.name == "^"
    assert isinstance(power.arguments[0], Constant)
    assert power.arguments[0].symbol == "4"
    assert isinstance(power.arguments[1], CompoundTerm)
    nested_power = power.arguments[1]
    assert nested_power.operator.name == "^"
    assert [argument.symbol for argument in nested_power.arguments if isinstance(argument, Constant)] == ["2", "3"]


def test_comparisons_lower_to_builtin_operators_and_not_formula() -> None:
    result = parse_workflow(
        """
        const a: Comparable;
        const b: Comparable;
        workflow comparisons {
          compare(
            if(a < b, a, b),
            if(a <= b, a, b),
            if(a > b, a, b),
            if(a >= b, a, b),
            if(a != b, a, b)
          ) == true;
        }
        """,
        context=_context(),
    )

    assert result.diagnostics == ()
    assert isinstance(result.core_ir, WorkflowFile)
    assertion = result.core_ir.workflows[0].assertions[0]
    assert isinstance(assertion.lhs, CompoundTerm)
    conditions = tuple(argument.condition for argument in assertion.lhs.arguments if isinstance(argument, IfTerm))
    assert len(conditions) == 5

    for condition, operator_name in zip(
        conditions[:4],
        (
            "comparison_lt_op",
            "comparison_lte_op",
            "comparison_gt_op",
            "comparison_gte_op",
        ),
        strict=True,
    ):
        assert isinstance(condition, Assertion)
        assert isinstance(condition.lhs, CompoundTerm)
        assert condition.lhs.operator.name == operator_name
        assert [argument.symbol for argument in condition.lhs.arguments if isinstance(argument, Constant)] == [
            "a",
            "b",
        ]
        assert isinstance(condition.rhs, Constant)
        assert condition.rhs.symbol == "True"
        assert [concept.name for concept in condition.rhs.belong_concepts] == ["Bool"]

    not_equal = conditions[-1]
    assert isinstance(not_equal, ConnectiveFormula)
    assert not_equal.connective == "NOT"
    assert not_equal.formula_right is None
    assert isinstance(not_equal.formula_left, Assertion)
    assert isinstance(not_equal.formula_left.lhs, Constant)
    assert isinstance(not_equal.formula_left.rhs, Constant)
    assert (not_equal.formula_left.lhs.symbol, not_equal.formula_left.rhs.symbol) == ("a", "b")


def test_literals_use_gk_constant_symbols_and_concepts() -> None:
    result = parse_workflow(
        """
        const name: Name;
        const "quoted": Name;
        workflow literals {
          custom(002, 2.50, TRUE, false, name, "quoted") == true;
        }
        """,
        context=_context(),
    )

    assert result.diagnostics == ()
    assert isinstance(result.core_ir, WorkflowFile)
    declared_name, declared_quoted = result.core_ir.constants
    assertion = result.core_ir.workflows[0].assertions[0]
    assert isinstance(assertion.lhs, CompoundTerm)
    integer, decimal, true, false, name, quoted = assertion.lhs.arguments
    assert isinstance(integer, Constant)
    assert isinstance(decimal, Constant)
    assert isinstance(true, Constant)
    assert isinstance(false, Constant)
    assert isinstance(name, Constant)
    assert isinstance(quoted, Constant)
    assert (integer.symbol, decimal.symbol, true.symbol, false.symbol) == ("2", "2.5", "True", "False")
    assert [concept.name for concept in integer.belong_concepts] == ["ComplexNumber"]
    assert [concept.name for concept in decimal.belong_concepts] == ["ComplexNumber"]
    assert [concept.name for concept in true.belong_concepts] == ["Bool"]
    assert [concept.name for concept in false.belong_concepts] == ["Bool"]
    assert name is declared_name
    assert quoted is declared_quoted
    assert [concept.name for concept in name.belong_concepts] == ["Name"]
    assert [concept.name for concept in quoted.belong_concepts] == ["Name"]
    assert assertion.rhs is true


def test_duplicate_declarations_reuse_identity() -> None:
    result = parse_workflow(
        """
        const item: First;
        const item: First;
        workflow duplicate { custom(item) == true; }
        """,
        context=_context(),
    )

    assert result.diagnostics == ()
    assert isinstance(result.core_ir, WorkflowFile)
    (item,) = result.core_ir.constants
    call = result.core_ir.workflows[0].assertions[0].lhs
    assert isinstance(call, CompoundTerm)
    assert call.arguments[0] is item


def test_numeric_literal_does_not_alias_numeric_declaration() -> None:
    result = parse_workflow(
        """
        const 2: Count;
        workflow numeric { custom(2) == true; }
        """,
        context=_context(),
    )

    assert result.diagnostics == ()
    assert isinstance(result.core_ir, WorkflowFile)
    (number,) = result.core_ir.constants
    call = result.core_ir.workflows[0].assertions[0].lhs
    assert isinstance(call, CompoundTerm)
    literal = call.arguments[0]
    assert isinstance(literal, Constant)
    assert literal is not number
    assert [concept.name for concept in number.belong_concepts] == ["Count"]
    assert [concept.name for concept in literal.belong_concepts] == ["ComplexNumber"]


def test_quoted_constants_do_not_alias_boolean_or_numeric_literals() -> None:
    result = parse_workflow(
        """
        const "true": Artifact;
        const "1": Artifact;
        workflow lexical_types { custom("true", true, "1", 1) == true; }
        """,
        context=_context(),
    )

    assert result.diagnostics == ()
    assert isinstance(result.core_ir, WorkflowFile)
    quoted_true, quoted_one = result.core_ir.constants
    call = result.core_ir.workflows[0].assertions[0].lhs
    assert isinstance(call, CompoundTerm)
    parsed_quoted_true, boolean_true, parsed_quoted_one, numeric_one = call.arguments
    assert parsed_quoted_true is quoted_true
    assert parsed_quoted_one is quoted_one
    assert isinstance(boolean_true, Constant)
    assert isinstance(numeric_one, Constant)
    assert boolean_true is not quoted_true
    assert numeric_one is not quoted_one
    assert [concept.name for concept in quoted_true.belong_concepts] == ["Artifact"]
    assert [concept.name for concept in quoted_one.belong_concepts] == ["Artifact"]
    assert [concept.name for concept in boolean_true.belong_concepts] == ["Bool"]
    assert [concept.name for concept in numeric_one.belong_concepts] == ["ComplexNumber"]


def test_conflicting_constant_declarations_are_rejected() -> None:
    with pytest.raises(ValueError, match="Conflicting FusionFlow constant declaration for 'item'"):
        parse_workflow(
            """
            const item: First;
            const item: Second;
            workflow duplicate { custom(item) == true; }
            """,
            context=_context(),
        )


def test_declared_quoted_and_unquoted_names_reuse_one_constant() -> None:
    result = parse_workflow(
        """
        const foo: Artifact;
        workflow reuse { typed(foo, "foo") == true; }
        """,
        context=_context(),
    )

    assert result.diagnostics == ()
    assert isinstance(result.core_ir, WorkflowFile)
    (foo,) = result.core_ir.constants
    call = result.core_ir.workflows[0].assertions[0].lhs
    assert isinstance(call, CompoundTerm)
    assert call.arguments == (foo, foo)


def test_undeclared_names_are_inferred_and_listed_in_first_use_order() -> None:
    result = parse_workflow(
        'workflow inferred { typed(second, "first") == true; }',
        context=_context(),
    )

    assert result.diagnostics == ()
    assert isinstance(result.core_ir, WorkflowFile)
    second, first = result.core_ir.constants
    assert (second.symbol, first.symbol) == ("second", "first")
    assert [concept.name for concept in second.belong_concepts] == ["Artifact"]
    assert [concept.name for concept in first.belong_concepts] == ["Artifact"]
    call = result.core_ir.workflows[0].assertions[0].lhs
    assert isinstance(call, CompoundTerm)
    assert call.arguments == (second, first)


@pytest.mark.parametrize(
    "source",
    (
        "const foo: Agent; workflow conflict { typed(foo, foo) == true; }",
        'workflow conflict { typed_mixed(foo, "foo") == true; }',
    ),
)
def test_constant_concept_conflicts_are_rejected(source: str) -> None:
    with pytest.raises(ValueError, match=r"FusionFlow constant 'foo'.*concept"):
        parse_workflow(source, context=_context())


def test_undeclared_constant_without_operator_concept_is_rejected() -> None:
    with pytest.raises(ValueError, match="Cannot infer concept for FusionFlow constant 'unknown'"):
        parse_workflow(
            "workflow missing { custom(unknown) == true; }",
            context=_context(),
        )


def test_unknown_parse_context_entries_are_rejected() -> None:
    with pytest.raises(ValueError, match="Unknown FusionFlow concept 'Missing'"):
        parse_workflow(
            "const foo: Missing; workflow missing { custom(foo) == true; }",
            context=_context(),
        )
    with pytest.raises(ValueError, match="Unknown FusionFlow operator 'missing'"):
        parse_workflow(
            "workflow missing { missing(true) == true; }",
            context=_context(),
        )


def test_syntax_errors_return_diagnostics_without_core_ir() -> None:
    result = parse_workflow(
        "workflow broken { custom(value) = true; }",
        context=_context(),
    )

    assert result.core_ir is None
    assert result.diagnostics
    diagnostic = result.diagnostics[0]
    assert diagnostic.severity == "error"
    assert diagnostic.message
    assert diagnostic.span is not None
    assert (diagnostic.span.start.line, diagnostic.span.start.column) == (1, 33)
    assert (diagnostic.span.end.line, diagnostic.span.end.column) == (1, 34)


def test_eof_diagnostics_have_a_visible_half_open_span() -> None:
    result = parse_workflow(
        "workflow broken { custom(value) == true;",
        context=_context(),
    )

    assert result.core_ir is None
    assert result.diagnostics
    span = result.diagnostics[0].span
    assert span is not None
    assert span.end.column == span.start.column + 1
