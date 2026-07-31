"""Private helper for the Feishu tools — authenticated client + request execution.

Wraps the ``lark_channel`` SDK (already a project dependency): builds one
authenticated ``Client`` from ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``,
caches it module-level, and runs ``BaseRequest`` objects through the SDK's native
async ``arequest``. Drive-comment requests reuse the SDK's ready-made builders;
docx/doc/sheet raw-content and create-reply requests are hand-built the same way
the SDK's own ``api/drive/comment.py`` does it.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import random
import re
from typing import Any

import _oauth_receiver as _oauth_rx
import _runtime_paths as _paths
import anyio
from lark_channel.api.drive import comment as _comment
from lark_channel.api.wiki import node as _wiki_node
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest
from loguru import logger

from psi_agent.channel.feishu._card_store import save_card_snapshot
from psi_agent.session.runtime_context import get_session_id

_client: Any = None


def dumps_result(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False)


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "message": message, **extra}


def error_result(message: str, **extra: Any) -> dict[str, Any]:
    """Public alias of ``_error`` for sibling tool helpers (e.g. the chart tools)."""
    return _error(message, **extra)


def _config() -> tuple[str, str] | None:
    app_id = os.environ.get("PSI_FEISHU_APP_ID", "").strip()
    app_secret = os.environ.get("PSI_FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        return None
    return app_id, app_secret


def _reset_client() -> None:
    global _client
    _client = None


def _get_client() -> Any:
    global _client
    if _client is not None:
        return _client
    creds = _config()
    if creds is None:
        return None
    from lark_channel.client import Client  # noqa: PLC0415

    app_id, app_secret = creds
    _client = Client.builder().app_id(app_id).app_secret(app_secret).build()
    return _client


# Shown to the user whenever a user_access_token is genuinely required (tenant
# token can't do it and no cached UAT exists). Spelled out step-by-step — the
# key gotcha is that the code lives in the browser ADDRESS BAR after redirect.
_AUTH_PROMPT = (
    "需要用你的飞书身份授权一次才能继续 (机器人自己的权限做不了这一步). 首选一键授权卡:\n"
    "1. 调 feishu_auth_card(user_key=<sender_open_id>, capabilities=<本次 need_capabilities>, "
    "reason=<一句话说明用途>) 给用户发一张授权卡, **然后这一轮就收尾** —— 别在同一轮里等待, "
    "也别再把链接当文本发一遍;\n"
    "2. 用户点卡上的按钮时, 飞书会把这次点击回调给你 (一条 <feishu_card_action>, dispatch.handler "
    "是 feishu_auth_wait): **那一轮**再调 feishu_auth_wait (用回调 value 里的 user_key) 等授权码"
    "自动回流, 拿到 token 后继续原来的操作;\n"
    "3. 卡片是一次性的: 用户点了按钮但没在授权页点「同意」时, 重新调 feishu_auth_card 发一张新的.\n"
    "环境没有自动回调通道时 (feishu_auth_card 返回 manual_required=True) 才退回手工: 调 "
    "feishu_auth_start 把 authorize_url 发给用户, 再让他从浏览器**地址栏**复制 code= 后面那一串 "
    "(或整段网址) 交给 feishu_auth_complete. 这种情况下想帮用户彻底免掉复制, 调 "
    "feishu_auth_env_check 查出确切缺哪一项配置并按它给的修法告诉用户.\n"
    "授权一次即缓存并自动续期, 之后同类操作不会再让你授权."
)


# Feishu permission-denied codes: the 999916xx family (drive/docs "no permission"),
# 1254xxx (bitable), 131006 (wiki node no permission), 1770032 (docx block edit denied
# for this identity). Combined with a msg-substring check so we still catch permission
# failures whose exact code we don't enumerate.
_PERMISSION_CODES = {99991672, 99991663, 99991661, 131006, 1254302, 1254045, 1254043, 1770032}
_PERMISSION_MSG_HINTS = ("permission", "forbidden", "无权限", "没有权限", "access denied", "not authorized")


def _is_permission_error(res: dict[str, Any]) -> bool:
    """True if ``res`` is a Feishu permission/authorization failure (so a UAT retry
    could help). Distinct from transport errors or empty-but-ok responses."""
    if res.get("ok"):
        return False
    code = res.get("code")
    if isinstance(code, int) and (code in _PERMISSION_CODES or 1254000 <= code <= 1254999):
        return True
    msg = f"{res.get('msg', '')} {res.get('message', '')}".lower()
    return any(h in msg for h in _PERMISSION_MSG_HINTS)


def _fresh(request: Any) -> Any:
    """The request to hand the SDK for one send attempt.

    ``Client.arequest`` mutates what it is given: ``verify()`` narrows ``token_types``
    to the single type it used, and ``Files.extract_files()`` *removes* the file entry
    from the body. Re-sending the same object therefore uploads nothing, under a token
    type the caller never chose — the second attempt raises
    ``NoAuthorizationException: user_access_token not found`` instead of falling back.

    Callers that must survive a retry pass a zero-arg factory and get a clean request
    each time. A plain ``BaseRequest`` is still accepted and made retry-safe by
    ``_restorable`` below.
    """
    return request() if callable(request) else request


def _restorable(request: Any) -> Any:
    """Turn a plain ``BaseRequest`` into a factory that rewinds the SDK's mutations.

    Not every call site can rebuild its request, but every call site can be retried
    under a second identity. Snapshot the two fields the SDK edits in place and restore
    them before handing the object over again. Streams in the body are rewound rather
    than copied, so an upload retry re-sends the same bytes.

    Objects that don't accept attribute assignment (test doubles, bare sentinels) are
    passed through untouched — rewinding is an optimization for retries, never a
    precondition for sending.
    """
    if callable(request):
        return request
    token_types = set(getattr(request, "token_types", set()) or set())
    body = getattr(request, "body", None)
    snapshot = dict(body) if isinstance(body, dict) else None

    def rewind() -> Any:
        with contextlib.suppress(AttributeError, TypeError):
            request.token_types = set(token_types)
            if snapshot is not None:
                request.body = dict(snapshot)
                for value in request.body.values():
                    if isinstance(value, io.IOBase) and value.seekable():
                        value.seek(0)
            request.files = None
        return request

    return rewind


async def _send_as_tenant(request: Any) -> dict[str, Any]:
    """Send a BaseRequest (or a request factory) with the bot's tenant token."""
    client = _get_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    try:
        resp = await client.arequest(_fresh(request))
    except Exception as exc:  # SDK/transport failure
        return _error(f"Feishu request failed: {type(exc).__name__}: {exc}")
    return _resp_to_result(resp)


async def _send_as_user(request: Any, user_key: str) -> dict[str, Any] | None:
    """Send a BaseRequest (or a request factory) with the user's UAT. Returns None (no
    send attempted) when the app isn't configured or the user has no cached/valid UAT —
    callers decide whether that means need_auth or a tenant fallback."""
    client = _get_uat_client()
    if client is None:
        return None
    uat = await _get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return None
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(_fresh(request), option)
    except Exception as exc:  # SDK/transport failure
        return _error(f"Feishu request failed: {type(exc).__name__}: {exc}")
    return _resp_to_result(resp)


_RATE_LIMIT_STATUS = 429
# Feishu's docx write limit is a few requests per second per app, and one agent turn can
# legitimately queue 20+ writes (a document full of charts). Six attempts of backoff
# spans ~15s, which is long enough for a burst that size to drain; fewer attempts left
# the tail of a 21-chart batch still being turned away.
_RATE_LIMIT_ATTEMPTS = 6
_RATE_LIMIT_BACKOFF = 0.5
_RATE_LIMIT_MAX_WAIT = 8.0


def _is_rate_limited(res: dict[str, Any]) -> bool:
    """Whether Feishu turned this request away for being too frequent."""
    return res.get("http_status") == _RATE_LIMIT_STATUS


def _retry_after_seconds(res: dict[str, Any], attempt: int) -> float:
    """How long to wait before retry ``attempt`` (1-based).

    Feishu's 429 carries no ``Retry-After``, so this is exponential backoff with a
    little jitter — without jitter a batch of charts throttled together would retry
    in lockstep and throttle each other again.
    """
    after = res.get("retry_after")
    if isinstance(after, (int, float)) and after > 0:
        return min(float(after), _RATE_LIMIT_MAX_WAIT)
    grown = _RATE_LIMIT_BACKOFF * (2 ** (attempt - 1))
    return min(grown, _RATE_LIMIT_MAX_WAIT) * (1.0 + random.random() * 0.25)


async def _retrying_rate_limits(send: Any) -> dict[str, Any]:
    """Call ``send()`` again while Feishu is only telling us to slow down.

    A 429 means "too fast", not "not allowed": the same request succeeds moments later.
    A rate limit that outlives every attempt is returned as-is, so the caller still
    reports a real, readable error instead of hanging.
    """
    res: dict[str, Any] = {}
    for attempt in range(1, _RATE_LIMIT_ATTEMPTS + 1):
        res = await send()
        if not _is_rate_limited(res) or attempt == _RATE_LIMIT_ATTEMPTS:
            return res
        await anyio.sleep(_retry_after_seconds(res, attempt))
    return res


async def _invoke(
    request: Any,
    user_key: str | None = None,
    prefer: str = "tenant",
    identity: str = "",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Send a request, retrying while Feishu is rate-limiting us.

    Retrying here rather than at each call site means every tool gets it — inserting
    five charts into one document is a single agent turn, and it hits the per-app limit
    (measured: ~3 concurrent writes go through, 5+ start getting turned away).

    ``_invoke_once`` holds the identity/permission strategy; this wrapper only adds
    waiting.
    """

    async def send() -> dict[str, Any]:
        return await _invoke_once(
            request, user_key=user_key, prefer=prefer, identity=identity, capabilities=capabilities
        )

    return await _retrying_rate_limits(send)


# Which capability an API path needs, matched by URI prefix (longest first, so the
# sheets/drive overlap resolves the specific way). Derived centrally instead of being
# named at each of the ~30 write call sites: a call site that forgets to declare its
# capability would ask the user for the wrong permissions, and every future tool would
# have to remember. Anything unmatched needs no *user* scope beyond the login itself.
_URI_CAPABILITIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("/open-apis/docx/v1/", ("docx_write",)),
    ("/open-apis/wiki/v2/", ("wiki_write",)),
    ("/open-apis/bitable/v1/", ("bitable_write",)),
    ("/open-apis/task/v2/", ("task_write",)),
    ("/open-apis/calendar/v4/", ("calendar_write",)),
    # Spreadsheets and file/permission operations are all cloud-drive writes.
    ("/open-apis/sheets/v2/", ("drive_write",)),
    ("/open-apis/drive/v1/permissions/", ("drive_write",)),
    ("/open-apis/drive/v1/medias/", ("drive_write",)),
    ("/open-apis/drive/v1/files/", ("drive_write",)),
    ("/open-apis/contact/v3/", ("contact_read",)),
)


def capabilities_for(request: Any) -> list[str]:
    """The user capabilities a request needs, inferred from its API path.

    ``request`` may be a factory (as passed for retry-safe uploads); it is inspected,
    never sent. An unrecognized path yields no capabilities, which means "a plain
    authorization is enough" — the honest answer when we can't attribute a scope, and
    it degrades to Feishu's own permission error rather than to a wrong prompt.
    """
    probe = request
    if callable(probe):
        with contextlib.suppress(Exception):
            probe = probe()
    uri = getattr(probe, "uri", "") or ""
    if not isinstance(uri, str):
        return []
    for prefix, caps in sorted(_URI_CAPABILITIES, key=lambda kv: -len(kv[0])):
        if uri.startswith(prefix):
            return list(caps)
    return []


_IDENTITY_PROMPT = (
    "这次操作会产出内容 (文档/表格/任务), 需要先定归属 -- 用**你本人的飞书身份**做, 产出就归你; "
    "用**机器人身份**做, 产出归机器人 (你可能需要它再共享给你).\n"
    "请问用哪个身份? 得到答复后调 feishu_identity_set 记下来, 之后不会再问."
)


def _identity_choice_needed(user_key: str, capabilities: list[str] | None) -> dict[str, Any]:
    """The 'ask the user who should own this' result. Deliberately sends nothing."""
    return _error(
        _IDENTITY_PROMPT,
        need_identity_choice=True,
        user_key=user_key,
        identity_options=list(_IDENTITY_CHOICES),
        would_need_capabilities=list(capabilities or []),
    )


async def _invoke_write(request: Any, key: str, identity: str, capabilities: list[str] | None) -> dict[str, Any]:
    """Perform an ownership-creating request under an explicitly chosen identity.

    Split out of ``_invoke_once`` so the ownership rules read in one place: without a
    user there is nobody to own anything, with a user the choice is theirs to make,
    and a chosen identity is honoured rather than silently swapped for the other one.

    The one exception is a *resource*-level denial: if Feishu refuses the user's own
    identity on this particular document, the bot retries, because that says nothing
    about who should own the result and refusing outright would abandon a write the
    user asked for.
    """
    if not key:
        # Nobody to attribute to and nobody to ask — the bot is the only identity.
        return await _send_as_tenant(request)

    # An explicit list wins; otherwise infer from the API path being called.
    needed = list(capabilities) if capabilities is not None else capabilities_for(request)
    choice = (identity or "").strip().lower() or get_identity(key)
    if choice not in _IDENTITY_CHOICES:
        return _identity_choice_needed(key, needed)

    if choice == _IDENTITY_BOT:
        # Explicitly the bot's: never reach for the user's token, even if cached.
        return await _send_as_tenant(request)

    missing = missing_capabilities(key, needed)
    if missing:
        return _error(
            f"{_AUTH_PROMPT}\n本次需要新的权限: {', '.join(missing)}.",
            need_auth=True,
            need_capabilities=missing,
        )
    user_res = await _send_as_user(request, key)
    if user_res is None:
        # No usable token at all: the user chose to own this, so ask them to
        # authorize rather than producing it under the bot's name behind their back.
        return _error(_AUTH_PROMPT, need_auth=True, need_capabilities=needed)
    if not _is_permission_error(user_res):
        return user_res
    # The user authorized the app, but Feishu refuses their identity on THIS resource
    # (e.g. 1770032 on a block they may not edit) — a fact about the target, not about
    # ownership. The bot can often do it, and finishing the write is what the user
    # asked for; failing here is how captions broke while the image went in fine.
    tenant_res = await _send_as_tenant(request)
    if tenant_res.get("ok"):
        return tenant_res
    # Neither identity may touch it: report the denial itself. Re-authorizing cannot
    # grant rights on someone else's document, so an auth prompt would be a dead end.
    return user_res


async def _invoke_once(
    request: Any,
    user_key: str | None = None,
    prefer: str = "tenant",
    identity: str = "",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Send a BaseRequest under a deliberate identity.

    ``request`` may be a ``BaseRequest`` or a zero-arg factory returning one. Pass a
    factory whenever the request could be sent twice (see ``_fresh``) — notably for
    uploads, whose file entry the SDK strips from the body on the first send.

    ``prefer`` selects the strategy (``user_key`` is the sender's open_id, used to
    resolve that user's cached UAT and remembered choices):

    - ``"tenant"`` (reads): try tenant first; if it fails with a *permission* error
      and the user has a cached UAT, transparently retry as the user. Reads create
      nothing, so nobody is asked to choose an owner — only to authorize, and only
      when tenant is genuinely denied. Passing ``user_key`` here is harmless.
    - ``"user"`` (writes/creates): the result is *owned* by whoever performs it, so
      the owner is chosen explicitly rather than inferred from who happens to have a
      cached token. ``identity`` decides:

      * ``"user"`` — act as the user; if their grant doesn't cover ``capabilities``
        (or they have no token), return ``need_auth`` with the missing capabilities
        instead of quietly producing bot-owned content under a different owner than
        the one just chosen.
      * ``"bot"`` — tenant token only, never the UAT. Content is owned by the bot.
      * ``""`` — fall back to this user's remembered choice; if they have never been
        asked, send nothing and return ``need_identity_choice`` so the caller asks.
        Ownership is not a detail to guess on someone's behalf.

    ``user_key`` empty/None means there is no user to own anything or to ask —
    tenant only, and no ownership question.
    """
    key = user_key.strip() if user_key else ""
    # Both branches below can send twice; make the request survive the first send.
    request = _restorable(request)

    if prefer == "user":
        return await _invoke_write(request, key, identity, capabilities)

    # prefer == "tenant": tenant first, UAT retry only on permission failure.
    tenant_res = await _send_as_tenant(request)
    if not _is_permission_error(tenant_res):
        return tenant_res
    if not key:
        # No user identity to fall back to — surface the original tenant error.
        return tenant_res
    user_res = await _send_as_user(request, key)
    if user_res is not None:
        return user_res
    # Tenant is denied and this user has no token: name the capability the read needs
    # so the authorize page asks for that rather than a blanket set.
    needed = list(capabilities) if capabilities is not None else capabilities_for(request)
    return _error(_AUTH_PROMPT, need_auth=True, need_capabilities=needed)


async def _invoke_wiki_read(request: Any, user_key: str | None, is_empty: Any) -> dict[str, Any]:
    """Wiki listing/resolve reads: tenant first, but the bot is usually not a member
    of any wiki space, so tenant succeeds with an *empty* payload rather than a
    permission error. Detect that (via ``is_empty(res)``) and transparently retry as
    the user, so we don't wrongly report "no knowledge bases". No re-auth prompt on
    the empty case — if the user simply has none, the empty tenant result stands."""
    request = _restorable(request)
    res = await _invoke(request, user_key=user_key, prefer="tenant")
    key = user_key.strip() if user_key else ""
    if res.get("ok") and key and is_empty(res):

        async def as_user() -> dict[str, Any]:
            # `or {}` so a missing-token None reads as "nothing to retry", not a rate limit.
            return await _send_as_user(request, key) or {}

        user_res = await _retrying_rate_limits(as_user)
        if user_res.get("ok"):
            return user_res
    return res


_HTTP_STATUS_HINTS = {
    429: "触发飞书接口频率限制: 请求过于频繁, 稍后重试或降低并发",
    502: "飞书网关错误 502",
    503: "飞书服务暂时不可用 503",
    504: "飞书网关超时 504",
}


def _resp_to_result(resp: Any) -> dict[str, Any]:
    code = getattr(resp, "code", None)
    msg = getattr(resp, "msg", "") or ""
    data: dict[str, Any] = {}
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if content:
        try:
            body = json.loads(bytes(content).decode("utf-8"))
            if isinstance(body, dict):
                data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
                if code is None:
                    code = body.get("code")
                if not msg:
                    msg = body.get("msg", "") or ""
        except ValueError, UnicodeDecodeError:
            pass

    ok = code == 0
    if not ok:
        # Rate limits and gateway errors come back with an EMPTY body and no JSON
        # content-type, so the SDK leaves `code` as None and there is nothing to parse:
        # the only evidence is the HTTP status. Without this fallback every 429 reported
        # itself as "Feishu API error None: " — which is how a plain rate limit got
        # misdiagnosed as a document lock and as a broken upload API.
        status = getattr(raw, "status_code", None)
        if code is None and isinstance(status, int) and status >= 400:
            msg = msg or _HTTP_STATUS_HINTS.get(status, f"飞书返回 HTTP {status}, 响应体为空")
            return {
                "ok": False,
                "code": None,
                "http_status": status,
                "msg": msg,
                "data": data,
                "message": f"Feishu HTTP {status}: {msg}",
            }
        return {
            "ok": False,
            "code": code,
            "msg": msg,
            "data": data,
            "message": f"Feishu API error {code}: {msg}",
        }
    return {"ok": True, "code": 0, "msg": msg, "data": data}


async def add_comment_impl(
    file_token: str,
    file_type: str,
    content: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    req = _comment.build_comment_create_request(file_token=file_token, file_type=file_type, content=content)
    return await _invoke(req, user_key=user_key, prefer="user", identity=identity)


async def list_comments_impl(file_token: str, file_type: str, page_size: int, page_token: str) -> dict[str, Any]:
    req = _comment.build_comment_list_request(
        file_token=file_token,
        file_type=file_type,
        page_size=page_size,
        page_token=page_token or None,
        is_whole="true",
    )
    return await _invoke(req)


async def list_comment_replies_impl(
    file_token: str, file_type: str, comment_id: str, page_size: int, page_token: str
) -> dict[str, Any]:
    req = _comment.build_comment_reply_list_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        page_size=page_size,
        page_token=page_token or None,
    )
    return await _invoke(req)


def _build_reply_create_request(
    *, file_token: str, file_type: str, comment_id: str, content: str, at_user_id: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/drive/v1/files/:file_token/comments/:comment_id/replies"
    req.paths["file_token"] = file_token
    req.paths["comment_id"] = comment_id
    req.add_query("file_type", file_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    elements: list[dict[str, Any]] = []
    if at_user_id:
        elements.append({"type": "person", "person": {"user_id": at_user_id}})
    elements.append({"type": "text_run", "text_run": {"text": content}})
    req.body = {"content": {"elements": elements}}
    return req


async def reply_comment_impl(
    file_token: str,
    file_type: str,
    comment_id: str,
    content: str,
    at_user_id: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    req = _build_reply_create_request(
        file_token=file_token,
        file_type=file_type,
        comment_id=comment_id,
        content=content,
        at_user_id=at_user_id,
    )
    return await _invoke(req, user_key=user_key, prefer="user", identity=identity)


def _raw_get(uri: str, path_name: str, path_value: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = uri
    req.paths[path_name] = path_value
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_docx_raw_request(document_id: str) -> BaseRequest:
    return _raw_get("/open-apis/docx/v1/documents/:document_id/raw_content", "document_id", document_id)


def _build_doc_raw_request(doc_token: str) -> BaseRequest:
    return _raw_get("/open-apis/doc/v2/:doc_token/raw_content", "doc_token", doc_token)


def _build_sheet_meta_request(spreadsheet_token: str) -> BaseRequest:
    return _raw_get(
        "/open-apis/sheets/v3/spreadsheets/:spreadsheet_token/sheets/query",
        "spreadsheet_token",
        spreadsheet_token,
    )


def _build_sheet_values_request(spreadsheet_token: str, range_: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values/:range"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.paths["range"] = range_
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _sheet_values_to_text(data: dict[str, Any]) -> str:
    grid = data.get("valueRange", {}).get("values", []) if isinstance(data, dict) else []
    lines: list[str] = []
    for row in grid if isinstance(grid, list) else []:
        cells = [("" if c is None else str(c)) for c in (row if isinstance(row, list) else [])]
        lines.append("\t".join(cells))
    return "\n".join(lines)


async def list_sheet_tabs_impl(token: str, user_key: str = "") -> dict[str, Any]:
    """List a spreadsheet's worksheets (sheet_id + title + size).

    Ranges are addressed as ``"<SHEET_ID>!A1:B2"``, and a SHEET_ID is not derivable
    from the spreadsheet URL — so anything that reads or writes a range generically
    needs this first.
    """
    if not token.strip():
        return _error("token (spreadsheet_token) is required.")
    res = await _invoke(_build_sheet_meta_request(token.strip()), user_key=user_key)
    if not res["ok"]:
        return res
    raw = res["data"].get("sheets", []) if isinstance(res["data"], dict) else []
    sheets: list[dict[str, Any]] = []
    for sh in raw if isinstance(raw, list) else []:
        if not isinstance(sh, dict):
            continue
        grid = sh.get("grid_properties") or {}
        sheets.append(
            {
                "sheet_id": sh.get("sheet_id") or sh.get("sheetId") or "",
                "title": sh.get("title", ""),
                "index": sh.get("index"),
                "row_count": grid.get("row_count") if isinstance(grid, dict) else None,
                "column_count": grid.get("column_count") if isinstance(grid, dict) else None,
            }
        )
    return {"ok": True, "token": token.strip(), "sheets": sheets, "count": len(sheets)}


def _flatten_sheet_cell(cell: Any) -> str:
    """Flatten one Feishu sheet cell into plain text.

    A cell is not always a scalar: mention cells (``@somebody``) arrive as a dict
    with ``type="mention"``, and styled cells arrive as a list of run segments
    (``{"type": "text", "text": ..., "segmentStyle": ...}``). Reading the "人名"
    or "mentor" column of a todo board therefore needs this flattening, otherwise
    the name is buried in JSON.
    """
    if cell is None:
        return ""
    if isinstance(cell, bool):
        return "TRUE" if cell else "FALSE"
    if isinstance(cell, str):
        return cell
    if isinstance(cell, (int, float)):
        return str(cell)
    if isinstance(cell, list):
        return "".join(_flatten_sheet_cell(part) for part in cell)
    if isinstance(cell, dict):
        for key in ("text", "name", "en_name", "link"):
            val = cell.get(key)
            if isinstance(val, str) and val:
                return val
        return ""
    return str(cell)


async def read_sheet_range_impl(token: str, range_: str, max_chars: int = 20000, user_key: str = "") -> dict[str, Any]:
    """Read one explicit range of a spreadsheet as a grid of plain-text cells.

    Complements ``read_doc_impl(file_type="sheet")``, which dumps *every* sheet
    whole. Reading an explicit range is what lets a caller (a) locate a person's
    row by scanning just the name column and (b) check whether one target cell is
    already occupied before overwriting it.
    """
    if not token.strip():
        return _error("token (spreadsheet_token) is required.")
    if not range_.strip():
        return _error("range is required, e.g. 'SHEET_ID!A1:H30' or just 'SHEET_ID'.")
    res = await _invoke(_build_sheet_values_request(token.strip(), range_.strip()), user_key=user_key)
    if not res["ok"]:
        return res
    value_range = res["data"].get("valueRange", {}) if isinstance(res["data"], dict) else {}
    raw_rows = value_range.get("values") or []
    rows: list[list[str]] = []
    truncated = False
    budget = max_chars
    for raw_row in raw_rows if isinstance(raw_rows, list) else []:
        cells = [_flatten_sheet_cell(c) for c in (raw_row if isinstance(raw_row, list) else [])]
        if max_chars > 0:
            spent = sum(len(c) for c in cells)
            if spent > budget:
                truncated = True
                break
            budget -= spent
        rows.append(cells)
    return {
        "ok": True,
        "token": token.strip(),
        "range": value_range.get("range", range_.strip()),
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }


async def _read_sheet(token: str) -> dict[str, Any]:
    meta = await _invoke(_build_sheet_meta_request(token))
    if not meta["ok"]:
        return meta
    sheets = meta["data"].get("sheets", [])
    parts: list[str] = []
    for sh in sheets if isinstance(sheets, list) else []:
        sheet_id = sh.get("sheet_id") or sh.get("sheetId")
        title = sh.get("title", "")
        if not sheet_id:
            continue
        values = await _invoke(_build_sheet_values_request(token, str(sheet_id)))
        if not values["ok"]:
            return values
        parts.append(f"# {title}\n{_sheet_values_to_text(values['data'])}")
    return {"ok": True, "content": "\n\n".join(parts)}


# ── Sheet writes — put/append values (incl. formulas) + set cell style ─────────
# Feishu Sheets v2 write APIs. A cell value that is a string starting with "="
# (e.g. "=SUM(A1:A2)") is stored by Feishu as a formula, so callers can write
# formulas simply by passing such strings. Ranges use the "<sheetId>!<A1:B2>"
# form; a bare "<sheetId>" targets the used range. Values may be str/int/float/
# bool/None (None = blank cell). See feishu_sheet.py for the user-facing tools.

# Feishu single-write cap: 5000 rows x 100 cols. We surface a clear error rather
# than letting the API reject a too-large payload with an opaque code.
_SHEET_MAX_ROWS = 5000
_SHEET_MAX_COLS = 100

# JSON-serialisable cell scalars Feishu accepts in a values grid.
_SHEET_CELL_TYPES = (str, int, float, bool)


def _validate_sheet_values(values: Any) -> str | None:
    """Return an error message if ``values`` isn't a valid grid, else None."""
    if not isinstance(values, list) or not values:
        return "values must be a non-empty list of rows (list of lists)."
    if not all(isinstance(row, list) for row in values):
        return "values must be a list of lists — each row is a list of cells."
    if len(values) > _SHEET_MAX_ROWS:
        return f"too many rows ({len(values)} > {_SHEET_MAX_ROWS} per write)."
    for row in values:
        if len(row) > _SHEET_MAX_COLS:
            return f"too many columns ({len(row)} > {_SHEET_MAX_COLS} per write)."
        for cell in row:
            if cell is not None and not isinstance(cell, _SHEET_CELL_TYPES):
                return f"unsupported cell value {cell!r} — use string/number/bool/null."
    return None


def _build_sheet_write_request(spreadsheet_token: str, range_: str, values: list[list[Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"valueRange": {"range": range_, "values": values}}
    return req


def _build_sheet_append_request(
    spreadsheet_token: str, range_: str, values: list[list[Any]], insert_data_option: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/values_append"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.add_query("insertDataOption", insert_data_option)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"valueRange": {"range": range_, "values": values}}
    return req


def _build_sheet_style_request(spreadsheet_token: str, range_: str, style: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/sheets/v2/spreadsheets/:spreadsheet_token/style"
    req.paths["spreadsheet_token"] = spreadsheet_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"appendStyle": {"range": range_, "style": style}}
    return req


def _sheet_result(res: dict[str, Any]) -> dict[str, Any]:
    """Normalise a Feishu sheet write response into the tool's success shape."""
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "spreadsheet_token": data.get("spreadsheetToken", ""),
        "updated_range": data.get("updatedRange") or data.get("tableRange", ""),
        "updated_rows": data.get("updatedRows"),
        "updated_columns": data.get("updatedColumns"),
        "updated_cells": data.get("updatedCells"),
        "revision": data.get("revision"),
    }


def _parse_values_json(values_json: str) -> tuple[list[list[Any]] | None, str | None]:
    """Parse a JSON grid string; return (values, error_message)."""
    try:
        values = json.loads(values_json)
    except ValueError as exc:
        return None, f"values_json is not valid JSON: {exc}"
    err = _validate_sheet_values(values)
    if err:
        return None, err
    return values, None


async def write_sheet_impl(
    token: str,
    range_: str,
    values_json: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Overwrite the given range of a spreadsheet with a grid of values/formulas."""
    if not token.strip():
        return _error("token (spreadsheet_token) is required.")
    if not range_.strip():
        return _error("range is required, e.g. 'SHEET_ID!A1:C3' or just 'SHEET_ID'.")
    values, err = _parse_values_json(values_json)
    if err or values is None:
        return _error(err or "values_json produced no rows.")
    res = await _invoke(
        _build_sheet_write_request(token.strip(), range_.strip(), values),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    return _sheet_result(res)


async def append_sheet_impl(
    token: str,
    range_: str,
    values_json: str,
    insert_data_option: str = "OVERWRITE",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Append rows after the last used row of the given range."""
    if not token.strip():
        return _error("token (spreadsheet_token) is required.")
    if not range_.strip():
        return _error("range is required, e.g. 'SHEET_ID!A1:C3' or just 'SHEET_ID'.")
    option = insert_data_option.strip().upper() or "OVERWRITE"
    if option not in ("OVERWRITE", "INSERT_ROWS"):
        return _error("insert_data_option must be 'OVERWRITE' or 'INSERT_ROWS'.")
    values, err = _parse_values_json(values_json)
    if err or values is None:
        return _error(err or "values_json produced no rows.")
    res = await _invoke(
        _build_sheet_append_request(token.strip(), range_.strip(), values, option),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    return _sheet_result(res)


async def format_sheet_impl(
    token: str,
    range_: str,
    style_json: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Apply a cell style (font/color/border/alignment/number-format) to a range."""
    if not token.strip():
        return _error("token (spreadsheet_token) is required.")
    if not range_.strip():
        return _error("range is required, e.g. 'SHEET_ID!A1:C3'.")
    try:
        style = json.loads(style_json)
    except ValueError as exc:
        return _error(f"style_json is not valid JSON: {exc}")
    if not isinstance(style, dict) or not style:
        return _error("style_json must be a non-empty JSON object of style fields.")
    res = await _invoke(
        _build_sheet_style_request(token.strip(), range_.strip(), style),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    return _sheet_result(res)


async def read_doc_impl(file_type: str, token: str, max_chars: int) -> dict[str, Any]:
    ft = file_type.strip().lower()
    if ft == "docx":
        res = await _invoke(_build_docx_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "doc":
        res = await _invoke(_build_doc_raw_request(token))
        content = res["data"].get("content", "") if res["ok"] else ""
    elif ft == "sheet":
        res = await _read_sheet(token)
        content = res.get("content", "") if res["ok"] else ""
    else:
        return _error(f"Unsupported file_type {file_type!r}. Use one of: docx, doc, sheet.")

    if not res["ok"]:
        return res

    truncated = False
    if max_chars > 0 and len(content) > max_chars:
        content = content[:max_chars]
        truncated = True
    return {
        "ok": True,
        "file_type": ft,
        "token": token,
        "content": content,
        "truncated": truncated,
    }


# ── IM (messaging) — find chat, send, reply-in-thread, list messages ──────────
#
# These power the "daily todo topic" schedules: find the main group by name,
# post a topic root message, reply-in-thread to form a native Feishu thread, and
# read the thread's replies. All use bot/tenant credentials (no user token).


def _build_chat_search_request(query: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/chats/search"
    req.add_query("query", query)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def find_chat_impl(name: str, exact: bool, page_size: int = 50, page_token: str = "") -> dict[str, Any]:
    """Search groups the bot is in by name. Returns candidates [{chat_id, name, description}]."""
    res = await _invoke(_build_chat_search_request(name, page_size, page_token))
    if not res["ok"]:
        return res
    items = res["data"].get("items", []) if isinstance(res["data"], dict) else []
    matches = [
        {"chat_id": it.get("chat_id", ""), "name": it.get("name", ""), "description": it.get("description", "")}
        for it in (items if isinstance(items, list) else [])
    ]
    if exact:
        matches = [m for m in matches if m["name"] == name]
    return {
        "ok": True,
        "query": name,
        "exact": exact,
        "matches": matches,
        "count": len(matches),
        "has_more": bool(res["data"].get("has_more")) if isinstance(res["data"], dict) else False,
        "page_token": res["data"].get("page_token", "") if isinstance(res["data"], dict) else "",
    }


def _infer_receive_id_type(receive_id: str, given: str) -> str:
    """Infer the Feishu ``receive_id_type`` from the id's prefix.

    The API rejects a mismatched type with ``230001 invalid receive_id`` (e.g.
    sending a DM by passing an ``ou_`` open_id while the type is still the default
    ``chat_id``). The id prefix is an unambiguous signal, so trust it: ``oc_`` is a
    chat_id, ``ou_`` an open_id, ``on_`` a union_id, and a value containing ``@`` an
    email. Only fall back to *given* when the prefix carries no signal (e.g. a bare
    user_id), so an explicit caller choice for those still wins.
    """
    rid = receive_id.strip()
    if rid.startswith("oc_"):
        return "chat_id"
    if rid.startswith("ou_"):
        return "open_id"
    if rid.startswith("on_"):
        return "union_id"
    if "@" in rid:
        return "email"
    return given


def _build_send_message_request(receive_id: str, receive_id_type: str, msg_type: str, content: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/messages"
    req.add_query("receive_id_type", receive_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {
        "receive_id": receive_id,
        "msg_type": msg_type,
        "content": content,
    }
    return req


async def _resolve_sender_name(open_id: str) -> str:
    """把发起人 open_id 解析成真实姓名, 供「代人带话」前缀用。

    复用 ``get_users_batch_impl`` 取 ``name``; 查名失败 / 取空 / 非 open_id 一律
    回退成传入值本身——绝不因查名失败而让消息发不出去 (转达失败比署名不全更糟)。
    """
    open_id = (open_id or "").strip()
    if not open_id:
        return ""
    try:
        res = await get_users_batch_impl(open_id, user_id_type="open_id")
    except Exception:
        return open_id
    if not res.get("ok"):
        return open_id
    users = res.get("users") or []
    if users and isinstance(users[0], dict):
        name = (users[0].get("name") or "").strip()
        if name:
            return name
    return open_id


_AT_TAG_RE = re.compile(
    r"(?:<|&lt;)\s*at\b[^>]*?user_id\s*=\s*[\"']?(?P<uid>[^\"'>&\s]+)[\"']?[^>]*?(?:>|&gt;)"
    r"(?:\s*(?:<|&lt;)\s*/\s*at\s*(?:>|&gt;))?",
    re.IGNORECASE,
)


def _extract_and_strip_at_tags(text: str) -> tuple[str, list[str]]:
    """Pull ``<at user_id=ou_xxx>`` tags (also HTML-escaped ``&lt;at&gt;``) out of *text*.

    Returns the text with those tags removed and the list of mentioned open_ids.
    A plain-text message's ``<at>`` does NOT render for bots (Feishu shows the raw
    tag, e.g. ``&lt;at&gt;``), so the caller must resend as a ``post`` message whose
    ``at`` element renders. Extracting here means the model can write the tag inline
    (as the tool docs historically told it to) and mentions still work.
    """
    open_ids = [m.group("uid") for m in _AT_TAG_RE.finditer(text)]
    stripped = _AT_TAG_RE.sub("", text).strip()
    return stripped, open_ids


async def send_message_impl(receive_id: str, text: str, receive_id_type: str, on_behalf_of: str = "") -> dict[str, Any]:
    """Send a text message to a chat/user. Returns message_id + thread_id (thread_id is the topic root).

    When ``on_behalf_of`` (发起人的 open_id) is given, the bot is relaying someone
    else's words, so the text is wrapped with a "{姓名}给你发了一条消息" attribution
    prefix — the recipient sees who it is from instead of a bare bubble authored by
    the bot. Name is resolved from the open_id; falls back to the raw open_id if
    unresolvable.

    ``receive_id_type`` is auto-corrected from the id prefix, and any ``<at>`` tags
    embedded in *text* are turned into a real ``post`` mention (a plain-text ``<at>``
    would render as a raw tag for bots), so mentions work regardless of id type or
    how the tag was written.

    Relay guard: relaying someone's words (``on_behalf_of`` set) is a private message
    to a person, never a group post. If ``receive_id`` is a group chat (``oc_``), the
    send is redirected to the mentioned person's DM (open_id taken from the ``<at>``
    tag in *text*). If no recipient open_id can be determined, it returns an error
    instead of leaking the private message into the group.
    """
    at_target_ids = [m.group("uid") for m in _AT_TAG_RE.finditer(text)]
    if on_behalf_of.strip() and receive_id.strip().startswith("oc_"):
        # A relay must stay private: never post it into the group. Redirect to the
        # mentioned person's DM; refuse (don't fall back to the group) if unknown.
        target = next((oid for oid in at_target_ids if oid.startswith("ou_")), "")
        if not target:
            return _error(
                "代人带话必须私发给本人, 但未能从消息里确定收件人 open_id; "
                "请用 feishu_chat_find_member 查到本人 open_id 后作为 receive_id 私发, 不要发到群里。",
                code="relay_recipient_unknown",
            )
        receive_id, receive_id_type = target, "open_id"

    if on_behalf_of.strip():
        sender = await _resolve_sender_name(on_behalf_of)
        if sender:
            text = f"{sender}给你发了一条消息：「{text}」"  # noqa: RUF001
    receive_id_type = _infer_receive_id_type(receive_id, receive_id_type)
    stripped, at_open_ids = _extract_and_strip_at_tags(text)
    # In a 1:1 DM an @-mention is noise; keep mentions only when sending to a group.
    if at_open_ids and receive_id_type == "chat_id":
        # Mentions only render in a post message; a plain-text <at> shows the raw tag.
        content = _build_post_at_content(stripped, at_open_ids, at_all=False)
        req = _build_send_message_request(receive_id, receive_id_type, "post", content)
    else:
        content = json.dumps({"text": stripped if at_open_ids else text}, ensure_ascii=False)
        req = _build_send_message_request(receive_id, receive_id_type, "text", content)
    res = await _invoke(req)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", ""),
    }


async def send_card_impl(
    receive_id: str,
    card_json: str,
    receive_id_type: str,
    user_key: str | None = None,
    business_context_json: str = "{}",
    action_handlers_json: str = "{}",
) -> dict[str, Any]:
    """Send an interactive card (``msg_type=interactive``) — buttons/forms/selectors etc.

    ``card_json`` is the full card object as a JSON string (Feishu 卡片 JSON, either the
    card 2.0 ``{"schema":"2.0","body":{"elements":[...]}}`` form or the legacy
    ``{"config":...,"elements":[...]}`` form). It is parsed, validated to be a JSON
    object, and posted verbatim as the message ``content`` — so any element the Feishu
    card spec supports (button / form / input / select_static / date_picker / …) works.

    ``receive_id_type`` is auto-corrected from the id prefix, same as ``send_message_impl``.
    Returns ``message_id`` + ``thread_id`` (thread_id is the topic root if in a thread).
    """
    if not isinstance(card_json, str):
        return _error("card_json must be a JSON string containing an object")
    try:
        card = json.loads(card_json)
    except ValueError as exc:
        return _error(f"card_json is not valid JSON: {exc}")
    if not isinstance(card, dict):
        return _error(
            "card_json must be a JSON object — the Feishu card, e.g. "
            '{"schema":"2.0","body":{"elements":[...]}} or {"config":...,"elements":[...]}.'
        )
    if not isinstance(business_context_json, str):
        return _error("business_context_json must be a JSON string containing an object")
    try:
        business_context = json.loads(business_context_json)
    except ValueError as exc:
        return _error(f"business_context_json is not valid JSON: {exc}")
    if not isinstance(business_context, dict):
        return _error("business_context_json must be a JSON object")
    if not isinstance(action_handlers_json, str):
        return _error("action_handlers_json must be a JSON string containing an object")
    try:
        raw_action_handlers = json.loads(action_handlers_json)
    except ValueError as exc:
        return _error(f"action_handlers_json is not valid JSON: {exc}")
    if not isinstance(raw_action_handlers, dict):
        return _error("action_handlers_json must be a JSON object")
    if not all(
        isinstance(action_id, str)
        and bool(action_id)
        and action_id.strip() == action_id
        and isinstance(handler, str)
        and bool(handler)
        and handler.strip() == handler
        for action_id, handler in raw_action_handlers.items()
    ):
        return _error("action_handlers_json keys and values must be non-empty strings without surrounding whitespace")
    action_handlers = dict(raw_action_handlers)
    receive_id_type = _infer_receive_id_type(receive_id, receive_id_type)
    content = json.dumps(card, ensure_ascii=False)
    req = _build_send_message_request(receive_id, receive_id_type, "interactive", content)
    res = await _invoke(req, user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    message_id = data.get("message_id", "")
    if isinstance(message_id, str) and message_id:
        try:
            source = {
                "session_id": get_session_id().strip(),
                "sender_open_id": (user_key or "").strip(),
                "receive_id": receive_id,
                "receive_id_type": receive_id_type,
            }
            await save_card_snapshot(
                message_id,
                card,
                source=source,
                business_context=business_context,
                action_handlers=action_handlers,
            )
        except Exception as exc:
            logger.warning(f"failed to save Feishu card snapshot for {message_id} — {exc!r}")
            return _error(
                "Feishu card was sent, but its callback context could not be saved; card actions will fail closed.",
                sent=True,
                callback_context_saved=False,
                message_id=message_id,
                thread_id=data.get("thread_id", ""),
                chat_id=data.get("chat_id", ""),
            )
    return {
        "ok": True,
        "callback_context_saved": bool(message_id),
        "message_id": message_id,
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", ""),
    }


def _build_reply_message_request(message_id: str, text: str, reply_in_thread: bool) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/messages/:message_id/reply"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {
        "content": json.dumps({"text": text}, ensure_ascii=False),
        "msg_type": "text",
        "reply_in_thread": reply_in_thread,
    }
    return req


async def reply_message_impl(message_id: str, text: str, reply_in_thread: bool) -> dict[str, Any]:
    """Reply to a message. reply_in_thread=True forms/continues a native Feishu thread (topic)."""
    res = await _invoke(_build_reply_message_request(message_id, text, reply_in_thread))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
    }


# ── Recall (unsend) a message ─────────────────────────────────────────────────
#
# DELETE /open-apis/im/v1/messages/:message_id removes a message from everyone's
# view. The bot can always recall what the bot itself sent (tenant token); recalling
# *someone else's* message additionally requires acting as a group owner/admin, i.e.
# that person's UAT — hence the tenant-first, UAT-on-permission-failure strategy.
# Messages sent through the batch-send API need the separate batch-recall endpoint.

_RECALL_ERROR_HINTS = {
    230002: "机器人不在该群里, 先把机器人加入群再撤回。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230009: "该消息已超出可撤回时限 (受企业管理员的撤回时限设置约束)。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230026: "只能撤回机器人自己发的消息; 撤回别人的消息需以群主/管理员身份操作 (传该管理员的 user_key 并完成授权)。",
    230027: "缺少撤回所需权限 (im:message 或 im:message:recall), 外部群还需开启对外共享。",
    230050: "该消息对当前操作身份不可见, 无法撤回。",
    230054: "该消息类型不支持撤回。",
    230110: "该消息已被撤回或删除, 无需再撤回。",
    232009: "群组已解散, 无法撤回。",
}


def _build_recall_message_request(message_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/im/v1/messages/:message_id"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def recall_message_impl(message_id: str, user_key: str = "") -> dict[str, Any]:
    """Recall (unsend) a message so it disappears for everyone in the chat.

    ``message_id`` must be a message id (``om_...``) — a chat_id/open_id is the
    common mix-up and is rejected up front rather than spending a request on
    ``230001 invalid param``.

    Feishu returns an empty ``data`` on success, so success is reported explicitly.
    Failures keep the raw ``code``/``msg`` and gain a ``hint`` naming the actual
    blocker (not the bot's own message, past the recall window, already recalled…),
    because those are indistinguishable from a bare "Feishu API error 2300xx".
    """
    mid = message_id.strip()
    if not mid:
        return _error("message_id is required (the om_... id of the message to recall).")
    if not mid.startswith("om_"):
        return _error(
            f"message_id must be a message id starting with 'om_', got {mid!r}. "
            "chat_id (oc_...) / open_id (ou_...) 不是消息 id; "
            "消息 id 来自 feishu_message_send 的返回、<feishu_context>, 或 feishu_message_list。",
        )
    res = await _invoke(_build_recall_message_request(mid), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        hint = _RECALL_ERROR_HINTS.get(res.get("code"))
        return {**res, "hint": hint} if hint else res
    return {"ok": True, "message_id": mid, "recalled": True}


def _build_list_messages_request(
    container_id: str, container_id_type: str, sort_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages"
    req.add_query("container_id_type", container_id_type)
    req.add_query("container_id", container_id)
    req.add_query("sort_type", sort_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_messages_impl(
    container_id: str,
    container_id_type: str,
    sort_type: str,
    page_size: int,
    page_token: str,
) -> dict[str, Any]:
    """List messages in a chat or thread. Use container_id_type='thread' + a thread_id to read a topic's replies."""
    res = await _invoke(_build_list_messages_request(container_id, container_id_type, sort_type, page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "items": data.get("items", []),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


def _extract_post_text(node: Any) -> str:
    """Recursively collect all 'text' values from a post rich-text content tree."""
    parts: list[str] = []
    if isinstance(node, dict):
        if node.get("tag") == "text" and isinstance(node.get("text"), str):
            parts.append(node["text"])
        for v in node.values():
            if isinstance(v, (dict, list)):
                parts.append(_extract_post_text(v))
    elif isinstance(node, list):
        for v in node:
            parts.append(_extract_post_text(v))
    return " ".join(p for p in parts if p)


def _message_plain_text(item: dict[str, Any]) -> str:
    """Best-effort plain text of a message item (handles text and post; others -> '')."""
    if item.get("deleted"):
        return ""
    body = item.get("body", {}) if isinstance(item.get("body"), dict) else {}
    raw = body.get("content", "")
    if not raw:
        return ""
    try:
        content = json.loads(raw)
    except ValueError, TypeError:
        return raw if isinstance(raw, str) else ""
    if not isinstance(content, dict):
        return ""
    if "text" in content and isinstance(content["text"], str):
        return content["text"]
    return _extract_post_text(content)  # post / rich text


async def read_thread_impl(thread_id: str, page_size: int = 50) -> dict[str, Any]:
    """Read a topic thread and return cleaned messages: [{message_id, sender_open_id, name?, text}]."""
    messages: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _invoke(_build_list_messages_request(thread_id, "thread", "ByCreateTimeAsc", page_size, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            sender = it.get("sender", {}) if isinstance(it.get("sender"), dict) else {}
            is_user = sender.get("sender_type") == "user"
            messages.append(
                {
                    "message_id": it.get("message_id", ""),
                    "sender_open_id": sender.get("id", "") if is_user else "",
                    "sender_type": sender.get("sender_type", ""),
                    "create_time": it.get("create_time", ""),
                    "text": _message_plain_text(it),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return {"ok": True, "thread_id": thread_id, "messages": messages, "count": len(messages)}


# ── Contact — resolve a member's user id (open_id) by name via chat roster ────
#
# Feishu tenant tokens cannot search all users by name; the supported path is to
# list a group's members (each item has name + member_id) and match by name.
# This resolves the "@ a specific person" need — the target is a group member.


def _build_chat_members_request(chat_id: str, member_id_type: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/chats/:chat_id/members"
    req.paths["chat_id"] = chat_id
    req.add_query("member_id_type", member_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def find_member_id_impl(
    chat_id: str,
    name: str,
    exact: bool,
    member_id_type: str = "open_id",
) -> dict[str, Any]:
    """Resolve a group member's id by name. Pages through the full roster and matches by name.

    Returns matches [{name, id, member_id_type}]. ``name`` empty returns the whole roster.
    """
    members: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _invoke(_build_chat_members_request(chat_id, member_id_type, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            members.append(
                {
                    "name": it.get("name", ""),
                    "id": it.get("member_id", ""),
                    "member_id_type": it.get("member_id_type", member_id_type),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break

    if not name:
        matches = members
    elif exact:
        matches = [m for m in members if m["name"] == name]
    else:
        matches = [m for m in members if name in m["name"]]
    return {
        "ok": True,
        "chat_id": chat_id,
        "query": name,
        "exact": exact,
        "matches": matches,
        "count": len(matches),
        "member_total": len(members),
    }


async def list_chat_members_impl(
    chat_id: str,
    member_id_type: str = "open_id",
) -> dict[str, Any]:
    """List every member of a group. Pages through the full roster automatically.

    Unlike ``find_member_id_impl`` (which matches by name), this returns the whole
    roster in one call. Returns members [{name, id, member_id_type}].
    """
    members: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _invoke(_build_chat_members_request(chat_id, member_id_type, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            members.append(
                {
                    "name": it.get("name", ""),
                    "id": it.get("member_id", ""),
                    "member_id_type": it.get("member_id_type", member_id_type),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break

    return {
        "ok": True,
        "chat_id": chat_id,
        "members": members,
        "count": len(members),
    }


def _build_create_chat_request(
    name: str,
    description: str,
    user_id_list: list[str],
    owner_id: str,
    user_id_type: str,
    set_bot_manager: bool,
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/chats"
    req.add_query("user_id_type", user_id_type)
    if set_bot_manager:
        req.add_query("set_bot_manager", "true")
    body: dict[str, Any] = {"name": name}
    if description:
        body["description"] = description
    if user_id_list:
        body["user_id_list"] = user_id_list
    if owner_id:
        body["owner_id"] = owner_id
    req.body = body
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def create_chat_impl(
    name: str,
    user_ids: list[str] | None = None,
    description: str = "",
    owner_id: str = "",
    user_id_type: str = "open_id",
) -> dict[str, Any]:
    """Create a new group chat and pull the given people in. Returns the new chat_id.

    Created with the bot's tenant token. ``owner_id`` should be the **requester**
    (the person who asked for the group — their ``sender_open_id``): the group is
    handed to them, and the bot stays on as an admin (``set_bot_manager``) so it can
    still post with ``feishu_message_send``. When ``owner_id`` is empty the bot itself
    owns the group (fallback for bot-authored groups with no human requester).
    ``user_ids`` are the members to invite (max 50, resolve names via
    ``feishu_chat_find_member`` / ``feishu_department_members``).
    """
    if not name.strip():
        return _error("name is required to create a group chat.")
    ids = [u.strip() for u in (user_ids or []) if u and u.strip()]
    if len(ids) > 50:
        return _error("Feishu allows at most 50 members per create-chat call; invite the rest afterwards.")
    # Hand the group to the requester (owner_id) but keep the bot as an admin so it
    # can still post afterwards; only when no owner is given does the bot own it.
    set_bot_manager = bool(owner_id.strip())
    req = _build_create_chat_request(
        name.strip(), description.strip(), ids, owner_id.strip(), user_id_type, set_bot_manager
    )
    res = await _invoke(req)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "chat_id": data.get("chat_id", ""),
        "name": data.get("name", name),
        "invited": ids,
        "invited_count": len(ids),
        "invalid_user_ids": data.get("invalid_user_id_list") or [],
        "owner_id": data.get("owner_id", ""),
    }


# ── Approval (审批) — list pending tasks, read instance, approve/reject ────────
#
# Lets the agent read an approval application's form content and decide whether
# to approve or reject it. Feishu requires approve/reject to carry the APPROVER's
# own user_id — the bot acts on behalf of a real approver (the action is recorded
# under that person). All endpoints work with bot/tenant credentials.

_APPROVAL_TASK_STATUS = {1: "待办", 2: "已办", 17: "未读", 18: "已读", 33: "处理中", 34: "撤回"}
_APPROVAL_INSTANCE_STATUS = {0: "none", 1: "running", 2: "approved", 3: "rejected", 4: "revoked", 5: "terminated"}


def _build_task_query_request(
    user_id: str, topic: str, user_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/approval/v4/tasks/query"
    req.add_query("user_id", user_id)
    req.add_query("topic", topic)
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_approval_tasks_impl(
    user_id: str,
    topic: str = "1",
    user_id_type: str = "open_id",
    page_size: int = 100,
    page_token: str = "",
) -> dict[str, Any]:
    """List a user's approval tasks. topic '1' = pending (待办). Returns task summaries + pagination."""
    res = await _invoke(_build_task_query_request(user_id, topic, user_id_type, page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    tasks = [
        {
            "task_id": t.get("task_id", ""),
            "instance_code": t.get("process_id", ""),
            "approval_code": t.get("definition_code", "") or t.get("process_code", ""),
            "title": t.get("title", ""),
            "status": _APPROVAL_TASK_STATUS.get(t.get("status"), t.get("status")),
            "process_status": _APPROVAL_INSTANCE_STATUS.get(t.get("process_status"), t.get("process_status")),
            "initiators": t.get("initiator_names", []),
        }
        for t in (data.get("tasks", []) if isinstance(data.get("tasks"), list) else [])
    ]
    return {
        "ok": True,
        "tasks": tasks,
        "count": len(tasks),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


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
    res = await _invoke(_build_instance_get_request(instance_id, user_id_type))
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


def _build_list_instances_request(
    approval_code: str, start_time: str, end_time: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/approval/v4/instances"
    req.add_query("approval_code", approval_code)
    if start_time:
        req.add_query("start_time", start_time)
    if end_time:
        req.add_query("end_time", end_time)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_approval_instances_impl(approval_code: str, start_time: str = "", end_time: str = "") -> dict[str, Any]:
    """List all instance codes for an approval definition in a time window (Unix ms strings).

    Defaults to the last 30 days when start/end omitted. Pages through everything and
    returns ``instance_codes`` to feed into ``get_approval_instance_impl`` one by one.
    """
    if not approval_code:
        return _error("approval_code is required (the approval definition code).")
    if not start_time or not end_time:
        import time  # noqa: PLC0415

        now_ms = int(time.time() * 1000)
        end_time = end_time or str(now_ms)
        start_time = start_time or str(now_ms - 30 * 24 * 3600 * 1000)
    codes: list[str] = []
    page_token = ""
    while True:
        res = await _invoke(_build_list_instances_request(approval_code, start_time, end_time, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        chunk = data.get("instance_code_list", [])
        if isinstance(chunk, list):
            codes.extend(str(c) for c in chunk)
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return {
        "ok": True,
        "approval_code": approval_code,
        "start_time": start_time,
        "end_time": end_time,
        "instance_codes": codes,
        "count": len(codes),
    }


def _build_task_action_request(
    action: str, approval_code: str, instance_code: str, user_id: str, task_id: str, comment: str, user_id_type: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = f"/open-apis/approval/v4/tasks/{action}"
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {
        "approval_code": approval_code,
        "instance_code": instance_code,
        "user_id": user_id,
        "task_id": task_id,
    }
    if comment:
        body["comment"] = comment
    req.body = body
    return req


async def decide_approval_task_impl(
    approve: bool,
    approval_code: str,
    instance_code: str,
    approver_user_id: str,
    task_id: str,
    comment: str = "",
    user_id_type: str = "open_id",
) -> dict[str, Any]:
    """Approve or reject a task on behalf of ``approver_user_id``. approve=True -> approve, else reject."""
    action = "approve" if approve else "reject"
    res = await _invoke(
        _build_task_action_request(
            action, approval_code, instance_code, approver_user_id, task_id, comment, user_id_type
        )
    )
    if not res["ok"]:
        return res
    return {"ok": True, "action": action, "instance_code": instance_code, "task_id": task_id}


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
        return _error("approval_code is required (the approval definition code).")
    res = await _invoke(_build_approval_definition_request(approval_code, user_id_type, with_admin_id))
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
        return _error("approval_code is required (the approval definition code).")
    if not applicant_open_id and not applicant_user_id:
        return _error(
            "an applicant id is required — pass applicant_open_id (the sender's open_id) or applicant_user_id."
        )
    try:
        form = json.loads(form_json)
    except ValueError as exc:
        return _error(f"form_json is not valid JSON: {exc}")
    if not isinstance(form, list):
        return _error("form_json must be a JSON array of {id, type, value} widget objects.")
    node_approvers: list[dict[str, Any]] | None = None
    if node_approver_open_id_list_json.strip():
        try:
            node_approvers = json.loads(node_approver_open_id_list_json)
        except ValueError as exc:
            return _error(f"node_approver_open_id_list_json is not valid JSON: {exc}")
        if not isinstance(node_approvers, list):
            return _error("node_approver_open_id_list_json must be a JSON array of {key, value} objects.")
    res = await _invoke(
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


# ── Approval event subscription — enable push (no polling) ────────────────────
#
# Subscribing an approval definition makes Feishu push an ``approval_instance``
# event over the app's event channel (the same WebSocket the bot already runs)
# every time an instance of that definition changes status. The channel layer
# turns those events into a proactive DM to the applicant — so status changes are
# pushed, never polled. Subscribe is idempotent per app: one call per approval
# definition is enough (repeat calls are a no-op on Feishu's side).


def _build_approval_subscribe_request(approval_code: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/approval/v4/approvals/:approval_code/subscribe"
    req.paths["approval_code"] = approval_code
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_approval_unsubscribe_request(approval_code: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/approval/v4/approvals/:approval_code/unsubscribe"
    req.paths["approval_code"] = approval_code
    req.token_types = {AccessTokenType.TENANT}
    return req


async def subscribe_approval_impl(approval_code: str) -> dict[str, Any]:
    """Subscribe to an approval definition's instance status-change events.

    After this, Feishu pushes an ``approval_instance`` event whenever any instance
    of ``approval_code`` changes status, and the bot DMs the applicant. Idempotent
    per app — one call per definition is enough. Uses the bot's tenant token.
    """
    if not approval_code:
        return _error("approval_code is required (the approval definition code).")
    res = await _invoke(_build_approval_subscribe_request(approval_code))
    if not res["ok"]:
        return res
    return {"ok": True, "approval_code": approval_code, "subscribed": True}


async def unsubscribe_approval_impl(approval_code: str) -> dict[str, Any]:
    """Cancel a previous subscription so status-change events stop being pushed."""
    if not approval_code:
        return _error("approval_code is required (the approval definition code).")
    res = await _invoke(_build_approval_unsubscribe_request(approval_code))
    if not res["ok"]:
        return res
    return {"ok": True, "approval_code": approval_code, "subscribed": False}


# ── Wiki — resolve a wiki node token to its underlying document ───────────────
#
# A Feishu wiki URL (.../wiki/<node_token>) is a shell; the real content lives in
# an underlying docx/sheet/bitable/... This resolves the node token to obj_token
# + obj_type so the agent can then read it (docx/doc/sheet via read_doc_impl).


async def get_wiki_node_impl(token: str, user_key: str = "") -> dict[str, Any]:
    """Resolve a wiki node token to its underlying document (obj_token + obj_type).

    Pass ``user_key`` to resolve as that user (needed when the wiki is user-owned and
    the bot isn't a member); empty uses the bot's tenant token.
    """
    res = await _invoke_wiki_read(
        _wiki_node.build_wiki_node_get_request(token=token),
        user_key,
        lambda r: not (r.get("data", {}) or {}).get("node"),
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    node = data.get("node", {}) if isinstance(data.get("node"), dict) else {}
    return {
        "ok": True,
        "node_token": node.get("node_token", ""),
        "obj_token": node.get("obj_token", ""),
        "obj_type": node.get("obj_type", ""),
        "title": node.get("title", ""),
        "space_id": node.get("space_id", ""),
        "has_child": bool(node.get("has_child")),
    }


# ── Start a group topic with @-mentions ──────────────────────────────────────
#
# Text messages' <at> tags do NOT render as real mentions for bots (Feishu shows
# the raw tag). Real mentions require the "post" rich-text message type, whose
# `at` element ({"tag":"at","user_id":...}) does render. So when mentions are
# requested we send a post; with no mentions we keep a plain text message.


def _build_post_at_content(text: str, at_open_ids: list[str], at_all: bool) -> str:
    """Build a post rich-text content JSON string: leading @ elements, then the text run."""
    line: list[dict[str, Any]] = []
    if at_all:
        line.append({"tag": "at", "user_id": "all"})
    line.extend({"tag": "at", "user_id": oid} for oid in at_open_ids if oid)
    # separate mentions from the message with a space, then the text
    line.append({"tag": "text", "text": f" {text}" if line else text})
    return json.dumps({"zh_cn": {"title": "", "content": [line]}}, ensure_ascii=False)


async def start_topic_impl(
    chat_id: str,
    text: str,
    at_open_ids: list[str] | None = None,
    at_all: bool = False,
) -> dict[str, Any]:
    """Post a topic root message to a group, @-mentioning the given open_ids (and/or everyone).

    Uses a post rich-text message when mentions are requested (so @ renders), a
    plain text message otherwise. Returns message_id + thread_id (the topic root).
    """
    ids = at_open_ids or []
    if ids or at_all:
        content = _build_post_at_content(text, ids, at_all)
        req = _build_send_message_request(chat_id, "chat_id", "post", content)
    else:
        content = json.dumps({"text": text}, ensure_ascii=False)
        req = _build_send_message_request(chat_id, "chat_id", "text", content)
    res = await _invoke(req)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", "") or chat_id,
    }


# ── Document search (needs user_access_token) ────────────────────────────────
#
# Feishu's doc search (/suite/docs-api/search/object) only accepts a
# user_access_token (UAT), not the bot's tenant token — it returns docs the
# authorizing USER can see. We use the SDK's device-flow OAuth to obtain/refresh
# a UAT, cache it in <workspace>/.psi/feishu/uat.json (plaintext — dev use), and
# call the search endpoint with a hand-built BaseRequest carrying the UAT.

_UAT_USER_KEY = "default"  # fallback key when a caller does not pass user_key
_token_store: Any = None
_uat_client: Any = None

# ── Capability-keyed OAuth scopes ────────────────────────────────────────────
#
# Ask the user for the permissions the task actually needs, instead of one fixed
# blanket set. But scopes can't be free text: a scope Feishu doesn't recognize
# makes it reject the whole authorize page (error 20043), so an LLM inventing
# "docx:write" would break authorization outright rather than degrade. Callers
# therefore name CAPABILITIES from this catalog and the real scope strings stay
# here, where they can be verified against Feishu's console.
#
# Every scope string below is one this project has already used against the live
# Feishu console. Adding a capability means verifying its scope there first —
# guessing a plausible-looking name here is what produces error 20043.
_SCOPE_CATALOG: dict[str, tuple[str, ...]] = {
    "docs_read": ("docs:doc:readonly",),
    "drive_read": ("drive:drive:readonly",),
    # Cloud-drive write: creating/deleting files and writing spreadsheets both go
    # through the drive, so sheet writing needs no separate capability.
    "drive_write": ("drive:drive",),
    "docx_write": ("docx:document",),  # covers both creating and editing docs
    "wiki_write": ("wiki:wiki",),
    "bitable_write": ("bitable:app",),
    "task_write": ("task:task:write",),
    "calendar_write": ("calendar:calendar",),
    "contact_read": ("contact:contact.base:readonly",),
    # Phone/email are separately gated: without these the contact tools still
    # succeed but return those fields empty, so ask for them only when needed.
    "contact_phone_email_read": (
        "contact:contact.base:readonly",
        "contact:user.phone:readonly",
        "contact:user.email:readonly",
    ),
}
# Granted alongside every request so a token can be refreshed instead of
# re-authorized; never itself a capability the caller has to ask for.
_OFFLINE_SCOPE = "offline_access"
# What a caller gets when it names no capabilities: the read-only docs/drive pair
# plus docx/wiki writing — the set this tool granted unconditionally before
# capabilities existed, so an un-updated caller keeps working.
_DEFAULT_CAPABILITIES = ("docs_read", "drive_read", "docx_write", "wiki_write")


def scope_catalog_keys() -> list[str]:
    """The capability keys a caller may ask to be authorized for."""
    return sorted(_SCOPE_CATALOG)


def _parse_capabilities(capabilities: str) -> tuple[list[str], str]:
    """Split a comma/space-separated capability list into (keys, error).

    Unknown keys are refused *before* the authorize URL is built — sending them to
    Feishu would fail the whole page with error 20043, which reads to the user as
    "authorization is broken" rather than "that capability doesn't exist".
    """
    raw = [c.strip() for c in re.split(r"[,\s]+", capabilities or "") if c.strip()]
    if not raw:
        return list(_DEFAULT_CAPABILITIES), ""
    unknown = [c for c in raw if c not in _SCOPE_CATALOG]
    if unknown:
        return [], (
            f"未知的权限能力键: {', '.join(unknown)}. 只能用这些: {', '.join(scope_catalog_keys())}. "
            "(不要直接传飞书原始 scope 串 — 无效 scope 会让整个授权页失败.)"
        )
    # Preserve caller order but drop duplicates.
    return list(dict.fromkeys(raw)), ""


def _scope_string(capabilities: list[str]) -> str:
    """The OAuth ``scope`` value granting ``capabilities`` (plus refresh).

    Capabilities can share a scope (the contact ones both need the base scope), so
    the flattened list is de-duplicated — a repeated scope is not an error, but it
    makes the consent screen list the same permission twice.
    """
    scopes = [s for c in capabilities if c in _SCOPE_CATALOG for s in _SCOPE_CATALOG[c]]
    return " ".join([*dict.fromkeys(scopes), _OFFLINE_SCOPE])


def _norm_user_key(user_key: str = "") -> str:
    """Normalize a per-user UAT key. Empty falls back to the shared 'default'.

    Callers pass the message sender's ``open_id`` (from the injected
    ``<feishu_context>``) so each user's authorization is isolated in the token
    store. Single-user / local dev can leave it empty and share ``default``.
    """
    return user_key.strip() or _UAT_USER_KEY


def _uat_store_path() -> str:
    base = pathlib.Path(_paths.workspace_dir())
    d = base / ".psi" / "feishu"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "uat.json")


def _granted_scopes_path() -> str:
    return str(pathlib.Path(_uat_store_path()).parent / "granted_scopes.json")


def _identity_path() -> str:
    return str(pathlib.Path(_uat_store_path()).parent / "identity.json")


def _read_json_map(path: str) -> dict[str, Any]:
    """Read a ``{user_key: value}`` JSON map; unreadable/corrupt reads as empty.

    A damaged file must not break the tools: losing the record means the user gets
    asked again, which is recoverable, whereas raising here would block every write.
    """
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_map(path: str, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def granted_capabilities(user_key: str = "") -> list[str]:
    """Capabilities ``user_key`` has already authorized, in catalog order.

    Tracked here rather than read back from ``UAT.scopes`` because a token refresh
    response need not echo ``scope`` — trusting the token would make previously
    granted permissions look revoked and re-prompt the user.
    """
    stored = _read_json_map(_granted_scopes_path()).get(_norm_user_key(user_key))
    if not isinstance(stored, list):
        return []
    return [c for c in scope_catalog_keys() if c in stored]


def _record_granted_capabilities(user_key: str, capabilities: list[str]) -> None:
    """Add ``capabilities`` to what ``user_key`` has authorized (union, never shrink)."""
    path = _granted_scopes_path()
    data = _read_json_map(path)
    key = _norm_user_key(user_key)
    stored = data.get(key)
    existing = stored if isinstance(stored, list) else []
    merged = {c for c in [*existing, *capabilities] if c in _SCOPE_CATALOG}
    data[key] = [c for c in scope_catalog_keys() if c in merged]
    with contextlib.suppress(OSError):
        _write_json_map(path, data)


def missing_capabilities(user_key: str, needed: list[str]) -> list[str]:
    """Which of ``needed`` this user has not authorized yet."""
    have = set(granted_capabilities(user_key))
    return [c for c in needed if c in _SCOPE_CATALOG and c not in have]


_IDENTITY_USER = "user"
_IDENTITY_BOT = "bot"
_IDENTITY_CHOICES = (_IDENTITY_USER, _IDENTITY_BOT)


def get_identity(user_key: str = "") -> str:
    """This user's remembered ownership choice (``user``/``bot``), or "" if unasked."""
    stored = _read_json_map(_identity_path()).get(_norm_user_key(user_key))
    return stored if stored in _IDENTITY_CHOICES else ""


def set_identity(user_key: str, identity: str) -> str:
    """Remember this user's ownership choice. Returns "" or an error message."""
    choice = (identity or "").strip().lower()
    if choice not in _IDENTITY_CHOICES:
        return f"identity must be one of {', '.join(_IDENTITY_CHOICES)} (got {identity!r})."
    path = _identity_path()
    data = _read_json_map(path)
    data[_norm_user_key(user_key)] = choice
    with contextlib.suppress(OSError):
        _write_json_map(path, data)
    return ""


def _pending_auth_path(user_key: str = "") -> str:
    """Per-user pending-auth file so concurrent authorizations don't clobber each other."""
    key = _norm_user_key(user_key)
    # Keep filenames filesystem-safe: only allow word chars + dash, replace the
    # rest (incl. path separators and dots, so a crafted open_id can't traverse).
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", key)
    return str(pathlib.Path(_uat_store_path()).parent / f"pending_auth_{safe}.json")


def _get_token_store() -> Any:
    global _token_store
    if _token_store is None:
        from lark_channel.channel.auth.token_store import FileTokenStore  # noqa: PLC0415

        _token_store = FileTokenStore(_uat_store_path())
    return _token_store


def _get_uat_client() -> Any:
    """A client built with enable_set_token(True) so we can attach a UAT per request."""
    global _uat_client
    if _uat_client is not None:
        return _uat_client
    creds = _config()
    if creds is None:
        return None
    from lark_channel.client import Client  # noqa: PLC0415

    app_id, app_secret = creds
    _uat_client = Client.builder().app_id(app_id).app_secret(app_secret).enable_set_token(True).build()
    return _uat_client


def _reset_uat_state() -> None:
    global _token_store, _uat_client
    _token_store = None
    _uat_client = None


# Authorization-code flow endpoints (China/feishu.cn — the device flow's v2
# endpoint 404s here). Browser authorize on accounts.feishu.cn; token exchange
# and refresh on open.feishu.cn/authen/v1.
_AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/access_token"
_REFRESH_URL = "https://open.feishu.cn/open-apis/authen/v1/refresh_access_token"
_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"


def _explicit_redirect_uri() -> str:
    """用户在应用后台登记并显式指定的 redirect_uri; 未设置返回空串。"""
    return os.environ.get("PSI_FEISHU_REDIRECT_URI", "").strip()


def _new_pkce_pair() -> tuple[str, str]:
    """生成 PKCE ``(code_verifier, code_challenge)``, S256。

    verifier 取 64 字符 (飞书要求 43-128, 字符集 ``[A-Za-z0-9-._~]``); challenge 是其
    SHA-256 的 base64url (去 padding)。实测飞书 authorize 接受 ``code_challenge``,
    换 token 时接受 ``code_verifier``, 故整条链路可直接开 PKCE。
    """
    import base64  # noqa: PLC0415
    import hashlib  # noqa: PLC0415

    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode("ascii")
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _extract_code(code_or_url: str) -> str:
    """Accept either a bare code or a full callback URL and return the code."""
    s = code_or_url.strip()
    if "code=" in s:
        from urllib.parse import parse_qs, urlparse  # noqa: PLC0415

        qs = parse_qs(urlparse(s).query)
        if qs.get("code"):
            return qs["code"][0]
    return s


async def _post_json(url: str, body: dict[str, Any], headers: dict[str, str] | None = None) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=body, headers=headers or {})
    with contextlib.suppress(ValueError):
        data = resp.json()
        if isinstance(data, dict):
            return data
    return {"code": resp.status_code, "msg": f"non-JSON response ({resp.status_code})"}


async def _get_app_access_token() -> str | None:
    creds = _config()
    if creds is None:
        return None
    app_id, app_secret = creds
    data = await _post_json(_APP_TOKEN_URL, {"app_id": app_id, "app_secret": app_secret})
    return data.get("app_access_token") if data.get("code") == 0 else None


def _uat_from_token_response(payload: dict[str, Any]) -> Any:
    import time  # noqa: PLC0415

    from lark_channel.channel.types import UAT  # noqa: PLC0415

    now = time.time()
    inner = payload.get("data")
    data: dict[str, Any] = inner if isinstance(inner, dict) else payload
    expires_in = int(data.get("expires_in") or 0)
    refresh_expires_in = int(data.get("refresh_expires_in") or 0)
    scope_str = data.get("scope") or ""
    return UAT(
        access_token=data.get("access_token") or "",
        refresh_token=data.get("refresh_token"),
        expires_at=now + expires_in if expires_in else None,
        refresh_expires_at=now + refresh_expires_in if refresh_expires_in else None,
        scopes=scope_str.split() if scope_str else [],
        open_id=data.get("open_id"),
        raw=data if isinstance(data, dict) else {},
    )


async def auth_start_impl(capabilities: str = "", user_key: str = "") -> dict[str, Any]:
    """Build the browser authorize URL, asking only for the capabilities needed, and
    pick an automatic code-receiving channel.

    授权码流程真正折磨人的是「同意之后还要自己从地址栏复制 code」。这里按环境选一条
    自动接收通道 (Gateway 回调 → 本机回环 → 都不行才手工), 把 ``state`` / PKCE
    verifier / 通道信息一并写进 pending 文件, 供 ``auth_wait_impl`` 与
    ``auth_complete_impl`` 取用。

    The requested scope is the UNION of what this user already granted and what
    ``capabilities`` asks for: Feishu issues a token carrying exactly the scopes of
    the latest grant, so asking for only the new capability would silently revoke
    the ones already working.
    """
    creds = _config()
    if creds is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    requested, err = _parse_capabilities(capabilities)
    if err:
        return _error(err, capability_keys=scope_catalog_keys())
    from urllib.parse import urlencode  # noqa: PLC0415

    app_id, _ = creds
    already = granted_capabilities(user_key)
    union = [c for c in scope_catalog_keys() if c in {*already, *requested}]
    state = os.urandom(24).hex()
    verifier, challenge = _new_pkce_pair()
    plan = _oauth_rx.plan_receiver(_explicit_redirect_uri())
    await anyio.Path(_pending_auth_path(user_key)).write_text(
        json.dumps(
            {
                "state": state,
                "code_verifier": verifier,
                "redirect_uri": plan.redirect_uri,
                "mode": plan.mode,
                "capabilities": union,
            }
        ),
        encoding="utf-8",
    )
    query = urlencode(
        {
            "client_id": app_id,
            "redirect_uri": plan.redirect_uri,
            "response_type": "code",
            "scope": _scope_string(union),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    authorize_url = f"{_AUTHORIZE_URL}?{query}"
    if plan.automatic:
        return {
            "ok": True,
            "authorize_url": authorize_url,
            "auto_receive": True,
            "mode": plan.mode,
            "redirect_uri": plan.redirect_uri,
            "capabilities": union,
            "newly_requested": [c for c in requested if c not in already],
            "already_granted": already,
            "message": (
                f"请把 authorize_url 发给用户 (本次申请的权限: {', '.join(union)}), "
                "让其打开并点「同意授权」-- **不用复制任何 code**, 授权码会自动回流.\n"
                "发完链接后立刻调 feishu_auth_wait (同一个 user_key) 等待用户点完, 它会自己完成授权.\n"
                "授权一次即缓存并自动续期; 只有当以后的任务需要**新的**权限时才会再请你授权一次."
            ),
            "next_step": "feishu_auth_wait",
        }
    return {
        "ok": True,
        "authorize_url": authorize_url,
        "auto_receive": False,
        "mode": plan.mode,
        "redirect_uri": plan.redirect_uri,
        "capabilities": union,
        "newly_requested": [c for c in requested if c not in already],
        "already_granted": already,
        "message": (
            f"请按以下步骤完成授权 (本次申请的权限: {', '.join(union)}):\n"
            "1. 打开下面的 authorize_url, 在飞书页面点「同意授权」;\n"
            "2. 同意后浏览器会自动跳转到一个新网址, **看浏览器地址栏** -- 它形如 "
            "`http://localhost/?code=xxxxxxxx&state=...`, 把 `code=` 后面, `&` 之前的那一串复制下来 "
            "(复制整段网址也行, 工具会自动提取);\n"
            "3. 把它作为 code 交给 feishu_auth_complete.\n"
            "授权一次即缓存并自动续期; 只有当以后的任务需要**新的**权限时才会再请你授权一次.\n"
            "(想免掉第 2 步的复制: 给 Gateway 配 PSI_OAUTH_CALLBACK_BASE, 或让 PSI_FEISHU_REDIRECT_URI "
            "指向本机回环端口并在飞书后台登记.)"
        ),
        "authorize_url_note": "把 authorize_url 原样发给用户点击; 下一步要的是跳转后地址栏里的 code.",
    }


# ── 授权卡 ───────────────────────────────────────────────────────────────────
#
# 一张卡把「发链接」和「等回调」缝在一起: 按钮的 behaviors 同时挂 open_url (打开
# 授权页) 和 callback (回传给 Channel), 于是用户**点一次**就既看到飞书授权页、又
# 把「我点了」这件事告诉了 agent。agent 收到 <feishu_card_action> 那一轮才去
# auth_wait 等真回调 —— 阻塞发生在用户正对着浏览器的时候, 而不是发卡那一轮 (发卡
# 那轮必须立刻收尾, 否则 SessionAgent 的 turn 锁会把用户后续消息全排到 180s 之后)。
_AUTH_CARD_ACTION = "feishu_auth_confirm"
_AUTH_CARD_HANDLER = "feishu_auth_wait"
# 用户点了按钮却没在授权页上点「同意」时的兜底: 卡片是一次性的 (Channel 领取快照即
# 立墓碑并改写原卡), 所以只能重新发一张, 不能让用户再点那张已消费的卡。
_AUTH_CARD_RETRY_NOTE = "若用户点了按钮但没在授权页点「同意」, 这张卡已作废: 重新调 feishu_auth_card 发一张新的."


def _auth_card_content(
    authorize_url: str,
    capabilities: list[str],
    reason: str,
    user_key: str,
) -> dict[str, Any]:
    """The authorization card: one button that opens the consent page *and* calls back.

    Card 2.0 is used deliberately: only its ``behaviors`` array lets a single click do
    both (the legacy ``url`` + ``value`` pair needs ``complex_interaction`` and is the
    older contract). The button carries ``user_key`` in its callback value so the
    handling turn knows whose pending authorization to wait on — a group card lands in
    the clicker's own private session, where the sender's context is not available.
    """
    why = reason.strip() or "需要用你的飞书身份授权一次才能继续 (机器人自己的权限做不了这一步)."
    # 这行操作提示原来是个 note 组件, 但卡片 2.0 删掉了 note, 而且 2.0 对不认识的
    # 标签/属性是**报错**而非像 1.0 那样忽略 (整张卡被拒: 200861 unsupported tag note)。
    # 官方替代是「普通文本组件配小字号灰字」, 这里改用同一张卡里已经在用的 markdown
    # 承载: 不引入任何未经验证的属性名, 免得再撞一次同类拒收。
    body = (
        f"**{why}**\n\n"
        f"本次申请的权限: {', '.join(capabilities) or '(默认)'}\n\n"
        "_点下面的按钮会打开飞书授权页, 在页面上点「同意授权」即可, 不用复制任何东西._"
    )
    return {
        "schema": "2.0",
        "header": {
            "title": {"tag": "plain_text", "content": "飞书授权"},
            "template": "blue",
        },
        "body": {
            "elements": [
                {"tag": "markdown", "content": body},
                {
                    "tag": "button",
                    "name": _AUTH_CARD_ACTION,
                    "text": {"tag": "plain_text", "content": "点此授权"},
                    "type": "primary",
                    "behaviors": [
                        {"type": "open_url", "default_url": authorize_url},
                        {
                            "type": "callback",
                            "value": {"action": _AUTH_CARD_ACTION, "user_key": user_key},
                        },
                    ],
                },
            ]
        },
    }


async def auth_card_impl(
    user_key: str,
    capabilities: str = "",
    reason: str = "",
    receive_id: str = "",
) -> dict[str, Any]:
    """Send ``user_key`` an authorization card instead of a bare authorize URL.

    Wraps ``auth_start_impl`` + ``send_card_impl`` so the model never hand-rolls this
    card: forgetting ``behaviors``/``action_handlers`` yields a button that opens the
    page but never tells the agent, and the authorization then hangs waiting for a
    turn that never comes.

    Only meaningful when the code can come back by itself — with a manual-only
    receiver the click would still leave the user copying ``code=`` out of the address
    bar, so this refuses rather than shipping a button that promises otherwise.

    ``receive_id`` defaults to ``user_key`` (a DM). Deliberately: the pending
    ``state``/PKCE verifier is written under the *sending* workspace, while a card
    clicked in a group is routed to the clicker's own private session — a different
    workspace, where ``auth_wait`` would find no pending authorization.
    """
    key = (user_key or "").strip()
    if not key:
        return _error("user_key is required — it is whose authorization this is (the sender's open_id).")
    target = (receive_id or "").strip() or key
    if not target.startswith("ou_"):
        return _error(
            "授权卡只能私聊发给本人 (receive_id 必须是 ou_ 开头的 open_id): 待完成的授权记录存在"
            "发卡方 workspace, 而群里点卡片会落到点击者自己的私聊会话, 那边读不到这条记录. "
            "群场景请先私聊该用户.",
            receive_id=target,
        )
    started = await auth_start_impl(capabilities, key)
    if not started.get("ok"):
        return started
    if not started.get("auto_receive"):
        return _error(
            "当前环境没有自动接收授权码的通道, 授权卡帮不上忙 (用户点完还得从地址栏复制 code). "
            "请按 feishu_auth_start 的 message 走手工流程. "
            "(想彻底免掉复制: 调 feishu_auth_env_check 看确切缺哪一项配置, 它会给出修法; "
            "笼统去配 PSI_OAUTH_CALLBACK_BASE 未必对症 —— 比如已设的 PSI_FEISHU_REDIRECT_URI "
            "优先级更高, 会盖掉它.)",
            manual_required=True,
            mode=started.get("mode", ""),
            authorize_url=started.get("authorize_url", ""),
            next_step="feishu_auth_env_check",
        )
    granted = [c for c in started.get("capabilities", []) if isinstance(c, str)]
    card = _auth_card_content(str(started.get("authorize_url", "")), granted, reason, key)
    sent = await send_card_impl(
        target,
        json.dumps(card, ensure_ascii=False),
        "open_id",
        key,
        json.dumps(
            {
                "purpose": "feishu_user_authorization",
                "user_key": key,
                "capabilities": granted,
                "reason": reason.strip(),
                "mode": started.get("mode", ""),
            },
            ensure_ascii=False,
        ),
        json.dumps({_AUTH_CARD_ACTION: _AUTH_CARD_HANDLER}, ensure_ascii=False),
    )
    if not sent.get("ok"):
        # The card is the whole delivery mechanism here; a send failure leaves the
        # pending authorization unusable, so report it rather than claiming progress.
        return {
            **sent,
            "authorize_url": started.get("authorize_url", ""),
            "capabilities": granted,
            "fallback": "卡片没发出去: 可以把 authorize_url 直接发给用户, 再调 feishu_auth_wait 等回调.",
        }
    return {
        "ok": True,
        "message_id": sent.get("message_id", ""),
        "receive_id": target,
        "capabilities": granted,
        "newly_requested": started.get("newly_requested", []),
        "already_granted": started.get("already_granted", []),
        "mode": started.get("mode", ""),
        "action_handler": _AUTH_CARD_HANDLER,
        "message": (
            "授权卡已发给用户. **这一轮到此为止, 不要再等待、也不要另发链接** —— 用户点卡片上的"
            "「点此授权」时飞书会把点击回调给你, 那一轮再调 "
            f"feishu_auth_wait(user_key={key!r}) 等授权码自动回流, 拿到 token 后继续原来的操作.\n"
            f"{_AUTH_CARD_RETRY_NOTE}"
        ),
        "next_step": f"等卡片回调, 届时调 {_AUTH_CARD_HANDLER}",
    }


async def _read_pending(user_key: str) -> dict[str, Any]:
    """读回 ``auth_start`` 写下的 pending 记录; 缺失/损坏返回空 dict。"""
    with contextlib.suppress(OSError, ValueError):
        raw = await anyio.Path(_pending_auth_path(user_key)).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    return {}


async def auth_wait_impl(user_key: str = "", timeout_seconds: int = 480) -> dict[str, Any]:
    """等浏览器把授权码送回来, 然后直接完成授权 -- 用户无需复制任何东西。

    按 ``auth_start`` 选定的通道等待: ``gateway`` 轮询 Gateway 的 ``/oauth/code``,
    ``loopback`` 起一次性本机监听。拿到 code 后立即走 token 交换。
    """
    pending = await _read_pending(user_key)
    state = str(pending.get("state") or "")
    mode = str(pending.get("mode") or "manual")
    if not state:
        return _error("没有待完成的授权, 请先调 feishu_auth_start.")
    if mode == "manual":
        return _error(
            "当前环境无法自动接收授权码, 请让用户从浏览器地址栏复制 code 后交给 feishu_auth_complete. "
            "(想免掉复制: 调 feishu_auth_env_check 看确切缺哪一项配置, 它会给出修法.)",
            manual_required=True,
            next_step="feishu_auth_env_check",
        )
    timeout = float(max(10, min(timeout_seconds, 600)))
    if mode == "gateway":
        got = await _oauth_rx.poll_gateway(state, timeout)
    else:
        port = _oauth_rx.loopback_port()
        with contextlib.suppress(ValueError):
            from urllib.parse import urlsplit  # noqa: PLC0415

            port = urlsplit(str(pending.get("redirect_uri") or "")).port or port
        got = await _oauth_rx.wait_loopback(port, state, timeout)
    if not got:
        # 别把超时当失败报给用户: 取件箱 TTL 600 秒, 用户晚点几十秒点完, code 仍在里面等着取。
        # (实测过一次真实场景: 等待窗口比用户点击早关了 12 秒, 而回调随后就到了。)
        return _error(
            f"等了 {int(timeout)} 秒还没收到授权回调 -- 这不代表失败: 用户可能还没点完. "
            "授权码在 Gateway 取件箱里可留存约 10 分钟, 所以**先再调一次 feishu_auth_wait 继续等**, "
            "拿到就照样能完成授权; 别急着让用户手抄 code, 也别告诉他失败了. "
            "只有再等一轮仍然没有, 才去确认用户是否真的点了「同意授权」.",
            timed_out=True,
            retry_hint="再调一次 feishu_auth_wait (同一个 user_key) 即可继续等待",
        )
    if got.get("error"):
        return _error(f"用户侧授权失败: {got['error']}")
    return await auth_complete_impl(got.get("code", ""), user_key)


async def identity_set_impl(user_key: str, identity: str) -> dict[str, Any]:
    """Record who owns what this user creates: themselves, or the bot."""
    err = set_identity(user_key, identity)
    if err:
        return _error(err, identity_options=list(_IDENTITY_CHOICES))
    choice = get_identity(user_key)
    owner = "你本人 (产出归你)" if choice == _IDENTITY_USER else "机器人 (产出归机器人)"
    missing = missing_capabilities(user_key, list(_DEFAULT_CAPABILITIES)) if choice == _IDENTITY_USER else []
    return {
        "ok": True,
        "identity": choice,
        "capabilities": granted_capabilities(user_key),
        "message": (
            f"已记住: 之后飞书写入操作用{owner}. 需要改的时候再调一次这个工具即可."
            + ("\n注意: 用你本人身份需要授权, 首次写入时会请你授权一次." if missing else "")
        ),
    }


async def identity_get_impl(user_key: str = "") -> dict[str, Any]:
    """Report this user's remembered ownership choice and granted capabilities."""
    choice = get_identity(user_key)
    return {
        "ok": True,
        "identity": choice,
        "asked": bool(choice),
        "capabilities": granted_capabilities(user_key),
        "capability_keys": scope_catalog_keys(),
        "message": (
            f"该用户的写入身份: {choice}."
            if choice
            else (
                "该用户还没被问过写入身份 -- 首次写入操作会返回 need_identity_choice, 那时问他并调 feishu_identity_set."
            )
        ),
    }


async def _pending_capabilities(user_key: str = "") -> list[str]:
    """Capabilities the matching ``auth_start_impl`` asked for.

    Falls back to the default set when the pending file is missing or predates this
    field — an old pending authorization still grants *something*, and recording the
    conservative default is better than recording nothing (which would re-prompt).
    """
    parked = await _read_pending(user_key)
    caps = parked.get("capabilities")
    if not isinstance(caps, list):
        return list(_DEFAULT_CAPABILITIES)
    return [c for c in caps if c in _SCOPE_CATALOG]


def _auth_error_hint(code: Any, redirect_uri: str) -> str:
    """把飞书授权类错误码翻成可操作的话。

    裸 ``msg`` (如 "redirect_uri mismatch") 对 agent 没用: 它给不出「去后台加这条
    URL」这种下一步。配置类错误里 20071/20043 占绝大多数, 单独翻译这几个就够。
    """
    hints = {
        20071: (
            f"redirect_uri 没登记或与授权时不一致. 去飞书开放平台「安全设置 -> 重定向 URL」"
            f"确认有这一条且完全一致 (含端口和末尾斜杠): {redirect_uri or '(本次未记录)'}. "
            "调 feishu_auth_redirect_url 可以拿到该填的地址."
        ),
        20043: (
            "申请的 scope 里有应用没开通或不存在的项. 去开放平台「权限管理」开通对应权限, "
            "或改用 capabilities 参数里的合法键 (别直接传飞书原始 scope 字符串)."
        ),
        20029: "授权码已过期或被用过. 授权码是一次性且短效的, 让用户重新点一次授权链接.",
    }
    try:
        return hints.get(int(code), "")
    except TypeError, ValueError:
        return ""


async def auth_complete_impl(code: str, user_key: str = "") -> dict[str, Any]:
    """Exchange the authorization code for a user_access_token and cache it."""
    if not code.strip():
        return _error("No code provided.")
    pending = await _read_pending(user_key)
    app_token = await _get_app_access_token()
    if app_token is None:
        return _error("Feishu app not configured or app_access_token fetch failed.")
    body: dict[str, Any] = {"grant_type": "authorization_code", "code": _extract_code(code)}
    # PKCE verifier 与 redirect_uri 必须与 authorize 阶段一致 (飞书: 不一致报 20071)。
    if pending.get("code_verifier"):
        body["code_verifier"] = pending["code_verifier"]
    if pending.get("redirect_uri"):
        body["redirect_uri"] = pending["redirect_uri"]
    payload = await _post_json(
        _TOKEN_URL,
        body,
        headers={"Authorization": f"Bearer {app_token}"},
    )
    if payload.get("code") not in (0, None):
        hint = _auth_error_hint(payload.get("code"), str(pending.get("redirect_uri") or ""))
        return {
            "ok": False,
            "code": payload.get("code"),
            "msg": payload.get("msg", ""),
            "message": f"Token exchange failed: {payload.get('msg', '')}" + (f"\n{hint}" if hint else ""),
            **({"config_hint": hint, "next_step": "feishu_auth_env_check"} if hint else {}),
        }
    uat = _uat_from_token_response(payload)
    if not uat.access_token:
        return _error("Token exchange returned no access_token.")
    await _get_token_store().set(_norm_user_key(user_key), uat)
    # Which capabilities this grant covers was decided in auth_start_impl and parked
    # in the pending-auth file; read it back before unlinking so the union survives.
    granted = await _pending_capabilities(user_key)
    _record_granted_capabilities(user_key, granted)
    with contextlib.suppress(OSError):
        await anyio.Path(_pending_auth_path(user_key)).unlink()
    return {
        "ok": True,
        "open_id": uat.open_id or "",
        "scopes": uat.scopes,
        "capabilities": granted_capabilities(user_key),
        "message": (
            "授权成功, 已缓存 user_access_token 并会自动续期 -- 已获得的权限会被记住, "
            "之后同类操作不会再让你授权 (只有需要新权限时才会再问一次)."
        ),
    }


async def _get_valid_uat(user_key: str = "") -> Any:
    """Return a non-expired UAT for ``user_key`` (refreshing if needed), or None."""
    from lark_channel.channel.auth.device_flow import uat_needs_refresh  # noqa: PLC0415

    key = _norm_user_key(user_key)
    store = _get_token_store()
    uat = await store.get(key)
    if uat is None:
        return None
    if uat_needs_refresh(uat) and uat.refresh_token:
        app_token = await _get_app_access_token()
        if app_token is not None:
            payload = await _post_json(
                _REFRESH_URL,
                {"grant_type": "refresh_token", "refresh_token": uat.refresh_token},
                headers={"Authorization": f"Bearer {app_token}"},
            )
            if payload.get("code") in (0, None) and (payload.get("data") or payload).get("access_token"):
                uat = _uat_from_token_response(payload)
                await store.set(key, uat)
    return uat


def _build_doc_search_request(search_key: str, count: int, offset: int, docs_types: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/suite/docs-api/search/object"
    req.token_types = {AccessTokenType.USER}
    body: dict[str, Any] = {"search_key": search_key, "count": count, "offset": offset}
    if docs_types:
        body["docs_types"] = docs_types
    req.body = body
    return req


async def search_docs_impl(
    search_key: str, count: int, offset: int, docs_types: str, user_key: str = ""
) -> dict[str, Any]:
    """Search cloud docs by keyword (needs a user_access_token). Returns matched docs."""
    client = _get_uat_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    uat = await _get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return _error(_AUTH_PROMPT, need_auth=True, need_capabilities=["docs_read"])

    types_list = [t.strip() for t in docs_types.split(",") if t.strip()]
    req = _build_doc_search_request(search_key, count, offset, types_list)
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(req, option)
    except Exception as exc:
        return _error(f"Feishu search failed: {type(exc).__name__}: {exc}")

    body = _parse_resp_body(resp)
    if body.get("code") not in (0, None):
        return {
            "ok": False,
            "code": body.get("code"),
            "msg": body.get("msg", ""),
            "message": f"Feishu API error {body.get('code')}: {body.get('msg', '')}",
        }
    data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    docs = [
        {
            "title": e.get("title", ""),
            "token": e.get("docs_token", ""),
            "obj_type": e.get("docs_type", ""),
            "owner_id": e.get("owner_id", ""),
        }
        for e in (data.get("docs_entities", []) if isinstance(data.get("docs_entities"), list) else [])
    ]
    return {
        "ok": True,
        "docs": docs,
        "count": len(docs),
        "has_more": bool(data.get("has_more")),
        "total": data.get("total", 0),
    }


def _build_wiki_space_create_request(name: str, description: str, open_sharing: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/wiki/v2/spaces"
    req.token_types = {AccessTokenType.USER}
    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if description:
        body["description"] = description
    if open_sharing:
        body["open_sharing"] = open_sharing
    req.body = body
    return req


async def create_wiki_space_impl(
    name: str, description: str = "", open_sharing: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Create a new Feishu wiki space (knowledge base). Needs a user_access_token.

    Feishu's create-space API only accepts a UAT (not the bot's tenant token); the
    new space is owned by the authorizing user. Returns the new space_id + name.
    """
    client = _get_uat_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    uat = await _get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return _error(_AUTH_PROMPT, need_auth=True, need_capabilities=["wiki_write"])

    sharing = open_sharing.strip()
    if sharing and sharing not in ("open", "closed"):
        return _error("open_sharing must be 'open' or 'closed' (or empty).")
    req = _build_wiki_space_create_request(name.strip(), description.strip(), sharing)
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(req, option)
    except Exception as exc:
        return _error(f"Feishu create wiki space failed: {type(exc).__name__}: {exc}")

    body = _parse_resp_body(resp)
    if body.get("code") not in (0, None):
        return {
            "ok": False,
            "code": body.get("code"),
            "msg": body.get("msg", ""),
            "message": f"Feishu API error {body.get('code')}: {body.get('msg', '')}",
        }
    data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    space = data.get("space", {}) if isinstance(data.get("space"), dict) else {}
    space_id = space.get("space_id", "")
    return {
        "ok": True,
        "space_id": space_id,
        "name": space.get("name", name),
        "description": space.get("description", description),
        "url": f"{_DOC_BASE_URL}/wiki/settings/{space_id}" if space_id else "",
    }


def _parse_resp_body(resp: Any) -> dict[str, Any]:
    """Extract the JSON body dict from an SDK BaseResponse (raw.content bytes)."""
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if content:
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            parsed = json.loads(bytes(content).decode("utf-8"))
            if isinstance(parsed, dict):
                return parsed
    code = getattr(resp, "code", None)
    return {"code": code, "msg": getattr(resp, "msg", "") or ""}


# ── Bitable (多维表格) — list tables, list/create records ─────────────────────
#
# Generic read/write for Feishu bases; the bot's tenant token can read+write
# records provided the app is a collaborator on the base (scope bitable:app).
# app_token is the segment in a feishu.cn/base/<app_token> URL (for wiki links,
# resolve via feishu_wiki_get_node — obj_token is the app_token when obj_type=bitable).


def _build_list_tables_request(app_token: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    req.paths["app_token"] = app_token
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_bitable_tables_impl(app_token: str, page_size: int = 100, page_token: str = "") -> dict[str, Any]:
    """List the data tables in a bitable app. Returns [{table_id, name}]."""
    res = await _invoke(_build_list_tables_request(app_token, page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    tables = [
        {"table_id": t.get("table_id", ""), "name": t.get("name", "")}
        for t in (data.get("items", []) if isinstance(data.get("items"), list) else [])
    ]
    return {
        "ok": True,
        "tables": tables,
        "count": len(tables),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


def _build_list_records_request(
    app_token: str, table_id: str, page_size: int, page_token: str, filter_: str, sort: str, field_names: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    if filter_:
        req.add_query("filter", filter_)
    if sort:
        req.add_query("sort", sort)
    if field_names:
        req.add_query("field_names", field_names)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_bitable_records_impl(
    app_token: str,
    table_id: str,
    page_size: int = 100,
    page_token: str = "",
    filter_: str = "",
    sort: str = "",
    field_names: str = "",
) -> dict[str, Any]:
    """List records in a bitable table. Returns [{record_id, fields}] + pagination."""
    res = await _invoke(
        _build_list_records_request(app_token, table_id, page_size, page_token, filter_, sort, field_names)
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    records = [
        {"record_id": r.get("record_id", ""), "fields": r.get("fields", {})}
        for r in (data.get("items", []) if isinstance(data.get("items"), list) else [])
    ]
    return {
        "ok": True,
        "records": records,
        "count": len(records),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
        "total": data.get("total", 0),
    }


# ── Bitable reads — conditional search and single-record fetch ────────────────
#
# list_bitable_records above is the plain GET: it pages the whole table (or a
# view) and its query-string `filter` only covers simple cases. The search
# endpoint is a POST whose body carries structured conditions, and Feishu's own
# docs point at it as the way to obtain a record_id — which is exactly what the
# update/delete tools need. Note the endpoint ignores `view_id` as soon as filter
# or sort is given: the request then applies to the whole table.

_SEARCH_OPERATORS = (
    "is",
    "isNot",
    "contains",
    "doesNotContain",
    "isEmpty",
    "isNotEmpty",
    "isGreater",
    "isGreaterEqual",
    "isLess",
    "isLessEqual",
)


def _build_search_records_request(
    app_token: str, table_id: str, body: dict[str, Any], page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/search"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = body
    return req


def _parse_search_filter(filter_json: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse and check a search filter object; return (filter, error message)."""
    try:
        parsed = json.loads(filter_json)
    except ValueError as exc:
        return None, f"filter_json is not valid JSON: {exc}"
    if not isinstance(parsed, dict):
        return None, (
            'filter_json must be a JSON object, e.g. \'{"conjunction":"and","conditions":'
            '[{"field_name":"状态","operator":"is","value":["进行中"]}]}\'.'
        )
    conjunction = str(parsed.get("conjunction", "")).strip().lower()
    if conjunction not in {"and", "or"}:
        return None, 'filter_json needs "conjunction": "and" or "or" (Feishu requires it).'
    parsed["conjunction"] = conjunction
    conditions = parsed.get("conditions")
    if not isinstance(conditions, list) or not conditions:
        return None, 'filter_json needs a non-empty "conditions" array.'
    if len(conditions) > 50:
        return None, f"filter_json has {len(conditions)} conditions; Feishu allows at most 50."
    for i, cond in enumerate(conditions):
        if not isinstance(cond, dict):
            return None, f"filter_json.conditions[{i}] must be an object with field_name and operator."
        if not str(cond.get("field_name", "")).strip():
            return None, f"filter_json.conditions[{i}] is missing a non-empty field_name."
        operator = str(cond.get("operator", "")).strip()
        if operator not in _SEARCH_OPERATORS:
            return None, (
                f"filter_json.conditions[{i}].operator {operator!r} is not supported; "
                f"use one of {', '.join(_SEARCH_OPERATORS)}."
            )
        value = cond.get("value")
        if value is not None and not isinstance(value, list):
            return None, (
                f'filter_json.conditions[{i}].value must be an array of strings, e.g. ["进行中"] '
                "(omit it for isEmpty / isNotEmpty)."
            )
    return parsed, None


async def search_bitable_records_impl(
    app_token: str,
    table_id: str,
    filter_json: str = "",
    sort_json: str = "",
    field_names: str = "",
    view_id: str = "",
    page_size: int = 100,
    page_token: str = "",
    automatic_fields: bool = False,
    user_key: str = "",
) -> dict[str, Any]:
    """Search records with structured conditions. Returns [{record_id, fields}] + pagination."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _error("No table_id provided (get it from feishu_bitable_list_tables).")
    if page_size < 1 or page_size > 500:
        return _error(f"page_size must be between 1 and 500 (got {page_size}).")
    body: dict[str, Any] = {}
    if filter_json.strip():
        parsed_filter, problem = _parse_search_filter(filter_json)
        if problem:
            return _error(problem)
        body["filter"] = parsed_filter
    if sort_json.strip():
        try:
            sort = json.loads(sort_json)
        except ValueError as exc:
            return _error(f"sort_json is not valid JSON: {exc}")
        if not isinstance(sort, list):
            return _error('sort_json must be a JSON array, e.g. \'[{"field_name":"日期","desc":true}]\'.')
        body["sort"] = sort
    if field_names.strip():
        try:
            names = json.loads(field_names)
        except ValueError as exc:
            return _error(f"field_names is not valid JSON: {exc}")
        if not isinstance(names, list):
            return _error('field_names must be a JSON array of column names, e.g. \'["状态","负责人"]\'.')
        body["field_names"] = names
    if view_id.strip():
        if "filter" in body or "sort" in body:
            # Feishu silently ignores view_id here; say so rather than let the caller
            # believe the search was scoped to their view.
            return _error(
                "view_id cannot be combined with filter_json / sort_json — Feishu then searches the "
                "whole table and ignores the view. Drop one of them."
            )
        body["view_id"] = view_id.strip()
    if automatic_fields:
        body["automatic_fields"] = True
    res = await _invoke(
        _build_search_records_request(app_token.strip(), table_id.strip(), body, page_size, page_token),
        user_key=user_key,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    records = [
        {"record_id": r.get("record_id", ""), "fields": r.get("fields", {})}
        for r in (data.get("items", []) if isinstance(data.get("items"), list) else [])
    ]
    return {
        "ok": True,
        "records": records,
        "count": len(records),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
        "total": data.get("total", 0),
    }


def _build_get_record_request(app_token: str, table_id: str, record_id: str, automatic_fields: bool) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.paths["record_id"] = record_id
    req.add_query("with_shared_url", "true")
    if automatic_fields:
        req.add_query("automatic_fields", "true")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def get_bitable_record_impl(
    app_token: str,
    table_id: str,
    record_id: str,
    automatic_fields: bool = False,
    user_key: str = "",
) -> dict[str, Any]:
    """Read one record by id. Returns its fields, url and (optionally) who created/changed it."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _error("No table_id provided (get it from feishu_bitable_list_tables).")
    if not record_id.strip():
        return _error("No record_id provided (get it from feishu_bitable_search_records).")
    res = await _invoke(
        _build_get_record_request(app_token.strip(), table_id.strip(), record_id.strip(), automatic_fields),
        user_key=user_key,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    record = data.get("record", {}) if isinstance(data.get("record"), dict) else {}
    result: dict[str, Any] = {
        "ok": True,
        "record_id": record.get("record_id", "") or record_id.strip(),
        "fields": record.get("fields", {}),
        "url": record.get("record_url", ""),
    }
    for key in ("created_by", "created_time", "last_modified_by", "last_modified_time"):
        if record.get(key):
            result[key] = record[key]
    return result


def _build_create_record_request(app_token: str, table_id: str, fields: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"fields": fields}
    return req


async def create_bitable_record_impl(
    app_token: str, table_id: str, fields_json: str, user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Create one record in a bitable table. fields_json is a JSON object of {column: value}."""
    try:
        fields = json.loads(fields_json)
    except ValueError as exc:
        return _error(f"fields_json is not valid JSON: {exc}")
    if not isinstance(fields, dict):
        return _error("fields_json must be a JSON object mapping column names to values.")
    res = await _invoke(
        _build_create_record_request(app_token, table_id, fields), user_key=user_key, prefer="user", identity=identity
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    record = data.get("record", {}) if isinstance(data.get("record"), dict) else {}
    return {"ok": True, "record_id": record.get("record_id", ""), "fields": record.get("fields", {})}


def _build_batch_create_records_request(app_token: str, table_id: str, records: list[dict[str, Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_create"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"records": records}
    return req


async def create_bitable_records_impl(
    app_token: str,
    table_id: str,
    records_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> dict[str, Any]:
    """Create many records in one call. records_json is a JSON array of row objects; batches of 500."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _error("No table_id provided (get it from feishu_bitable_list_tables).")
    try:
        rows = json.loads(records_json)
    except ValueError as exc:
        return _error(f"records_json is not valid JSON: {exc}")
    if not isinstance(rows, list) or not rows:
        return _error(
            "records_json must be a non-empty JSON array of row objects, e.g. "
            '\'[{"姓名":"张三","状态":"在读"},{"姓名":"李四"}]\'.'
        )
    # Accept both the bare {column: value} shape and Feishu's {"fields": {...}} wrapper,
    # since a caller who just used update_records will reach for the latter.
    records: list[dict[str, Any]] = []
    names: list[str] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            return _error(f"records_json[{i}] must be a JSON object of column → value.")
        wrapped = row.get("fields")
        fields = _as_field_map(wrapped) if isinstance(wrapped, dict) else _as_field_map(row)
        if not fields:
            return _error(f"records_json[{i}] has no column values.")
        records.append({"fields": fields})
        names.extend(k for k in fields if k not in names)
    if validate_fields:
        problem = await _check_bitable_columns(app_token.strip(), table_id.strip(), names)
        if problem:
            return problem
    created: list[str] = []
    dropped: list[str] = []
    for i in range(0, len(records), 500):
        batch = records[i : i + 500]
        res = await _invoke(
            _build_batch_create_records_request(app_token.strip(), table_id.strip(), batch),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "created": created, "count": len(created)}
        data = res["data"] if isinstance(res["data"], dict) else {}
        echoed = data.get("records", []) if isinstance(data.get("records"), list) else []
        for offset, rec in enumerate(echoed):
            if not isinstance(rec, dict):
                continue
            created.append(rec.get("record_id", ""))
            if offset < len(batch):
                dropped.extend(_dropped_fields(batch[offset]["fields"], rec.get("fields", {})))
    result: dict[str, Any] = {"ok": True, "created": created, "count": len(created)}
    if dropped:
        result["dropped_fields"] = sorted(set(dropped))
        result["warning"] = (
            f"Feishu accepted the call but did not write {', '.join(sorted(set(dropped)))} — "
            "check the column names and value types."
        )
    return result


# ── Bitable record updates — change cell values in existing rows ──────────────
#
# The update APIs are *incremental*: only the field names present in `fields` are
# written, everything else on the row keeps its value, and an explicit null blanks
# a cell. That is what makes "改一个单元格" possible without re-sending the row.
#
# The hazard these two impls guard against: Feishu **silently drops** field names
# the table doesn't have and still answers code:0. A caller who writes "Mentor"
# into a table whose column is "导师" gets a cheerful success and an unchanged
# cell. So the column names are checked against the table's real fields before the
# write, and the response is compared with what was asked for afterwards.


def _build_update_record_request(app_token: str, table_id: str, record_id: str, fields: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/:record_id"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.paths["record_id"] = record_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"fields": fields}
    return req


def _build_batch_update_records_request(app_token: str, table_id: str, records: list[dict[str, Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_update"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"records": records}
    return req


def _as_field_map(value: Any) -> dict[str, Any]:
    """Read a parsed-JSON object as a {column: value} map.

    ``json.loads`` is typed as ``Any``, so an ``isinstance(x, dict)`` check leaves the
    key type unknown and every column name downstream types as ``object``. JSON object
    keys are always strings, so restating that here keeps the callers plainly typed
    instead of casting at each use.
    """
    return {str(k): v for k, v in value.items()} if isinstance(value, dict) else {}


async def _check_bitable_columns(app_token: str, table_id: str, names: list[str]) -> dict[str, Any] | None:
    """Reject column names the table doesn't have; return an error dict, or None if fine.

    Returns None as well when the field list can't be read (e.g. the bot may write but
    not list fields) — a failed *check* must not block a write the user asked for.
    """
    listed = await list_bitable_fields_impl(app_token, table_id)
    if not listed.get("ok"):
        return None
    valid = [f.get("name", "") for f in listed.get("fields", [])]
    unknown = [n for n in names if n not in valid]
    if not unknown:
        return None
    return _error(
        f"These column names are not in the table and would be silently ignored by Feishu: "
        f"{', '.join(unknown)}. Existing columns: {', '.join(valid)}.",
        unknown_fields=unknown,
        valid_fields=valid,
    )


def _dropped_fields(requested: dict[str, Any], written: Any) -> list[str]:
    """Field names asked for but missing from Feishu's echo of the updated record."""
    if not isinstance(written, dict):
        return []
    return [k for k, v in requested.items() if v is not None and k not in written]


async def update_bitable_record_impl(
    app_token: str,
    table_id: str,
    record_id: str,
    fields_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> dict[str, Any]:
    """Update cells in one existing record. Only the given columns change; null clears one."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _error("No table_id provided (get it from feishu_bitable_list_tables).")
    if not record_id.strip():
        return _error("No record_id provided (get it from feishu_bitable_list_records).")
    try:
        parsed = json.loads(fields_json)
    except ValueError as exc:
        return _error(f"fields_json is not valid JSON: {exc}")
    if not isinstance(parsed, dict) or not parsed:
        return _error(
            "fields_json must be a non-empty JSON object mapping column names to new values, "
            'e.g. \'{"状态":"已完成"}\'.'
        )
    fields = _as_field_map(parsed)
    if validate_fields:
        problem = await _check_bitable_columns(app_token.strip(), table_id.strip(), list(fields))
        if problem:
            return problem
    res = await _invoke(
        _build_update_record_request(app_token.strip(), table_id.strip(), record_id.strip(), fields),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    record = data.get("record", {}) if isinstance(data.get("record"), dict) else {}
    written = record.get("fields", {})
    result = {
        "ok": True,
        "record_id": record.get("record_id", "") or record_id.strip(),
        "updated_fields": list(fields),
        "fields": written,
    }
    dropped = _dropped_fields(fields, written)
    if dropped:
        result["dropped_fields"] = dropped
        result["warning"] = (
            f"Feishu accepted the call but did not write {', '.join(dropped)} — check the column names and value types."
        )
    return result


async def update_bitable_records_impl(
    app_token: str,
    table_id: str,
    records_json: str,
    user_key: str = "",
    identity: str = "",
    validate_fields: bool = True,
) -> dict[str, Any]:
    """Update many records in one go. records_json is [{record_id, fields}]; batches of 1000."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _error("No table_id provided (get it from feishu_bitable_list_tables).")
    try:
        parsed = json.loads(records_json)
    except ValueError as exc:
        return _error(f"records_json is not valid JSON: {exc}")
    if not isinstance(parsed, list) or not parsed:
        return _error(
            'records_json must be a non-empty JSON array, e.g. \'[{"record_id":"recA","fields":{"状态":"已完成"}}]\'.'
        )
    records: list[dict[str, Any]] = []
    names: list[str] = []
    for i, rec in enumerate(parsed):
        if not isinstance(rec, dict):
            return _error(f"records_json[{i}] must be a JSON object with record_id and fields.")
        record_id = str(rec.get("record_id", "")).strip()
        if not record_id:
            return _error(f"records_json[{i}] is missing a non-empty record_id.")
        raw_fields = rec.get("fields")
        if not isinstance(raw_fields, dict) or not raw_fields:
            return _error(f"records_json[{i}].fields must be a non-empty object of column → new value.")
        fields = _as_field_map(raw_fields)
        records.append({"record_id": record_id, "fields": fields})
        names.extend(k for k in fields if k not in names)
    if validate_fields:
        problem = await _check_bitable_columns(app_token.strip(), table_id.strip(), names)
        if problem:
            return problem
    updated: list[str] = []
    dropped: list[str] = []
    for i in range(0, len(records), 1000):
        batch = records[i : i + 1000]
        res = await _invoke(
            _build_batch_update_records_request(app_token.strip(), table_id.strip(), batch),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "updated": updated, "count": len(updated)}
        data = res["data"] if isinstance(res["data"], dict) else {}
        echoed = data.get("records", []) if isinstance(data.get("records"), list) else []
        by_id = {r.get("record_id", ""): r.get("fields", {}) for r in echoed if isinstance(r, dict)}
        for rec in batch:
            rid = str(rec["record_id"])
            updated.append(rid)
            if rid in by_id:
                dropped.extend(f"{rid}.{n}" for n in _dropped_fields(_as_field_map(rec["fields"]), by_id[rid]))
    result: dict[str, Any] = {"ok": True, "updated": updated, "count": len(updated)}
    if dropped:
        result["dropped_fields"] = dropped
        result["warning"] = (
            f"Feishu accepted the call but did not write {len(dropped)} value(s) — "
            "check the column names and value types."
        )
    return result


def _build_batch_delete_records_request(app_token: str, table_id: str, record_ids: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/records/batch_delete"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"records": record_ids}
    return req


async def delete_bitable_records_impl(
    app_token: str, table_id: str, record_ids: str, user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Delete records (rows) by id. record_ids is comma-separated; batches of 500."""
    ids = [r.strip() for r in record_ids.split(",") if r.strip()]
    if not ids:
        return _error("No record_ids provided (comma-separated record ids).")
    deleted = 0
    for i in range(0, len(ids), 500):
        batch = ids[i : i + 500]
        res = await _invoke(
            _build_batch_delete_records_request(app_token, table_id, batch),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "deleted": deleted}
        deleted += len(batch)
    return {"ok": True, "deleted": deleted, "record_ids": ids}


async def clear_bitable_table_impl(
    app_token: str,
    table_id: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Delete ALL records (rows) in a table — pages through every record, then batch-deletes."""
    ids: list[str] = []
    page_token = ""
    while True:
        res = await _invoke(
            _build_list_records_request(app_token, table_id, 500, page_token, "", "", ""), user_key=user_key
        )
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for r in data.get("items", []) if isinstance(data.get("items"), list) else []:
            rid = r.get("record_id", "")
            if rid:
                ids.append(rid)
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    if not ids:
        return {"ok": True, "deleted": 0, "note": "table already has no records"}
    deleted = 0
    for i in range(0, len(ids), 500):
        batch = ids[i : i + 500]
        res = await _invoke(
            _build_batch_delete_records_request(app_token, table_id, batch),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            return {**res, "deleted": deleted}
        deleted += len(batch)
    return {"ok": True, "deleted": deleted}


_BITABLE_FIELD_TYPES = {
    1: "文本",
    2: "数字",
    3: "单选",
    4: "多选",
    5: "日期",
    7: "复选框",
    11: "人员",
    13: "电话",
    15: "超链接",
    17: "附件",
    18: "单向关联",
    20: "公式",
    21: "双向关联",
    22: "地理位置",
    23: "群组",
    1001: "创建时间",
    1002: "最后更新时间",
    1003: "创建人",
    1004: "修改人",
    1005: "自动编号",
}


def _build_list_fields_request(app_token: str, table_id: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_bitable_fields_impl(app_token: str, table_id: str) -> dict[str, Any]:
    """List a table's fields (columns). Returns [{field_id, name, type, is_primary}] for all fields."""
    fields: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _invoke(_build_list_fields_request(app_token, table_id, 100, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for f in data.get("items", []) if isinstance(data.get("items"), list) else []:
            ftype = f.get("type")
            fields.append(
                {
                    "field_id": f.get("field_id", ""),
                    "name": f.get("field_name", ""),
                    "type": _BITABLE_FIELD_TYPES.get(ftype, ftype),
                    "is_primary": bool(f.get("is_primary")),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return {"ok": True, "fields": fields, "count": len(fields)}


def _build_delete_field_request(app_token: str, table_id: str, field_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.paths["field_id"] = field_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def delete_bitable_fields_impl(
    app_token: str, table_id: str, field_ids: str, user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Delete fields (columns) by id. field_ids is comma-separated. Primary field cannot be deleted."""
    ids = [f.strip() for f in field_ids.split(",") if f.strip()]
    if not ids:
        return _error("No field_ids provided (comma-separated field ids from feishu_bitable_list_fields).")
    deleted: list[str] = []
    for fid in ids:
        res = await _invoke(
            _build_delete_field_request(app_token, table_id, fid), user_key=user_key, prefer="user", identity=identity
        )
        if not res["ok"]:
            return {**res, "deleted": deleted, "failed_field_id": fid}
        deleted.append(fid)
    return {"ok": True, "deleted": deleted, "count": len(deleted)}


# ── Bitable creation — new base, new data table, new field ────────────────────
#
# The tools above all need an app_token that already exists, i.e. a base somebody
# built by hand. These three create it: base (POST /bitable/v1/apps) → data table
# (POST .../tables, optionally with its initial columns) → extra field
# (POST .../fields). Writes prefer the user's identity so the base is owned by the
# person who asked for it, falling back to the bot's tenant token.
#
# Field `type` is the same integer vocabulary list_bitable_fields decodes:
# 1 文本, 2 数字, 3 单选, 4 多选, 5 日期, 7 复选框, 11 人员, 13 电话, 15 超链接,
# 17 附件, 18 单向关联, 20 公式, 21 双向关联, 22 地理位置, 23 群组, 1001 创建时间,
# 1002 最后更新时间, 1003 创建人, 1004 修改人, 1005 自动编号. Type 19 (查找引用)
# cannot be created. The first field of a table is its index (primary) column and
# only accepts 1, 2, 5, 13, 15, 20, 22 — Feishu answers 1254012 otherwise.

_INDEX_FIELD_TYPES = {1, 2, 5, 13, 15, 20, 22}
_UNCREATABLE_FIELD_TYPE = 19


def _build_create_bitable_app_request(name: str, folder_token: str, time_zone: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if folder_token:
        body["folder_token"] = folder_token
    if time_zone:
        body["time_zone"] = time_zone
    req.body = body
    return req


async def create_bitable_app_impl(
    name: str, folder_token: str = "", time_zone: str = "", user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Create a new bitable (多维表格). Returns its app_token, url and default_table_id."""
    res = await _invoke(
        _build_create_bitable_app_request(name.strip(), folder_token.strip(), time_zone.strip()),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    app = data.get("app", {}) if isinstance(data.get("app"), dict) else {}
    app_token = app.get("app_token", "")
    return {
        "ok": True,
        "app_token": app_token,
        "name": app.get("name", name),
        "folder_token": app.get("folder_token", ""),
        "default_table_id": app.get("default_table_id", ""),
        "time_zone": app.get("time_zone", ""),
        "url": app.get("url") or (f"{_DOC_BASE_URL}/base/{app_token}" if app_token else ""),
    }


def _validate_bitable_fields(fields: Any, *, as_table_fields: bool) -> str | None:
    """Check a parsed fields list; return an error message, or None when it is usable."""
    if not isinstance(fields, list) or not fields:
        return "fields_json must be a non-empty JSON array of field objects."
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            return f"fields_json[{i}] must be a JSON object with field_name and type."
        if not str(f.get("field_name", "")).strip():
            return f"fields_json[{i}] is missing a non-empty field_name."
        ftype = f.get("type")
        if not isinstance(ftype, int) or isinstance(ftype, bool):
            return f"fields_json[{i}].type must be an integer field type (1=文本, 2=数字, 3=单选, 5=日期, ...)."
        if ftype == _UNCREATABLE_FIELD_TYPE:
            return f"fields_json[{i}].type 19 (查找引用) cannot be created via the API."
        if as_table_fields and i == 0 and ftype not in _INDEX_FIELD_TYPES:
            return (
                f"fields_json[0].type {ftype} cannot be the index (primary) column; "
                f"the first field must be one of {sorted(_INDEX_FIELD_TYPES)} "
                "(1=文本, 2=数字, 5=日期, 13=电话, 15=超链接, 20=公式, 22=地理位置)."
            )
    return None


def _build_create_table_request(app_token: str, table: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"table": table}
    return req


async def create_bitable_table_impl(
    app_token: str,
    table_name: str,
    fields_json: str = "",
    default_view_name: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Create a data table in a bitable. fields_json is a JSON array of field objects."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_name.strip():
        return _error("No table_name provided.")
    table: dict[str, Any] = {"name": table_name.strip()}
    if fields_json.strip():
        try:
            fields = json.loads(fields_json)
        except ValueError as exc:
            return _error(f"fields_json is not valid JSON: {exc}")
        problem = _validate_bitable_fields(fields, as_table_fields=True)
        if problem:
            return _error(problem)
        table["fields"] = fields
    if default_view_name.strip():
        if "fields" not in table:
            # Feishu rejects default_view_name on its own; say so instead of failing upstream.
            return _error("default_view_name requires fields_json (Feishu only accepts the two together).")
        table["default_view_name"] = default_view_name.strip()
    res = await _invoke(
        _build_create_table_request(app_token.strip(), table), user_key=user_key, prefer="user", identity=identity
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    field_ids = data.get("field_id_list", [])
    return {
        "ok": True,
        "table_id": data.get("table_id", ""),
        "name": table["name"],
        "default_view_id": data.get("default_view_id", ""),
        "field_ids": field_ids if isinstance(field_ids, list) else [],
    }


def _build_batch_create_tables_request(app_token: str, names: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/batch_create"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"tables": [{"name": n} for n in names]}
    return req


async def create_bitable_tables_impl(
    app_token: str, table_names: str, user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Create several empty data tables at once (names only — no columns). Max 50 per call."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    names = [n.strip() for n in table_names.split(",") if n.strip()]
    if not names:
        return _error("No table_names provided (comma-separated table names).")
    if len(names) > 50:
        return _error(f"{len(names)} table names given; Feishu creates at most 50 per call.")
    bad = [n for n in names if any(c in n for c in "/\\?*:[]")]
    if bad:
        return _error(f"These table names contain characters Feishu rejects (/ \\ ? * : [ ]): {', '.join(bad)}.")
    res = await _invoke(
        _build_batch_create_tables_request(app_token.strip(), names),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    ids = data.get("table_ids", [])
    ids = ids if isinstance(ids, list) else []
    return {
        "ok": True,
        "tables": [{"table_id": tid, "name": name} for tid, name in zip(ids, names, strict=False)],
        "count": len(ids),
        "note": "these tables have only a placeholder column — add columns with feishu_bitable_create_field",
    }


def _build_batch_delete_tables_request(app_token: str, table_ids: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/batch_delete"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"table_ids": table_ids}
    return req


async def delete_bitable_tables_impl(
    app_token: str, table_ids: str, user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Delete whole data tables (with all their data) by id. Max 50 per call; last table can't go."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    ids = [t.strip() for t in table_ids.split(",") if t.strip()]
    if not ids:
        return _error("No table_ids provided (comma-separated table ids from feishu_bitable_list_tables).")
    if len(ids) > 50:
        return _error(f"{len(ids)} table ids given; Feishu deletes at most 50 per call.")
    res = await _invoke(
        _build_batch_delete_tables_request(app_token.strip(), ids),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        if str(res.get("code")) == "1254034":
            return {
                **res,
                "hint": "a bitable must keep at least one data table — Feishu refuses to delete the last one.",
            }
        return res
    return {"ok": True, "deleted": ids, "count": len(ids)}


# ── Bitable base metadata — read, rename / toggle advanced perms, copy ─────────
#
# App-level rather than table-level: the metadata call is also how you check
# `is_advanced` before trying to create a role (advanced permission must be on),
# and `copy` turns an existing base into a template — a standard ledger built once
# and duplicated per project instead of rebuilt column by column.


def _build_get_bitable_app_request(app_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def get_bitable_app_impl(app_token: str, user_key: str = "") -> dict[str, Any]:
    """Read a base's metadata: name, whether advanced permission is on, time zone, revision."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    res = await _invoke(_build_get_bitable_app_request(app_token.strip()), user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    app = data.get("app", {}) if isinstance(data.get("app"), dict) else {}
    token = app.get("app_token", "") or app_token.strip()
    return {
        "ok": True,
        "app_token": token,
        "name": app.get("name", ""),
        "is_advanced": bool(app.get("is_advanced")),
        "time_zone": app.get("time_zone", ""),
        "revision": app.get("revision", 0),
        "url": f"{_DOC_BASE_URL}/base/{token}",
    }


def _build_update_bitable_app_request(app_token: str, body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/bitable/v1/apps/:app_token"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = body
    return req


async def update_bitable_app_impl(
    app_token: str,
    name: str = "",
    is_advanced: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Rename a base and/or switch advanced permission on or off. Omitted settings stay put."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    body: dict[str, Any] = {}
    if name.strip():
        if len(name.strip()) > 100:
            return _error(f"name is {len(name.strip())} chars; Feishu allows at most 100.")
        if any(c in name for c in "?/\\*:[]"):
            return _error("name cannot contain ? / \\ * : [ ] (Feishu answers 1254031).")
        body["name"] = name.strip()
    advanced = is_advanced.strip().lower()
    if advanced:
        if advanced not in {"true", "false"}:
            return _error('is_advanced must be "true" or "false" (leave it empty to keep the current setting).')
        body["is_advanced"] = advanced == "true"
    if not body:
        return _error("Nothing to change — pass name and/or is_advanced.")
    res = await _invoke(
        _build_update_bitable_app_request(app_token.strip(), body),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        if str(res.get("code")) == "1254301":
            return {
                **res,
                "hint": "advanced permission cannot be enabled on a base that lives in a wiki or is "
                "embedded in a doc/sheet.",
            }
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    app = data.get("app", {}) if isinstance(data.get("app"), dict) else {}
    result: dict[str, Any] = {
        "ok": True,
        "app_token": app.get("app_token", "") or app_token.strip(),
        "name": app.get("name", body.get("name", "")),
        "changed": sorted(body),
    }
    if "is_advanced" in app or "is_advanced" in body:
        result["is_advanced"] = bool(app.get("is_advanced", body.get("is_advanced")))
    return result


def _build_copy_bitable_app_request(
    app_token: str, name: str, folder_token: str, without_content: bool, time_zone: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/copy"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {}
    if name:
        body["name"] = name
    if folder_token:
        body["folder_token"] = folder_token
    if without_content:
        body["without_content"] = True
    if time_zone:
        body["time_zone"] = time_zone
    req.body = body
    return req


async def copy_bitable_app_impl(
    app_token: str,
    name: str = "",
    folder_token: str = "",
    without_content: bool = False,
    time_zone: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Duplicate a whole base — the template move. without_content copies structure only."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    res = await _invoke(
        _build_copy_bitable_app_request(
            app_token.strip(), name.strip(), folder_token.strip(), without_content, time_zone.strip()
        ),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        if str(res.get("code")) == "1254036":
            return {**res, "hint": "this base is already being copied — wait a moment and retry."}
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    app = data.get("app", {}) if isinstance(data.get("app"), dict) else {}
    new_token = app.get("app_token", "")
    return {
        "ok": True,
        "app_token": new_token,
        "name": app.get("name", name.strip()),
        "folder_token": app.get("folder_token", ""),
        "time_zone": app.get("time_zone", ""),
        "url": app.get("url") or (f"{_DOC_BASE_URL}/base/{new_token}" if new_token else ""),
        "without_content": without_content,
    }


def _build_create_field_request(app_token: str, table_id: str, field: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = field
    return req


async def create_bitable_field_impl(
    app_token: str,
    table_id: str,
    field_name: str,
    field_type: int = 1,
    property_json: str = "",
    ui_type: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Add one field (column) to an existing table. property_json holds type-specific settings."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _error("No table_id provided (get it from feishu_bitable_list_tables).")
    field: dict[str, Any] = {"field_name": field_name.strip(), "type": field_type}
    problem = _validate_bitable_fields([field], as_table_fields=False)
    if problem:
        return _error(problem.replace("fields_json[0].", "").replace("fields_json[0]", "field"))
    if property_json.strip():
        try:
            prop = json.loads(property_json)
        except ValueError as exc:
            return _error(f"property_json is not valid JSON: {exc}")
        if not isinstance(prop, dict):
            return _error('property_json must be a JSON object, e.g. \'{"options":[{"name":"高","color":0}]}\'.')
        field["property"] = prop
    if ui_type.strip():
        field["ui_type"] = ui_type.strip()
    res = await _invoke(
        _build_create_field_request(app_token.strip(), table_id.strip(), field),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    created = data.get("field", {}) if isinstance(data.get("field"), dict) else {}
    ftype = created.get("type", field_type)
    return {
        "ok": True,
        "field_id": created.get("field_id", ""),
        "name": created.get("field_name", field["field_name"]),
        "type": _BITABLE_FIELD_TYPES.get(ftype, ftype),
        "is_primary": bool(created.get("is_primary")),
    }


def _build_update_field_request(app_token: str, table_id: str, field_id: str, field: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/bitable/v1/apps/:app_token/tables/:table_id/fields/:field_id"
    req.paths["app_token"] = app_token
    req.paths["table_id"] = table_id
    req.paths["field_id"] = field_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = field
    return req


async def update_bitable_field_impl(
    app_token: str,
    table_id: str,
    field_id: str,
    field_name: str = "",
    field_type: int = 0,
    property_json: str = "",
    ui_type: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Change a column's definition (rename, retype, edit its options). Keeps the column's data."""
    if not app_token.strip():
        return _error("No app_token provided (the segment in a feishu.cn/base/<app_token> URL).")
    if not table_id.strip():
        return _error("No table_id provided (get it from feishu_bitable_list_tables).")
    if not field_id.strip():
        return _error("No field_id provided (get it from feishu_bitable_list_fields).")
    # Feishu's update is a FULL replace of the field definition and demands both
    # field_name and type, so anything the caller left out is read back from the
    # table rather than silently reset to a default.
    current: dict[str, Any] = {}
    if not field_name.strip() or not field_type:
        listed = await _invoke(
            _build_list_fields_request(app_token.strip(), table_id.strip(), 100, ""), user_key=user_key
        )
        if not listed["ok"]:
            return _error(
                "field_name and field_type are both required (Feishu replaces the whole field "
                f"definition), and reading the current one failed: {listed.get('message', '')}"
            )
        data = listed["data"] if isinstance(listed["data"], dict) else {}
        for f in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if f.get("field_id") == field_id.strip():
                current = f
                break
        if not current:
            return _error(f"field_id {field_id.strip()!r} is not in this table — check feishu_bitable_list_fields.")
    name = field_name.strip() or str(current.get("field_name", ""))
    ftype = field_type or current.get("type", 0)
    if not name:
        return _error("No field_name available for this field; pass field_name explicitly.")
    if not isinstance(ftype, int) or not ftype:
        return _error("No field_type available for this field; pass field_type explicitly.")
    field: dict[str, Any] = {"field_name": name, "type": ftype}
    problem = _validate_bitable_fields([field], as_table_fields=False)
    if problem:
        return _error(
            problem.replace("fields_json[0].", "").replace("fields_json[0]", "field").replace("created", "updated")
        )
    if current.get("is_primary") and ftype not in _INDEX_FIELD_TYPES:
        return _error(
            f"this is the index (primary) column, so type {ftype} is not allowed; "
            f"it must be one of {sorted(_INDEX_FIELD_TYPES)} (Feishu answers 1254012)."
        )
    if property_json.strip():
        try:
            prop = json.loads(property_json)
        except ValueError as exc:
            return _error(f"property_json is not valid JSON: {exc}")
        if not isinstance(prop, dict):
            return _error('property_json must be a JSON object, e.g. \'{"options":[{"name":"高","color":0}]}\'.')
        field["property"] = prop
    elif current.get("property") and (field_type in (0, current.get("type"))):
        # Same type and no new property: carry the existing settings over, otherwise
        # this full-replace update would wipe the select options / number format.
        field["property"] = current["property"]
    if ui_type.strip():
        field["ui_type"] = ui_type.strip()
    res = await _invoke(
        _build_update_field_request(app_token.strip(), table_id.strip(), field_id.strip(), field),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    updated = data.get("field", {}) if isinstance(data.get("field"), dict) else {}
    new_type = updated.get("type", ftype)
    return {
        "ok": True,
        "field_id": updated.get("field_id", "") or field_id.strip(),
        "name": updated.get("field_name", name),
        "type": _BITABLE_FIELD_TYPES.get(new_type, new_type),
        "is_primary": bool(updated.get("is_primary") or current.get("is_primary")),
    }


# ── Attendance (考勤) — read clock-in/out results (read-only) ─────────────────
#
# Query attendance task results (who clocked in/out, when, where, and whether
# late/early/missing). Read-only — no proxy clock-in. Bot/tenant token works with
# the attendance:task:readonly scope; the app must be a Custom App and be granted
# a data-permission scope in the attendance admin console.


def _fmt_check_time(rec: Any) -> str:
    """Format a check record's check_time (epoch seconds string) to 'YYYY-MM-DD HH:MM:SS'."""
    if not isinstance(rec, dict):
        return ""
    ts = rec.get("check_time")
    if not ts:
        return ""
    import datetime  # noqa: PLC0415

    with contextlib.suppress(ValueError, OSError, OverflowError):
        return datetime.datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M:%S")
    return str(ts)


def _build_user_tasks_query_request(
    user_ids: list[str], check_date_from: int, check_date_to: int, employee_type: str, need_overtime: bool
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/attendance/v1/user_tasks/query"
    req.add_query("employee_type", employee_type)
    req.add_query("ignore_invalid_users", "true")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {
        "user_ids": user_ids,
        "check_date_from": check_date_from,
        "check_date_to": check_date_to,
        "need_overtime_result": need_overtime,
    }
    return req


async def query_attendance_impl(
    user_ids: str,
    date_from: str,
    date_to: str,
    employee_type: str = "employee_id",
    need_overtime: bool = False,
) -> dict[str, Any]:
    """Query attendance clock results for users over a date range (read-only)."""
    ids = [u.strip() for u in user_ids.split(",") if u.strip()]
    if not ids:
        return _error("No user_ids provided (comma-separated, max 50).")
    try:
        df = int(date_from.strip())
        dt = int(date_to.strip())
    except ValueError:
        return _error("date_from / date_to must be yyyyMMdd integers, e.g. 20260714.")
    res = await _invoke(_build_user_tasks_query_request(ids, df, dt, employee_type, need_overtime))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    results = []
    for r in data.get("user_task_results", []) if isinstance(data.get("user_task_results"), list) else []:
        # Each user_task_result has a "records" array with per-shift check-in/out
        records = r.get("records", []) if isinstance(r.get("records"), list) else []
        for rec in records:
            cin = rec.get("check_in_record", {}) if isinstance(rec.get("check_in_record"), dict) else {}
            cout = rec.get("check_out_record", {}) if isinstance(rec.get("check_out_record"), dict) else {}
            results.append(
                {
                    "user_id": r.get("user_id", ""),
                    "name": r.get("employee_name", ""),
                    "day": r.get("day", ""),
                    "check_in_time": _fmt_check_time(cin),
                    "check_in_result": rec.get("check_in_result", ""),
                    "check_in_location": cin.get("location_name", ""),
                    "check_out_time": _fmt_check_time(cout),
                    "check_out_result": rec.get("check_out_result", ""),
                    "check_out_location": cout.get("location_name", ""),
                }
            )
    return {
        "ok": True,
        "results": results,
        "count": len(results),
        "invalid_user_ids": data.get("invalid_user_ids", []),
        "unauthorized_user_ids": data.get("unauthorized_user_ids", []),
    }


# ── Attendance admin config — groups (考勤组) & shifts (班次), read-only ────────
#
# The user_tasks/query API above only tells you *who clocked in/out*. The admin
# console config — which shift someone is on, the punch time segments, the
# flexible/late/early rules, the punch method, and the schedule — lives in two
# separate read-only APIs: attendance groups (考勤组) and shifts (班次). Both work
# with the bot's tenant token given attendance:task:readonly + a data-permission
# scope in the attendance admin console. list endpoints return only id+name, so
# fetch the detail endpoint for the full rule set.


def _build_list_attendance_groups_request(page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/attendance/v1/groups"
    req.add_query("page_size", max(1, min(page_size, 50)))
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_attendance_groups_impl(page_size: int = 50, page_token: str = "") -> dict[str, Any]:
    """List attendance groups (考勤组) the app can see — id + name only (read-only)."""
    res = await _invoke(_build_list_attendance_groups_request(page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    groups = [
        {"group_id": g.get("group_id", ""), "group_name": g.get("group_name", "")}
        for g in (data.get("group_list") or [])
        if isinstance(g, dict)
    ]
    return {
        "ok": True,
        "groups": groups,
        "count": len(groups),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


_GROUP_CONFIG_FIELDS = (
    "group_id",
    "group_name",
    "group_type",  # 0 fixed shift, 2 scheduled, 3 free/flexible
    "punch_type",  # bitwise: 1 GPS, 2 Wi-Fi, 4 machine, 8 IP
    "allow_out_punch",
    "out_punch_need_approval",
    "out_punch_need_photo",
    "allow_pc_punch",
    "work_day_no_punch_as_lack",
    "punch_day_shift_ids",  # bound shift ids (fixed-shift groups)
    "free_punch_cfg",  # free/flexible-mode window
    "free_clock_setting",
    "overtime_clock_cfg",
    "need_punch_special_days",  # extra dates requiring punch + their shift
    "no_need_punch_special_days",
    "calendar_id",
    "new_calendar_id",
    "bind_default_dept_ids",
    "bind_default_user_ids",
)


def _build_get_attendance_group_request(group_id: str, employee_type: str, dept_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/attendance/v1/groups/:group_id"
    req.paths["group_id"] = group_id
    req.add_query("employee_type", employee_type)
    req.add_query("dept_type", dept_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def get_attendance_group_impl(
    group_id: str, employee_type: str = "employee_id", dept_type: str = "open_id"
) -> dict[str, Any]:
    """Get one attendance group's full config (考勤组配置) — punch method, 外勤/PC
    打卡, 缺卡规则, 绑定班次, 排班特殊日期 (read-only)."""
    gid = group_id.strip()
    if not gid:
        return _error("group_id is required (get it from feishu_attendance_groups).")
    res = await _invoke(_build_get_attendance_group_request(gid, employee_type, dept_type))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    group = {k: data.get(k) for k in _GROUP_CONFIG_FIELDS if k in data}
    return {"ok": True, "group": group}


def _build_list_shifts_request(page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/attendance/v1/shifts"
    req.add_query("page_size", max(1, min(page_size, 50)))
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_shifts_impl(page_size: int = 50, page_token: str = "") -> dict[str, Any]:
    """List attendance shifts (班次) the app can see — id + name + punch count (read-only)."""
    res = await _invoke(_build_list_shifts_request(page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    shifts = [
        {
            "shift_id": s.get("shift_id", ""),
            "shift_name": s.get("shift_name", ""),
            "punch_times": s.get("punch_times"),
            "is_flexible": s.get("is_flexible"),
        }
        for s in (data.get("shift_list") or [])
        if isinstance(s, dict)
    ]
    return {
        "ok": True,
        "shifts": shifts,
        "count": len(shifts),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


_SHIFT_CONFIG_FIELDS = (
    "shift_id",
    "shift_name",
    "punch_times",
    "day_type",  # 1 workday, 2 rest day
    "is_flexible",
    "flexible_minutes",
    "flexible_rule",  # [{flexible_early_minutes, flexible_late_minutes}]
    "no_need_off",
    "punch_time_rule",  # 打卡时间段: on_time/off_time + late/early thresholds
    "late_off_late_on_rule",
    "rest_time_rule",
    "overtime_rule",
    "overtime_rest_time_rule",
    "shift_middle_time_rule",
    "late_off_late_on_setting",
    "late_minutes_as_serious_late",
)


def _build_get_shift_request(shift_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/attendance/v1/shifts/:shift_id"
    req.paths["shift_id"] = shift_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def get_shift_impl(shift_id: str) -> dict[str, Any]:
    """Get one shift's full config (班次配置) — 打卡时间段 (punch_time_rule), 弹性规则
    (flexible_rule/is_flexible), 迟到/早退/缺卡阈值, 休息时段 (read-only)."""
    sid = shift_id.strip()
    if not sid:
        return _error("shift_id is required (get it from feishu_attendance_shifts).")
    res = await _invoke(_build_get_shift_request(sid))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    shift = {k: data.get(k) for k in _SHIFT_CONFIG_FIELDS if k in data}
    return {"ok": True, "shift": shift}


# ── Tasks (任务 v2) — create/assign, list, update, complete ───────────────────
#
# Feishu native tasks: assign work to people with a due date, list, and mark
# done. Bot/tenant token works (task:task:write). Note: list returns "my_tasks"
# = tasks the CALLING identity (the bot) is responsible for — not an arbitrary
# person's tasks (that would need that user's OAuth).


def _due_to_ms(due: str) -> str | None:
    """Parse 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' to a ms-epoch string, or None if empty/invalid."""
    s = due.strip()
    if not s:
        return None
    import datetime  # noqa: PLC0415

    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        with contextlib.suppress(ValueError):
            dt = datetime.datetime.strptime(s, fmt)
            return str(int(dt.timestamp() * 1000))
    return None


def _build_create_task_request(body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/task/v2/tasks"
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = body
    return req


async def create_task_impl(
    summary: str, description: str, due: str, assignees: str, followers: str, user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Create a task, optionally with a due date and assignee/follower open_ids."""
    if not summary.strip():
        return _error("Task summary is required.")
    # Feishu member object: type is the member KIND ("user"/"app"), id_type is the
    # ID form (open_id/user_id). (Not type="open_id" — that's rejected as 1470400.)
    members: list[dict[str, str]] = []
    for oid in (a.strip() for a in assignees.split(",")):
        if oid:
            members.append({"id": oid, "type": "user", "id_type": "open_id", "role": "assignee"})
    for oid in (f.strip() for f in followers.split(",")):
        if oid:
            members.append({"id": oid, "type": "user", "id_type": "open_id", "role": "follower"})
    body: dict[str, Any] = {"summary": summary}
    if description.strip():
        body["description"] = description
    due_ms = _due_to_ms(due)
    if due_ms:
        body["due"] = {"timestamp": due_ms, "is_all_day": False}
    if members:
        body["members"] = members
    res = await _invoke(_build_create_task_request(body), user_key=user_key, prefer="user", identity=identity)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    task = data.get("task", {}) if isinstance(data.get("task"), dict) else {}
    return {
        "ok": True,
        "task_guid": task.get("guid", ""),
        "summary": task.get("summary", ""),
        "url": task.get("url", ""),
    }


def _build_list_tasks_request(completed: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/task/v2/tasks"
    req.add_query("page_size", page_size)
    req.add_query("type", "my_tasks")
    req.add_query("user_id_type", "open_id")
    if completed in ("true", "false"):
        req.add_query("completed", completed)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_tasks_impl(completed: str = "", page_size: int = 50, page_token: str = "") -> dict[str, Any]:
    """List the calling identity's (bot's) tasks. completed '' = all, 'true'/'false' to filter."""
    res = await _invoke(_build_list_tasks_request(completed, page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    tasks = [
        {
            "guid": t.get("guid", ""),
            "summary": t.get("summary", ""),
            "status": t.get("status", ""),
            "due": (t.get("due") or {}).get("timestamp", "") if isinstance(t.get("due"), dict) else "",
            "url": t.get("url", ""),
        }
        for t in (data.get("items", []) if isinstance(data.get("items"), list) else [])
    ]
    return {
        "ok": True,
        "tasks": tasks,
        "count": len(tasks),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


def _build_patch_task_request(task_guid: str, task_fields: dict[str, Any], update_fields: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/task/v2/tasks/:task_guid"
    req.paths["task_guid"] = task_guid
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"task": task_fields, "update_fields": update_fields}
    return req


async def update_task_impl(
    task_guid: str, summary: str, description: str, due: str, user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Update only the provided (non-empty) fields of a task."""
    task_fields: dict[str, Any] = {}
    update_fields: list[str] = []
    if summary.strip():
        task_fields["summary"] = summary
        update_fields.append("summary")
    if description.strip():
        task_fields["description"] = description
        update_fields.append("description")
    due_ms = _due_to_ms(due)
    if due_ms:
        task_fields["due"] = {"timestamp": due_ms, "is_all_day": False}
        update_fields.append("due")
    if not update_fields:
        return _error("Nothing to update: provide summary, description, or due.")
    res = await _invoke(
        _build_patch_task_request(task_guid, task_fields, update_fields),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    return {"ok": True, "task_guid": task_guid, "updated": update_fields}


async def complete_task_impl(task_guid: str, completed: bool, user_key: str = "", identity: str = "") -> dict[str, Any]:
    """Mark a task complete (completed=True) or reopen it (False)."""
    import time  # noqa: PLC0415

    ts = str(int(time.time() * 1000)) if completed else "0"
    res = await _invoke(
        _build_patch_task_request(task_guid, {"completed_at": ts}, ["completed_at"]),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    return {"ok": True, "task_guid": task_guid, "completed": completed}


def _build_get_task_request(task_guid: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/task/v2/tasks/:task_guid"
    req.paths["task_guid"] = task_guid
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def get_task_impl(task_guid: str) -> dict[str, Any]:
    """Get a task's detail incl. completion status and per-assignee completion.

    Works for any task the calling identity (bot) can read — e.g. a task the bot
    created and assigned to someone; lets you check whether that person finished it.
    """
    res = await _invoke(_build_get_task_request(task_guid))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    task = data.get("task", {}) if isinstance(data.get("task"), dict) else {}
    members = [
        {"id": m.get("id", ""), "name": m.get("name", ""), "role": m.get("role", "")}
        for m in (task.get("members", []) if isinstance(task.get("members"), list) else [])
    ]
    per_assignee = [
        {"id": a.get("id", ""), "completed_at": _fmt_ms(a.get("completed_at"))}
        for a in (task.get("assignee_related", []) if isinstance(task.get("assignee_related"), list) else [])
    ]
    return {
        "ok": True,
        "task_guid": task.get("guid", task_guid),
        "summary": task.get("summary", ""),
        "status": task.get("status", ""),
        "completed": task.get("status") == "done" or bool(task.get("completed_at")),
        "completed_at": _fmt_ms(task.get("completed_at")),
        "members": members,
        "assignee_completion": per_assignee,
        "url": task.get("url", ""),
    }


def _fmt_ms(ms: Any) -> str:
    """Format a ms-epoch value (str/int) to 'YYYY-MM-DD HH:MM:SS', or '' if empty/0."""
    if not ms or str(ms) == "0":
        return ""
    import datetime  # noqa: PLC0415

    with contextlib.suppress(ValueError, OSError, OverflowError):
        return datetime.datetime.fromtimestamp(int(ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    return str(ms)


# ── Calendar (日历) — create an event on the bot's primary calendar ───────────
#
# The bot creates events on its own primary calendar (auto-resolved). Bot/tenant
# token works (calendar:calendar), but the app must have bot ability enabled
# (else 190007). Attendees are added via a second call.

_primary_calendar_id: str | None = None


async def _get_primary_calendar_id() -> str | None:
    """Resolve (and cache) the bot's primary calendar_id, or None on failure."""
    global _primary_calendar_id
    if _primary_calendar_id:
        return _primary_calendar_id
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/calendar/v4/calendars/primary"
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    res = await _invoke(req)
    if not res["ok"]:
        return None
    data = res["data"] if isinstance(res["data"], dict) else {}
    for item in data.get("calendars", []) if isinstance(data.get("calendars"), list) else []:
        cal = item.get("calendar", {}) if isinstance(item, dict) else {}
        cid = cal.get("calendar_id", "")
        if cid:
            _primary_calendar_id = cid
            return cid
    return None


def _time_to_info(t: str, timezone: str) -> dict[str, str] | None:
    """Parse 'YYYY-MM-DD HH:MM' -> timed {timestamp, timezone}; 'YYYY-MM-DD' -> all-day {date, timezone}."""
    s = t.strip()
    if not s:
        return None
    import datetime  # noqa: PLC0415

    with contextlib.suppress(ValueError):
        dt = datetime.datetime.strptime(s, "%Y-%m-%d %H:%M")
        return {"timestamp": str(int(dt.timestamp())), "timezone": timezone}
    with contextlib.suppress(ValueError):
        datetime.datetime.strptime(s, "%Y-%m-%d")
        return {"date": s, "timezone": timezone}
    return None


def _build_create_event_request(calendar_id: str, body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/calendar/v4/calendars/:calendar_id/events"
    req.paths["calendar_id"] = calendar_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = body
    return req


def _build_add_attendees_request(calendar_id: str, event_id: str, open_ids: list[str]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/calendar/v4/calendars/:calendar_id/events/:event_id/attendees"
    req.paths["calendar_id"] = calendar_id
    req.paths["event_id"] = event_id
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"attendees": [{"type": "user", "user_id": oid} for oid in open_ids]}
    return req


async def create_event_impl(
    summary: str, start: str, end: str, description: str = "", attendees: str = "", timezone: str = "Asia/Shanghai"
) -> dict[str, Any]:
    """Create a calendar event on the bot's primary calendar, optionally adding attendees."""
    start_info = _time_to_info(start, timezone)
    end_info = _time_to_info(end, timezone)
    if start_info is None or end_info is None:
        return _error("start/end must be 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'.")
    calendar_id = await _get_primary_calendar_id()
    if not calendar_id:
        return _error(
            "Could not resolve the bot's primary calendar. Ensure bot ability is enabled and calendar scope granted."
        )
    body: dict[str, Any] = {"summary": summary, "start_time": start_info, "end_time": end_info}
    if description.strip():
        body["description"] = description
    res = await _invoke(_build_create_event_request(calendar_id, body))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    event = data.get("event", {}) if isinstance(data.get("event"), dict) else {}
    event_id = event.get("event_id", "")
    result: dict[str, Any] = {
        "ok": True,
        "event_id": event_id,
        "calendar_id": calendar_id,
        "summary": event.get("summary", summary),
        "start": start,
        "end": end,
    }
    open_ids = [a.strip() for a in attendees.split(",") if a.strip()]
    if open_ids and event_id:
        att_res = await _invoke(_build_add_attendees_request(calendar_id, event_id, open_ids))
        if att_res["ok"]:
            result["attendees_added"] = open_ids
        else:
            result["attendee_warning"] = att_res.get("message", "failed to add attendees")
    return result


# ── Calendar (日历) — list events on a calendar over a time range ─────────────
#
# Read the schedule of a calendar (the bot's primary one by default) between two
# instants. Reading someone else's calendar needs the identity to have reader
# access to it; scope calendar:calendar or calendar:calendar.event:read.


def _ts_of(t: str, timezone: str) -> str | None:
    """Parse 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD' (00:00 that day) to a Unix-second string."""
    info = _time_to_info(t, timezone)
    if info is None:
        return None
    if "timestamp" in info:
        return info["timestamp"]
    import datetime  # noqa: PLC0415

    with contextlib.suppress(ValueError):
        dt = datetime.datetime.strptime(info["date"], "%Y-%m-%d")
        return str(int(dt.timestamp()))
    return None


def _build_list_events_request(
    calendar_id: str, start_ts: str, end_ts: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/calendar/v4/calendars/:calendar_id/events"
    req.paths["calendar_id"] = calendar_id
    req.add_query("start_time", start_ts)
    req.add_query("end_time", end_ts)
    req.add_query("page_size", page_size)
    req.add_query("user_id_type", "open_id")
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _event_time_str(t: Any) -> str:
    """Normalize a calendar event start/end object to a readable string."""
    if not isinstance(t, dict):
        return ""
    if t.get("timestamp"):
        return _fmt_ms(str(int(t["timestamp"]) * 1000)) if str(t["timestamp"]).isdigit() else str(t["timestamp"])
    return str(t.get("date", ""))


def _normalize_event(ev: dict[str, Any]) -> dict[str, Any]:
    organizer = ev.get("organizer_calendar_id", "") or ev.get("event_organizer", {}).get("display_name", "")
    attendee_ability = ev.get("attendee_ability", "")
    start = ev.get("start_time", {})
    return {
        "event_id": ev.get("event_id", ""),
        "summary": ev.get("summary", ""),
        "description": ev.get("description", ""),
        "start": _event_time_str(ev.get("start_time", {})),
        "end": _event_time_str(ev.get("end_time", {})),
        "status": ev.get("status", ""),
        "is_all_day": isinstance(start, dict) and "date" in start and "timestamp" not in start,
        "organizer": organizer,
        "attendee_ability": attendee_ability,
    }


async def list_events_impl(
    start: str, end: str, calendar_id: str = "", timezone: str = "Asia/Shanghai", max_events: int = 50
) -> dict[str, Any]:
    """List events on a calendar between start and end. Blank calendar_id uses the bot's primary calendar."""
    start_ts = _ts_of(start, timezone)
    end_ts = _ts_of(end, timezone)
    if start_ts is None or end_ts is None:
        return _error("start/end must be 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DD'.")
    cal_id = calendar_id.strip() or await _get_primary_calendar_id()
    if not cal_id:
        return _error("Could not resolve a calendar_id. Pass one, or ensure the bot's primary calendar is available.")
    events: list[dict[str, Any]] = []
    page_token = ""
    while len(events) < max_events:
        page_size = min(1000, max(50, max_events - len(events)))
        res = await _invoke(_build_list_events_request(cal_id, start_ts, end_ts, page_size, page_token))
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for ev in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if isinstance(ev, dict):
                events.append(_normalize_event(ev))
            if len(events) >= max_events:
                break
        page_token = data.get("page_token", "") if data.get("has_more") else ""
        if not page_token:
            break
    return {"ok": True, "calendar_id": cal_id, "count": len(events), "events": events}


# ── Calendar (日历) — create a separate event for each person ─────────────────
#
# For "give each person their own schedule": create one independent event per
# attendee on the bot's primary calendar, each inviting only that one person.
# Partial failures are reported per person rather than crashing the batch.


async def create_events_per_person_impl(
    summary: str,
    start: str,
    end: str,
    attendees: str,
    description: str = "",
    timezone: str = "Asia/Shanghai",
) -> dict[str, Any]:
    """Create one independent event per open_id, each inviting only that person."""
    open_ids = [a.strip() for a in attendees.split(",") if a.strip()]
    if not open_ids:
        return _error("attendees must contain at least one comma-separated open_id.")
    created: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for oid in open_ids:
        res = await create_event_impl(summary, start, end, description, oid, timezone)
        if res.get("ok") and not res.get("attendee_warning"):
            created.append({"open_id": oid, "event_id": res.get("event_id", "")})
        else:
            failed.append({"open_id": oid, "error": res.get("attendee_warning") or res.get("message", "failed")})
    return {"ok": not failed, "count": len(created), "created": created, "failed": failed}


# ── Contact (通讯录) — list department members ────────────────────────────────
#
# Get the roster for a department (or the whole org from root id "0"), so the
# agent has the user_id list needed to batch-query attendance/payroll. Tenant
# token works; the app's 通讯录权限范围 must cover the members you want to see.


def _build_dept_children_request(
    department_id: str, department_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/departments/:department_id/children"
    req.paths["department_id"] = department_id
    req.add_query("department_id_type", department_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_find_by_department_request(
    department_id: str, department_id_type: str, user_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/users/find_by_department"
    req.add_query("department_id", department_id)
    req.add_query("department_id_type", department_id_type)
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _members_of_department(
    department_id: str, department_id_type: str, user_id_type: str
) -> tuple[list[dict[str, str]], dict[str, Any] | None]:
    """All members directly in one department (paged). Returns (members, error_or_None)."""
    members: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _invoke(
            _build_find_by_department_request(department_id, department_id_type, user_id_type, 50, page_token)
        )
        if not res["ok"]:
            return members, res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            members.append(
                {
                    "user_id": it.get("user_id", ""),
                    "open_id": it.get("open_id", ""),
                    "name": it.get("name", ""),
                }
            )
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return members, None


async def _child_department_ids(department_id: str, department_id_type: str) -> list[str]:
    """Direct child department ids of a department (one level, paged)."""
    ids: list[str] = []
    page_token = ""
    while True:
        res = await _invoke(_build_dept_children_request(department_id, department_id_type, 50, page_token))
        if not res["ok"]:
            return ids
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            did = (
                it.get("department_id", "")
                if department_id_type == "department_id"
                else it.get("open_department_id", "")
            )
            if did:
                ids.append(did)
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return ids


async def list_department_members_impl(
    department_id: str = "0",
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    recursive: bool = False,
) -> dict[str, Any]:
    """List members of a department. recursive=True walks sub-departments too.

    department_id "0" is the org root. Returns de-duplicated [{user_id, open_id, name}].
    """
    seen: set[str] = set()
    all_members: list[dict[str, str]] = []
    to_visit = [department_id]
    visited: set[str] = set()
    while to_visit:
        did = to_visit.pop()
        if did in visited:
            continue
        visited.add(did)
        members, err = await _members_of_department(did, department_id_type, user_id_type)
        if err is not None:
            return err
        for m in members:
            key = m.get("open_id") or m.get("user_id") or m.get("name")
            if key and key not in seen:
                seen.add(key)
                all_members.append(m)
        if recursive:
            child_type = "department_id" if department_id_type == "department_id" else "open_department_id"
            to_visit.extend(await _child_department_ids(did, child_type))
    return {
        "ok": True,
        "department_id": department_id,
        "recursive": recursive,
        "members": all_members,
        "count": len(all_members),
    }


# ── Contact — batch user detail (contact info: mobile / email / job title) ─────
#
# find_by_department only gives name + ids. To hand someone a colleague's contact
# details (so an employee stuck on a blocker can reach the right owner), fetch the
# full user records via the batch endpoint: mobile, email, job title, department.
# Tenant token works; the app's 通讯录权限范围 must cover the users, and reading
# mobile/email needs the corresponding contact scopes (see feishu_contact tool).


def _build_batch_users_request(user_ids: list[str], user_id_type: str, department_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/users/batch"
    for uid in user_ids:
        req.add_query("user_ids", uid)
    req.add_query("user_id_type", user_id_type)
    req.add_query("department_id_type", department_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def get_users_batch_impl(
    user_ids: str,
    user_id_type: str = "open_id",
    department_id_type: str = "open_department_id",
) -> dict[str, Any]:
    """Fetch full user records (contact details) for up to 50 ids in one call.

    Returns [{open_id, user_id, name, mobile, email, enterprise_email, job_title,
    department_ids, leader_user_id}] — the info needed to hand someone a colleague's
    contact details. mobile/email are only populated if the app has the matching
    contact scopes and 通讯录权限范围 covers the user.
    """
    ids = [uid.strip() for uid in user_ids.split(",") if uid.strip()]
    if not ids:
        return _error("user_ids is required (comma-separated ids).")
    if len(ids) > 50:
        return _error("Feishu allows at most 50 user_ids per batch call.")
    res = await _invoke(_build_batch_users_request(ids, user_id_type, department_id_type))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    users: list[dict[str, Any]] = []
    for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
        users.append(
            {
                "open_id": it.get("open_id", ""),
                "user_id": it.get("user_id", ""),
                "name": it.get("name", ""),
                "mobile": it.get("mobile", ""),
                "email": it.get("email", ""),
                "enterprise_email": it.get("enterprise_email", ""),
                "job_title": it.get("job_title", ""),
                "department_ids": it.get("department_ids", []),
                "leader_user_id": it.get("leader_user_id", ""),
            }
        )
    return {"ok": True, "user_id_type": user_id_type, "users": users, "count": len(users)}


# ── Contact — global user search by name (search/v1/user) ─────────────────────
#
# The one way to resolve a person by name WITHOUT already knowing which group or
# department they're in. feishu_chat_find_member only searches a known group's
# roster; department_members needs a department id. This searches the whole org
# by name keyword. Feishu only allows this via a user_access_token (the bot's
# tenant token can't call it), so it follows the same auth flow as doc search:
# the caller must have authorized once (feishu_auth_start / _complete).


def _build_search_user_request(query: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/search/v1/user"
    req.add_query("query", query)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.USER}
    return req


async def search_users_impl(
    query: str, page_size: int = 20, page_token: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Search all users in the org by name keyword (needs a user_access_token).

    Unlike find_member (group roster) or department_members (a department), this
    matches any user across the whole organization by name — no chat_id/department
    needed. Returns [{open_id, user_id, name, avatar, department_ids}].
    """
    query = (query or "").strip()
    if not query:
        return _error("query is required (a name or name keyword to search for).")
    if page_size < 1 or page_size > 200:
        return _error("page_size must be between 1 and 200.")

    client = _get_uat_client()
    if client is None:
        return _error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    uat = await _get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return _error(_AUTH_PROMPT, need_auth=True, need_capabilities=["contact_read"])

    req = _build_search_user_request(query, page_size, page_token)
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(req, option)
    except Exception as exc:  # SDK/transport failure
        return _error(f"Feishu user search failed: {type(exc).__name__}: {exc}")

    body = _parse_resp_body(resp)
    if body.get("code") not in (0, None):
        return {
            "ok": False,
            "code": body.get("code"),
            "msg": body.get("msg", ""),
            "message": f"Feishu API error {body.get('code')}: {body.get('msg', '')}",
        }
    data = body.get("data", {}) if isinstance(body.get("data"), dict) else {}
    users = [
        {
            "open_id": u.get("open_id", ""),
            "user_id": u.get("user_id", ""),
            "name": u.get("name", ""),
            "avatar": (u.get("avatar") or {}).get("avatar_240", "") if isinstance(u.get("avatar"), dict) else "",
            "department_ids": u.get("department_ids", []),
        }
        for u in (data.get("users", []) if isinstance(data.get("users"), list) else [])
        if isinstance(u, dict)
    ]
    return {
        "ok": True,
        "query": query,
        "users": users,
        "count": len(users),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


# ── Drive — download a file/attachment to disk ────────────────────────────────
#
# Two sources: a drive media file_token (goes through the medias endpoint), or a
# direct URL (approval-form attachments are direct URLs valid only ~12h — download
# them straight, NOT via medias).


def _build_media_download_request(file_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/drive/v1/medias/:file_token/download"
    req.paths["file_token"] = file_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _download_url_bytes(url: str) -> tuple[bytes | None, str]:
    import httpx  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            resp = await client.get(url)
    except Exception as exc:  # transport failure
        return None, f"{type(exc).__name__}: {exc}"
    if resp.status_code in (403, 404):
        return None, (
            f"HTTP {resp.status_code} — the attachment link may have expired "
            "(approval-form URLs are valid ~12h). Re-read the instance detail for a fresh URL."
        )
    if resp.status_code >= 400:
        return None, f"HTTP {resp.status_code}"
    return resp.content, ""


def _media_resp_to_bytes(resp: Any) -> tuple[bytes | None, str]:
    """Extract file bytes from a media-download response, or an (err) if it failed."""
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if not content:
        code = getattr(resp, "code", None)
        return None, f"no file content returned (code={code})"
    data = bytes(content)
    # A JSON error body (not a binary file) means the token was rejected.
    if data[:1] in (b"{", b"["):
        with contextlib.suppress(ValueError, UnicodeDecodeError):
            body = json.loads(data.decode("utf-8"))
            if isinstance(body, dict) and body.get("code") not in (0, None):
                return None, f"Feishu API error {body.get('code')}: {body.get('msg', '')}"
    return data, ""


async def _download_media_as_tenant(file_token: str) -> tuple[bytes | None, str]:
    client = _get_client()
    if client is None:
        return None, "Feishu app not configured."
    try:
        resp = await client.arequest(_build_media_download_request(file_token))
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    return _media_resp_to_bytes(resp)


async def _download_media_as_user(file_token: str, user_key: str) -> tuple[bytes | None, str] | None:
    """Download as the user's UAT. None → no usable UAT (caller decides need_auth)."""
    client = _get_uat_client()
    if client is None:
        return None
    uat = await _get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return None
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(_build_media_download_request(file_token), option)
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    return _media_resp_to_bytes(resp)


async def _download_media_bytes(file_token: str, user_key: str = "") -> tuple[bytes | None, str]:
    # Tenant-first: try the bot's token, and only if it's denied (and the user has a
    # cached UAT) retry as the user — so we still fetch files the user can see but the
    # bot can't (e.g. a PDF in the user's wiki/drive) without forcing authorization.
    data, err = await _download_media_as_tenant(file_token)
    if data is not None:
        return data, ""
    key = user_key.strip()
    if not key:
        return None, err
    user_out = await _download_media_as_user(file_token, key)
    if user_out is None:
        return None, f"{err} — 或需用户授权后重试. (need_auth)"
    return user_out


async def download_file_impl(source: str, save_path: str, is_url: bool = False, user_key: str = "") -> dict[str, Any]:
    """Download a Feishu file to disk. is_url=True treats source as a direct URL, else a media file_token.

    Pass ``user_key`` (only used when is_url=False) to download as that user — needed for
    files the user can see but the bot can't (e.g. a PDF in the user's wiki/drive).
    """
    if not source or not save_path:
        return _error("source and save_path are required.")
    data, err = await (_download_url_bytes(source) if is_url else _download_media_bytes(source, user_key))
    if data is None:
        extra = {"need_auth": True} if "need_auth" in (err or "") else {}
        return _error(err or "download failed", source=source, **extra)
    path = pathlib.Path(save_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await anyio.Path(path).write_bytes(data)
    except OSError as exc:
        return _error(f"could not write file: {exc}", path=str(path))
    return {"ok": True, "path": str(path), "bytes": len(data)}


# ── Message resources — download an image / file attached to a chat message ────
#
# Distinct from the drive medias endpoint above: images and files sent *inside a
# chat message* are fetched via im/v1/messages/:message_id/resources/:file_key,
# keyed by the message they belong to. The channel auto-downloads resources on the
# message that is triggering the agent right now, but an image discovered later in
# history (via feishu_message_list / feishu_thread_read) can only be pulled with
# this endpoint. The file_key is the ``image_key``/``file_key`` inside the
# message's content JSON; ``type`` is "image" for an image message, "file" for a
# file/audio/video/media attachment.


def _build_message_resource_request(message_id: str, file_key: str, resource_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages/:message_id/resources/:file_key"
    req.paths["message_id"] = message_id
    req.paths["file_key"] = file_key
    req.add_query("type", resource_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _download_msg_resource_as_tenant(
    message_id: str, file_key: str, resource_type: str
) -> tuple[bytes | None, str]:
    client = _get_client()
    if client is None:
        return None, "Feishu app not configured."
    try:
        resp = await client.arequest(_build_message_resource_request(message_id, file_key, resource_type))
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    return _media_resp_to_bytes(resp)


async def _download_msg_resource_as_user(
    message_id: str, file_key: str, resource_type: str, user_key: str
) -> tuple[bytes | None, str] | None:
    """Download as the user's UAT. None → no usable UAT (caller decides need_auth)."""
    client = _get_uat_client()
    if client is None:
        return None
    uat = await _get_valid_uat(user_key)
    if uat is None or not uat.access_token:
        return None
    from lark_channel.core.model import RequestOption  # noqa: PLC0415

    option = RequestOption.builder().user_access_token(uat.access_token).build()
    try:
        resp = await client.arequest(_build_message_resource_request(message_id, file_key, resource_type), option)
    except Exception as exc:  # SDK/transport failure
        return None, f"{type(exc).__name__}: {exc}"
    return _media_resp_to_bytes(resp)


async def _download_msg_resource_bytes(
    message_id: str, file_key: str, resource_type: str, user_key: str = ""
) -> tuple[bytes | None, str]:
    # Tenant-first, same policy as _download_media_bytes: the bot's token is tried
    # first and the UAT only if it's denied (and the user has a cached UAT).
    data, err = await _download_msg_resource_as_tenant(message_id, file_key, resource_type)
    if data is not None:
        return data, ""
    key = user_key.strip()
    if not key:
        return None, err
    user_out = await _download_msg_resource_as_user(message_id, file_key, resource_type, key)
    if user_out is None:
        return None, f"{err} — 或需用户授权后重试. (need_auth)"
    return user_out


async def get_message_image_impl(
    message_id: str, file_key: str, save_path: str, resource_type: str = "image", user_key: str = ""
) -> dict[str, Any]:
    """Download an image/file attached to a chat message to disk.

    Fetches via im/v1/messages/:message_id/resources/:file_key. ``file_key`` is the
    ``image_key`` (image message) or ``file_key`` (file/media message) inside the
    message content JSON. Tenant-first; falls back to the user's UAT when the bot
    can't see it and a user_key is given.
    """
    if not message_id or not file_key or not save_path:
        return _error("message_id, file_key and save_path are required.")
    rtype = resource_type.strip() or "image"
    data, err = await _download_msg_resource_bytes(message_id, file_key, rtype, user_key)
    if data is None:
        extra = {"need_auth": True} if "need_auth" in (err or "") else {}
        return _error(err or "download failed", message_id=message_id, file_key=file_key, **extra)
    path = pathlib.Path(save_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        await anyio.Path(path).write_bytes(data)
    except OSError as exc:
        return _error(f"could not write file: {exc}", path=str(path))
    return {"ok": True, "path": str(path), "bytes": len(data)}


# ── Delete a cloud file / document (to trash) ─────────────────────────────────
#
# DELETE /drive/v1/files/:file_token?type=... moves the file to the recycle bin
# (recoverable). Works with tenant OR user token; deleting inside a user-owned
# wiki needs the user's UAT (pass user_key). To delete a *wiki* doc: resolve the
# node with get_wiki_node_impl → obj_token/obj_type, then delete that.

_DELETABLE_FILE_TYPES = {"file", "docx", "doc", "sheet", "bitable", "mindnote", "slides", "folder", "shortcut"}


def _build_delete_file_request(file_token: str, file_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/drive/v1/files/:file_token"
    req.paths["file_token"] = file_token
    req.add_query("type", file_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def delete_file_impl(file_token: str, file_type: str, user_key: str = "", identity: str = "") -> dict[str, Any]:
    """Delete a cloud file/document (moves it to the recycle bin — recoverable).

    Pass ``user_key`` to delete as that user (required when the file/wiki is owned by
    the user and the bot isn't a collaborator); empty uses the bot's tenant token.
    """
    token = file_token.strip()
    if not token:
        return _error("file_token is required.")
    ftype = file_type.strip()
    if ftype not in _DELETABLE_FILE_TYPES:
        return _error(f"file_type must be one of {sorted(_DELETABLE_FILE_TYPES)}, got {ftype!r}.")
    res = await _invoke(_build_delete_file_request(token, ftype), user_key=user_key, prefer="user", identity=identity)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    out: dict[str, Any] = {"ok": True, "file_token": token, "type": ftype}
    # Folder deletion is async and returns a task_id — surface it for status polling.
    if data.get("task_id"):
        out["task_id"] = data["task_id"]
    return out


# ── Create documents: standalone docx + wiki (knowledge base) nodes ───────────
#
# Read tools above only *fetch* content; these create new documents. A wiki doc
# is a two-layer thing: the wiki *node* (the entry in a knowledge space) wraps an
# underlying docx whose token is `obj_token` — that token is the docx document_id
# you pass to `append_doc_content_impl` to fill in the body. So the full flow is
# list_wiki_spaces → create_wiki_node → append_doc_content.

_DOC_BASE_URL = "https://feishu.cn"


def _build_docx_create_request(title: str, folder_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/docx/v1/documents"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {}
    if title:
        body["title"] = title
    if folder_token:
        body["folder_token"] = folder_token
    req.body = body
    return req


async def create_docx_impl(
    title: str,
    folder_token: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Create a new standalone docx cloud document. Returns its document_id + URL.

    Pass ``user_key`` to create as that user (doc owned by them); empty uses tenant token.
    """
    res = await _invoke(
        _build_docx_create_request(title.strip(), folder_token.strip()),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    doc = data.get("document", {}) if isinstance(data.get("document"), dict) else {}
    document_id = doc.get("document_id", "")
    return {
        "ok": True,
        "document_id": document_id,
        "title": doc.get("title", title),
        "revision_id": doc.get("revision_id"),
        "url": f"{_DOC_BASE_URL}/docx/{document_id}" if document_id else "",
    }


def _build_wiki_node_create_request(
    *, space_id: str, obj_type: str, node_type: str, parent_node_token: str, title: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/wiki/v2/spaces/:space_id/nodes"
    req.paths["space_id"] = space_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"obj_type": obj_type, "node_type": node_type}
    if parent_node_token:
        body["parent_node_token"] = parent_node_token
    if title:
        body["title"] = title
    req.body = body
    return req


async def create_wiki_node_impl(
    space_id: str,
    title: str,
    obj_type: str = "docx",
    parent_node_token: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Create a node (default: a docx doc) in a wiki space. Returns node_token + obj_token(=document_id).

    Pass ``user_key`` to act as that user (needed when the wiki space is owned by the
    user, so the bot isn't a collaborator); empty uses the bot's tenant token.
    """
    if not space_id.strip():
        return _error("space_id is required. Use feishu_wiki_list_spaces to find it.")
    # Feishu deprecated `doc`; the API rejects it with error 131010.
    obj_type = (obj_type or "docx").strip()
    if obj_type == "doc":
        obj_type = "docx"
    res = await _invoke(
        _build_wiki_node_create_request(
            space_id=space_id.strip(),
            obj_type=obj_type,
            node_type="origin",
            parent_node_token=parent_node_token.strip(),
            title=title.strip(),
        ),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    node = data.get("node", {}) if isinstance(data.get("node"), dict) else {}
    obj_token = node.get("obj_token", "")
    return {
        "ok": True,
        "node_token": node.get("node_token", ""),
        "obj_token": obj_token,
        "obj_type": node.get("obj_type", obj_type),
        "space_id": node.get("space_id", space_id),
        "title": node.get("title", title),
        # For a docx node, obj_token is the document_id — write the body with
        # feishu_doc_append_content(document_id=obj_token, ...).
        "url": f"{_DOC_BASE_URL}/wiki/{node.get('node_token', '')}",
    }


async def create_wiki_doc_with_content_impl(
    space_id: str, title: str, content: str, parent_node_token: str = "", user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Create a wiki docx node AND write its body in one call (atomic-ish).

    Avoids the "empty node" failure of doing create + append as two separate LLM
    tool calls: creates the node, then appends the body. If the body write fails,
    the node_token/obj_token are returned alongside the error (so the half-created
    node can be found or retried), rather than leaving a silent empty page.
    """
    node = await create_wiki_node_impl(space_id, title, "docx", parent_node_token, user_key, identity)
    if not node["ok"]:
        return node
    obj_token = node.get("obj_token", "")
    # No body requested (or only blank lines): return the node as-is, not an error.
    if not _content_to_blocks(content or ""):
        return {**node, "added": 0, "note": "no body content — created an empty doc"}
    if not obj_token:
        return {**node, "ok": False, "message": "node created but obj_token missing — cannot write body"}
    written = await append_doc_content_impl(obj_token, content, user_key, identity)
    if not written["ok"]:
        # Surface the node so the caller knows a doc exists and can retry the body.
        return {
            **node,
            "ok": False,
            "body_written": False,
            "added": written.get("added", 0),
            "message": f"Node created but writing body failed: {written.get('message', '')}",
            **({"need_auth": True} if written.get("need_auth") else {}),
        }
    return {**node, "body_written": True, "added": written.get("added", 0)}


def _build_list_spaces_request(page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/wiki/v2/spaces"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    return req


async def list_wiki_spaces_impl(page_size: int = 20, page_token: str = "", user_key: str = "") -> dict[str, Any]:
    """List the wiki (knowledge base) spaces the app/user can access. Returns space_id + name.

    Pass ``user_key`` to list the spaces THAT USER can see (the bot's own tenant token
    only sees spaces the bot was added to — usually none); empty uses the bot token.
    """
    page_size = max(1, min(int(page_size or 20), 50))
    res = await _invoke_wiki_read(
        _build_list_spaces_request(page_size, page_token.strip()),
        user_key,
        lambda r: not (r.get("data", {}) or {}).get("items"),
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    spaces = [
        {"space_id": it.get("space_id", ""), "name": it.get("name", ""), "space_type": it.get("space_type", "")}
        for it in items
        if isinstance(it, dict)
    ]
    return {
        "ok": True,
        "spaces": spaces,
        "page_token": data.get("page_token", ""),
        "has_more": bool(data.get("has_more")),
    }


def _build_list_wiki_nodes_request(
    space_id: str, page_size: int, page_token: str, parent_node_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/wiki/v2/spaces/:space_id/nodes"
    req.paths["space_id"] = space_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    if parent_node_token:
        req.add_query("parent_node_token", parent_node_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_wiki_nodes_impl(
    space_id: str, page_size: int = 50, page_token: str = "", parent_node_token: str = "", user_key: str = ""
) -> dict[str, Any]:
    """List the child nodes (documents/pages) of a wiki space (or under a parent node).

    Pass ``user_key`` to browse as that user (the bot's tenant token only sees spaces
    it was added to); empty uses the bot token. ``parent_node_token`` empty lists the
    space's top level; set it to drill into a node's children.
    """
    if not space_id.strip():
        return _error("space_id is required. Use feishu_wiki_list_spaces to find it.")
    page_size = max(1, min(int(page_size or 50), 50))
    res = await _invoke_wiki_read(
        _build_list_wiki_nodes_request(space_id.strip(), page_size, page_token.strip(), parent_node_token.strip()),
        user_key,
        lambda r: not (r.get("data", {}) or {}).get("items"),
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    nodes = [
        {
            "node_token": it.get("node_token", ""),
            "obj_token": it.get("obj_token", ""),
            "obj_type": it.get("obj_type", ""),
            "title": it.get("title", ""),
            "has_child": bool(it.get("has_child")),
        }
        for it in items
        if isinstance(it, dict)
    ]
    return {
        "ok": True,
        "nodes": nodes,
        "page_token": data.get("page_token", ""),
        "has_more": bool(data.get("has_more")),
    }


# ── Write body content into a docx ────────────────────────────────────────────
#
# The docx block API is rich (tables/images/code/…). We map plain text / light
# Markdown to the two blocks that cover "write a knowledge-base doc": headings
# (`# ` → h1 … up to `###### ` → h6, block_type 3..8) and paragraphs (block_type
# 2). Blank lines are skipped. Children are appended to the document root
# (block_id == document_id) in batches of <=50 (the API cap).

_HEADING_KEYS = {3: "heading1", 4: "heading2", 5: "heading3", 6: "heading4", 7: "heading5", 8: "heading6"}
_BLOCKS_BATCH = 50


def _line_to_block(line: str) -> dict[str, Any] | None:
    text = line.rstrip()
    if not text.strip():
        return None
    stripped = text.lstrip()
    level = 0
    while level < len(stripped) and stripped[level] == "#":
        level += 1
    # "# " .. "###### " → heading blocks (block_type 3..8)
    if 1 <= level <= 6 and level < len(stripped) and stripped[level] == " ":
        block_type = 2 + level
        content = stripped[level + 1 :].strip()
        key = _HEADING_KEYS[block_type]
        return {"block_type": block_type, key: {"elements": [{"text_run": {"content": content}}]}}
    # Everything else → a plain text paragraph (block_type 2)
    return {"block_type": 2, "text": {"elements": [{"text_run": {"content": text.strip()}}]}}


def _content_to_blocks(content: str) -> list[dict[str, Any]]:
    blocks = [b for b in (_line_to_block(ln) for ln in content.splitlines()) if b is not None]
    return blocks


def _build_blocks_append_request(document_id: str, children: list[dict[str, Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    # Root block: the document_id doubles as the root block_id.
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = document_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"children": children}
    return req


# ── Tables (block_type 31) + flowcharts/swimlanes rendered AS tables ────────────
# Feishu docx has no API to *draw* a flowchart/mindnote/board: block_type 21
# (diagram) and 44 (board) are empty canvases the open API can't populate with
# nodes/edges, so a "生成流程图/泳道图" request can't produce a real editable diagram
# via the API. The faithful, fully-supported alternative is a native Feishu table:
# a flowchart becomes a single-column "步骤 → 步骤" ladder, a swimlane becomes a
# grid whose columns are the lanes (角色/部门) and rows are the stages. Both render
# as real, editable tables in the doc — not an image, not a broken embed.
#
# A table can't be created with the plain /children endpoint: the table block, its
# cell blocks (block_type 32) and each cell's text block must all be sent together
# to the /descendant endpoint, which takes a flat `descendants` list plus the
# `children_id` of the blocks that attach at the insert point (here: the table).
_TABLE_BLOCK_TYPE = 31
_TABLE_CELL_BLOCK_TYPE = 32


def _text_block(block_id: str, text: str, *, bold: bool = False) -> dict[str, Any]:
    """A paragraph (block_type 2) carrying one text run, for use inside a table cell."""
    run: dict[str, Any] = {"content": text}
    if bold:
        run["text_style"] = {"bold": True}
    return {"block_id": block_id, "block_type": 2, "text": {"elements": [{"text_run": run}]}}


def _table_descendants(
    rows: list[list[str]],
    *,
    table_id: str = "tbl",
    header_row: bool = True,
    column_width: list[int] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Build the (table_block_id, descendants) for a 2-D grid of cell strings.

    ``rows`` is a list of rows, each a list of cell texts; every row is padded to
    the widest row so the grid is rectangular (Feishu requires it). Returns the
    table block_id to put in ``children_id`` and the flat descendants list (table,
    then each cell, then each cell's text block) the /descendant endpoint wants.
    """
    row_size = len(rows)
    column_size = max((len(r) for r in rows), default=0)
    cell_ids: list[str] = []
    descendants: list[dict[str, Any]] = []
    cell_blocks: list[dict[str, Any]] = []
    text_blocks: list[dict[str, Any]] = []
    for r, row in enumerate(rows):
        for c in range(column_size):
            text = row[c] if c < len(row) else ""
            cid = f"{table_id}_c{r}_{c}"
            tid = f"{cid}_t"
            cell_ids.append(cid)
            cell_blocks.append({"block_id": cid, "block_type": _TABLE_CELL_BLOCK_TYPE, "children": [tid]})
            text_blocks.append(_text_block(tid, text, bold=header_row and r == 0))
    table_prop: dict[str, Any] = {"row_size": row_size, "column_size": column_size, "header_row": header_row}
    if column_width:
        table_prop["column_width"] = column_width
    table_block = {
        "block_id": table_id,
        "block_type": _TABLE_BLOCK_TYPE,
        "table": {"cells": cell_ids, "property": table_prop},
    }
    # Order: table first, then all cells, then all cell-text blocks. The API only
    # requires every referenced block_id to be present somewhere in descendants.
    descendants.append(table_block)
    descendants.extend(cell_blocks)
    descendants.extend(text_blocks)
    return table_id, descendants


def _build_descendant_request(
    document_id: str, children_id: list[str], descendants: list[dict[str, Any]], index: int
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    # Root block: the document_id doubles as the root block_id (append at doc root).
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/descendant"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = document_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"children_id": children_id, "descendants": descendants}
    if index >= 0:
        body["index"] = index
    req.body = body
    return req


def _parse_rows(rows_json: str) -> list[list[str]] | dict[str, Any]:
    """Parse a JSON 2-D array of cell values into list[list[str]] (or an error dict).

    Accepts a JSON array of arrays; each cell is str()-coerced (numbers/bools become
    text). Rejects anything that isn't a non-empty list of lists.
    """
    try:
        data = json.loads(rows_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return _error(f'rows must be a JSON 2-D array, e.g. [["a","b"],["1","2"]]. Parse error: {exc}')
    if not isinstance(data, list) or not data:
        return _error("rows must be a non-empty JSON array of rows.")
    parsed: list[list[str]] = []
    for i, row in enumerate(data):
        if not isinstance(row, list):
            return _error(f"row {i} must be an array of cell values.")
        parsed.append(["" if c is None else str(c) for c in row])
    return parsed


async def _append_table_descendants(
    document_id: str,
    rows: list[list[str]],
    *,
    header_row: bool,
    column_width: list[int] | None,
    user_key: str,
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Send one table (built from ``rows``) to the /descendant endpoint. Shared by
    the table / flowchart / swimlane tools.

    A caption is written *before* the table, not after: the academic convention places a
    table's title above it and a figure's below, and these three tools produce tables.
    Its "表 N" is numbered off the document's existing captions, so tables and figures
    keep independent, gap-free sequences.
    """
    if not document_id.strip():
        return _error("document_id is required.")
    if not rows:
        return _error("no rows to write — the table would be empty.")
    doc = document_id.strip()
    result_extra: dict[str, Any] = {}
    if caption.strip():
        # Before the table, and before the table's own request, so a caption that fails
        # doesn't leave a numbered heading pointing at nothing.
        text, fields = await _resolve_table_caption(doc, caption, auto_number, user_key, identity)
        result_extra.update(fields)
        note = await append_doc_content_impl(doc, text, user_key, identity)
        result_extra["caption_written"] = bool(note.get("ok"))
        if not note.get("ok"):
            result_extra["caption_error"] = note.get("message", "")
    table_id, descendants = _table_descendants(rows, header_row=header_row, column_width=column_width)
    req = _build_descendant_request(doc, [table_id], descendants, index=-1)
    res = await _invoke(req, user_key=user_key, prefer="user", identity=identity)
    if not res["ok"]:
        return res
    return {
        "ok": True,
        "document_id": doc,
        "rows": len(rows),
        "columns": max((len(r) for r in rows), default=0),
        **result_extra,
    }


async def _resolve_table_caption(
    document_id: str, caption: str, auto_number: bool, user_key: str, identity: str
) -> tuple[str, dict[str, Any]]:
    """A caption body becomes a numbered 表 caption, counted off the document's existing ones.

    Imported lazily because ``_chart_caption`` imports this module: at module scope the
    two would form an import cycle.
    """
    import _chart_caption as _cap  # noqa: PLC0415

    body = _cap.strip_own_number(caption, _cap.TABLE)
    if not auto_number:
        return _cap.format_caption(_cap.TABLE, 0, body), {}
    numbered = await _cap.next_number(document_id, _cap.TABLE, user_key, identity)
    if not numbered.get("ok"):
        return (
            _cap.format_caption(_cap.TABLE, 0, body),
            {"caption_number_skipped": numbered.get("reason", "could not read the document")},
        )
    number = int(numbered["number"])
    return _cap.format_caption(_cap.TABLE, number, body), {"caption_number": number}


async def append_doc_table_impl(
    document_id: str,
    rows_json: str,
    header_row: bool = True,
    column_width_json: str = "",
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append a native Feishu table (block_type 31) to a docx body.

    ``rows_json`` is a JSON 2-D array; the first row is styled as a header when
    ``header_row`` is true. ``column_width_json`` optionally sets per-column pixel
    widths (JSON array of ints). ``caption`` writes a numbered 表 caption line above it.
    """
    rows = _parse_rows(rows_json)
    if isinstance(rows, dict):  # parse error
        return rows
    column_width: list[int] | None = None
    if column_width_json.strip():
        try:
            cw = json.loads(column_width_json)
            if isinstance(cw, list) and all(isinstance(x, int) for x in cw):
                column_width = cw
        except json.JSONDecodeError, TypeError:
            column_width = None
    return await _append_table_descendants(
        document_id,
        rows,
        header_row=header_row,
        column_width=column_width,
        user_key=user_key,
        identity=identity,
        caption=caption,
        auto_number=auto_number,
    )


async def append_doc_flowchart_impl(
    document_id: str,
    steps_json: str,
    title: str = "",
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append a flowchart rendered as a single-column Feishu table (each step a row,
    joined by ↓ arrows). Feishu's API can't draw a real diagram, so this is the
    faithful, editable alternative. ``steps_json`` is a JSON array of step labels."""
    try:
        steps = json.loads(steps_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return _error(f"steps must be a JSON array of strings. Parse error: {exc}")
    if not isinstance(steps, list) or not steps:
        return _error('steps must be a non-empty JSON array, e.g. ["开始","审批","结束"].')
    labels = ["" if s is None else str(s) for s in steps]
    # Interleave arrow rows so the ladder reads top-to-bottom like a flowchart.
    rows: list[list[str]] = [[title or "流程图"]]
    for i, label in enumerate(labels):
        rows.append([label])
        if i < len(labels) - 1:
            rows.append(["↓"])
    return await _append_table_descendants(
        document_id,
        rows,
        header_row=bool(title) or True,
        column_width=None,
        user_key=user_key,
        identity=identity,
        caption=caption,
        auto_number=auto_number,
    )


async def append_doc_swimlane_impl(
    document_id: str,
    lanes_json: str,
    stages_json: str = "",
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append a swimlane diagram rendered as a Feishu table: columns = lanes
    (角色/部门), rows = stages. Feishu's API can't draw a real swimlane diagram, so
    this grid is the faithful, editable alternative.

    ``lanes_json`` — either a JSON array of lane names (then ``stages_json`` gives
    the per-stage cells) OR a JSON object mapping lane→[activities] (auto-gridded).
    """
    try:
        lanes = json.loads(lanes_json)
    except (json.JSONDecodeError, TypeError) as exc:
        return _error(f"lanes must be JSON. Parse error: {exc}")
    rows: list[list[str]]
    if isinstance(lanes, dict):
        # {lane: [activity, ...]} — columns are lanes, each column filled top-down.
        if not lanes:
            return _error("lanes object is empty.")
        lane_names = [str(name) for name in lanes]
        columns = [[str(a) for a in (lanes[name] or [])] for name in lane_names]
        depth = max((len(col) for col in columns), default=0)
        rows = [lane_names]
        for r in range(depth):
            rows.append([col[r] if r < len(col) else "" for col in columns])
    elif isinstance(lanes, list) and lanes:
        # lanes = header (column) names; stages_json = 2-D array of body rows.
        lane_names = [str(x) for x in lanes]
        rows = [lane_names]
        if stages_json.strip():
            body = _parse_rows(stages_json)
            if isinstance(body, dict):  # parse error
                return body
            rows.extend(body)
    else:
        return _error("lanes must be a non-empty JSON array of lane names or an object {lane:[activities]}.")
    return await _append_table_descendants(
        document_id,
        rows,
        header_row=True,
        column_width=None,
        user_key=user_key,
        identity=identity,
        caption=caption,
        auto_number=auto_number,
    )


async def append_doc_content_impl(
    document_id: str,
    content: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Append text/heading blocks (from plain text or light Markdown) to a docx body.

    Pass ``user_key`` to write as that user (e.g. into a doc inside a user-owned wiki);
    empty uses the bot's tenant token.
    """
    if not document_id.strip():
        return _error("document_id is required.")
    blocks = _content_to_blocks(content or "")
    if not blocks:
        return _error("content is empty — nothing to write.")
    added = 0
    for start in range(0, len(blocks), _BLOCKS_BATCH):
        batch = blocks[start : start + _BLOCKS_BATCH]
        res = await _invoke(
            _build_blocks_append_request(document_id.strip(), batch),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            res["added"] = added
            return res
        added += len(batch)
    return {"ok": True, "document_id": document_id.strip(), "added": added}


# ── Drive permissions — make a doc public / give different people different access ──
# One doc/sheet/bitable/wiki, per-member permission (view/edit/full_access). Add a
# department (e.g. the whole company) for "全员可查", or add specific users/groups at
# different perm levels so different people see/do different things on the artifact.
_PERM_MEMBER_TYPES = {"openid", "openchat", "opendepartmentid", "userid", "unionid", "email", "groupid", "wikispaceid"}
_PERM_LEVELS = {"view", "edit", "full_access"}


def _build_add_permission_member_request(
    token: str, obj_type: str, member_type: str, member_id: str, member_kind: str, perm: str, need_notification: bool
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/drive/v1/permissions/:token/members"
    req.paths["token"] = token
    req.add_query("type", obj_type)
    req.add_query("need_notification", "true" if need_notification else "false")
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"member_type": member_type, "member_id": member_id, "perm": perm, "type": member_kind}
    return req


async def add_permission_member_impl(
    token: str,
    obj_type: str,
    member_id: str,
    perm: str = "view",
    member_type: str = "openid",
    member_kind: str = "user",
    need_notification: bool = False,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Grant a user/chat/department a permission (view/edit/full_access) on a Feishu file."""
    if not token.strip() or not member_id.strip():
        return _error("token and member_id are required.")
    if perm not in _PERM_LEVELS:
        return _error(f"perm must be one of {sorted(_PERM_LEVELS)}.")
    if member_type not in _PERM_MEMBER_TYPES:
        return _error(f"member_type must be one of {sorted(_PERM_MEMBER_TYPES)}.")
    req = _build_add_permission_member_request(
        token.strip(), obj_type, member_type, member_id.strip(), member_kind, perm, need_notification
    )
    res = await _invoke(req, user_key=user_key, prefer="user", identity=identity)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "member": data.get("member", {}), "token": token.strip(), "type": obj_type}


def _build_list_permission_members_request(token: str, obj_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/drive/v1/permissions/:token/members"
    req.paths["token"] = token
    req.add_query("type", obj_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_permission_members_impl(token: str, obj_type: str, user_key: str = "") -> dict[str, Any]:
    """List everyone who has an explicit permission on a Feishu file (who can see/edit it)."""
    if not token.strip():
        return _error("token is required.")
    res = await _invoke(_build_list_permission_members_request(token.strip(), obj_type), user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    members = [
        {
            "member_id": m.get("member_id", ""),
            "member_type": m.get("member_type", ""),
            "perm": m.get("perm", ""),
            "type": m.get("type", ""),
            "name": m.get("name", ""),
        }
        for m in items
        if isinstance(m, dict)
    ]
    return {"ok": True, "members": members, "member_total": len(members)}


def _build_delete_permission_member_request(
    token: str, obj_type: str, member_id: str, member_type: str, member_kind: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/drive/v1/permissions/:token/members/:member_id"
    req.paths["token"] = token
    req.paths["member_id"] = member_id
    req.add_query("type", obj_type)
    req.add_query("member_type", member_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"type": member_kind}
    return req


async def delete_permission_member_impl(
    token: str,
    obj_type: str,
    member_id: str,
    member_type: str = "openid",
    member_kind: str = "user",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Revoke a user/chat/department's permission on a Feishu file."""
    if not token.strip() or not member_id.strip():
        return _error("token and member_id are required.")
    req = _build_delete_permission_member_request(token.strip(), obj_type, member_id.strip(), member_type, member_kind)
    res = await _invoke(req, user_key=user_key, prefer="user", identity=identity)
    if not res["ok"]:
        return res
    return {"ok": True, "token": token.strip(), "member_id": member_id.strip()}


# ── Bitable advanced permission — one base, different roles see different rows/fields ──
# A custom role (自定义角色) controls per-table read/edit, optional per-record visibility
# rules, and per-field permissions. Assign people to a role so everyone opens the same
# base but each role sees different rows/fields. Requires advanced permission on the base.
def _build_create_bitable_role_request(app_token: str, body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/roles"
    req.paths["app_token"] = app_token
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = body
    return req


async def create_bitable_role_impl(
    app_token: str, role_name: str, table_roles_json: str, user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Create a custom role on a bitable. table_roles_json is a JSON list of per-table perms."""
    if not app_token.strip() or not role_name.strip():
        return _error("app_token and role_name are required.")
    try:
        table_roles = json.loads(table_roles_json)
    except ValueError as exc:
        return _error(f"table_roles_json is not valid JSON: {exc}")
    if not isinstance(table_roles, list):
        return _error("table_roles_json must be a JSON array of per-table permission objects.")
    body = {"role_name": role_name.strip(), "table_roles": table_roles}
    res = await _invoke(
        _build_create_bitable_role_request(app_token.strip(), body), user_key=user_key, prefer="user", identity=identity
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    role = data.get("role", {}) if isinstance(data.get("role"), dict) else {}
    return {"ok": True, "role_id": role.get("role_id", ""), "role_name": role.get("role_name", "")}


def _build_list_bitable_roles_request(app_token: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/bitable/v1/apps/:app_token/roles"
    req.paths["app_token"] = app_token
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_bitable_roles_impl(
    app_token: str, page_size: int = 100, page_token: str = "", user_key: str = ""
) -> dict[str, Any]:
    """List the custom roles defined on a bitable (each with its role_id and table perms)."""
    if not app_token.strip():
        return _error("app_token is required.")
    res = await _invoke(_build_list_bitable_roles_request(app_token.strip(), page_size, page_token), user_key=user_key)
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    roles = [
        {"role_id": r.get("role_id", ""), "role_name": r.get("role_name", ""), "table_roles": r.get("table_roles", [])}
        for r in items
        if isinstance(r, dict)
    ]
    return {
        "ok": True,
        "roles": roles,
        "has_more": data.get("has_more", False),
        "page_token": data.get("page_token", ""),
    }


def _build_add_bitable_role_member_request(
    app_token: str, role_id: str, member_id: str, member_id_type: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/bitable/v1/apps/:app_token/roles/:role_id/members"
    req.paths["app_token"] = app_token
    req.paths["role_id"] = role_id
    req.add_query("member_id_type", member_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"member_id": member_id}
    return req


async def add_bitable_role_member_impl(
    app_token: str,
    role_id: str,
    member_id: str,
    member_id_type: str = "open_id",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Assign a user to a bitable custom role (that person then sees the role's rows/fields)."""
    if not app_token.strip() or not role_id.strip() or not member_id.strip():
        return _error("app_token, role_id and member_id are required.")
    req = _build_add_bitable_role_member_request(app_token.strip(), role_id.strip(), member_id.strip(), member_id_type)
    res = await _invoke(req, user_key=user_key, prefer="user", identity=identity)
    if not res["ok"]:
        return res
    return {"ok": True, "role_id": role_id.strip(), "member_id": member_id.strip()}


# ── eLearning (在线学习) — query each person's course-registration / learning records ──
# Reads who signed up for a course and their completion status/progress/score. Note:
# creating/publishing a course and assigning it to 全员 is done in the eLearning admin
# console — the open platform exposes the *reading* of registration/learning records.
# The exact path & scope below follow Feishu's naming convention; verify on the live
# doc during integration (the doc site is a JS SPA and can't be scraped).
def _build_list_course_registrations_request(
    user_ids: list[str], user_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/elearning/v2/course_registrations"
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    for uid in user_ids:
        req.add_query("user_ids", uid)
    req.token_types = {AccessTokenType.TENANT}
    return req


async def list_course_registrations_impl(
    user_ids: str = "",
    user_id_type: str = "open_id",
    page_size: int = 100,
    page_token: str = "",
) -> dict[str, Any]:
    """List eLearning course registrations (learning records) — optionally filtered by user."""
    ids = [u.strip() for u in user_ids.split(",") if u.strip()]
    res = await _invoke(_build_list_course_registrations_request(ids, user_id_type, page_size, page_token))
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items", []) if isinstance(data.get("items"), list) else []
    return {
        "ok": True,
        "registrations": items,
        "has_more": data.get("has_more", False),
        "page_token": data.get("page_token", ""),
    }


# ── Drive media upload — put a learning video / signed proof into Feishu Drive ─────────
# upload_all handles files up to 20MB in one shot (multipart). Larger files need the
# chunked upload_prepare/upload_part/upload_finish flow (not implemented here).
_UPLOAD_ALL_MAX_BYTES = 20 * 1024 * 1024


class _NamedBytes(io.BytesIO):
    """An in-memory file that carries a filename.

    The SDK decides "this is multipart" by finding ``io.IOBase`` values in the request
    *body* — plain ``bytes`` is not enough — and httpx reads ``.name`` to fill in the
    multipart ``filename=``. A bare ``BytesIO`` would upload as "upload" with no
    extension, which Feishu rejects for images.
    """

    def __init__(self, data: bytes, name: str) -> None:
        super().__init__(data)
        self.name = name


def _build_media_upload_all_request(
    file_name: str, parent_type: str, parent_node: str, size: int, data: bytes, extra: dict[str, Any] | None
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/drive/v1/medias/upload_all"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {
        "file_name": file_name,
        "parent_type": parent_type,
        "parent_node": parent_node,
        "size": str(size),
    }
    if extra:
        body["extra"] = json.dumps(extra, ensure_ascii=False)
    # The binary goes in the BODY, not in req.files: Client.arequest overwrites
    # req.files with Files.extract_files(req.body) right before sending, so anything
    # assigned here is discarded — the request then goes out as application/json and
    # Feishu answers "boundary not found".
    body["file"] = _NamedBytes(data, file_name)
    req.body = body
    return req


async def upload_media_impl(
    file_path: str,
    parent_type: str = "explorer",
    parent_node: str = "",
    file_name: str = "",
    extra_json: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Upload a local file (e.g. a learning video) to Feishu Drive; returns its file_token."""
    p = anyio.Path(file_path)
    if not await p.is_file():
        return _error(f"file not found: {file_path}")
    if not parent_node.strip():
        return _error("parent_node is required (the target folder token for parent_type=explorer).")
    extra: dict[str, Any] | None = None
    if extra_json.strip():
        try:
            extra = json.loads(extra_json)
        except ValueError as exc:
            return _error(f"extra_json is not valid JSON: {exc}")
    name = file_name.strip() or p.name
    data = await p.read_bytes()
    size = len(data)
    if size > _UPLOAD_ALL_MAX_BYTES:
        return _error(
            f"file is {size} bytes (> 20MB). upload_all supports files up to 20MB; "
            "use the chunked upload flow for larger files.",
            size=size,
        )
    # A factory, not a request: an upload may be attempted under both identities and
    # the SDK consumes the file entry on the first send.
    res = await _invoke(
        lambda: _build_media_upload_all_request(name, parent_type, parent_node.strip(), size, data, extra),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "file_token": rdata.get("file_token", ""), "file_name": name, "size": size}


# ── Data charts as docx image blocks (block_type 27) ───────────────────────────
# The one way to land a real data chart (pie/line/bar/…) in a Feishu doc: docx has
# no chart block and the Sheets API can't create charts, but it does have an *image*
# block, and drive medias/upload_all can target that block directly. The dance is
# three calls and the order matters:
#
#   1. POST .../blocks/:block_id/children with an empty ``image`` block → returns the
#      new block's block_id. The block exists but renders as a placeholder.
#   2. POST drive/v1/medias/upload_all with parent_type="docx_image" and
#      parent_node=<that block_id> → uploads the PNG *into* the block.
#   3. PATCH .../blocks/:block_id with replace_image=<file_token> → binds the token so
#      the block renders the picture.
#
# Step 3 is required: without it the upload is attached but the block keeps showing
# a placeholder. An empty image block left behind by a failed step 2/3 is worse than
# no chart, so failures try to clean it up.
_IMAGE_BLOCK_TYPE = 27


def _build_image_block_create_request(document_id: str, block_id: str, index: int) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    # An image block is created empty: width/height are the display box, and the
    # token is filled in later by the replace_image patch.
    body: dict[str, Any] = {"children": [{"block_type": _IMAGE_BLOCK_TYPE, "image": {"token": ""}}]}
    if index >= 0:
        body["index"] = index
    req.body = body
    return req


def _build_image_block_patch_request(document_id: str, block_id: str, file_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"replace_image": {"token": file_token}}
    return req


def _build_block_delete_request(document_id: str, block_id: str, index: int) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children/batch_delete"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"start_index": index, "end_index": index + 1}
    return req


async def _upload_into_image_block(image_path: str, block_id: str, user_key: str, identity: str = "") -> dict[str, Any]:
    """Upload a local PNG into an existing docx image block; returns its file_token."""
    path = anyio.Path(image_path)
    if not await path.is_file():
        return _error(f"chart image not found: {image_path}")
    data = await path.read_bytes()
    size = len(data)
    if size > _UPLOAD_ALL_MAX_BYTES:
        return _error(f"chart image is {size} bytes (> 20MB) — too large to upload.", size=size)
    res = await _invoke(
        lambda: _build_media_upload_all_request(path.name, "docx_image", block_id, size, data, None),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    token = rdata.get("file_token", "")
    if not token:
        return _error("upload succeeded but returned no file_token.")
    return {"ok": True, "file_token": token, "size": size}


async def read_doc_for_captions(document_id: str, user_key: str = "", identity: str = "") -> dict[str, Any]:
    """A docx's plain text, for counting the 图/表 captions already in it.

    Separate from ``read_doc_impl`` because that one is a user-facing reader with no
    identity plumbing (tenant token only): a chart being written into someone's own doc
    has to be counted with the same credentials that will do the writing, or the read
    fails on exactly the user-owned docs where captions matter most. ``prefer="tenant"``
    since this is a read — a doc the bot can see needs no user authorization.
    """
    doc = document_id.strip()
    if not doc:
        return _error("document_id is required.")
    res = await _invoke(
        lambda: _build_docx_raw_request(doc),
        user_key=user_key,
        prefer="tenant",
        identity=identity,
    )
    if not res["ok"]:
        return res
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "content": data.get("content", "")}


async def append_doc_image_impl(
    document_id: str, image_path: str, caption: str = "", user_key: str = "", identity: str = ""
) -> dict[str, Any]:
    """Append a local image to a docx as a real image block, with an optional caption.

    Shared by every chart tool: they render a PNG, then hand it here. The caption is
    written as a separate paragraph below the image (docx image blocks carry no
    caption field of their own) — a chart without a "图N: what this shows" line makes
    the reader guess at what they're looking at.
    """
    doc = document_id.strip()
    if not doc:
        return _error("document_id is required.")
    created = await _invoke(
        lambda: _build_image_block_create_request(doc, doc, -1), user_key=user_key, prefer="user", identity=identity
    )
    if not created["ok"]:
        return created
    cdata = created["data"] if isinstance(created["data"], dict) else {}
    children = cdata.get("children") or []
    block_id = children[0].get("block_id", "") if children and isinstance(children[0], dict) else ""
    if not block_id:
        return _error("created the image block but the response carried no block_id.")
    # Where the placeholder landed, so a later failure can remove exactly that block.
    index = cdata.get("index")

    uploaded = await _upload_into_image_block(image_path, block_id, user_key, identity)
    if not uploaded["ok"]:
        await _discard_image_block(doc, block_id, index, user_key, identity)
        return uploaded
    patched = await _invoke(
        lambda: _build_image_block_patch_request(doc, block_id, uploaded["file_token"]),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not patched["ok"]:
        await _discard_image_block(doc, block_id, index, user_key, identity)
        return patched
    result: dict[str, Any] = {
        "ok": True,
        "document_id": doc,
        "block_id": block_id,
        "file_token": uploaded["file_token"],
        "bytes": uploaded["size"],
    }
    if caption.strip():
        # A failed caption doesn't invalidate the chart itself, so it's reported
        # rather than treated as a failure of the whole append.
        note = await append_doc_content_impl(doc, caption.strip(), user_key, identity)
        result["caption_written"] = bool(note.get("ok"))
        if not note.get("ok"):
            result["caption_error"] = note.get("message", "")
    return result


def _build_block_children_list_request(document_id: str, block_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.add_query("page_size", 500)
    return req


async def _discard_image_block(document_id: str, block_id: str, index: Any, user_key: str, identity: str = "") -> None:
    """Best-effort removal of a placeholder image block after a failed upload/patch.

    The create response carries no ``index``, so the delete range is found by locating
    ``block_id`` among the document's children — deleting by a guessed range could take
    the user's real content with it. If the block can't be located the empty placeholder
    is left in place: an orphan is unfortunate, deleting the wrong block is not
    recoverable.
    """
    if not isinstance(index, int) or index < 0:
        index = await _locate_child_index(document_id, block_id, user_key, identity)
    if not isinstance(index, int) or index < 0:
        return
    with contextlib.suppress(Exception):
        await _invoke(
            lambda: _build_block_delete_request(document_id, document_id, index),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )


async def _locate_child_index(document_id: str, block_id: str, user_key: str, identity: str = "") -> int:
    """Position of ``block_id`` among the doc root's children, or -1 if not found."""
    with contextlib.suppress(Exception):
        res = await _invoke(
            lambda: _build_block_children_list_request(document_id, document_id),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if res.get("ok"):
            data = res.get("data")
            items = data.get("items") or [] if isinstance(data, dict) else []
            for position, item in enumerate(items):
                if isinstance(item, dict) and item.get("block_id") == block_id:
                    return position
    return -1


# ── Block-level editing — revise a doc in place instead of only appending ────────
# Everything above only ever *adds* to a document, so fixing one wrong sentence meant
# rewriting the whole doc. These three close that loop: list the blocks to learn their
# block_ids and current text, rewrite one block's text, or delete blocks outright.
#
# The block_id is the unit of address, never a line number: Feishu's delete endpoint
# takes a *child index range* under a parent, and indexes shift as soon as anything is
# added or removed. So delete resolves block_id → current index right before deleting,
# and refuses rather than guessing when the block can't be found — a wrong index here
# deletes someone else's paragraph, which no retry can undo.
_BLOCK_TYPE_NAMES = {
    1: "page",
    2: "text",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    6: "heading4",
    7: "heading5",
    8: "heading6",
    9: "heading7",
    10: "heading8",
    11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    17: "todo",
    19: "callout",
    22: "divider",
    23: "file",
    24: "grid",
    25: "grid_column",
    27: "image",
    28: "iframe",
    30: "sheet",
    31: "table",
    32: "table_cell",
    34: "quote_container",
    999: "unsupported",
}

# The typed payload key holding a block's text elements, per block_type. Text lives
# under the block's own kind name (a heading2's runs are in "heading2"), which is why
# reading a block's text needs the type→key mapping rather than one fixed field.
_TEXTUAL_BLOCK_KEYS = {
    2: "text",
    3: "heading1",
    4: "heading2",
    5: "heading3",
    6: "heading4",
    7: "heading5",
    8: "heading6",
    9: "heading7",
    10: "heading8",
    11: "heading9",
    12: "bullet",
    13: "ordered",
    14: "code",
    15: "quote",
    17: "todo",
}


def _build_document_blocks_list_request(document_id: str, page_size: int, page_token: str) -> BaseRequest:
    """GET every block of a document (flat, with parent_id/children), not just one level."""
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks"
    req.paths["document_id"] = document_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    return req


def _build_block_text_patch_request(document_id: str, block_id: str, elements: list[dict[str, Any]]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"update_text_elements": {"elements": elements}}
    return req


def _build_blocks_batch_delete_request(document_id: str, block_id: str, start: int, end: int) -> BaseRequest:
    """Delete children [start, end) of ``block_id`` — the range is half-open."""
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/docx/v1/documents/:document_id/blocks/:block_id/children/batch_delete"
    req.paths["document_id"] = document_id
    req.paths["block_id"] = block_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"start_index": start, "end_index": end}
    return req


def _block_plain_text(block: dict[str, Any]) -> str:
    """The block's visible text, joined from its text_run/equation/mention elements."""
    key = _TEXTUAL_BLOCK_KEYS.get(block.get("block_type") or 0, "")
    payload = block.get(key) if key else None
    if not isinstance(payload, dict):
        return ""
    parts: list[str] = []
    for element in payload.get("elements") or []:
        if not isinstance(element, dict):
            continue
        run = element.get("text_run")
        if isinstance(run, dict):
            parts.append(str(run.get("content", "")))
            continue
        equation = element.get("equation")
        if isinstance(equation, dict):
            parts.append(str(equation.get("content", "")))
            continue
        mention = element.get("mention_doc")
        if isinstance(mention, dict):
            parts.append(str(mention.get("title", "")))
            continue
        mention_user = element.get("mention_user")
        if isinstance(mention_user, dict):
            parts.append("@" + str(mention_user.get("user_id", "")))
    return "".join(parts)


_BLOCKS_LIST_PAGE_MAX = 500


async def list_doc_blocks_impl(
    document_id: str,
    max_blocks: int = 200,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """List a docx's blocks as ``{block_id, block_type, type_name, text, parent_id}``.

    The prerequisite for editing anything: ``update_doc_block`` / ``delete_doc_blocks``
    address content by ``block_id``, and this is the only way to learn those ids. Text
    is trimmed to a preview so listing a long document doesn't flood the context; read
    the full body with ``read_doc_impl`` when that's what's wanted.

    ``prefer="tenant"`` because this is a read — a doc the bot can already see needs no
    user authorization — with the user's identity used when one is available.
    """
    doc = document_id.strip()
    if not doc:
        return _error("document_id is required.")
    limit = max(1, min(int(max_blocks or 200), 2000))
    items: list[dict[str, Any]] = []
    page_token = ""
    truncated = False
    while True:
        remaining = limit - len(items)
        page_size = min(_BLOCKS_LIST_PAGE_MAX, max(remaining, 1))
        res = await _invoke(
            _build_document_blocks_list_request(doc, page_size, page_token),
            user_key=user_key,
            prefer="tenant",
            identity=identity,
        )
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        for raw in data.get("items") or []:
            if not isinstance(raw, dict):
                continue
            if len(items) >= limit:
                truncated = True
                break
            block_type = raw.get("block_type") or 0
            text = _block_plain_text(raw)
            items.append(
                {
                    "block_id": raw.get("block_id", ""),
                    "block_type": block_type,
                    "type_name": _BLOCK_TYPE_NAMES.get(block_type, str(block_type)),
                    "parent_id": raw.get("parent_id", ""),
                    "text": text if len(text) <= 200 else text[:200] + "…",
                    "editable_text": block_type in _TEXTUAL_BLOCK_KEYS,
                }
            )
        page_token = str(data.get("page_token") or "")
        if truncated or not page_token or len(items) >= limit:
            truncated = truncated or bool(page_token)
            break
    return {"ok": True, "document_id": doc, "count": len(items), "truncated": truncated, "blocks": items}


async def update_doc_block_impl(
    document_id: str,
    block_id: str,
    text: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Replace the text of one docx block, keeping the block itself (and its type).

    Rewriting a heading leaves it a heading, a bullet stays a bullet: only the text runs
    are replaced. Structural blocks (image/table/divider/page) carry no text runs to
    replace and are rejected up front with the reason, rather than sent off to fail as
    an opaque Feishu error.
    """
    doc = document_id.strip()
    block = block_id.strip()
    if not doc:
        return _error("document_id is required.")
    if not block:
        return _error("block_id is required — get it from feishu_doc_list_blocks.")
    if block == doc:
        return _error(
            "block_id is the document's root block, which holds no text. "
            "Pass the block_id of a paragraph/heading from feishu_doc_list_blocks."
        )
    if text == "":
        return _error("text is required — to remove a block entirely use feishu_doc_delete_blocks.")
    res = await _invoke(
        _build_block_text_patch_request(doc, block, [{"text_run": {"content": text}}]),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not res["ok"]:
        return res
    return {"ok": True, "document_id": doc, "block_id": block, "text": text}


async def delete_doc_blocks_impl(
    document_id: str,
    block_ids_json: str,
    parent_block_id: str = "",
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Delete one or more blocks, addressed by block_id, from a docx.

    Feishu deletes by child-index range under a parent, so each id is resolved to its
    current index first. Deletions run highest-index-first: removing a block shifts
    every later sibling down, so deleting low-to-high would make each subsequent index
    point one block too far. Ids that can't be located are reported as ``not_found``
    instead of being guessed at.
    """
    doc = document_id.strip()
    if not doc:
        return _error("document_id is required.")
    try:
        raw_ids = json.loads(block_ids_json or "[]")
    except json.JSONDecodeError as exc:
        return _error(f"block_ids_json is not valid JSON: {exc}")
    if isinstance(raw_ids, str):
        raw_ids = [raw_ids]
    if not isinstance(raw_ids, list) or not raw_ids:
        return _error("block_ids_json must be a non-empty JSON array of block_ids, e.g. '[\"abc123\"]'.")
    wanted = [str(item).strip() for item in raw_ids if str(item).strip()]
    if not wanted:
        return _error("block_ids_json contained no usable block_id.")
    parent = parent_block_id.strip() or doc
    if doc in wanted:
        return _error("refusing to delete the document's root block — delete the file with feishu_drive_delete_file.")
    listed = await _invoke(
        _build_block_children_list_request(doc, parent),
        user_key=user_key,
        prefer="user",
        identity=identity,
    )
    if not listed["ok"]:
        return listed
    ldata = listed["data"] if isinstance(listed["data"], dict) else {}
    positions: dict[str, int] = {}
    for position, item in enumerate(ldata.get("items") or []):
        if isinstance(item, dict) and item.get("block_id"):
            positions[str(item["block_id"])] = position
    targets = sorted(((positions[bid], bid) for bid in wanted if bid in positions), reverse=True)
    not_found = [bid for bid in wanted if bid not in positions]
    deleted: list[str] = []
    for index, bid in targets:
        res = await _invoke(
            _build_blocks_batch_delete_request(doc, parent, index, index + 1),
            user_key=user_key,
            prefer="user",
            identity=identity,
        )
        if not res["ok"]:
            res["deleted"] = deleted
            res["not_found"] = not_found
            return res
        deleted.append(bid)
    if not deleted:
        return _error(
            f"none of those block_ids are children of {parent} — "
            "re-check them with feishu_doc_list_blocks (nested blocks need their own parent_block_id).",
            not_found=not_found,
        )
    return {
        "ok": True,
        "document_id": doc,
        "parent_block_id": parent,
        "deleted": deleted,
        "deleted_count": len(deleted),
        "not_found": not_found,
    }
