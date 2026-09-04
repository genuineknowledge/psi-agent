from __future__ import annotations

import hashlib
import sys
import types
from pathlib import Path

_MCP_PATH = Path(__file__).resolve().parent / "_weekly_mcp.py"
_MODULE_NAME = f"guoshu_weekly_tool__weekly_mcp_{hashlib.sha256(str(_MCP_PATH).encode()).hexdigest()[:12]}"
_module = sys.modules.get(_MODULE_NAME)
if _module is None:
    _module = types.ModuleType(_MODULE_NAME)
    _module.__file__ = str(_MCP_PATH)
    sys.modules[_MODULE_NAME] = _module
    exec(compile(_MCP_PATH.read_text(encoding="utf-8"), str(_MCP_PATH), "exec"), _module.__dict__)
_call = _module.__dict__["call"]
_invalid = _module.__dict__["invalid_argument"]


async def weekly_progress_history(task: str, published_only: bool = True, limit: int = 200) -> str:
    """Return one task's progress versions, newest first.

    version_no is unique and monotonically increasing, so the first row is the
    current period. Draft rows (is_published = 0) are excluded by default and
    must not be reported as formal progress.

    Only ONE task's periods come back, even when the name belongs to a series.
    「数据资源登记体系建设」 and its three 期 suffixes are four separate tasks
    with separate progress; a bare name resolves to one of them and the siblings
    arrive under same_name_series. Report the resolved task's periods as that
    task's, mention the series if the question is open-ended, and query again by
    id or by the full name when the caller wants another 期 -- never merge the
    family's periods into one history.

    Args:
        task: Task id or name.
        published_only: True keeps only formally published progress.
        limit: Max versions to return, capped at 200.
    """
    if not task.strip():
        return _invalid("task must not be empty")
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call(
        "weekly_progress_history",
        {"task": task, "published_only": bool(published_only), "limit": bounded},
    )


async def weekly_progress_range(
    date_from: str = "",
    date_to: str = "",
    last_days: int = 0,
    by: str = "",
    date_field: str = "progress_date",
    peak: bool = False,
    limit: int = 200,
) -> str:
    """Query or count published progress in a time window, across all tasks.

    Use this for any "how many / which progress in <period>" question. Walking
    weekly_progress_history task by task cannot answer those: it exhausts the
    tool budget long before the window is covered.

    Relative windows are anchored to the data snapshot date on the server, not to
    today's clock. Do not compute date arithmetic yourself.

    Read total_count / total_tasks for counting questions -- rows may be
    truncated at 200 while the totals stay exact.

    Args:
        date_from: Inclusive start, YYYY-MM-DD. Empty means unbounded.
        date_to: Inclusive end, YYYY-MM-DD. Empty means unbounded.
        last_days: Window of N days ending at the snapshot date; 0 disables it.
        by: Empty lists rows; month / quarter / task returns counts per group.
        date_field: progress_date (period reported on) or report_time (when filed).
        peak: With by set, returns only the highest-count group ("哪个月上报最多").
            The ordering and the tie-break happen on the server; the single row
            you get back is the answer, so do not re-compare bucket counts.
        limit: Max rows to return, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
        days = max(0, int(last_days))
    except TypeError, ValueError:
        return _invalid("limit and last_days must be integers")
    return await _call(
        "weekly_progress_range",
        {
            "date_from": date_from,
            "date_to": date_to,
            "last_days": days,
            "by": by,
            "date_field": date_field,
            "peak": bool(peak),
            "limit": bounded,
        },
    )


async def weekly_task_lifecycle(by: str = "", year: int = 0) -> str:
    """Report when formal tasks were created and how long they took to publish.

    This is task.created_at / published_at -- the setup clock, a different axis
    from progress reporting. Use it for "when was this set up", "how many tasks
    were opened in <year>", "how long from creation to publication".

    Bucketed results carry ``currently_finished`` next to ``created_count``:
    "2025 和 2026 年各完成了多少" needs both, and the task table has no completion
    date, so the finished figure is the CURRENT status of tasks opened in that
    bucket (2025: 105 opened / 26 now done, 2026: 23 / 5) -- not "finished during
    that year". The 31 finished tasks store-wide split 26 + 5 across the two years.

    Args:
        by: Empty returns a min/max/average summary; month or year counts per bucket.
        year: Restrict to one creation year; 0 means all years.
    """
    try:
        yr = max(0, int(year))
    except TypeError, ValueError:
        return _invalid("year must be an integer")
    return await _call("weekly_task_lifecycle", {"by": by, "year": yr})


async def weekly_freshness_distribution(
    task: str = "",
    within_days: int = 0,
    drift: bool = False,
    stale_days: int = 0,
    recent_days: int = 0,
    in_flight: bool = False,
    by: str = "",
    reported_only: bool = False,
    lag_bands: bool = False,
    limit: int = 200,
) -> str:
    """Report how stale progress is: 30/90/180-day buckets, a custom window, or drift.

    The default bucket view also carries newest_progress and days_behind, which
    answer "how current is the board overall", plus task_total so the buckets can
    be checked against it -- they sum to it.

    The "4 从未报进展" bucket keys off t.latest_progress_time being NULL, which is
    the 9 tasks that reported into NEITHER table -- the answer to "从来没报过进展的
    任务有哪些". The other reading, "no published row in task_progress", is 55 and
    includes all 46 group-board tasks, whose effect text lives in
    task_group_progress_history instead. weekly_progress_coverage
    scope="never_reported" returns both: total_count 55 and
    never_reported_either_table 9. Pick by the question; neither supersedes the
    other.

    stale_days and recent_days are the LISTINGS behind "哪些任务很久没上报" and
    "最近有哪些任务上报了". Both anchor to the snapshot date on the server.

    Args:
        task: Empty covers all formal tasks; an id/name returns that one task.
        within_days: When > 0, counts tasks that reported within that many days
            of the snapshot date. Use this for windows the fixed buckets cannot
            express, such as 7 days.
        drift: True lists only tasks whose latest_progress_time disagrees with
            their real newest published progress row. Both directions count as a
            disagreement -- the denormalised column can be earlier OR later than
            the progress rows -- so this is a drift list, not a list of missed
            reports. It spans 73 tasks; report that figure rather than the first
            few rows.
        stale_days: When > 0, lists 在办任务 (status 0 未开始 and 1 进行中, both
            count) whose newest progress is older than that many days. Never
            reported counts as stale and sorts first, with days_since NULL.
        recent_days: When > 0, lists tasks that reported within that many days,
            newest first. No status filter -- the question is whether a report
            came in, not whether the task is still open.
        in_flight: True restricts the bucket distribution to 在办 tasks (status 0
            未开始 and 1 进行中). Required for "有多少条在办任务从来没报过进展时间":
            the answer is 8, while the un-gated buckets say 9, the extra one being
            task 88, which is already 已完成. With stale_days AND by, it also gates
            the per-group figures; by itself the grouped view covers every formal
            task, which is what "占比" questions mean.
        by: With stale_days, groups instead of listing: "board" or
            "project_group". Each row carries total (the group's own denominator),
            stale_count / stale_pct and the complementary active_count /
            active_pct, so "哪个组滞后占比最高" and "两个看板的上报活跃度对比" are
            both single calls. Counts alone mislead: 标准安全组 has the most stale
            tasks (5) yet 26.3% ranks below 国家工程办 (4 of 15 = 26.7%). The board
            view reads 技术组 61 active of 82 (74.4%) vs 集团 46 of 46 (100%).
        reported_only: True drops never-reported tasks from the stale_days
            listing. "最久没上报进展的 5 个任务" asks whose LAST report is furthest
            back, and the never-reported tasks have no such figure at all --
            8 of them under this listing's 在办 gate (9 un-gated) sort first and
            fill the whole top 5, so the answer shares no rows with the intended
            one. The reply carries never_reported_count either way;
            "从来没报过的有哪些" is the other question -- 9 tasks reported into
            neither table, which is what this listing's NULL bucket holds.
        lag_bands: True returns a per-board table binned 0-7 / 8-14 / 15-30 /
            超过 30 天 / 无正式进展. Use it for "某个看板周报有多陈旧" and
            "新鲜度怎么样": those ask for the distribution across that board's
            tasks, which the default 30/90/180 buckets cannot express and which a
            single newest timestamp does not answer at all. Each board's bands sum
            to its own formal task count (技术组 82: 17 in 15-30, 56 over 30, 9
            with none; 集团组 46: 14 / 28 / 4). The two boards are read from
            different tables -- task_progress for 技术组, task_group_progress_history
            for 集团组 -- so do not substitute latest_progress_time, which counts
            unpublished rows and leaves the group board empty.
        limit: Max rows for the listing views, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
        days = max(0, int(within_days))
        stale = max(0, int(stale_days))
        recent = max(0, int(recent_days))
    except TypeError, ValueError:
        return _invalid("within_days, stale_days, recent_days and limit must be integers")
    return await _call(
        "weekly_freshness_distribution",
        {
            "task": task,
            "within_days": days,
            "drift": bool(drift),
            "stale_days": stale,
            "recent_days": recent,
            "in_flight": bool(in_flight),
            "by": by,
            "reported_only": bool(reported_only),
            "lag_bands": bool(lag_bands),
            "limit": bounded,
        },
    )


async def weekly_approval_turnaround(scope: str = "summary", top: int = 8) -> str:
    """Measure approval elapsed time: overall, per board, slowest rounds, or backlog.

    scope="pending" is the still-unfinished backlog and deliberately does not
    apply the published filter -- a submission stuck in approval is by definition
    not published yet.

    scope="slowest" returns task_id alongside the name, breaks ties by that id,
    and carries top_tie_count. The slowest round is a tie: two rounds sit at 59
    days, so "审批最慢的一轮是哪条任务" is answered by the first row alone
    (task 76). Listing both without saying they tie reads as two separate
    answers, which is a different claim.

    Args:
        scope: summary / board / slowest / pending.
        top: Row cap for slowest and pending, 1..50.
    """
    try:
        bounded = max(1, min(50, int(top)))
    except TypeError, ValueError:
        return _invalid("top must be an integer")
    return await _call("weekly_approval_turnaround", {"scope": scope, "top": bounded})


async def weekly_milestone_query(task: str = "", year: str = "", status: str = "", limit: int = 200) -> str:
    """List milestones, re-checked against the formal-task caliber (R-17).

    Pass task for any single-task question. Leaving it empty covers every formal
    task, so "task 19 的里程碑" without it returns the first page of the whole
    board -- a complete, plausible-looking answer to a different question.
    Single-task listings come back in the task's own sort_order, which is the
    business sequence of that plan; board-wide browsing comes back newest year
    first.

    A single-task listing covers ONE task even when the name heads a series.
    「数据资源登记体系建设」 has 5 milestones of its own while its three 期 siblings
    hold 5 / 3 / 2 more; the siblings arrive under same_name_series. Report the
    resolved task's milestones as that task's arrangement and query again by id or
    full name for another 期 -- pooling the family's 15 rows answers a different
    question.

    Args:
        task: Task id or name; empty covers every formal task.
        year: Four-digit year, empty for all years.
        status: 0未完成 / 1已完成, empty for both.
        limit: Max rows to return, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call(
        "weekly_milestone_query",
        {"task": task, "year": year, "status": status, "limit": bounded},
    )


async def weekly_workflow_query(
    task: str = "",
    action: str = "",
    board: str = "",
    by_task: bool = False,
    scope: str = "",
    limit: int = 200,
) -> str:
    """Trace the approval action LOG (who did what, at which node).

    This is the action history, not the submission forms. For submission status
    or round counts use weekly_submission_query -- the action log cannot be
    aggregated into submission status.

    Filter with action / board rather than reading 200 rows and sorting by eye:
    the log is longer than the row cap, so a hand-filtered page is a subset of
    the real answer. An unsupported action name comes back as an error listing
    the values that exist, not as an unfiltered result.

    Approval opinions are permission-gated (R-04/R-14): when the service returns
    "[按权限不展示]", say the field is withheld by permission -- do not guess at
    its contents or retry to get around it.

    Args:
        task: Task id or name; empty returns recent actions across all tasks.
        action: Keep only this action; empty keeps all. Errors list the domain.
        board: Board code or name to scope the log.
        by_task: True returns action_count per task (次数, not 任务数) instead of
            the action rows.
        scope: Empty for the log itself. "by_node_action" counts every node_type +
            action pair, one row each: the same approved appears separately at the
            audit, leader and sign nodes, so grouping by action alone collapses 955
            approvals into one bucket and cannot say which node rejects most. Do
            not count the listing by hand -- the log holds 1578 rows against a
            200-row cap, so a hand count only ever sees the first page.
            "actions_per_task" returns the average action count (10.52) with its
            numerator 1578 and denominator 150 -- the denominator is the tasks that
            have actions, not the 128 published ones.
            "recent" orders by the action's own timestamp, newest first, and adds
            the task name, the submission's round_no, its reporter_name and its
            status. Every "最近谁被驳回了 / 谁驳回的" question needs this scope: the
            plain log is ordered by task id, so the newest action sits in the
            middle of the page and cannot be identified. Combine it with
            action="rejected" and limit to take the newest N rejections.
            Four further scopes report the LOG ITSELF and so carry no task gate --
            an approval action is a fact about the approval table, not about
            whether its task is still in flight. Each returns caliber_tiers with
            all three totals (raw table 1613 / soft-delete gate 1578 / formal-task
            gate 1519) so the caliber can be checked rather than guessed:
            "by_node" counts each 审批节点 once, five rows -- this is
            "审批走过哪些环节 / 各几次", a different question from by_node_action,
            which splits one node across its actions.
            "by_operator" counts actions per 经办人 and carries total_count (57
            people). Use it for "谁经办得最多" (孙立群, 244) and for "各操作了几次";
            the listing cannot be hand-counted at 57 rows against a 200-row cap.
            "log_span" gives first_at / last_at / total in one row.
            "opinion_count" counts actions carrying a non-empty opinion (1455). It
            counts existence only and returns no opinion text, so the R-04/R-14
            masking does not apply to it.
            Pick a gated scope instead whenever the question names a task or board.
        limit: Max rows to return, capped at 200. With scope="recent" this is how
            many of the newest actions you want (e.g. 8).
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call(
        "weekly_workflow_query",
        {
            "task": task,
            "action": action,
            "board": board,
            "by_task": bool(by_task),
            "scope": scope,
            "limit": bounded,
        },
    )


async def weekly_submission_query(
    task: str = "",
    reporter: str = "",
    status: str = "",
    exclude_status: str = "",
    status_mismatch: bool = False,
    scope: str = "",
    board: str = "",
    limit: int = 200,
) -> str:
    """Query approval submission forms: round_no, status, reporter, signer.

    Use this for "how many submissions / what status / whose submissions are not
    yet approved". Returns a status_breakdown alongside the rows. The draft
    snapshot (payload) is never returned and must not be reported as formal data.

    task.workflow_status and submission.status are two separate vocabularies over
    two tables; comparing them by eye across two calls mixes up the row sets. Use
    status_mismatch for that comparison instead.

    Args:
        task: Task id or name; empty covers all tasks.
        reporter: Reporter id or name.
        status: Keep only this status.
        exclude_status: Drop this status, e.g. "approved" for pending ones.
        status_mismatch: True returns the tasks whose workflow_status disagrees
            with their newest submission's status. Deliberately drops the
            published gate -- a published task whose latest form is still in
            flight is exactly what this asks for.
        scope: Empty for the normal listing. "by_kind" answers "how many initial
            vs progress submissions" in one call -- 312 progress and 150 initial,
            summing to 462. Do not read the listing back and count rows: it caps
            at 200, so a hand count sees only the first page. This scope keeps the
            soft-delete gate only; adding the publish gate would shrink it to
            310/128 and drop the forms belonging to the 22 unpublished tasks.
            Two scopes report the submission TABLE itself and so carry no gate at
            all -- a submitted form is a fact about the approval table, whatever
            later happened to its task. Both return caliber_tiers with all three
            totals (raw table 470 / soft-delete gate 462 / formal-task gate 438):
            "table_total" gives the row count, distinct tasks and max round_no;
            "by_status" breaks the table down by the form's own status, one row per
            status, summing to the raw total. Use these for "一共提交过几张审批单"
            and "审批单现在都是什么状态"; use the plain listing when the question
            names a task or a person.
            "inflight_count" returns the in-flight total (61) and the tasks holding
            them; "inflight_by_board" splits that by board and status (rejected is
            one of the in-flight states -- omitting it undercounts every board);
            "inflight_by_kind" splits the same 61 by status and submission_kind
            into nine buckets -- "按状态和类型分开看" means this axis, not the
            board one, and the two must not answer for each other;
            "inflight_multi" lists the tasks carrying more than one in-flight form.
            "rejected_by_board" gives the rejection RATE per board with numerator
            and denominator both taken from the submission table (tech 9/293 =
            3.07% against group 4/169 = 2.37%). Do not derive this from the action
            log: its 13 rejections are actions, and one form can be rejected more
            than once.
            "sign_summary" answers "how many need countersigning" -- 155 need_sign
            against 307 that do not, summing to 462. That is a different question
            from status = 'signing', which is the 9 currently at the sign node; do
            not answer one with the other. "by_signer" gives per-signer counts with
            blank signers excluded, since a blank means no signer was assigned
            rather than someone who signed nothing. "sign_turnaround" gives the
            average days by need_sign over completed forms only (274 rows at 14.5
            days versus 128 at 14.7) -- unfinished forms have no duration, so the
            two groups sum to 402, not 462. "rounds_per_task" gives the average
            rounds (3.08 = 462 / 150) with both numerator and denominator.
            "published_vs_progress" returns the published progress FORMS (272)
            beside the published progress ROWS (943), which live in different
            tables under different gates -- counting initial forms too gives 400,
            which answers a different question, and folding in the group board's
            task_group_progress_history gives 1305.
            "external_ids" reports how many
            forms carry each O2OA identifier (o2_process_id / o2_work_id /
            o2_task_id) -- the three fill rates differ, so one cannot stand in
            for another. "inflight_external" counts the in-flight forms that
            have a process id, with 在途 enumerated member by member rather than
            negated: status <> 'published' also picks up the cancelled form,
            which is neither published nor in flight.
        board: Board code or name (group / tech) to keep only that board's forms.
            The board lives on task, not on the form, so ask for it here instead of
            narrowing a 462-row listing by hand: 宋佳明 holds 32 forms across both
            boards and only 18 of them are group ones, and the listing caps at 200
            so a hand-filtered page is a subset of the real answer.
        limit: Max rows to return, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call(
        "weekly_submission_query",
        {
            "task": task,
            "reporter": reporter,
            "status": status,
            "exclude_status": exclude_status,
            "status_mismatch": bool(status_mismatch),
            "scope": scope,
            "board": board,
            "limit": bounded,
        },
    )


async def weekly_owner_roles(person: str) -> str:
    """Count one person's formal tasks split by role.

    Returns as_owner / as_project_owner / as_lead_owner / any_role counts only.
    For the actual task LIST use weekly_task_query with the owner filter --
    this tool answers "how many", not "which ones".

    Args:
        person: User id or name.
    """
    if not person.strip():
        return _invalid("person must not be empty")
    return await _call("weekly_owner_roles", {"person": person})


async def weekly_field_completeness(field: str = "", list_missing: bool = False, limit: int = 200) -> str:
    """Count how many formal tasks have a given field filled in (R-07 / R-19).

    Use this for "how many tasks have an overall goal / a named owner" instead of
    listing every task and counting by hand. Call with an empty field to see which
    columns are supported. Empty strings count as missing, not filled.

    Name columns and id columns are different questions: project_owner_name is
    filled on all 128 formal tasks while project_owner_id is filled on 119, so
    "which tasks have no project owner" must be asked against the id column.
    Set list_missing to get those rows instead of the counts.

    The count form already carries filled_pct, the rate over the formal-task
    total rounded to one decimal (project_owner_id: 128 / 119 / 93.0). Quote it
    as-is; do not recompute filled / total or round it yourself.

    "Is this field trustworthy" is a different question from how often it is
    filled, so the same reply carries distinct_values and top_value_rows.
    implementation_measure is filled on every 集团组 detail row yet holds a
    single distinct value -- one sentence copied down the column. A 100% fill
    rate there means the opposite of healthy, and no fill rate can reveal that.
    Read the two columns before calling a field usable for差异化归纳.

    Detail-table fields come with a second denominator. filled / total counts
    formal tasks (128, R-08 keeps tasks that have no detail row), while
    raw_row_count / raw_filled / raw_distinct_values count task_group_detail
    itself (55 rows, 9 of them on soft-deleted or unpublished tasks). Field
    quality reads the raw tier; "how many tasks filled it" reads the gated one.
    Both are correct for their own question -- say which one a number came from
    rather than mixing them in one sentence.

    Args:
        field: Column to measure; empty lists the supported columns.
        list_missing: Return the tasks missing the field instead of counts.
        limit: Row cap for list_missing, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call(
        "weekly_field_completeness",
        {"field": field, "list_missing": bool(list_missing), "limit": bounded},
    )


async def weekly_progress_coverage(
    scope: str = "summary",
    project_group: str = "",
    limit: int = 200,
    rule: str = "",
    keyword: str = "",
    task: str = "",
    all_versions: bool = False,
) -> str:
    """Summarise progress history depth: row count, tasks covered, date span, max version.

    Use this for "how far back does the history go" or "how many progress records
    are there in total" -- one call instead of walking every task.

    summary also carries avg_rounds_per_task, the answer to "平均每条有进展的任务
    报了多少期": 12.92, which is 943 periods over the 73 tasks that reported, not
    over the 128 formal tasks (that division gives 7.37). Quote the column rather
    than dividing by a denominator of your own choosing.

    For "下一步打算做什么" use scope="latest_round", never the full history: the
    newest period is picked by version_no (ties broken by id), and a task with 19
    periods would otherwise contribute 19 rows whose oldest plan reads as current.
    Picking by progress_date is also wrong -- a late-filed old period can carry a
    newer date than the latest one.

    Args:
        scope: summary (depth totals) / publish_split (published, unpublished and
            total progress rows in ONE row: 943 / 123 / 1066. Use this for "进展
            记录里有多少已发布、多少还没发布" -- summary carries only the published
            side and unpublished only the other, and reading the two separately
            invites dropping the task gate, which turns the pair into 945 / 1068) /
            import_split (published progress split into imported vs hand-entered
            by import_id IS NULL: 943 / 943 / 0. Use this for "有多少条进展是手工
            填的" -- no other tool exposes import_id, so the answer otherwise reads
            as "cannot tell" when it is a plain 0. The 118 manual rows the whole
            table holds are all unpublished) /
            unpublished (未发布进展按自身审批码值分档,
            0 草稿 / 1 待审核 / 2 驳回 / 3 通过 -- not the task's workflow_status.
            Every bucket carries cnt AND task_count, so "被驳回的进展有多少条、
            涉及多少条任务" is one call: 39 rows over 33 tasks. The task_counts
            must not be summed -- one task can hold both a draft and a rejection,
            and de-duplicated the whole unpublished set spans 72 tasks) /
            pending_review (the 待审核 periods, newest report_time first, each with
            the task's publicly visible version_no alongside -- for "存在待审核
            进展、但对外看到的还是上一期". 58 rows over 47 tasks; a NULL
            public_version means the very first period is still in review, so
            nothing is public yet) /
            same_text (the periods whose latest_progress and next_work were written
            identically -- "最新进展和下一步计划写成一样的有哪些任务". Compares the two
            columns WITHIN each row, whole-field after TRIM, across EVERY published
            period rather than the newest one only: a task that wrote them the same
            way in period 7 alone would be missed by a latest-only reading. The reply
            carries scanned_rows / scanned_tasks, so 0 rows reads as "every one of
            those periods was checked and none matched" instead of "not checked") /
            latest_unpublished (each task's NEWEST period, kept only when that
            period is still unpublished -- 72 rows, the answer to "最新写的那版进展
            还没对外发布". Three nearby calibers, do not swap them: this one spans
            draft, in-review and rejected alike, because none of them are public;
            pending_review keeps only the 58 in review; unpublished counts 123 rows
            and includes superseded older versions, whose tasks already show a
            newer period publicly. Each row carries progress_status so the reply
            can say which stage it is stuck at) /
            unpublished_by_task (tasks whose submission form IS published while
            progress rows are still unpublished, counted per task in PERIODS:
            version_no is de-duplicated, so this is "几期" and not "几行") /
            never_reported (two readings side by side, pick by the question --
            neither supersedes the other. total_count 55 = no published row in
            task_progress = the 128 formal tasks minus the 73 summary counts as
            covered, but it sweeps in all 46 group-board tasks, which DID report,
            keeping their 成效 in task_group_progress_history.
            never_reported_either_table 9 = reported into neither table, the rows
            with has_group_history = 0, equal to t.latest_progress_time being NULL
            and to the freshness bucket "4 从未报进展". "从来没上报过进展的任务有
            哪些" and "有多少" are both the 9. Likewise any count of progress ROWS -- monthly
            distribution, month-on-month, year-on-year -- stays on task_progress
            alone unless the question names the group board, or the group history
            rows get added on top) /
            version_gaps (tasks whose max version_no exceeds their actual row
            count, i.e. missing periods) / latest_round (each task's newest
            period with its next_work, one row per task) / missing_next (how many
            tasks left 下一步 blank in their newest period -- only the newest one
            counts, a gap in the middle does not) /
            backfill (补报: the periods whose report_time is LATER than that of the
            period after them, i.e. a smaller version_no filed afterwards. One row
            per adjacent pair, with filed_later_by_days computed server-side. This
            is the only outlet for "有哪些任务出现过补报"; do NOT try to answer it
            off weekly_progress_range's lag_days, which is "report date minus
            period date" WITHIN one row and says nothing about which period was
            filed later) /
            text_check (the 规则信号题出口: run a text rule over each task's
            NEWEST published period. rule=number_conflict finds 硬冲突 (报批或
            征求意见数大于草案数, 当前 9 项, 其中 4 项阶段数量之和超 100) with
            draft_cnt/report_cnt/consult_cnt and conflict_type per row;
            rule=availability lists 可用性低于 90% 的 (13 项, 只能作待核实清单,
            无统一指标定义不能直接判为业务风险); rule=keyword searches next_work
            for 协调/协同/联动/牵头组织 (19 条) or a custom keyword. Do NOT hand-
            count these off progress texts -- the regexes live server-side).
        project_group: Narrow latest_round / missing_next to one 项目组, for
            "算力网络组各任务下一步做什么". Matched exactly after trimming.
        limit: Max rows for the listing scopes, capped at 200.
        rule: For scope=text_check: number_conflict / availability / keyword.
        keyword: For scope=text_check with rule=keyword: custom search term.
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call(
        "weekly_progress_coverage",
        {
            "scope": scope,
            "project_group": project_group,
            "limit": bounded,
            "rule": rule,
            "keyword": keyword,
            "task": task,
            "all_versions": all_versions,
        },
    )


async def weekly_task_ranking(metric: str = "attachments", top: int = 5) -> str:
    """Rank formal tasks by child-record count: which task has the most X.

    This is the plain "top N" view. When the question turns on ties -- "并列的都
    列出来", "每个专项组各自的第一名", "最少的几个" -- use weekly_rank instead:
    the same metric yields a different row count under each reading, and that
    decision cannot be made from the rows this tool returns.

    Args:
        metric: attachments / progress / milestones / submissions.
        top: How many rows to return, 1..50.
    """
    try:
        bounded = max(1, min(50, int(top)))
    except TypeError, ValueError:
        return _invalid("top must be an integer")
    return await _call("weekly_task_ranking", {"metric": metric, "top": bounded})


async def weekly_rank(
    metric: str = "progress_rounds",
    mode: str = "cut",
    top: int = 5,
    ascending: bool = False,
    group_by: str = "",
    board: str = "",
) -> str:
    """Rank formal tasks with the tie rule decided on the server: cut / keep_ties / per_group.

    "前 5 条"、"并列的也都列出来" 和 "每组各自第一" are three different SETS over
    one metric, and their row counts differ -- 前 3 名 by progress periods is 3
    rows under cut and 12 rows under keep_ties. Pick the mode that matches the
    wording, then report the rows as returned: the caliber states which rule was
    applied, so neither padding the list with ties nor trimming it is correct.

    Zero-value rows survive (LEFT JOIN), so ascending genuinely answers "the
    fewest" -- a task with no progress at all is the right answer to that, and
    an inner join would have silently dropped it.

    Args:
        metric: progress_rounds (已发布进展期数) / milestones / milestones_done /
            attachments / submissions / group_rounds (集团看板成效期数) /
            project_team_size (项目团队人数).
            project_team_size counts the people written in the task row's own
            project_owner_name, splitting on the three delimiters the column uses
            (ideographic comma, ASCII comma, fullwidth semicolon). It is NOT the group board's
            multi-value project_owner_names column: that one lives on
            task_group_detail and covers only the 46 group tasks, while this covers
            all 128 tasks on both boards. Ask "哪个任务的项目团队人数最多" and you
            want this metric; tasks with an empty owner column are excluded rather
            than counted as 0 people, since they have no team size to compare.
            Every metric here is a per-task COUNT, so this tool answers "谁最多 /
            谁最少". It does NOT answer "都提交过几轮 / 给我前 N 条", which wants the
            submission rows themselves (task, round_no, status, submitted_at) --
            that is weekly_submission_query, optionally with board="group". Ranking
            that question returns one aggregate row per task and drops round_no
            entirely, which reads plausible and answers something else.
        mode: cut -- hard-cut to top rows, ties past the boundary excluded;
            keep_ties -- RANK(), every task down to place top, so expect more
            than top rows; per_group -- ROW_NUMBER() per bucket, one row per
            group, ties inside a group settled by task id;
            distribution -- one row of five-number summary (q1 / median / q3 /
            min / max / avg / task_total) over EVERY task under the caliber;
            quartiles -- NTILE(4), four equal-sized bands with each band's task
            count and rounds range.
            The last two are not ranking questions: reading a median off cut's
            top rows takes the middle of what happens to be visible and answers
            14 where the truth is 6. Both keep zero-round tasks in the
            population (32 of them fill the first quartile exactly), and
            quartiles is EQUAL-COUNT banding (32/32/32/32), not equal-WIDTH
            banding over the rounds range (which gives 17/39/41/31).
        top: cut takes this many rows; keep_ties ranks down to this place. 1..200.
            per_group ignores it -- one row per group means the group count is
            the row count.
        ascending: True ranks from the bottom ("期数最少的 5 个").
        group_by: Required for per_group: project_group / board /
            primary_category / status.
        board: Optional board code or name to scope the ranking to one board.
            Needed for "某看板的每个任务各有几个" -- without it the whole library
            competes for the top slots and the board's own tasks never surface.
            In cut mode the reply carries total_count, the task count under the
            caliber: when the question is "每个任务各多少", raise top until
            total_count equals row_count, otherwise the list is short.
    """
    try:
        bounded = max(1, min(200, int(top)))
    except TypeError, ValueError:
        return _invalid("top must be an integer")
    return await _call(
        "weekly_rank",
        {
            "metric": metric,
            "mode": mode,
            "top": bounded,
            "ascending": bool(ascending),
            "group_by": group_by,
            "board": board,
        },
    )


async def weekly_attachment_query(task: str = "", board: str = "", limit: int = 200) -> str:
    """List attachments. storage_path is never returned and must not be requested.

    Args:
        task: Task id or name; empty lists across all formal tasks.
        board: Board code or name (group / tech) to keep only that board's
            attachments; the rows then carry task_name too. Ask for the board here
            rather than looping this tool over each of the board's tasks -- the
            board lives on task, not on the attachment row, so a per-task loop is
            both 46 calls and unable to tell you the board total (52 for group).
        limit: Max rows to return, capped at 200.
    """
    try:
        bounded = max(1, min(200, int(limit)))
    except TypeError, ValueError:
        return _invalid("limit must be an integer")
    return await _call("weekly_attachment_query", {"task": task, "board": board, "limit": bounded})


async def weekly_attachment_stats(
    scope: str = "summary",
    date_from: str = "",
    task: str = "",
    top: int = 200,
    include_informal: bool = False,
) -> str:
    """Aggregate attachments: size totals, file types, uploaders, soft-delete audit.

    weekly_attachment_query caps at 200 rows, so counting or summing by reading
    rows back understates every total -- there are 454 live attachments on formal
    tasks. Sizes come back in bytes and in MB; the byte figure is authoritative.

    Args:
        scope: summary (count, total bytes/MB, average) / by_ext (per file
            extension) / largest (biggest first) / by_uploader (per uploader with
            size) / uploader_count (distinct uploaders) / by_link (attached to
            progress vs submission vs the task itself) / by_progress (per published
            progress round, attachment-heavy first) / zero_attachment (the 22 formal
            tasks holding no live attachment at all, listed in one call -- do not
            loop weekly_attachment_query over all 128 tasks looking for empty
            replies; total_formal_tasks is the denominator, not the row count) /
            on_open_submission (how many hang off submissions that are not yet
            published) / by_month (uploads per month) / deleted (soft-delete audit
            over the whole table) / deleted_by_link / orphan (rows whose task_id
            matches no task).
        date_from: For by_month, inclusive lower bound YYYY-MM-DD.
        task: Task id or name; every scope above then covers only that task. Use
            this for "how big are task N's attachments in total" instead of
            listing rows through weekly_attachment_query and adding them up by
            hand. Works for tasks outside the formal set too (attachments hang
            off task_id as a plain foreign key), so a task that
            weekly_task_detail rejects as task_not_formal still answers here.
        top: Row cap, capped at 200.
        include_informal: True counts the whole attachment table (510 live rows)
            instead of only attachments on formal tasks (454). Both readings are
            legitimate -- "how many attachments are there in total" means the
            table, "how many do the formal tasks have" means the gated set, which
            is the reporting caliber and stays the default. The 56-row gap sits on
            non-formal tasks, and 3 of the 510 are orphans whose task_id matches
            no task at all. Ignored when task is set, which is already ungated.
    """
    try:
        bounded = max(1, min(200, int(top)))
    except TypeError, ValueError:
        return _invalid("top must be an integer")
    return await _call(
        "weekly_attachment_stats",
        {
            "scope": scope,
            "date_from": date_from,
            "task": task,
            "top": bounded,
            "include_informal": bool(include_informal),
        },
    )


async def weekly_health() -> str:
    """Check that the weekly取数 service is reachable and report table row counts.

    Use this when a query fails and you need to tell a connection problem from an
    empty result. Never edit .env or ask the user for a token.
    """
    return await _call("weekly_health", {})
