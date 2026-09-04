"""Audit the three question banks for defective reference answers.

The banks were generated with model assistance, so a wrong reference answer is a
normal outcome rather than an exception -- and a wrong reference answer is worse
than a missing one, because grading against it pushes the implementation toward
the defect. Every family below was first found by hand on a single question, then
turned into a rule here so the rest of the bank gets the same check.

Confirmed by hand before being encoded (each with the measured numbers):

  ratio_over_100   OA-H5-01  coverage 125/82 = 152.4%, numerator ungated
  fanout           K6-01     COUNT(m.id) over two LEFT JOINs -> 1363, real 294
                             (its own `traps` field says fan_out_double_count)
  rows_as_tasks    C2-02     "how many TASKS reported" counts 943 rows,真值 73
  empty_filter     M3-03     status <> 'approved' where 'approved' is not in the
                             column domain, so the predicate filters nothing
  period_dropped   C2-01     asks 「这一期」, SQL has no period filter (943 rows)
  limit_vs_wording R7-03     gold LIMIT 10 while the question never asks for 10
  sibling_conflict V1-04     LIMIT 1 vs F1-01/L2-02 HAVING = MAX, same question
  as_of_drift      E6-01     params as_of 2026-08-18 against a 2026-08-15 mock

Usage (mock service not required, only MySQL):
    python tests/audit_question_banks.py
    python tests/audit_question_banks.py --out C:/tmp/audit
"""

# ruff: noqa: RUF001, RUF003  中文口径文案里的全角标点是给人看的正文, 不能换成半角。
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
HOME = Path.home()

BANKS: tuple[tuple[str, Path], ...] = (
    ("nl2sql-396", HOME / "Downloads" / "nl2sql-answers.jsonl"),
    ("oa_biz-200", HOME / "Downloads" / "oa_biz_200.jsonl"),
    ("oa_biz-mock-127", HOME / "Downloads" / "oa_biz-mock-answers.jsonl"),
    ("g93", WORKSPACE / "tests" / "g93-answers.jsonl"),
)

MOCK_AS_OF = "2026-08-15"

_TABLE_REF = re.compile(r"(?:FROM|JOIN)\s+`?(\w+)`?", re.IGNORECASE)
_NAMED_PARAM = re.compile(r":(\w+)")
_LIMIT_N = re.compile(r"\bLIMIT\s+(\d+)", re.IGNORECASE)
_DATE_LITERAL = re.compile(r"'(\d{4}-\d{2}-\d{2})")

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
"""Child tables with many rows per task: a JOIN to them multiplies task rows."""

_AS_OF_KEYS = frozenset({"as_of", "asof", "today", "now", "ref_date", "snapshot"})

# 问句里出现这些词，说明问的是「几个任务」而不是「几行」。
_ASKS_TASKS = ("多少个任务", "多少任务", "几个任务", "多少条任务", "任务有多少")
# 问句里出现这些词，说明要收敛到当期/最新一期，而不是铺开全部历史。
_ASKS_PERIOD = ("这一期", "当期", "本期", "这期", "最新一期", "最近一期", "最新的一期")
# 问句里出现这些词才允许 gold 用 LIMIT 截断。
_ASKS_TOPN = ("前", "top", "最多", "最少", "最长", "最短", "最大", "最小", "第一", "榜首", "几个最")


def _load_db() -> Any:
    spec = importlib.util.spec_from_file_location("_audit_db", WORKSPACE / "mock-mcp" / "_db.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load mock-mcp/_db.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tables_used(sql: str) -> set[str]:
    return {name.lower() for name in _TABLE_REF.findall(sql or "")}


# 闸门类规则已整体删除，不是简化而是它测错了东西。
#
# 原规则：凡 SQL 碰任务域就要求 task 表的 is_deleted + workflow_status，碰
# task_progress 就要求 is_published。它在三库上报出 198 处，我手工抽验 8 道，
# 8 道全是误报：
#   H4-01「里程碑表里有多少条被软删了」——问的正是软删审计本身，故意不加闸门，
#          而且一次给全 566/36/602 三个数，是正确做法
#   I1-01/02/03「在途提交单」——提交单不该加任务发布闸门，在途任务的提交单同样
#          是提交单（这条口径我自己写在 weekly_submission_query by_kind 的注释里）
#   O3-01「任务 2 有哪些附件」——已带 a.is_deleted = 0，只是不 JOIN task 表
#   M1-03「附件表都有哪些字段」——查 information_schema，没有数据行可过滤
#
# 根因：「正式任务闸门」从来不是所有题都该加的。审批单、附件、里程碑各有自己的
# 口径——这正是我为审批表补裸表六档的理由——而我写规则时把它当成了普遍要求。
# 一个测不准的审计比没有审计更坏：它会把正确的参考答案报成缺陷，据此改代码就把
# 对的实现改坏了。要恢复这类检查，必须先有一份「哪些表在哪些问法下该加哪道闸门」
# 的口径表，逐题比对，而不是对 SQL 文本做关键字存在性判断。


def _run_scalar(connection: Any, sql: str, params: dict[str, Any]) -> Any:
    """Execute a rewritten SQL and return its first cell, or None if it will not run."""
    bound = _NAMED_PARAM.sub(lambda m: f"%({m.group(1)})s", sql)
    try:
        with connection.cursor() as cursor:
            cursor.execute(bound, params)
            row = cursor.fetchone()
    except Exception:
        return None
    if not row:
        return None
    return next(iter(row.values()), None)


def _run_rowcount(connection: Any, sql: str, params: dict[str, Any]) -> int | None:
    bound = _NAMED_PARAM.sub(lambda m: f"%({m.group(1)})s", sql)
    try:
        with connection.cursor() as cursor:
            cursor.execute(bound, params)
            return len(cursor.fetchall())
    except Exception:
        return None


def _params_for(record: dict[str, Any]) -> dict[str, Any]:
    """Bind params, rebinding any snapshot anchor to the mock's own day."""
    params = dict(record.get("params") or {})
    for key in list(params):
        if key.lower() in _AS_OF_KEYS:
            params[key] = MOCK_AS_OF
    for name in _NAMED_PARAM.findall(record.get("gold_sql") or ""):
        if name not in params:
            if name.lower() in _AS_OF_KEYS:
                params[name] = MOCK_AS_OF
            else:
                return {}  # 缺参数就跑不了，交给调用方跳过
    return params


def audit_fanout(
    connection: Any,
    record: dict[str, Any],
    question: str,
    sql: str,
    lowered: str,
    tables: set[str],
) -> list[dict[str, str]]:
    """COUNT(*) counting rows where the question asks for a task count.

    Differential execution is a hard gate here: a finding is emitted only when
    swapping COUNT(*) for COUNT(DISTINCT t.id) actually RETURNS A DIFFERENT VALUE.
    The previous version judged from SQL text and scored 0 true positives out of 19
    when 42 findings were hand-checked:
      B7-03 was flagged for "COUNT(*) over a one-to-many JOIN" -- that SQL has no
            JOIN at all; task_progress appears only inside a NOT EXISTS anti-join,
            so both spellings are identically 55.
      C4-04 already collapses the child table to one row per task via
            ROW_NUMBER() ... rn = 1, so both spellings agree there too.
    "Could fan out" and "does fan out" are different claims; only running it tells
    them apart.
    """
    if "count(*)" not in lowered or not (tables & _ONE_TO_MANY):
        return []
    asks = "任务" in question and any(w in question for w in ("多少", "几个", "几条"))
    if not (asks or any(w in question for w in _ASKS_TASKS)):
        return []
    params = _params_for(record)
    if not params and _NAMED_PARAM.search(sql):
        return []
    gold_value = _run_scalar(connection, sql, params)
    if gold_value is None:
        return []
    # 只替换聚合本身，不动任何过滤或分组：差异就只能来自去重。
    variant = re.sub(r"count\(\s*\*\s*\)", "COUNT(DISTINCT t.id)", sql, flags=re.IGNORECASE)
    if variant == sql:
        return []
    fixed_value = _run_scalar(connection, variant, params)
    if fixed_value is None or str(fixed_value) == str(gold_value):
        return []
    return [
        {
            "family": "rows_as_tasks",
            "detail": (
                f"问的是任务数，但 COUNT(*) 数的是行数：gold 得 {gold_value}，"
                f"换成 COUNT(DISTINCT t.id) 得 {fixed_value}（实测两值不同，确认扇出）"
            ),
        }
    ]


def audit_period(
    connection: Any,
    record: dict[str, Any],
    question: str,
    sql: str,
    tables: set[str],
) -> list[dict[str, str]]:
    """Question scopes to one period but the SQL returns many rows per task.

    Same hard gate: emit only when the measured row count EXCEEDS the number of
    distinct tasks in the result. The previous version hunted for convergence
    keywords (max(version_no), rn = 1, ...) and scored 0 true positives out of 6 --
    there are too many ways to converge to enumerate, and in this corpus the word
    used for "current period" usually means "the published snapshot" rather than
    "the newest version" (siblings C2-02 and C2-04 are both written that way).
    Whether rows actually outnumber tasks is a question for the database.
    """
    if not any(word in question for word in _ASKS_PERIOD):
        return []
    if not (tables & {"task_progress", "task_group_progress_history"}):
        return []
    params = _params_for(record)
    if not params and _NAMED_PARAM.search(sql):
        return []
    rows = _run_rowcount(connection, sql, params)
    if rows is None or rows <= 1:
        return []
    # 涉及任务数：把选择列换成 COUNT(DISTINCT) 不可靠（子查询形态各异），
    # 改为把整条 SQL 包一层，数它自己返回的 task 标识有几个不同值。
    ident = next(
        (col for col in ("task_id", "id", "task_name") if re.search(rf"\b{col}\b", sql, re.IGNORECASE)),
        None,
    )
    if ident is None:
        return []
    wrapped = f"SELECT COUNT(DISTINCT `{ident}`) AS n FROM ({sql}) AS _audit_sub"
    tasks = _run_scalar(connection, wrapped, params)
    if tasks is None or int(tasks) >= rows:
        return []
    return [
        {
            "family": "period_dropped",
            "detail": (
                f"问句限定当期/最新一期，但 gold 返回 {rows} 行只涉及 {tasks} 个任务"
                f"（同一任务多期都在内，实测确认未收敛）"
            ),
        }
    ]


_CN_NUMERALS = "一二三四五六七八九十两"


def audit_limit(connection: Any, record: dict[str, Any], question: str, sql: str) -> list[dict[str, str]]:
    """A LIMIT that truncates a set the question asked for in full.

    Two failures from the previous version are closed here:
      D4-01 asks for "the most recent three periods" -- the count is written in
            Chinese numerals, which an Arabic-digit regex never saw.
      D6-01's LIMIT 8 happens to equal the true row count, so the truncation is
            inert and reporting it is noise.
    So this version reads Chinese numerals too, and requires differential
    execution: dropping the LIMIT must actually yield more rows. It also fires
    only when the question asks for a complete set -- taking the head of a
    "most recent / largest" question is legitimate.
    """
    match = _LIMIT_N.search(sql or "")
    if not match:
        return []
    n = int(match.group(1))
    if n == 1:
        # LIMIT 1 常是「取最值」的合法写法，交给 sibling 检查去判并列。
        return []
    lowered_q = question.lower()
    if any(word in lowered_q for word in _ASKS_TOPN):
        return []
    if str(n) in question:
        return []
    # A Chinese numeral only counts as a quantity when a measure word follows it.
    # Matching the bare character misfired on both real defects found by hand:
    # E6-04 and I8-04 each contain 不一致, whose 一 is not a count at all, so the
    # two confirmed truncations were filtered out. D4-01's 最近三期 still matches,
    # because there the numeral is followed by a measure word.
    if re.search(rf"[{_CN_NUMERALS}](?=[期条个批位人天月年份组张次])", question):
        return []
    # 只有问句明确要「全部」时，截断才是缺陷。
    if not any(word in question for word in ("有哪些", "都有", "列一下", "哪些", "全部", "各是")):
        return []
    params = _params_for(record)
    if not params and _NAMED_PARAM.search(sql):
        return []
    gold_rows = _run_rowcount(connection, sql, params)
    if gold_rows is None:
        return []
    without = _LIMIT_N.sub("", sql, count=1)
    full_rows = _run_rowcount(connection, without, params)
    if full_rows is None or full_rows <= gold_rows:
        return []
    return [
        {
            "family": "limit_vs_wording",
            "detail": (
                f"问句要完整集合，gold 用 LIMIT {n} 截成 {gold_rows} 行；"
                f"去掉 LIMIT 实测 {full_rows} 行，丢了 {full_rows - gold_rows} 行"
            ),
        }
    ]


def audit_traps(record: dict[str, Any], lowered: str, tables: set[str]) -> list[dict[str, str]]:
    """The question declares a trap that its own SQL falls into."""
    traps = record.get("traps") or []
    names = {str(t).lower() if not isinstance(t, dict) else str(t.get("name", "")).lower() for t in traps}
    blob = " ".join(names)
    found: list[dict[str, str]] = []
    if "fan_out" in blob or "double_count" in blob:
        multi_join = lowered.count(" join ") >= 2
        if multi_join and "count(distinct" not in lowered and "count(*)" not in lowered:
            found.append(
                {
                    "family": "trap_self_violation",
                    "detail": "自标 fan_out/double_count 陷阱，但 SQL 在多个 JOIN 上未去重",
                }
            )
    # soft_delete / publish_gate 这两个标签不再据 SQL 文本判定，理由同上面删掉的
    # 闸门规则：标签的意思是「本题要留意这道闸门」，而闸门该落在哪张表随问法而变。
    # 收窄一版后仍剩 4 道，查表结构后确认 4 道也全是误报——
    # task_group_progress_history 与 task_workflow_submission 根本没有 is_deleted
    # 列（实测 SHOW COLUMNS），标 soft_delete 指的是关联的 task 要过软删，
    # 而 I8-03 / K1-04 都带了 t.is_deleted = 0。R2-03 则已带 h.is_published = 1。
    # 只保留 fan_out 那一条：它比对的是「多 JOIN 且未去重」这个结构事实，
    # 不依赖对某张表该加哪道闸门的判断，K6-01 已人工验证成立。
    return found


_STATUS_PREDICATE = re.compile(
    r"(\w+)\.(\w*status\w*)\s*(=|<>|!=)\s*'([^']+)'",
    re.IGNORECASE,
)

# 列所属的表：SQL 里用的是别名，得映射回真表才能查值域。
_ALIAS_TABLES: dict[str, tuple[str, ...]] = {
    "status": ("task_workflow_submission", "task_progress", "task", "task_milestone"),
    "workflow_status": ("task",),
}


def audit_empty_filter(connection: Any, sql: str, cache: dict[tuple[str, str], set[str]]) -> list[dict[str, str]]:
    """A status predicate whose literal is not in the column's own domain.

    M3-03 filters `status <> 'approved'` on task_workflow_submission, whose domain
    holds no 'approved' at all -- so the predicate removes zero rows and the
    question's "尚未发布" intent is silently dropped. An `=` against a missing
    value is the mirror image: it can only ever return nothing.
    """
    found: list[dict[str, str]] = []
    for _alias, column, op, literal in _STATUS_PREDICATE.findall(sql or ""):
        col = column.lower()
        for table in _ALIAS_TABLES.get(col, ()):
            if table not in tables_used(sql):
                continue
            key = (table, col)
            if key not in cache:
                try:
                    with connection.cursor() as cursor:
                        cursor.execute(f"SELECT DISTINCT `{col}` AS v FROM `{table}`")
                        cache[key] = {str(row["v"]) for row in cursor.fetchall() if row["v"] is not None}
                except Exception:
                    cache[key] = set()
            domain = cache[key]
            if not domain or literal in domain:
                continue
            if op == "=":
                found.append(
                    {
                        "family": "empty_filter",
                        "detail": (
                            f"{table}.{col} = '{literal}'，但该列值域不含此值"
                            f"（值域：{', '.join(sorted(domain))}）——条件永不成立，结果恒为空"
                        ),
                    }
                )
            else:
                found.append(
                    {
                        "family": "empty_filter",
                        "detail": (
                            f"{table}.{col} <> '{literal}'，但该列值域不含此值"
                            f"（值域：{', '.join(sorted(domain))}）——该条件筛不掉任何行，等于没过滤"
                        ),
                    }
                )
            break
    return found


def audit_answer_values(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Reference values that are impossible on their face."""
    found: list[dict[str, str]] = []
    for row in rows:
        for name, value in row.items():
            label = str(name).lower()
            if not any(token in label for token in ("pct", "rate", "percent", "ratio", "占比", "率")):
                continue
            try:
                number = float(value)
            except TypeError, ValueError:
                continue
            if number > 100:
                found.append(
                    {
                        "family": "ratio_over_100",
                        "detail": f"{name} = {value} 超过 100%，分子与分母口径不同源",
                    }
                )
            elif number < 0:
                found.append({"family": "ratio_negative", "detail": f"{name} = {value} 为负"})
    return found


def audit_as_of(record: dict[str, Any]) -> list[dict[str, str]]:
    """Snapshot anchors that disagree with the mock's own day."""
    found: list[dict[str, str]] = []
    params = record.get("params") or {}
    for key, value in params.items():
        if key.lower() in _AS_OF_KEYS and str(value)[:10] != MOCK_AS_OF:
            found.append(
                {
                    "family": "as_of_drift",
                    "detail": f"params.{key} = {value}，与 mock 快照日 {MOCK_AS_OF} 不一致（相对时间答案会整体偏移）",
                }
            )
    declared = str(record.get("as_of") or "")[:10]
    if declared and declared != MOCK_AS_OF:
        found.append(
            {
                "family": "as_of_drift",
                "detail": f"题目声明 as_of = {declared}，mock 锚 {MOCK_AS_OF}",
            }
        )
    return found


_SUPERLATIVE = ("最多", "最少", "最长", "最短", "最大", "最小", "最高", "最低")


def audit_siblings(records: list[dict[str, Any]]) -> dict[str, list[dict[str, str]]]:
    """Sibling questions asking the same thing under opposite tie rules.

    V1-04「技术组这边谁手上的任务最多」uses LIMIT 1 while F1-01「谁手里的任务最多」
    and L2-02「任务量最大的那个负责人是谁」use HAVING = MAX. Nothing in the wording
    separates them -- L2-02 even says 「那个」, which reads MORE singular than V1-04 --
    so whichever rule the implementation picks, some sibling grades it wrong.
    Grouped by (superlative wording, the column being ranked) so the pairing is
    about one real axis rather than any two questions that share a word.
    """
    buckets: dict[tuple[str, str], list[tuple[str, str]]] = collections.defaultdict(list)
    for record in records:
        question = str(record.get("question") or "")
        sql = " ".join(str(record.get("gold_sql") or "").split())
        if not sql:
            continue
        word = next((w for w in _SUPERLATIVE if w in question), "")
        if not word:
            continue
        lowered = sql.lower()
        if "having" in lowered and "max(" in lowered:
            rule = "keep_ties (HAVING = MAX)"
        elif _LIMIT_N.search(lowered):
            rule = "hard_cut (LIMIT n)"
        else:
            continue
        # 被排名的列：取 GROUP BY 的第一个字段名，作为「同一根轴」的判据。
        axis_match = re.search(r"group by\s+([\w.]+)", lowered)
        axis = axis_match.group(1).split(".")[-1] if axis_match else "?"
        buckets[(word, axis)].append((str(record.get("id")), rule))

    out: dict[str, list[dict[str, str]]] = collections.defaultdict(list)
    for (word, axis), members in buckets.items():
        rules = {rule for _qid, rule in members}
        if len(rules) < 2:
            continue
        for qid, rule in members:
            others = [f"{other}={r}" for other, r in members if other != qid]
            out[qid].append(
                {
                    "family": "sibling_conflict",
                    "detail": (
                        f"同问「{word}」且同按 {axis} 排名，本题用 {rule}，"
                        f"兄弟题用了相反规则：{', '.join(others)}——问句分不出该用哪套"
                    ),
                }
            )
    return out


def _answer_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalise the several answer shapes the banks use into row dicts."""
    answer = record.get("gold_answer")
    if isinstance(answer, dict) and "columns" in answer:
        cols = answer.get("columns") or []
        return [dict(zip(cols, row, strict=False)) for row in (answer.get("rows") or [])]
    if isinstance(answer, dict):
        return [answer]
    return []


def audit_bank(name: str, path: Path, connection: Any) -> dict[str, Any]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    sibling = audit_siblings(records)
    domain_cache: dict[tuple[str, str], set[str]] = {}
    findings: dict[str, list[dict[str, str]]] = collections.defaultdict(list)

    for record in records:
        qid = str(record.get("id"))
        question = str(record.get("question") or "")
        sql = " ".join(str(record.get("gold_sql") or "").split())
        hits: list[dict[str, str]] = []
        if sql:
            lowered = sql.lower()
            tables = tables_used(sql)
            hits += audit_fanout(connection, record, question, sql, lowered, tables)
            hits += audit_period(connection, record, question, sql, tables)
            hits += audit_limit(connection, record, question, sql)
            hits += audit_traps(record, lowered, tables)
            hits += audit_empty_filter(connection, sql, domain_cache)
        hits += audit_answer_values(_answer_rows(record))
        hits += audit_as_of(record)
        hits += sibling.get(qid, [])
        if hits:
            findings[qid] = hits

    return {"bank": name, "path": str(path), "total": len(records), "findings": dict(findings)}


def main() -> int:
    parser = argparse.ArgumentParser(description="审查三个题库的参考答案缺陷")
    parser.add_argument("--out", default=str(HOME / "_bank_audit"), help="报告输出目录")
    args = parser.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    db = _load_db()
    connection = db.connect()
    reports = []
    try:
        for name, path in BANKS:
            if not path.exists():
                print(f"[skip] {name}: 文件不存在 {path}")
                continue
            report = audit_bank(name, path, connection)
            reports.append(report)
            flagged = len(report["findings"])
            print(f"\n=== {name} === {flagged}/{report['total']} 题命中")
            counts: collections.Counter[str] = collections.Counter(
                hit["family"] for hits in report["findings"].values() for hit in hits
            )
            for family, count in counts.most_common():
                print(f"    {family:<26} {count}")
    finally:
        connection.close()

    (out_dir / "audit.json").write_text(
        json.dumps(reports, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    lines: list[str] = ["# 题库审查报告", ""]
    for report in reports:
        lines.append(f"## {report['bank']}（{len(report['findings'])}/{report['total']} 题命中）")
        lines.append("")
        for qid, hits in sorted(report["findings"].items()):
            lines.append(f"- **{qid}**")
            for hit in hits:
                lines.append(f"  - `{hit['family']}` {hit['detail']}")
        lines.append("")
    (out_dir / "audit.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"\n报告写入 {out_dir / 'audit.json'} 与 {out_dir / 'audit.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
