"""Guard against ghost tool names in the runtime-facing desktop (ToC) docs.

Mirror of ``agents/feishu/tests/test_feishu_ghost_tools.py`` for the desktop
agent; keep the two files in sync when a rule changes.  A ghost tool is a
name written as if it were a real, callable tool that has no matching
top-level ``async def`` in ``tools/*.py``.

Why desktop needs its own guard:

- The two workspaces share most skills, and a feishu skill synced into the
  desktop agent carries feishu-only tool teaching with it.  Calling any of
  those from the desktop agent fails with "tool does not exist" and burns
  rounds on "not found" retries until the turn hits ``max_tool_rounds``.
- The never-existed / #612-deleted families are project-wide ghosts: they
  must not be taught by *either* agent, feishu or desktop.

Rules applied to every runtime-facing doc (AGENTS.md, TOOLS.md, and every
skill document under ``skills/``):

- NEVER_EXISTED / MIGRATED_AWAY names are banned outright;
- a real feishu-only tool (a tool that exists only in the feishu workspace)
  may never be named as a tool in desktop docs;
- every other feishu_/wiki_ mention must resolve against the desktop real
  surface, or be a documented non-tool (``DOC_ONLY``), or read as a family
  reference (globs ending in ``_``, labels that prefix a real tool, or
  tool-table row headers of the form `` `name` (``name.py`` ...) ``).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
FEISHU_ROOT = Path(__file__).resolve().parents[2] / "feishu"
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
# (feishu-message / feishu-chat / feishu-contact / feishu-api / ...).
# Verified with git log -S "async def <name>(": the def existed, then was
# removed. These names must not be taught as callable tools anywhere; their
# capabilities are reached through feishu_api + the endpoint tables.
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

# feishu_/wiki_ identifiers the docs deliberately name as *non-tools*
# (event tags, card action tags, routing/context hints, "there is no X"
# statements, state/block markers). Each entry needs a reason.
DOC_ONLY = {
    "feishu_approval_event": "approval event tag (routing hint)",
    "feishu_auth_granted": "<feishu_auth_granted> card block carried on an auth resume turn",
    "feishu_card_action": "card action tag / callback payload",
    "feishu_card_action_batch": "card batch-action callback payload",
    "feishu_context": "routing/context hint, not a tool",
    "feishu_task_create": "negative teaching: task domain migrated to endpoints, no such tool",
    "feishu_task_get": "negative teaching: task domain migrated to endpoints, no such tool",
}

# Directories whose *.md files are skill internals (generated code,
# vendored bundles, schemas of sub-packages), not runtime-loaded guidance.
_SKIP_DIRS = {
    "examples",
    "fusion_flow",
    "generated",
    "grammar",
    "node_modules",
    "__pycache__",
    "runtime",
}

_TOKEN_RE = re.compile(r"\b(feishu_[a-z_]+|wiki_[a-z_]+)\b")


def _tool_names(tools_dir: Path) -> set[str]:
    """Public async tool function names (feishu_*/wiki_*) from tools/*.py via AST."""
    names: set[str] = set()
    for py in tools_dir.glob("*.py"):
        if py.name.startswith("_"):
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and (
                node.name.startswith("feishu_") or node.name.startswith("wiki_")
            ):
                names.add(node.name)
    return names


def _runtime_docs() -> list[Path]:
    """AGENTS.md, TOOLS.md, and every skill document a model may be shown."""
    docs = [WORKSPACE_ROOT / name for name in ("AGENTS.md", "TOOLS.md")]
    for p in (WORKSPACE_ROOT / "skills").rglob("*.md"):
        if not any(part in _SKIP_DIRS for part in p.relative_to(WORKSPACE_ROOT).parts):
            docs.append(p)
    return docs


def _tool_mentions(text: str) -> set[str]:
    """Extract feishu_*/wiki_* identifiers referenced from prose."""
    return set(_TOKEN_RE.findall(text))


def _is_family_glob(token: str) -> bool:
    """Glob-style family reference: ``feishu_doc_`` / ``feishu_*`` style tokens."""
    return token.endswith("_")


def _prefixes_a_real_tool(token: str, real: set[str]) -> bool:
    """Category/module label naming a family of real tools (``feishu_auth`` -> ``feishu_auth_*``)."""
    return any(name.startswith(token) and name != token for name in real)


def _is_module_header(token: str, text: str) -> bool:
    """Row header `` `name` (``name.py`` ...) `` of the AGENTS.md tool table."""
    return re.search(rf"`{re.escape(token)}`\s*\(`{re.escape(token)}\.py`", text) is not None


def _unresolved_mentions(text: str, real: set[str], extra_banned: set[str]) -> dict[str, str]:
    """Map each doc mention that fails every allowance rule to its reason."""
    unresolved: dict[str, str] = {}
    for token in sorted(_tool_mentions(text)):
        if token in NEVER_EXISTED:
            unresolved[token] = "never existed; banned everywhere"
        elif token in MIGRATED_AWAY:
            unresolved[token] = "deleted by #612 (endpoint tables); banned everywhere"
        elif token in extra_banned:
            unresolved[token] = "exists only in the feishu workspace; not callable here"
        elif (
            token in real
            or token in DOC_ONLY
            or _is_family_glob(token)
            or _prefixes_a_real_tool(token, real)
            or _is_module_header(token, text)
        ):
            continue
        else:
            unresolved[token] = "no matching tool definition"
    return unresolved


def test_never_existed_ghosts_do_not_appear_in_docs() -> None:
    """The never-existed ghosts must never appear in any desktop doc again."""
    real = _tool_names(TOOLS_DIR)
    for doc in _runtime_docs():
        unresolved = _unresolved_mentions(doc.read_text(encoding="utf-8"), real, set())
        ghost = {t: why for t, why in unresolved.items() if t in NEVER_EXISTED}
        assert not ghost, f"{doc.relative_to(WORKSPACE_ROOT)} mentions ghost tool(s) that never existed: {ghost}"


def test_migrated_away_tools_are_not_taught_as_callable() -> None:
    """#612-deleted tools must not be written as callable tools anywhere."""
    real = _tool_names(TOOLS_DIR)
    for doc in _runtime_docs():
        unresolved = _unresolved_mentions(doc.read_text(encoding="utf-8"), real, set())
        migrated = {t: why for t, why in unresolved.items() if t in MIGRATED_AWAY}
        assert not migrated, f"{doc.relative_to(WORKSPACE_ROOT)} teaches #612-deleted tool(s): {migrated}"


def test_desktop_docs_never_teach_feishu_only_tools() -> None:
    """A real feishu tool must not be named as a tool in desktop docs.

    Desktop and feishu share most skills; a feishu-only name copied into a
    desktop doc would send the ToC agent to call a tool that does not exist
    in its workspace.
    """
    real = _tool_names(TOOLS_DIR)
    feishu_only = _tool_names(FEISHU_ROOT / "tools") - real
    for doc in _runtime_docs():
        unresolved = _unresolved_mentions(doc.read_text(encoding="utf-8"), real, feishu_only)
        leaked = {t: why for t, why in unresolved.items() if t in feishu_only}
        assert not leaked, f"{doc.relative_to(WORKSPACE_ROOT)} names feishu-only tool(s): {leaked}"


def test_all_runtime_docs_only_reference_real_tools() -> None:
    """Every feishu_/wiki_ name any desktop doc treats as a tool must exist."""
    real = _tool_names(TOOLS_DIR)
    feishu_only = _tool_names(FEISHU_ROOT / "tools") - real
    for doc in _runtime_docs():
        unresolved = _unresolved_mentions(doc.read_text(encoding="utf-8"), real, feishu_only)
        assert not unresolved, f"{doc.relative_to(WORKSPACE_ROOT)} names tool(s) with no definition: {unresolved}"
