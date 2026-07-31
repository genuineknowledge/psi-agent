"""Fusion Flow prompt routing must prefer Next and preserve explicit legacy fallback."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import anyio
import pytest

AGENT_ROOT = Path(__file__).resolve().parents[1]
SYSTEMS = AGENT_ROOT / "systems"


def _load_system(module_name: str) -> Any:
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


@pytest.mark.anyio
async def test_fusion_prompt_prefers_next_with_safe_step_boundary(tmp_path: Path) -> None:
    module_name = f"haitun_fusion_prompt_test_{id(tmp_path)}"
    module = _load_system(module_name)
    try:
        system = module.System(
            anyio.Path(str(AGENT_ROOT)),
            user_workspace=anyio.Path(tmp_path),
        )
        section = await system._build_fusion_section()
    finally:
        sys.modules.pop(module_name, None)

    assert "Use `fusion-flow-next` and `run_flow` by default" in section
    assert "Inner Agent Steps have no tools" in section
    assert "`instructions_json` as an exact mapping" in section
    assert "at most 32 Steps, concurrency 8" in section
    assert "15 minutes total" in section


@pytest.mark.anyio
async def test_fusion_prompt_keeps_explicit_legacy_route(tmp_path: Path) -> None:
    module_name = f"haitun_fusion_legacy_test_{id(tmp_path)}"
    module = _load_system(module_name)
    try:
        system = module.System(
            anyio.Path(str(AGENT_ROOT)),
            user_workspace=anyio.Path(tmp_path),
        )
        section = await system._build_fusion_section()
    finally:
        sys.modules.pop(module_name, None)

    assert "existing `.flow.ts` file" in section
    assert "`skills/fusion-flow/SKILL.md`" in section
    assert "legacy `flow_run` path" in section
    assert "FLOW_PSI_COMMAND_ARGS=" in section
    assert "FLOW_PSI_AI / FLOW_PSI_MODEL / FLOW_PSI_BASE_URL / FLOW_PSI_API_KEY" in section
    assert "Never write API keys into this workspace, generated `.flow.ts` files" in section
