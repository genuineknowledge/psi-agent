from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

from psi_agent.session.conversation import Conversation
from psi_agent.session.protocol import AgentRunResult, AgentRunStatus, AgentStopCause

_WORKSPACE_DIR = Path(__file__).resolve().parents[3]
_TOOLS_DIR = _WORKSPACE_DIR / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

flow_manage_module = cast(Any, importlib.import_module("flow_manage"))
flow_run_module = cast(Any, importlib.import_module("flow_run"))
run_flow_module = cast(Any, importlib.import_module("run_flow"))


class _StubRun:
    def __init__(self, result: AgentRunResult | None) -> None:
        self.result = result

    def __aiter__(self) -> _StubRun:
        return self

    async def __anext__(self) -> Any:
        raise StopAsyncIteration

    async def aclose(self) -> None:
        pass


class _StubAgent:
    def __init__(self, run: _StubRun) -> None:
        self._run = run

    def run_streamed(
        self,
        user_message: dict[str, Any],
        extra_params: dict[str, Any] | None = None,
    ) -> _StubRun:
        assert user_message == {"role": "user", "content": "do the step"}
        assert extra_params is None
        return self._run


def test_g4_and_legacy_public_entry_points_coexist() -> None:
    assert callable(run_flow_module.run_flow)
    assert callable(run_flow_module.run_flow_resume)
    assert callable(flow_run_module.flow_run)
    assert callable(flow_manage_module.flow_manage)


@pytest.mark.anyio
async def test_flow_manage_prefers_g4_but_keeps_legacy_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = anyio.Path(tmp_path)
    flows = workspace / "flows"
    dual = flows / "dual"
    legacy = flows / "legacy"
    await dual.mkdir(parents=True)
    await legacy.mkdir(parents=True)
    await (dual / "dual.flow.ts").write_text("legacy source", encoding="utf-8")
    await (dual / "dual.workflow").write_text("g4 source", encoding="utf-8")
    await (legacy / "legacy.flow.ts").write_text("legacy only", encoding="utf-8")
    monkeypatch.setattr(flow_manage_module._paths, "resolve_workspace", lambda: workspace)

    assert await flow_manage_module.flow_manage("view", "dual", target="tasks") == "g4 source"
    assert await flow_manage_module.flow_manage("view", "legacy", target="tasks") == "legacy only"

    listing = await flow_manage_module.flow_manage("list", target="tasks")
    assert "dual: dual.workflow" in listing
    assert "legacy: legacy.flow.ts" in listing


@pytest.mark.anyio
async def test_flow_manage_rejects_create_over_existing_adhoc_g4(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = anyio.Path(tmp_path)
    adhoc = workspace / "flows" / "adhoc" / "existing"
    await adhoc.mkdir(parents=True)
    await (adhoc / "flow.g4").write_text("workflow existing {}", encoding="utf-8")
    monkeypatch.setattr(flow_manage_module._paths, "resolve_workspace", lambda: workspace)

    result = await flow_manage_module.flow_manage(
        "create",
        "existing",
        target="adhoc",
        flow_source="workflow replacement {}",
    )

    assert result == "[Error] Adhoc flow already exists: 'existing'"
    assert not await (adhoc / "flow.workflow").exists()
    assert await (adhoc / "flow.g4").read_text(encoding="utf-8") == "workflow existing {}"


@pytest.mark.anyio
async def test_run_flow_accepts_workflow_and_g4_sources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flows = anyio.Path(tmp_path) / "flows"
    await flows.mkdir()
    await (flows / "first.workflow").write_text("workflow first {}", encoding="utf-8")
    await (flows / "second.g4").write_text("workflow second {}", encoding="utf-8")
    await (flows / "unsupported.txt").write_text("workflow third {}", encoding="utf-8")
    monkeypatch.setattr(run_flow_module, "_WORKSPACE_DIR", tmp_path)

    workflow_path = await run_flow_module._resolve_flow_path("flows/first.workflow")
    g4_path = await run_flow_module._resolve_flow_path("flows/second.g4")

    assert workflow_path.name == "first.workflow"
    assert g4_path.name == "second.g4"
    with pytest.raises(ValueError, match=r"\.workflow or \.g4"):
        await run_flow_module._resolve_flow_path("flows/unsupported.txt")


@pytest.mark.anyio
async def test_step_agent_requires_a_complete_structured_result() -> None:
    conversation = Conversation(messages=[{"role": "assistant", "content": "done"}])
    completed = AgentRunResult(
        status=AgentRunStatus.COMPLETED,
        stop_cause=AgentStopCause.MODEL_COMPLETED,
        model_finish_reason="stop",
        model_turns=1,
    )

    assert (
        await run_flow_module._complete_step_agent(
            _StubAgent(_StubRun(completed)),
            conversation,
            "do the step",
        )
        == "done"
    )

    incomplete = AgentRunResult(
        status=AgentRunStatus.INCOMPLETE,
        stop_cause=AgentStopCause.AGENT_TURN_LIMIT,
        model_finish_reason="tool_calls",
        model_turns=2,
    )
    with pytest.raises(
        RuntimeError,
        match=r"stop_cause=agent_turn_limit, model_finish_reason='tool_calls', model_turns=2",
    ):
        await run_flow_module._complete_step_agent(
            _StubAgent(_StubRun(incomplete)),
            conversation,
            "do the step",
        )

    with pytest.raises(RuntimeError, match="without a terminal result"):
        await run_flow_module._complete_step_agent(
            _StubAgent(_StubRun(None)),
            conversation,
            "do the step",
        )
