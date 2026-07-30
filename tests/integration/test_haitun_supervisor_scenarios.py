# ruff: noqa: RUF001

from __future__ import annotations

import runpy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
RUNNER = ROOT / "examples" / "haitun-workspace" / "demo_supervisor_scenarios.py"


def _module() -> dict[str, Any]:
    return runpy.run_path(str(RUNNER), run_name="scenario_test")


def test_scenario_schema_and_identities() -> None:
    module = _module()
    scenarios = module["SCENARIOS"]
    assert set(scenarios) == {"ceo-cicd", "legal-agent-governance"}
    ceo = scenarios["ceo-cicd"]
    legal = scenarios["legal-agent-governance"]
    assert ceo.user_id == "demo-ceo-cicd"
    assert ceo.profile_id == "executive-decision"
    assert len(ceo.turns) == 8
    assert legal.user_id == "demo-legal-agent-governance"
    assert legal.profile_id == "legal-learning"
    assert len(legal.turns) == 8
    assert all(not scenario.session_id.startswith("supervisor-") for scenario in scenarios.values())


def test_evidence_schema_is_complete() -> None:
    module = _module()
    evidence = module["new_turn_evidence"]("REAL", "question")
    assert set(evidence) == {
        "mode",
        "timestamp",
        "user_message",
        "assistant_message",
        "supervisor_input",
        "raw_advice",
        "validated_advice",
        "prompt_advice_injected",
        "profile",
        "heatmap_before",
        "heatmap_after",
        "map_before",
        "map_after",
        "errors",
    }


def test_deterministic_scenarios_break_out_and_isolate_users(tmp_path: Path) -> None:
    module = _module()
    result = module["run_deterministic"](tmp_path)
    assert len(result["ceo-cicd"]["turns"]) == 8
    assert len(result["legal-agent-governance"]["turns"]) == 8
    assert any(turn["validated_advice"]["breakout"]["needed"] for turn in result["ceo-cicd"]["turns"])
    assert any(turn["validated_advice"]["breakout"]["needed"] for turn in result["legal-agent-governance"]["turns"])
    assert result["ceo-cicd"]["user_hash"] != result["legal-agent-governance"]["user_hash"]
    assert all(turn["mode"] == "DETERMINISTIC MOCK" for item in result.values() for turn in item["turns"])


def test_report_contains_dialogue_and_evidence(tmp_path: Path) -> None:
    module = _module()
    result = module["run_deterministic"](tmp_path)
    report = module["build_report"](result, real_failures=["APIConnectionError('Connection error.')"])
    assert "CEO：是否采用 CI/CD" in report
    assert "法律顾问：Agent 治理" in report
    assert "DETERMINISTIC MOCK" in report
    assert "APIConnectionError" in report
    assert "副 Agent 原始输出" in report
    assert "热力图" in report
    assert "知识地图" in report
