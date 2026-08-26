"""Feishu Mentor Ledger — idempotently provision one mentor's TODO-tracking Bitable base.

Split out of ``_feishu_impl.py`` by domain, following the same shape as the other
``_feishu/*.py`` modules. Reaches the shared client/token layer through ``_core``.

Backs the "per-mentor 独立 Bitable 台账" design: rather than one shared base with
row-level visibility (which needs Feishu's advanced permission, unavailable once a
base lives inside a wiki or is embedded in a doc — error 1254301), each mentor gets
their own base copied from a template, with isolation coming from Feishu's plain file
permissions instead. See the ``feishu-bitable`` skill (``copy`` is a **template**
operation, needs a user token) and ``feishu-permission`` skill (member grants).

Three actions have to happen exactly once no matter how many times this runs, and
failing partway must not leave a half-provisioned ledger invisible to everyone:
finding whether the base already exists, copying the template only if it doesn't,
and resolving the one table inside it. Doing any of this from a prompt would risk
double-copying (a second base named the same) or leaving out one of the two grants
this module *can* make (see the ``bot_access`` note in ``mentor_ledger_ensure_impl``).

When no ``template_app_token`` is configured, the tool provisions a base **directly**
from ``_LEDGER_SCHEMA_FIELDS`` — the fixed, code-owned column definition (field
names/types/options/colors) so a fresh ledger can never drift into text-typed
person columns or a missing option palette. The first field is the index column
(bitable requires type 1/2/5/13/15/20/22 there — "序号" is text, fine).
"""

from __future__ import annotations

from typing import Any

import _feishu_impl as _core
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

_LEDGER_NAME_PREFIX = "TODO 台账-"

# Fixed column definition for directly-provisioned ledgers. 负责人/mentor are
# PERSON (11) columns — never text. 层级 and 父项 are both single-select (3)
# whose options company-todo-sync syncs from the current cycle BEFORE writing
# rows: 层级 options are per-item level tags ("大目标1", "小目标1", "todo1",
# numbered independently per level, color by level kind — palette 1/3/5);
# 父项 options are the parent-able subset of those tags (大目标*/小目标*).
# 截止日期 is a plain date (5) — callers write nothing (not a default) when
# the source cell has no deadline.
_LEDGER_SCHEMA_FIELDS: list[dict[str, Any]] = [
    {"field_name": "周期日期", "type": 5},
    {"field_name": "负责人", "type": 11},
    {"field_name": "mentor", "type": 11},
    {"field_name": "层级", "type": 3, "property": {"options": []}},
    {"field_name": "父项", "type": 3, "property": {"options": []}},
    {"field_name": "标题", "type": 1},
    {"field_name": "截止日期", "type": 5},
    {
        "field_name": "状态",
        "type": 3,
        "property": {
            "options": [
                {"name": "待开始", "color": 0},
                {"name": "进行中", "color": 1},
                {"name": "已交付", "color": 2},
                {"name": "已闭环", "color": 3},
                {"name": "未闭环逾期", "color": 4},
                {"name": "请假顺延", "color": 5},
            ]
        },
    },
    {
        "field_name": "闭环五要素",
        "type": 4,
        "property": {
            "options": [
                {"name": "有验收人"},
                {"name": "截止到期或提前"},
                {"name": "已勾选提交成果"},
                {"name": "mentor已评分"},
                {"name": "评价已回写wiki"},
            ]
        },
    },
    {"field_name": "mentor打分", "type": 2},
    {"field_name": "mentor评语", "type": 1},
    {"field_name": "外部成果", "type": 1},
    {"field_name": "友商对比", "type": 1},
    {"field_name": "任务GUID", "type": 1},
]


def _ledger_base_name(mentor_name: str) -> str:
    return f"{_LEDGER_NAME_PREFIX}{mentor_name.strip()}"


def _build_list_folder_request(folder_token: str, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/drive/v1/files"
    req.add_query("folder_token", folder_token)
    req.add_query("page_size", "100")
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _find_existing_base(folder_token: str, base_name: str) -> tuple[str | None, dict[str, Any] | None]:
    """Look for a bitable file named ``base_name`` directly in ``folder_token``.

    Returns (app_token_or_None, error_or_None). Listing does **not** recurse — the
    ledger folder is expected to be flat (one file per mentor) — matching
    ``feishu-drive``'s own note that this endpoint lists one level only.
    """
    page_token = ""
    while True:
        res = await _core._invoke(_build_list_folder_request(folder_token, page_token))
        if not res["ok"]:
            return None, res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for f in data.get("files", []) if isinstance(data.get("files"), list) else []:
            if isinstance(f, dict) and f.get("type") == "bitable" and f.get("name") == base_name:
                token = f.get("token", "")
                return (token or None), None
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            return None, None


def _build_copy_app_request(template_app_token: str, name: str, folder_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/copy"
    req.paths["app_token"] = template_app_token
    # Copy is documented as user-token-only (feishu-bitable skill) — Feishu rejects
    # the bot's tenant token on this endpoint, unlike most other bitable writes.
    req.token_types = {AccessTokenType.USER}
    req.body = {"name": name, "folder_token": folder_token, "without_content": True}
    return req


def _build_create_app_request(name: str, folder_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"name": name, "folder_token": folder_token}
    return req


def _build_create_table_request(app_token: str, table_name: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    # Fields must live INSIDE the table object — a top-level "fields" key is
    # silently ignored by Feishu and yields a table with only placeholder columns.
    req.body = {
        "table": {"name": table_name, "fields": _LEDGER_SCHEMA_FIELDS},
    }
    return req


async def _provision_direct(
    folder_token: str, base_name: str, table_name: str, user_key: str, identity: str
) -> tuple[str | None, dict[str, Any] | None]:
    """Create a fresh base + table from ``_LEDGER_SCHEMA_FIELDS`` (no template).

    Feishu auto-creates a blank default "数据表" alongside the new app — the
    caller (per ``company-todo-sync``) deletes it via the confirm-code flow, so
    the returned error carries ``cleanup_note``.
    """
    res = await _core._invoke(_build_create_app_request(base_name, folder_token), user_key=user_key, identity=identity)
    if not res["ok"]:
        return None, res
    data = res["data"] if isinstance(res["data"], dict) else {}
    app = data.get("app", {}) if isinstance(data.get("app"), dict) else {}
    app_token = app.get("app_token", "")
    if not app_token:
        return None, _core._error(f"App creation succeeded but the response carried no app_token: {data!r}")

    table_res = await _core._invoke(
        _build_create_table_request(app_token, table_name), user_key=user_key, identity=identity
    )
    if not table_res["ok"]:
        return None, table_res
    tdata = table_res["data"] if isinstance(table_res["data"], dict) else {}
    table_id = tdata.get("table_id", "")
    if not table_id:
        return None, _core._error(f"Table creation succeeded but the response carried no table_id: {tdata!r}")

    cleanup_note = (
        "Fresh bases carry an auto-generated blank 数据表; delete it via the "
        "confirm-code flow (batch_delete) so the base keeps only 台账."
    )
    return app_token, {"ok": True, "table_id": table_id, "cleanup_note": cleanup_note}


def _build_list_tables_request(app_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    req.paths["app_token"] = app_token
    req.add_query("page_size", "20")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _first_table_id(app_token: str, user_key: str) -> tuple[str | None, dict[str, Any] | None]:
    res = await _core._invoke(_build_list_tables_request(app_token), user_key=user_key)
    if not res["ok"]:
        return None, res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    if not items:
        return None, _core._error(f"Base {app_token!r} has no tables — check the template base has one 待办 table.")
    first = items[0] if isinstance(items[0], dict) else {}
    table_id = first.get("table_id", "")
    if not table_id:
        return None, _core._error(f"Base {app_token!r}'s first table has no table_id in the response.")
    return table_id, None


def _build_grant_member_request(token: str, member_id: str, perm: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/drive/v1/permissions/:token/members"
    req.paths["token"] = token
    req.add_query("type", "bitable")
    req.add_query("need_notification", "false")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"member_type": "openid", "member_id": member_id, "perm": perm, "type": "user"}
    return req


async def _grant(token: str, member_id: str, perm: str, user_key: str, identity: str) -> dict[str, Any]:
    return await _core._invoke(
        _build_grant_member_request(token, member_id, perm), user_key=user_key, prefer="user", identity=identity
    )


async def mentor_ledger_ensure_impl(
    mentor_open_id: str,
    mentor_name: str,
    folder_token: str,
    template_app_token: str,
    boss_open_id: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Idempotently ensure one mentor's TODO ledger base exists, granted to mentor + boss.

    First lists ``folder_token`` for a bitable already named "TODO 台账-<mentor_name>";
    if found, returns its ``app_token``/``table_id`` without copying again. Otherwise
    provisions it: with ``template_app_token`` set, copies the template (structure only,
    ``without_content=True``) — this step needs a real person's Feishu identity
    (Feishu's ``/copy`` endpoint rejects the bot's tenant token); without a template,
    creates a fresh base + 台账 table from the fixed ``_LEDGER_SCHEMA_FIELDS`` so the
    columns are always correct (人员-typed 负责人/mentor, colored 层级 options, …).
    ``user_key``/``identity`` follow the same ownership-choice convention as every
    other write tool here (see ``feishu_sheet_write``): omit and this returns
    ``need_identity_choice`` on first use, then remembers the choice.

    Grants ``mentor_open_id`` edit and, if given, ``boss_open_id`` view — both via the
    documented ``drive/v1/permissions`` member grant. It does **not** grant the bot
    access: Feishu's permissions member-type enum (openid/userid/unionid/openchat/
    opendepartmentid/email/groupid/wikispaceid) has no entry for "the app itself", so
    the bot cannot be added as a collaborator through this endpoint. The result's
    ``bot_access`` field is always ``"not_granted"`` — add the app as a collaborator
    once per base through the Feishu client (更多 → 协作者管理) if the bot's own
    later reads/writes against this base need it.

    Args:
        mentor_open_id: The mentor's open_id — the ledger is named and granted for them.
        mentor_name: The mentor's display name, used in the base's title
            ("TODO 台账-<mentor_name>") and to detect an already-provisioned ledger.
        folder_token: The shared drive folder all mentor ledgers live under.
        template_app_token: The app_token of the pre-built template base to copy from.
            Empty = provision directly from the built-in fixed schema.
        boss_open_id: Optional — grant this person read-only access too.
        user_key: The sender's open_id whose identity performs the copy/grants.
        identity: ``"user"`` or ``"bot"`` — who owns the resulting base if it has to be
            created. Omit to use this person's remembered choice.

    Returns:
        JSON with ok, app_token, table_id, created (bool — False when an existing
        ledger was found), provision_mode ("copy" | "direct" | "" for an existing
        base), base_name, granted ({mentor, boss}), bot_access, and a
        ``cleanup_note`` when a fresh base carries an auto-generated 数据表 — or
        ok=false with a message (including ``need_identity_choice``/``need_auth``
        shapes) on failure.
    """
    if not mentor_open_id.strip():
        return _core._error("mentor_open_id is required.")
    if not mentor_name.strip():
        return _core._error("mentor_name is required.")
    if not folder_token.strip():
        return _core._error("folder_token is required (the shared drive folder for mentor ledgers).")

    base_name = _ledger_base_name(mentor_name)
    app_token, err = await _find_existing_base(folder_token.strip(), base_name)
    if err is not None:
        return err

    created = False
    provision_mode = ""
    cleanup_note = ""
    table_id_direct = ""
    if app_token is None:
        if template_app_token.strip():
            copy_res = await _core._invoke(
                _build_copy_app_request(template_app_token.strip(), base_name, folder_token.strip()),
                user_key=user_key,
                prefer="user",
                identity=identity,
            )
            if not copy_res["ok"]:
                return copy_res
            data = copy_res["data"] if isinstance(copy_res["data"], dict) else {}
            app = data.get("app", {}) if isinstance(data.get("app"), dict) else {}
            app_token = app.get("app_token", "")
            if not app_token:
                return _core._error(f"Copy succeeded but the response carried no app_token: {data!r}")
            provision_mode = "copy"
        else:
            app_token, direct = await _provision_direct(folder_token.strip(), base_name, "台账", user_key, identity)
            if app_token is None or direct is None:
                return direct if direct is not None else _core._error("provision direct failed without error detail")
            table_id_direct = direct["table_id"]
            cleanup_note = direct.get("cleanup_note", "")
            provision_mode = "direct"
        created = True

    table_id = table_id_direct if provision_mode == "direct" else ""
    if not table_id:
        table_id, err = await _first_table_id(app_token, user_key)
        if err is not None:
            return err

    granted: dict[str, str] = {}
    mentor_grant = await _grant(app_token, mentor_open_id.strip(), "edit", user_key, identity)
    granted["mentor"] = "ok" if mentor_grant["ok"] else mentor_grant.get("message", "failed")
    if boss_open_id.strip():
        boss_grant = await _grant(app_token, boss_open_id.strip(), "view", user_key, identity)
        granted["boss"] = "ok" if boss_grant["ok"] else boss_grant.get("message", "failed")

    result = {
        "ok": True,
        "app_token": app_token,
        "table_id": table_id,
        "created": created,
        "base_name": base_name,
        "granted": granted,
        "bot_access": "not_granted",
    }
    if provision_mode:
        result["provision_mode"] = provision_mode
    if cleanup_note:
        result["cleanup_note"] = cleanup_note
    return result
