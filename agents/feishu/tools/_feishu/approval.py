"""Feishu Approval (审批) — read instance, read definition, submit.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

import _feishu_impl as _core
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

# ── Approval (审批) — read an instance, read a definition, submit one ──────────
#
# What is left here are the calls whose value is a *transformation*: an instance's
# and a definition's ``form`` both arrive as a JSON string holding an array, and
# both are parsed into something callable code can use — attachments tagged by how
# they must be downloaded, and widget ids that must be copied rather than invented.
# Everything else in this domain (task lists, instance lists, approve, reject,
# subscribe) is an endpoint table row in the ``feishu-approval`` skill, called
# through ``feishu_api``. The numeric status vocabularies moved there too, as a
# Markdown table: the rules vocabulary has checks, not value mappings.


def _build_instance_get_request(instance_id: str, user_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/approval/v4/instances/:instance_id"
    req.paths["instance_id"] = instance_id
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _parse_approval_attachments(form: Any) -> list[dict[str, Any]]:
    """Pull downloadable attachments out of an approval form.

    The ``form`` field is a JSON string of widget objects. File/image widgets
    (attachmentV2/image/imageV2/…) carry **direct URLs** in their ``value`` —
    these are valid only ~12 hours, so download them promptly. Only ``document``
    widgets return a drive token instead of a URL.
    """
    widgets: Any = form
    if isinstance(form, str):
        with contextlib.suppress(ValueError):
            widgets = json.loads(form)
    if not isinstance(widgets, list):
        return []
    attachments: list[dict[str, Any]] = []
    for w in widgets:
        if not isinstance(w, dict):
            continue
        wtype = str(w.get("type", "")).lower()
        name = w.get("name", "") or w.get("id", "")
        value = w.get("value")
        if "document" in wtype:
            for tok in value if isinstance(value, list) else [value]:
                if tok:
                    attachments.append({"name": name, "type": w.get("type", ""), "kind": "drive", "value": tok})
        elif any(k in wtype for k in ("attachment", "image", "file")):
            for v in value if isinstance(value, list) else [value]:
                if v:
                    attachments.append({"name": name, "type": w.get("type", ""), "kind": "url", "value": v})
    return attachments


async def get_approval_instance_impl(instance_id: str, user_id_type: str = "open_id") -> dict[str, Any]:
    """Read an approval instance's detail — applicant, status, the submitted form, and task_list."""
    res = await _core._invoke(_build_instance_get_request(instance_id, user_id_type))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    form = data.get("form", "")
    return {
        "ok": True,
        "instance_code": instance_id,
        "approval_code": data.get("approval_code", ""),
        "approval_name": data.get("approval_name", ""),
        "status": data.get("status", ""),
        "applicant": data.get("user_id", "") or data.get("open_id", ""),
        "form": form,
        "attachments": _parse_approval_attachments(form),
        "task_list": data.get("task_list", []),
        "timeline": data.get("timeline", []),
    }


# ── Approval (审批) —发起端: read a definition's form schema + submit an instance ─
#
# The submit side of approvals: read what fields an approval requires (its form
# template), then create an instance *on behalf of an applicant*. Feishu records
# the instance under the applicant's open_id/user_id carried in the body — the
# bot's tenant token creates it, so no per-employee UAT authorization is needed
# (unlike the audit side's decide, which still needs a real approver's user_id).


def _build_approval_definition_request(approval_code: str, user_id_type: str, with_admin_id: bool) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/approval/v4/approvals/:approval_code"
    req.paths["approval_code"] = approval_code
    req.add_query("user_id_type", user_id_type)
    if with_admin_id:
        req.add_query("with_admin_id", True)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _parse_approval_form_schema(form: Any) -> list[dict[str, Any]]:
    """Parse a definition's stringified ``form`` JSON into a clean widget list.

    The ``form`` field is a JSON string of control (widget) objects. Each widget
    becomes ``{id, custom_id, name, type, required}`` — the ``id`` (and optional
    ``custom_id``) is what a create-instance form must reference, ``name`` is the
    display label, ``type`` is the control type (input/textarea/number/amount/
    date/radioV2/...), and ``required`` flags mandatory fields. Mirrors the
    tolerant json.loads + isinstance(list) guard of ``_parse_approval_attachments``.
    """
    widgets: Any = form
    if isinstance(form, str):
        with contextlib.suppress(ValueError):
            widgets = json.loads(form)
    if not isinstance(widgets, list):
        return []
    fields: list[dict[str, Any]] = []
    for w in widgets:
        if not isinstance(w, dict):
            continue
        fields.append(
            {
                "id": w.get("id", ""),
                "custom_id": w.get("custom_id", ""),
                "name": w.get("name", ""),
                "type": w.get("type", ""),
                "required": bool(w.get("required")),
            }
        )
    return fields


async def get_approval_definition_impl(
    approval_code: str, user_id_type: str = "open_id", with_admin_id: bool = False
) -> dict[str, Any]:
    """Read an approval definition's form schema + node list so the agent knows which fields to fill.

    Returns the parsed widget list (``form``) and an approval-chain summary
    (``node_list``). Use this before ``create_approval_instance_impl`` to map an
    employee's words onto the real field ids/types — never invent field ids.
    """
    if not approval_code:
        return _core._error("approval_code is required (the approval definition code).")
    res = await _core._invoke(_build_approval_definition_request(approval_code, user_id_type, with_admin_id))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    node_list = [
        {"name": n.get("name", ""), "node_id": n.get("node_id", ""), "node_type": n.get("node_type", "")}
        for n in (data.get("node_list", []) if isinstance(data.get("node_list"), list) else [])
        if isinstance(n, dict)
    ]
    return {
        "ok": True,
        "approval_code": approval_code,
        "approval_name": data.get("approval_name", ""),
        "status": data.get("status", ""),
        "form": _parse_approval_form_schema(data.get("form", "")),
        "node_list": node_list,
    }


def _build_create_instance_request(
    approval_code: str,
    form: str,
    applicant_open_id: str,
    applicant_user_id: str,
    node_approver_open_id_list: list[dict[str, Any]] | None,
    title: str,
    user_id_type: str,
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/approval/v4/instances"
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"approval_code": approval_code, "form": form}
    if applicant_open_id:
        body["open_id"] = applicant_open_id
    if applicant_user_id:
        body["user_id"] = applicant_user_id
    if title:
        body["title"] = title
    if node_approver_open_id_list:
        body["node_approver_open_id_list"] = node_approver_open_id_list
    req.body = body
    return req


async def create_approval_instance_impl(
    approval_code: str,
    form_json: str,
    applicant_open_id: str = "",
    applicant_user_id: str = "",
    node_approver_open_id_list_json: str = "",
    title: str = "",
    user_id_type: str = "open_id",
    user_key: str = "",
) -> dict[str, Any]:
    """Submit an approval instance on behalf of an applicant. Returns the new instance_code.

    ``form_json`` is a JSON array of ``{id, type, value}`` widgets whose ids come
    from ``get_approval_definition_impl``. An applicant id (open_id or user_id) is
    required — the instance is recorded under that person.
    """
    if not approval_code:
        return _core._error("approval_code is required (the approval definition code).")
    if not applicant_open_id and not applicant_user_id:
        return _core._error(
            "an applicant id is required — pass applicant_open_id (the sender's open_id) or applicant_user_id."
        )
    try:
        form = json.loads(form_json)
    except ValueError as exc:
        return _core._error(f"form_json is not valid JSON: {exc}")
    if not isinstance(form, list):
        return _core._error("form_json must be a JSON array of {id, type, value} widget objects.")
    node_approvers: list[dict[str, Any]] | None = None
    if node_approver_open_id_list_json.strip():
        try:
            node_approvers = json.loads(node_approver_open_id_list_json)
        except ValueError as exc:
            return _core._error(f"node_approver_open_id_list_json is not valid JSON: {exc}")
        if not isinstance(node_approvers, list):
            return _core._error("node_approver_open_id_list_json must be a JSON array of {key, value} objects.")
    res = await _core._invoke(
        _build_create_instance_request(
            approval_code,
            json.dumps(form, ensure_ascii=False),
            applicant_open_id,
            applicant_user_id,
            node_approvers,
            title,
            user_id_type,
        ),
        user_key=user_key,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "instance_code": data.get("instance_code", "")}
