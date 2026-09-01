"""Feishu/Lark approval (审批) — the two calls that need more than a request.

Most of this domain is now endpoint knowledge in the ``feishu-approval`` skill, called
through ``feishu_api``: listing a user's tasks, listing a definition's instances,
reading an instance, approving, rejecting, and subscribing to status changes. What
stays here are the two calls whose value is a *transformation* rather than a request:

- ``feishu_approval_get_definition`` — the definition's ``form`` arrives as a JSON
  *string* holding an array of widgets. This parses it into
  ``{id, custom_id, name, type, required}`` so field ids can be copied instead of
  guessed; an invented id only fails later, at submit time.
- ``feishu_approval_create`` — the submitted ``form`` has to go out as a JSON string
  *containing* a JSON array, and an applicant id is mandatory (without one the
  instance is filed under nobody).

Identity in this domain is carried in the body, not by the caller's token: create
records the applicant from ``open_id``/``user_id``, and approve/reject records the
approver from ``user_id`` — who must be the current task's real assignee. The bot's
tenant token submits either way, so no per-applicant authorization is needed. Requires
the app authorized on the approval definition and the ``approval:*`` scopes, plus
``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``.
"""

from __future__ import annotations

# ruff: noqa: E402
import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f


async def feishu_approval_get(instance_id: str, user_id_type: str = "open_id") -> str:
    """Read an approval instance's detail — applicant, status, submitted form, and task_list.

    Use this to inspect what an application actually contains before deciding.
    The ``form`` field is a JSON string of the submitted form widgets, and
    ``attachments`` lists downloadable files pulled from that form: each is
    ``{name, type, kind, value}`` where kind ``"url"`` is a direct link (valid
    only ~12h — download promptly with ``feishu_file_download`` is_url=True) and
    kind ``"drive"`` is a media token (download with is_url=False).

    Args:
        instance_id: The approval instance code (from the task list or instance list —
            ``feishu_api`` GET ``/open-apis/approval/v4/tasks/query`` or
            ``/open-apis/approval/v4/instances``).
        user_id_type: Id form for returned user ids — open_id (default), union_id, user_id.
    """
    return _f.dumps_result(await _f.get_approval_instance_impl(instance_id, user_id_type))


async def feishu_approval_get_definition(
    approval_code: str, user_id_type: str = "open_id", with_admin_id: bool = False
) -> str:
    """Read an approval definition's form template so you know which fields to fill before submitting.

    Returns the ``form`` as a widget list — each ``{id, custom_id, name, type, required}`` —
    plus a ``node_list`` summary of the approval chain. Feed each widget's ``id`` and
    ``type`` into ``feishu_approval_create``'s form_json. Read this first and map the
    applicant's words onto the real field ids/types — never invent field ids.

    Args:
        approval_code: The approval definition code (identifies which approval flow).
        user_id_type: Id form for returned user ids — open_id (default), union_id, user_id.
        with_admin_id: True to also return the definition's admin user ids (optional).
    """
    return _f.dumps_result(await _f.get_approval_definition_impl(approval_code, user_id_type, with_admin_id))


async def feishu_approval_create(
    approval_code: str,
    form_json: str,
    applicant_open_id: str = "",
    applicant_user_id: str = "",
    node_approver_open_id_list_json: str = "",
    title: str = "",
    user_id_type: str = "open_id",
    user_key: str = "",
) -> str:
    """Submit an approval application on behalf of an applicant. Returns the new instance_code.

    Use this to file a leave/reimbursement/etc. application for someone. The
    instance is recorded under the applicant, so pass the requester's own id —
    in a Feishu DM that is the ``sender_open_id`` from ``<feishu_context>``.
    Build ``form_json`` from ``feishu_approval_get_definition`` first, and confirm
    the filled form with the applicant before submitting (see the
    feishu-self-service-agent skill / admin-finance-governance).

    Args:
        approval_code: The approval definition code (which approval flow to file).
        form_json: JSON array of ``{"id","type","value"}`` widgets, ids/types from get_definition.
        applicant_open_id: The applicant's open_id (the DM sender's open_id). Pass this or applicant_user_id.
        applicant_user_id: The applicant's user_id (alternative to applicant_open_id).
        node_approver_open_id_list_json: Optional JSON array of ``{"key":node_id,"value":[open_id,...]}``
            for flows where the initiator picks approvers.
        title: Optional custom instance title.
        user_id_type: Id form for the ids above — open_id (default), union_id, user_id.
        user_key: Optional UAT slot (a user's open_id) to submit as that user; empty uses the bot's tenant token.
    """
    return _f.dumps_result(
        await _f.create_approval_instance_impl(
            approval_code,
            form_json,
            applicant_open_id,
            applicant_user_id,
            node_approver_open_id_list_json,
            title,
            user_id_type,
            user_key,
        )
    )
