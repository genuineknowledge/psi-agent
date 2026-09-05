from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from agents.desktop.tools._fusion_memory.journal import (
        EvidenceSpan,
        JournalConflictError,
        JsonlJournal,
        canonical_json,
        span_to_record,
    )
else:
    from _fusion_memory.journal import (
        EvidenceSpan,
        JournalConflictError,
        JsonlJournal,
        canonical_json,
        span_to_record,
    )


def span(span_id: str = "span-1", content: str = "原始文本") -> EvidenceSpan:
    return EvidenceSpan(
        span_id,
        "workspace-a",
        "session-1",
        "turn-1",
        2,
        "assistant",
        content,
        hashlib.sha256(content.encode()).hexdigest(),
        "2026-09-03T12:00:00+00:00",
        "history:///session-1#L2",
    )


def test_append_is_canonical_utf8_idempotent_and_conflict_checked(tmp_path: Path) -> None:
    journal = JsonlJournal(tmp_path / "evidence.jsonl", fsync=False)
    assert journal.append_spans([span()]) == [span()]
    assert journal.append_spans([span()]) == []
    with pytest.raises(JournalConflictError):
        journal.append_spans([span(content="different")])
    records = [json.loads(line) for line in (tmp_path / "evidence.jsonl").read_text().splitlines()]
    assert records == [span_to_record(span())]
    assert (tmp_path / "evidence.jsonl").read_bytes() == canonical_json(span_to_record(span())) + b"\n"


def test_invalid_tail_is_copied_then_truncated_before_append(tmp_path: Path) -> None:
    path = tmp_path / "evidence.jsonl"
    path.write_bytes(b'{"record_type":"evidence_span"')
    journal = JsonlJournal(path, fsync=False)
    journal.append_spans([span("span-2")])
    assert [item.span_id for item in journal.iter_active_spans()] == ["span-2"]
    partials = list(tmp_path.glob("evidence.jsonl.partial-*"))
    assert len(partials) == 1 and partials[0].read_bytes() == b'{"record_type":"evidence_span"'


def test_complete_tail_gets_newline_and_tombstone_prevents_resurrection(tmp_path: Path) -> None:
    path = tmp_path / "evidence.jsonl"
    path.write_bytes(canonical_json(span_to_record(span())))
    journal = JsonlJournal(path, fsync=False)
    journal.append_scope_clear("workspace-a")
    assert list(journal.iter_active_spans()) == []
    assert path.read_bytes().endswith(b"\n")


def test_batch_preflight_does_not_append_on_conflict(tmp_path: Path) -> None:
    journal = JsonlJournal(tmp_path / "j.jsonl", fsync=False)
    journal.append_spans([span()])
    with pytest.raises(JournalConflictError):
        journal.append_spans([span("new"), span(content="different")])
    assert [s.span_id for s in journal.iter_active_spans()] == ["span-1"]


def test_initialization_rejects_conflicting_authority_records(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    path.write_bytes(
        canonical_json(span_to_record(span()))
        + b"\n"
        + canonical_json(span_to_record(span(content="different")))
        + b"\n"
    )

    with pytest.raises(JournalConflictError):
        JsonlJournal(path, fsync=False)


def test_incremental_append_does_not_read_the_whole_journal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    journal = JsonlJournal(tmp_path / "j.jsonl", fsync=False)
    journal.append_spans([span()])

    def reject_full_read(_path: Path) -> bytes:
        raise AssertionError("incremental append must inspect only the journal tail")

    monkeypatch.setattr(Path, "read_bytes", reject_full_read)
    assert journal.append_spans([span("span-2")]) == [span("span-2")]


def test_replay_skips_complete_malformed_lines_and_copy_preserves_bytes(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path, fsync=False)
    journal.append_spans([span()])
    with path.open("ab") as fh:
        fh.write(b'{"record_type":"unknown"}\n')
        fh.write(b"not-json\n")
    seen: list[str] = []
    report = journal.replay(lambda item: seen.append(item.span_id), lambda _: None)
    assert seen == ["span-1"]
    assert report.records == 3 and report.skipped_records == 2
    destination = tmp_path / "copy.jsonl"
    journal.copy_to(destination)
    assert destination.read_bytes() == path.read_bytes()


def test_replay_counts_incomplete_tail_separately_and_empty_copy_exists(tmp_path: Path) -> None:
    path = tmp_path / "j.jsonl"
    journal = JsonlJournal(path, fsync=False)
    empty_copy = tmp_path / "backup" / "evidence.jsonl"
    journal.copy_to(empty_copy)
    assert empty_copy.read_bytes() == b""
    journal.append_spans([span()])
    with path.open("ab") as handle:
        handle.write(b'{"record_type":"evidence_span"')
    report = journal.replay(lambda _: True, lambda _: None)
    assert report.records == 1
    assert report.inserted == 1
    assert report.skipped_records == 0
    assert report.skipped_tail == 1
