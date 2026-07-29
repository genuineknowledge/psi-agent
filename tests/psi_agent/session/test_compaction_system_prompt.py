from __future__ import annotations

from pathlib import Path

import anyio
import pytest

from psi_agent.session.system_prompt import SystemPrompt


@pytest.mark.anyio
async def test_extracts_compact_history_from_system_py(tmp_path: Path) -> None:
    systems_dir = tmp_path / "systems"
    await anyio.Path(str(systems_dir)).mkdir()
    await anyio.Path(str(systems_dir / "system.py")).write_text(
        """async def system_prompt_builder() -> str:
    return "You are helpful."

async def compact_history(history, complete_fn) -> str:
    return "SUMMARY: " + str(len(history))
"""
    )

    sp = await SystemPrompt.from_workspace(tmp_path, "test_session")
    assert sp.compaction_fn is not None
    result = await sp.compaction_fn([{"role": "user"}], None)  # type: ignore[arg-type]
    assert result == "SUMMARY: 1"


@pytest.mark.anyio
async def test_compaction_fn_none_when_not_defined(tmp_path: Path) -> None:
    systems_dir = tmp_path / "systems"
    await anyio.Path(str(systems_dir)).mkdir()
    await anyio.Path(str(systems_dir / "system.py")).write_text(
        'async def system_prompt_builder() -> str:\n    return "You are helpful."\n'
    )

    sp = await SystemPrompt.from_workspace(tmp_path, "test_session")
    assert sp.compaction_fn is None


@pytest.mark.anyio
async def test_compaction_fn_none_when_no_system_py(tmp_path: Path) -> None:
    sp = await SystemPrompt.from_workspace(tmp_path, "test_session")
    assert sp.compaction_fn is None
