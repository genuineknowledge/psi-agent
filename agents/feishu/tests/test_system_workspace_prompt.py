"""system_prompt_builder must announce user workspace from ContextVar, not agent __file__."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import anyio
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
    # system.py expects sibling prompt_sections on sys.path
    sys.path.insert(0, str(SYSTEMS))
    try:
        spec.loader.exec_module(module)
    finally:
        if sys.path and sys.path[0] == str(SYSTEMS):
            sys.path.pop(0)
    return module


@pytest.mark.anyio
async def test_system_prompt_workspace_section_uses_get_workspace(tmp_path: Path) -> None:
    user_ws = tmp_path / "desktop-haitun"
    user_ws.mkdir()
    name = f"haitun_system_ws_test_{id(tmp_path)}"
    module = _load_system(name)
    try:
        with path_scope(workspace=str(user_ws), agent=str(AGENT_ROOT)):
            prompt = await module.system_prompt_builder()
    finally:
        sys.modules.pop(name, None)

    user_abs = str(user_ws.resolve())
    assert user_abs in prompt
    # Must announce the bound user workspace as the file-IO root.
    assert "user workspace** (file IO / deliverables / schedules / flows) is:" in prompt
    assert f"is: {user_abs}" in prompt or f"is: {user_abs.replace(chr(92), '/')}" in prompt


@pytest.mark.anyio
async def test_history_recall_reaches_the_assembled_prompt(tmp_path: Path) -> None:
    """Assembly layer, not just the constant.

    ``SESSION_MANAGEMENT_SECTION`` is injected only when the tool list overlaps
    ``_session_tools``. Asserting on the constant alone would stay green while
    the guidance never reached a real prompt, so this drives the builder with a
    tool list containing ``history_recall`` and nothing else from that set.
    """
    user_ws = tmp_path / "ws-recall"
    user_ws.mkdir()
    name = f"haitun_system_recall_test_{id(tmp_path)}"
    module = _load_system(name)
    try:
        with path_scope(workspace=str(user_ws), agent=str(AGENT_ROOT)):
            prompt = await module.system_prompt_builder()
            builder = module.System(anyio.Path(AGENT_ROOT), user_workspace=anyio.Path(user_ws))
            gated = await builder.build_system_prompt(tool_names=["history_recall", "read"])
    finally:
        sys.modules.pop(name, None)

    assert "history_recall" in prompt
    assert "history_recall" in gated
    assert "[已省略" in gated
