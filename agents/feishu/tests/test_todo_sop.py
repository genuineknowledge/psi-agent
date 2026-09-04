"""Validate the user-editable company TODO SOP config and its loader.

The company-specific TODO judgment requirements (three-level schema, quota, leave approval
codes, closure elements, ledger field schema, completion verdicts) are the one thing that
changes per company. They live in ``config/todo-sop.yaml`` and are loaded on demand by
``_feishu/todo_sop.py``; a missing or malformed file must make every caller fall back to its
built-in default instead of silently changing behaviour.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any

import yaml

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
CONFIG = WORKSPACE_ROOT / "config" / "todo-sop.yaml"
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

# Skills whose judgment requirements are driven by the company SOP.
SOP_SKILLS = (
    "todo-writing-standard",
    "company-todo-fill-check",
    "company-todo-audit",
    "company-todo-sync",
    "todo-completion-standard",
    "company-todo-review",
    "todo-alignment-check",
)


def _config() -> dict[str, Any]:
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8"))


def test_config_file_exists_and_parses() -> None:
    assert CONFIG.is_file(), "missing config/todo-sop.yaml"
    assert isinstance(_config(), dict), "config must parse to a mapping"


def test_schema_has_three_levels() -> None:
    levels = _config()["schema"]["levels"]
    assert [level["name"] for level in levels] == ["大目标", "小目标", "TODO"]


def test_rules_pin_the_params() -> None:
    rules = _config()["rules"]
    assert rules["todo_quota"] == 5
    assert rules["mentor_check_hours"] == 24
    assert rules["remind_time"]
    assert rules["check_time"]


def test_leave_approval_codes_are_present() -> None:
    leave = _config()["leave"]
    assert leave["leave_approval_code"]
    assert leave["makeup_approval_code"]


def test_closure_elements_have_five() -> None:
    assert len(_config()["closure_elements"]) == 5


def test_ledger_schema_fields_are_typed() -> None:
    fields = _config()["ledger_schema"]["fields"]
    assert fields, "ledger fields must not be empty"
    assert all(isinstance(field, dict) and field.get("field_name") and "type" in field for field in fields)


def test_completion_verdicts_have_four() -> None:
    assert len(_config()["completion_verdicts"]) == 4


def test_schema_requires_external_outcomes_on_big_goal() -> None:
    required = _config()["schema"]["levels"][0]["required"]
    assert "外部成果" in required
    assert "友商对比" in required


def test_alignment_section_is_present() -> None:
    cfg = _config()["alignment"]
    assert len(cfg["verdicts"]) == 4
    assert list(cfg["dimensions"]) == ["A1", "A2", "A3", "A4", "A5", "A6"]


def test_priority_importance_section() -> None:
    pri = _config()["priority"]
    importance = pri["importance"]
    for tier in ("high", "high_examples", "medium", "medium_examples", "low", "low_examples", "judgment"):
        assert importance.get(tier), f"priority.importance must carry {tier}"
    assert pri["business_priority"], "must pin the business priority ordering"
    assert "外部成果" in pri["seventy_three_rule"], "seventy_three_rule must tie to external outcomes"
    assert isinstance(pri["urgency_trap"], list) and pri["urgency_trap"]


def test_external_outcome_section() -> None:
    ext = _config()["external_outcome"]
    assert isinstance(ext["types"], list) and ext["types"], "external_outcome.types must be a non-empty list"
    assert isinstance(ext["evidence"], list) and ext["evidence"], "external_outcome.evidence must be a non-empty list"
    assert isinstance(ext["team_types"], list) and ext["team_types"]
    assert ext["annual_trajectory"], "annual_trajectory must be present"


def test_judgment_skills_point_at_the_config() -> None:
    for name in SOP_SKILLS:
        body = (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")
        assert "config/todo-sop.yaml" in body, f"{name} must point at config/todo-sop.yaml"


def test_loader_valid_ledger_check() -> None:
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))
    todo_sop = importlib.import_module("_feishu.todo_sop")
    assert todo_sop._valid_ledger({"ledger_schema": {"fields": [{"field_name": "x", "type": 1}]}})
    assert not todo_sop._valid_ledger({})
    assert not todo_sop._valid_ledger({"ledger_schema": {}})
    assert not todo_sop._valid_ledger({"ledger_schema": {"fields": []}})
