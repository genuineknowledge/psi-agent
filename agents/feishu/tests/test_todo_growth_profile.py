"""Validate the todo-growth-profile skill (dynamic layer 2: growth briefs).

Growth is not a score or a verdict: it is an evidence-backed portrait drawn from the
accumulated cycle stores (wiki snapshots, per-cycle mentor ledgers, .todo-eval records),
person vs self, never person vs person. These tests guard the parts whose removal would
silently turn it back into a vague "he's improving" chat: the person-vs-self-only rule,
the no-invented-scores rule, the traceability requirement on every indicator, the four
authoritative data sources, the sample-size floor, and the config growth section.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

SKILL = "todo-growth-profile"

INDICATORS = (
    "闭环率",
    "按时率",
    "回流次数",
    "持续逾期段",
    "mentor打分趋势",
    "承担层级迁移",
    "外部成果累计",
)


def _split_frontmatter(text: str) -> tuple[dict[str, str], str]:
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
    assert fm.get("category") == "knowledge-base", f"{SKILL} needs category knowledge-base"
    assert body.strip(), f"{SKILL} needs a non-empty body"


def test_description_carries_the_trigger_phrases() -> None:
    """Discovery is the whole game: an unloaded rulebook enforces nothing."""
    description = _split_frontmatter(_skill_text())[0]["description"]
    for phrase in ("成长简报", "个人成长", "LOAD"):
        assert phrase in description, f"description must contain the trigger phrase {phrase}"


def test_portrait_is_person_vs_self_only() -> None:
    """Growth is a self-comparison portrait; peer comparison is P3 and forbidden here."""
    body = _body()
    assert "人 vs 自己" in body, "the portrait must compare person to themselves only"
    assert "严禁人 vs 人" in body or "不做人 vs 人" in body, "must forbid person-vs-person"
    assert "P3" in body, "must defer scale-up / peer comparison to P3"


def test_no_invented_scores_or_ratings() -> None:
    body = _body()
    assert "不发明" in body, "must forbid inventing scores"
    assert "分数" in body and "评级" in body, "must explicitly forbid scores and ratings"
    assert "百分制" in body, "must forbid percentage grades"


def test_every_indicator_is_listed() -> None:
    body = _body()
    for indicator in INDICATORS:
        assert indicator in body, f"missing indicator {indicator}"


def test_indicators_must_be_traceable() -> None:
    """An indicator without a traceable row/snapshot/record is a vague claim."""
    body = _body()
    assert "可回溯" in body, "every indicator must be traceable to a concrete store"
    assert "证据引用" in body, "every observation must cite its evidence"


def test_data_sources_are_the_existing_stores() -> None:
    """No invented tables: growth reads the cycle stores the todo system already keeps."""
    body = _body()
    assert ".todo-eval" in body, "must read the .todo-eval continuity sequence"
    assert "wiki" in body and "快照" in body, "must read the wiki snapshot chain"
    assert "台账" in body, "must read the mentor ledgers"


def test_sample_size_floor_is_stated() -> None:
    body = _body()
    assert "样本不足" in body, "too few cycles must be reported, not papered over"
    assert "min_cycles" in body, "the floor must come from config growth.min_cycles"


def test_judgment_criteria_live_in_config() -> None:
    body = _body()
    assert "config/todo-sop.yaml" in body, "must point the criteria at config/todo-sop.yaml"
    assert "growth" in body, "must point at the config growth section"


def test_only_references_real_tools() -> None:
    real = _public_tool_names()
    # sanity: the collector actually found the toolset
    assert "feishu_bitable_search_records" in real
    assert "wiki_read" in real

    referenced = set(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", _skill_text()))
    non_tools = {"feishu_context"}
    concrete = {n for n in referenced if not n.endswith("_") and n not in non_tools}
    unknown = concrete - real
    assert not unknown, f"skill references tool names that don't exist: {sorted(unknown)}"


def test_indexed_in_agents_md() -> None:
    agents = (WORKSPACE_ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert f"`{SKILL}`" in agents, f"{SKILL} must be listed in the AGENTS.md skills index"
