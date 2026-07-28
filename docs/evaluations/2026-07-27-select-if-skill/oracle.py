from __future__ import annotations

import os
import re
import runpy
import sys
from typing import Any

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
_SKILL_ROOT = os.path.join(
    _ROOT,
    "examples",
    "haitun-workspace",
    "skills",
    "fusion-flow-next",
)
_RUNNER = os.path.join(_SKILL_ROOT, "examples", "run_workflow.py")
_GRAMMAR = os.path.join(_SKILL_ROOT, "grammar", "FusionFlow.g4")
for _path in (os.path.join(_ROOT, "src"), _SKILL_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)
_runtime = runpy.run_path(_RUNNER)
Concept = _runtime["Concept"]
Operator = _runtime["Operator"]
ParseContext = _runtime["ParseContext"]
WorkflowGraphCompiler = _runtime["WorkflowGraphCompiler"]
parse_workflow = _runtime["parse_workflow"]

_FENCE = re.compile(r"\A\s*```fusionflow[ \t]*\r?\n(?P<source>.*?)\r?\n```[ \t]*\s*\Z", re.DOTALL)
_INLINE_IF = re.compile(r"consumes\s*\([^)]*\)\s*==\s*\[[^\]]*\bif\s*\(", re.DOTALL)
_SIGNATURE = re.compile(
    r"^\s*\*\s+([a-z][a-z0-9_]*)\(([^)]*)\)\s*->\s*([A-Z][A-Za-z0-9_]*)\s+"
    r"\[arity\s+(\d+)\]\s*$",
    re.MULTILINE,
)


def _compile_workflow(source: str) -> Any:
    with open(_GRAMMAR, encoding="utf-8") as grammar_file:
        grammar = grammar_file.read()
    signatures = _SIGNATURE.findall(grammar)
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
    for name in ("comparison_lt_op", "comparison_lte_op", "comparison_gt_op", "comparison_gte_op"):
        operators[name] = Operator(name=name, output_concept=concepts["Bool"])
    parsed = parse_workflow(source, context=ParseContext(concepts=concepts, operators=operators))
    if parsed.core_ir is None:
        details = "; ".join(
            diagnostic.message
            if diagnostic.span is None
            else f"{diagnostic.span.start.line}:{diagnostic.span.start.column}: {diagnostic.message}"
            for diagnostic in parsed.diagnostics
        )
        raise ValueError(f"workflow parse failed: {details}")
    compilations = WorkflowGraphCompiler().compile(parsed.core_ir)
    if not isinstance(compilations, tuple) or len(compilations) != 1:
        raise ValueError("oracle expects exactly one workflow")
    return compilations[0]


def extract_source(text: str) -> str | None:
    match = _FENCE.fullmatch(text)
    return match.group("source") if match else None


def _condition_operators(condition: object) -> set[str]:
    operator = getattr(condition, "operator", None)
    found = {operator} if isinstance(operator, str) else set()
    for child in getattr(condition, "conditions", ()):
        found.update(_condition_operators(child))
    return found


def _comparisons(condition: object) -> list[object]:
    if hasattr(condition, "left") and hasattr(condition, "right"):
        return [condition]
    result: list[object] = []
    for child in getattr(condition, "conditions", ()):
        result.extend(_comparisons(child))
    return result


def _selector_depth(selectors: tuple[Any, ...]) -> int:
    by_output = {selector.output_artifact_id: selector for selector in selectors}

    def depth(selector: Any, active: set[str]) -> int:
        output = selector.output_artifact_id
        if output in active:
            return 0
        active.add(output)
        children = [
            depth(by_output[candidate], active)
            for candidate in (selector.when_true_artifact_id, selector.when_false_artifact_id)
            if candidate in by_output
        ]
        active.remove(output)
        return 1 + max(children, default=0)

    return max((depth(selector, set()) for selector in selectors), default=0)


def _graph_facts(compiled: Any, source: str) -> dict[str, Any]:
    graph = compiled.graph
    selectors = graph.selectors
    selector_outputs = {selector.output_artifact_id for selector in selectors}
    candidates = {
        candidate
        for selector in selectors
        for candidate in (selector.when_true_artifact_id, selector.when_false_artifact_id)
    }
    produced_by = {edge.artifact_id: edge.step_id for edge in graph.edges if getattr(edge, "kind", None) == "produces"}
    consumed_by: dict[str, set[str]] = {}
    for edge in graph.edges:
        if getattr(edge, "kind", None) == "consumes":
            consumed_by.setdefault(edge.step_id, set()).add(edge.artifact_id)
    workflow_outputs = {artifact.artifact_id for artifact in graph.artifacts if artifact.is_output}
    output_steps = {step_id for artifact_id, step_id in produced_by.items() if artifact_id in workflow_outputs}
    selectors_consumed_by_output_step = [
        len(consumed_by.get(step_id, set()) & selector_outputs) for step_id in output_steps
    ]
    operators = {operator for selector in selectors for operator in _condition_operators(selector.condition)}
    comparisons = [comparison for selector in selectors for comparison in _comparisons(selector.condition)]
    return {
        "named_selector_count": len(selectors),
        "selector_chain_depth": _selector_depth(selectors),
        "condition_operators": operators,
        "comparisons": comparisons,
        "eager_candidate_step_count": len({produced_by[item] for item in candidates if item in produced_by}),
        "final_step_consumes_selector_outputs": max(selectors_consumed_by_output_step, default=0),
        "one_final_step_consumes_all_selector_outputs": any(
            count == len(selector_outputs) for count in selectors_consumed_by_output_step
        ),
        "selectors_are_independent": not any(
            candidate in selector_outputs
            for selector in selectors
            for candidate in (selector.when_true_artifact_id, selector.when_false_artifact_id)
        ),
        "selector_output_is_workflow_output": bool(selector_outputs & workflow_outputs),
        "inline_if_in_consumes_count": len(_INLINE_IF.findall(source)),
    }


def _states_limitation(text: str) -> bool:
    lowered = text.lower()
    limitation = any(word in lowered for word in ("cannot", "can't", "unable", "unsupported", "无法", "不能", "不支持"))
    eager = any(word in lowered for word in ("eager", "backend", "后端", "两个分支", "所有分支", "都执行", "预先执行"))
    return limitation and eager and "token" in lowered


def _offers_approximation(text: str) -> bool:
    return bool(
        re.search(
            r"(?is)(?:here(?:'s| is)|offer|provide|instead|can use).{0,80}(?:eager|approximation)"
            r"|(?:可以|可改用|建议|下面).{0,40}(?:近似|两个分支都)",
            text,
        )
    )


def evaluate_response(case: dict[str, Any], text: str) -> dict[str, Any]:
    expected = case["oracle"]
    source = extract_source(text)
    response_kind = (
        "single_fusionflow_fence"
        if source is not None
        else "refusal_without_code_fence"
        if "```" not in text and text.strip()
        else "invalid_response"
    )
    predicates: dict[str, bool] = {"response": response_kind == expected["response"]}
    diagnostics: dict[str, str] = {}
    facts: dict[str, Any] = {}
    compiled: Any | None = None
    if source is not None:
        try:
            compiled = _compile_workflow(source)
            facts = _graph_facts(compiled, source)
        except Exception as error:
            diagnostics["compile"] = f"{type(error).__name__}: {error}"

    if "compiles" in expected:
        predicates["compiles"] = (compiled is not None) == expected["compiles"]
    if compiled is not None:
        for key in (
            "named_selector_count",
            "selector_chain_depth",
            "eager_candidate_step_count",
            "final_step_consumes_selector_outputs",
            "selectors_are_independent",
            "one_final_step_consumes_all_selector_outputs",
            "selector_output_is_workflow_output",
            "inline_if_in_consumes_count",
        ):
            if key in expected:
                predicates[key] = facts[key] == expected[key]
        if "condition_operators" in expected:
            predicates["condition_operators"] = set(expected["condition_operators"]) <= facts["condition_operators"]
        if expected.get("has_ordered_condition"):
            wanted_operator = expected["ordered_condition_operator"]
            wanted_literal = expected["ordered_condition_literal"]
            predicates["has_ordered_condition"] = any(
                getattr(comparison, "operator", None) == wanted_operator
                and wanted_literal
                in {
                    getattr(getattr(comparison, "left", None), "value", None),
                    getattr(getattr(comparison, "right", None), "value", None),
                }
                for comparison in facts["comparisons"]
            )
    else:
        for key in expected:
            if key not in {
                "response",
                "compiles",
                "must_state_eager_backend_limitation",
                "must_not_offer_eager_approximation",
            }:
                predicates[key] = False

    if "must_state_eager_backend_limitation" in expected:
        predicates["must_state_eager_backend_limitation"] = _states_limitation(text)
    if "must_not_offer_eager_approximation" in expected:
        predicates["must_not_offer_eager_approximation"] = not _offers_approximation(text) and source is None

    return {
        "case_id": case["id"],
        "passed": all(predicates.values()),
        "predicates": predicates,
        "diagnostics": diagnostics,
        "graph": compiled.graph.to_dict() if compiled is not None else None,
    }
