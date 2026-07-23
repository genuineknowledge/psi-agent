"""Validate the admin-finance skill set (governance + leave + reimbursement + assistant).

These four skills are pure SKILL.md recipes (no new Python tools). The tests guard
what can silently rot: the frontmatter must be well-formed (name matches the dir,
description present), and every ``feishu_*`` / ``wiki_*`` tool name the skills tell
the agent to call must actually exist as a public tool function — so a typo in a
skill can't send the agent after a non-existent tool.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = WORKSPACE_ROOT / "skills"
TOOLS_DIR = WORKSPACE_ROOT / "tools"

NEW_SKILLS = [
    "admin-finance-governance",
    "feishu-leave-audit-board",
    "feishu-reimbursement-audit-report",
    "feishu-admin-finance-assistant",
]


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


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_skill_file_exists(skill: str) -> None:
    assert (SKILLS_DIR / skill / "SKILL.md").is_file(), f"missing skills/{skill}/SKILL.md"


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_frontmatter_name_matches_dir_and_has_description(skill: str) -> None:
    fm, body = _split_frontmatter((SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8"))
    assert fm.get("name") == skill, f"frontmatter name must equal dir name for {skill}"
    assert fm.get("description", "").strip(), f"{skill} needs a non-empty description"
    assert fm.get("category", "").strip(), f"{skill} needs a category"
    assert body.strip(), f"{skill} needs a non-empty body"


def test_skills_only_reference_real_tools() -> None:
    real = _public_tool_names()
    # sanity: the collector actually found the toolset
    assert "feishu_approval_decide" in real
    assert "wiki_search" in real

    referenced: set[str] = set()
    for skill in NEW_SKILLS:
        text = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        referenced.update(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", text))

    # Drop wildcard/family mentions (e.g. feishu_bitable_*, feishu_wiki_*, feishu_chat_find*)
    # and known non-tool tokens that share the prefix (feishu_context is the context block,
    # not a tool).
    non_tools = {"feishu_context"}
    concrete = {
        n
        for n in referenced
        if not n.endswith("_")  # trailing-underscore stubs from feishu_* / wiki_* wildcards
        and n not in non_tools
    }
    unknown = concrete - real
    assert not unknown, f"skills reference tool names that don't exist: {sorted(unknown)}"
