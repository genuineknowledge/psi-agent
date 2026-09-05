from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from psi_agent.session.runtime_context import runtime_scope

if TYPE_CHECKING:
    from agents.desktop.tools._fusion_memory.runtime import reset_runtime_cache_for_tests
    from agents.desktop.tools.memory_add import memory_add
    from agents.desktop.tools.memory_answer_context import memory_answer_context
    from agents.desktop.tools.memory_search import memory_search
else:
    from _fusion_memory.runtime import reset_runtime_cache_for_tests
    from memory_add import memory_add
    from memory_answer_context import memory_answer_context
    from memory_search import memory_search


@pytest.mark.anyio
async def test_disabled_wrappers_return_structured_result(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FUSION_MEMORY_ENABLE_JOURNAL", "0")
    await reset_runtime_cache_for_tests()
    with runtime_scope(session_id="s1", workspace=str(tmp_path), agent=str(tmp_path)):
        assert json.loads(await memory_search("secret"))["disabled"] is True
        assert json.loads(await memory_answer_context("secret"))["disabled"] is True
        assert json.loads(await memory_add(["missing"]))["ok"] is False
