from __future__ import annotations

import importlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GRAMMAR = ROOT / "grammar" / "FusionFlow.g4"
GENERATED = ROOT / "fusion_flow_next" / "generated"


def test_preset_operators_document_signatures() -> None:
    grammar = GRAMMAR.read_text(encoding="utf-8")
    preset_operators = re.findall(
        r"^\s*(?::|\|)\s*'([a-z][a-z0-9_]*)'\s*$",
        grammar,
        re.MULTILINE,
    )
    signatures = re.findall(
        r"^\s*\*\s+([a-z][a-z0-9_]*)\(([^)]*)\)\s*->\s*[A-Z][A-Za-z0-9_]*\s+\[arity\s+(\d+)\]\s*$",
        grammar,
        re.MULTILINE,
    )

    assert sorted(name for name, _, _ in signatures) == sorted(set(preset_operators))
    for operator, parameters, arity in signatures:
        parameter_types = [item.strip() for item in parameters.split(",") if item.strip()]
        assert all(re.fullmatch(r"[A-Z][A-Za-z0-9_]*", item) for item in parameter_types), operator
        assert len(parameter_types) == int(arity), operator


def test_generated_directory_contains_runtime_sources_only() -> None:
    assert {path.name for path in GENERATED.iterdir() if path.is_file()} == {
        "FusionFlowLexer.py",
        "FusionFlowParser.py",
        "__init__.py",
    }


def test_generated_parser_imports() -> None:
    importlib.import_module("fusion_flow_next.generated.FusionFlowLexer")
    importlib.import_module("fusion_flow_next.generated.FusionFlowParser")
