from __future__ import annotations

# SQL statements are kept close to their owning operation for auditability.
# ruff: noqa: E501
import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .journal import EvidenceSpan, JsonlJournal, MemoryPromotion, ReplayReport, ScopeClear, span_to_record

SQLITE_SCHEMA_VERSION = 2
BUSINESS_TABLES = frozenset({"evidence_spans", "memory_items", "summary_cards", "ingest_checkpoints"})
FTS_TABLES = frozenset(
    {
        "fts_memory",
        "fts_memory_config",
        "fts_memory_content",
        "fts_memory_data",
        "fts_memory_docsize",
        "fts_memory_idx",
    }
)
SCHEMA_TABLES = BUSINESS_TABLES | FTS_TABLES
ALLOWED_SCHEMA_TABLES = SCHEMA_TABLES | {"sqlite_sequence"}


@dataclass(frozen=True, slots=True)
class StoredCandidate:
    doc_type: str
    doc_id: str
    workspace_id: str
    text: str
    source_span_ids: tuple[str, ...]
    timestamp: str | None
    score: float


@dataclass(frozen=True, slots=True)
class MemoryItem:
    item_id: str
    workspace_id: str
    kind: str
    text: str
    confidence: float
    salience: float
    source_span_ids: tuple[str, ...]
    model: str | None
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class IngestCheckpoint:
    workspace_id: str
    history_path: str
    session_id: str
    confirmed_line_count: int
    prefix_hash: str
    file_size: int
    mtime_ns: int
    extraction_line: int = 0
    embedding_line: int = 0
    card_line: int = 0
    updated_at: str = ""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _parse_ids(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        raise ValueError("source_span_ids must be a JSON list")
    return tuple(str(item) for item in parsed)


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = sum(a * a for a in left) ** 0.5
    norm_right = sum(b * b for b in right) ** 0.5
    return dot / (norm_left * norm_right) if norm_left and norm_right else 0.0


def _turn_card_id(turn_id: str) -> str:
    return hashlib.sha256(f"turn:{turn_id}".encode()).hexdigest()


class MemoryStore:
    """Workspace-scoped SQLite projection over the authoritative JSONL journal."""

    def __init__(self, database_path: str | os.PathLike[str], journal: JsonlJournal, workspace_id: str) -> None:
        self.database_path = Path(database_path)
        self.journal = journal
        self.workspace_id = workspace_id
        self.connection: sqlite3.Connection | None = None
        self._closed = False

    def open(self) -> MemoryStore:
        if self.connection is not None and not self._closed:
            return self
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.connection = sqlite3.connect(str(self.database_path), timeout=5.0, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self._prepare_schema()
        except sqlite3.DatabaseError as exc:
            self._close_connection()
            if not self._is_corrupt(exc):
                raise
            self._quarantine("corrupt")
            self.connection = sqlite3.connect(str(self.database_path), timeout=5.0, check_same_thread=False)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA busy_timeout=5000")
            self.connection.execute("PRAGMA journal_mode=WAL")
            self.connection.execute("PRAGMA synchronous=NORMAL")
            self._create_schema()
        self._closed = False
        self.replay_journal()
        return self

    @property
    def conn(self) -> sqlite3.Connection:
        if self.connection is None or self._closed:
            raise RuntimeError("SQLite connection is closed")
        return self.connection

    def _close_connection(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            finally:
                self.connection = None

    @staticmethod
    def _is_corrupt(exc: sqlite3.DatabaseError) -> bool:
        message = str(exc).lower()
        return any(
            token in message for token in ("not a database", "malformed", "encrypted", "unsupported file format")
        )

    def _quarantine(self, kind: str) -> Path:
        suffix = uuid.uuid4().hex[:12]
        target = self.database_path.with_name(f"{self.database_path.name}.{kind}-{suffix}")
        os.replace(self.database_path, target)
        for sidecar in (Path(f"{self.database_path}-wal"), Path(f"{self.database_path}-shm")):
            if sidecar.exists():
                os.replace(sidecar, target.with_name(target.name + sidecar.name[len(self.database_path.name) :]))
        return target

    def _prepare_schema(self) -> None:
        version = int(self.conn.execute("PRAGMA user_version").fetchone()[0])
        names = {
            str(row[0])
            for row in self.conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','virtual table')")
            if row[0] is not None
        }
        incompatible = version not in (0, SQLITE_SCHEMA_VERSION) or (
            version == SQLITE_SCHEMA_VERSION and (not names >= SCHEMA_TABLES or bool(names - ALLOWED_SCHEMA_TABLES))
        )
        if version == 0 and names and names - {"sqlite_sequence"}:
            incompatible = True
        if incompatible:
            self._migrate_legacy()
            return
        self._create_schema()

    def _migrate_legacy(self) -> None:
        self.conn.commit()
        timestamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
        backup = self.database_path.with_name(f"{self.database_path.name}.legacy-{timestamp}-{uuid.uuid4().hex[:8]}")
        try:
            target = sqlite3.connect(str(backup))
            self.conn.backup(target)
            target.close()
        except sqlite3.DatabaseError:
            shutil.copyfile(self.database_path, backup)
        self._close_connection()
        os.replace(self.database_path, backup.with_name(backup.name + ".active"))
        for suffix in ("-wal", "-shm"):
            sidecar = Path(f"{self.database_path}{suffix}")
            if sidecar.exists():
                os.replace(sidecar, Path(f"{backup.with_name(backup.name + '.active')}{suffix}"))
        journal_backup = self.journal.path.with_name(
            f"{self.journal.path.name}.legacy-{timestamp}-{uuid.uuid4().hex[:8]}"
        )
        if self.journal.path.exists():
            shutil.copyfile(self.journal.path, journal_backup)
        self.connection = sqlite3.connect(str(self.database_path), timeout=5.0, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self._create_schema()

    def _create_schema(self) -> None:
        self.conn.executescript(
            """
            create table if not exists evidence_spans (
              span_id text primary key,
              workspace_id text not null,
              session_id text not null,
              turn_id text not null,
              line_no integer not null,
              speaker text not null check (speaker in ('user','assistant')),
              content text not null,
              content_hash text not null,
              timestamp text,
              source_uri text not null,
              embedding_json text,
              embedding_model text,
              embedded_at text
            );
            create table if not exists memory_items (
              item_id text primary key,
              workspace_id text not null,
              kind text not null,
              text text not null,
              confidence real not null,
              salience real not null,
              source_span_ids text not null,
              embedding_json text,
              embedding_model text,
              model text,
              schema_version integer not null,
              created_at text not null,
              updated_at text not null
            );
            create table if not exists summary_cards (
              card_id text primary key,
              workspace_id text not null,
              retrieval_key text not null,
              snippet text not null,
              source_span_ids text not null,
              updated_at text not null
            );
            create table if not exists ingest_checkpoints (
              workspace_id text not null,
              history_path text not null,
              session_id text not null,
              confirmed_line_count integer not null,
              prefix_hash text not null,
              file_size integer not null,
              mtime_ns integer not null,
              extraction_line integer not null default 0,
              embedding_line integer not null default 0,
              card_line integer not null default 0,
              updated_at text not null,
              primary key (workspace_id, history_path)
            );
            create index if not exists evidence_workspace_idx on evidence_spans(workspace_id);
            create index if not exists memory_workspace_idx on memory_items(workspace_id);
            create virtual table if not exists fts_memory using fts5(
              doc_type unindexed, doc_id unindexed, workspace_id unindexed, text,
              tokenize='trigram'
            );
            pragma user_version = 2;
            """
        )
        self.conn.commit()

    def _span_row(self, span: EvidenceSpan) -> tuple[object, ...]:
        return (
            span.span_id,
            span.workspace_id,
            span.session_id,
            span.turn_id,
            span.line_no,
            span.speaker,
            span.content,
            span.content_hash,
            span.timestamp,
            span.source_uri,
        )

    def _row_span(self, row: sqlite3.Row) -> EvidenceSpan:
        return EvidenceSpan(
            span_id=row["span_id"],
            workspace_id=row["workspace_id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            line_no=row["line_no"],
            speaker=row["speaker"],
            content=row["content"],
            content_hash=row["content_hash"],
            timestamp=row["timestamp"],
            source_uri=row["source_uri"],
        )

    def _insert_span(self, span: EvidenceSpan, *, replace_conflict: bool = False) -> bool:
        existing = self.conn.execute("select * from evidence_spans where span_id = ?", (span.span_id,)).fetchone()
        if existing is not None:
            if span_to_record(self._row_span(existing)) != span_to_record(span):
                if not replace_conflict:
                    raise ValueError(f"conflicting index row for span_id={span.span_id}")
                self.conn.execute("delete from evidence_spans where span_id = ?", (span.span_id,))
                self.conn.execute("delete from fts_memory where doc_type = 'evidence' and doc_id = ?", (span.span_id,))
            else:
                return False
        self.conn.execute(
            """insert into evidence_spans
            (span_id,workspace_id,session_id,turn_id,line_no,speaker,content,content_hash,timestamp,source_uri)
            values (?,?,?,?,?,?,?,?,?,?)""",
            self._span_row(span),
        )
        self.conn.execute(
            "insert into fts_memory(doc_type,doc_id,workspace_id,text) values ('evidence',?,?,?)",
            (span.span_id, span.workspace_id, span.content),
        )
        return True

    def index_spans(self, spans: Iterable[EvidenceSpan]) -> list[EvidenceSpan]:
        batch = list(spans)
        if not batch:
            return []
        for span in batch:
            if span.workspace_id != self.workspace_id:
                raise ValueError("span workspace does not match store scope")
        new = self.journal.append_spans(batch)
        active = self.journal.active_spans(batch)
        self.conn.execute("begin")
        try:
            for span in active:
                self._insert_span(span)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return new

    def _delete_derived_referencing(self, span_ids: Iterable[str]) -> None:
        targets = set(span_ids)
        if not targets:
            return
        for table, id_column, doc_type in (
            ("memory_items", "item_id", "memory_item"),
            ("summary_cards", "card_id", "summary_card"),
        ):
            derived_ids = [
                str(row[0])
                for row in self.conn.execute(
                    f"select {id_column}, source_span_ids from {table} where workspace_id = ?",
                    (self.workspace_id,),
                )
                if targets.intersection(_parse_ids(row[1]))
            ]
            self.conn.executemany(f"delete from {table} where {id_column} = ?", ((item,) for item in derived_ids))
            self.conn.executemany(
                "delete from fts_memory where doc_type = ? and doc_id = ?",
                ((doc_type, item) for item in derived_ids),
            )

    def _delete_spans(self, span_ids: Iterable[str]) -> None:
        ids = tuple(dict.fromkeys(span_ids))
        if not ids:
            return
        self._delete_derived_referencing(ids)
        self.conn.executemany("delete from evidence_spans where span_id = ?", ((item,) for item in ids))
        self.conn.executemany(
            "delete from fts_memory where doc_type = 'evidence' and doc_id = ?", ((item,) for item in ids)
        )

    def _replay_journal_in_transaction(self) -> ReplayReport:
        inserted = 0
        duplicates = 0
        clears = 0
        replay_active: dict[str, EvidenceSpan] = {}

        def on_span(span: EvidenceSpan) -> None:
            nonlocal inserted, duplicates
            if span.workspace_id != self.workspace_id:
                return
            replay_active[span.span_id] = span
            if self._insert_span(span, replace_conflict=True):
                inserted += 1
            else:
                duplicates += 1

        def on_clear(clear: ScopeClear) -> None:
            nonlocal clears
            if clear.workspace_id != self.workspace_id:
                return
            ids = [
                span_id
                for span_id, span in replay_active.items()
                if clear.session_id is None or span.session_id == clear.session_id
            ]
            self._delete_spans(ids)
            for span_id in ids:
                del replay_active[span_id]
            clears += 1

        def on_promotion(promotion: MemoryPromotion) -> None:
            if promotion.workspace_id != self.workspace_id:
                return
            try:
                item = self._manual_item(
                    promotion.workspace_id,
                    promotion.source_span_ids,
                    promotion.kind,
                    promotion.salience,
                )
            except ValueError:
                return
            self._upsert_memory_items_in_transaction(self.workspace_id, [item])

        report = self.journal.replay(on_span, on_clear, on_promotion)
        stale_ids = [
            str(row[0])
            for row in self.conn.execute(
                "select span_id from evidence_spans where workspace_id = ?", (self.workspace_id,)
            )
            if str(row[0]) not in replay_active
        ]
        self._delete_spans(stale_ids)
        self.conn.execute(
            """
            delete from fts_memory
            where workspace_id = ? and (
              (doc_type = 'evidence' and not exists (
                select 1 from evidence_spans where span_id = fts_memory.doc_id
              ))
              or (doc_type = 'memory_item' and not exists (
                select 1 from memory_items where item_id = fts_memory.doc_id
              ))
              or (doc_type = 'summary_card' and not exists (
                select 1 from summary_cards where card_id = fts_memory.doc_id
              ))
            )
            """,
            (self.workspace_id,),
        )
        return ReplayReport(
            records=report.records,
            inserted=inserted,
            duplicates=duplicates,
            scope_clears=clears,
            skipped_records=report.skipped_records,
            skipped_tail=report.skipped_tail,
        )

    def replay_journal(self) -> ReplayReport:
        self.conn.execute("begin")
        try:
            report = self._replay_journal_in_transaction()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return report

    def rebuild_index(self) -> ReplayReport:
        self.conn.execute("begin")
        try:
            self.conn.execute("delete from evidence_spans where workspace_id = ?", (self.workspace_id,))
            self.conn.execute("delete from memory_items where workspace_id = ?", (self.workspace_id,))
            self.conn.execute("delete from summary_cards where workspace_id = ?", (self.workspace_id,))
            self.conn.execute("delete from fts_memory where workspace_id = ?", (self.workspace_id,))
            self.conn.execute(
                "update ingest_checkpoints set extraction_line = 0, embedding_line = 0, card_line = 0, updated_at = ? "
                "where workspace_id = ?",
                (_now(), self.workspace_id),
            )
            report = self._replay_journal_in_transaction()
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return report

    def _candidate(self, row: sqlite3.Row, score: float) -> StoredCandidate:
        return StoredCandidate(
            doc_type=row["doc_type"],
            doc_id=row["doc_id"],
            workspace_id=row["workspace_id"],
            text=row["text"],
            source_span_ids=_parse_ids(row["source_span_ids"]),
            timestamp=row["timestamp"],
            score=score,
        )

    def _doc_row(self, doc_type: str, doc_id: str) -> sqlite3.Row | None:
        if doc_type == "evidence":
            return self.conn.execute(
                "select 'evidence' doc_type, span_id doc_id, workspace_id, content text, json_array(span_id) source_span_ids, timestamp from evidence_spans where span_id = ?",
                (doc_id,),
            ).fetchone()
        if doc_type == "memory_item":
            return self.conn.execute(
                "select 'memory_item' doc_type, item_id doc_id, workspace_id, text, source_span_ids, null timestamp from memory_items where item_id = ?",
                (doc_id,),
            ).fetchone()
        if doc_type == "summary_card":
            return self.conn.execute(
                "select 'summary_card' doc_type, card_id doc_id, workspace_id, snippet text, source_span_ids, updated_at timestamp from summary_cards where card_id = ?",
                (doc_id,),
            ).fetchone()
        return None

    def search_fts(self, query: str, workspace_id: str, limit: int = 20) -> list[StoredCandidate]:
        if workspace_id != self.workspace_id or not query.strip():
            return []
        try:
            if len(query.strip()) < 3:
                raise sqlite3.OperationalError("trigram query too short")
            rows = self.conn.execute(
                "select doc_type, doc_id, workspace_id, text, bm25(fts_memory) score from fts_memory where fts_memory match ? and workspace_id = ? order by score limit ?",
                (query, workspace_id, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = self.conn.execute(
                "select doc_type, doc_id, workspace_id, text, 0.0 score from fts_memory where workspace_id = ? and text like ? limit ?",
                (workspace_id, f"%{query}%", limit),
            ).fetchall()
        results: list[StoredCandidate] = []
        for row in rows:
            source = self._doc_row(row["doc_type"], row["doc_id"])
            if source is not None:
                results.append(self._candidate(source, float(row["score"])))
        return results

    def search_dense(self, query_vector: Sequence[float], workspace_id: str, limit: int = 20) -> list[StoredCandidate]:
        if workspace_id != self.workspace_id:
            return []
        candidates: list[StoredCandidate] = []
        for row in self.conn.execute(
            "select 'evidence' doc_type, span_id doc_id, workspace_id, content text, json_array(span_id) source_span_ids, timestamp, embedding_json from evidence_spans where workspace_id = ? and embedding_json is not null",
            (workspace_id,),
        ):
            score = _cosine(query_vector, json.loads(row["embedding_json"]))
            candidates.append(self._candidate(row, score))
        for row in self.conn.execute(
            "select 'memory_item' doc_type, item_id doc_id, workspace_id, text, source_span_ids, null timestamp, embedding_json from memory_items where workspace_id = ? and embedding_json is not null",
            (workspace_id,),
        ):
            score = _cosine(query_vector, json.loads(row["embedding_json"]))
            candidates.append(self._candidate(row, score))
        candidates.sort(key=lambda item: (-item.score, item.doc_id))
        return candidates[:limit]

    def get_source_spans(self, workspace_id: str, source_span_ids: Iterable[str]) -> list[EvidenceSpan]:
        if workspace_id != self.workspace_id:
            return []
        ids = list(dict.fromkeys(source_span_ids))
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            f"select * from evidence_spans where workspace_id = ? and span_id in ({placeholders})", (workspace_id, *ids)
        ).fetchall()
        by_id = {row["span_id"]: self._row_span(row) for row in rows}
        return [by_id[item] for item in ids if item in by_id]

    def _manual_item(self, workspace_id: str, source_span_ids: Iterable[str], kind: str, salience: float) -> MemoryItem:
        if workspace_id != self.workspace_id:
            raise ValueError("workspace does not match store scope")
        ids = tuple(dict.fromkeys(str(item) for item in source_span_ids))
        if not ids or any(not item for item in ids):
            raise ValueError("source_span_ids must not be empty")
        spans = self.get_source_spans(workspace_id, ids)
        if len(spans) != len(ids):
            raise ValueError("all source spans must belong to the current workspace")
        spans.sort(key=lambda item: (item.turn_id, item.line_no, item.span_id))
        text = "\n".join(item.content for item in spans)
        canonical_ids = tuple(item.span_id for item in spans)
        item_id = hashlib.sha256(_json({"kind": kind, "source_span_ids": canonical_ids}).encode()).hexdigest()
        return MemoryItem(item_id, workspace_id, kind, text, 1.0, float(salience), canonical_ids, None)

    def promote(self, workspace_id: str, source_span_ids: Iterable[str], kind: str, salience: float) -> MemoryItem:
        item = self._manual_item(workspace_id, source_span_ids, kind, salience)
        self.journal.append_promotion(workspace_id, item.source_span_ids, kind, salience)
        self.upsert_memory_items(workspace_id, [item])
        return item

    def _upsert_memory_items_in_transaction(self, workspace_id: str, items: Iterable[MemoryItem]) -> None:
        if workspace_id != self.workspace_id:
            raise ValueError("workspace does not match store scope")
        for item in items:
            if item.workspace_id != workspace_id:
                raise ValueError("memory item workspace does not match store scope")
            ids = tuple(dict.fromkeys(item.source_span_ids))
            if not ids or len(self.get_source_spans(workspace_id, ids)) != len(ids):
                raise ValueError("memory item source spans are missing or cross-workspace")
            now = _now()
            existing = self.conn.execute(
                "select created_at from memory_items where item_id = ?", (item.item_id,)
            ).fetchone()
            created = existing[0] if existing else now
            self.conn.execute(
                """insert into memory_items(item_id,workspace_id,kind,text,confidence,salience,source_span_ids,model,schema_version,created_at,updated_at)
                values (?,?,?,?,?,?,?,?,?,?,?) on conflict(item_id) do update set workspace_id=excluded.workspace_id,kind=excluded.kind,text=excluded.text,confidence=excluded.confidence,salience=excluded.salience,source_span_ids=excluded.source_span_ids,model=excluded.model,schema_version=excluded.schema_version,updated_at=excluded.updated_at""",
                (
                    item.item_id,
                    workspace_id,
                    item.kind,
                    item.text,
                    item.confidence,
                    item.salience,
                    _json(ids),
                    item.model,
                    item.schema_version,
                    created,
                    now,
                ),
            )
            self.conn.execute("delete from fts_memory where doc_type='memory_item' and doc_id=?", (item.item_id,))
            self.conn.execute(
                "insert into fts_memory(doc_type,doc_id,workspace_id,text) values ('memory_item',?,?,?)",
                (item.item_id, workspace_id, item.text),
            )

    def upsert_memory_items(self, workspace_id: str, items: Iterable[MemoryItem]) -> None:
        batch = list(items)
        self.conn.execute("begin")
        try:
            self._upsert_memory_items_in_transaction(workspace_id, batch)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def upsert_turn_card(
        self,
        workspace_id: str,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        source_span_ids: Iterable[str],
    ) -> str:
        if workspace_id != self.workspace_id:
            raise ValueError("workspace does not match store scope")
        ids = tuple(dict.fromkeys(source_span_ids))
        if not ids or len(self.get_source_spans(workspace_id, ids)) != len(ids):
            raise ValueError("card source spans are missing or cross-workspace")
        card_id = _turn_card_id(turn_id)
        retrieval_key = user_text[:240]
        snippet = assistant_text[:500]
        self.conn.execute("begin")
        try:
            self.conn.execute(
                "insert into summary_cards(card_id,workspace_id,retrieval_key,snippet,source_span_ids,updated_at) values (?,?,?,?,?,?) on conflict(card_id) do update set workspace_id=excluded.workspace_id,retrieval_key=excluded.retrieval_key,snippet=excluded.snippet,source_span_ids=excluded.source_span_ids,updated_at=excluded.updated_at",
                (card_id, workspace_id, retrieval_key, snippet, _json(ids), _now()),
            )
            self.conn.execute("delete from fts_memory where doc_type='summary_card' and doc_id=?", (card_id,))
            self.conn.execute(
                "insert into fts_memory(doc_type,doc_id,workspace_id,text) values ('summary_card',?,?,?)",
                (card_id, workspace_id, f"{retrieval_key}\n{snippet}"),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return card_id

    def missing_turn_cards(self, workspace_id: str, turn_ids: Iterable[str]) -> list[str]:
        if workspace_id != self.workspace_id:
            return []
        missing: list[str] = []
        for turn_id in dict.fromkeys(turn_ids):
            row = self.conn.execute(
                "select 1 from summary_cards where workspace_id=? and card_id=?",
                (workspace_id, _turn_card_id(turn_id)),
            ).fetchone()
            if row is None:
                missing.append(turn_id)
        return missing

    def pending_session_turns(
        self,
        workspace_id: str,
        session_id: str,
        after_line: int,
        limit: int,
    ) -> list[list[EvidenceSpan]]:
        if workspace_id != self.workspace_id or limit <= 0:
            return []
        rows = self.conn.execute(
            "select * from evidence_spans where workspace_id = ? and session_id = ? and line_no > ? "
            "order by line_no, span_id limit ?",
            (workspace_id, session_id, after_line, limit * 2),
        ).fetchall()
        turns: dict[str, list[EvidenceSpan]] = {}
        for row in rows:
            span = self._row_span(row)
            turns.setdefault(span.turn_id, []).append(span)
        return [spans for spans in turns.values() if {span.speaker for span in spans} == {"user", "assistant"}][:limit]

    def pending_embeddings(self, workspace_id: str, limit: int = 100) -> list[tuple[str, str, str]]:
        if workspace_id != self.workspace_id:
            return []
        rows = self.conn.execute(
            "select 'evidence', span_id, content from evidence_spans where workspace_id=? and embedding_json is null order by rowid limit ?",
            (workspace_id, limit),
        ).fetchall()
        rows += self.conn.execute(
            "select 'memory_item', item_id, text from memory_items where workspace_id=? and embedding_json is null order by rowid limit ?",
            (workspace_id, limit),
        ).fetchall()
        return [(str(row[0]), str(row[1]), str(row[2])) for row in rows[:limit]]

    def write_embeddings(
        self, workspace_id: str, model: str, vectors_by_typed_id: Mapping[tuple[str, str], Sequence[float]]
    ) -> int:
        if workspace_id != self.workspace_id:
            return 0
        updated = 0
        self.conn.execute("begin")
        try:
            for (doc_type, doc_id), vector in vectors_by_typed_id.items():
                encoded = _json([float(value) for value in vector])
                if doc_type == "evidence":
                    cursor = self.conn.execute(
                        "update evidence_spans set embedding_json=?,embedding_model=?,embedded_at=? where span_id=? and workspace_id=?",
                        (encoded, model, _now(), doc_id, workspace_id),
                    )
                elif doc_type == "memory_item":
                    cursor = self.conn.execute(
                        "update memory_items set embedding_json=?,embedding_model=?,updated_at=? where item_id=? and workspace_id=?",
                        (encoded, model, _now(), doc_id, workspace_id),
                    )
                else:
                    continue
                updated += cursor.rowcount
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return updated

    def read_checkpoint(self, workspace_id: str, history_path: str) -> IngestCheckpoint | None:
        if workspace_id != self.workspace_id:
            return None
        row = self.conn.execute(
            "select * from ingest_checkpoints where workspace_id=? and history_path=?", (workspace_id, history_path)
        ).fetchone()
        if row is None:
            return None
        return IngestCheckpoint(**dict(row))

    def write_checkpoint(self, checkpoint: IngestCheckpoint) -> None:
        if checkpoint.workspace_id != self.workspace_id:
            raise ValueError("checkpoint workspace does not match store scope")
        self.conn.execute("begin")
        try:
            self.conn.execute(
                "insert into ingest_checkpoints(workspace_id,history_path,session_id,confirmed_line_count,prefix_hash,file_size,mtime_ns,extraction_line,embedding_line,card_line,updated_at) values (?,?,?,?,?,?,?,?,?,?,?) on conflict(workspace_id,history_path) do update set session_id=excluded.session_id,confirmed_line_count=excluded.confirmed_line_count,prefix_hash=excluded.prefix_hash,file_size=excluded.file_size,mtime_ns=excluded.mtime_ns,extraction_line=excluded.extraction_line,embedding_line=excluded.embedding_line,card_line=excluded.card_line,updated_at=excluded.updated_at",
                (
                    checkpoint.workspace_id,
                    checkpoint.history_path,
                    checkpoint.session_id,
                    checkpoint.confirmed_line_count,
                    checkpoint.prefix_hash,
                    checkpoint.file_size,
                    checkpoint.mtime_ns,
                    checkpoint.extraction_line,
                    checkpoint.embedding_line,
                    checkpoint.card_line,
                    checkpoint.updated_at or _now(),
                ),
            )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def backup_to(self, destination: str | os.PathLike[str]) -> Path:
        target_dir = Path(destination)
        if target_dir.suffix.lower() in {".db", ".sqlite", ".sqlite3"}:
            target_db = target_dir
            target_dir = target_db.parent
        else:
            target_db = target_dir / self.database_path.name
        target_dir.mkdir(parents=True, exist_ok=True)
        if target_db.resolve() == self.database_path.resolve():
            raise ValueError("backup destination must differ from source")
        self.conn.commit()
        destination_conn = sqlite3.connect(str(target_db))
        try:
            self.conn.backup(destination_conn)
        finally:
            destination_conn.close()
        self.journal.copy_to(target_dir / "evidence.jsonl")
        return target_db

    def close(self) -> None:
        if not self._closed:
            self._close_connection()
            self._closed = True

    def __enter__(self) -> MemoryStore:
        return self.open()

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.close()
