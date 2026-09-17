"""Rebuild the oa_biz question set against the local mock store.

国数 gave us oa_biz_200.jsonl: 200 questions carrying `gold_sql` plus an
`expected` value.  The `expected` values cannot be used as our grading key --
they were computed against the real oa_biz database at snapshot 2026-08-18,
while our mock is anchored at 2026-08-15.  Measured, not assumed: of the 74
runnable scalar questions only 3 agree with `expected`, and the gaps are
systematic (B1-01 expects 81 where mock holds 128; owner 刘玮 does not exist in
mock at all, so his questions return 0).  Zero SQL errors, so this is a data
mismatch and not a syntax one.

So we do what the 396-question set already does: execute `gold_sql` against the
mock and treat the result set as that question's reference answer.  What this
measures is tool selection and retrieval accuracy on the new question shapes;
the numbers are mock numbers and do not transfer to the real database.

Two groups are dropped rather than shipped broken:

  * questions touching the 10 tables the mock does not have (key_works,
    key_tasks, oa_calendar_* and friends) -- blocked until 国数 sends the DDL.
  * questions whose mock result is empty or 0 while the question does not ask
    an existence question.  Those grade whether the model says "查不到", not
    whether it can retrieve, so they measure the mock's gaps instead of the
    agent.

Question ids are prefixed `OA-`: the new 200 and the old 396 share 108 ids with
entirely different question text, so an unprefixed merge would silently
overwrite half the set.

Usage:
    python tests/build_oa_answers.py                     # writes next to the source
    python tests/build_oa_answers.py --out other.jsonl
"""

# ruff: noqa: RUF001, RUF002  中文口径文案与文档串里的全角标点是给人看的正文, 不能换成半角。
# ruff: noqa: T201  这是命令行脚本, stdout 就是它的输出通道。
from __future__ import annotations

import argparse
import collections
import importlib.util
import json
import re
from pathlib import Path
from typing import Any

WORKSPACE = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE = Path.home() / "Downloads" / "oa_biz_200.jsonl"
DEFAULT_OUT = Path.home() / "Downloads" / "oa_biz-mock-answers.jsonl"

MOCK_TABLES = frozenset(
    {
        "task",
        "task_attachment",
        "task_board",
        "task_category",
        "task_group_detail",
        "task_group_progress_history",
        "task_milestone",
        "task_progress",
        "task_progress_import",
        "task_workflow_action",
        "task_workflow_submission",
        "task_year_goal",
    }
)
"""The 12 tables the mock dump actually contains."""

DIFFICULTY = {"简单": "easy", "中等": "medium", "困难": "hard"}

SNAPSHOT_NOTE = (
    "答案由 gold_sql 在本地 mock 库(锚 2026-08-15)执行得出；原 expected 按国数真实库 2026-08-18 快照，两者不可互换"
)

_TABLE_REF = re.compile(r"(?:FROM|JOIN)\s+`?(\w+)`?", re.IGNORECASE)
_NAMED_PARAM = re.compile(r":(\w+)")

MOCK_AS_OF = "2026-08-15"
"""The mock's own snapshot date, and the only correct anchor for these answers.

oa_biz_200 ships `params` computed against the real database at 2026-08-18. Passing
those through verbatim dates the answer three days into the mock's future: E6-01's
`DATEDIFF(:as_of, MAX(report_time))` yields 17 under 08-18 and 14 under 08-15 off
the same rows. Any param that names a date anchor is rebound here, and the
rebinding is recorded per question so a reader can tell an anchored answer from a
literal one.
"""

_AS_OF_PARAMS = frozenset({"as_of", "asof", "today", "now", "ref_date", "snapshot"})


def tables_used(sql: str) -> set[str]:
    return {name.lower() for name in _TABLE_REF.findall(sql or "")}


def audit_caliber(sql: str, question: str = "") -> list[str]:
    """Flag the formal-scope gates a gold_sql leaves out.

    A reference answer computed without these gates is not a harder version of the
    same question -- it is a different question. Measured on the first run: of 64
    questions failing both rounds, 36 have a gap flagged here, against 5 of the 45
    that pass both. So this is reported per question rather than silently fixed:
    the answer set is 国数's artefact, and rewriting their SQL would swap one
    unverifiable key for another. Grading can skip the flagged ones, or a reviewer
    can decide case by case.

    Two examples of what the gaps do. OA-B7-02 counts `task_progress` with no gate
    at all and lands on 1068 rows / 83 tasks where the formal caliber holds 943/73.
    OA-H5-01 filters the denominator by board but not the numerator, so its
    "coverage" reads 125/82 = 152.4% -- above 100%, which no coverage can be.
    """
    text = " ".join((sql or "").split())
    lowered = text.lower()
    tables = tables_used(text)
    gaps: list[str] = []
    # Only require the task gates when the query actually touches the task domain;
    # a pure category-tree or board-dictionary query has no task rows to filter.
    task_scoped = bool(tables & {"task", "task_progress", "task_milestone", "task_year_goal", "task_attachment"})
    if task_scoped:
        if "is_deleted" not in lowered:
            gaps.append("缺软删闸门 is_deleted = 0")
        if "workflow_status" not in lowered:
            gaps.append("缺发布闸门 workflow_status = 'published'")
    if "task_progress" in tables and "is_published" not in lowered:
        gaps.append("缺进展行发布闸门 is_published = 1")
    if "task_group_progress_history" in tables and "is_published" not in lowered:
        gaps.append("缺集团历史行发布闸门 is_published = 1")
    gaps.extend(_audit_fanout(text, lowered, tables, question))
    return gaps


_ONE_TO_MANY = frozenset(
    {
        "task_progress",
        "task_milestone",
        "task_year_goal",
        "task_attachment",
        "task_workflow_action",
        "task_workflow_submission",
        "task_group_progress_history",
    }
)
"""Child tables holding many rows per task, so a JOIN to them multiplies task rows."""


def _audit_fanout(text: str, lowered: str, tables: set[str], question: str = "") -> list[str]:
    """Flag a COUNT(*) over a one-to-many JOIN, i.e. rows counted as tasks.

    The gate audit above cannot see this one: the SQL can carry every gate and
    still answer the wrong question. Four measured cases, all asking 「多少个任务」
    and all landing on the row count instead:

      OA-C2-02  「当期一共有多少个任务报了进展」   943 rows vs   73 tasks
      OA-G3-02  「两处都写了年度目标的任务多少个」 313 rows vs  128 tasks
      OA-K3-03  「定了目标又报了进展的任务多少个」 186 rows vs   73 tasks
      OA-H5-01  技术组里程碑覆盖率                125/82 = 152.4%, above 100%

    Each needs COUNT(DISTINCT t.id) where it wrote COUNT(*). Reported, not
    rewritten -- same reason as the gates: the answer set is 国数's artefact.
    """
    if not (tables & _ONE_TO_MANY):
        return []
    joins_task = " join task " in lowered or "from task " in lowered
    if not joins_task:
        return []
    # A plain COUNT(*) is only suspect when nothing else de-duplicates the rows.
    counts_rows = "count(*)" in lowered
    dedupes = "count(distinct" in lowered or "group by" in lowered
    # "asks about tasks" can only be judged from the question text: the SQL holds no
    # Chinese at all. An earlier draft searched the SQL for these words, so none of
    # the four known cases could ever match.
    # Match on 任务 plus a counting word rather than on fixed phrases: OA-K3-03
    # separates 任务 from 多少个 with a comma, which no phrase list anticipated.
    asks_tasks = "任务" in question and any(word in question for word in ("多少", "几个", "几条"))
    if counts_rows and asks_tasks and not dedupes:
        return ["COUNT(*) 落在一对多 JOIN 上，问的是任务数却在数行数（应为 COUNT(DISTINCT t.id)）"]
    return []


def audit_answer(rows: list[dict[str, Any]]) -> list[str]:
    """Flag reference answers that are impossible on their face.

    Checked against the computed values rather than the SQL, because that is where
    it shows: OA-H5-01's coverage reads 125/82 = 152.4%, and no coverage can exceed
    100%. Its numerator counts every task holding a milestone library-wide while its
    denominator counts only the tech board, so the two are not the same population.
    A ratio audit on the SQL text kept missing this -- the denominator subquery sits
    before the ROUND in the select list, so any "is the numerator gated" heuristic
    read the denominator's own filter and passed.
    """
    flagged: list[str] = []
    for row in rows:
        for name, value in row.items():
            if not any(token in str(name).lower() for token in ("pct", "rate", "percent", "ratio")):
                continue
            try:
                number = float(value)
            except TypeError, ValueError:
                continue
            if number > 100:
                flagged.append(f"{name} = {value} 超过 100%，分子与分母口径不同源")
    return flagged


def _load_db() -> Any:
    """Import the workspace's own _db so we connect exactly as the tools do."""
    spec = importlib.util.spec_from_file_location("_oa_build_db", WORKSPACE / "mock-mcp" / "_db.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load mock-mcp/_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _classify(rows: list[dict[str, Any]], columns: list[str]) -> str:
    if len(rows) == 1 and len(columns) == 1:
        return "scalar"
    return "row" if len(rows) == 1 else "table"


def _is_degenerate(rows: list[dict[str, Any]], columns: list[str], cells: list[list[str]]) -> bool:
    """True when mock simply has no data for this question.

    A single 0 counts as no data too: 「刘玮负责多少个重点任务？」 returns 0 because
    刘玮 is absent from mock, so grading it measures whether the model says
    「查不到」 rather than whether it can retrieve.  Questions that genuinely ask
    an existence question carry `expect_empty` and are kept.
    """
    if not rows:
        return True
    return len(rows) == 1 and len(columns) == 1 and cells[0][0] in {"0", "None", ""}


def build(source: Path, out: Path, *, exclude_flagged: bool = False) -> int:
    db = _load_db()
    records = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]

    kept: list[dict[str, Any]] = []
    blocked: list[tuple[str, str]] = []
    degenerate: list[tuple[str, str]] = []
    errors: list[tuple[str, str]] = []

    connection = db.connect()
    try:
        for record in records:
            missing = tables_used(record.get("gold_sql", "")) - MOCK_TABLES
            if missing:
                blocked.append((record["id"], "+".join(sorted(missing))))
                continue
            # gold_sql uses :name placeholders; pymysql wants %(name)s.
            bound = _NAMED_PARAM.sub(lambda m: f"%({m.group(1)})s", record["gold_sql"])
            params = dict(record.get("params") or {})
            rebound = sorted(k for k in params if k.lower() in _AS_OF_PARAMS)
            for key in rebound:
                params[key] = MOCK_AS_OF
            try:
                cursor = connection.cursor()
                cursor.execute(bound, params)
                rows = cursor.fetchall()
                cursor.close()
            except Exception as exc:
                errors.append((record["id"], f"{type(exc).__name__}: {exc}"[:150]))
                continue

            columns = list(rows[0].keys()) if rows else []
            cells = [["" if value is None else str(value) for value in row.values()] for row in rows]
            if _is_degenerate(rows, columns, cells) and not record.get("expect_empty"):
                degenerate.append((record["id"], record["question"][:40]))
                continue

            caliber_gaps = audit_caliber(record["gold_sql"], record.get("question", ""))
            caliber_gaps.extend(audit_answer(rows))
            item = {
                "id": "OA-" + record["id"],
                "type_id": record["type_id"],
                "category": record["category"] + "（oa_biz）",
                "type": record["type"],
                "difficulty": DIFFICULTY.get(record["difficulty"], "medium"),
                "question": record["question"],
                "kind": _classify(rows, columns),
                "gold_sql": record["gold_sql"],
                "gold_answer": {"columns": columns, "rows": cells},
                "gold_row_count": len(rows),
                "traps": record.get("traps", []),
                "oa_expected": record.get("expected"),
                "snapshot_note": SNAPSHOT_NOTE,
            }
            if rebound:
                item["as_of_rebound"] = {"params": rebound, "to": MOCK_AS_OF}
            if caliber_gaps:
                item["caliber_gaps"] = caliber_gaps
            kept.append(item)
    finally:
        connection.close()

    written = [item for item in kept if not item.get("caliber_gaps")] if exclude_flagged else kept
    out.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in written) + "\n",
        encoding="utf-8",
    )
    _report(kept, blocked, degenerate, errors, source, out, exclude_flagged=exclude_flagged)
    return 0 if written and not errors else 1


def _report(
    kept: list[dict[str, Any]],
    blocked: list[tuple[str, str]],
    degenerate: list[tuple[str, str]],
    errors: list[tuple[str, str]],
    source: Path,
    out: Path,
    *,
    exclude_flagged: bool = False,
) -> None:
    flagged_count = sum(1 for item in kept if item.get("caliber_gaps"))
    written_count = len(kept) - flagged_count if exclude_flagged else len(kept)
    print(f"源题库 {source}")
    print(f"入库 {written_count} 题 -> {out}")
    if exclude_flagged:
        print(f"（--exclude-flagged 生效：另有 {flagged_count} 题因 gold_sql 口径存疑未写入）")
    print(f"因缺 10 张新表跳过 {len(blocked)} 题；mock 无数据剔除 {len(degenerate)} 题；SQL 报错 {len(errors)}")

    for label, key in (("类别", "category"), ("难度", "difficulty"), ("形态", "kind")):
        counts = collections.Counter(str(item[key]) for item in kept)
        print(f"\n按{label}：")
        for name, count in sorted(counts.items()):
            print(f"  {name:<18} {count}")

    rebound = [item for item in kept if item.get("as_of_rebound")]
    print(f"\nas_of 重锚到 {MOCK_AS_OF} 的题：{len(rebound)}")
    for item in rebound:
        print(f"  {item['id']:<13} params={','.join(item['as_of_rebound']['params'])}")

    flagged = [item for item in kept if item.get("caliber_gaps")]
    print(f"\ngold_sql 口径存疑的题：{len(flagged)} / {len(kept)}")
    print("  这些题的参考答案不加正式口径闸门算出，评分时不应据它反推工具有错；")
    print("  逐题复核后再决定采信或剔出评分集（--exclude-flagged 可直接剔除）。")
    gap_counts = collections.Counter(gap for item in flagged for gap in item["caliber_gaps"])
    for gap, count in gap_counts.most_common():
        print(f"    {gap:<34} {count}")
    for item in flagged:
        print(f"  {item['id']:<13} {'; '.join(item['caliber_gaps'])}")

    if blocked:
        print("\n卡在缺表的题（按缺哪张表）：")
        for qid, missing in blocked:
            print(f"  {qid:<10} {missing}")
    if degenerate:
        print("\nmock 无数据被剔除的题：")
        for qid, question in degenerate:
            print(f"  {qid:<10} {question}")
    if errors:
        print("\nSQL 报错（需要人看）：")
        for qid, message in errors:
            print(f"  {qid:<10} {message}")


def main() -> int:
    parser = argparse.ArgumentParser(description="用 mock 库重建 oa_biz 题目的参考答案")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="国数给的 oa_biz_200.jsonl")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出的 mock 版答案集")
    parser.add_argument(
        "--exclude-flagged",
        action="store_true",
        help="不写入 gold_sql 口径存疑的题（缺正式口径闸门），只留可信的那部分",
    )
    args = parser.parse_args()

    source = Path(args.source)
    if not source.exists():
        print(f"源题库不存在：{source}")
        return 2
    return build(source, Path(args.out), exclude_flagged=args.exclude_flagged)


if __name__ == "__main__":
    raise SystemExit(main())
