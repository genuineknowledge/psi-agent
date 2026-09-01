"""``compact_history`` in the example workspaces: chaining and the summary cap.

The live compaction hook is the *module-level* ``compact_history`` (the
same-named method on ``System`` is unused prototype code).  Its ``compacted``
row is not a ``user``/``assistant`` message, so before chaining existed each
compaction silently dropped the previous summary — losing one more layer of the
conversation every time.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

WORKSPACES = sorted([*Path("examples").glob("*/systems/system.py"), *Path("agents").glob("*/systems/system.py")])


# Sibling helper modules the workspaces import by bare name.  Several workspaces
# ship their own copy, so a cached entry from one would satisfy the next one's
# import and load the wrong file (or fail on a symbol it lacks).
_SIBLING_MODULES = ("prompt_sections", "prompt_texts", "tool_docs")


def _load(path: Path) -> Any:
    name = f"sysmod_{path.parent.parent.name.replace('-', '_')}"
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    saved = {k: sys.modules.pop(k) for k in _SIBLING_MODULES if k in sys.modules}
    # Each workspace's systems/ dir must win for its own sibling imports.
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
        for k in _SIBLING_MODULES:
            sys.modules.pop(k, None)
        sys.modules.update(saved)
    return module


def _chat(n: int, prefix: str = "m") -> list[dict[str, Any]]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"{prefix}{i}"} for i in range(n)]


@pytest.fixture(params=WORKSPACES, ids=lambda p: p.parent.parent.name)
def sysmod(request: pytest.FixtureRequest) -> Any:
    return _load(request.param)


def test_constants_are_aligned(sysmod: Any) -> None:
    assert sysmod.RECENT_TURNS_KEPT_VERBATIM == 20
    assert sysmod.SUMMARY_MAX_CHARS == 8000


@pytest.mark.anyio
async def test_below_guard_returns_empty(sysmod: Any) -> None:
    """A non-empty return makes the agent write a ``compacted`` row, which drops
    every real message — so a history too short to summarize must return ""."""

    async def complete(_: list[dict[str, Any]]) -> str:
        raise AssertionError("must not call the model below the guard")

    assert await sysmod.compact_history(_chat(20), complete) == ""


@pytest.mark.anyio
async def test_first_compaction_has_no_existing_summary(sysmod: Any) -> None:
    seen: list[str] = []

    async def complete(messages: list[dict[str, Any]]) -> str:
        seen.append(messages[1]["content"])
        return "SUMMARY_1"

    out = await sysmod.compact_history(_chat(30), complete)
    assert "<existing-summary>" not in seen[0]
    assert out.startswith("SUMMARY_1")
    assert "[Recent turns]" in out


@pytest.mark.anyio
async def test_previous_summary_is_fed_back(sysmod: Any) -> None:
    seen: list[str] = []

    async def complete(messages: list[dict[str, Any]]) -> str:
        seen.append(messages[1]["content"])
        return "SUMMARY_2"

    history = [
        *_chat(4),
        {"role": "compacted", "content": "SUMMARY_1 decision A, path B", "kind": "compacted"},
        *_chat(30, "n"),
    ]
    await sysmod.compact_history(history, complete)
    assert "<existing-summary>" in seen[0]
    assert "SUMMARY_1 decision A, path B" in seen[0]


@pytest.mark.anyio
async def test_only_latest_summary_is_used(sysmod: Any) -> None:
    """Earlier summaries are already folded into the latest one; replaying them
    would re-introduce stale context."""
    seen: list[str] = []

    async def complete(messages: list[dict[str, Any]]) -> str:
        seen.append(messages[1]["content"])
        return "SUMMARY_3"

    history = [
        {"role": "compacted", "content": "STALE_SUMMARY", "kind": "compacted"},
        *_chat(4),
        {"role": "compacted", "content": "CURRENT_SUMMARY", "kind": "compacted"},
        *_chat(30, "n"),
    ]
    await sysmod.compact_history(history, complete)
    assert "CURRENT_SUMMARY" in seen[0]
    assert "STALE_SUMMARY" not in seen[0]


@pytest.mark.anyio
async def test_summary_carried_when_nothing_new_to_summarize(sysmod: Any) -> None:
    """No summarizable older messages must not mean losing the running summary."""
    called = False

    async def complete(_: list[dict[str, Any]]) -> str:
        nonlocal called
        called = True
        return "UNUSED"

    history = [
        {"role": "compacted", "content": "CARRY_ME", "kind": "compacted"},
        {"role": "tool", "tool_call_id": "1", "name": "t", "content": "tool output"},
        {"role": "system", "content": "SYS"},
        *_chat(20),
    ]
    out = await sysmod.compact_history(history, complete)
    assert "CARRY_ME" in out
    assert called is False


@pytest.mark.anyio
async def test_summary_is_capped(sysmod: Any) -> None:
    """Chained summaries grow monotonically and land in the system prompt, so an
    uncapped summary would shrink the very budget it protects."""

    async def complete(_: list[dict[str, Any]]) -> str:
        return "X" * 50_000

    out = await sysmod.compact_history(_chat(30), complete)
    assert len(out) < sysmod.SUMMARY_MAX_CHARS + 2_000
    assert "truncated" in out


@pytest.mark.anyio
async def test_model_failure_still_preserves_previous_summary(sysmod: Any) -> None:
    async def complete(_: list[dict[str, Any]]) -> str:
        raise RuntimeError("upstream down")

    history = [
        *_chat(4),
        {"role": "compacted", "content": "SURVIVOR", "kind": "compacted"},
        *_chat(30, "n"),
    ]
    out = await sysmod.compact_history(history, complete)
    assert "SURVIVOR" in out
