from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, cast

import anyio
import pytest

_WORKSPACE_DIR = Path(__file__).resolve().parents[3]
_TOOLS_DIR = _WORKSPACE_DIR / "tools"
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

flow_manage_module = cast(Any, importlib.import_module("flow_manage"))
flow_run_module = cast(Any, importlib.import_module("flow_run"))
run_flow_module = cast(Any, importlib.import_module("run_flow"))


def test_g4_and_legacy_public_entry_points_coexist() -> None:
    assert callable(run_flow_module.run_flow)
    assert callable(flow_run_module.flow_run)
    assert callable(flow_manage_module.flow_manage)


@pytest.mark.anyio
async def test_inner_agent_is_single_round_and_has_no_tools() -> None:
    agent, conversation = await run_flow_module._create_step_agent(
        "http://ai.example",
        "fusion-flow-smoke",
    )

    assert conversation.session_id == "fusion-flow-smoke"
    assert agent._max_tool_rounds == 1
    assert agent._tool_registry.tools == {}


def test_legacy_runner_loads_env_from_relocated_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = "FUSION_FLOW_LEGACY_ENV_TEST"
    flow = tmp_path / "flows" / "demo.flow.ts"
    canonical = tmp_path / "skills" / "fusion-flow"
    legacy = tmp_path / "skills" / "fusion-flow-legacy"
    flow.parent.mkdir(parents=True)
    canonical.mkdir(parents=True)
    legacy.mkdir(parents=True)
    flow.write_text("", encoding="utf-8")
    (canonical / ".env").write_text(f"{key}=wrong\n", encoding="utf-8")
    (legacy / ".env").write_text(f"{key}=relocated\n", encoding="utf-8")
    monkeypatch.delenv(key, raising=False)

    assert flow_run_module._load_flow_env(flow)[key] == "relocated"


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
