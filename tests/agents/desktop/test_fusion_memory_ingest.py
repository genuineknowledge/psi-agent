from __future__ import annotations

# ruff: noqa: RUF001
import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    import agents.desktop.tools._fusion_memory.ingest as ingest_module
    from agents.desktop.tools._fusion_memory.ingest import (
        HistorySource,
        discover_current_history,
        ingest_confirmed_turn,
        parse_completed_turns,
        workspace_scope,
    )
    from agents.desktop.tools._fusion_memory.journal import JsonlJournal
    from agents.desktop.tools._fusion_memory.store import MemoryStore
else:
    import _fusion_memory.ingest as ingest_module
    from _fusion_memory.ingest import (
        HistorySource,
        discover_current_history,
        ingest_confirmed_turn,
        parse_completed_turns,
        workspace_scope,
    )
    from _fusion_memory.journal import JsonlJournal
    from _fusion_memory.store import MemoryStore


def test_parse_filters_non_raw_rows_and_preserves_history(tmp_path: Path) -> None:
    history = tmp_path / "s1.jsonl"
    rows = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "记住我用 PostgreSQL\n[RECV:/tmp/a.txt]", "kind": "chat", "turn_context": "clock"},
        {"role": "assistant", "reasoning": "thinking", "tool_calls": [{"id": "1"}], "kind": "chat"},
        {"role": "tool", "content": "secret tool output", "kind": "chat"},
        {"role": "assistant", "content": "好的，已记录。[SEND:/tmp/result.md]", "reasoning": "hidden", "kind": "chat"},
        {"role": "user", "content": "heartbeat", "kind": "schedule.silent"},
        {"role": "assistant", "content": "HEARTBEAT_OK", "kind": "schedule.silent"},
        {"role": "user_schedule", "content": "legacy trigger", "chat_type": "schedule"},
        {"role": "assistant_schedule", "content": "legacy output", "chat_type": "schedule"},
        {"role": "user", "content": "tool loop", "kind": "chat"},
        {"role": "assistant", "content": "[Max tool rounds reached]", "kind": "chat"},
        {"role": "user", "content": "unfinished", "kind": "chat"},
    ]
    history.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    before = history.read_bytes()
    scope = workspace_scope(tmp_path)
    spans = parse_completed_turns(scope, HistorySource("s1", history))
    assert [(span.speaker, span.content) for span in spans] == [
        ("user", "记住我用 PostgreSQL\n"),
        ("assistant", "好的，已记录。"),
    ]
    assert all(span.timestamp is None for span in spans)
    assert history.read_bytes() == before


def test_turn_id_is_derived_from_each_completed_source_pair(tmp_path: Path) -> None:
    history = tmp_path / "s1.jsonl"
    rows = [
        {"id": "duplicate", "role": "user", "content": "first question", "kind": "chat"},
        {"role": "assistant", "content": "first answer", "kind": "chat"},
        {"id": "duplicate", "role": "user", "content": "second question", "kind": "chat"},
        {"role": "assistant", "content": "second answer", "kind": "chat"},
    ]
    history.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    scope = workspace_scope(tmp_path)
    source = HistorySource("s1", history)

    spans = parse_completed_turns(scope, source)

    assert spans[0].turn_id == spans[1].turn_id
    assert spans[2].turn_id == spans[3].turn_id
    assert spans[0].turn_id != spans[2].turn_id
    assert [span.turn_id for span in parse_completed_turns(scope, source)] == [span.turn_id for span in spans]


def test_parse_fails_closed_and_only_removes_transfer_markers(tmp_path: Path) -> None:
    history = tmp_path / "s1.jsonl"
    rows = [
        {"role": "user", "content": "unknown input", "kind": "heartbeat"},
        {"role": "assistant", "content": "unknown output", "kind": "heartbeat"},
        {"role": "user_trigger", "content": "trigger input"},
        {"role": "assistant_trigger", "content": "trigger output"},
        {"role": "user", "content": "  raw\n\n\n[RECV:/tmp/in.txt]\n", "kind": "chat"},
        {
            "role": "assistant",
            "content": "maximum context length and max rounds are configurable",
            "kind": "chat",
        },
    ]
    history.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")

    spans = parse_completed_turns(workspace_scope(tmp_path), HistorySource("s1", history))

    assert [(span.speaker, span.content) for span in spans] == [
        ("user", "  raw\n\n\n\n"),
        ("assistant", "maximum context length and max rounds are configurable"),
    ]


def test_ingest_is_idempotent_and_updates_checkpoint(tmp_path: Path) -> None:
    history = tmp_path / "s1.jsonl"
    history.write_text(
        json.dumps({"role": "user", "content": "你好", "kind": "chat"}, ensure_ascii=False)
        + "\n"
        + json.dumps({"role": "assistant", "content": "你好！", "kind": "chat"}, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    scope = workspace_scope(tmp_path)
    journal = JsonlJournal(tmp_path / "evidence.jsonl", fsync=False)
    store = MemoryStore(tmp_path / "memory.sqlite3", journal, scope.workspace_id).open()
    source = HistorySource("s1", history)
    user_message = {"role": "user", "content": "你好", "kind": "chat"}
    assistant_message = {"role": "assistant", "content": "你好！", "kind": "chat"}
    first, _ = ingest_confirmed_turn(store, scope, source, user_message, assistant_message)
    second, _ = ingest_confirmed_turn(store, scope, source, user_message, assistant_message)
    assert first.completed_turns == 1 and second.completed_turns == 0
    assert first.spans_appended == 2 and second.spans_appended == 0
    assert store.read_checkpoint(scope.workspace_id, str(history.resolve())) is not None
    history.write_text(json.dumps({"role": "user", "content": "changed", "kind": "chat"}) + "\n", encoding="utf-8")
    third, _ = ingest_confirmed_turn(store, scope, source, {"role": "user", "content": "changed"}, assistant_message)
    assert third.rescanned_files == 1
    store.close()


def test_confirmed_ingest_uses_committed_line_provenance_without_prefix_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    history = tmp_path / "s1.jsonl"
    rows = [
        {"role": "system", "content": "large mutable prompt"},
        {"role": "user", "content": "question", "kind": "chat"},
        {"role": "assistant", "tool_calls": [{"id": "call-1"}], "kind": "chat"},
        {"role": "tool", "content": "non-raw tool output", "kind": "chat"},
        {"role": "assistant", "content": "answer", "kind": "chat"},
    ]
    history.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    scope = workspace_scope(tmp_path)
    store = MemoryStore(
        tmp_path / "memory.sqlite3", JsonlJournal(tmp_path / "evidence.jsonl", fsync=False), scope.workspace_id
    ).open()

    def reject_prefix_scan(*_args, **_kwargs):
        raise AssertionError("trusted after-turn provenance must not scan the history prefix")

    monkeypatch.setattr(ingest_module, "_prefix_hash", reject_prefix_scan)
    user_message = {
        **rows[1],
        "_psi_history_provenance": {
            "path": str(history),
            "appdata_root": str(tmp_path),
            "user_line": 2,
            "assistant_line": 5,
        },
    }

    report, spans = ingest_confirmed_turn(store, scope, HistorySource("s1", history), user_message, rows[4])

    assert report.completed_turns == 1
    assert [(span.line_no, span.content) for span in spans] == [(2, "question"), (5, "answer")]
    store.close()


def test_system_prompt_rewrite_does_not_reset_derivation_progress(tmp_path: Path) -> None:
    history = tmp_path / "s1.jsonl"
    first_rows = [
        {"role": "system", "content": "prompt v1"},
        {"role": "user", "content": "question 1", "kind": "chat"},
        {"role": "assistant", "content": "answer 1", "kind": "chat"},
    ]
    history.write_text("\n".join(json.dumps(row) for row in first_rows) + "\n", encoding="utf-8")
    scope = workspace_scope(tmp_path)
    store = MemoryStore(
        tmp_path / "memory.sqlite3", JsonlJournal(tmp_path / "evidence.jsonl", fsync=False), scope.workspace_id
    ).open()
    source = HistorySource("s1", history)
    ingest_confirmed_turn(store, scope, source, first_rows[1], first_rows[2])
    checkpoint = store.read_checkpoint(scope.workspace_id, str(history.resolve()))
    assert checkpoint is not None
    store.write_checkpoint(replace(checkpoint, extraction_line=3, card_line=3))

    second_rows = [
        {"role": "system", "content": "prompt v2"},
        *first_rows[1:],
        {"role": "user", "content": "question 2", "kind": "chat"},
        {"role": "assistant", "content": "answer 2", "kind": "chat"},
    ]
    history.write_text("\n".join(json.dumps(row) for row in second_rows) + "\n", encoding="utf-8")

    report, _ = ingest_confirmed_turn(store, scope, source, second_rows[-2], second_rows[-1])

    updated = store.read_checkpoint(scope.workspace_id, str(history.resolve()))
    assert report.rescanned_files == 0
    assert updated is not None and updated.extraction_line == 3 and updated.card_line == 3
    store.close()


def test_confirmed_ingest_excludes_prior_turn_without_finish_provenance(tmp_path: Path) -> None:
    history = tmp_path / "s1.jsonl"
    rows = [
        {"role": "user", "content": "truncated question", "kind": "chat"},
        {"role": "assistant", "content": "plausible but truncated answer", "kind": "chat"},
        {"role": "user", "content": "confirmed question", "kind": "chat"},
        {"role": "assistant", "content": "confirmed answer", "kind": "chat"},
    ]
    history.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    scope = workspace_scope(tmp_path)
    journal = JsonlJournal(tmp_path / "evidence.jsonl", fsync=False)
    store = MemoryStore(tmp_path / "memory.sqlite3", journal, scope.workspace_id).open()

    report, spans = ingest_confirmed_turn(
        store,
        scope,
        HistorySource("s1", history),
        rows[-2],
        rows[-1],
    )

    assert report.completed_turns == 1
    assert [item.content for item in spans] == ["confirmed question", "confirmed answer"]
    assert [item.content for item in journal.iter_active_spans()] == ["confirmed question", "confirmed answer"]
    store.close()


def test_ingest_does_not_match_stale_duplicate_before_history_tail(tmp_path: Path) -> None:
    history = tmp_path / "s1.jsonl"
    rows = [
        {"role": "user", "content": "重复问题", "kind": "chat"},
        {"role": "assistant", "content": "重复回答", "kind": "chat"},
        {"role": "user", "content": "后续问题", "kind": "chat"},
        {"role": "assistant", "content": "后续回答", "kind": "chat"},
    ]
    history.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n", encoding="utf-8")
    scope = workspace_scope(tmp_path)
    journal = JsonlJournal(tmp_path / "evidence.jsonl", fsync=False)
    store = MemoryStore(tmp_path / "memory.sqlite3", journal, scope.workspace_id).open()

    report, spans = ingest_confirmed_turn(
        store,
        scope,
        HistorySource("s1", history),
        rows[0],
        rows[1],
    )

    assert report.completed_turns == 0
    assert spans == []
    assert list(journal.iter_active_spans()) == []
    assert store.read_checkpoint(scope.workspace_id, str(history.resolve())) is None
    store.close()


def test_full_rescan_cannot_resurrect_tombstoned_history(tmp_path: Path) -> None:
    history = tmp_path / "s1.jsonl"
    history.write_text(
        json.dumps({"role": "user", "content": "secret", "kind": "chat"})
        + "\n"
        + json.dumps({"role": "assistant", "content": "saved", "kind": "chat"})
        + "\n",
        encoding="utf-8",
    )
    scope = workspace_scope(tmp_path)
    journal = JsonlJournal(tmp_path / "evidence.jsonl", fsync=False)
    store = MemoryStore(tmp_path / "memory.sqlite3", journal, scope.workspace_id).open()
    source = HistorySource("s1", history)
    user_message = {"role": "user", "content": "secret", "kind": "chat"}
    assistant_message = {"role": "assistant", "content": "saved", "kind": "chat"}
    ingest_confirmed_turn(store, scope, source, user_message, assistant_message)
    journal.append_scope_clear(scope.workspace_id)
    store.rebuild_index()
    store.conn.execute("delete from ingest_checkpoints")
    store.conn.commit()
    ingest_confirmed_turn(store, scope, source, user_message, assistant_message)
    assert (
        store.get_source_spans(scope.workspace_id, [span.span_id for span in parse_completed_turns(scope, source)])
        == []
    )
    assert list(journal.iter_active_spans()) == []
    store.close()


@pytest.mark.anyio
async def test_discover_current_history_ignores_other_gateway_sessions(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    (appdata / "histories").mkdir(parents=True)
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    (workspace_a / "histories").mkdir(parents=True)
    (workspace_b / "histories").mkdir(parents=True)
    (appdata / "histories" / "s1.jsonl").write_text("{}\n", encoding="utf-8")
    (appdata / "histories" / "s2.jsonl").write_text("{}\n", encoding="utf-8")
    (appdata / "histories" / "unowned.jsonl").write_text("{}\n", encoding="utf-8")
    (appdata / "state").mkdir()
    (appdata / "state" / "latest.json").write_text(
        json.dumps(
            {
                "sessions": [{"id": "s1", "workspace": str(workspace_a)}, {"id": "s2", "workspace": str(workspace_b)}],
                "ais": [{"id": "secret", "api_key": "do-not-read"}],
            }
        ),
        encoding="utf-8",
    )
    found = await discover_current_history(workspace_scope(workspace_a), "s1", appdata)
    assert found is not None and found.session_id == "s1"


@pytest.mark.anyio
async def test_discover_current_history_rejects_appdata_history_owned_by_other_workspace(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    (histories / "shared.jsonl").write_text("{}\n", encoding="utf-8")
    (appdata / "state").mkdir()
    (appdata / "state" / "latest.json").write_text(
        json.dumps({"sessions": [{"id": "shared", "workspace": str(workspace_a)}]}), encoding="utf-8"
    )

    found = await discover_current_history(workspace_scope(workspace_b), "shared", appdata)

    assert found is None


@pytest.mark.anyio
async def test_discover_current_history_does_not_use_legacy_state_for_appdata_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    workspace = tmp_path / "workspace"
    (histories / "shared.jsonl").write_text("{}\n", encoding="utf-8")
    legacy = tmp_path / "state"
    legacy.mkdir()
    (legacy / "latest.json").write_text(
        json.dumps({"sessions": [{"id": "shared", "workspace": str(workspace)}]}), encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    found = await discover_current_history(workspace_scope(workspace), "shared", appdata)

    assert found is None


@pytest.mark.anyio
async def test_discover_current_history_rejects_ambiguous_appdata_session_ownership(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    (histories / "shared.jsonl").write_text("{}\n", encoding="utf-8")
    (appdata / "state").mkdir()
    (appdata / "state" / "latest.json").write_text(
        json.dumps(
            {
                "sessions": [
                    {"id": "shared", "workspace": str(workspace_a)},
                    {"id": "shared", "workspace": str(workspace_b)},
                ]
            }
        ),
        encoding="utf-8",
    )

    assert await discover_current_history(workspace_scope(workspace_a), "shared", appdata) is None
    assert await discover_current_history(workspace_scope(workspace_b), "shared", appdata) is None


@pytest.mark.anyio
async def test_discover_current_history_accepts_trusted_standalone_commit_path(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    history = histories / "standalone.jsonl"
    history.write_text("{}\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    found = await discover_current_history(
        workspace_scope(workspace), "standalone", appdata, committed_path=str(history)
    )

    assert found == HistorySource("standalone", history)


@pytest.mark.anyio
async def test_discover_current_history_rejects_untrusted_commit_path(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    (appdata / "histories").mkdir(parents=True)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("{}\n", encoding="utf-8")
    workspace = tmp_path / "workspace"

    found = await discover_current_history(
        workspace_scope(workspace), "standalone", appdata, committed_path=str(outside)
    )

    assert found is None


@pytest.mark.anyio
async def test_discover_current_history_rejects_unsafe_session_id(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    (appdata / "histories").mkdir(parents=True)
    (appdata / "outside.jsonl").write_text("{}\n", encoding="utf-8")
    (appdata / "state").mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (appdata / "state" / "latest.json").write_text(
        json.dumps({"sessions": [{"id": "../outside", "workspace": str(workspace)}]}),
        encoding="utf-8",
    )

    found = await discover_current_history(workspace_scope(workspace), "../outside", appdata)

    assert found is None


@pytest.mark.anyio
async def test_discover_current_history_rejects_cross_workspace_symlink(tmp_path: Path) -> None:
    appdata = tmp_path / "appdata"
    (appdata / "histories").mkdir(parents=True)
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    histories_a = workspace_a / "histories"
    histories_b = workspace_b / "histories"
    histories_a.mkdir(parents=True)
    histories_b.mkdir(parents=True)
    target = histories_b / "current.jsonl"
    target.write_text("{}\n", encoding="utf-8")
    (histories_a / "current.jsonl").symlink_to(target)

    found = await discover_current_history(workspace_scope(workspace_a), "current", appdata)

    assert found is None
