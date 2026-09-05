from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import pytest

if TYPE_CHECKING:
    import agents.desktop.tools._fusion_memory.runtime as runtime_module
    from agents.desktop.tools._fusion_memory.runtime import get_runtime, reset_runtime_cache_for_tests
else:
    import _fusion_memory.runtime as runtime_module
    from _fusion_memory.runtime import get_runtime, reset_runtime_cache_for_tests


@pytest.fixture(autouse=True)
async def clear_runtime_cache() -> AsyncIterator[None]:
    await reset_runtime_cache_for_tests()
    yield
    await reset_runtime_cache_for_tests()


@pytest.mark.anyio
async def test_cache_is_workspace_scoped_and_survives_session_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    appdata = tmp_path / "appdata"
    (appdata / "histories").mkdir(parents=True)
    (appdata / "state").mkdir()
    history = appdata / "histories" / "s1.jsonl"
    user_message = {"role": "user", "content": "我使用 PostgreSQL", "kind": "chat"}
    assistant_message = {"role": "assistant", "content": "已记录数据库偏好", "kind": "chat"}
    history.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in (user_message, assistant_message)) + "\n",
        encoding="utf-8",
    )
    (appdata / "state" / "latest.json").write_text(
        json.dumps({"sessions": [{"id": "s1", "workspace": str(workspace_a)}]}), encoding="utf-8"
    )
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    monkeypatch.setenv("FUSION_MEMORY_JOURNAL_FSYNC", "0")
    runtime_a1 = await get_runtime(str(workspace_a))
    runtime_a2 = await get_runtime(str(workspace_a / "."))
    runtime_b = await get_runtime(str(workspace_b))
    assert runtime_a1 is runtime_a2
    assert runtime_a1 is not runtime_b
    assert runtime_a1.workspace_id != runtime_b.workspace_id
    assert (await runtime_a1.ingest_current_session("s1", user_message, assistant_message))["ok"] is True
    assert (await runtime_b.ingest_current_session("s1", user_message, assistant_message))["ok"] is False
    hits = await runtime_a1.search("PostgreSQL")
    assert hits and hits[0].session_id == "s1"
    assert await runtime_b.search("PostgreSQL") == []
    block = await runtime_a1.first_turn_recall("s2", "PostgreSQL")
    assert "PostgreSQL" in block
    assert await runtime_a1.first_turn_recall("s2", "PostgreSQL") == ""


@pytest.mark.anyio
async def test_disabled_runtime_creates_no_memory_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("FUSION_MEMORY_ENABLE_JOURNAL", "0")
    runtime = await get_runtime(str(workspace))
    assert not runtime.enabled
    assert not (workspace / ".fusion-memory").exists()
    assert await runtime.search("anything") == []


@pytest.mark.anyio
async def test_standalone_committed_turn_ingests_without_gateway_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    explicit_appdata = tmp_path / "explicit-appdata"
    histories = explicit_appdata / "histories"
    histories.mkdir(parents=True)
    history = histories / "standalone.jsonl"
    rows = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "standalone memory", "kind": "chat"},
        {"role": "assistant", "content": "remembered", "kind": "chat"},
    ]
    history.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("PSI_APPDATA", str(tmp_path / "environment-appdata"))
    monkeypatch.setenv("FUSION_MEMORY_JOURNAL_FSYNC", "0")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("FUSION_MEMORY_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("PSI_AI_API_KEY", raising=False)
    runtime = await get_runtime(str(workspace))
    user_message = {
        **rows[1],
        "_psi_history_provenance": {
            "path": str(history),
            "appdata_root": str(explicit_appdata),
            "user_line": 2,
            "assistant_line": 3,
        },
    }

    result = await runtime.ingest_current_session("standalone", user_message, rows[2])

    assert result["ok"] is True
    assert {hit.content for hit in await runtime.search("standalone memory")} == {
        "standalone memory",
        "remembered",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provenance",
    [
        {"path": "{history}"},
        {"path": "{history}", "user_line": 2, "assistant_line": 3},
        {"path": "{history}", "appdata_root": "{appdata}"},
    ],
)
async def test_incomplete_committed_provenance_cannot_bypass_gateway_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provenance: dict[str, object]
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    history = histories / "standalone.jsonl"
    rows = [
        {"role": "user", "content": "untrusted memory", "kind": "chat"},
        {"role": "assistant", "content": "must not be indexed", "kind": "chat"},
    ]
    history.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    monkeypatch.setenv("FUSION_MEMORY_JOURNAL_FSYNC", "0")
    runtime = await get_runtime(str(workspace))
    resolved_provenance = {
        key: str(history) if value == "{history}" else str(appdata) if value == "{appdata}" else value
        for key, value in provenance.items()
    }
    user_message = {**rows[0], "_psi_history_provenance": resolved_provenance}

    result = await runtime.ingest_current_session("standalone", user_message, rows[1])

    assert result == {"ok": False, "error": "HistoryUnavailable"}
    assert runtime.store is not None
    assert runtime.store.conn.execute("select count(*) from evidence_spans").fetchone()[0] == 0


@pytest.mark.anyio
async def test_concurrent_first_use_creates_one_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("FUSION_MEMORY_JOURNAL_FSYNC", "0")
    original = runtime_module._create_runtime
    count = 0
    count_lock = threading.Lock()

    def counted(settings):
        nonlocal count
        with count_lock:
            count += 1
        return original(settings)

    monkeypatch.setattr(runtime_module, "_create_runtime", counted)
    results = []

    async def load() -> None:
        results.append(await get_runtime(str(workspace)))

    async with anyio.create_task_group() as group:
        group.start_soon(load)
        group.start_soon(load)
    assert len(results) == 2 and results[0] is results[1]
    assert count == 1


@pytest.mark.anyio
async def test_only_hook_confirmed_turn_is_derived_and_recovery_is_bounded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    history = histories / "s1.jsonl"
    (appdata / "state").mkdir()
    (appdata / "state" / "latest.json").write_text(
        json.dumps({"sessions": [{"id": "s1", "workspace": str(workspace)}]}), encoding="utf-8"
    )
    rows = []
    for turn in range(1, 11):
        rows.extend(
            (
                {"role": "user", "content": f"question {turn}", "kind": "chat"},
                {"role": "assistant", "content": f"answer {turn}", "kind": "chat"},
            )
        )
    history.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    monkeypatch.setenv("FUSION_MEMORY_JOURNAL_FSYNC", "0")
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    monkeypatch.delenv("FUSION_MEMORY_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("PSI_AI_API_KEY", raising=False)
    runtime = await get_runtime(str(workspace))

    assert (await runtime.ingest_current_session("s1", rows[-2], rows[-1]))["ok"] is True
    assert runtime.store is not None
    checkpoint = runtime.store.read_checkpoint(runtime.workspace_id, str(history.resolve()))
    assert checkpoint is not None and checkpoint.extraction_line == 0
    assert checkpoint.confirmed_line_count == 20
    assert runtime.store.conn.execute("select count(*) from summary_cards").fetchone()[0] == 1

    assert (await runtime.ingest_current_session("s1", rows[-2], rows[-1]))["ok"] is True
    checkpoint = runtime.store.read_checkpoint(runtime.workspace_id, str(history.resolve()))
    assert checkpoint is not None and checkpoint.extraction_line == 0
    assert runtime.store.conn.execute("select count(*) from summary_cards").fetchone()[0] == 1

    await reset_runtime_cache_for_tests()
    monkeypatch.setenv("PSI_AI_PROVIDER", "openai")
    monkeypatch.setenv("PSI_AI_MODEL", "agent-model")
    monkeypatch.setenv("PSI_AI_API_KEY", "agent-key")
    monkeypatch.setenv("PSI_AI_BASE_URL", "https://agent.example/v1")
    extracted_lines = []

    async def extract(_models, spans):
        extracted_lines.append([span.line_no for span in spans])
        return []

    monkeypatch.setattr(runtime_module, "extract_memory_items", extract)
    runtime = await get_runtime(str(workspace))
    assert (await runtime.ingest_current_session("s1", rows[-2], rows[-1]))["ok"] is True
    assert runtime.store is not None
    checkpoint = runtime.store.read_checkpoint(runtime.workspace_id, str(history.resolve()))
    assert checkpoint is not None and checkpoint.extraction_line == 20
    assert len(extracted_lines) == 1

    assert (await runtime.ingest_current_session("s1", rows[-2], rows[-1]))["ok"] is True
    checkpoint = runtime.store.read_checkpoint(runtime.workspace_id, str(history.resolve()))
    assert checkpoint is not None and checkpoint.extraction_line == 20
    assert len(extracted_lines) == 1


@pytest.mark.anyio
async def test_ingest_without_successful_hook_provenance_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    (histories / "s1.jsonl").write_text(
        json.dumps({"role": "assistant", "content": "unproven history text", "kind": "chat"}) + "\n",
        encoding="utf-8",
    )
    (appdata / "state").mkdir()
    (appdata / "state" / "latest.json").write_text(
        json.dumps({"sessions": [{"id": "s1", "workspace": str(workspace)}]}), encoding="utf-8"
    )
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    monkeypatch.setenv("FUSION_MEMORY_JOURNAL_FSYNC", "0")
    runtime = await get_runtime(str(workspace))

    assert await runtime.ingest_current_session("s1") == {"ok": False, "unconfirmed": True}
    assert runtime.store is not None
    assert runtime.store.conn.execute("select count(*) from evidence_spans").fetchone()[0] == 0


@pytest.mark.anyio
async def test_turn_card_failure_retries_from_confirmed_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    history = histories / "s1.jsonl"
    (appdata / "state").mkdir()
    (appdata / "state" / "latest.json").write_text(
        json.dumps({"sessions": [{"id": "s1", "workspace": str(workspace)}]}), encoding="utf-8"
    )
    rows = []
    for turn in range(10):
        rows.extend(
            (
                {"role": "user", "content": f"question {turn}", "kind": "chat"},
                {"role": "assistant", "content": f"answer {turn}", "kind": "chat"},
            )
        )
    history.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    monkeypatch.setenv("FUSION_MEMORY_JOURNAL_FSYNC", "0")
    monkeypatch.delenv("FUSION_MEMORY_MODEL_API_KEY", raising=False)
    monkeypatch.delenv("PSI_AI_API_KEY", raising=False)
    runtime = await get_runtime(str(workspace))
    assert runtime.store is not None
    original = runtime.store.upsert_turn_card
    calls = 0

    def fail_once(*args):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary card failure")
        return original(*args)

    monkeypatch.setattr(runtime.store, "upsert_turn_card", fail_once)
    assert (await runtime.ingest_current_session("s1", rows[-2], rows[-1]))["ok"] is True
    checkpoint = runtime.store.read_checkpoint(runtime.workspace_id, str(history.resolve()))
    assert checkpoint is not None and checkpoint.card_line == 0

    assert (await runtime.ingest_current_session("s1", rows[-2], rows[-1]))["ok"] is True
    checkpoint = runtime.store.read_checkpoint(runtime.workspace_id, str(history.resolve()))
    assert checkpoint is not None and checkpoint.card_line == 20
    assert calls == 2


@pytest.mark.anyio
async def test_failed_extraction_is_retried_without_blocking_turn_card(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    history = histories / "s1.jsonl"
    (appdata / "state").mkdir()
    (appdata / "state" / "latest.json").write_text(
        json.dumps({"sessions": [{"id": "s1", "workspace": str(workspace)}]}), encoding="utf-8"
    )
    history.write_text(
        "\n".join(
            (
                json.dumps({"role": "user", "content": "question", "kind": "chat"}),
                json.dumps({"role": "assistant", "content": "answer", "kind": "chat"}),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("PSI_APPDATA", str(appdata))
    monkeypatch.setenv("FUSION_MEMORY_JOURNAL_FSYNC", "0")
    monkeypatch.setenv("PSI_AI_PROVIDER", "openai")
    monkeypatch.setenv("PSI_AI_MODEL", "agent-model")
    monkeypatch.setenv("PSI_AI_API_KEY", "agent-key")
    monkeypatch.setenv("PSI_AI_BASE_URL", "https://agent.example/v1")
    calls = 0

    async def extract(_models, _spans):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary failure")
        return []

    monkeypatch.setattr(runtime_module, "extract_memory_items", extract)
    runtime = await get_runtime(str(workspace))

    user_message = {"role": "user", "content": "question", "kind": "chat"}
    assistant_message = {"role": "assistant", "content": "answer", "kind": "chat"}
    assert (await runtime.ingest_current_session("s1", user_message, assistant_message))["ok"] is True
    assert runtime.store is not None
    checkpoint = runtime.store.read_checkpoint(runtime.workspace_id, str(history.resolve()))
    assert checkpoint is not None and checkpoint.extraction_line == 0
    assert checkpoint.card_line == 2
    assert runtime.store.conn.execute("select count(*) from summary_cards").fetchone()[0] == 1

    assert (await runtime.ingest_current_session("s1", user_message, assistant_message))["ok"] is True
    checkpoint = runtime.store.read_checkpoint(runtime.workspace_id, str(history.resolve()))
    assert checkpoint is not None and checkpoint.extraction_line == 2
    assert checkpoint.card_line == 2
    assert calls == 2
