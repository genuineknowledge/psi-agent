"""Feishu Mentor Ledger — idempotent first-time provisioning.

Backs the "each mentor gets their own ledger base" rule: ensures the mentor's
TODO 台账 base exists (copied from a template, or created fresh with the fixed
schema), granted to mentor + boss, and returns the ``app_token`` / ``table_id``.
Cycle tables are then provisioned per cycle by ``feishu_mentor_ledger_cycle_table``.
"""

from __future__ import annotations

import json

import _feishu_impl as _f


async def feishu_mentor_ledger_ensure(
    mentor_open_id: str,
    mentor_name: str,
    folder_token: str,
    template_app_token: str = "",
    boss_open_id: str = "",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Idempotently ensure one mentor's TODO ledger base exists.

    Lists ``folder_token`` for a bitable named "TODO 台账-<mentor_name>"; if
    found, returns its coordinates without copying again. Otherwise provisions
    it: with ``template_app_token`` set, copies the template (structure only);
    without one, creates a fresh base + 台账 table from the fixed
    ``_LEDGER_SCHEMA_FIELDS``. Grants the mentor edit (and the boss view, if
    given). ``user_key`` / ``identity`` follow the usual write-ownership
    convention; a first write without identity returns ``need_identity_choice``.

    Args:
        mentor_open_id: The mentor's open_id (edit grant).
        mentor_name: Mentor display name (used in the base/table name).
        folder_token: Drive folder the base should live in.
        template_app_token: Optional template base to copy (structure only).
        boss_open_id: Optional boss open_id (view grant).
        user_key: The sender's open_id (from ``<feishu_context>``).
        identity: ``"user"`` / ``"bot"`` ownership choice; omit to use the
            remembered choice (returns ``need_identity_choice`` on first use).
    """
    outcome = await _f.mentor_ledger_ensure_impl(
        mentor_open_id=mentor_open_id,
        mentor_name=mentor_name,
        folder_token=folder_token,
        template_app_token=template_app_token,
        boss_open_id=boss_open_id,
        user_key=user_key,
        identity=identity,
    )
    return json.dumps(outcome, ensure_ascii=False, default=str)
