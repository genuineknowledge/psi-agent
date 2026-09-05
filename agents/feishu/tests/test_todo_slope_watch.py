"""Validate the dynamic slope-watch consumer in the company TODO audit chain.

The static exit (todo-truthfulness-check 防复制) catches same-cycle cross-person
copying; what was still missing on main is the dynamic consumer that catches a
person copying their own previous period (半衰期滑坡). The plan doc specs it as:
质量时间序列逐期落库, trend detection on similarity jumps, then 关注而非惩罚 -
private reminder with evidence, repeated hits into the mentor report 需关注 block,
and only then an evidence-only watchlist for boss/mentor. These tests guard the
parts whose removal would silently regress to "the tool exists but nobody consumes
it": the person-level per-cycle similarity record appended into .todo-eval, the
jump/continuous/sustained verdict ladder, the watch-not-punish discipline (no
违规/失实 verdicts, no 承接 penalty, no data = no judgment), the truthfulness
skill exit pointer that now points at the implementation, and the AGENTS.md rows.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

AUDIT = "company-todo-audit"
TRUTH = "todo-truthfulness-check"


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Return (frontmatter dict, body). Only top-level ``key: value`` lines are parsed."""
    assert text.startswith("---\n"), "SKILL.md must start with a YAML frontmatter fence"
    end = text.index("\n---", 4)
    fm_block = text[4:end]
    body = text[end + 4 :]
    fm: dict[str, str] = {}
    for line in fm_block.splitlines():
        if not line or line[0] in " \t":
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


def _skill_text(name: str) -> str:
    return (SKILLS_DIR / name / "SKILL.md").read_text(encoding="utf-8")


def _body(name: str) -> str:
    return _split_frontmatter(_skill_text(name))[1]


def test_audit_skill_has_slope_consumer_section() -> None:
    fm, body = _split_frontmatter(_skill_text(AUDIT))
    assert fm.get("name") == AUDIT
    assert "动态防滑坡消费侧" in fm.get("description", ""), "frontmatter must advertise the slope-watch consumer"
    assert "动态防滑坡" in body, "audit must carry the dynamic slope-watch section"
    assert "消费侧" in body, "the slope section must be the consuming side"
    assert "feishu_text_similarity" in body, "the consumer must name the similarity tool"


def test_slope_record_appends_person_level_similarity() -> None:
    body = _body(AUDIT)
    assert "逐期追加" in body, "similarity must accumulate period over period"
    assert "person 级" in body, "the record is person-level, not item-level"
    assert "item_type=similarity" in body, "the record must carry its own item_type"
    assert "item" in body and "留空" in body, "item stays empty on person-level rows"
    assert "幂等" in body, "the append must stay idempotent per (cycle, person, item)"
    assert "不同步飞书" in body, "person-level rows stay local to .todo-eval"
    assert "评测记录" in body, "the exclusion names the Feishu eval bitable"
    for verdict in ("无需关注", "疑似自复制", "连续自复制", "防滑坡"):
        assert verdict in body, f"verdict ladder must include {verdict}"


def test_slope_jump_rule_is_a_ladder() -> None:
    body = _body(AUDIT)
    assert "跳升" in body, "the rule is about a similarity jump, not raw similarity"
    assert "阈值" in body, "the rule must name the threshold"
    assert "上一条" in body, "judgment compares against the person's own previous pair"
    assert "基线" in body, "first-ever pair is baseline only"
    assert "私聊" in body and "提醒" in body, "level 1 is a private reminder"
    assert "需关注" in body, "level 2 lands in the mentor report block"
    assert "关注名单" in body, "level 3 is an evidence-only watchlist"
    assert "回落" in body, "a drop back under threshold clears the streak"


def test_slope_watch_is_not_a_verdict() -> None:
    body = _body(AUDIT)
    assert "关注 ≠ 违规 ≠ 失实" in body, "watch is explicitly not a violation"
    assert "不改五要素闭环结论" in body, "closure findings stay untouched"
    assert "红线定性留人工" in body, "red-line naming stays with humans"
    assert "先软后硬" in body, "escalation stays soft first"
    assert "不写" in body and "结论词" in body, "watchlist carries evidence, not labels"


def test_slope_no_data_no_judgment_and_carryover_exempt() -> None:
    body = _body(AUDIT)
    assert "无基线" in body and "待积累" in body, "missing history means accumulate, not judge"
    assert "查询失败" in body, "read failures must be reported, not waved away"
    assert "承接项" in body, "carry-over items must be excluded before comparing"
    assert "剔除" in body, "carry-over items are subtracted from the compared text"
    assert "无法判定" in body, "too little remaining text means cannot judge"
    assert "不判" in body or "不顺势关注" in body, "never force a watch without data"


def test_slope_granularity_matches_static_copy_exit() -> None:
    body = _body(AUDIT)
    assert "TODO 层" in body, "comparison granularity stops at the TODO layer"
    assert "大目标" in body and "不参与比对" in body, "stable goals must not be compared"
    assert "0.85" in body and "threshold" in body, "threshold default must be stated"
    assert "matched_fragment" in body, "hits must carry the matched fragment as evidence"
    assert "原文出处" in body, "evidence must cite where the two texts came from"


def test_truthfulness_exit_pointer_now_targets_implementation() -> None:
    body = _body(TRUTH)
    assert "动态抓滑坡" in body, "truthfulness must keep naming the dynamic exit"
    assert "消费侧已实现在" in body, "the exit must point at the audit consumer"
    assert AUDIT in body, "the pointer must name company-todo-audit"
    assert "两个出口" in body, "the tool has two exits: static copy, dynamic slope"
    assert "后续迭代" not in body and "本文不实现" not in body, (
        "the exit note must no longer claim the dynamic exit is unimplemented"
    )


def test_only_references_real_tools() -> None:
    real = _public_tool_names()
    assert "feishu_text_similarity" in real

    referenced = set(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", _skill_text(AUDIT)))
    # feishu_task_get is named in the skill as *the tool that does NOT exist*; it is not a reference.
    non_tools = {"feishu_context", "feishu_task_get"}
    concrete = {n for n in referenced if not n.endswith("_") and n not in non_tools}
    unknown = concrete - real
    assert not unknown, f"audit skill references tool names that don't exist: {sorted(unknown)}"


def test_indexed_in_agents_md() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{AUDIT}`" in agents, f"{AUDIT} must be listed in the AGENTS.md skills index"
    tool_line = next(
        (line for line in agents.splitlines() if "确定性文本相似度" in line and "feishu_text_similarity" in line),
        "",
    )
    assert tool_line, "AGENTS.md must describe feishu_text_similarity"
    assert "动态防滑坡消费" in tool_line, "the tool row must mention the dynamic consumer"
    assert "属后续迭代" not in tool_line, "the tool row must not defer the consumer anymore"
    index_line = next(
        (line for line in agents.splitlines() if f"`{AUDIT}`" in line and "闭环判定与回流" in line),
        "",
    )
    assert "防滑坡消费侧" in index_line, "the audit index row must mention the consumer"
