"""B6 — the kernel tells workspace hooks their package root (``agent_raw``).

A workspace module that derives its own root from ``__file__`` silently follows
the file when the package is re-laid-out, and the kernel has no way to correct
it. ``SystemPrompt`` therefore passes down the root it loaded the module from,
to every hook that declares ``agent_raw``. Hooks that do not declare it must
keep being called exactly as before.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import pytest

from psi_agent.session.conversation import Conversation
from psi_agent.session.system_prompt import SystemPrompt

AGENT_ROOT = Path("X:/relocated/agent-package")

HOOKS_SRC = """
recorded: dict[str, str] = {}


async def system_prompt_builder(user_message=None, *, agent_raw: str = "") -> str:
    recorded["builder"] = agent_raw
    return "SYS"


async def system_prompt_rebuild_checker(_user_message=None, *, agent_raw: str = "") -> bool:
    recorded["checker"] = agent_raw
    return True


async def turn_context_builder(*, agent_raw: str = "") -> str:
    recorded["turn_context"] = agent_raw
    return "TC"


async def system_before_turn(user_message, *, agent_raw: str = "") -> dict:
    recorded["before_turn"] = agent_raw
    return {}


async def system_after_turn(user_message, assistant_message, *, agent_raw: str = "") -> None:
    recorded["after_turn"] = agent_raw
"""

LEGACY_SRC = """
calls: list[tuple] = []


async def system_prompt_builder() -> str:
    calls.append(())
    return "LEGACY"


async def turn_context_builder() -> str:
    return "LEGACY_TC"
"""


class _Conv(Conversation):
    """真 ``Conversation``, 只补一个读 system 的快捷方式。

    原先是个独立的替身类, 但 ``ensure()`` 收的是 ``Conversation``, 替身要么骗过类型检查
    要么被报不兼容; 而 ``path=None`` 的 ``Conversation`` 本身就不落盘, 没有需要替掉的
    东西。
    """

    def __init__(self, messages: list[dict[str, Any]]) -> None:
        super().__init__(messages=messages)

    @property
    def system(self) -> str | None:
        if not self.messages or self.messages[0].get("role") != "system":
            return None
        return str(self.messages[0]["content"])


def _hook_globals(sp: SystemPrompt, name: str) -> Any:
    """读 hook 所在模块的全局变量 —— 判据靠它看 hook 记下了什么。

    ``_builder`` 的声明类型是 ``Callable[..., Any]``, 协议类型上没有 ``__globals__``;
    运行时它恒是动态加载模块里的一个真函数。抑制注释集中在这一处, 而不是散在每个用例里。
    """
    return sp._builder.__globals__[name]  # ty: ignore[unresolved-attribute]


async def _workspace(root: Path, source: str) -> Path:
    systems = root / "systems"
    await anyio.Path(systems).mkdir(parents=True)
    await anyio.Path(systems / "system.py").write_text(source, encoding="utf-8")
    return root


@pytest.mark.anyio
async def test_every_hook_receives_the_loaded_agent_root(tmp_path: Path) -> None:
    ws = await _workspace(tmp_path / "pkg", HOOKS_SRC)
    sp = await SystemPrompt.from_workspace(ws, "sid-hooks")

    await sp.ensure(_Conv([]))  # empty history → builder
    await sp.ensure(_Conv([{"role": "user", "content": "hi"}]))  # → checker + builder
    await sp.turn_context()
    await sp.run_before_turn({"content": "hi"})
    await sp.run_after_turn({"content": "u"}, {"content": "a"})

    module = _hook_globals(sp, "recorded")
    assert set(module) == {"builder", "checker", "turn_context", "before_turn", "after_turn"}
    assert set(module.values()) == {str(ws)}


@pytest.mark.anyio
async def test_injected_root_wins_over_module_file(tmp_path: Path) -> None:
    """``agent_raw`` reports the root the kernel loaded, not the module's own path."""
    ws = await _workspace(tmp_path / "pkg", HOOKS_SRC)
    sp = SystemPrompt(
        builder=(await SystemPrompt.from_workspace(ws, "sid-x"))._builder,
        agent_path=AGENT_ROOT,
    )
    await sp.ensure(_Conv([]))
    assert _hook_globals(sp, "recorded")["builder"] == str(AGENT_ROOT)


@pytest.mark.anyio
async def test_hooks_without_agent_raw_are_called_unchanged(tmp_path: Path) -> None:
    """Opt-in by parameter name — a legacy signature must not gain an argument."""
    ws = await _workspace(tmp_path / "legacy", LEGACY_SRC)
    sp = await SystemPrompt.from_workspace(ws, "sid-legacy")

    conv = _Conv([])
    await sp.ensure(conv)
    assert conv.system == "LEGACY"
    assert await sp.turn_context() == "LEGACY_TC"
    assert _hook_globals(sp, "calls") == [()]


@pytest.mark.anyio
async def test_no_agent_path_passes_nothing(tmp_path: Path) -> None:
    """Directly constructed ``SystemPrompt`` (no root known) stays argument-free."""
    seen: list[str] = []

    async def builder(*, agent_raw: str = "SENTINEL") -> str:
        seen.append(agent_raw)
        return "S"

    await SystemPrompt(builder=builder).ensure(_Conv([]))
    assert seen == ["SENTINEL"]
