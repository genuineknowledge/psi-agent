"""Validate the feishu-todo-board-sync skill (personal ToDoList docx → team board sheet).

The skill itself is a pure SKILL.md recipe; the only new Python surface is
``feishu_sheet_read`` (range read, tested in test_feishu.py). These tests guard what
can silently rot: well-formed frontmatter, every ``feishu_*`` tool name the skill
tells the agent to call actually existing, and the three non-negotiable rules
(@-name attribution, caller-supplied target column, warn-before-overwrite) still
being stated in the body — those are the requirements the skill exists to enforce.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

SKILL = "feishu-todo-board-sync"


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


def _skill_text() -> str:
    return (SKILLS_DIR / SKILL / "SKILL.md").read_text(encoding="utf-8")


def test_skill_file_exists() -> None:
    assert (SKILLS_DIR / SKILL / "SKILL.md").is_file(), f"missing skills/{SKILL}/SKILL.md"


def test_frontmatter_name_matches_dir_and_has_description() -> None:
    fm, body = _split_frontmatter(_skill_text())
    assert fm.get("name") == SKILL, "frontmatter name must equal dir name"
    assert fm.get("description", "").strip(), f"{SKILL} needs a non-empty description"
    assert fm.get("category", "").strip(), f"{SKILL} needs a category"
    assert body.strip(), f"{SKILL} needs a non-empty body"


def test_skill_only_references_real_tools() -> None:
    real = _public_tool_names()
    # sanity: the collector actually found the toolset
    assert "feishu_sheet_read" in real
    assert "feishu_sheet_write" in real

    referenced = set(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", _skill_text()))
    non_tools = {"feishu_context"}
    concrete = {n for n in referenced if not n.endswith("_") and n not in non_tools}
    unknown = concrete - real
    assert not unknown, f"skill references tool names that don't exist: {sorted(unknown)}"


def test_skill_names_the_tools_the_flow_needs() -> None:
    """The docx→sheet flow is impossible without these five; a rename must fail loudly."""
    text = _skill_text()
    for tool in (
        "feishu_wiki_get_node",  # wiki node_token → obj_token
        "feishu_doc_read",  # read the source docx
        "feishu_sheet_tabs",  # discover SHEET_ID (not derivable from the URL)
        "feishu_sheet_read",  # locate the person's row / probe the target cell
        "feishu_sheet_write",  # write the cell
    ):
        assert tool in text, f"{SKILL} must tell the agent to use {tool}"


def test_skill_discovers_structure_instead_of_hardcoding() -> None:
    """The skill must generalize past the one board it was written against."""
    body = _split_frontmatter(_skill_text())[1]
    # the sample sheet_id / tokens from the original run must be marked as examples only
    assert "别写死" in body or "现场探" in body, "must say the structure is discovered per run"
    assert "别假定人名一定在 B 列" in body, "must warn against assuming a fixed name column"
    # SHEET_ID is not in the URL — the skill has to say so, or the agent will invent one
    assert "不在 URL 里" in body


def test_skill_states_the_three_hard_rules() -> None:
    """Attribution by @name, caller-given target column, and warn-before-overwrite."""
    body = _split_frontmatter(_skill_text())[1]
    assert "@人名" in body, "must state that attribution follows @-names"
    # target column comes from the caller, never inferred from the source doc's date
    assert "调用方" in body and "列" in body, "must state the target column is caller-supplied"
    # a non-empty target cell is reported for confirmation instead of being clobbered
    assert "非空" in body, "must state what happens when the target cell is non-empty"


def test_skill_documents_both_document_types() -> None:
    """Source is docx, target is a spreadsheet — conflating them is the main failure mode."""
    body = _split_frontmatter(_skill_text())[1]
    assert "docx" in body and "sheet" in body
    # the wiki tokens in the URLs are node tokens, not document ids
    assert "node_token" in body and "obj_token" in body
