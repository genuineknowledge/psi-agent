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
