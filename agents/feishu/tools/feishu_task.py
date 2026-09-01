"""Feishu/Lark task creation for the assignment publish path.

The task DOMAIN is an endpoint table now — see ``skills/feishu-task/SKILL.md`` and
call it through ``feishu_api``. The five task tools that used to live here are gone.

What stays is one **private** helper (leading underscore, so it is not registered as
a tool): ``assignment_accept`` publishes a task while holding a Fusion Memory claim
token, and must create it **exactly once**. A rate-limit retry could publish twice
under a single claim, so that path needs ``retry_rate_limits=False`` — a guarantee an
endpoint-table row cannot express.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def _feishu_task_create_once(
    summary: str,
    description: str = "",
    due: str = "",
    assignees: str = "",
    followers: str = "",
    user_key: str = "",
    identity: str = "",
) -> str:
    """Create a task without rate-limit retries, so the caller can guarantee once-only."""
    return _f.dumps_result(
        await _f.create_task_impl(
            summary,
            description,
            due,
            assignees,
            followers,
            user_key,
            identity,
            retry_rate_limits=False,
        )
    )
