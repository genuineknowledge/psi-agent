"""Formal-source hard guards shared by every ChatBI query.

The 2026-09-08 oa-weekly field note makes the following rules *mandatory* for
the ChatBI agent (they are enforced server-side, never left to the model):

  1. 发布准入(每条任务): ``is_deleted = 0 AND workflow_status = 'published'``
     是唯一问答准入;``task.status`` 只作业务状态参考,不作发布开关。
  2. 进展只取正式展示版本: ``task_progress.is_published = 1`` 且
     ``version_no`` 最大;历史版本问答须任务已发布且版本 ``status = 3``。
  3. 过程信息只取 ``task_workflow_submission.status = 'published'`` 轮次,
     禁止把 pending_*/rejected/cancelled 轮的 payload 当正式数据回答。
  4. 分类路径用 ``task_category.parent_id`` 自关联向上拼,不臆造;
     集团看板内容只在 ``board.code = 'group'`` 且使用 ``task_group_detail``。
  5. 年度目标问答必须显式给 ``year``(``task_year_goal.year``)。
  6. 任何查询都带软删过滤(表有该字段时);长文本为空如实答"未填写"。

These helpers are dialect-neutral building blocks; the SQL fragments for the
MySQL demo and the PostgreSQL formal source live in the two ``sql_*`` maps so a
rule can never be written differently in one place and loosened in another.
"""

from __future__ import annotations

PUBLISHED = "published"

# oa-weekly value domains (2026-09-08 note; demo vocabulary is identical).
WORKFLOW_STATUS_DOMAIN = {
    "draft",
    PUBLISHED,
    "pending_fill",
    "signing",
    "pending_audit",
    "pending_leader",
    "rejected",
}
# Values seen in the mock dataset that mirrors oa-weekly but that the note's
# domain table omits.  They are *non-published* states, so admission still only
# ever matches PUBLISHED; the extra values exist so distribution/reporting tools
# can name them instead of silently dropping those rows.
OBSERVED_EXTRA_WORKFLOW_STATUS = {"cancelled"}
PROGRESS_STATUS_DOMAIN = {0, 1, 2, 3}  # 0草稿 1待审核 2已驳回 3已通过
PROGRESS_APPROVED = 3  # rule 2 exception: 历史版本问答只认已通过版本
SUBMISSION_STATUS_DOMAIN = {
    "pending_fill",
    "signing",
    "pending_audit",
    "pending_leader",
    PUBLISHED,
    "rejected",
    "cancelled",
}
SUBMISSION_KIND_DOMAIN = {"initial", "progress"}
BOARD_CODE_DOMAIN = {"tech", "group"}

# rule 6 / note 2: ``task.status`` varies between oa-weekly versions and is a
# business hint only -- never the publish switch.  Tools quote this line in
# ``caliber`` so the model cannot present the business status as freshness.
BUSINESS_STATUS_NOTE = "task.status 仅业务参考(版本间取值有差异),发布判定一律以 workflow_status='published' 为准"

# Tables that the note lists as optional: queries touching them must first prove
# the grant exists, otherwise the answer degrades instead of erroring.
OPTIONAL_TABLES = (
    "task_group_progress_history",
    "task_workflow_action",
    "task_attachment",
    "task_progress_import",
)


def _pick(domain: set[str], value: str, label: str) -> tuple[str | None, str | None]:
    """Validate ``value`` against a domain; returns (ok_value, error_hint)."""
    if value not in domain:
        ordered = ", ".join(sorted(domain))
        return None, (f"{label} 取值不在值域内({ordered});该过滤条件未生效,结果按未过滤返回")
    return value, None


def check_workflow_status(value: str | None) -> tuple[str | None, str | None]:
    known = WORKFLOW_STATUS_DOMAIN | OBSERVED_EXTRA_WORKFLOW_STATUS
    return _pick(known, value or PUBLISHED, "workflow_status")


def check_submission_status(value: str | None) -> tuple[str | None, str | None]:
    return _pick(SUBMISSION_STATUS_DOMAIN, value or PUBLISHED, "submission.status")


def check_board_code(value: str | None) -> tuple[str | None, str | None]:
    return _pick(BOARD_CODE_DOMAIN, value or "", "board.code")


def check_year(value: int | str | None) -> tuple[int | None, str | None]:
    """Year must be explicit for 年度目标 questions (rule 5)."""
    if value in (None, ""):
        return None, "年度目标问答必须显式给出 year,不能返回全部年度"
    try:
        year = int(value)
    except TypeError, ValueError:
        return None, f"year 不是有效年份:{value!r}"
    if not (2000 <= year <= 2100):
        return None, f"year 超出合理范围:{year}"
    return year, None


def require_optional_table(name: str, granted: bool) -> str | None:
    """Return a degradation hint when an optional table is not granted.

    The note grants eight tables; the four optional ones (``OPTIONAL_TABLES``)
    answer 审批意见 / 集团板历史 / 附件元数据 / 导入来源.  Callers pass what the
    live source actually exposes; the template layer raises ``PermissionError``
    carrying this text and the tool answers with it, so a missing grant degrades
    the answer instead of failing the whole turn.
    """
    if name not in OPTIONAL_TABLES:
        return None
    if granted:
        return None
    return f"{name} 未在本次只读授权范围内,相关问题无法回答(如需请联系数据侧补开 SELECT 授权)"


# ---- dialect fragments -----------------------------------------------------


def sql_task_admission(dialect: str, alias: str = "t") -> str:
    """Rule 1: the only admission set into ChatBI answers."""
    clause = f"{alias}.is_deleted = 0 AND {alias}.workflow_status = 'published'"
    if dialect == "pg":
        return clause
    return clause


def sql_published_progress(dialect: str, alias: str = "p") -> str:
    """Rule 2: current displayed progress version only."""
    if dialect == "pg":
        return f"{alias}.is_published = 1"
    return f"{alias}.is_published = 1"


def sql_historical_progress(dialect: str, alias: str = "p") -> str:
    """Rule 2 exception: historical versions must be approved (``status = 3``)."""
    clause = f"{alias}.status = {PROGRESS_APPROVED}"
    if dialect == "pg":
        return clause
    return clause


def sql_submission_published(dialect: str, alias: str = "s") -> str:
    """Rule 3: only the published submission round carries formal data."""
    if dialect == "pg":
        return f"{alias}.status = 'published'"
    return f"{alias}.status = 'published'"


def sql_soft_delete(alias: str) -> str:
    """Rule 6: every table that has ``is_deleted`` filters on it."""
    return f"{alias}.is_deleted = 0"


def normalize_ts_sql(column: str, dialect: str = "pg") -> str:
    """Return an expression that renders ``column`` as ``YYYY-MM-DD HH:MM:SS``.

    The O2OA data-center note says time fields are stored either as text
    (``YYYY-MM-DD HH:mm:ss``) or as a native timestamp, depending on the
    environment.  ``to_char()`` only accepts a temporal type, so a text column
    would make the whole query fail with ``function to_char(text, unknown) does
    not exist``; casting first makes one expression work for both shapes (the
    cast is a no-op for a timestamp column, and parses the documented text
    format).  The confirmed storage type is re-checked during联调; this helper
    is the single place to adjust it.
    """
    if dialect == "pg":
        return f"to_char(({column})::timestamp, 'YYYY-MM-DD HH24:MI:SS')"
    return f"DATE_FORMAT(({column}), '%Y-%m-%d %H:%i:%s')"


def parse_ts_sql(column: str, dialect: str = "pg") -> str:
    """Return ``column`` as a comparable temporal value (for range filters).

    Comparing the raw column would compare text lexically when the environment
    stores text; use this in ``WHERE``/``ORDER BY`` whenever a date window is
    involved (PDF 六-3).
    """
    if dialect == "pg":
        return f"({column})::timestamp"
    return f"STR_TO_DATE(({column}), '%Y-%m-%d %H:%i:%s')"
