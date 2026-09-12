"""Feishu TODO ledger reconcile — 台账行的 负责人/mentor 是否还是看板上的当前关系。

The ledger stamps ``负责人``/``mentor`` once, when ``company-todo-sync`` creates the cycle's
rows, and never updates them again. A board edit made after that (moving someone to another
mentor) therefore leaves the ledger asserting the pre-edit relation, and every ledger
consumer keeps reporting it — the 16:00 compare cards, the mentor report, the review-card
routing. This tool is the deterministic check for that, so "改了关系但还按旧关系发" shows up
as an explicit mismatch with a ``record_id`` instead of as a plausible-looking card.

Reached directly rather than through ``_feishu_impl``'s re-exports: nothing in that module
imports ``_feishu.ledger_reconcile``, so importing it here keeps the load order one-way.
"""

from __future__ import annotations

import json

from _feishu.ledger_reconcile import ledger_reconcile_impl


async def feishu_todo_ledger_reconcile(
    board_link: str,
    ledgers_json: str = "",
    cycle_date: str = "",
    folder_token: str = "",
    apply_fixes: bool = False,
    user_key: str = "",
    identity: str = "",
) -> str:
    """Diff 台账行的 负责人/mentor against the board's mentor column; optionally fix them.

    Call this **before pushing any per-mentor ledger output** (16:00 前后对比卡、mentor 报表、
    评价卡补发) and whenever someone says they changed someone's mentor on the board — the
    ledger is a snapshot, so a board edit does not reach it by itself. The board's mentor
    column is the authority; 负责人 and mentor are two different people in two columns.

    Reading the result:
    - ``relation_aligned: true`` — every checked row agrees with the board. Only then may the
      push describe the grouping as current.
    - ``mentor_changed`` — rows still carry the pre-edit mentor (each with ``record_id``,
      ``row_mentor`` and ``board_mentor``). Re-run with ``apply_fixes=true`` to rewrite the
      ``mentor`` field; a name that does not resolve to exactly one directory entry is left
      alone and reported under ``needs_manual``.
    - ``needs_move`` — the row's mentor disagrees with the base it was read from, i.e. it
      still lives in the old mentor's ``TODO 台账-<旧 mentor>``. **Rewriting the field does
      not move the row**: a consumer that enumerates bases still reads it under the old
      mentor. Move the row into the new mentor's cycle table (or group by the mentor column
      instead) before claiming the ledger is aligned.
    - ``person_not_on_board`` / ``row_missing_mentor`` / ``board_missing_mentor`` /
      ``row_missing_owner`` — needs a human; names here must not be relayed to a mentor
      before ``feishu_member_status_check`` classifies them (离职人员不体现).

    Args:
        board_link: The TODO board's /wiki/ or /sheets/ URL.
        ledgers_json: JSON array of ``{"app_token", "table_id"?, "mentor_name"?,
            "person_field"?, "mentor_field"?}`` — one entry per mentor base to check.
            ``table_id`` may be omitted when ``cycle_date`` is given. ``mentor_name`` is the
            mentor that base belongs to; give it to also get ``needs_move``.
        cycle_date: Cycle column header (e.g. ``9.9``), used to resolve each base's
            ``台账-<cycle_date>`` table.
        folder_token: The folder holding the per-mentor ledger bases; when given, every
            ``TODO 台账-*`` base in it is checked without listing coordinates.
        apply_fixes: Rewrite mismatched ``mentor`` fields (only uniquely resolvable names).
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: Who owns a write — ``"user"`` / ``"bot"``.
    """
    outcome = await ledger_reconcile_impl(
        board_link=board_link,
        ledgers_json=ledgers_json,
        cycle_date=cycle_date,
        folder_token=folder_token,
        apply_fixes=apply_fixes,
        user_key=user_key,
        identity=identity,
    )
    return json.dumps(outcome, ensure_ascii=False, default=str)
