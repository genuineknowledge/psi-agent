"""Unit tests for POC L2 playbook probe (capability self-check)."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

import pytest

FEISHU_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = FEISHU_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

impl: Any = importlib.import_module("_poc_l2_impl")
tool: Any = importlib.import_module("poc_l2_probe")

PLAYBOOK = FEISHU_ROOT / "skills" / "poc-l2-reproduce" / "playbooks" / "proposal-sop-closed-loop.json"


def test_proposal_sop_playbook_passes_against_feishu_agent() -> None:
    playbook = json.loads(PLAYBOOK.read_text(encoding="utf-8"))
    result = impl.run_playbook(
        playbook,
        agent_dir=FEISHU_ROOT,
        workspace_dir=FEISHU_ROOT,
        tools_dir=TOOLS_DIR,
    )
    assert result["verdict"] == "pass", result
    assert result["operable"] is True
    assert result["ok"] is True
    assert result["gaps"] == []
    assert "disclaimer" in result


def test_method_text_alone_is_not_applicable() -> None:
    result = impl.probe_from_method_text_only("实验方法: 人工盲评 1000 条")
    assert result["verdict"] == "l2_not_applicable"
    assert result["operable"] is False


def test_assert_tool_missing_fails() -> None:
    playbook = {
        "id": "t",
        "title": "t",
        "steps": [{"id": "x", "kind": "assert_tool", "tool": "no_such_tool_xyz"}],
    }
    result = impl.run_playbook(
        playbook,
        agent_dir=FEISHU_ROOT,
        workspace_dir=FEISHU_ROOT,
        tools_dir=TOOLS_DIR,
    )
    assert result["verdict"] == "fail"
    assert result["operable"] is True


def test_unsupported_step_is_not_applicable() -> None:
    playbook = {
        "id": "t",
        "title": "t",
        "steps": [
            {
                "id": "blind",
                "kind": "unsupported",
                "reason": "needs human blind labels",
            }
        ],
    }
    result = impl.run_playbook(
        playbook,
        agent_dir=FEISHU_ROOT,
        workspace_dir=FEISHU_ROOT,
        tools_dir=TOOLS_DIR,
    )
    assert result["verdict"] == "l2_not_applicable"
    assert result["operable"] is False


@pytest.mark.anyio
async def test_poc_l2_probe_tool_loads_playbook(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tool._rp,
        "agent_dir",
        lambda explicit="": str(FEISHU_ROOT),
    )
    monkeypatch.setattr(
        tool._rp,
        "workspace_dir",
        lambda explicit="": str(FEISHU_ROOT),
    )
    raw = await tool.poc_l2_probe(playbook_path="skills/poc-l2-reproduce/playbooks/proposal-sop-closed-loop.json")
    data = json.loads(raw)
    assert data["verdict"] == "pass", data
