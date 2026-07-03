from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import anyio
import pytest

from psi_agent.session.after_turn import load_after_turn_fn, make_complete_fn
from psi_agent.session.agent import SessionAgent
from psi_agent.session.ai_client import AiClient
from psi_agent.session.protocol import AiDelta
from psi_agent.session.tool_registry import ToolRegistry


class _FakeAiClient(AiClient):
    """AiClient stand-in that replays a fixed list of ``AiDelta`` objects."""

    def __init__(self, deltas: list[AiDelta]) -> None:
        self._deltas = deltas

    async def stream(self, request_body: dict) -> Any:
        for d in self._deltas:
            yield d


# -- make_complete_fn ----------------------------------------------------------


@pytest.mark.anyio
async def test_complete_fn_aggregates_content_and_tool_calls() -> None:
    deltas = [
        AiDelta(content="Hel"),
        AiDelta(content="lo"),
        AiDelta(tool_calls=[{"index": 0, "id": "c1", "function": {"name": "skill_manage", "arguments": '{"a":'}}]),
        AiDelta(tool_calls=[{"index": 0, "function": {"arguments": "1}"}}]),
        AiDelta(finish_reason="tool_calls"),
    ]
    complete_fn = make_complete_fn(_FakeAiClient(deltas))

    result = await complete_fn([{"role": "user", "content": "hi"}], [{"type": "function"}])

    message = result["choices"][0]["message"]
    assert message["content"] == "Hello"
    assert message["tool_calls"][0]["id"] == "c1"
    assert message["tool_calls"][0]["function"]["name"] == "skill_manage"
    assert message["tool_calls"][0]["function"]["arguments"] == '{"a":1}'
    assert result["choices"][0]["finish_reason"] == "tool_calls"


# -- load_after_turn_fn --------------------------------------------------------


async def _write_system(workspace: Path, body: str) -> None:
    systems = workspace / "systems"
    await anyio.Path(systems).mkdir(parents=True, exist_ok=True)
    await anyio.Path(systems / "system.py").write_text(textwrap.dedent(body), encoding="utf-8")


@pytest.mark.anyio
async def test_load_after_turn_missing_workspace_returns_none(tmp_path: Path) -> None:
    fn = load_after_turn_fn(tmp_path, ai_client=_FakeAiClient([]), tool_executors={})
    assert fn is None


@pytest.mark.anyio
async def test_load_after_turn_no_system_class_returns_none(tmp_path: Path) -> None:
    await _write_system(tmp_path, "async def system_prompt_builder() -> str:\n    return ''\n")
    fn = load_after_turn_fn(tmp_path, ai_client=_FakeAiClient([]), tool_executors={})
    assert fn is None


@pytest.mark.anyio
async def test_load_after_turn_non_async_method_returns_none(tmp_path: Path) -> None:
    await _write_system(
        tmp_path,
        """
        class System:
            def __init__(self, workspace_dir):
                pass
            def after_turn(self, messages, tool_call_count, called_tools):
                return None
        """,
    )
    fn = load_after_turn_fn(tmp_path, ai_client=_FakeAiClient([]), tool_executors={})
    assert fn is None


@pytest.mark.anyio
async def test_load_after_turn_injects_complete_fn_and_tool_executors(tmp_path: Path) -> None:
    # A System whose after_turn records what it was called with into a file.
    await _write_system(
        tmp_path,
        """
        import json
        from pathlib import Path

        class System:
            def __init__(self, workspace_dir):
                self._ws = workspace_dir

            async def after_turn(self, messages, tool_call_count, called_tools, *, complete_fn, tool_executors):
                Path(str(self._ws) + "/called.json").write_text(json.dumps({
                    "tool_call_count": tool_call_count,
                    "called_tools": called_tools,
                    "has_complete_fn": complete_fn is not None,
                    "executors": sorted(tool_executors),
                }))
        """,
    )

    async def _exec() -> str:
        return "ok"

    fn = load_after_turn_fn(tmp_path, ai_client=_FakeAiClient([]), tool_executors={"skill_manage": _exec})
    assert fn is not None

    await fn([{"role": "user", "content": "hi"}], 3, ["skill_manage", "bash"])

    recorded = json.loads((tmp_path / "called.json").read_text())
    assert recorded["tool_call_count"] == 3
    assert recorded["called_tools"] == ["skill_manage", "bash"]
    assert recorded["has_complete_fn"] is True
    assert recorded["executors"] == ["skill_manage"]


# -- SessionAgent.spawn_after_turn_task ---------------------------------------


@pytest.mark.anyio
async def test_spawn_after_turn_noop_without_hook() -> None:
    # No after_turn_fn configured: spawning must be a safe no-op.
    agent = SessionAgent(ai_client=_FakeAiClient([]), tool_registry=ToolRegistry())
    agent.spawn_after_turn_task()  # should not raise even without a task group


@pytest.mark.anyio
async def test_spawn_after_turn_runs_hook_in_background() -> None:
    calls: list[tuple[int, list[str]]] = []

    async def hook(messages: list[dict], tool_call_count: int, called_tools: list[str]) -> None:
        calls.append((tool_call_count, called_tools))

    agent = SessionAgent(ai_client=_FakeAiClient([]), tool_registry=ToolRegistry(), after_turn_fn=hook)
    agent._last_turn_tool_count = 2
    agent._last_turn_tools = ["skill_manage", "write"]

    async with anyio.create_task_group() as tg:
        agent.set_after_turn_task_group(tg)
        agent.spawn_after_turn_task()

    assert calls == [(2, ["skill_manage", "write"])]


@pytest.mark.anyio
async def test_spawn_after_turn_swallows_hook_errors() -> None:
    async def boom(messages: list[dict], tool_call_count: int, called_tools: list[str]) -> None:
        raise RuntimeError("hook failed")

    agent = SessionAgent(ai_client=_FakeAiClient([]), tool_registry=ToolRegistry(), after_turn_fn=boom)

    # An exception inside the hook must not propagate out of the task group.
    async with anyio.create_task_group() as tg:
        agent.set_after_turn_task_group(tg)
        agent.spawn_after_turn_task()
