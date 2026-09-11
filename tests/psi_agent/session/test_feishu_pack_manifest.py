"""The feishu pack's exposure manifest, checked against the tools it names.

The manifest replaces a hardcoded kernel list, and the migration itself found
what a list drifting from its pack looks like: two of the 64 names
(``feishu_message_list``, ``feishu_permission_list_members``) had no tool behind
them at all — deleted by #612, still listed months later, exposed as function
definitions the model could call and always fail (see
``agents/feishu/tests/test_feishu_ghost_tools.py``). The list could not catch that
because it lived a directory away from the thing it described. Now that it ships
beside the tools, this criterion is what keeps them agreeing.

Names are matched by parsing ``tools/*.py`` rather than by loading them: loading
the feishu pack means importing 201 files with their third-party dependencies,
which is neither available nor desirable in a unit run. The cost is one blind
spot, handled explicitly below — tools generated at import time by ``@mcp`` have
no ``async def`` to find.
"""

from __future__ import annotations

import ast
from pathlib import Path

from psi_agent.session.tool_exposure import DISCOVERY_TOOLS, MANIFEST_NAME, parse_manifest_text

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FEISHU_TOOLS = _REPO_ROOT / "agents" / "feishu" / "tools"

# Tools that exist only after their module runs: ``@mcp(keep=(...))`` generates a
# function per kept upstream tool, so no ``async def`` of this name is in the
# source. Listed with the file that generates it so the pairing stays checkable
# by hand — and asserted below to still be generated there, which is the part a
# stale entry here would otherwise hide.
_GENERATED_AT_IMPORT = {"serper_google_search": "search.py"}


def _declared() -> frozenset[str]:
    return parse_manifest_text((_FEISHU_TOOLS / MANIFEST_NAME).read_text(encoding="utf-8"))


def _async_def_names() -> set[str]:
    """Top-level ``async def`` names across the pack's non-private tool files."""
    names: set[str] = set()
    for path in sorted(_FEISHU_TOOLS.glob("*.py")):
        if path.name.startswith("_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.AsyncFunctionDef) and not node.name.startswith("_"):
                names.add(node.name)
    return names


def test_every_declared_name_has_a_tool_behind_it() -> None:
    """No ghosts. A declared name with nothing behind it is worse than an omission:
    the model is handed a callable definition that fails on every call."""
    real = _async_def_names() | set(_GENERATED_AT_IMPORT)
    ghosts = sorted(_declared() - real)
    assert ghosts == [], f"清单里的名字在 tools/*.py 里找不到: {ghosts}"


def test_the_import_time_generated_names_are_still_generated() -> None:
    """The blind spot above, pinned. ``_GENERATED_AT_IMPORT`` is an exemption from
    the ghost check, so an entry that stopped being generated would silently
    re-open the hole the check exists to close."""
    for name, filename in _GENERATED_AT_IMPORT.items():
        source = (_FEISHU_TOOLS / filename).read_text(encoding="utf-8")
        assert name in source, f"{name} 不再由 {filename} 生成, 该从豁免表里删掉"


def test_the_manifest_declares_far_less_than_the_pack_ships() -> None:
    """The manifest is a narrowing, and stops being one if it grows to cover the
    pack. A ratio rather than a count so tools can be added on either side."""
    declared, shipped = len(_declared()), len(_async_def_names())
    assert shipped > 150, f"pack 只有 {shipped} 个工具, 判据的前提不成立了"
    assert declared < shipped * 0.5, f"清单已覆盖 {declared}/{shipped}, 不再是收窄"


def test_the_manifest_covers_the_discovery_escape_hatch() -> None:
    """The kernel exposes these unconditionally, so this is belt-and-braces — but
    the pack teaching them in its own manifest is what makes the file readable as
    "what the model sees" without having to know the kernel's exception."""
    assert _declared() >= DISCOVERY_TOOLS
