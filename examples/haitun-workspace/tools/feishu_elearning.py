"""Feishu/Lark eLearning (在线学习) tools — read each person's learning records.

Query course registrations / learning records so you can report who has (and hasn't)
completed a course — e.g. "全员学习" tracking. Returns each registration's status and
progress; use it to build a completion report per person.

Note: creating/publishing a course and assigning it to the whole company (全员) is done
in the Feishu eLearning admin console, not via the open API — these tools cover the
*reading* side (learning records/completion). Pair with ``feishu_approval`` for the
"本人确认" step (a person approving on their own behalf = their confirmation) and with
``feishu_drive_upload`` to attach a learning video as evidence.

Requires ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET`` and the eLearning read scope.
The endpoint path/scope follow Feishu's naming convention — verify on the live doc when
you wire this to a real tenant (an API error will report the exact missing scope).
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_elearning_list_registrations(
    user_ids: str = "",
    user_id_type: str = "open_id",
    page_size: int = 100,
    page_token: str = "",
) -> str:
    """List eLearning course registrations (per-person learning records / completion status).

    Use this to check who completed a course. Pass ``user_ids`` to filter to specific
    people (e.g. verify one employee), or leave it empty to page through all registrations.

    Args:
        user_ids: Comma-separated user ids to filter by (form matches user_id_type).
            Empty returns all registrations (page through with page_token).
        user_id_type: Id form for user_ids and returned ids — open_id (default), union_id, user_id.
        page_size: Max registrations per page (default 100).
        page_token: Pagination cursor from a previous call's has_more result (optional).
    """
    return _f.dumps_result(await _f.list_course_registrations_impl(user_ids, user_id_type, page_size, page_token))
