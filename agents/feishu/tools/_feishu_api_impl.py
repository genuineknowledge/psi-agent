"""Implementation for the generic ``feishu_api`` tool.

Builds a ``BaseRequest`` from plain JSON arguments and hands it to the shared
``_feishu_impl._invoke`` — so the generic path inherits the authenticated client,
the tenant/user token strategy, rate-limit retry, and the error-code hint tables
rather than re-deriving any of it.

What this module adds on top is the *refusals*: a generic entry point can be pointed
at an endpoint whose request shape it cannot express (binary uploads), and it can be
handed a path it shouldn't reach. Failing early with the name of the right tool is
more useful than a Feishu 400 the caller has to decode.
"""

from __future__ import annotations

# ruff: noqa: E402
import json
import pathlib
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_confirm as _confirm
import _feishu_impl as _f
import _feishu_spec as _spec
import _runtime_paths as _paths
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest

from psi_agent.session.runtime_context import get_session_id

dumps_result = _f.dumps_result

_METHODS = {
    "GET": HttpMethod.GET,
    "POST": HttpMethod.POST,
    "PUT": HttpMethod.PUT,
    "PATCH": HttpMethod.PATCH,
    "DELETE": HttpMethod.DELETE,
}

# Endpoints whose body must carry a real file handle. A JSON string can't express one,
# and `Client.arequest` re-derives `request.files` from the body, so a generic caller
# would get a 400 "boundary not found" with nothing pointing at the cause.
_UPLOAD_ENDPOINTS = {
    "/open-apis/im/v1/images": "feishu_message_send_image",
    "/open-apis/im/v1/files": "feishu_message_send_file / _send_audio / _send_video",
    "/open-apis/drive/v1/medias/upload_all": "feishu_drive_upload",
    "/open-apis/drive/v1/files/upload_all": "feishu_drive_upload",
}

# Where a hand-built request is a known foot-gun. Not a block — a warning attached to
# the result, because the endpoint list below is not exhaustive and a hard refusal
# would strand legitimate calls.
_PREFER_DEDICATED = (
    ("/open-apis/sheets/", "飞书表格写入: 裸 `!A1` 区间会静默丢数据, 建议用 feishu_sheet_write / _append"),
    (
        "/open-apis/bitable/",
        "多维表格: 列名对不上会被静默丢弃, 写行建议用 feishu_bitable_create_records / _update_records (它们先核对列名)",
    ),
    ("/open-apis/authen/", "OAuth 流程: 用 feishu_auth_* 工具, 它们管着 UAT 存储与回调接收"),
)

_ALL_HINTS: dict[int, str] = {}
for _name in dir(_f):
    if _name.endswith("_HINTS"):
        _table = getattr(_f, _name)
        if isinstance(_table, dict):
            for _code, _text in _table.items():
                if isinstance(_code, int):
                    _ALL_HINTS.setdefault(_code, _text)


def _loads_object(raw: str, what: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Parse a JSON object argument; empty string means "not given"."""
    text = (raw or "").strip()
    if not text:
        return {}, None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        return {}, _f.error_result(f'{what} is not valid JSON: {exc}. Pass a JSON object, e.g. \'{{"k":"v"}}\'.')
    if not isinstance(parsed, dict):
        return {}, _f.error_result(f"{what} must be a JSON object, got {type(parsed).__name__}.")
    return {str(k): v for k, v in parsed.items()}, None


def _normalize_uri(uri: str) -> tuple[str, dict[str, Any] | None]:
    """Require an absolute Open Platform path — a relative one silently 404s."""
    path = (uri or "").strip()
    if not path:
        return "", _f.error_result("uri is required, e.g. '/open-apis/contact/v3/users/:user_id'.")
    if path.startswith("http://") or path.startswith("https://"):
        return "", _f.error_result(
            "uri must be a path, not a full URL — the host comes from the SDK client. Use '/open-apis/...' instead."
        )
    if not path.startswith("/"):
        path = "/" + path
    if not path.startswith("/open-apis/"):
        return "", _f.error_result(
            f"uri must start with '/open-apis/', got {path!r}. Every Feishu Open Platform endpoint lives under it."
        )
    return path, None


def _check_not_upload(uri: str) -> dict[str, Any] | None:
    """Refuse endpoints that need a file handle, naming the tool that has one."""
    for endpoint, tool in _UPLOAD_ENDPOINTS.items():
        if uri.startswith(endpoint):
            return _f.error_result(
                f"{endpoint} uploads binary content, which this tool cannot send: the body must be a real "
                f"file handle, not JSON. Use {tool} instead — it does the upload and the send together.",
                code="use_dedicated_tool",
                tool=tool,
            )
    return None


def _warning_for(uri: str) -> str:
    for prefix, note in _PREFER_DEDICATED:
        if uri.startswith(prefix):
            return note
    return ""


def _skills_dir() -> str:
    """Where the endpoint tables live. Agent root, same place the model reads them from."""
    return str(pathlib.Path(_paths.agent_dir()) / "skills")


def _spec_refusal(
    rule: Any,
    body: dict[str, Any],
    query: dict[str, Any],
    paths: dict[str, Any],
    confirm: str = "",
) -> dict[str, Any] | None:
    """Refuse this call if the endpoint table says it cannot succeed as written.

    This is the difference between a table the model *reads* and a table that
    *executes*. Feishu accepts a bare ``!A1`` range and a mismatched Bitable column
    with ``code: 0`` and writes nothing — a warning attached to a successful-looking
    result is indistinguishable from success to the caller, so the only useful place
    to stop is before the request goes out.

    The ``confirm`` gate is *not* here — it needs the network (it sends the user a
    code) and so lives in :func:`_confirm_refusal`. Everything in this function is
    pure, which is what lets it run before any request is built.
    """
    if rule is None:
        return None
    if rule.prefer_tool and rule.prefer_hard:
        why = f" — {rule.why}" if rule.why else ""
        return _f.error_result(
            f"这个端点请用 {rule.prefer_tool}{why}",
            code="use_dedicated_tool",
            tool=rule.prefer_tool,
            endpoint=rule.endpoint,
        )
    if violations := _spec.validate(rule, body, query, paths):
        return _f.error_result(
            "请求没有发出 —— 端点表校验未通过: " + "; ".join(violations),
            code="spec_violation",
            endpoint=rule.endpoint,
            violations=violations,
            pitfalls=rule.pitfalls or None,
            note="若确认飞书已放宽该限制, 改 skills/*/SKILL.md 里这条 rules 而不是绕过校验",
        )
    return None


async def _send_paged(
    build: Any,
    paginate: dict[str, Any],
    user_key: str | None,
    prefer: str,
    identity: str,
) -> dict[str, Any]:
    """Follow ``page_token`` until Feishu says there is no more, concatenating items.

    This is what makes a paging endpoint expressible as a table row. The protocol is
    the same everywhere — ask with ``page_size``, read ``has_more`` and the next
    ``page_token`` back — so the only per-endpoint facts are which key holds the items
    and how big a page to ask for, both of which come from the rule.

    A partial failure returns what was already collected alongside the error: for a
    roster read, three pages of members plus "page 4 was rate-limited" is useful,
    while discarding everything is not. Callers can tell the difference because
    ``ok`` is false and ``partial`` is set.
    """
    key = paginate["items"]
    collected: list[Any] = []
    token = ""
    for page in range(1, paginate["max_pages"] + 1):
        res = await _f._invoke(build(token), user_key=user_key, prefer=prefer, identity=identity)
        if not res.get("ok"):
            if collected:
                return {**res, "partial": True, key: collected, "count": len(collected), "pages": page - 1}
            return res
        payload = res.get("data")
        data: dict[str, Any] = payload if isinstance(payload, dict) else {}
        chunk = data.get(key)
        if isinstance(chunk, list):
            collected.extend(chunk)
        token = str(data.get("page_token", "") or "")
        if not data.get("has_more") or not token:
            return {"ok": True, "code": 0, key: collected, "count": len(collected), "pages": page}
    return {
        "ok": True,
        "code": 0,
        key: collected,
        "count": len(collected),
        "pages": paginate["max_pages"],
        "truncated": True,
        "message": f"已读 {paginate['max_pages']} 页后停止 —— 飞书仍报 has_more。"
        f"若结果确实该更多, 检查 skills 里这条 rules 的 paginate.items 是否写对了键名。",
    }


def _query_pairs(query: dict[str, Any]) -> dict[str, Any]:
    """Stringify query values; keep lists as lists so the SDK repeats the key."""
    out: dict[str, Any] = {}
    for key, value in query.items():
        if isinstance(value, bool):
            out[key] = "true" if value else "false"
        elif isinstance(value, list):
            out[key] = [str(v) for v in value]
        elif value is None:
            continue
        else:
            out[key] = str(value)
    return out


def _build_request(
    http_method: HttpMethod,
    uri: str,
    body: dict[str, Any],
    query: dict[str, Any],
    paths: dict[str, Any],
    prefer: str,
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = http_method
    req.uri = uri
    # Both token types are declared as *candidates* regardless of strategy, because
    # ``prefer`` is what selects one — ``_invoke`` reads it and routes to the tenant or
    # the user send. Narrowing to USER here instead would make the request unsendable as
    # tenant (the SDK raises before any network call), and ``_invoke_write`` sends as
    # tenant on purpose in two cases: nobody is logged in, so there is no identity to
    # attribute to, and the user explicitly answered "the bot should own this".
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    for key, value in paths.items():
        req.paths[key] = str(value)
    for key, value in _query_pairs(query).items():
        if isinstance(value, list):
            for item in value:
                req.add_query(key, item)
        else:
            req.add_query(key, value)
    if body:
        req.body = body
    return req


async def call_api_impl(
    method: str,
    uri: str,
    body_json: str = "",
    query_json: str = "",
    paths_json: str = "",
    prefer: str = "tenant",
    identity: str = "",
    user_key: str = "",
    confirm: str = "",
) -> dict[str, Any]:
    """Send one arbitrary Open Platform request, reusing the shared invoke path."""
    verb = (method or "").strip().upper()
    if verb not in _METHODS:
        return _f.error_result(f"method must be one of {', '.join(sorted(_METHODS))}, got {method!r}.")
    path, err = _normalize_uri(uri)
    if err:
        return err
    if refusal := _check_not_upload(path):
        return refusal

    body, err = _loads_object(body_json, "body_json")
    if err:
        return err
    query, err = _loads_object(query_json, "query_json")
    if err:
        return err
    paths, err = _loads_object(paths_json, "paths_json")
    if err:
        return err

    missing = [name for name in _placeholders(path) if name not in paths]
    if missing:
        return _f.error_result(
            f'uri has unfilled placeholders {missing}; supply them in paths_json, e.g. \'{{"{missing[0]}":"..."}}\'.',
            code="missing_path_params",
        )

    # Endpoint table: refuse what it says cannot work, then fill the defaults it
    # declares. Both happen before the request is built, so a violation costs nothing.
    rule = _spec.rules_for(_skills_dir(), verb, path)
    if refusal := _spec_refusal(rule, body, query, paths, confirm):
        return refusal
    # Kept after the pure checks and before anything is sent: a call that is malformed
    # anyway should be corrected without spending a confirmation code on it.
    if refusal := await _confirm_refusal(rule, verb, path, paths, confirm, user_key):
        return refusal
    for name, value in _spec.defaults_for(rule)["query"].items():
        query.setdefault(name, value)
    for name, value in _spec.defaults_for(rule)["body"].items():
        body.setdefault(name, value)

    strategy = "user" if (prefer or "").strip().lower() == "user" else "tenant"
    # A rule that names its token strategy overrides the default, not an explicit
    # caller choice: "user" is the caller insisting, and some endpoints only accept it.
    if rule is not None and rule.token and (prefer or "").strip().lower() != "user":
        strategy = "user" if rule.token == "user" else "tenant"

    caller_key = user_key or None
    acts_as = (identity or "").strip()
    paginate = rule.paginate if rule is not None else None
    if paginate and verb in ("GET", "POST"):
        # A fresh request per page: `_invoke` mutates what it is given (token_types
        # narrowed by verify, files stripped from the body), so re-sending one object
        # with a new token would send it under an identity the caller never chose.
        def build_page(token: str, _pg: dict[str, Any] = paginate) -> BaseRequest:
            paged = dict(query)
            paged.setdefault(_pg["param"], _pg["page_size"])
            if token:
                paged["page_token"] = token
            return _build_request(_METHODS[verb], path, body, paged, paths, strategy)

        res = await _send_paged(build_page, paginate, caller_key, strategy, acts_as)
        return _f._with_hint(res, _ALL_HINTS)

    request = _build_request(_METHODS[verb], path, body, query, paths, strategy)
    res = await _f._invoke(request, user_key=caller_key, prefer=strategy, identity=acts_as)
    res = _f._with_hint(res, _ALL_HINTS)
    if (note := _warning_for(path)) and not res.get("ok", True):
        res = {**res, "warning": note}
    return res


def _placeholders(uri: str) -> list[str]:
    """``:name`` segments the SDK will substitute from ``request.paths``."""
    return [seg[1:] for seg in uri.split("/") if seg.startswith(":") and len(seg) > 1]


async def _confirm_refusal(
    rule: _spec.Rule | None,
    verb: str,
    path: str,
    paths: dict[str, Any],
    confirm: str,
    user_key: str,
) -> dict[str, Any] | None:
    """Hold an irreversible call until the *user* supplies a code sent out of band.

    Why not just compare ``confirm`` to the rule's token, as this used to: the token
    is a constant in the skill file the model has already read. Being told "echo
    解散群" is not an obstacle to something that knows the word — the refusal and the
    retry both fit in one turn, with the user never consulted. Feishu keeps no history
    for a dissolved group, so that turn destroys every message and file in it with no
    way back.

    So the secret becomes one only the person has: a 6-digit code sent to them as a
    private message and deliberately **not** returned here. A model that cannot see
    the code cannot proceed without asking, which is the entire property being bought.

    Scope binds the code to session + method + uri + path params, so approving one
    group's dismissal approves exactly that group. Without ``user_key`` there is
    nobody to ask, and the call is refused rather than waved through.
    """
    if rule is None or not rule.confirm:
        return None
    scope = _confirm.scope_key(get_session_id(), verb, path, paths)
    supplied = (confirm or "").strip()
    if supplied and await _confirm.redeem(scope, supplied):
        return None

    target = (user_key or "").strip()
    pitfalls = rule.pitfalls or None
    if not target:
        return _f.error_result(
            "这一步不可逆, 没有执行。需要本人用验证码确认, 但缺 user_key —— "
            "把 <feishu_context> 里的 sender_open_id 作为 user_key 传进来, 我会把验证码私聊发给他。",
            code="need_confirmation",
            need_confirmation=True,
            endpoint=rule.endpoint,
            pitfalls=pitfalls,
        )

    code = await _confirm.issue(scope, rule.endpoint, "")
    what = f"{rule.confirm} ({rule.endpoint})"
    detail = "".join(f"\n- {note}" for note in (rule.pitfalls or []))
    sent = await _f.send_message_impl(
        target,
        f"【不可逆操作确认】即将执行: {what}\n"
        f"目标: {json.dumps(paths, ensure_ascii=False) if paths else '(无)'}"
        f"{detail}\n\n"
        f"确认码: {code}\n"
        f"确认执行请把这 6 位数字回给我; {_confirm.ttl_minutes()} 分钟内有效, 只能用一次。"
        f"不想做就别回它 —— 不回就不会执行。",
        "open_id",
    )
    if not sent.get("ok"):
        why = sent.get("error") or sent.get("message") or "发送失败"
        return _f.error_result(
            f"这一步不可逆, 没有执行 —— 而且确认码没能发给用户: {why}。"
            "请直接在对话里向本人确认后重试 (确认码只能由本人提供)。",
            code="need_confirmation",
            need_confirmation=True,
            endpoint=rule.endpoint,
            pitfalls=pitfalls,
        )
    hint = "确认码不对或已过期, 已重新发送一条。" if supplied else "已把确认码私聊发给本人。"
    return _f.error_result(
        f"这一步不可逆, 没有执行。{hint}请让本人把收到的 6 位确认码告诉你, "
        "然后带 confirm=<那6位数字> 再调一次 —— 现在先告诉他将要发生什么, 不要自己编码。",
        code="need_confirmation",
        need_confirmation=True,
        endpoint=rule.endpoint,
        confirm_sent_to=target,
        pitfalls=pitfalls,
    )
