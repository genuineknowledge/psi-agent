"""Validate the feishu-user-auth-onboarding skill (全员 user-token 授权普及).

E2 证据摄入 (company-todo-audit) 与任务读取依赖 user-token-only 工具
(feishu_message_search 等): 每个成员必须用本人身份授权一次。单条 need_auth 可以随用随授,
但周期任务 (audit / todo-check) 不能等用户撞上才补——所以需要主动普及: 定名单 → 解析 open_id →
查缺 → 逐个发授权卡 → 复查。这些测试钉住该 skill 的触发短语、真实工具引用、事实源纪律
(不编造名单/不代点/不轰炸) 与配套定时种子存在。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
SCHEDULES_DIR = WORKSPACE_ROOT / "schedules"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

SKILL = "feishu-user-auth-onboarding"


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body). Only top-level ``key: value`` lines are parsed."""
    assert text.startswith("---\n"), "SKILL.md must start with a YAML frontmatter fence"
    end = text.index("\n---", 4)
    fm_block = text[4:end]
    body = text[end + 4 :]
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if not line or line[0] in " \t":  # skip blanks and continuation/indented lines
            continue
        m = re.match(r"^([A-Za-z0-9_-]+):\s*(.*)$", line)
        if m:
            fm[m.group(1)] = m.group(2).strip().strip('"')
    return fm, body


def _public_tool_names() -> set[str]:
    """Collect public async tool function names (feishu_*/wiki_*) from tools/*.py via AST."""
    names: set[str] = set()
    for py in TOOLS_DIR.glob("*.py"):
        if py.name.startswith("_"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and (
                node.name.startswith("feishu_") or node.name.startswith("wiki_")
            ):
                names.add(node.name)
    return names


def _skill_text(name: str = SKILL) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


def _body(name: str = SKILL) -> str:
    return _split_frontmatter(_skill_text(name))[1]


def test_skill_file_exists() -> None:
    assert (SKILLS_DIR / SKILL / "SKILL.md").is_file(), f"missing skills/{SKILL}/SKILL.md"


def test_frontmatter_name_matches_dir_and_has_description() -> None:
    fm, body = _split_frontmatter(_skill_text())
    assert fm.get("name") == SKILL, "frontmatter name must equal dir name"
    assert fm.get("description", "").strip(), f"{SKILL} needs a non-empty description"
    assert fm.get("category", "").strip(), f"{SKILL} needs a category"
    assert body.strip(), f"{SKILL} needs a non-empty body"


def test_description_carries_the_trigger_phrases() -> None:
    description = _split_frontmatter(_skill_text())[0]["description"]
    for phrase in ("授权", "user-token", "E2"):
        assert phrase in description, f"description must contain the trigger phrase {phrase}"


def test_states_the_must_have_flow_steps() -> None:
    body = _body()
    for phrase in ("看板表", "feishu_auth_check", "feishu_auth_request", "24h"):
        assert phrase in body, f"missing flow anchor {phrase}"


def test_fact_source_discipline_and_red_lines_are_spelled_out() -> None:
    """名单来自事实源; 不代点、不冒充、不轰炸; 查询失败明说。"""
    body = _body()
    assert "不编造" in body, "must not invent roster names"
    assert "不代点" in body or "不伪造" in body, "must never authorize on a user's behalf"
    assert "不无限轰炸" in body, "must cap repeat requests (red line against harassment)"
    assert "待人工确认" in body, "unresolvable names must go to a human queue, not be dropped"


def test_only_references_real_tools() -> None:
    real = _public_tool_names()
    # sanity: the collector actually found the toolset
    assert "feishu_auth_request" in real
    assert "feishu_auth_check" in real

    referenced = set(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", _skill_text()))
    non_tools = {"feishu_context"}
    concrete = {n for n in referenced if not n.endswith("_") and n not in non_tools}
    unknown = concrete - real
    assert not unknown, f"skill references tool names that don't exist: {sorted(unknown)}"


def test_onboarding_is_wired_into_audit_e2_section() -> None:
    """E2 缺授权要指回本技能, 别让 audit 每条都撞一次 need_auth 才补。"""
    audit = _body("company-todo-audit")
    assert SKILL in audit, "audit must point batch-authorization needs to the onboarding skill"
    assert "feishu_auth_request" in audit, "audit must still use instant auth for single rows"


def test_seed_schedule_exists_and_is_self_contained() -> None:
    task = (SCHEDULES_DIR / "uat-onboarding" / "TASK.md").read_text(encoding="utf-8")
    assert 'cron: "0 10 * * 2"' in task, "must run weekly on Tuesday 10:00"
    assert SKILL in task, "the scheduled task must drive the onboarding skill"
    assert "H6icwLWn1iwpXAk73QMcA6MgnWc" in task, "schedule body must be self-contained (data source included)"


def test_indexed_in_agents_md() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{SKILL}`" in agents, f"{SKILL} must be listed in the AGENTS.md skills index"
