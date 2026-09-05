from __future__ import annotations

# ruff: noqa: RUF001
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from psi_agent.session.runtime_context import runtime_scope

if TYPE_CHECKING:
    from agents.desktop.tools._fusion_memory.runtime import get_runtime, reset_runtime_cache_for_tests
    from agents.desktop.tools.memory_add import memory_add
    from agents.desktop.tools.memory_answer_context import memory_answer_context
    from agents.desktop.tools.memory_search import memory_search
else:
    from _fusion_memory.runtime import get_runtime, reset_runtime_cache_for_tests
    from memory_add import memory_add
    from memory_answer_context import memory_answer_context
    from memory_search import memory_search


@pytest.fixture(autouse=True)
async def clear_runtime() -> AsyncIterator[None]:
    await reset_runtime_cache_for_tests()
    yield
    await reset_runtime_cache_for_tests()


@pytest.mark.anyio
async def test_cross_session_workspace_isolation_and_jsonl_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    appdata = tmp_path / "appdata"
    (appdata / "histories").mkdir(parents=True)
    (appdata / "state").mkdir()
    valid_user = "项目代号是 Quartz-927，数据库选择 PostgreSQL"
    valid_assistant = "已确认 Quartz-927 使用 PostgreSQL"
    mixed = [
        {"role": "system", "content": "summary system"},
        {"role": "user", "content": f"{valid_user}\n[RECV:/tmp/input]", "kind": "chat"},
        {"role": "assistant", "reasoning": "thinking", "tool_calls": [{"id": "call"}], "kind": "chat"},
        {"role": "tool", "content": "secret tool output", "kind": "chat"},
        {"role": "assistant", "content": valid_assistant + "[SEND:/tmp/output]", "kind": "chat"},
        {"role": "user", "content": "heartbeat", "kind": "schedule.silent"},
        {"role": "assistant", "content": "HEARTBEAT_OK", "kind": "schedule.silent"},
        {"role": "user", "content": "unfinished", "kind": "chat"},
    ]
    (appdata / "histories" / "session-1.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in mixed) + "\n", encoding="utf-8"
    )
    (appdata / "state" / "latest.json").write_text(
        json.dumps({"sessions": [{"id": "session-1", "workspace": str(workspace_a)}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    monkeypatch.setenv("FUSION_MEMORY_JOURNAL_FSYNC", "0")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("FUSION_MEMORY_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("PSI_AI_API_KEY", raising=False)

    runtime_a = await get_runtime(str(workspace_a))
    assert (await runtime_a.ingest_current_session("session-1", mixed[1], mixed[4]))["ok"] is True
    with runtime_scope(session_id="session-2", workspace=str(workspace_a), agent=str(workspace_a)):
        first = await runtime_a.first_turn_recall("session-2", "Quartz-927")
        assert "Quartz-927" in first and "session_id=session-1" in first
        assert await runtime_a.first_turn_recall("session-2", "Quartz-927") == ""
        search = json.loads(await memory_search("Quartz-927"))
        answer = json.loads(await memory_answer_context("Quartz-927"))
        assert search["evidence"] and answer["evidence"]
        source_id = search["evidence"][0]["span_id"]
        assert json.loads(await memory_add([source_id]))["ok"] is True

    runtime_b = await get_runtime(str(workspace_b))
    assert await runtime_b.search("Quartz-927") == []
    assert await runtime_b.first_turn_recall("session-b", "Quartz-927") == ""

    journal_path = workspace_a / ".fusion-memory" / "evidence.jsonl"
    records = [json.loads(line) for line in journal_path.read_text(encoding="utf-8").splitlines()]
    assert {record["record_type"] for record in records} == {"evidence_span", "memory_promotion"}
    evidence_records = [record for record in records if record["record_type"] == "evidence_span"]
    promotion_records = [record for record in records if record["record_type"] == "memory_promotion"]
    assert {record["content"] for record in evidence_records} == {valid_user + "\n", valid_assistant}
    assert len(promotion_records) == 1
    assert promotion_records[0]["source_span_ids"] == [source_id]
    assert "content" not in promotion_records[0]
    authority = journal_path.read_text(encoding="utf-8")
    assert not any(term in authority for term in ["tool output", "thinking", "HEARTBEAT_OK", "summary"])

    await reset_runtime_cache_for_tests()
    database = workspace_a / ".fusion-memory" / "memory.sqlite3"
    database.unlink()
    recovered = await get_runtime(str(workspace_a))
    hits = await recovered.search("Quartz-927")
    assert hits and hits[0].session_id == "session-1"
