"""Ledger ↔ board reconciliation — re-align every ledger row's 负责人/mentor with the board.

The TODO ledger is a **snapshot**: ``company-todo-sync`` reads the board's mentor column
once and stamps ``负责人``/``mentor`` onto each row it creates, and nothing ever updates
those two columns afterwards — every other ledger write touches 状态 / 截止日期 / 任务GUID /
mentor打分 / mentor评语. So a board edit made after the rows exist (reassigning someone to
another mentor) leaves the ledger asserting the pre-edit relation, and every consumer of
the ledger keeps reporting it: the 16:00 compare cards (whose data source is the cycle
ledger table), the mentor report, the review-card routing
(``_review_card_impl._send_review_card`` reads the row's ``mentor`` field), and
same-mentor peer contrast.

This module is the deterministic half of that fix: it re-reads the board's mentor column
and diffs it against the ledger rows, so the caller learns *which* rows still carry the old
relation — with their ``record_id`` — instead of discovering it from a card sent to the
wrong person.

Two things it deliberately does not do:

* **No guessing.** A row whose owner is absent from the board, or whose new mentor cannot
  be resolved to exactly one directory entry, is reported as needing a human. It is never
  rewritten to a plausible-looking value.
* **No half-fix claimed as a fix.** Rows live in one base *per mentor*
  (``TODO 台账-<mentor_name>``), so rewriting the ``mentor`` field in the old mentor's base
  is not enough on its own — a consumer that enumerates bases still reads the row from the
  old mentor's table. That state is reported separately as ``needs_move`` and must not be
  reported to anyone as "已对齐" until the row physically lives in the right base.
"""

from __future__ import annotations

import json
from typing import Any

import _feishu_impl as _core

from _feishu.bitable import search_bitable_records_impl, update_bitable_records_impl
from _feishu.contact import member_status_check_impl
from _feishu.ledger_schema import _LEDGER_NAME_PREFIX
from _feishu.mentor_ledger import _build_list_folder_request
from _feishu.sheet import read_sheet_grid_impl
from _feishu.todo_sop import _build_wiki_get_node_request, _find_col

#: Board headers naming the person column / the mentor column. ``_find_col`` matches
#: exact-first then substring, case-sensitively — the same table the board readers use.
_BOARD_PERSON_HEADERS = ("负责人", "人", "姓名")
_BOARD_MENTOR_HEADERS = ("mentor", "上级", "导师")

_PERSON_FIELD_DEFAULT = "负责人"
_MENTOR_FIELD_DEFAULT = "mentor"

#: Cycle tables are named ``台账-<cycle_date>`` by ``feishu_mentor_ledger_cycle_table``.
_CYCLE_TABLE_PREFIX = "台账-"

_PAGE_SIZE = 500
_MAX_PAGES = 40  # 40 x 500 = the 20000-row table cap
_REPORT_LIMIT = 50  # rows echoed per bucket; the true total always travels with them
_MAX_BOARD_ROWS = 8 * 50  # same paging budget the fill-status reader uses (400 rows)


def _norm(value: Any) -> str:
    """Normalize one cell / person-field name for matching.

    The board's person and mentor columns are Feishu **mention** cells, which the sheet
    reader flattens to ``"@张三"``, while the ledger's person fields carry the bare name.
    Both sides therefore go through the single ``_norm_name`` implementation that
    ``_feishu/strike.py`` exports for exactly this reason — a second, subtly different
    strip here is how "改了关系但两边对不上" starts.
    """
    return _core._norm_name(str(value if value is not None else ""))


def _person_names(value: Any) -> list[str]:
    """Pull display names out of a Bitable PERSON field (``[{"id", "name"}, …]``)."""
    names: list[str] = []
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                name = entry.get("name") or entry.get("en_name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
            elif isinstance(entry, str) and entry.strip():
                names.append(entry.strip())
    elif isinstance(value, str) and value.strip():
        names.append(value.strip())
    return names


def _clip(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    """Return the first ``_REPORT_LIMIT`` rows plus whether the list was cut."""
    return items[:_REPORT_LIMIT], len(items) > _REPORT_LIMIT


def classify_rows(board: dict[str, str], rows: list[dict[str, Any]], expected_mentor: str = "") -> dict[str, Any]:
    """Diff ledger rows against the board's mentor column. Pure; every row lands once.

    Args:
        board: normalized person name → normalized mentor name (``""`` when the board cell
            is blank).
        rows: ``[{"record_id", "owner", "mentor"}]`` in raw display-name form, one entry per
            ledger record.
        expected_mentor: the mentor whose base/table these rows were read from (the suffix
            of ``TODO 台账-<mentor_name>``). Empty = unknown, and the ``needs_move`` check is
            then skipped rather than guessed.

    Returns the primary buckets (each row in exactly one of ``mentor_changed`` /
    ``person_not_on_board`` / ``row_missing_owner`` / ``row_missing_mentor`` /
    ``board_missing_mentor`` / consistent), plus ``needs_move`` which is an **independent**
    signal: a row can agree with the board and still sit in the wrong mentor's base. Each
    bucket carries its true total and the report is clipped at ``_REPORT_LIMIT``.
    """
    buckets: dict[str, list[dict[str, Any]]] = {
        "mentor_changed": [],
        "needs_move": [],
        "person_not_on_board": [],
        "row_missing_owner": [],
        "row_missing_mentor": [],
        "board_missing_mentor": [],
    }
    consistent = 0
    expected = _norm(expected_mentor)

    for row in rows:
        record_id = str(row.get("record_id", ""))
        owner = _norm(row.get("owner"))
        mentor = _norm(row.get("mentor"))
        entry = {"record_id": record_id, "person": owner, "row_mentor": mentor}

        # Location check first and independently: it is the half-fixed state, and it must
        # stay visible even for rows whose field already agrees with the board.
        if expected and mentor and mentor != expected:
            buckets["needs_move"].append({**entry, "expected_base": expected_mentor})

        if not owner:
            buckets["row_missing_owner"].append({"record_id": record_id})
            continue
        if owner not in board:
            buckets["person_not_on_board"].append({"record_id": record_id, "person": owner})
            continue
        board_mentor = board[owner]
        if not board_mentor:
            buckets["board_missing_mentor"].append({"record_id": record_id, "person": owner})
            continue
        if not mentor:
            buckets["row_missing_mentor"].append({"record_id": record_id, "person": owner})
            continue
        if mentor == board_mentor:
            consistent += 1
            continue
        buckets["mentor_changed"].append({**entry, "board_mentor": board_mentor})

    out: dict[str, Any] = {"rows": len(rows), "consistent": consistent, "truncated": {}}
    for name, items in buckets.items():
        shown, cut = _clip(items)
        out[name] = shown
        out[f"{name}_total"] = len(items)
        if cut:
            out["truncated"][name] = True
    return out


async def _read_board(board_link: str, user_key: str = "") -> dict[str, Any]:
    """Read the board's person + mentor columns into ``{person: mentor}`` (normalized).

    Mirrors the fill-status reader: resolve the wiki node, read the header row limited to
    one row, then page the body with a bounded row budget so a wide board cannot be cut
    mid-row. Only the two columns are kept.
    """
    wiki_token = board_link.rstrip("/").split("/")[-1]
    res = await _core._invoke(_build_wiki_get_node_request(wiki_token), user_key=user_key)
    if not res.get("ok"):
        return res
    node = res.get("data", {}).get("node", {}) if isinstance(res.get("data"), dict) else {}
    obj_token = node.get("obj_token", "")
    if not obj_token:
        return _core._error(f"wiki get_node 没拿到 obj_token: {board_link}")

    header_grid = await read_sheet_grid_impl(obj_token, range_="!A1:AZ1", max_rows=1, user_key=user_key)
    if not header_grid.get("ok"):
        return header_grid
    header_rows = header_grid.get("rows", []) or []
    if not header_rows:
        return _core._error("看板表读不到表头。")
    header = [str(c).strip() for c in header_rows[0]]
    person_col = _find_col(header, _BOARD_PERSON_HEADERS)
    mentor_col = _find_col(header, _BOARD_MENTOR_HEADERS)
    if person_col < 0:
        return _core._error(f"人名列认不出来,表头: {header[:10]}")
    if mentor_col < 0:
        return _core._error(
            f"mentor 列认不出来,表头: {header[:10]} —— 表头可能写作 带教/师父 等本工具尚未收录的同义词。"
            "先确认看板哪一列是 mentor(负责人与 mentor 是两个不同的人、两列),再决定是否补别名。"
        )

    board: dict[str, str] = {}
    duplicates: list[str] = []
    start_row = 2  # 表头占第 1 行
    read_rows = 0
    while read_rows < _MAX_BOARD_ROWS:
        grid = await read_sheet_grid_impl(
            obj_token, range_="!A1:AZ400", max_rows=50, start_row=start_row, user_key=user_key
        )
        if not grid.get("ok"):
            return grid
        for row in grid.get("rows", []) or []:
            if len(row) <= person_col:
                continue
            name = _norm(row[person_col])
            if not name:
                continue
            mentor = _norm(row[mentor_col]) if len(row) > mentor_col else ""
            if name in board and board[name] != mentor:
                duplicates.append(name)
            board[name] = mentor
            read_rows += 1
        if not grid.get("has_more"):
            break
        start_row = grid.get("next_start_row") or start_row + 1
    else:
        # Ran out of budget before the board ended: say so rather than treat the tail as absent.
        return {
            "ok": True,
            "board": board,
            "columns": {"person": header[person_col], "mentor": header[mentor_col]},
            "people": len(board),
            "without_mentor": sorted(n for n, m in board.items() if not m),
            "duplicate_names": sorted(set(duplicates)),
            "truncated": True,
        }

    return {
        "ok": True,
        "board": board,
        "columns": {"person": header[person_col], "mentor": header[mentor_col]},
        "people": len(board),
        "without_mentor": sorted(n for n, m in board.items() if not m),
        "duplicate_names": sorted(set(duplicates)),
        "truncated": False,
    }


async def _read_ledger_rows(
    app_token: str, table_id: str, person_field: str, mentor_field: str, user_key: str
) -> dict[str, Any]:
    """Page one ledger table into ``[{"record_id", "owner", "mentor"}]`` (raw names)."""
    rows: list[dict[str, Any]] = []
    page_token = ""
    for _ in range(_MAX_PAGES):
        res = await search_bitable_records_impl(
            app_token,
            table_id,
            field_names=json.dumps([person_field, mentor_field], ensure_ascii=False),
            page_size=_PAGE_SIZE,
            page_token=page_token,
            user_key=user_key,
        )
        if not res.get("ok"):
            return res
        for record in res.get("records", []) if isinstance(res.get("records"), list) else []:
            fields = record.get("fields", {}) if isinstance(record, dict) else {}
            owners = _person_names(fields.get(person_field)) if isinstance(fields, dict) else []
            mentors = _person_names(fields.get(mentor_field)) if isinstance(fields, dict) else []
            rows.append(
                {
                    "record_id": str(record.get("record_id", "")),
                    "owner": owners[0] if owners else "",
                    "mentor": mentors[0] if mentors else "",
                }
            )
        if not res.get("has_more"):
            return {"ok": True, "rows": rows, "truncated": False}
        page_token = str(res.get("page_token") or "")
        if not page_token:
            break
    return {"ok": True, "rows": rows, "truncated": True}


async def _cycle_table_id(app_token: str, cycle_date: str, user_key: str) -> dict[str, Any]:
    """Find ``台账-<cycle_date>`` inside a base; return its table_id."""
    res = await _core._invoke(_core._build_list_tables_request(app_token), user_key=user_key)
    if not res.get("ok"):
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    wanted = f"{_CYCLE_TABLE_PREFIX}{cycle_date.strip()}"
    for item in data.get("items", []) if isinstance(data.get("items"), list) else []:
        if isinstance(item, dict) and str(item.get("name", "")).strip() == wanted:
            table_id = item.get("table_id", "")
            if table_id:
                return {"ok": True, "table_id": table_id, "name": wanted}
    return _core._error(
        f"base {app_token} 里没有 {wanted} 表 —— 本周期还没建表(先跑 feishu_mentor_ledger_cycle_table)。"
    )


async def _discover_ledgers(folder_token: str, cycle_date: str, user_key: str) -> dict[str, Any]:
    """List ``TODO 台账-*`` bases in the ledger folder and resolve each one's cycle table.

    The mentor name travels with the coordinate: it is the base's own name suffix, and it
    is what makes ``needs_move`` detectable (a row living in the wrong mentor's base).
    """
    ledgers: list[dict[str, Any]] = []
    page_token = ""
    for _ in range(_MAX_PAGES):
        res = await _core._invoke(_build_list_folder_request(folder_token, page_token), user_key=user_key)
        if not res.get("ok"):
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for f in data.get("files", []) if isinstance(data.get("files"), list) else []:
            if not isinstance(f, dict) or f.get("type") != "bitable":
                continue
            name = str(f.get("name", ""))
            if not name.startswith(_LEDGER_NAME_PREFIX):
                continue
            app_token = str(f.get("token", ""))
            if not app_token:
                continue
            entry: dict[str, Any] = {
                "app_token": app_token,
                "mentor_name": name[len(_LEDGER_NAME_PREFIX) :].strip(),
            }
            table = await _cycle_table_id(app_token, cycle_date, user_key)
            if table.get("ok"):
                entry["table_id"] = table["table_id"]
            else:
                entry["error"] = table.get("message") or table.get("error") or "cycle table unresolved"
            ledgers.append(entry)
        page_token = str(data.get("page_token") or "")
        if not data.get("has_more") or not page_token:
            break
    return {"ok": True, "ledgers": ledgers}


def _parse_ledgers(ledgers_json: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Validate the caller's ledger coordinates."""
    try:
        parsed = json.loads(ledgers_json)
    except ValueError as exc:
        return None, f"ledgers_json is not valid JSON: {exc}"
    if not isinstance(parsed, list) or not parsed:
        return None, (
            "ledgers_json must be a non-empty JSON array, e.g. "
            '[{"app_token":"bascn…","table_id":"tbl…","mentor_name":"孙逊"}]. '
            "app_token alone is enough when cycle_date is given (the 台账-<cycle_date> table is then looked up)."
        )
    out: list[dict[str, Any]] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            return None, f"ledgers_json[{i}] must be a JSON object."
        app_token = str(item.get("app_token", "")).strip()
        if not app_token:
            return None, f"ledgers_json[{i}] is missing a non-empty app_token."
        out.append(
            {
                "app_token": app_token,
                "table_id": str(item.get("table_id", "")).strip(),
                "mentor_name": str(item.get("mentor_name", "")).strip(),
                "person_field": str(item.get("person_field", "")).strip() or _PERSON_FIELD_DEFAULT,
                "mentor_field": str(item.get("mentor_field", "")).strip() or _MENTOR_FIELD_DEFAULT,
            }
        )
    return out, None


async def _resolve_mentor_open_ids(names: list[str]) -> dict[str, Any]:
    """Resolve mentor display names to open_ids; only unique, present entries count."""
    if not names:
        return {"ok": True, "resolved": {}, "unresolved": {}}
    classified = await member_status_check_impl(names)
    if not classified.get("ok"):
        return classified
    resolved: dict[str, str] = {}
    for entry in classified.get("active", []) if isinstance(classified.get("active"), list) else []:
        if isinstance(entry, dict) and entry.get("name") and entry.get("open_id"):
            resolved[_norm(entry["name"])] = str(entry["open_id"])
    unresolved: dict[str, str] = {}
    for name in classified.get("unresolved", []) if isinstance(classified.get("unresolved"), list) else []:
        unresolved[_norm(name)] = "通讯录里同名多人,无法确定是哪一位"
    for name in classified.get("resigned", []) if isinstance(classified.get("resigned"), list) else []:
        unresolved[_norm(name)] = "通讯录里查不到这个人(离职/冻结,或名字写法不一致)"
    return {"ok": True, "resolved": resolved, "unresolved": unresolved}


async def ledger_reconcile_impl(
    board_link: str,
    ledgers_json: str = "",
    cycle_date: str = "",
    folder_token: str = "",
    apply_fixes: bool = False,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Diff every ledger row against the board's mentor column; optionally fix the field.

    Args:
        board_link: The TODO board's /wiki/ or /sheets/ URL (authoritative for 负责人/mentor).
        ledgers_json: JSON array of ``{"app_token", "table_id"?, "mentor_name"?, "person_field"?,
            "mentor_field"?}``. ``table_id`` may be omitted when ``cycle_date`` is given.
        cycle_date: Cycle column header (e.g. ``9.9``); resolves each base's
            ``台账-<cycle_date>`` table when ``table_id`` is not given.
        folder_token: The folder holding the per-mentor ledger bases. When given, every
            ``TODO 台账-*`` base in it is checked too (no coordinate list needed).
        apply_fixes: Rewrite the ``mentor`` field of rows whose relation changed, **only**
            when the new mentor resolves to exactly one directory entry. Off by default.
        user_key: Identity for reads (usual convention).
        identity: Who owns a write — ``"user"`` / ``"bot"``; the app must be a collaborator
            on that base for a bot write to be accepted.

    Returns ``{"ok", "columns", "board_people", "ledgers": [...], "mentor_changed",
    "needs_move", "needs_attention", "relation_aligned"}``. The three counts are **issue**
    counts, not distinct-row counts — one row can raise both a stale relation and a
    misplacement. ``relation_aligned`` is true only when no row carries a stale relation, no
    row sits in the wrong mentor's base, and nothing needed a human: a clean bill of health,
    not "the send succeeded".
    """
    if not board_link.strip():
        return _core._error("board_link is required (the TODO board URL).")
    ledgers: list[dict[str, Any]] = []
    if ledgers_json.strip():
        parsed, problem = _parse_ledgers(ledgers_json)
        if problem or parsed is None:
            return _core._error(problem or "ledgers_json is invalid.")
        ledgers = parsed
    if folder_token.strip():
        if not cycle_date.strip():
            return _core._error("cycle_date is required to resolve each base's 台账-<cycle_date> table.")
        found = await _discover_ledgers(folder_token.strip(), cycle_date, user_key)
        if not found.get("ok"):
            return found
        known = {entry["app_token"] for entry in ledgers}
        ledgers.extend(e for e in found["ledgers"] if e["app_token"] not in known)
    if not ledgers:
        return _core._error(
            "no ledger to check: pass ledgers_json (an app_token per mentor base) or folder_token + cycle_date."
        )

    board_read = await _read_board(board_link.strip(), user_key)
    if not board_read.get("ok"):
        return board_read
    board_map: dict[str, str] = board_read["board"]

    results: list[dict[str, Any]] = []
    needing_human = 0
    stale = 0
    misplaced = 0
    for ledger in ledgers:
        app_token = str(ledger["app_token"])
        table_id = str(ledger.get("table_id", ""))
        resolved_note = ""
        if not table_id and cycle_date.strip():
            table = await _cycle_table_id(app_token, cycle_date, user_key)
            if table.get("ok"):
                table_id = str(table["table_id"])
            else:
                resolved_note = str(table.get("message") or table.get("error") or "cycle table unresolved")
        if not table_id:
            results.append(
                {
                    "app_token": app_token,
                    "mentor_name": ledger.get("mentor_name", ""),
                    "ok": False,
                    "error": resolved_note or "table_id unresolved (pass table_id, or cycle_date to look it up).",
                }
            )
            needing_human += 1
            continue

        read = await _read_ledger_rows(
            app_token, table_id, str(ledger["person_field"]), str(ledger["mentor_field"]), user_key
        )
        if not read.get("ok"):
            results.append(
                {
                    "app_token": app_token,
                    "table_id": table_id,
                    "mentor_name": ledger.get("mentor_name", ""),
                    "ok": False,
                    "error": read.get("message") or read.get("error") or "ledger read failed",
                }
            )
            needing_human += 1
            continue

        outcome = classify_rows(board_map, read["rows"], str(ledger.get("mentor_name", "")))
        stale += outcome["mentor_changed_total"]
        misplaced += outcome["needs_move_total"]
        needing_human += (
            outcome["person_not_on_board_total"]
            + outcome["row_missing_owner_total"]
            + outcome["row_missing_mentor_total"]
            + outcome["board_missing_mentor_total"]
        )
        entry: dict[str, Any] = {
            "app_token": app_token,
            "table_id": table_id,
            "mentor_name": ledger.get("mentor_name", ""),
            "ok": True,
            "read_truncated": bool(read.get("truncated")),
            **outcome,
        }

        if apply_fixes and outcome["mentor_changed"]:
            targets = sorted({row["board_mentor"] for row in outcome["mentor_changed"]})
            lookup = await _resolve_mentor_open_ids(targets)
            if not lookup.get("ok"):
                entry["apply_errors"] = [lookup.get("message") or lookup.get("error") or "directory read failed"]
            else:
                resolved: dict[str, str] = lookup["resolved"]
                blocked: dict[str, str] = lookup["unresolved"]
                updates: list[dict[str, Any]] = []
                skipped: list[dict[str, Any]] = []
                for row in outcome["mentor_changed"]:
                    target = row["board_mentor"]
                    if target in resolved:
                        updates.append(
                            {
                                "record_id": row["record_id"],
                                "fields": {str(ledger["mentor_field"]): [{"id": resolved[target]}]},
                            }
                        )
                    else:
                        skipped.append({**row, "reason": blocked.get(target, "无法解析新 mentor")})
                if updates:
                    written = await update_bitable_records_impl(
                        app_token,
                        table_id,
                        json.dumps(updates, ensure_ascii=False),
                        user_key=user_key,
                        identity=identity,
                    )
                    entry["applied"] = written.get("updated", [])
                    entry["applied_count"] = len(entry["applied"])
                    if not written.get("ok"):
                        entry["apply_errors"] = [written.get("message") or written.get("error") or "update failed"]
                    if written.get("warning"):
                        entry["apply_warning"] = written["warning"]
                if skipped:
                    entry["needs_manual"] = skipped
                    needing_human += len(skipped)
        elif outcome["mentor_changed"]:
            entry["fix_hint"] = "apply_fixes=false: 未写入。带 apply_fixes=true 重跑即按看板修正 mentor 字段。"

        results.append(entry)

    out: dict[str, Any] = {
        "ok": True,
        "columns": board_read.get("columns", {}),
        "board_people": board_read.get("people", 0),
        "board_without_mentor": board_read.get("without_mentor", []),
        "board_duplicate_names": board_read.get("duplicate_names", []),
        "board_truncated": board_read.get("truncated", False),
        "ledgers": results,
        "mentor_changed": stale,
        "needs_move": misplaced,
        "needs_attention": stale + misplaced + needing_human,
        "relation_aligned": stale == 0 and misplaced == 0 and needing_human == 0,
    }
    if cycle_date.strip():
        out["cycle_date"] = cycle_date.strip()
    if misplaced:
        out["note"] = (
            "needs_move 非空:这些行的 mentor 字段与它所在的 base 不一致 —— 行还躺在旧 mentor 的 "
            "`TODO 台账-<旧 mentor>` 里。按 base 枚举的消费者(逐 mentor 报表/对比卡)仍会按旧关系读到它们。"
            "改写字段不等于搬了家:先把行搬进新 mentor 的本周期表(或让消费侧改按 mentor 列分组),再做任何"
            "「已对齐」的声明。"
        )
    elif stale:
        out["note"] = (
            "mentor_changed 非空:台账行仍带着改前的 mentor。带 apply_fixes=true 重跑可修正字段;修正后若"
            "出现 needs_move,仍需搬行。"
        )
    return out
