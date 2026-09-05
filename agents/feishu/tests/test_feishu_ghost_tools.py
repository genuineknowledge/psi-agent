"""Guard against ghost tool names in the runtime-facing Feishu documentation.

A ghost tool is a name written as if it were a real, callable tool that has
no matching top-level ``async def`` in ``tools/*.py``. Two families exist:

- never existed at all (``feishu_user_get``, ``feishu_contact_search``,
  ``feishu_chat_list_members``, ``feishu_message_reply``,
  ``feishu_bitable_records``) — pure doc inventions;
- existed before #612, then their domain moved to endpoint tables and the
  Python tool was deleted (``feishu_message_react``, ``feishu_chat_create``,
  ``feishu_user_manage``, ...). Calling either family from the agent fails
  with "tool does not exist".

The agent reads AGENTS.md / TOOLS.md / skill frontmatter as its tool guide,
so a ghost there sends the model to call a tool that does not exist. These
tests pin both families out of the runtime-facing docs and fail when a skill
document references a tool that is not on the real surface, instead of
letting the drift back in silently.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"

# Ghosts that never had a top-level async def in tools/*.py at any point in
# the repo history (verified with git log -S "async def <name>(").
NEVER_EXISTED = {
    "feishu_user_get",
    "feishu_contact_search",
    "feishu_chat_list_members",
    "feishu_message_reply",
    "feishu_bitable_records",
}

# Tools deleted when #612 moved their domains to SKILL endpoint tables
# (feishu-message / feishu-chat / feishu-contact / feishu-api). Verified with
# git log -S "async def <name>(": the def existed, then was removed. These
# names must not be taught as callable tools; their capabilities are reached
# through feishu_api + the endpoint tables.
MIGRATED_AWAY = {
    "feishu_calendar_create_event",
    "feishu_chat_add_members",
    "feishu_chat_create",
    "feishu_chat_dismiss",
    "feishu_chat_find",
    "feishu_chat_list",
    "feishu_chat_menu_add",
    "feishu_chat_menu_delete",
    "feishu_chat_menu_get",
    "feishu_chat_mute",
    "feishu_chat_remove_members",
    "feishu_chat_tab_add",
    "feishu_chat_tab_delete",
    "feishu_chat_tabs",
    "feishu_chat_transfer_owner",
    "feishu_chat_update",
    "feishu_department_manage",
    "feishu_message_forward",
    "feishu_message_list",
    "feishu_message_merge_forward",
    "feishu_message_pin",
    "feishu_message_pins",
    "feishu_message_react",
    "feishu_message_reactions",
    "feishu_message_recall",
    "feishu_message_unpin",
    "feishu_user_group",
    "feishu_user_manage",
}

# Every feishu_/wiki_ name a doc treats as a tool must be on the real
# surface, except identifiers the docs deliberately name as non-tools
# (routing hints, "there is no X tool" statements, event tags).
DOC_ONLY = {
    "feishu_approval_event",
    "feishu_card_action",
    "feishu_card_action_batch",
    "feishu_context",
    "feishu_task_create",
    "feishu_task_get",
}

# Skill docs are loaded whole into model context, so every feishu_/wiki_ tool
# they reference must exist. AGENTS.md/TOOLS.md are also runtime-loaded (the
# former is truncated at 40k chars, the latter fits whole), so both families
# of ghosts are banned there too.
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
    """The never-existed ghosts must never appear in any runtime-facing doc again."""
    for rel in _ALL_DOCS:
        text = (WORKSPACE_ROOT / rel).read_text(encoding="utf-8")
        mentioned = _tool_mentions(text) & NEVER_EXISTED
        assert not mentioned, f"{rel} mentions ghost tool(s) that never existed: {sorted(mentioned)}"


def test_migrated_away_tools_are_not_taught_as_callable() -> None:
    """#612-deleted tools must not be written as callable tools in the docs.

    Their capabilities are endpoint-table rows reached via feishu_api; the
    row docs (AGENTS.md tool directory + TOOLS.md scenario manual) now point
    there instead, so the old callable names may not reappear.
    """
    for rel in _ALL_DOCS:
        text = (WORKSPACE_ROOT / rel).read_text(encoding="utf-8")
        mentioned = _tool_mentions(text) & MIGRATED_AWAY
        assert not mentioned, f"{rel} teaches #612-deleted tool(s): {sorted(mentioned)}"


def test_skill_docs_only_reference_real_tools() -> None:
    """Every feishu_/wiki_ name a skill doc treats as a tool must exist in tools/.

    Names a skill explicitly describes as absent are allowed via the
    exception list.
    """
    real = _public_tool_names()
    for rel in _SKILL_DOCS:
        text = (WORKSPACE_ROOT / rel).read_text(encoding="utf-8")
        mentioned = _tool_mentions(text) - real - DOC_ONLY
        assert not mentioned, f"{rel} names tool(s) with no definition: {sorted(mentioned)}"
