"""Read the TPMF work-tree mindnote and list per-person @-assigned items.

The work tree (a Feishu mindnote, 思维笔记) is the company's task-assignment
map — every node names a work item and its owner(s) via ``@`` mentions. This
tool reads all nodes, rebuilds the tree, resolves each mentioned person's name
from the contact book, and returns one entry per person: their name, open_id,
and the full node paths they are @-assigned on.

Use it as the reference side of the 「重要 todo 全覆盖」 check: for each person,
every work-tree item assigned to them should be traceable in that cycle's
filing (大目标 / 小目标 / TODO) — anything with no counterpart is a missed
important item to remind them about.

Mindnote reading is the ONE Feishu API that only works with a user-level token
(scope ``mindnote:node:read``) — the tenant token cannot read mindnotes. If the
cached user authorization is missing or predates this scope, the result carries
``need_auth``; ask the user to authorize once with
``feishu_auth_request(capabilities="mindnote_read")``.

Args:
    mindnote_token: The mindnote's token. For a ``/wiki/`` link, resolve the node
        first (``feishu_api`` GET /open-apis/wiki/v2/spaces/get_node) and pass
        its ``obj_token`` (``obj_type`` should be ``mindnote``).
    user_key: The sender's open_id (from ``<feishu_context>``).
"""

from __future__ import annotations

import json

import _feishu_impl as _f


async def feishu_worktree_read(mindnote_token: str, user_key: str = "") -> str:
    """Return per-person work-tree @-assignments (name / open_id / node paths)."""
    outcome = await _f.read_worktree_impl(mindnote_token=mindnote_token, user_key=user_key)
    return json.dumps(outcome, ensure_ascii=False)
