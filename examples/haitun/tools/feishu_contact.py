"""Feishu/Lark contact (通讯录) tools — list department members.

Get the roster (user ids + names) for a department, or the whole organization
from the root department "0". This gives the agent the ``user_id`` list needed to
batch-query attendance and compute payroll.

Requires the app's 通讯录权限范围 to cover the members you want to see, the
``contact:contact.base:readonly`` scope (plus ``contact:user.employee_id:readonly``
for the ``user_id``/employee-id field), and ``PSI_FEISHU_APP_ID`` /
``PSI_FEISHU_APP_SECRET``.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_department_members(
    department_id: str = "0",
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    recursive: bool = False,
) -> str:
    """List the members of a department (or the whole org from root "0").

    Returns de-duplicated members, each ``{user_id, open_id, name}``. Use the ids
    to batch-query attendance (``feishu_attendance_query``) or compute payroll.

    Args:
        department_id: Department id ("0" is the organization root). Default "0".
        department_id_type: Id form for department_id — open_department_id (default) or department_id.
        user_id_type: Id form for returned member ids — open_id (default), union_id, user_id.
        recursive: If True, also include members of all sub-departments. Default False.
    """
    return _f.dumps_result(
        await _f.list_department_members_impl(department_id, department_id_type, user_id_type, recursive)
    )
