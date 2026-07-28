from __future__ import annotations

import importlib
import re
from pathlib import Path

from fusion_flow_next.core_ir import CompoundTerm, Concept, Constant, ListTerm, Operator
from fusion_flow_next.parser import ParseContext, parse_workflow

ROOT = Path(__file__).resolve().parents[1]
GRAMMAR = ROOT / "grammar" / "FusionFlow.g4"
GENERATED = ROOT / "fusion_flow_next" / "generated"
SKILL = ROOT / "SKILL.md"

CANONICAL_DATAFLOW_OPERATORS = {
    "input_workflow",
    "output_workflow",
    "consumes",
    "produces",
}
EXPECTED_PRESET_OPERATORS = CANONICAL_DATAFLOW_OPERATORS | {
    "agent_config",
    "agent_system_prompt",
    "allowed_tool",
    "foreach_item",
    "max_attempts",
    "max_concurrency",
    "max_output_tokens",
    "max_turns",
    "program_path",
    "reasoning_effort",
    "resource_requirement",
    "step_executor",
    "step_instruction",
    "step_name",
    "step_timeout",
    "temperature",
    "workflow_timeout",
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


def test_preset_operators_document_signatures() -> None:
    grammar = GRAMMAR.read_text(encoding="utf-8")
    preset_operators = re.findall(
        r"^\s*(?::|\|)\s*'([a-z][a-z0-9_]*)'\s*$",
        grammar,
        re.MULTILINE,
    )
    signatures = _documented_signatures()

    assert len(set(preset_operators)) == 21
    assert sorted(name for name, _, _, _ in signatures) == sorted(set(preset_operators))
    assert set(preset_operators) == EXPECTED_PRESET_OPERATORS
    assert {
        name: (parameters, return_type, arity)
        for name, parameters, return_type, arity in signatures
        if name in {"program_path", "agent_system_prompt"}
    } == {
        "program_path": ("Program", "Path", "1"),
        "agent_system_prompt": ("Agent", "Instruction", "1"),
    }
    canonical_list_operators = {
        "input_workflow": ("Workflow", "List", "1"),
        "consumes": ("Step", "List", "1"),
        "produces": ("Step", "List", "1"),
        "output_workflow": ("Workflow", "List", "1"),
    }
    assert {
        name: (parameters, return_type, arity)
        for name, parameters, return_type, arity in signatures
        if name in canonical_list_operators
    } == canonical_list_operators
    assert all("_multi" not in name for name in preset_operators)
    for operator, parameters, _, arity in signatures:
        parameter_types = [item.strip() for item in parameters.split(",") if item.strip()]
        assert all(re.fullmatch(r"[A-Z][A-Za-z0-9_]*", item) for item in parameter_types), operator
        assert len(parameter_types) == int(arity), operator


def test_skill_examples_follow_canonical_dataflow_contract() -> None:
    skill = SKILL.read_text(encoding="utf-8")
    examples = re.findall(r"```fusionflow\s*\n(.*?)\n```", skill, re.DOTALL)

    assert examples
    removed_operator_pattern = "|".join(re.escape(name) for name in sorted(REMOVED_DATAFLOW_OPERATORS))
    assert re.search(rf"\b(?:{removed_operator_pattern})\b", skill) is None

    seen_operators: set[str] = set()
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
                seen_operators.add(operator_name)
                assert len(assertion.lhs.arguments) == 1, (
                    f"FusionFlow example {index} uses legacy arity for {operator_name}"
                )
                assert isinstance(assertion.rhs, ListTerm), (
                    f"FusionFlow example {index} must give {operator_name} an explicit List RHS"
                )
                assert all(isinstance(item, Constant) for item in assertion.rhs.items), (
                    f"FusionFlow example {index} must name Artifacts directly in {operator_name}"
                )

    assert seen_operators == CANONICAL_DATAFLOW_OPERATORS


def test_preset_operators_have_five_disjoint_owner_groups() -> None:
    grammar = GRAMMAR.read_text(encoding="utf-8")
    builtin_rule = re.search(
        r"^workflowBuiltinOperator\s*\n(?P<body>.*?^\s*;)",
        grammar,
        re.MULTILINE | re.DOTALL,
    )
    assert builtin_rule is not None
    assert set(re.findall(r"\b([a-z][A-Za-z]+Operator)\b", builtin_rule.group("body"))) == {
        "workflowOwnerOperator",
        "programOwnerOperator",
        "stepOwnerOperator",
        "dataResourceOperator",
        "agentOwnerOperator",
    }

    program_rule = re.search(
        r"^programOwnerOperator\s*\n(?P<body>.*?^\s*;)",
        grammar,
        re.MULTILINE | re.DOTALL,
    )
    assert program_rule is not None
    assert re.findall(r"'([a-z][a-z0-9_]*)'", program_rule.group("body")) == [
        "program_path",
    ]

    data_resource_rule = re.search(
        r"^dataResourceOperator\s*\n(?P<body>.*?^\s*;)",
        grammar,
        re.MULTILINE | re.DOTALL,
    )
    assert data_resource_rule is not None
    assert re.findall(r"'([a-z][a-z0-9_]*)'", data_resource_rule.group("body")) == [
        "consumes",
        "produces",
        "foreach_item",
        "resource_requirement",
    ]


def test_generated_directory_contains_runtime_sources_only() -> None:
    assert {path.name for path in GENERATED.iterdir() if path.is_file()} == {
        "FusionFlowLexer.py",
        "FusionFlowParser.py",
        "__init__.py",
    }


def test_generated_parser_imports() -> None:
    importlib.import_module("fusion_flow_next.generated.FusionFlowLexer")
    importlib.import_module("fusion_flow_next.generated.FusionFlowParser")
