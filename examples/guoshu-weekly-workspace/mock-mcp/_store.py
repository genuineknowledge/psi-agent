"""Read-only query layer over the demo's MySQL mock store.

Every business rule from chapter 七 of the plan lives here, on the *service*
side of the MCP boundary -- the agent cannot bypass or misstate them.  The
formal-task filter (R-01: ``is_deleted = 0 AND workflow_status = 'published'``)
is applied by ``formal_task_clause`` and reported back in each result envelope
so the agent can cite the caliber it actually got.

Placeholders are pymysql's ``%(name)s`` form.  Values are always bound, never
interpolated -- the injection surface stays closed even though the agent cannot
reach this layer directly.
"""

# ruff: noqa: RUF001  中文口径文案里的全角标点是给模型看的字面量, 不能换成半角。
from __future__ import annotations

import os
import re
from typing import Any

import _db
import pymysql

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
"""Dates reach SQL as bound parameters, so this is a clarity guard, not the
injection defense: a malformed date would otherwise be silently coerced by MySQL
and answer a different question than the one asked."""

MAX_ROWS = 200
"""Hard cap on rows returned to the agent.

Chat context is the scarce resource here: an unbounded task dump would crowd
out the conversation it is meant to support.  Truncation is reported via
``has_more`` rather than silently applied.
"""

FORMAL_TASK_CALIBER = "is_deleted = 0 AND workflow_status = 'published'"

# Fields that must never cross the MCP boundary (chapter 7.2 of the plan).
BLOCKED_FIELDS = frozenset({"storage_path", "payload"})

# Key NAMES from payload may cross the boundary; key values may not.
#
# A field-name list is contract information -- it goes into the MCP contract
# document either way, and ["nextWork", "progressDate", "latestProgress"] reveals
# nothing about what anyone reported.  The values are business data: draft text
# may be unapproved wording that would be quoted as if it were final, so payload
# itself stays in BLOCKED_FIELDS.
#
# These columns are computed server-side via JSON_KEYS and JSON_EXTRACT ... IS
# NULL, so they carry names and existence only.  Releasing them is not releasing
# payload: _scrub still drops the column named payload, which is why a derived
# column must be named something else -- one of the three below.
PAYLOAD_KEY_COLUMNS = frozenset({"payload_keys", "payload_key_count", "has_payload"})

# Fields released only when the caller holds the matching permission.
SENSITIVE_FIELDS = frozenset({"review_comment", "opinion"})

SNAPSHOT_NOTE = "演示数据（weekly_mock 自建库），非集团真实周报"

DEFAULT_AS_OF = "2026-08-15"
"""Anchor used unless ``GUOSHU_AS_OF`` overrides it."""

AS_OF = os.environ.get("GUOSHU_AS_OF", "").strip() or DEFAULT_AS_OF
"""The snapshot's "today" -- every relative time window is measured from here.

Not ``CURDATE()``, and this is the whole point.  The data stops at
``progress_date`` 2026-08-01 while the machine's wall clock is well past it, so
answering "the last 30 days" from the real clock silently slides the window off
the data and returns a smaller count than the truth.  The question bank flags
this as the ``now_instead_of_as_of`` trap.

Fixing the anchor on the service side also removes the model's ability to get it
wrong: it never has to know today's date, and cannot substitute its own.

Why this is an env knob and not a constant: the two answer sets we grade against
were built against *different* snapshot days.  The 396-question set binds
2026-08-15; the 93-question 全量问题清单 binds 2026-08-17, and 10 of the 396
change answers between the two (E6-01 days_behind 1 vs 3, E6-03 23 rows vs 14,
R6-04 lag_days 16 vs 18, ...).  Hardcoding either day silently fails the other
set, so the anchor moves with the harness instead of the code.
"""

if not _DATE_RE.match(AS_OF):
    raise ValueError(f"GUOSHU_AS_OF 必须是 YYYY-MM-DD，收到 {AS_OF!r}")


_TABLE_NAME_RE = re.compile(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)


def source_tables(sql: str) -> list[str]:
    """Extract the tables a query reads, in first-appearance order.

    Used for provenance: the evidence panel shows 来源表 next to the row
    count, so a reader can see at a glance which tables produced the answer.
    Sub-query bodies and aliases are naturally handled — the regex only picks
    identifiers directly after FROM/JOIN, so ``FROM (SELECT …)`` contributes
    nothing and the inner tables still surface from their own FROM/JOIN.
    """
    seen: list[str] = []
    for match in _TABLE_NAME_RE.finditer(sql):
        name = match.group(1)
        if name not in seen:
            seen.append(name)
    return seen


def as_of_caliber() -> str:
    return f"相对时间窗以数据快照日 {AS_OF} 为基准（非当前系统时间）"


def date_window(
    date_from: str = "",
    date_to: str = "",
    last_days: str | int | None = None,
) -> tuple[str, str]:
    """Resolve a caller's time window into an inclusive ``(from, to)`` date pair.

    ``last_days`` is relative to :data:`AS_OF`.  An empty window means "no time
    filter" and is returned as ``("", "")`` so callers can skip the clause
    entirely rather than binding a wide-open range.
    """
    lo = (date_from or "").strip()
    hi = (date_to or "").strip()
    if last_days is not None and str(last_days).strip():
        try:
            days = int(str(last_days).strip())
        except (TypeError, ValueError) as exc:
            raise QueryError("invalid_argument", f"last_days 必须是整数：{last_days!r}") from exc
        if days <= 0:
            raise QueryError("invalid_argument", f"last_days 必须为正数：{days}")
        conn = connect()
        try:
            row = _one(
                conn,
                "SELECT DATE_SUB(%(as_of)s, INTERVAL %(d)s DAY) AS lo",
                {"as_of": AS_OF, "d": days},
            )
        finally:
            conn.close()
        lo = str(row["lo"]) if row else lo
        hi = hi or AS_OF
    for label, value in (("date_from", lo), ("date_to", hi)):
        if value and not _DATE_RE.match(value):
            raise QueryError("invalid_argument", f"{label} 需为 YYYY-MM-DD：{value!r}")
    return lo, hi


def month_window(months: str | int) -> tuple[str, str]:
    """Resolve "最近 N 个月" into an inclusive date pair anchored at :data:`AS_OF`.

    Calendar months, not ``months * 30`` days: three months back from 2026-08-15
    is 2026-05-15, whereas ``last_days=90`` lands on 2026-05-17 and silently
    drops the three group-history rows in between.  The question bank tracks the
    two as different windows (``incomparable_periods``), so they get different
    parameters rather than one approximating the other.
    """
    try:
        n = int(str(months).strip())
    except (TypeError, ValueError) as exc:
        raise QueryError("invalid_argument", f"last_months 必须是整数：{months!r}") from exc
    if n <= 0:
        raise QueryError("invalid_argument", f"last_months 必须为正数：{n}")
    conn = connect()
    try:
        row = _one(
            conn,
            "SELECT DATE_SUB(%(as_of)s, INTERVAL %(n)s MONTH) AS lo",
            {"as_of": AS_OF, "n": n},
        )
    finally:
        conn.close()
    return (str(row["lo"]) if row else ""), AS_OF


def window_clause(column: str, lo: str, hi: str, params: dict[str, Any], *, prefix: str = "w") -> str:
    """Build the bound SQL fragment for a resolved window. ``column`` is caller-controlled."""
    parts: list[str] = []
    if lo:
        params[f"{prefix}_lo"] = lo
        parts.append(f"{column} >= %({prefix}_lo)s")
    if hi:
        params[f"{prefix}_hi"] = hi
        parts.append(f"{column} <= %({prefix}_hi)s")
    return " AND ".join(parts)


def window_caliber(lo: str, hi: str, *, label: str) -> str:
    if lo and hi:
        return f"{label} 介于 {lo} 与 {hi} 之间（含端点）"
    if lo:
        return f"{label} 自 {lo} 起（含）"
    if hi:
        return f"{label} 截至 {hi}（含）"
    return f"{label} 未设时间过滤"


GROUP_BOARD_CODE = "group"
"""``task_board.code`` for the 集团组 board -- the stable business key, not the id.

The name is editable in the OA, the id is an autoincrement that differs between
the mock and the real store; only ``code`` survives both.
"""


def group_board_join(alias: str = "t", board: str = "b") -> str:
    """Restrict a task query to the 集团组 board, soft-deleted boards excluded."""
    return (
        f"JOIN task_board {board} ON {board}.id = {alias}.board_id "
        f"AND {board}.is_deleted = 0 AND {board}.code = '{GROUP_BOARD_CODE}'"
    )


def group_history_gate(hist: str = "h", alias: str = "t") -> str:
    """The two gates every ``task_group_progress_history`` read must pass.

    Both, not either.  The task must be a formal task (R-01) *and* the history
    row itself must be published: 404 rows exist, 362 pass both, and dropping
    ``is_published`` silently folds 42 un-approved drafts into the answer.  The
    question bank tracks this separately from plain ``publish_gate`` because the
    row-level flag has no counterpart on the task side.
    """
    return f"{formal_task_clause(alias)} AND {hist}.is_published = 1"


GROUP_HISTORY_CALIBER = (
    "任务侧 is_deleted = 0 AND workflow_status = 'published'，"
    "且历史行自身 is_published = 1（两道闸门缺一不可，共 404 行、过闸 362 行）"
)


class QueryError(Exception):
    """A caller-visible failure that carries a stable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def connect() -> Any:
    try:
        return _db.connect()
    except pymysql.Error as exc:
        raise QueryError(
            "store_unreachable",
            f"cannot reach {_db.DSN_DESCRIPTION}: {exc.args[-1] if exc.args else exc}",
        ) from exc


def formal_task_clause(alias: str = "t") -> str:
    """R-01: the formal-task caliber, cited by 111 of the 396 test questions."""
    return f"{alias}.is_deleted = 0 AND {alias}.workflow_status = 'published'"


def _scrub(row: dict[str, Any], *, can_read_sensitive: bool) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in BLOCKED_FIELDS:
            continue
        if key in SENSITIVE_FIELDS and not can_read_sensitive:
            out[key] = "[按权限不展示]"
            continue
        out[key] = value
    return out


def fetch(
    sql: str,
    params: dict[str, Any] | None = None,
    *,
    caliber: str = "",
    can_read_sensitive: bool = False,
    limit: int = MAX_ROWS,
) -> dict[str, Any]:
    """Run a read-only query and wrap it in the standard result envelope."""
    bounded = max(1, min(MAX_ROWS, int(limit)))
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or {})
            # One extra row distinguishes "exactly full" from "truncated".
            raw = cursor.fetchmany(bounded + 1)
            columns = [d[0] for d in cursor.description] if cursor.description else []
    except pymysql.Error as exc:
        raise QueryError("query_failed", str(exc.args[-1] if exc.args else exc)) from exc
    finally:
        conn.close()

    has_more = len(raw) > bounded
    rows = [_scrub(dict(r), can_read_sensitive=can_read_sensitive) for r in raw[:bounded]]
    if rows:
        columns = list(rows[0].keys())
    return {
        "ok": True,
        "caliber": caliber or "无附加口径",
        "snapshot_note": SNAPSHOT_NOTE,
        "snapshot_date": AS_OF,
        "source_tables": source_tables(sql),
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "has_more": has_more,
    }


def all_rows(sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Run a read-only query and return ALL rows, unbounded.

    Only for server-side scans that need the full corpus (text-rule checks):
    the agent-facing envelope stays capped at MAX_ROWS, and anything that
    would cross the boundary must go through ``fetch`` instead.
    """
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or {})
            return [dict(row) for row in cursor.fetchall()]
    except pymysql.Error as exc:
        raise QueryError("query_failed", str(exc.args[-1] if exc.args else exc)) from exc
    finally:
        conn.close()


def scalar(sql: str, params: dict[str, Any] | None = None, *, caliber: str = "") -> dict[str, Any]:
    conn = connect()
    try:
        with conn.cursor() as cursor:
            cursor.execute(sql, params or {})
            row = cursor.fetchone()
    except pymysql.Error as exc:
        raise QueryError("query_failed", str(exc.args[-1] if exc.args else exc)) from exc
    finally:
        conn.close()
    value = None if not row else next(iter(row.values()))
    return {
        "ok": True,
        "caliber": caliber or "无附加口径",
        "snapshot_note": SNAPSHOT_NOTE,
        "snapshot_date": AS_OF,
        "source_tables": source_tables(sql),
        "value": value,
    }


def _one(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        row = cursor.fetchone()
    return dict(row) if row else None


def resolve_board(board: str) -> int | None:
    """Map a board code or name to its id. Returns None when unmatched."""
    token = (board or "").strip()
    if not token:
        return None
    conn = connect()
    try:
        row = _one(
            conn,
            "SELECT id FROM task_board WHERE is_deleted = 0 AND (code = %(t)s OR name = %(t)s)",
            {"t": token},
        )
        if row is not None:
            return int(row["id"])
        row = _one(
            conn,
            "SELECT id FROM task_board WHERE is_deleted = 0 AND name LIKE %(like)s",
            {"like": f"%{token}%"},
        )
        return None if row is None else int(row["id"])
    finally:
        conn.close()


def resolve_task(task: str) -> dict[str, Any] | None:
    """Locate one formal task by id, or by name.

    A purely numeric token is an id and nothing else.  Falling through to name
    matching on a numeric miss is what made ``task="2"`` return a *different*
    task: id 2 is not published, so the LIKE branch matched some other row whose
    name merely contained "2", and the caller then reported that row's data as
    task 2's.  A wrong task silently substituted for the requested one is worse
    than an honest miss.
    """
    token = (task or "").strip()
    if not token:
        return None
    conn = connect()
    try:
        if token.isdigit():
            return _one(
                conn,
                f"SELECT * FROM task t WHERE t.id = %(id)s AND {formal_task_clause()}",
                {"id": int(token)},
            )
        row = _one(
            conn,
            f"SELECT * FROM task t WHERE {formal_task_clause()} AND t.task_name = %(name)s",
            {"name": token},
        )
        if row is not None:
            return row
        # Shortest match wins: with substring matching, the shortest name is the
        # least over-specified reading of what the user typed.
        return _one(
            conn,
            f"SELECT * FROM task t WHERE {formal_task_clause()} AND t.task_name LIKE %(like)s "
            "ORDER BY CHAR_LENGTH(t.task_name) LIMIT 1",
            {"like": f"%{token}%"},
        )
    finally:
        conn.close()


def task_miss_reason(task: str) -> dict[str, Any]:
    """Explain a formal-task miss: no such row, or a row that is not formal.

    "no match for a formal task: 2" is true but a dead end. Task 2 exists, is not
    deleted and carries a full approval trail -- it merely sits at workflow_status
    'rejected', so R-01 keeps it out of the formal set. A caller told only "no
    match" cannot tell that apart from a typo'd id, and (this is what actually
    happened on M2-01) starts guessing: a name search lands on the 3rd/4th-phase
    siblings, which ARE formal but are different tasks, while the submission and
    action tools answer about task 2 quite happily. The three signals contradict
    each other and the round budget goes to arbitration.

    So say which case it is, and for the non-formal case name the tools that
    still answer -- the foreign-key ones, which by design do not apply R-01.
    Diagnosing is not widening: the formal-task gate stays exactly where it was.
    """
    token = (task or "").strip()
    if not token or not token.isdigit():
        return {"kind": "unknown"}
    conn = connect()
    try:
        row = _one(
            conn,
            "SELECT id, task_name, is_deleted, workflow_status FROM task WHERE id = %(id)s",
            {"id": int(token)},
        )
    finally:
        conn.close()
    if row is None:
        return {"kind": "absent"}
    return {
        "kind": "not_formal",
        "task_id": int(row["id"]),
        "task_name": row["task_name"],
        "is_deleted": int(row["is_deleted"]),
        "workflow_status": row["workflow_status"],
    }


_SERIES_SUFFIX = re.compile(r"（\d+期）$")


def name_series(task_id: int) -> list[dict[str, Any]]:
    """List the OTHER formal tasks whose name is the same series as ``task_id``'s.

    「数据资源登记体系建设」 and its three 期 suffixes are four separate tasks
    with their own progress. Resolving the bare name lands on exactly one of them,
    which is correct -- but a caller told only "here are 14 periods" cannot tell
    whether the series exists at all, and answers about "this task's history" then
    drift into listing every 期 in the family. Returning the siblings makes the
    choice visible instead of implicit; an empty list means the name is unique.
    """
    conn = connect()
    try:
        row = _one(conn, "SELECT task_name FROM task WHERE id = %(id)s", {"id": int(task_id)})
        if row is None:
            return []
        base = _SERIES_SUFFIX.sub("", str(row["task_name"]))
        with conn.cursor() as cursor:
            cursor.execute(
                f"SELECT t.id, t.task_name FROM task t WHERE {formal_task_clause()} "
                "AND t.id <> %(id)s AND (t.task_name = %(base)s OR t.task_name LIKE %(like)s) "
                "ORDER BY t.id",
                {"id": int(task_id), "base": base, "like": f"{base}（%期）"},
            )
            return [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()


def resolve_task_id(task: str) -> int | None:
    """Resolve a foreign-key task_id, WITHOUT the formal-task filter.

    Submissions, attachments and progress rows hang off ``task_id`` as a plain
    foreign key.  Questions about them ("task 2's submissions") are asking about
    that key, not about whether the parent task is currently published -- gating
    on R-01 here silently drops rows that genuinely exist.  Returns the integer
    directly for a numeric token; otherwise falls back to formal-task name
    resolution, which is the only way a name can be turned into an id.
    """
    token = (task or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    found = resolve_task(token)
    return None if found is None else int(found["id"])
