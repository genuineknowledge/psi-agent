"""B6 — the agent package root is received, not derived from ``__file__``.

Priority is ``agent_raw`` (kernel-supplied) → ``get_agent()`` (per-turn
ContextVar) → ``__file__`` (last-resort compat), matching
``tools/_runtime_paths.agent_dir``. Without the first two, moving files out of
the package root (B3 moves ``SOUL.md`` / ``USER.md``) silently repoints every
root this module computes.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from psi_agent.session.runtime_context import path_scope

AGENT_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = AGENT_ROOT / "systems"


def _load_system(module_name: str):
    path = SYSTEMS / "system.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    sys.path.insert(0, str(SYSTEMS))
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == str(SYSTEMS):
            sys.path.pop(0)
    return module


@pytest.fixture
def system(request: pytest.FixtureRequest):
    name = f"haitun_system_agent_root_{id(request)}"
    module = _load_system(name)
    yield module
    sys.modules.pop(name, None)


def test_resolve_agent_priority(system, tmp_path: Path) -> None:
    explicit = tmp_path / "explicit"
    ctxvar = tmp_path / "ctxvar"

    assert str(system._resolve_agent(str(explicit))) == str(explicit)
    with path_scope(workspace="", agent=str(ctxvar)):
        assert str(system._resolve_agent()) == str(ctxvar)
        # An explicit root outranks the ContextVar.
        assert str(system._resolve_agent(str(explicit))) == str(explicit)
    # Nothing bound → package fallback, which is this package.
    assert str(system._resolve_agent()) == str(AGENT_ROOT)


def test_workspace_falls_back_to_injected_agent_root(system, tmp_path: Path) -> None:
    """No user workspace bound → single-root compat uses the *injected* agent root."""
    relocated = tmp_path / "relocated"
    relocated.mkdir()
    resolved = str(system._resolve_workspace("", str(relocated)))
    assert resolved == str(Path(relocated).resolve())


@pytest.mark.anyio
async def test_prompt_reads_soul_from_injected_root(system, tmp_path: Path) -> None:
    """The prompt must follow the injected root, not this file's location."""
    relocated = tmp_path / "relocated-agent"
    relocated.mkdir()
    marker = "RELOCATED-SOUL-MARKER-B6"
    (relocated / "SOUL.md").write_text(f"# {marker}", encoding="utf-8")

    injected = await system.system_prompt_builder(agent_raw=str(relocated))
    assert marker in injected

    # Same call without the injected root cannot see it — that is the bug B6 fixes.
    assert marker not in await system.system_prompt_builder()


@pytest.mark.anyio
async def test_hooks_accept_agent_raw(system, tmp_path: Path) -> None:
    relocated = tmp_path / "relocated-hooks"
    relocated.mkdir()
    assert await system.system_prompt_rebuild_checker(agent_raw=str(relocated)) is True
    assert isinstance(await system.turn_context_builder(agent_raw=str(relocated)), str)
