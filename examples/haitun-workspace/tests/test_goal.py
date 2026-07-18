"""Tests for the Haitun workspace ``goal`` toolset.

No network and no extra dependencies: goals are written to a ``tmp_path``
workspace and read back, exercising slugging, frontmatter round-trips,
``[[link]]`` extraction, progress logging, status transitions, and listing.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

from psi_agent.session.tool_registry import ToolFunction

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

impl: Any = importlib.import_module("_goal_impl")
goal: Any = importlib.import_module("goal")


async def _set(tmp_path: Path, title: str, description: str = "", **kw: Any) -> dict[str, Any]:
    return await impl.goal_set_impl(title=title, description=description, workspace_raw=str(tmp_path), **kw)


def test_tool_metadata_is_loadable() -> None:
    for fn, expected in (
        (goal.goal_set, {"title", "description", "status", "priority", "progress", "target_date", "tags", "overwrite"}),
        (goal.goal_progress, {"title_or_slug", "note", "progress", "status"}),
        (goal.goal_get, {"title_or_slug"}),
        (goal.goal_list, {"status", "tag", "limit"}),
        (goal.goal_delete, {"title_or_slug"}),
    ):
        meta = ToolFunction.from_callable(fn)
        assert meta.name == fn.__name__
        assert set(meta.parameters["properties"]) >= expected


def test_tool_returns_are_json() -> None:
    # The public tool functions must serialize to JSON strings, not dicts.
    assert impl.dumps_result({"ok": True}) == '{"ok": true}'


def test_slugify() -> None:
    assert impl.slugify("Ship Payments v2") == "ship-payments-v2"
    assert impl.slugify("  90% Coverage!! ") == "90-coverage"
    assert impl.slugify("###") == "untitled"


def test_slugify_preserves_non_latin_titles() -> None:
    assert impl.slugify("上线支付") == "上线支付"
    assert impl.slugify("学习代码库") == "学习代码库"
    assert impl.slugify("上线支付") != impl.slugify("学习代码库")


def test_extract_links_dedupes_and_handles_labels() -> None:
    body = "Depends on [[Auth]] and [[Auth|the auth rewrite]] plus [[Billing]]."
    assert impl.extract_links(body) == ["auth", "billing"]


def test_normalizers_reject_bad_values() -> None:
    assert impl._normalize_status("bogus") is None
    assert impl._normalize_priority("urgent") is None
    assert impl._clamp_progress("nope") is None
    assert impl._clamp_progress(150) == 100
    assert impl._clamp_progress(-5) == 0
    assert impl._normalize_status("") == impl.DEFAULT_STATUS
    assert impl._normalize_priority("HIGH") == "high"


async def test_set_creates_goal_with_frontmatter(tmp_path: Path) -> None:
    result = await _set(
        tmp_path,
        "Ship Payments v2",
        "Deliver the rewrite. Depends on [[Auth]].",
        priority="high",
        tags="product, q3",
        target_date="2026-09-01",
    )
    assert result["ok"] is True
    assert result["slug"] == "ship-payments-v2"
    assert result["created"] is True
    assert result["status"] == "active"
    assert result["priority"] == "high"
    assert result["progress"] == 0
    assert result["target_date"] == "2026-09-01"
    assert result["links"] == ["auth"]

    goal_file = tmp_path / "goals" / "ship-payments-v2.md"
    text = goal_file.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    meta, body = impl._parse_goal(text)
    assert meta["title"] == "Ship Payments v2"
    assert meta["tags"] == ["product", "q3"]
    assert "[[Auth]]" in body


async def test_set_rejects_invalid_status_and_priority(tmp_path: Path) -> None:
    bad_status = await _set(tmp_path, "G", status="wip")
    assert bad_status["ok"] is False and "status" in bad_status["message"]
    bad_priority = await _set(tmp_path, "G", priority="urgent")
    assert bad_priority["ok"] is False and "priority" in bad_priority["message"]


async def test_set_no_overwrite_refuses_existing(tmp_path: Path) -> None:
    await _set(tmp_path, "Goal A", "v1")
    result = await _set(tmp_path, "Goal A", "v2", overwrite=False)
    assert result["ok"] is False
    assert "already exists" in result["message"]


async def test_update_preserves_created_and_unset_fields(tmp_path: Path) -> None:
    await _set(tmp_path, "Goal A", "desc", priority="high", tags="x")
    created = (await impl.goal_get_impl("Goal A", workspace_raw=str(tmp_path)))["created"]
    # Update with empty priority/tags/description — should keep prior values.
    await _set(tmp_path, "Goal A", "", priority="", tags="")
    after = await impl.goal_get_impl("Goal A", workspace_raw=str(tmp_path))
    assert after["created"] == created
    assert after["priority"] == "high"
    assert after["tags"] == ["x"]
    assert after["description"] == "desc"


async def test_progress_appends_log_and_moves_percent(tmp_path: Path) -> None:
    await _set(tmp_path, "Goal A")
    r1 = await impl.goal_progress_impl("Goal A", "started", progress=25, workspace_raw=str(tmp_path))
    assert r1["ok"] is True
    assert r1["progress"] == 25
    assert r1["status"] == "active"
    assert r1["entry"]["note"] == "started"
    assert len(r1["log"]) == 1

    r2 = await impl.goal_progress_impl("Goal A", "halfway", progress=50, workspace_raw=str(tmp_path))
    assert r2["progress"] == 50
    assert len(r2["log"]) == 2


async def test_progress_100_implies_achieved(tmp_path: Path) -> None:
    await _set(tmp_path, "Goal A")
    r = await impl.goal_progress_impl("Goal A", "done", progress=100, workspace_raw=str(tmp_path))
    assert r["progress"] == 100
    assert r["status"] == "achieved"


async def test_progress_requires_something_to_record(tmp_path: Path) -> None:
    await _set(tmp_path, "Goal A")
    r = await impl.goal_progress_impl("Goal A", "", workspace_raw=str(tmp_path))
    assert r["ok"] is False
    assert "at least one" in r["message"]


async def test_progress_missing_goal(tmp_path: Path) -> None:
    r = await impl.goal_progress_impl("nope", "note", workspace_raw=str(tmp_path))
    assert r["ok"] is False
    assert "No goal" in r["message"]


async def test_list_filters_and_rolls_up_status(tmp_path: Path) -> None:
    await _set(tmp_path, "A", priority="low", tags="team")
    await _set(tmp_path, "B", priority="high", tags="team")
    await _set(tmp_path, "C", status="achieved")
    await impl.goal_progress_impl("A", "note", status="paused", workspace_raw=str(tmp_path))

    all_goals = await impl.goal_list_impl(workspace_raw=str(tmp_path))
    assert all_goals["total"] == 3
    assert all_goals["status_counts"]["achieved"] == 1
    assert all_goals["status_counts"]["paused"] == 1
    # High priority sorts to the top.
    assert all_goals["goals"][0]["slug"] == "b"

    active_only = await impl.goal_list_impl(status="active", workspace_raw=str(tmp_path))
    assert {g["slug"] for g in active_only["goals"]} == {"b"}

    team = await impl.goal_list_impl(tag="team", workspace_raw=str(tmp_path))
    assert {g["slug"] for g in team["goals"]} == {"a", "b"}


async def test_list_rejects_bad_status_filter(tmp_path: Path) -> None:
    r = await impl.goal_list_impl(status="wip", workspace_raw=str(tmp_path))
    assert r["ok"] is False


async def test_get_missing_goal(tmp_path: Path) -> None:
    r = await impl.goal_get_impl("ghost", workspace_raw=str(tmp_path))
    assert r["ok"] is False
    assert r["slug"] == "ghost"


async def test_delete_reports_orphaned_backlinks(tmp_path: Path) -> None:
    await _set(tmp_path, "Auth", "the auth rewrite")
    await _set(tmp_path, "Payments", "needs [[Auth]] first")
    r = await impl.goal_delete_impl("Auth", workspace_raw=str(tmp_path))
    assert r["ok"] is True
    assert r["deleted"] is True
    assert r["orphaned_backlinks"] == ["payments"]
    assert not (tmp_path / "goals" / "auth.md").exists()


async def test_chinese_titles_do_not_collide_on_disk(tmp_path: Path) -> None:
    await _set(tmp_path, "上线支付", "见 [[学习代码库]]")
    await _set(tmp_path, "学习代码库", "读懂支付模块")
    files = sorted(p.name for p in (tmp_path / "goals").glob("*.md"))
    assert files == ["上线支付.md", "学习代码库.md"]
    back = await impl.goal_get_impl("上线支付", workspace_raw=str(tmp_path))
    assert back["ok"] is True
    assert back["links"] == ["学习代码库"]


async def test_tool_shell_returns_json_string(tmp_path: Path, monkeypatch: Any) -> None:
    # The public goal.py functions must return JSON strings the runtime can emit.
    monkeypatch.setenv("WORKSPACE_DIR", str(tmp_path))
    out = await goal.goal_set("Demo Goal", "body", priority="high")
    parsed = json.loads(out)
    assert parsed["ok"] is True
    assert parsed["slug"] == "demo-goal"

    listed = json.loads(await goal.goal_list())
    assert listed["ok"] is True
    assert listed["total"] == 1
