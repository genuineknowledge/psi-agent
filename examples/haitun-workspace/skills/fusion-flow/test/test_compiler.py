from __future__ import annotations

import pytest
from fusion_flow.compiler import CoreIRCompiler, _CompiledDeclarations
from fusion_flow.core_ir import (
    Assertion,
    CompoundTerm,
    ConnectiveFormula,
    Constant,
    IfTerm,
    ListTerm,
    Operator,
    Workflow,
    WorkflowFile,
)


class _RecordingCompiler(CoreIRCompiler):
    def _compile_constant(self, constant: Constant) -> object:
        return ("constant", constant.symbol)

    def _compile_compound_term(self, term: CompoundTerm) -> object:
        return (
            "call",
            term.operator.name,
            tuple(self._compile_term(argument) for argument in term.arguments),
        )

    def _compile_list_term(self, term: ListTerm) -> object:
        return ("list", tuple(self._compile_term(item) for item in term.items))

    def _compile_if_term(self, term: IfTerm) -> object:
        return (
            "if",
            self._compile_formula(term.condition),
            self._compile_term(term.when_true),
            self._compile_term(term.when_false),
        )

    def _compile_assertion(self, assertion: Assertion) -> object:
        return (
            "assertion",
            self._compile_term(assertion.lhs),
            self._compile_term(assertion.rhs),
        )

    def _compile_connective_formula(self, formula: ConnectiveFormula) -> object:
        return (
            formula.connective,
            self._compile_formula(formula.formula_left),
            None if formula.formula_right is None else self._compile_formula(formula.formula_right),
        )

    def _build_workflow(
        self,
        workflow: Workflow,
        *,
        assertions: tuple[object, ...],
    ) -> object:
        return ("workflow", workflow.name, assertions)

    def _build_program(
        self,
        declarations: _CompiledDeclarations,
        *,
        workflows: tuple[object, ...],
    ) -> object:
        return {
            "constants": tuple((constant.symbol, compiled) for constant, compiled in declarations.constants.items()),
            "workflows": workflows,
        }


def test_core_ir_compiler_traverses_workflow_file_through_backend_hooks() -> None:
    first = Constant("first")
    second = Constant("second")
    condition = ConnectiveFormula(Assertion(first, second), "NOT")
    result = IfTerm(
        condition=condition,
        when_true=CompoundTerm(operator=Operator("+"), arguments=(first, second)),
        when_false=ListTerm((second, first)),
    )
    workflow_file = WorkflowFile(
        constants=(first, second),
        workflows=(Workflow("main", (Assertion(result, first),)),),
    )
    compiler = _RecordingCompiler()

    compiled = compiler.compile(workflow_file)

    assert compiled == {
        "constants": (
            ("first", ("constant", "first")),
            ("second", ("constant", "second")),
        ),
        "workflows": (
            (
                "workflow",
                "main",
                (
                    (
                        "assertion",
                        (
                            "if",
                            (
                                "NOT",
                                (
                                    "assertion",
                                    ("constant", "first"),
                                    ("constant", "second"),
                                ),
                                None,
                            ),
                            (
                                "call",
                                "+",
                                (("constant", "first"), ("constant", "second")),
                            ),
                            (
                                "list",
                                (("constant", "second"), ("constant", "first")),
                            ),
                        ),
                        ("constant", "first"),
                    ),
                ),
            ),
        ),
    }
    assert not hasattr(compiler, "core_ir")


def test_core_ir_compiler_rejects_unimplemented_node_hooks() -> None:
    workflow_file = WorkflowFile(constants=(Constant("value"),), workflows=())

    with pytest.raises(
        ValueError,
        match="CoreIRCompiler cannot compile unsupported constant declaration node of type Constant",
    ):
        CoreIRCompiler().compile(workflow_file)


def test_core_ir_compiler_requires_program_builder() -> None:
    with pytest.raises(NotImplementedError, match="CoreIRCompiler must implement _build_program"):
        CoreIRCompiler().compile(WorkflowFile(constants=(), workflows=()))
