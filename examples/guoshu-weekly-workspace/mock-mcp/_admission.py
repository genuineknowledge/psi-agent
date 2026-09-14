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
  7. **可选开关**:``GUOSHU_WEEKLY_EXCLUDE_TEST_LIKE`` 打开后,名字像测试/演示的任务
     不进**正式**口径。这是**客户的口径决定**(那几条是他们台账里自己的行),所以
     默认关;判据只写在 ``sql_test_like_exclusion`` 一处,且贴在正式门**里面** ——
     计数类模板与清单类模板因此同增同减。

These helpers are dialect-neutral building blocks; the SQL fragments for the
MySQL demo and the PostgreSQL formal source live in the two ``sql_*`` maps so a
rule can never be written differently in one place and loosened in another.
"""

from __future__ import annotations

import os

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


def sql_task_admission_plain(dialect: str, alias: str = "t") -> str:
    """Rule 1 **原文**:正式任务的准入集,不含 rule 7 那层剔除。

    ``dialect`` 收下但不用:与 ``sql_task_admission`` 同一形状(演示与正式源当前共用
    这套措辞,``TestAdmissionRules.test_published_only`` 钉着这条方言无关性),
    参数位对齐是为了将来某一边真要分开时不必动所有调用点。
    需要"剔除以外"的正式门时用它 —— 例如数一下被剔掉了几条(见
    ``_o2oa_templates.test_like_excluded_total``)。
    """
    return f"{alias}.is_deleted = 0 AND {alias}.workflow_status = 'published'"


def sql_task_admission(dialect: str, alias: str = "t") -> str:
    """Rule 1 (+ rule 7):the only admission set into ChatBI answers.

    开关打开时把"名字像测试数据"那几条剔在**正式门里面**,而不是在清单出口上删行 ——
    所以计数类模板(`count(*)`)与清单类模板(`SELECT ... LIMIT`)看到的是**同一个集合**:
    不可能出现"列表里删了、数还留着"。

    剔除只加在 ``pg``(正式源)这一支。开关问的是**客户的正式台账**要不要去掉那几条,
    演示快照是冻结的合成数据(它的每个数都有契约用例钉着),一行都不许因此动。
    """
    clause = sql_task_admission_plain(dialect, alias)
    if dialect == "pg" and exclude_test_like():
        return f"{clause} AND {sql_test_like_exclusion(alias)}"
    return clause


# ---- rule 7:名称像测试数据的剔除(可选开关,默认关) --------------------------

#: 开关变量名。取值 ``1`` / ``true`` / ``yes``(大小写不敏感)视为开,其余与不设都视为关。
EXCLUDE_TEST_LIKE_ENV = "GUOSHU_WEEKLY_EXCLUDE_TEST_LIKE"

#: 名字含其中任一子串(不区分大小写)即算"像测试数据"。**判据只写在这一行**:
#: SQL 谓词、以及口径自述里那个"剔了几条"的条数,都从它派生。
TEST_LIKE_SUBSTRINGS: tuple[str, ...] = (
    "test",
    "测试",
    "演示",
    "示例",
    "demo",
    "流程验证",
    "完整流程",
)

_TEST_LIKE_ARRAY = "ARRAY[{}]".format(", ".join(f"'%{word}%'" for word in TEST_LIKE_SUBSTRINGS))
#: 剔除谓词的**内核**(与别名无关)。``sql_test_like_exclusion`` 拼它,
#: ``has_test_like_exclusion`` 也拿它认 SQL —— 同一个常量,换别名、加词都不会失配。
_TEST_LIKE_EXCLUDED = f"NOT ILIKE ALL ({_TEST_LIKE_ARRAY})"


def exclude_test_like() -> bool:
    """开关是否打开;读法与 ``_formal.enabled()`` 同一形状(去空白 + 小写 + 白名单)。

    **默认关**是硬要求:不设这个变量时,正式门的那句 SQL 与改动前逐字节相同,
    正式任务总数(实测 88)也照旧。剔不剔是客户的口径决定 —— 那 8 条是他们自己
    台账里的行,我们只提供开关,不替他们定。
    """
    raw = os.environ.get(EXCLUDE_TEST_LIKE_ENV, "").strip().lower()
    return raw in ("1", "true", "yes")


def sql_test_like_exclusion(alias: str = "t") -> str:
    """剔除谓词:名字像测试数据的行**不**进正式口径(判据的唯一一处实现)。

    用 ``coalesce`` 兜住 NULL:``NOT (NULL ILIKE ...)`` 求值仍是 NULL,写成
    ``NOT ({alias}.task_name ILIKE ...)`` 会把**任务名为空的行一起剔掉**——
    那不是选中的判据,是 SQL 三值逻辑的意外。
    """
    return f"coalesce({alias}.task_name, '') {_TEST_LIKE_EXCLUDED}"


def sql_test_like_match(alias: str = "t") -> str:
    """上面那条的**补集**(德摩根):名字像测试数据的行。

    口径自述要报"剔了几条",数的是它。两半共用同一份词表、互为补集,所以
    "自述里 8 条"与"结果少掉 8 条"必然一致。写法与剔除那条**不同**
    (``ILIKE ANY`` vs ``NOT ILIKE ALL``),于是 ``has_test_like_exclusion``
    不会把"数被剔了几条"的那条 SQL 误认成"这条已经剔过了"(两者语义正相反)。
    """
    return f"coalesce({alias}.task_name, '') ILIKE ANY ({_TEST_LIKE_ARRAY})"


def has_test_like_exclusion(sql: str) -> bool:
    """``sql`` 是否已把剔除当**筛选条件**(别名无关,照样认 ``t`` / ``t2``)。

    口径自述只加在真正受影响的那条返回上:模板里带正式任务门的加;刻意不带门的档
    (裸表口径 ``whole_table``、在途提交单、审批动作、``unpublished_by_task``)不加 ——
    给它们加一句"已剔除 N 条"是在描述一件没发生的事。
    """
    return _TEST_LIKE_EXCLUDED in sql


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


def normalize_date_sql(column: str, dialect: str = "pg") -> str:
    """Render a date-valued column as ``YYYY-MM-DD`` text.

    ``task_progress.progress_date`` is declared ``date`` by the note, but the demo
    snapshot stores it as text -- the same text-or-native split as the timestamp
    columns.  Selecting it raw makes the answer envelope carry either a
    ``datetime.date`` (formal source) or a ``str`` (text-backed source); casting
    to ``date`` first then formatting gives every caller one stable shape, and is
    a no-op on the formal source.
    """
    if dialect == "pg":
        return f"to_char(({column})::date, 'YYYY-MM-DD')"
    return f"DATE_FORMAT(({column}), '%Y-%m-%d')"
