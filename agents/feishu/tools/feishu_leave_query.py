"""Enumerate 假勤 approval instances and decide which days of a window each person is off.

Leave comes straight from Feishu approvals: ``GET /open-apis/approval/v4/instances`` lists
the instance codes for one ``approval_code`` over a time range, then each instance's detail
carries the applicant and the dates. This is the path ``feishu-leave-audit-board`` already
uses — there is no need for anyone to re-file leave into a spreadsheet.

It is a tool rather than a paragraph in a skill because overlapping-interval arithmetic is
pure logic: left in the model's hands it is a calendar computation redone every cycle, and
one slip assigns work to somebody on holiday and books them overdue. The rule for "which
day counts as leave" belongs in code, once.

Args:
    approval_code: The 假勤 approval **definition** code. Get it from the Feishu approval
        admin console, or from ``feishu_api`` GET /open-apis/approval/v4/tasks/query — the
        ``definition_code`` (sometimes ``process_code``) in its response is this value.
    date_from: First day of the window, ISO (``2026-08-05``). Inclusive.
    date_to: Last day of the window, inclusive. Empty = same as ``date_from``.
    names_json: Optional JSON array restricting the answer, e.g. ``'["张三","dg429f6d"]'``.
        Names and applicant ids both work — applicants come back as ids (this tenant returns
        an 8-char ``user_id``) and their names are resolved from the contact book, then
        either form is matched. Empty = everyone.
    user_key: The sender's open_id (from ``<feishu_context>``).
"""

from __future__ import annotations

import _feishu_impl as _f


async def feishu_leave_query(
    approval_code: str,
    date_from: str = "",
    date_to: str = "",
    names_json: str = "",
    user_key: str = "",
) -> str:
    """Decide who is on approved leave on which days of ``[date_from, date_to]``.

    Call this **before** dispatching a cycle's todos — that ordering is what keeps work off
    people who are away. The fixed rules it applies, so nobody re-derives them:

    - the window and each leave interval are **closed** — both endpoints count as leave;
    - **only approved** applications count. Pending ones do not: that person is still
      expected at work, so assigning them work is correct, and once it is approved the next
      cycle's audit sees it. Rejected and revoked ones never count.
    - a blank end date means one day of leave (the start day);
    - an application whose dates cannot be read lands in ``needs_fix`` and is **not**
      silently dropped — dropping it would turn "did file leave" into "did not";
    - leave that never went through an approval flow (verbal, or edited straight in the HR
      console) is invisible here, so it counts as not-on-leave and the person appeals.

    The result carries ``on_leave`` (per applicant: ``name``, hit dates and each leave
    interval), ``full_period_applicants`` for the people to skip dispatching entirely,
    ``skipped_not_approved`` so a pile of pending applications is visible rather than
    looking like nobody asked for leave, and ``needs_fix`` for applications a human must
    look at.

    When ``names_json`` is used, check ``unmatched_filter`` before concluding anybody was
    at work: an entry there means that spelling matched no leave record, which is either
    "not on leave" or "the spelling never resolved" — ``name_lookup_error`` tells them
    apart. An empty ``on_leave`` alone cannot, and reading it as "everyone was at work"
    books overdue todos against people who were away.
    """
    return _f.dumps_result(
        await _f.query_leave_impl(
            approval_code=approval_code,
            date_from=date_from,
            date_to=date_to,
            names_json=names_json,
            user_key=user_key,
        )
    )
