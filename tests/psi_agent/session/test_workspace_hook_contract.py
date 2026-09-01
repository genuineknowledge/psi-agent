"""B5 — the 6 workspace hooks are matched by *name*, so a typo is silent.

``SystemPrompt._load_module`` resolves each hook with
``getattr(module, name, None)`` and ``agent.py`` only warns about the one
missing ``compact_history``; the other five vanish without a log line. Rename a
hook (or move a workspace so its sibling imports break) and the kernel keeps
running with a default — no error, just a quietly different agent.

So the contract is pinned as a table: for all 12 workspaces x 6 hooks, whether
the kernel resolves the hook is asserted cell by cell. Both directions fail —
a hook that disappears *and* a hook that appears where none was expected — which
is what makes this a net for renames rather than a lower bound.

Not every workspace exposes all 6, by design (see ``SystemPrompt`` docstring:
builder / checker / before / after have kernel defaults, and ``None`` is the
meaningful "this workspace has no volatile block" signal for
``turn_context_fn``). Asserting "all 6 non-None everywhere" would encode a
contract the kernel does not have. What *is* required of every workspace is
asserted separately below: the module must load, and it must resolve a builder
and a ``compact_history``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from psi_agent.session.system_prompt import SystemPrompt

WORKSPACES = sorted([*Path("examples").glob("*/systems/system.py"), *Path("agents").glob("*/systems/system.py")])

# The kernel's lookup order in ``_load_module``.
HOOKS = (
    "system_prompt_builder",
    "system_prompt_rebuild_checker",
    "compact_history",
    "turn_context_builder",
    "system_before_turn",
    "system_after_turn",
)

# Hooks every workspace must resolve regardless of its feature set: without a
# builder the agent gets an empty system prompt, without ``compact_history``
# compaction is skipped and the history grows until the request is rejected.
REQUIRED_HOOKS = ("system_prompt_builder", "compact_history")

# Measured on this tree, one row per workspace. Update deliberately when a
# workspace gains or drops a hook — an accidental change must fail here.
EXPECTED: dict[str, tuple[str, ...]] = {
    "a-serper-mcp-workspace": ("system_prompt_builder", "compact_history"),
    "a-simple-bash-only-workspace": ("system_prompt_builder", "compact_history"),
    "a-simple-schedule-workspace": ("system_prompt_builder", "compact_history"),
    "a-simple-windows-workspace": ("system_prompt_builder", "compact_history"),
    "fusion-flow-workspace": ("system_prompt_builder", "compact_history"),
    "fusion-haven-workspace": ("system_prompt_builder", "compact_history"),
    "fusion-memory-workspace": (
        "system_prompt_builder",
        "system_prompt_rebuild_checker",
        "compact_history",
    ),
    "haitun-supervisor-workspace": (
        "system_prompt_builder",
        "system_prompt_rebuild_checker",
        "compact_history",
    ),
    "hermes-style-workspace": ("system_prompt_builder", "compact_history"),
    "openclaw-style-workspace": (
        "system_prompt_builder",
        "compact_history",
        "turn_context_builder",
    ),
    "tb2-specific-workspace": ("system_prompt_builder", "compact_history"),
    "feishu": HOOKS,
    # ``desktop`` is ``feishu`` minus the Feishu-coupled capabilities, so its
    # ``systems/`` is byte-identical apart from three prompt-text edits — all 6
    # hooks must resolve there too. A drift to fewer hooks means the extraction
    # broke a sibling import (``prompt_sections`` / ``curator`` / …), which is
    # exactly the silent failure this table exists to catch.
    "desktop": HOOKS,
}

# Sibling helpers the workspaces import by bare name. Several ship their own
# copy, so a cached entry from one workspace satisfies the next one's import and
# loads the wrong file — which is itself one of the silent failures this guards:
# ``agents/feishu`` resolves 0 of 6 hooks if ``openclaw``'s ``prompt_sections``
# is left in ``sys.modules``.
_SIBLING_MODULES = (
    "prompt_sections",
    "prompt_texts",
    "tool_docs",
    "prompt_constants",
    "curator",
    "supervisor",
    "supervisor_protocol",
    "supervisor_store",
    "threat_patterns",
    "background_review",
)


async def _resolved_hooks(system_py: Path) -> tuple[str, ...]:
    """Hook names the kernel actually binds for the workspace at *system_py*.

    Loading several workspaces in one process needs both halves of the isolation:
    the module cache *and* ``sys.path``. Each ``system.py`` puts its own
    ``systems/`` dir on ``sys.path`` and never takes it off, so without the
    snapshot the workspace loaded first shadows every later one's bare sibling
    imports. In production only one workspace is loaded per process, so this is
    harness bookkeeping — but getting it wrong turns this net into a coin flip
    on collection order.
    """
    saved_modules = {k: sys.modules.pop(k) for k in _SIBLING_MODULES if k in sys.modules}
    saved_path = list(sys.path)
    sys.path.insert(0, str(system_py.parent))
    try:
        found = await SystemPrompt._load_module(system_py.parent.parent, "hook-contract")
    finally:
        sys.path[:] = saved_path
        for k in _SIBLING_MODULES:
            sys.modules.pop(k, None)
        sys.modules.update(saved_modules)
    return tuple(name for name, func in zip(HOOKS, found, strict=True) if func is not None)


def test_every_workspace_is_covered() -> None:
    """The glob must not quietly stop matching — B2 lost 10 tests that way."""
    assert len(WORKSPACES) == 13
    assert {p.parent.parent.name for p in WORKSPACES} == set(EXPECTED)


@pytest.mark.anyio
@pytest.mark.parametrize("system_py", WORKSPACES, ids=lambda p: p.parent.parent.name)
async def test_resolved_hooks_match_the_pinned_contract(system_py: Path) -> None:
    """All 6 hooks asserted per workspace: present ones and absent ones both."""
    name = system_py.parent.parent.name
    resolved = set(await _resolved_hooks(system_py))
    expected = set(EXPECTED[name])

    assert resolved - expected == set(), f"{name}: hook appeared, update EXPECTED if intended"
    assert expected - resolved == set(), f"{name}: hook no longer resolves (renamed? import broken?)"


@pytest.mark.anyio
@pytest.mark.parametrize("system_py", WORKSPACES, ids=lambda p: p.parent.parent.name)
async def test_required_hooks_resolve(system_py: Path) -> None:
    """A workspace whose module fails to execute resolves *nothing* — the
    kernel returns 6 ``None``s for a broken import exactly as it does for an
    empty module, so this is what catches a botched move."""
    resolved = await _resolved_hooks(system_py)
    missing = [h for h in REQUIRED_HOOKS if h not in resolved]
    assert not missing, f"{system_py.parent.parent.name}: missing {missing}"
