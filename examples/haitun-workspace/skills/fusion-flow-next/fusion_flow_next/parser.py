"""Parse FusionFlow source into target-neutral Workflow Core IR."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from antlr4 import CommonTokenStream, InputStream, Token

from .contracts import Diagnostic, ParseResult, SourcePosition, SourceSpan
from .core_ir import (
    Assertion,
    CompoundTerm,
    Concept,
    ConnectiveFormula,
    Constant,
    Formula,
    IfTerm,
    ListTerm,
    Operator,
    Term,
    Workflow,
    WorkflowFile,
)
from .generated.FusionFlowLexer import FusionFlowLexer
from .generated.FusionFlowParser import FusionFlowParser


@dataclass(slots=True)
class ParseContext:
    """Concept and operator symbols shared by related FusionFlow parses."""

    concepts: dict[str, Concept]
    operators: dict[str, Operator]


class _DiagnosticListener:
    """Collect ANTLR errors as one-based, half-open public source spans."""

    def __init__(self) -> None:
        self.diagnostics: list[Diagnostic] = []

    def __getattr__(self, name: str) -> Any:
        if name == "syntaxError":
            return self._syntax_error
        raise AttributeError(name)

    def _syntax_error(
        self,
        recognizer: object,
        offending_symbol: object,
        line: int,
        column: int,
        message: str,
        error: object,
    ) -> None:
        del recognizer, error
        token = offending_symbol if isinstance(offending_symbol, Token) else None
        width = 1 if token is None or token.type == Token.EOF else max(len(token.text or ""), 1)
        start_column = column + 1
        self.diagnostics.append(
            Diagnostic(
                severity="error",
                message=message,
                span=SourceSpan(
                    start=SourcePosition(line=line, column=start_column),
                    end=SourcePosition(line=line, column=start_column + width),
                ),
            )
        )


class _CoreIRVisitor:
    """Lower a parse tree while reusing declarations by their source symbol.

    The traversal follows KEDispatcher's handwritten visitor pattern. Reusing
    concepts, constants, and operators preserves shared Core IR references.
    """

    _COMPARISON_OPERATORS: ClassVar[dict[str, str]] = {
        "<": "comparison_lt_op",
        "<=": "comparison_lte_op",
        ">": "comparison_gt_op",
        ">=": "comparison_gte_op",
    }

    def __init__(self, context: ParseContext) -> None:
        self._context = context
        self._constants: dict[str, Constant] = {}
        self._boolean_constants: dict[bool, Constant] = {}

    def visit_workflow_file(self, context: Any) -> WorkflowFile:
        for declaration in context.constDecl():
            self.visit_const_decl(declaration)
        workflows = tuple(self.visit_workflow_decl(workflow) for workflow in context.workflowDecl())
        return WorkflowFile(constants=tuple(self._constants.values()), workflows=workflows)

    def visit_const_decl(self, context: Any) -> Constant:
        symbol = self._strip_quotes(context.constantName().getText())
        concepts = tuple(
            dict.fromkeys(
                self._resolve_concept(concept.getText()) for concept in context.conceptNameList().conceptName()
            )
        )
        existing = self._constants.get(symbol)
        if existing is not None:
            if set(existing.belong_concepts) == set(concepts):
                return existing
            raise ValueError(
                f"Conflicting FusionFlow constant declaration for {symbol!r}: "
                f"{existing.belong_concepts!r} versus {concepts!r}."
            )
        constant = Constant(symbol=symbol, belong_concepts=concepts)
        self._constants[symbol] = constant
        return constant

    def visit_workflow_decl(self, context: Any) -> Workflow:
        return Workflow(
            name=str(context.workflowName().getText()),
            assertions=tuple(self.visit_assertion(item.assertion()) for item in context.workflowItem()),
        )

    def visit_assertion(self, context: Any) -> Assertion:
        terms = context.term()
        return Assertion(lhs=self.visit_term(terms[0]), rhs=self.visit_term(terms[1]))

    def visit_formula(self, context: Any) -> Formula:
        comparison = context.comparison()
        if comparison is not None:
            return self.visit_comparison(comparison)
        if context.NOT() is not None:
            return ConnectiveFormula(formula_left=self.visit_formula(context.formula(0)), connective="NOT")
        if context.left is not None and context.right is not None:
            connective = "AND" if context.AND() is not None else "OR"
            return ConnectiveFormula(
                formula_left=self.visit_formula(context.left),
                connective=connective,
                formula_right=self.visit_formula(context.right),
            )
        return self.visit_formula(context.formula(0))

    def visit_comparison(self, context: Any) -> Formula:
        terms = context.term()
        symbol = context.comparisonOp().getText()
        if symbol in {"=", "!="}:
            equality = Assertion(lhs=self.visit_term(terms[0]), rhs=self.visit_term(terms[1]))
            if symbol == "=":
                return equality
            # HACK: FusionFlow intentionally keeps != as NOT equality; gk uses comparison_ne_op.
            return ConnectiveFormula(formula_left=equality, connective="NOT")

        operator = self._resolve_operator(self._COMPARISON_OPERATORS[symbol])
        lhs = self.visit_term(
            terms[0],
            None if not operator.input_concepts else operator.input_concepts[0],
        )
        rhs = self.visit_term(
            terms[1],
            None if len(operator.input_concepts) < 2 else operator.input_concepts[1],
        )
        return Assertion(
            lhs=CompoundTerm(operator=operator, arguments=(lhs, rhs)),
            rhs=self._boolean_constant("true"),
        )

    def visit_term(self, context: Any, expected_concept: Concept | None = None) -> Term:
        if context.left is not None and context.right is not None:
            operator = self._resolve_operator(context.op.text)
            return CompoundTerm(
                operator=operator,
                arguments=(
                    self.visit_term(
                        context.left,
                        None if not operator.input_concepts else operator.input_concepts[0],
                    ),
                    self.visit_term(
                        context.right,
                        None if len(operator.input_concepts) < 2 else operator.input_concepts[1],
                    ),
                ),
            )

        if context.op is not None:
            if context.op.text == "+":
                return self.visit_term(context.term(0), expected_concept)
            operator = self._resolve_operator("-")
            operand = self.visit_term(
                context.term(0),
                None if not operator.input_concepts else operator.input_concepts[0],
            )
            return CompoundTerm(operator=operator, arguments=(operand,))

        conditional = context.ifExpression()
        if conditional is not None:
            return self.visit_if_expression(conditional)

        operator_name = context.operatorName()
        if operator_name is not None:
            operator = self._resolve_operator(operator_name.getText())
            term_list = context.termList()
            terms = () if term_list is None else tuple(term_list.term())
            return CompoundTerm(
                operator=operator,
                arguments=tuple(
                    self.visit_term(
                        term,
                        operator.input_concepts[index] if index < len(operator.input_concepts) else None,
                    )
                    for index, term in enumerate(terms)
                ),
            )

        list_literal = context.listLiteral()
        if list_literal is not None:
            return self.visit_list_literal(list_literal)

        atomic_term = context.atomicTerm()
        if atomic_term is not None:
            return self.visit_atomic_term(atomic_term, expected_concept)

        return self.visit_term(context.term(0), expected_concept)

    def visit_if_expression(self, context: Any) -> IfTerm:
        branches = context.term()
        return IfTerm(
            condition=self.visit_formula(context.formula()),
            when_true=self.visit_term(branches[0]),
            when_false=self.visit_term(branches[1]),
        )

    def visit_list_literal(self, context: Any) -> ListTerm:
        term_list = context.termList()
        items = () if term_list is None else tuple(self.visit_term(term) for term in term_list.term())
        return ListTerm(items=items)

    def visit_atomic_term(self, context: Any, expected_concept: Concept | None = None) -> Constant:
        boolean_literal = context.booleanLiteral()
        if boolean_literal is not None:
            return self._boolean_constant(boolean_literal.getText())
        return self._resolve_constant(context.constantName().getText(), expected_concept)

    def _resolve_constant(self, raw_text: str, expected_concept: Concept | None = None) -> Constant:
        is_numeric = raw_text.replace(".", "", 1).isdigit()
        if is_numeric:
            value = float(raw_text) if "." in raw_text else int(raw_text)
            return Constant(symbol=str(value), belong_concepts=(self._resolve_concept("ComplexNumber"),))

        # Quoted and unquoted names intentionally share one constant history key.
        symbol = self._strip_quotes(raw_text)
        existing = self._constants.get(symbol)
        if existing is not None:
            if expected_concept is not None and expected_concept not in existing.belong_concepts:
                concepts = tuple(concept.name for concept in existing.belong_concepts)
                raise ValueError(
                    f"FusionFlow constant {symbol!r} has concepts {concepts!r}; "
                    f"operator position requires concept {expected_concept.name!r}."
                )
            return existing

        if expected_concept is None:
            raise ValueError(f"Cannot infer concept for FusionFlow constant {symbol!r}.")
        # HACK: GK uses Any for implicit constants; FusionFlow infers direct operator arguments here.
        constant = Constant(symbol=symbol, belong_concepts=(expected_concept,))
        self._constants[symbol] = constant
        return constant

    def _boolean_constant(self, raw_text: str) -> Constant:
        value = raw_text.lower() == "true"
        constant = self._boolean_constants.get(value)
        if constant is None:
            constant = Constant(symbol=str(value), belong_concepts=(self._resolve_concept("Bool"),))
            self._boolean_constants[value] = constant
        return constant

    def _resolve_concept(self, name: str) -> Concept:
        try:
            return self._context.concepts[name]
        except KeyError:
            raise ValueError(f"Unknown FusionFlow concept {name!r}.") from None

    def _resolve_operator(self, name: str) -> Operator:
        try:
            return self._context.operators[name]
        except KeyError:
            raise ValueError(f"Unknown FusionFlow operator {name!r}.") from None

    @staticmethod
    def _strip_quotes(symbol: str) -> str:
        if symbol.startswith('"') and symbol.endswith('"'):
            return symbol[1:-1]
        return symbol


def parse_workflow(source: str, *, context: ParseContext) -> ParseResult:
    """Parse syntax and lower it without performing static workflow checks.

    Syntax failures are returned as parser diagnostics. Formula equality lowers
    to an assertion, inequality to NOT over an assertion, and ordered
    comparisons to KEDispatcher comparison operators asserted true. Compilation
    and workflow execution are outside this boundary.
    """

    listener = _DiagnosticListener()
    lexer = FusionFlowLexer(InputStream(source))
    lexer.removeErrorListeners()
    lexer.addErrorListener(listener)

    parser = FusionFlowParser(CommonTokenStream(lexer))
    parser.removeErrorListeners()
    parser.addErrorListener(listener)
    tree = parser.workflowFile()

    diagnostics = tuple(listener.diagnostics)
    if diagnostics:
        return ParseResult(core_ir=None, diagnostics=diagnostics)
    return ParseResult(core_ir=_CoreIRVisitor(context).visit_workflow_file(tree), diagnostics=diagnostics)
