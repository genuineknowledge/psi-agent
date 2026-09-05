from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from agents.desktop.tools._fusion_memory.journal import EvidenceSpan, JsonlJournal, canonical_json, span_to_record
    from agents.desktop.tools._fusion_memory.store import IngestCheckpoint, MemoryItem, MemoryStore
else:
    from _fusion_memory.journal import EvidenceSpan, JsonlJournal, canonical_json, span_to_record
    from _fusion_memory.store import IngestCheckpoint, MemoryItem, MemoryStore


def make_span(
    span_id: str, workspace_id: str = "workspace-a", content: str = "原始文本", line_no: int = 1
) -> EvidenceSpan:
    return EvidenceSpan(
        span_id=span_id,
        workspace_id=workspace_id,
        session_id="session-1",
        turn_id="turn-1",
        line_no=line_no,
        speaker="assistant",
        content=content,
        content_hash=hashlib.sha256(content.encode()).hexdigest(),
        timestamp=None,
        source_uri="history:///session-1#L1",
    )


def opened(tmp_path: Path, workspace: str = "workspace-a") -> tuple[JsonlJournal, MemoryStore]:
    journal = JsonlJournal(tmp_path / "evidence.jsonl", fsync=False)
    store = MemoryStore(tmp_path / "memory.sqlite3", journal, workspace).open()
    return journal, store


def test_schema_is_minimal_fts5_wal_and_rebuildable(tmp_path: Path) -> None:
    journal, store = opened(tmp_path)
    journal.append_spans([make_span("span-1")])
    store.replay_journal()
    names = {row[0] for row in store.conn.execute("select name from sqlite_master")}
    assert {"evidence_spans", "memory_items", "summary_cards", "ingest_checkpoints"} <= names
    assert "fts_memory" in names
    assert not {"fact_relations", "event_edges", "entities", "current_views", "entity_profiles"} & names
    assert store.conn.execute("pragma journal_mode").fetchone()[0] == "wal"
    store.conn.execute("delete from evidence_spans")
    store.conn.execute("delete from fts_memory")
    store.conn.commit()
    assert store.rebuild_index().inserted == 1
    assert [row.doc_id for row in store.search_fts("原始", "workspace-a", 5)] == ["span-1"]
    store.close()


def test_index_writes_journal_before_sqlite_and_enforces_scope(tmp_path: Path) -> None:
    _journal, store = opened(tmp_path)
    span = make_span("span-1")
    assert store.index_spans([span]) == [span]
    assert len((tmp_path / "evidence.jsonl").read_bytes().splitlines()) == 1
    with pytest.raises(ValueError):
        store.index_spans([make_span("foreign", "workspace-b")])
    store.close()


def test_incremental_index_does_not_replay_the_full_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal, store = opened(tmp_path)

    def reject_replay():
        raise AssertionError("incremental indexing must not replay the authority")

    monkeypatch.setattr(journal, "iter_active_spans", reject_replay)
    assert store.index_spans([]) == []
    span = make_span("span-1")
    assert store.index_spans([span]) == [span]
    assert store.get_source_spans("workspace-a", ["span-1"]) == [span]
    store.close()


def test_retry_projects_active_span_after_sqlite_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal, store = opened(tmp_path)
    span = make_span("span-1")
    original = store._insert_span

    def fail_once(_span, *, replace_conflict=False):
        raise sqlite3.OperationalError("forced write failure")

    monkeypatch.setattr(store, "_insert_span", fail_once)
    with pytest.raises(sqlite3.OperationalError):
        store.index_spans([span])
    assert len(list(journal.iter_active_spans())) == 1

    monkeypatch.setattr(store, "_insert_span", original)
    assert store.index_spans([span]) == []
    assert store.get_source_spans("workspace-a", [span.span_id]) == [span]
    store.close()


def test_replay_repairs_conflicting_projection_from_jsonl_authority(tmp_path: Path) -> None:
    journal, store = opened(tmp_path)
    span = make_span("span-1", content="authority")
    store.index_spans([span])
    store.conn.execute(
        "update evidence_spans set content='tampered', content_hash='tampered' where span_id=?",
        (span.span_id,),
    )
    store.conn.execute("update fts_memory set text='tampered' where doc_id=?", (span.span_id,))
    store.conn.commit()

    store.replay_journal()

    assert store.get_source_spans("workspace-a", [span.span_id]) == [span]
    assert [item.doc_id for item in store.search_fts("authority", "workspace-a")] == [span.span_id]
    assert store.search_fts("tampered", "workspace-a") == []
    journal.copy_to(tmp_path / "authority-copy.jsonl")
    store.close()


def test_replay_removes_sqlite_only_evidence_and_fts_rows(tmp_path: Path) -> None:
    journal, store = opened(tmp_path)
    authority = make_span("authority", content="authority")
    stale = make_span("stale", content="sqlite only")
    store.index_spans([authority])
    store._insert_span(stale)
    store.conn.execute(
        "insert into fts_memory(doc_type, doc_id, workspace_id, text) values ('memory_item', 'ghost', ?, 'ghost')",
        ("workspace-a",),
    )
    store.conn.commit()

    store.replay_journal()

    assert store.get_source_spans("workspace-a", ["authority"]) == [authority]
    assert store.get_source_spans("workspace-a", ["stale"]) == []
    assert store.search_fts("sqlite", "workspace-a") == []
    assert store.search_fts("ghost", "workspace-a") == []
    journal.copy_to(tmp_path / "authority-copy.jsonl")
    store.close()


def test_promote_card_embeddings_and_checkpoint_round_trip(tmp_path: Path) -> None:
    _, store = opened(tmp_path)
    store.index_spans([make_span("u", content="用户偏好", line_no=1), make_span("a", content="助手记住", line_no=2)])
    item = store.promote("workspace-a", ["a", "u"], "preference", 0.7)
    same_item = store.promote("workspace-a", ["u", "a"], "preference", 0.7)
    assert item.text == "用户偏好\n助手记住"
    assert same_item.item_id == item.item_id
    assert store.upsert_turn_card("workspace-a", "turn-1", "用户偏好", "助手记住", ["u", "a"])
    assert {row[0] for row in store.conn.execute("select doc_type from fts_memory")} == {
        "evidence",
        "memory_item",
        "summary_card",
    }
    pending = store.pending_embeddings("workspace-a", 10)
    assert {row[0] for row in pending} == {"evidence", "memory_item"}
    assert store.write_embeddings("workspace-a", "text-embedding-v4", {("evidence", "u"): [1.0, 0.0]}) == 1
    checkpoint = IngestCheckpoint(
        workspace_id="workspace-a",
        history_path="/history.jsonl",
        session_id="session-1",
        confirmed_line_count=2,
        prefix_hash="abc",
        file_size=42,
        mtime_ns=9,
        extraction_line=2,
        embedding_line=0,
        card_line=2,
        updated_at="2026-09-03T12:00:00+00:00",
    )
    store.write_checkpoint(checkpoint)
    assert store.read_checkpoint("workspace-a", "/history.jsonl") == checkpoint
    store.close()


def test_turn_card_failure_rolls_back_shared_connection(tmp_path: Path) -> None:
    _, store = opened(tmp_path)
    store.index_spans([make_span("u", line_no=1), make_span("a", line_no=2)])
    store.conn.execute(
        "create trigger reject_card before insert on summary_cards "
        "begin select raise(abort, 'forced card failure'); end"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert_turn_card("workspace-a", "turn-1", "user", "assistant", ["u", "a"])
    assert not store.conn.in_transaction
    store.conn.execute("drop trigger reject_card")
    store.index_spans([make_span("after-card")])
    store.close()


def test_checkpoint_failure_rolls_back_shared_connection(tmp_path: Path) -> None:
    _, store = opened(tmp_path)
    checkpoint = IngestCheckpoint("workspace-a", "/history.jsonl", "session-1", 2, "abc", 42, 9)
    store.conn.execute(
        "create trigger reject_checkpoint before insert on ingest_checkpoints "
        "begin select raise(abort, 'forced checkpoint failure'); end"
    )
    with pytest.raises(sqlite3.IntegrityError):
        store.write_checkpoint(checkpoint)
    assert not store.conn.in_transaction
    store.conn.execute("drop trigger reject_checkpoint")
    store.index_spans([make_span("after-checkpoint")])
    store.close()


def test_scope_clear_replay_and_source_boundary(tmp_path: Path) -> None:
    journal, store = opened(tmp_path)
    store.index_spans([make_span("local")])
    store.promote("workspace-a", ["local"], "fact", 1.0)
    store.upsert_turn_card("workspace-a", "turn-1", "user", "assistant", ["local"])
    journal.append_spans([make_span("foreign", "workspace-b")])
    journal.append_scope_clear("workspace-a")
    store.replay_journal()
    assert list(store.get_source_spans("workspace-a", ["local"])) == []
    assert store.conn.execute("select count(*) from memory_items").fetchone()[0] == 0
    assert store.conn.execute("select count(*) from summary_cards").fetchone()[0] == 0
    assert store.conn.execute("select count(*) from fts_memory").fetchone()[0] == 0
    with pytest.raises(ValueError):
        store.promote("workspace-a", ["foreign"], "fact", 1.0)
    store.close()


def test_failed_rebuild_preserves_active_index(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal, store = opened(tmp_path)
    span = make_span("local")
    store.index_spans([span])

    def fail_replay(*_args, **_kwargs):
        raise sqlite3.OperationalError("forced replay failure")

    monkeypatch.setattr(journal, "replay", fail_replay)
    with pytest.raises(sqlite3.OperationalError):
        store.rebuild_index()

    assert store.get_source_spans("workspace-a", [span.span_id]) == [span]
    store.close()


def test_rebuild_clears_derived_rows_and_resets_derivation_progress(tmp_path: Path) -> None:
    _, store = opened(tmp_path)
    span = make_span("local")
    store.index_spans([span])
    store.upsert_memory_items(
        "workspace-a",
        [MemoryItem("derived", "workspace-a", "fact", span.content, 1.0, 1.0, (span.span_id,), "model")],
    )
    store.upsert_turn_card("workspace-a", "turn-1", "user", "assistant", [span.span_id])
    checkpoint = IngestCheckpoint("workspace-a", "/history.jsonl", "session-1", 2, "abc", 42, 9, 2, 2, 2)
    store.write_checkpoint(checkpoint)

    store.rebuild_index()

    assert store.conn.execute("select count(*) from memory_items").fetchone()[0] == 0
    assert store.conn.execute("select count(*) from summary_cards").fetchone()[0] == 0
    restored = store.read_checkpoint("workspace-a", "/history.jsonl")
    assert restored is not None
    assert restored.extraction_line == 0
    assert restored.embedding_line == 0
    assert restored.card_line == 0
    store.close()


def test_manual_promotion_survives_index_rebuild(tmp_path: Path) -> None:
    _, store = opened(tmp_path)
    source = make_span("source", content="durable preference")
    store.index_spans([source])
    promoted = store.promote("workspace-a", [source.span_id], "preference", 0.8)

    store.rebuild_index()

    row = store.conn.execute(
        "select kind, text, salience, source_span_ids, model from memory_items where item_id = ?",
        (promoted.item_id,),
    ).fetchone()
    assert tuple(row) == ("preference", "durable preference", 0.8, '["source"]', None)
    assert {candidate.doc_id for candidate in store.search_fts("durable", "workspace-a")} == {
        promoted.item_id,
        source.span_id,
    }
    store.close()


def test_manual_promotion_journal_reopens(tmp_path: Path) -> None:
    _, store = opened(tmp_path)
    source = make_span("source", content="persistent preference")
    store.index_spans([source])
    promoted = store.promote("workspace-a", [source.span_id], "preference", 0.9)
    store.close()

    _, reopened = opened(tmp_path)

    row = reopened.conn.execute("select text from memory_items where item_id = ?", (promoted.item_id,)).fetchone()
    assert tuple(row) == ("persistent preference",)
    reopened.close()


def test_corrupt_sqlite_is_quarantined_and_backup_is_paired(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    journal_path = tmp_path / "evidence.jsonl"
    journal_path.write_bytes(canonical_json(span_to_record(make_span("recover"))) + b"\n")
    db.write_bytes(b"not a database")
    journal = JsonlJournal(journal_path, fsync=False)
    store = MemoryStore(db, journal, "workspace-a").open()
    assert store.get_source_spans("workspace-a", ["recover"])
    assert list(tmp_path.glob("memory.sqlite3.corrupt-*"))
    backup = store.backup_to(tmp_path / "backup")
    assert backup.exists() and (tmp_path / "backup" / "evidence.jsonl").exists()
    store.close()


def test_legacy_database_and_journal_are_backed_up_without_row_conversion(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite3"
    journal_path = tmp_path / "evidence.jsonl"
    with sqlite3.connect(db) as conn:
        conn.execute("create table legacy_marker (value text)")
        conn.execute("insert into legacy_marker values ('must-not-convert')")
        conn.execute("pragma user_version = 37")
    journal_path.write_bytes(canonical_json(span_to_record(make_span("journal"))) + b"\n")
    store = MemoryStore(db, JsonlJournal(journal_path, fsync=False), "workspace-a").open()
    names = {row[0] for row in store.conn.execute("select name from sqlite_master")}
    assert "legacy_marker" not in names
    assert store.get_source_spans("workspace-a", ["journal"])
    assert list(tmp_path.glob("memory.sqlite3.legacy-*"))
    assert list(tmp_path.glob("evidence.jsonl.legacy-*"))
    store.close()


def test_version_one_database_with_unknown_table_is_migrated(tmp_path: Path) -> None:
    journal, store = opened(tmp_path)
    store.close()
    with sqlite3.connect(tmp_path / "memory.sqlite3") as connection:
        connection.execute("create table rogue_business_data (value text)")
        connection.execute("insert into rogue_business_data values ('do-not-keep-active')")
        connection.execute("pragma user_version = 1")

    migrated = MemoryStore(tmp_path / "memory.sqlite3", journal, "workspace-a").open()

    names = {row[0] for row in migrated.conn.execute("select name from sqlite_master where type='table'")}
    assert "rogue_business_data" not in names
    assert list(tmp_path.glob("memory.sqlite3.legacy-*"))
    migrated.close()


def test_current_schema_reopens_without_legacy_migration(tmp_path: Path) -> None:
    journal, store = opened(tmp_path)
    store.close()

    reopened = MemoryStore(tmp_path / "memory.sqlite3", journal, "workspace-a").open()

    assert reopened.conn.execute("pragma user_version").fetchone()[0] == 2
    assert list(tmp_path.glob("memory.sqlite3.legacy-*")) == []
    reopened.close()
