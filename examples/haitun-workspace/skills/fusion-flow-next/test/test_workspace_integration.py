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


def test_next_and_legacy_public_entry_points_coexist() -> None:
    assert callable(run_flow_module.run_flow)
    assert callable(flow_run_module.flow_run)
    assert callable(flow_manage_module.flow_manage)


@pytest.mark.anyio
async def test_flow_manage_prefers_next_but_keeps_legacy_fallback(
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
    await (dual / "dual.workflow").write_text("next source", encoding="utf-8")
    await (legacy / "legacy.flow.ts").write_text("legacy only", encoding="utf-8")
    monkeypatch.setattr(flow_manage_module._paths, "resolve_workspace", lambda: workspace)

    assert await flow_manage_module.flow_manage("view", "dual", target="tasks") == "next source"
    assert await flow_manage_module.flow_manage("view", "legacy", target="tasks") == "legacy only"

    listing = await flow_manage_module.flow_manage("list", target="tasks")
    assert "dual: dual.workflow" in listing
    assert "legacy: legacy.flow.ts" in listing


@pytest.mark.anyio
async def test_flow_manage_creates_next_without_removing_legacy_support(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = anyio.Path(tmp_path)
    monkeypatch.setattr(flow_manage_module._paths, "resolve_workspace", lambda: workspace)

    created_next = await flow_manage_module.flow_manage(
        "create",
        "next-demo",
        target="adhoc",
        flow_source="workflow next_demo {}",
    )
    created_legacy = await flow_manage_module.flow_manage(
        "create",
        "legacy-demo",
        target="adhoc",
        flow_ts="export const legacy = true;",
    )

    assert created_next == "Adhoc flow created: 'next-demo'"
    assert created_legacy == "Adhoc flow created: 'legacy-demo'"
    assert await (workspace / "flows" / "adhoc" / "next-demo" / "flow.workflow").exists()
    assert await (workspace / "flows" / "adhoc" / "legacy-demo" / "flow.ts").exists()
