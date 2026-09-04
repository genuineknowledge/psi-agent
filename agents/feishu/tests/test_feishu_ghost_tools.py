"""Guard against ghost tool names in the runtime-facing Feishu documentation.

A ghost tool is a name written as if it were a real, callable tool
(``feishu_user_get``, ``feishu_contact_search``, ``feishu_chat_list_members``,
``feishu_message_reply``, ``feishu_bitable_records``) that has no matching
top-level ``async def`` in ``tools/*.py`` and never had one. The agent reads
AGENTS.md / TOOLS.md / skill frontmatter as its tool guide, so a ghost there
sends the model to call a tool that does not exist. These tests pin the
known-never-existing ghosts and fail when a skill document references a tool
that is not on the real surface, instead of letting the drift back in
silently.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"

# The five ghosts: never had a top-level async def in tools/*.py at any point
# in the repo history (verified with git log -S "async def <name>(").
NEVER_EXISTED = {
    "feishu_user_get",
    "feishu_contact_search",
    "feishu_chat_list_members",
    "feishu_message_reply",
    "feishu_bitable_records",
}

# Skill docs are loaded whole into model context, so every feishu_/wiki_ tool
# they reference must exist. AGENTS.md/TOOLS.md are excluded here on purpose:
# their long service-tool rows still carry pre-#612 directory text naming
# tools that were deleted when their domains moved to endpoint tables (a
# separate migration-sync debt), so the strict check below applies only to the
# skill files the model reads as rules.
_SKILL_DOCS = (
    "skills/company-todo-fill-check/SKILL.md",
    "skills/work-assignment-delegation/SKILL.md",
    "skills/company-todo-audit/SKILL.md",
    "skills/todo-truthfulness-check/SKILL.md",
    "skills/todo-writing-standard/SKILL.md",
    "skills/todo-completion-standard/SKILL.md",
)

_ALL_DOCS = ("AGENTS.md", "TOOLS.md", *_SKILL_DOCS)


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


def _tool_mentions(text: str) -> set[str]:
    """Extract feishu_*/wiki_* identifiers referenced from prose."""
    return set(re.findall(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b", text))


def test_never_existed_ghosts_do_not_appear_in_docs() -> None:
    """The five ghosts must never appear in any runtime-facing doc again."""
    for rel in _ALL_DOCS:
        text = (WORKSPACE_ROOT / rel).read_text(encoding="utf-8")
        mentioned = _tool_mentions(text) & NEVER_EXISTED
        assert not mentioned, f"{rel} mentions ghost tool(s) that never existed: {sorted(mentioned)}"


def test_skill_docs_only_reference_real_tools() -> None:
    """Every feishu_/wiki_ name a skill doc treats as a tool must exist in tools/.

    Names a skill explicitly describes as absent are allowed via a short
    exception list.
    """
    real = _public_tool_names()
    # Identifiers that are deliberately named as non-tools inside the skills
    # (e.g. "there is no feishu_task_get tool", routing prefixes, event tags).
    doc_only = {
        "feishu_context",
        "feishu_task_get",
        "feishu_approval_event",
        "feishu_card_action",
        "feishu_card_action_batch",
    }
    for rel in _SKILL_DOCS:
        text = (WORKSPACE_ROOT / rel).read_text(encoding="utf-8")
        mentioned = _tool_mentions(text) - real - doc_only
        assert not mentioned, f"{rel} names tool(s) with no definition: {sorted(mentioned)}"
