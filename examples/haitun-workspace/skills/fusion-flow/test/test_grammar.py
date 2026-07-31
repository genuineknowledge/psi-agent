from __future__ import annotations

import importlib
import re
from pathlib import Path

from fusion_flow.core_ir import CompoundTerm, Concept, Constant, ListTerm, Operator
from fusion_flow.parser import ParseContext, parse_workflow
from fusion_flow.workflow_runner import compile_workflow

ROOT = Path(__file__).resolve().parents[1]
GRAMMAR = ROOT / "grammar" / "FusionFlow.g4"
SKILL = ROOT / "SKILL.md"

CANONICAL_DATAFLOW_OPERATORS = {
    "input_workflow",
    "output_workflow",
    "consumes",
    "produces",
}
REMOVED_DATAFLOW_OPERATORS = {
    "input_workflow_multi",
    "output_workflow_multi",
    "consumes_multi",
    "produces_multi",
}
SIGNATURE_PATTERN = re.compile(
    r"^\s*\*\s+([a-z][a-z0-9_]*)\(([^)]*)\)\s*->\s*([A-Z][A-Za-z0-9_]*)\s+"
    r"\[arity\s+(\d+)\]\s*$",
    re.MULTILINE,
)


def _documented_signatures() -> list[tuple[str, str, str, str]]:
    return SIGNATURE_PATTERN.findall(GRAMMAR.read_text(encoding="utf-8"))


def _skill_parse_context() -> ParseContext:
    signatures = _documented_signatures()
    concept_names = {"Bool", "ComplexNumber"}
    for _, parameters, output_type, _ in signatures:
        concept_names.add(output_type)
        concept_names.update(item.strip() for item in parameters.split(",") if item.strip())
    concepts = {name: Concept(name) for name in concept_names}
    operators = {
        name: Operator(
            name=name,
            input_concepts=tuple(concepts[item.strip()] for item in parameters.split(",") if item.strip()),
            output_concept=concepts[output_type],
        )
        for name, parameters, output_type, _ in signatures
    }
    for name in ("+", "-", "*", "/", "%", "^"):
        operators[name] = Operator(name=name, output_concept=concepts["ComplexNumber"])
    for name in (
        "comparison_lt_op",
        "comparison_lte_op",
        "comparison_gt_op",
        "comparison_gte_op",
    ):
        operators[name] = Operator(name=name, output_concept=concepts["Bool"])
    return ParseContext(concepts=concepts, operators=operators)


def test_skill_examples_follow_canonical_dataflow_contract() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    examples = re.findall(r"```fusionflow\s*\n(.*?)\n```", skill, re.DOTALL)

    assert examples
    removed_operator_pattern = "|".join(re.escape(name) for name in sorted(REMOVED_DATAFLOW_OPERATORS))
    assert re.search(rf"\b(?:{removed_operator_pattern})\b", skill) is None

    for index, source in enumerate(examples, start=1):
        parsed = parse_workflow(source, context=_skill_parse_context())
        assert parsed.diagnostics == (), f"FusionFlow example {index}"
        assert parsed.core_ir is not None
        for workflow in parsed.core_ir.workflows:
            for assertion in workflow.assertions:
                assert not (
                    isinstance(assertion.rhs, CompoundTerm)
                    and assertion.rhs.operator.name in CANONICAL_DATAFLOW_OPERATORS
                ), f"FusionFlow example {index} reverses a canonical dataflow assertion"
                if not isinstance(assertion.lhs, CompoundTerm):
                    continue
                operator_name = assertion.lhs.operator.name
                if operator_name not in CANONICAL_DATAFLOW_OPERATORS:
                    continue
                assert len(assertion.lhs.arguments) == 1, (
                    f"FusionFlow example {index} uses legacy arity for {operator_name}"
                )
                assert isinstance(assertion.rhs, ListTerm), (
                    f"FusionFlow example {index} must give {operator_name} an explicit List RHS"
                )
                assert all(isinstance(item, Constant) for item in assertion.rhs.items), (
                    f"FusionFlow example {index} must name Artifacts directly in {operator_name}"
                )


def test_skill_examples_compile_for_the_one_shot_runner() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    examples = re.findall(r"```fusionflow\s*\n(.*?)\n```", skill, re.DOTALL)

    assert examples
    for index, source in enumerate(examples, start=1):
        compiled = compile_workflow(source, strict_executors=True)
        assert set(compiled.executor_kinds.values()) == {"Agent"}, f"FusionFlow example {index}"


def test_generated_parser_imports() -> None:
    importlib.import_module("fusion_flow.generated.FusionFlowLexer")
    importlib.import_module("fusion_flow.generated.FusionFlowParser")
