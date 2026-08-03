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

import _feishu_auth_watch as _auth_watch
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
    "需要用你的飞书身份授权一次才能继续 (机器人自己的权限做不了这一步).\n"
    "**只调一个工具**: feishu_auth_request(user_key=<sender_open_id>, "
    "capabilities=<本次 need_capabilities>, reason=<一句话说明用途>). 它按下面的优先级"
    "自动挑当前环境能用的最省事那种, 你不用自己判断:\n"
    "1. tier=card —— 卡片授权, 用户点一下就好. **这一轮立刻收尾**, 别等待、也别再把链接"
    "当文本发一遍; 等飞书把点击回调给你 (一条 <feishu_card_action>, dispatch.handler 是 "
    "feishu_auth_collect), **那一轮**调 feishu_auth_collect (用回调 value 里的 user_key) —— "
    "它把等待放到后台, 那一轮同样立刻收尾, 码到了后台自己换 token 并私聊告知用户.\n"
    "2. tier=link_auto —— 网站授权但不用复制 code. 把 authorize_url 发给用户后**这一轮收尾**, "
    "请他点完「同意授权」回你一句; 那一轮再调 feishu_auth_check 查一眼即可完成. 想让码自己回来"
    "不用用户再回话, 就在发完链接那一轮调 feishu_auth_collect (不阻塞). 无论哪条路都别在工具里"
    "干等 —— 等待会占住 turn 锁, 用户这期间说什么都排队, 看着就是机器人卡死.\n"
    "3. tier=link_manual —— 网站授权且需要复制 code (兜底). 把 authorize_url 发给用户, "
    "再让他从浏览器**地址栏**复制 code= 后面那一串 (或整段网址) 交给 feishu_auth_complete. "
    "想帮用户彻底免掉复制 (把这个部署升到前两级), 调 feishu_auth_env_check 查出确切缺哪一项"
    "配置并按它给的修法告诉用户.\n"
    "返回里有 downgraded_from/downgrade_reason 时, 如实告诉用户为什么用了更麻烦的方式, "
    "别假装走的是更顺的那条. 卡片是一次性的: 用户点了按钮但没在授权页点「同意」, 就重新调 "
    "feishu_auth_request 发一张新的.\n"
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


# ── Edit a message that was already sent ──────────────────────────────────────
#
# PUT /open-apis/im/v1/messages/:message_id replaces a sent message's content in
# place: the bubble keeps its id, its position in the chat and its thread, and
# Feishu just marks it 已编辑. That is the difference from recall+resend, which
# loses the id (breaking replies/threads that point at it) and shows everyone a
# "撤回了一条消息" notice.
#
# Only text and post messages can be edited this way. An interactive card is
# updated through PATCH on the same path (``edit_card_impl`` below); image / file /
# audio / media messages cannot be edited at all (230054) and do have to be
# recalled and re-sent.
#
# Three limits are invisible in the raw error text and are the ones editing
# actually trips over: only the *sender* may edit (230071), a message can be
# edited at most 20 times (230072), and the tenant admin configures how long a
# message stays editable (230075).

_EDIT_ERROR_HINTS = {
    230001: "请求参数不合法; 编辑只支持文本(text)和富文本(post)消息, 卡片要用 feishu_message_edit_card。",
    230002: "机器人不在该群里, 先把机器人加入群再编辑。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230011: "该消息已被撤回, 无法再编辑。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230018: "该群的设置不允许这次操作 (如全员禁言)。",
    230025: "内容超长 (文本上限约 150KB, 富文本约 30KB), 缩短后再编辑。",
    230027: "缺少编辑所需权限 (im:message / im:message:send_as_bot / im:message:update)。",
    230054: "该消息类型不支持编辑; 图片/文件/音频/视频消息只能撤回重发, 卡片用 feishu_message_edit_card。",
    230071: "只有消息的发送者能编辑它: 这条不是当前身份发的。机器人只能改自己发的消息; "
    "要改某人自己发的消息, 传该用户的 user_key 并让其完成授权。",
    230072: "该消息已达到 20 次编辑上限, 无法继续编辑。",
    230073: "密聊消息不支持编辑。",
    230074: "第三方加密群的消息不支持编辑。",
    230075: "已超出可编辑时限 (受企业管理员配置约束), 只能撤回重发。",
    230110: "该消息已被删除, 无法编辑。",
    232009: "群组已解散, 无法编辑。",
}


def _build_edit_message_request(message_id: str, msg_type: str, content: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/im/v1/messages/:message_id"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"msg_type": msg_type, "content": content}
    return req


def _require_message_id(message_id: str, what: str) -> tuple[str, dict[str, Any] | None]:
    """Normalize an ``om_...`` message id, or explain why the given value can't be one."""
    mid = message_id.strip()
    if not mid:
        return "", _error(f"message_id is required (the om_... id of the message to {what}).")
    if not mid.startswith("om_"):
        return "", _error(
            f"message_id must be a message id starting with 'om_', got {mid!r}. "
            "chat_id (oc_...) / open_id (ou_...) 不是消息 id; "
            "消息 id 来自 feishu_message_send 的返回、<feishu_context>, 或 feishu_message_list。",
        )
    return mid, None


def _with_hint(res: dict[str, Any], hints: dict[int, str]) -> dict[str, Any]:
    """Attach the human-readable cause for a known Feishu error code, if we have one."""
    hint = hints.get(res.get("code"))  # type: ignore[arg-type]
    return {**res, "hint": hint} if hint else res


async def edit_message_impl(message_id: str, text: str, user_key: str = "") -> dict[str, Any]:
    """Replace the content of an already-sent text/post message, keeping its message_id.

    ``<at>`` tags in *text* are turned into a real ``post`` mention exactly as in
    ``send_message_impl`` — a plain-text ``<at>`` renders as a raw tag — so editing a
    message to add or fix a mention works.

    Tenant-first with a UAT fallback: the bot edits its own messages with its own
    token, and passing the sender's ``user_key`` is what makes editing *that person's*
    own message possible (Feishu only lets the sender edit).
    """
    mid, bad = _require_message_id(message_id, "edit")
    if bad is not None:
        return bad
    if not text.strip():
        return _error(
            "text is required: editing replaces the whole message content, and Feishu has no empty message. "
            "要让消息消失请用 feishu_message_recall。"
        )
    stripped, at_open_ids = _extract_and_strip_at_tags(text)
    if at_open_ids:
        # Mentions only render in a post message; a plain-text <at> shows the raw tag.
        msg_type = "post"
        content = _build_post_at_content(stripped, at_open_ids, at_all=False)
    else:
        msg_type = "text"
        content = json.dumps({"text": text}, ensure_ascii=False)
    res = await _invoke(_build_edit_message_request(mid, msg_type, content), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _EDIT_ERROR_HINTS)
    return {"ok": True, "message_id": mid, "edited": True, "msg_type": msg_type}


# A card is not edited by the text/post PUT above — it has its own PATCH on the same
# path, taking only ``content`` (the whole new card). Two extra rules apply:
# the card must declare ``config.update_multi`` (both the old and the new card;
# without it Feishu refuses or updates the card for only one viewer), and a card is
# only updatable for 14 days after it was sent.
_CARD_EDIT_ERROR_HINTS = {
    230001: "请求参数不合法; 这个接口只能更新**交互卡片**消息, 文本/富文本消息用 feishu_message_edit。",
    230011: "该卡片消息已被撤回, 无法再更新。",
    230025: "卡片超长 (上限约 30KB), 精简后再更新。",
    230027: "缺少更新所需权限 (im:message / im:message:send_as_bot / im:message:update)。",
    230054: "该消息不是交互卡片, 不支持卡片更新; 文本/富文本用 feishu_message_edit。",
    230071: "只有卡片的发送者能更新它: 这条不是当前身份发的。",
    230075: "已超出可更新时限 (卡片发送 14 天内可更新)。",
    230110: "该消息已被删除, 无法更新。",
    232009: "群组已解散, 无法更新。",
}


def _build_edit_card_request(message_id: str, content: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/im/v1/messages/:message_id"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"content": content}
    return req


def _ensure_update_multi(card: dict[str, Any]) -> dict[str, Any]:
    """Make a legacy card updatable-for-everyone, which Feishu requires opt-in for.

    A legacy card without ``config.update_multi = true`` either refuses the update or
    applies it for a single viewer only — a silently half-broken result. Card 2.0
    (``{"schema": "2.0", ...}``) has no such flag and is left alone.
    """
    if str(card.get("schema", "")).startswith("2"):
        return card
    config = card.get("config")
    merged = dict(config) if isinstance(config, dict) else {}
    merged["update_multi"] = True
    return {**card, "config": merged}


async def edit_card_impl(message_id: str, card_json: str, user_key: str = "") -> dict[str, Any]:
    """Replace a sent interactive card's content in place, keeping its message_id.

    Its own endpoint (PATCH, not the text/post PUT) and its own payload: just the whole
    new card. Used to reflect state on a card that is already in the chat — mark an
    approval 已通过, disable buttons, refresh a dashboard — without the recipient losing
    the original bubble.

    The card's callback context is **not** re-registered: an already-sent card's
    handlers were snapshotted at send time and consumed on first click, so an update
    changes what the card *shows*, not what its buttons dispatch. Send a new card with
    ``send_card_impl`` when the actions themselves must change.
    """
    mid, bad = _require_message_id(message_id, "update")
    if bad is not None:
        return bad
    if not isinstance(card_json, str):
        return _error("card_json must be a JSON string containing an object")
    try:
        card = json.loads(card_json)
    except ValueError as exc:
        return _error(f"card_json is not valid JSON: {exc}")
    if not isinstance(card, dict):
        return _error(
            "card_json must be a JSON object — the full replacement card, e.g. "
            '{"schema":"2.0","body":{"elements":[...]}} or {"config":...,"elements":[...]}.'
        )
    content = json.dumps(_ensure_update_multi(card), ensure_ascii=False)
    res = await _invoke(_build_edit_card_request(mid, content), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _CARD_EDIT_ERROR_HINTS)
    return {"ok": True, "message_id": mid, "edited": True, "msg_type": "interactive"}


# ── Emoji reactions on a message ───────────────────────────────────────────────
#
# A reaction is the lightest possible acknowledgement: 收到 / 已处理 / 赞 without
# adding a message to the chat. Three endpoints under
# im/v1/messages/:message_id/reactions — POST to add (returns a reaction_id), DELETE
# .../:reaction_id to remove, GET to list.
#
# Removal needs the reaction_id, and only the identity that added a reaction can
# remove it. Rather than make the caller carry ids around, ``remove_reaction_impl``
# accepts an ``emoji_type`` and resolves it through the list endpoint, keeping the
# tool symmetric with add (same argument removes what it added).
#
# ``emoji_type`` values come from Feishu's emoji table and are **case-sensitive and
# inconsistently cased** (``THUMBSUP``/``OK``/``DONE`` but ``Fire``/``OnIt``/``Get``),
# so a wrong guess yields 231001. The common ones are aliased below.
_REACTION_ERROR_HINTS = {
    230110: "该消息已被删除, 无法操作表情回应。",
    231001: "emoji_type 不是飞书支持的值 (大小写敏感, 如 THUMBSUP / OK / DONE / Fire); 换一个再试。",
    231002: "当前身份不在该消息所在会话里, 先把机器人加入群 (或换成群内成员的 user_key)。",
    231003: "找不到该消息 (id 有误或已撤回)。",
    231004: "该会话不存在、已解散或已归档。",
    231008: "当前身份无权访问该消息。",
    231017: "该消息类型不支持表情回应 (如系统消息)。",
    231018: "该消息对当前身份不可见。",
    231021: "外部群里没有操作表情回应的权限。",
    231022: "机器人对该用户不可用 (把该用户加入应用可用范围后重新发布)。",
    232009: "群组已解散, 无法操作表情回应。",
}

# 中文/口语说法 → 飞书 emoji_type。飞书的枚举大小写混乱 (THUMBSUP 全大写, Fire 首字母大写),
# 模型按字面猜十次错九次, 所以常用的这些一律先过一遍映射, 并且大小写不敏感地兜住。
_EMOJI_ALIASES = {
    "赞": "THUMBSUP",
    "点赞": "THUMBSUP",
    "👍": "THUMBSUP",
    "好的": "OK",
    "ok": "OK",
    "👌": "OK",
    "完成": "DONE",
    "已完成": "DONE",
    "收到": "OnIt",
    "在办": "OnIt",
    "处理中": "OnIt",
    "感谢": "THANKS",
    "谢谢": "THANKS",
    "鼓掌": "APPLAUSE",
    "👏": "APPLAUSE",
    "笑": "SMILE",
    "😄": "SMILE",
    "心": "HEART",
    "❤️": "HEART",
    "爱心": "HEART",
    "火": "Fire",
    "🔥": "Fire",
    "庆祝": "PARTY",
    "🎉": "PARTY",
    "加油": "JIAYI",
    "对勾": "CheckMark",
    "✅": "DONE",
    "打勾": "CheckMark",
    "叉": "CrossMark",
    "❌": "CrossMark",
}
# The canonical spelling for values whose casing is the usual mistake, keyed lowercase.
_EMOJI_CANONICAL = {
    v.lower(): v
    for v in (
        "THUMBSUP",
        "OK",
        "DONE",
        "SMILE",
        "HEART",
        "APPLAUSE",
        "CLAP",
        "PRAISE",
        "THANKS",
        "LGTM",
        "Fire",
        "PARTY",
        "OnIt",
        "JIAYI",
        "Get",
        "CheckMark",
        "CrossMark",
        "Hundred",
        "Trophy",
        "FIREWORKS",
        "ROSE",
        "MUSCLE",
        "WAVE",
        "LAUGH",
        "CRY",
        "THINKING",
        "ThumbsDown",
        "MinusOne",
    )
}


def _normalize_emoji_type(emoji_type: str) -> str:
    """Map a Chinese word / emoji character / mis-cased key onto a Feishu ``emoji_type``.

    Unknown values pass through untouched: Feishu's table is ~130 entries and grows,
    so an unrecognized value is sent as given (and answered with 231001) rather than
    rejected here by a list that would go stale.
    """
    raw = emoji_type.strip()
    if not raw:
        return ""
    alias = _EMOJI_ALIASES.get(raw) or _EMOJI_ALIASES.get(raw.lower())
    if alias:
        return alias
    return _EMOJI_CANONICAL.get(raw.lower(), raw)


def _build_add_reaction_request(message_id: str, emoji_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/messages/:message_id/reactions"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"reaction_type": {"emoji_type": emoji_type}}
    return req


def _build_remove_reaction_request(message_id: str, reaction_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/im/v1/messages/:message_id/reactions/:reaction_id"
    req.paths["message_id"] = message_id
    req.paths["reaction_id"] = reaction_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_list_reactions_request(message_id: str, emoji_type: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages/:message_id/reactions"
    req.paths["message_id"] = message_id
    if emoji_type:
        req.add_query("reaction_type", emoji_type)
    req.add_query("page_size", max(1, min(page_size, 50)))
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _reaction_record(item: Any) -> dict[str, Any]:
    """One reaction as {reaction_id, emoji_type, operator_id, operator_type, action_time}."""
    if not isinstance(item, dict):
        return {}
    reaction_type = item.get("reaction_type")
    operator = item.get("operator")
    return {
        "reaction_id": item.get("reaction_id", ""),
        "emoji_type": (reaction_type or {}).get("emoji_type", "") if isinstance(reaction_type, dict) else "",
        "operator_id": (operator or {}).get("operator_id", "") if isinstance(operator, dict) else "",
        "operator_type": (operator or {}).get("operator_type", "") if isinstance(operator, dict) else "",
        "action_time": item.get("action_time", ""),
    }


async def add_reaction_impl(message_id: str, emoji_type: str, user_key: str = "") -> dict[str, Any]:
    """React to a message with an emoji — an acknowledgement that adds no message.

    ``emoji_type`` accepts a Feishu key (``THUMBSUP``), a Chinese word (``赞``,
    ``收到``) or the emoji itself (``👍``); all three are normalized to the key
    Feishu expects, whose casing is irregular enough that a literal guess usually
    fails with 231001.

    Returns the ``reaction_id``. Keep it if you want to remove exactly this reaction
    later, though ``remove_reaction_impl`` can also find it from the emoji.
    """
    mid, bad = _require_message_id(message_id, "react to")
    if bad is not None:
        return bad
    emoji = _normalize_emoji_type(emoji_type)
    if not emoji:
        return _error("emoji_type is required (e.g. THUMBSUP / OK / DONE / OnIt, or 赞 / 收到 / 完成).")
    res = await _invoke(_build_add_reaction_request(mid, emoji), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _REACTION_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "message_id": mid, **_reaction_record(data), "emoji_type": emoji}


async def list_reactions_impl(
    message_id: str, emoji_type: str = "", page_size: int = 50, page_token: str = "", user_key: str = ""
) -> dict[str, Any]:
    """List a message's reactions — who reacted with what, and each ``reaction_id``."""
    mid, bad = _require_message_id(message_id, "list reactions of")
    if bad is not None:
        return bad
    emoji = _normalize_emoji_type(emoji_type)
    res = await _invoke(
        _build_list_reactions_request(mid, emoji, page_size, page_token.strip()),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _REACTION_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    raw_items = data.get("items")
    items: list[Any] = raw_items if isinstance(raw_items, list) else []
    reactions = [r for r in (_reaction_record(i) for i in items) if r]
    return {
        "ok": True,
        "message_id": mid,
        "reactions": reactions,
        "count": len(reactions),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", ""),
    }


async def remove_reaction_impl(
    message_id: str, emoji_type: str = "", reaction_id: str = "", user_key: str = ""
) -> dict[str, Any]:
    """Remove a reaction, addressed either by ``reaction_id`` or by its emoji.

    Feishu deletes by ``reaction_id`` and only lets the identity that added a reaction
    remove it. Given an ``emoji_type`` instead, the message's reactions are listed and
    the matching one is resolved — so "把刚才那个赞取消" works from the same argument
    that added it, without the caller having stored an id.

    Resolution stays deliberately strict: if several reactions share that emoji (added
    by different people), the ids are returned and nothing is deleted rather than
    guessing whose to take back.
    """
    mid, bad = _require_message_id(message_id, "remove a reaction from")
    if bad is not None:
        return bad
    rid = reaction_id.strip()
    emoji = _normalize_emoji_type(emoji_type)
    if not rid:
        if not emoji:
            return _error("pass either reaction_id, or emoji_type (e.g. THUMBSUP / 赞) to look it up.")
        listed = await list_reactions_impl(mid, emoji, page_size=50, user_key=user_key)
        if not listed["ok"]:
            return listed
        matches = [r for r in listed["reactions"] if r["emoji_type"] == emoji and r["reaction_id"]]
        if not matches:
            return _error(
                f"没有找到 emoji_type={emoji!r} 的表情回应 (可能本来没加, 或已被取消)。",
                message_id=mid,
                emoji_type=emoji,
                code="reaction_not_found",
            )
        if len(matches) > 1:
            return _error(
                f"该消息上有 {len(matches)} 个 {emoji!r} 表情回应 (不同人加的), 无法确定要取消哪一个; "
                "从 candidates 里挑一个 reaction_id 再调一次 (只能取消自己加的那个)。",
                message_id=mid,
                emoji_type=emoji,
                candidates=matches,
                code="reaction_ambiguous",
            )
        rid = matches[0]["reaction_id"]
    res = await _invoke(_build_remove_reaction_request(mid, rid), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _REACTION_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    # The echoed record first, then the ids we know: Feishu's delete response omits
    # fields for some message types, and an empty echo must not blank out the answer.
    return {"ok": True, **_reaction_record(data), "message_id": mid, "reaction_id": rid, "removed": True}


# ── Rich media messages — image / file / audio / video / rich text ──────────────
#
# Sending anything but text is always two calls: upload the bytes to get a key, then
# send a message whose content references that key. Two *different* upload endpoints,
# and picking the wrong one is the usual failure:
#
#   im/v1/images  → image_key (img_v3_...)  — pictures only, ≤10MB
#   im/v1/files   → file_key  (file_v3_...) — documents, audio, video, ≤30MB
#
# These are IM-message uploads, unrelated to drive medias/upload_all (which puts a
# file in the cloud drive / a doc block, see upload_media_impl). A drive file_token
# cannot be sent as a message and vice versa.
#
# Both go out as multipart, which under this SDK means the binary must sit in the
# request **body** as an io.IOBase carrying a .name — Client.arequest overwrites
# req.files with Files.extract_files(req.body) right before sending, so a file put in
# req.files is dropped and the request leaves as application/json ("boundary not
# found"). Same reason _NamedBytes exists for drive uploads.
_IMAGE_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
_FILE_UPLOAD_MAX_BYTES = 30 * 1024 * 1024

# What Feishu accepts for im/v1/images. TIFF/HEIC are converted to JPG server-side.
_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".ico", ".tif", ".tiff", ".heic"}
# file_type for im/v1/files is an enum, not the extension: audio must be opus, video
# mp4, documents their own four, and anything else falls back to "stream" (which is
# what a .zip/.csv/.txt attachment is sent as).
_FILE_TYPE_BY_SUFFIX = {
    ".opus": "opus",
    ".mp4": "mp4",
    ".pdf": "pdf",
    ".doc": "doc",
    ".docx": "doc",
    ".xls": "xls",
    ".xlsx": "xls",
    ".ppt": "ppt",
    ".pptx": "ppt",
}
_FILE_TYPES = {"opus", "mp4", "pdf", "doc", "xls", "ppt", "stream"}
# msg_type → which upload endpoint feeds it, so one send path serves all of them.
_MEDIA_MSG_TYPES = {"image": "image", "file": "file", "audio": "file", "media": "file"}
_UPLOAD_ERROR_HINTS = {
    234001: "上传参数不合法 (image_type / file_type / file_name 有问题)。",
    234002: "上传鉴权失败, 检查 PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET。",
    234006: "文件超过大小上限 (图片 10MB, 文件 30MB)。",
    234007: "应用未启用机器人能力, 到开发者后台开启后再试。",
    234010: "文件是空的 (0 字节), 飞书拒收。",
    234011: "无法识别的图片格式; 支持 JPG/JPEG/PNG/WEBP/GIF/BMP/ICO/TIFF/HEIC。",
    234039: "图片分辨率超限 (GIF 2000x2000, 其它 12000x12000); 改用文件方式发送。",
}
_SEND_MEDIA_ERROR_HINTS = {
    230001: "请求参数不合法; 常见原因是 image_key 与 file_key 用反了 (图片用 image_key, 音视频/文件用 file_key)。",
    230002: "机器人不在该群里, 先把机器人加入群。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230055: "上传时的 file_type 与消息类型不一致 (音频要 opus, 视频要 mp4)。",
}


def _build_image_upload_request(image_type: str, file_name: str, data: bytes) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/images"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    # Binary in the body (not req.files) — see the note above _IMAGE_UPLOAD_MAX_BYTES.
    req.body = {"image_type": image_type, "image": _NamedBytes(data, file_name)}
    return req


def _build_file_upload_request(file_type: str, file_name: str, data: bytes, duration_ms: int) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/files"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"file_type": file_type, "file_name": file_name}
    if duration_ms > 0:
        body["duration"] = duration_ms
    body["file"] = _NamedBytes(data, file_name)
    req.body = body
    return req


async def _read_upload_bytes(file_path: str, limit: int, what: str) -> tuple[bytes, str, dict[str, Any] | None]:
    """Read a local file for upload; returns (data, name, error) with error set on refusal."""
    p = anyio.Path(file_path)
    if not await p.is_file():
        return b"", "", _error(f"file not found: {file_path}")
    data = await p.read_bytes()
    if not data:
        return b"", "", _error(f"{file_path} is empty (0 bytes); Feishu rejects empty uploads.")
    if len(data) > limit:
        return (
            b"",
            "",
            _error(
                f"{what} is {len(data)} bytes, over the {limit // (1024 * 1024)}MB limit for this endpoint. "
                "更大的文件先上传到云盘 (feishu_drive_upload) 再把链接发出去。",
                size=len(data),
            ),
        )
    return data, p.name, None


async def upload_image_impl(image_path: str, user_key: str = "") -> dict[str, Any]:
    """Upload a picture for use in messages; returns its ``image_key`` (``img_v3_...``).

    Separate from ``upload_media_impl`` (cloud drive): only an IM ``image_key`` can be
    sent as an image message or embedded in a post, and only a drive ``file_token``
    can live in a document.
    """
    data, name, bad = await _read_upload_bytes(image_path, _IMAGE_UPLOAD_MAX_BYTES, "image")
    if bad is not None:
        return bad
    suffix = pathlib.Path(name).suffix.lower()
    if suffix and suffix not in _IMAGE_SUFFIXES:
        return _error(
            f"{name} is not an image Feishu accepts ({', '.join(sorted(_IMAGE_SUFFIXES))}). "
            "非图片文件用 feishu_message_send_file 发送。",
        )
    # A factory: the SDK consumes the file entry on the first send, and this may be
    # retried under a second identity.
    res = await _invoke(
        lambda: _build_image_upload_request("message", name, data),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _UPLOAD_ERROR_HINTS)
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "image_key": rdata.get("image_key", ""), "file_name": name, "size": len(data)}


async def upload_file_impl(
    file_path: str, file_type: str = "", file_name: str = "", duration_ms: int = 0, user_key: str = ""
) -> dict[str, Any]:
    """Upload a document/audio/video for use in messages; returns its ``file_key``.

    ``file_type`` is Feishu's enum, not the extension — it is derived from the suffix
    (``.mp4``→mp4, ``.pdf``→pdf, ``.docx``→doc, …) and anything unmapped uploads as
    ``stream``, which is how a .zip/.csv/.txt attachment is sent.

    Audio must genuinely be OPUS: Feishu plays an ``audio`` message only for
    ``file_type=opus``, and sending an .mp3 as audio is rejected with 230055. Convert
    first (``ffmpeg -i in.mp3 -acodec libopus -ac 1 -ar 16000 out.opus``) or send the
    .mp3 as a plain file instead.
    """
    data, name, bad = await _read_upload_bytes(file_path, _FILE_UPLOAD_MAX_BYTES, "file")
    if bad is not None:
        return bad
    name = file_name.strip() or name
    ftype = file_type.strip() or _FILE_TYPE_BY_SUFFIX.get(pathlib.Path(name).suffix.lower(), "stream")
    if ftype not in _FILE_TYPES:
        return _error(
            f"file_type must be one of {', '.join(sorted(_FILE_TYPES))}, got {ftype!r} "
            "(it is Feishu's enum, not the file extension; unlisted formats use 'stream').",
        )
    res = await _invoke(
        lambda: _build_file_upload_request(ftype, name, data, max(0, duration_ms)),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _UPLOAD_ERROR_HINTS)
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "file_key": rdata.get("file_key", ""), "file_name": name, "file_type": ftype, "size": len(data)}


async def send_media_message_impl(
    receive_id: str,
    file_path: str,
    msg_type: str,
    receive_id_type: str = "chat_id",
    cover_image_path: str = "",
    file_name: str = "",
    duration_ms: int = 0,
    user_key: str = "",
) -> dict[str, Any]:
    """Upload a local file and send it as an image / file / audio / video message.

    Both halves of the two-call dance in one place, because doing them separately is
    where the keys get crossed: ``msg_type`` decides which upload endpoint runs
    (``image`` → im/v1/images → ``image_key``; everything else → im/v1/files →
    ``file_key``) and what the message content looks like.

    ``media`` (video) may carry a cover: ``cover_image_path`` is uploaded as an image
    and referenced as the thumbnail. Without one the video shows no preview frame.
    """
    kind = msg_type.strip().lower()
    if kind not in _MEDIA_MSG_TYPES:
        return _error(
            f"msg_type must be one of {', '.join(sorted(_MEDIA_MSG_TYPES))}, got {msg_type!r}. "
            "image=图片, file=文档/附件, audio=语音(opus), media=视频(mp4)。",
        )
    if kind == "image":
        uploaded = await upload_image_impl(file_path, user_key=user_key)
        if not uploaded["ok"]:
            return uploaded
        content: dict[str, Any] = {"image_key": uploaded["image_key"]}
        detail = {"image_key": uploaded["image_key"]}
    else:
        forced = {"audio": "opus", "media": "mp4"}.get(kind, "")
        uploaded = await upload_file_impl(
            file_path, file_type=forced, file_name=file_name, duration_ms=duration_ms, user_key=user_key
        )
        if not uploaded["ok"]:
            return uploaded
        content = {"file_key": uploaded["file_key"]}
        detail = {"file_key": uploaded["file_key"], "file_type": uploaded["file_type"]}
        if kind == "media" and cover_image_path.strip():
            cover = await upload_image_impl(cover_image_path.strip(), user_key=user_key)
            if not cover["ok"]:
                # The video is uploaded and sendable; a missing cover must not lose it.
                logger.warning(f"video cover upload failed, sending without a cover — {cover.get('message', '')}")
            else:
                content["image_key"] = cover["image_key"]
                detail["cover_image_key"] = cover["image_key"]
    rid_type = _infer_receive_id_type(receive_id, receive_id_type)
    req = _build_send_message_request(receive_id, rid_type, kind, json.dumps(content, ensure_ascii=False))
    res = await _invoke(req, user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, **detail}, _SEND_MEDIA_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", ""),
        "msg_type": kind,
        "size": uploaded["size"],
        **detail,
    }


# A post message is the only way to put text, pictures, links and mentions in **one**
# bubble. Its content is a list of paragraphs, each a list of nodes — so the tool takes
# a compact block list and expands it, uploading any local image on the way. Feishu
# requires img and media nodes to occupy a paragraph of their own, which the builder
# enforces rather than leaving to the caller.
_POST_BLOCK_TAGS = {"text", "a", "at", "img", "code_block", "hr", "md"}


def _post_node(block: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """One post node from a compact block dict; returns (node, error message)."""
    tag = str(block.get("tag", "text")).strip() or "text"
    if tag not in _POST_BLOCK_TAGS:
        return None, f"unsupported tag {tag!r}; use one of {', '.join(sorted(_POST_BLOCK_TAGS))}"
    if tag == "hr":
        return {"tag": "hr"}, ""
    if tag == "at":
        user_id = str(block.get("user_id", "")).strip()
        if not user_id:
            return None, "an 'at' block needs user_id (an ou_... open_id, or \"all\")"
        return {"tag": "at", "user_id": user_id}, ""
    if tag == "a":
        href = str(block.get("href", "")).strip()
        if not href:
            return None, "an 'a' block needs href"
        return {"tag": "a", "text": str(block.get("text", "")) or href, "href": href}, ""
    if tag == "img":
        # image_path is resolved to an image_key by the caller before we get here.
        image_key = str(block.get("image_key", "")).strip()
        if not image_key:
            return None, "an 'img' block needs image_key or image_path"
        return {"tag": "img", "image_key": image_key}, ""
    text = block.get("text")
    if not isinstance(text, str) or not text:
        return None, f"a {tag!r} block needs non-empty text"
    if tag == "code_block":
        node: dict[str, Any] = {"tag": "code_block", "text": text}
        language = str(block.get("language", "")).strip()
        if language:
            node["language"] = language
        return node, ""
    if tag == "md":
        return {"tag": "md", "text": text}, ""
    node = {"tag": "text", "text": text}
    style = block.get("style")
    if isinstance(style, list) and style:
        node["style"] = [str(s) for s in style]
    return node, ""


def _build_post_content(title: str, nodes: list[dict[str, Any]]) -> str:
    """Group post nodes into paragraphs: img/hr/md stand alone, runs of text merge."""
    paragraphs: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    for node in nodes:
        if node["tag"] in {"img", "hr", "md"}:
            if current:
                paragraphs.append(current)
                current = []
            paragraphs.append([node])
            continue
        current.append(node)
    if current:
        paragraphs.append(current)
    return json.dumps({"zh_cn": {"title": title, "content": paragraphs}}, ensure_ascii=False)


async def send_post_message_impl(
    receive_id: str,
    blocks_json: str,
    title: str = "",
    receive_id_type: str = "chat_id",
    user_key: str = "",
) -> dict[str, Any]:
    """Send a **rich text** (post) message: styled text, links, mentions and images in one bubble.

    ``blocks_json`` is a JSON array of compact blocks, in order, e.g.::

        [{"tag": "text", "text": "本周周报", "style": ["bold"]},
         {"tag": "at", "user_id": "ou_xxx"},
         {"tag": "a", "text": "看板", "href": "https://..."},
         {"tag": "img", "image_path": "C:/tmp/chart.png"},
         {"tag": "md", "text": "1. 第一项\\n2. 第二项"}]

    An ``img`` block may name a local ``image_path`` (uploaded here) or an existing
    ``image_key``. Blocks are grouped into paragraphs the way Feishu requires — images,
    separators and markdown each get their own line, adjacent text/link/mention nodes
    share one — so the caller writes a flat list and gets a correct layout.
    """
    if not isinstance(blocks_json, str):
        return _error("blocks_json must be a JSON string containing an array of blocks")
    try:
        blocks = json.loads(blocks_json)
    except ValueError as exc:
        return _error(f"blocks_json is not valid JSON: {exc}")
    if not isinstance(blocks, list) or not blocks:
        return _error(
            'blocks_json must be a non-empty JSON array, e.g. [{"tag":"text","text":"hi"},'
            '{"tag":"img","image_path":"C:/tmp/a.png"}]'
        )
    nodes: list[dict[str, Any]] = []
    uploaded_keys: list[str] = []
    for position, raw_block in enumerate(blocks):
        if not isinstance(raw_block, dict):
            return _error(f"block #{position} is not a JSON object", block_index=position)
        block: dict[str, Any] = {str(k): v for k, v in raw_block.items()}
        if str(block.get("tag", "")).strip() == "img" and not str(block.get("image_key", "")).strip():
            path = str(block.get("image_path", "")).strip()
            if not path:
                return _error(f"block #{position}: an 'img' block needs image_path or image_key", block_index=position)
            up = await upload_image_impl(path, user_key=user_key)
            if not up["ok"]:
                return {**up, "block_index": position}
            block["image_key"] = up["image_key"]
            uploaded_keys.append(up["image_key"])
        node, err = _post_node(block)
        if node is None:
            return _error(f"block #{position}: {err}", block_index=position)
        nodes.append(node)
    rid_type = _infer_receive_id_type(receive_id, receive_id_type)
    content = _build_post_content(title.strip(), nodes)
    res = await _invoke(
        _build_send_message_request(receive_id, rid_type, "post", content), user_key=user_key, prefer="tenant"
    )
    if not res["ok"]:
        return _with_hint(res, _SEND_MEDIA_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "message_id": data.get("message_id", ""),
        "thread_id": data.get("thread_id", ""),
        "chat_id": data.get("chat_id", ""),
        "msg_type": "post",
        "blocks": len(nodes),
        "uploaded_image_keys": uploaded_keys,
    }


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


# ── Group administration — read a group's settings, add/remove members ───────
#
# ``create_chat_impl`` could only pull people in at creation time; running a group
# afterwards needs the roster to be editable and its settings to be readable. Both
# halves share Feishu's im/v1 chat errors, so the hint table is shared too. The
# 232017 case is the one that actually bites: most groups restrict 加人 to
# owner/admin, and the bot is neither unless it created the group — so the caller
# must pass that person's ``user_key`` rather than expect the bot to manage.
_CHAT_ADMIN_ERROR_HINTS = {
    232006: "chat_id 无效; 用 feishu_chat_find 重新解析群名到 chat_id。",
    232009: "群已解散, 无法操作。",
    232010: "机器人与该群不在同一租户 (外部群), 内部接口管不了。",
    232011: "机器人不在该群里, 先把机器人加入群。",
    232013: "群成员数已达上限 (普通群/话题群 5000, 会议群 3000)。",
    232014: "token 缺少所需权限 (im:chat 或 im:chat.members:write_only)。",
    232017: "该群限定「仅群主和群管理员可添加成员」, 机器人不是群主/管理员; "
    "传群主或管理员的 user_key 以本人身份操作, 或请他们把该设置改为「所有群成员」。",
    232019: "同一个群被并发操作触发限流; 串行调用重试。",
    232024: "机器人对该用户不可见, 或双方无协作权限; 检查应用可用范围。",
    232025: "应用未启用机器人能力, 到开发者后台开启后再试。",
    232027: "id_list 里没有有效成员。",
    232028: "外部成员不能加入内部群。",
    232033: "无操作外部群的权限。",
    232034: "应用在该租户未安装或未启用。",
    232043: "列表里含不可用的 ID; 核对后重试。",
    232044: "达到企业管理员配置的成员上限, 需管理员放开。",
    232076: "群主不能被移出群; 先转让群主再移出。",
    232090: "群类型不支持该操作 (仅普通群 group / 话题群 topic)。",
    99992351: "open_id 必须是 ou_ 前缀; 用 feishu_chat_find_member / feishu_contact_search 解析。",
}
# Feishu returns every group setting as a bare enum string. Naming them once here
# keeps the tool's answer readable ("只有群主能加人") instead of making the model
# guess what only_owner means in each of eight different fields.
_CHAT_WHO = {
    "only_owner": "仅群主和管理员",
    "all_members": "所有群成员",
    "not_anyone": "任何人都不可",
    "moderator_list": "指定人员",
    "allowed": "允许",
    "not_allowed": "不允许",
}
_CHAT_SETTING_FIELDS = (
    ("add_member_permission", "谁可以加人"),
    ("share_card_permission", "是否可分享群名片"),
    ("at_all_permission", "谁可以@所有人"),
    ("edit_permission", "谁可以编辑群信息"),
    ("membership_approval", "入群是否需审批"),
    ("moderation_permission", "谁可以发言"),
    ("join_message_visibility", "入群消息对谁可见"),
    ("leave_message_visibility", "退群消息对谁可见"),
    ("urgent_setting", "谁可以加急"),
    ("video_conference_setting", "谁可以发起视频会议"),
    ("hide_member_count_setting", "对谁隐藏成员数"),
)


def _build_get_chat_request(chat_id: str, user_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/chats/:chat_id"
    req.paths["chat_id"] = chat_id
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _chat_settings(data: dict[str, Any]) -> dict[str, str]:
    """Group settings as {人话标签: 人话取值}, skipping fields Feishu didn't return."""
    out: dict[str, str] = {}
    for key, label in _CHAT_SETTING_FIELDS:
        raw = data.get(key)
        if not isinstance(raw, str) or not raw:
            continue
        if key == "membership_approval":
            out[label] = "需审批" if raw == "approval_required" else "无需审批"
            continue
        out[label] = _CHAT_WHO.get(raw, raw)
    restricted = data.get("restricted_mode_setting")
    if isinstance(restricted, dict) and restricted.get("status"):
        out["保密模式"] = "已开启"
        for key, label in (
            ("screenshot_has_permission_setting", "可截屏录屏"),
            ("download_has_permission_setting", "可下载图片/视频/文件"),
            ("message_has_permission_setting", "可复制转发"),
        ):
            raw = restricted.get(key)
            if isinstance(raw, str) and raw:
                out[label] = _CHAT_WHO.get(raw, raw)
    return out


async def get_chat_impl(chat_id: str, user_id_type: str = "open_id", user_key: str = "") -> dict[str, Any]:
    """Read a group's owner, member counts, and settings.

    Feishu deliberately answers a **non-member** caller with only name/avatar/counts
    /status, so a thin result is not an error — ``partial`` says so rather than letting
    the caller report "这个群没有群主". ``owner_id`` is also absent when the owner is a
    bot, which is why the two cases are distinguished in the result.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    res = await _invoke(_build_get_chat_request(cid, user_id_type), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _CHAT_ADMIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    owner_id = data.get("owner_id", "") or ""
    # user_count/bot_count come back as strings; a count is only useful as a number.
    counts: dict[str, Any] = {}
    for key in ("user_count", "bot_count"):
        raw = data.get(key)
        if isinstance(raw, str | int):
            with contextlib.suppress(TypeError, ValueError):
                counts[key] = int(raw)
    return {
        "ok": True,
        "chat_id": cid,
        "name": data.get("name", ""),
        "description": data.get("description", ""),
        "owner_id": owner_id,
        "owner_id_type": data.get("owner_id_type", "") or (user_id_type if owner_id else ""),
        "owner_is_bot": not owner_id and data.get("chat_mode") != "p2p",
        **counts,
        "user_manager_ids": data.get("user_manager_id_list") or [],
        "bot_manager_app_ids": data.get("bot_manager_id_list") or [],
        "chat_mode": data.get("chat_mode", ""),
        "chat_type": data.get("chat_type", ""),
        "chat_tag": data.get("chat_tag", ""),
        "chat_status": data.get("chat_status", ""),
        "external": bool(data.get("external")),
        "settings": _chat_settings(data),
        "avatar": data.get("avatar", ""),
        # A caller outside the group gets a stub; say so instead of implying the group
        # has no owner or no settings.
        "partial": not owner_id and not data.get("chat_mode"),
    }


def _build_chat_members_change_request(
    chat_id: str, id_list: list[str], member_id_type: str, add: bool, succeed_type: int
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST if add else HttpMethod.DELETE
    req.uri = "/open-apis/im/v1/chats/:chat_id/members"
    req.paths["chat_id"] = chat_id
    req.add_query("member_id_type", member_id_type)
    if add:
        req.add_query("succeed_type", succeed_type)
    req.body = {"id_list": id_list}
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


_CHAT_MEMBER_ID_TYPES = ("open_id", "union_id", "user_id", "app_id")


def _clean_member_ids(user_ids: list[str] | None, member_id_type: str, verb: str) -> tuple[list[str], dict | None]:
    """Validate a member id list for add/remove; returns (ids, error)."""
    if member_id_type not in _CHAT_MEMBER_ID_TYPES:
        return [], _error(f"member_id_type must be one of {', '.join(_CHAT_MEMBER_ID_TYPES)}, got {member_id_type!r}.")
    ids = list(dict.fromkeys(u.strip() for u in (user_ids or []) if u and u.strip()))
    if not ids:
        return [], _error(f"user_ids is required — give at least one id to {verb}.")
    if len(ids) > 50:
        return [], _error(
            f"Feishu takes at most 50 ids per call ({len(ids)} given); split the list and call again. "
            "机器人一次最多 5 个。",
        )
    return ids, None


async def add_chat_members_impl(
    chat_id: str,
    user_ids: list[str] | None = None,
    member_id_type: str = "open_id",
    succeed_type: int = 1,
    user_key: str = "",
) -> dict[str, Any]:
    """Add people (or bots) to an existing group.

    ``succeed_type=1`` is the default deliberately: Feishu's own default (0) fails the
    **whole** call over one unreachable id, so adding nine reachable people would add
    nobody. With 1 the reachable ones go in and the rest come back classified, which is
    what a caller can actually act on.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    ids, bad = _clean_member_ids(user_ids, member_id_type, "add")
    if bad is not None:
        return bad
    if succeed_type not in (0, 1, 2):
        return _error("succeed_type must be 0 (all-or-nothing), 1 (add what's reachable), or 2 (strict).")
    res = await _invoke(
        _build_chat_members_change_request(cid, ids, member_id_type, True, succeed_type),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid, "requested": ids}, _CHAT_ADMIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    invalid = data.get("invalid_id_list") or []
    missing = data.get("not_existed_id_list") or []
    pending = data.get("pending_approval_id_list") or []
    added = [i for i in ids if i not in {*invalid, *missing, *pending}]
    return {
        "ok": True,
        "chat_id": cid,
        "member_id_type": member_id_type,
        "requested": ids,
        "added": added,
        "added_count": len(added),
        # Kept apart because the fixes differ: unreachable (scope/离职) vs nonexistent
        # id vs waiting on the owner's approval — the last one *will* join later.
        "invalid_ids": invalid,
        "not_existed_ids": missing,
        "pending_approval_ids": pending,
    }


async def remove_chat_members_impl(
    chat_id: str,
    user_ids: list[str] | None = None,
    member_id_type: str = "open_id",
    user_key: str = "",
) -> dict[str, Any]:
    """Remove people (or bots) from a group.

    Only the owner, an admin, or the bot that created the group may remove **others**
    (232017), and the owner can never be removed (232076) — both surface as hints
    rather than as a bare error code.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    ids, bad = _clean_member_ids(user_ids, member_id_type, "remove")
    if bad is not None:
        return bad
    res = await _invoke(
        _build_chat_members_change_request(cid, ids, member_id_type, False, 0),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid, "requested": ids}, _CHAT_ADMIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    invalid = data.get("invalid_id_list") or []
    removed = [i for i in ids if i not in set(invalid)]
    return {
        "ok": True,
        "chat_id": cid,
        "member_id_type": member_id_type,
        "requested": ids,
        "removed": removed,
        "removed_count": len(removed),
        "invalid_ids": invalid,
    }


# ── 群公告 (chat announcement) — read and write the pinned notice board ──────────
#
# An announcement is not a message: it is a *document* hanging off the chat, so it is
# read and written with the docx block APIs (docx/v1/chats/:chat_id/announcement/...)
# rather than im/v1. That is the whole reason this needs a tool instead of one
# feishu_api call — writing one requires three separate facts to line up:
#
# 1. There are TWO generations of announcement. The legacy one (im/v1 .../announcement,
#    old-doc serialization) and the docx one. Feishu refuses cross-generation calls with
#    232097, and every group created in recent years is docx. So only the docx endpoints
#    are used here, and 232097 is translated rather than passed through as a bare code.
# 2. Every write is optimistic-locked on ``revision_id``. Sending a stale one fails, so
#    the revision is always read immediately before writing instead of being asked of
#    the caller — an agent has no way to know it.
# 3. The root block_id of an announcement is the ``chat_id`` itself (the same trick docx
#    uses, where document_id doubles as the root block_id). Guessing anything else here
#    produces a 404 that reads like "no announcement".
_ANNOUNCEMENT_ERROR_HINTS = {
    232001: "参数不合法; 检查 chat_id 是不是 oc_ 开头的群 (单聊 p2p 没有群公告)。",
    232002: "该群限定「仅群主和管理员可编辑群信息」; 传群主/管理员的 user_key 以本人身份改, 或请他们放开该设置。",
    232003: "群公告数据异常, 稍后重试。",
    232010: "操作者与该群不在同一租户 (外部群), 内部接口管不了。",
    232011: "调用者不在该群里; 先把机器人 (或本人) 加入群。",
    232018: "更新失败, 请求结构有问题 (检查 content 是否为空)。",
    232019: "同一个群被并发操作触发限流; 串行调用重试。",
    232024: "群公告可见性或协作权限不足。",
    232025: "应用未启用机器人能力, 到开发者后台开启后再试。",
    232033: "外部群不支持该操作。",
    232034: "应用在该租户未安装或未启用。",
    232066: "缺少群公告文档的阅读权限; 让群主把公告共享给机器人, 或传本人 user_key。",
    232097: "这是旧版 (非 docx) 群公告, 本工具的 docx 端点操作不了; "
    "请群主在群里手动把公告重建一次 (新建的即为 docx 版), 或改用旧版接口。",
}


def _build_announcement_get_request(chat_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/docx/v1/chats/:chat_id/announcement"
    req.paths["chat_id"] = chat_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_announcement_blocks_request(chat_id: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/docx/v1/chats/:chat_id/announcement/blocks"
    req.paths["chat_id"] = chat_id
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_announcement_children_request(
    chat_id: str, children: list[dict[str, Any]], revision_id: int, index: int
) -> BaseRequest:
    """Append blocks under the announcement root (whose block_id IS the chat_id)."""
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/docx/v1/chats/:chat_id/announcement/blocks/:block_id/children"
    req.paths["chat_id"] = chat_id
    req.paths["block_id"] = chat_id
    req.add_query("revision_id", revision_id)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"children": children}
    if index >= 0:
        body["index"] = index
    req.body = body
    return req


def _build_announcement_delete_request(chat_id: str, start: int, end: int, revision_id: int) -> BaseRequest:
    """Delete children [start, end) of the announcement root — the range is half-open."""
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/docx/v1/chats/:chat_id/announcement/blocks/:block_id/children/batch_delete"
    req.paths["chat_id"] = chat_id
    req.paths["block_id"] = chat_id
    req.add_query("revision_id", revision_id)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"start_index": start, "end_index": end}
    return req


async def _announcement_meta(chat_id: str, user_key: str) -> dict[str, Any]:
    """``{revision_id, announcement_type, ...}`` for a chat's announcement.

    Read before every write: ``revision_id`` is an optimistic lock the caller cannot
    know, and ``announcement_type`` tells us up front whether the docx endpoints even
    apply (a legacy announcement would otherwise fail mid-write with 232097).
    """
    res = await _invoke(_build_announcement_get_request(chat_id), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _ANNOUNCEMENT_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    revision = data.get("revision_id")
    if not isinstance(revision, int):
        with contextlib.suppress(TypeError, ValueError):
            revision = int(str(revision))
    return {
        "ok": True,
        "revision_id": revision if isinstance(revision, int) else 0,
        "announcement_type": data.get("announcement_type", "") or "",
        "owner_id": data.get("owner_id", "") or "",
        "modifier_id": data.get("modifier_id", "") or "",
        "create_time": data.get("create_time_v2") or data.get("create_time") or "",
        "update_time": data.get("update_time_v2") or data.get("update_time") or "",
    }


async def read_chat_announcement_impl(chat_id: str, max_chars: int = 20000, user_key: str = "") -> dict[str, Any]:
    """Read a group's 群公告 as plain text plus its block structure.

    The announcement is a document, so this pages its blocks and joins their text the
    same way ``list_doc_blocks_impl`` does — the caller wants to know what the notice
    says, not to parse docx JSON. ``blocks`` is returned alongside so a follow-up edit
    can address a specific paragraph.

    An **empty** announcement is a legitimate answer (``text == ""``, ``block_count``
    counting only the root), not an error: a group that never had a notice set still
    has an announcement document.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    meta = await _announcement_meta(cid, user_key)
    if not meta["ok"]:
        return meta

    limit = max(1, min(int(max_chars or 20000), 100000))
    blocks: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _invoke(
            _build_announcement_blocks_request(cid, _BLOCKS_LIST_PAGE_MAX, page_token),
            user_key=user_key,
            prefer="tenant",
        )
        if not res["ok"]:
            return _with_hint(res, _ANNOUNCEMENT_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        for raw in data.get("items") or []:
            if not isinstance(raw, dict):
                continue
            block_type = raw.get("block_type") or 0
            blocks.append(
                {
                    "block_id": raw.get("block_id", ""),
                    "block_type": block_type,
                    "type_name": _BLOCK_TYPE_NAMES.get(block_type, str(block_type)),
                    "parent_id": raw.get("parent_id", ""),
                    "text": _block_plain_text(raw),
                }
            )
        page_token = str(data.get("page_token") or "")
        if not data.get("has_more") or not page_token:
            break

    # The root block (its id is the chat_id) is scaffolding, not content.
    body = [b for b in blocks if b["block_id"] != cid]
    text = "\n".join(b["text"] for b in body if b["text"])
    return {
        "ok": True,
        "chat_id": cid,
        "revision_id": meta["revision_id"],
        "announcement_type": meta["announcement_type"],
        "owner_id": meta["owner_id"],
        "modifier_id": meta["modifier_id"],
        "update_time": meta["update_time"],
        "text": text if len(text) <= limit else text[:limit] + "…",
        "truncated": len(text) > limit,
        "block_count": len(body),
        "blocks": body,
        "empty": not text.strip(),
    }


async def set_chat_announcement_impl(
    chat_id: str,
    content: str,
    replace: bool = True,
    user_key: str = "",
) -> dict[str, Any]:
    """Write a group's 群公告 from plain text / light Markdown headings.

    ``replace=True`` (the default) rewrites the notice: the existing body blocks are
    deleted first, then the new content is appended. That ordering is deliberate — the
    delete bumps ``revision_id``, so the append must re-read it rather than reuse the
    one it started with, or Feishu rejects the write on a stale lock.

    ``replace=False`` appends to whatever is already there, for adding a line to a
    standing notice without retyping it.

    Blank ``content`` with ``replace=True`` is refused rather than treated as "clear
    the announcement": wiping a group's notice is not something to do by accident. Use
    ``clear_chat_announcement_impl`` to say that explicitly.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    if not (content or "").strip():
        return _error(
            "content is empty — nothing to write. 要清空群公告请用 feishu_chat_announcement_clear (显式操作)。"
        )
    blocks = _content_to_blocks(content)
    if not blocks:
        return _error("content produced no blocks — nothing to write.")

    deleted = 0
    if replace:
        cleared = await clear_chat_announcement_impl(cid, user_key=user_key)
        if not cleared["ok"]:
            return cleared
        deleted = cleared["deleted"]

    # Re-read the revision: a delete above (or anyone else's edit) has moved it on.
    meta = await _announcement_meta(cid, user_key)
    if not meta["ok"]:
        return meta
    added = 0
    for start in range(0, len(blocks), _BLOCKS_BATCH):
        batch = blocks[start : start + _BLOCKS_BATCH]
        revision = meta["revision_id"]
        res = await _invoke(
            _build_announcement_children_request(cid, batch, revision, -1),
            user_key=user_key,
            prefer="tenant",
        )
        if not res["ok"]:
            return _with_hint({**res, "chat_id": cid, "added": added, "deleted": deleted}, _ANNOUNCEMENT_ERROR_HINTS)
        added += len(batch)
        # Each successful batch advances the document version; the next one must use it.
        data = res["data"] if isinstance(res["data"], dict) else {}
        next_revision = data.get("revision_id")
        meta = {**meta, "revision_id": next_revision if isinstance(next_revision, int) else revision + 1}
    return {
        "ok": True,
        "chat_id": cid,
        "added": added,
        "deleted": deleted,
        "replaced": replace,
        "revision_id": meta["revision_id"],
    }


async def clear_chat_announcement_impl(chat_id: str, user_key: str = "") -> dict[str, Any]:
    """Delete every body block of a group's 群公告, leaving it empty.

    Separate from ``set_chat_announcement_impl`` because emptying a group's notice is a
    destructive act with no undo, so it has to be asked for by name. Deleting nothing
    (an already-empty announcement) succeeds with ``deleted == 0`` rather than erroring.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    current = await read_chat_announcement_impl(cid, max_chars=1, user_key=user_key)
    if not current["ok"]:
        return current
    count = int(current.get("block_count") or 0)
    if count <= 0:
        return {"ok": True, "chat_id": cid, "deleted": 0, "revision_id": current.get("revision_id", 0)}
    res = await _invoke(
        _build_announcement_delete_request(cid, 0, count, int(current.get("revision_id") or 0)),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid}, _ANNOUNCEMENT_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    revision = data.get("revision_id")
    return {
        "ok": True,
        "chat_id": cid,
        "deleted": count,
        "revision_id": revision if isinstance(revision, int) else 0,
    }


# ── 群设置变更 / 解散群 / 转让群主 (chat update, delete, ownership) ───────────────
#
# All three ride the same two endpoints — PUT /open-apis/im/v1/chats/:chat_id for every
# setting (including ``owner_id``, which is how ownership transfer works) and DELETE on
# the same path to dismiss the group. They are separate tools because the *consequences*
# differ by an order of magnitude, and because Feishu's raw body is easy to get wrong in
# two specific ways that silently produce the opposite of what was asked:
#
# 1. **``add_member_permission`` and ``share_card_permission`` are coupled.** Feishu
#    rejects ``only_owner`` + ``allowed``. Sending one alone is accepted but leaves the
#    pair inconsistent, so the pair is completed here from whichever half was given.
# 2. **禁言 is not a field on this endpoint.** "全员禁言" lives on a *different* endpoint
#    (PUT .../moderation, ``moderation_setting``); an agent reaching for
#    ``moderation_permission`` on the update body gets a silently ignored field. Hence
#    ``update_chat_moderation_impl`` below, and the guard in ``update_chat_impl``.
#
# The human-facing vocabulary is deliberately Chinese-first: the agent is told "把群改名"
# / "开全员禁言", and mapping that onto Feishu's enum strings is exactly the knowledge
# that has to be *guaranteed* rather than remembered.
_CHAT_UPDATE_ERROR_HINTS = {
    232002: "该群限定「仅群主和管理员可编辑群信息」; 传群主/管理员的 user_key 以本人身份改。",
    232012: "指定的新群主还不是群成员; 先用 feishu_chat_add_members 把他加进群再转让。",
    232016: "普通成员只能改群头像/群名称/群描述/国际化名称; 其它设置要群主或管理员。",
    232020: "群名称不合法 (公开群至少 2 个字符)。",
    232021: "群头像 image_key 无效; 必须用 image_type='avatar' 上传 (feishu_chat_upload_avatar)。",
}
# Who-can-do-what enums, keyed by the words a user actually says.
_CHAT_WHO_VALUES = {
    "all_members": "all_members",
    "only_owner": "only_owner",
    "not_anyone": "not_anyone",
    "所有群成员": "all_members",
    "所有人": "all_members",
    "仅群主和管理员": "only_owner",
    "仅群主": "only_owner",
    "群主和管理员": "only_owner",
    "任何人都不可": "not_anyone",
    "禁止": "not_anyone",
}
# The update body's own fields, split by the value vocabulary each one takes, so an
# unknown value is refused with the accepted list instead of being sent off to Feishu.
_CHAT_WHO_FIELDS = (
    "add_member_permission",
    "at_all_permission",
    "edit_permission",
    "join_message_visibility",
    "leave_message_visibility",
    "urgent_setting",
    "video_conference_setting",
    "hide_member_count_setting",
)
_CHAT_APPROVAL_VALUES = {
    "approval_required": "approval_required",
    "no_approval_required": "no_approval_required",
    "需审批": "approval_required",
    "需要审批": "approval_required",
    "开": "approval_required",
    "无需审批": "no_approval_required",
    "不需要审批": "no_approval_required",
    "关": "no_approval_required",
}
_CHAT_TYPE_VALUES = {"private": "private", "public": "public", "私有": "private", "公开": "public"}


def _normalize_chat_who(field: str, value: str) -> tuple[str, str]:
    """Map a who-can-do-this value onto Feishu's enum; returns (enum, error)."""
    mapped = _CHAT_WHO_VALUES.get(value.strip())
    if not mapped:
        return "", (
            f"{field} 的取值 {value!r} 无效; 只能是 all_members (所有群成员) / only_owner (仅群主和管理员)"
            " / not_anyone (任何人都不可)。"
        )
    return mapped, ""


def _build_update_chat_request(chat_id: str, body: dict[str, Any], user_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/im/v1/chats/:chat_id"
    req.paths["chat_id"] = chat_id
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = body
    return req


def _chat_update_body(
    name: str,
    description: str,
    avatar: str,
    add_member_permission: str,
    at_all_permission: str,
    edit_permission: str,
    membership_approval: str,
    chat_type: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The PUT body for a settings change; returns (body, error).

    Only fields the caller actually named are included — Feishu treats an omitted field
    as "leave it alone", so building the body from non-empty arguments is what keeps a
    rename from also resetting permissions.
    """
    body: dict[str, Any] = {}
    if name.strip():
        body["name"] = name.strip()
    if description.strip():
        body["description"] = description.strip()
    if avatar.strip():
        body["avatar"] = avatar.strip()
    for field, raw in (
        ("add_member_permission", add_member_permission),
        ("at_all_permission", at_all_permission),
        ("edit_permission", edit_permission),
    ):
        if not raw.strip():
            continue
        mapped, err = _normalize_chat_who(field, raw)
        if err:
            return {}, _error(err)
        body[field] = mapped
    if membership_approval.strip():
        mapped_approval = _CHAT_APPROVAL_VALUES.get(membership_approval.strip())
        if not mapped_approval:
            return {}, _error(
                f"membership_approval 的取值 {membership_approval!r} 无效; "
                "只能是 approval_required (入群需审批) 或 no_approval_required (无需审批)。"
            )
        body["membership_approval"] = mapped_approval
    if chat_type.strip():
        mapped_type = _CHAT_TYPE_VALUES.get(chat_type.strip())
        if not mapped_type:
            return {}, _error(f"chat_type 的取值 {chat_type!r} 无效; 只能是 private (私有群) 或 public (公开群)。")
        body["chat_type"] = mapped_type
    # Feishu refuses only_owner + allowed, and accepting one half alone leaves the pair
    # contradictory — so the partner field is derived rather than left to the caller.
    if "add_member_permission" in body:
        body["share_card_permission"] = "allowed" if body["add_member_permission"] == "all_members" else "not_allowed"
    return body, None


async def update_chat_impl(
    chat_id: str,
    name: str = "",
    description: str = "",
    avatar: str = "",
    add_member_permission: str = "",
    at_all_permission: str = "",
    edit_permission: str = "",
    membership_approval: str = "",
    chat_type: str = "",
    user_key: str = "",
) -> dict[str, Any]:
    """Change a group's name / avatar / description / permissions (群设置变更).

    Every argument is optional and only the named ones are sent, so renaming a group
    cannot accidentally reset who may add members. ``share_card_permission`` is derived
    from ``add_member_permission`` because Feishu requires the pair to agree.

    Not here on purpose: **全员禁言** (use ``update_chat_moderation_impl`` — a different
    endpoint) and **转让群主** (use ``transfer_chat_owner_impl`` — same endpoint, but the
    consequence warrants its own tool).
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    body, bad = _chat_update_body(
        name,
        description,
        avatar,
        add_member_permission,
        at_all_permission,
        edit_permission,
        membership_approval,
        chat_type,
    )
    if bad is not None:
        return bad
    if not body:
        return _error(
            "没有要改的东西 — 至少给一个字段 (name / description / avatar / add_member_permission / "
            "at_all_permission / edit_permission / membership_approval / chat_type)。"
            "全员禁言用 feishu_chat_mute, 转让群主用 feishu_chat_transfer_owner。"
        )
    res = await _invoke(_build_update_chat_request(cid, body, "open_id"), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid}, {**_CHAT_ADMIN_ERROR_HINTS, **_CHAT_UPDATE_ERROR_HINTS})
    return {"ok": True, "chat_id": cid, "updated": body}


async def transfer_chat_owner_impl(
    chat_id: str,
    new_owner_id: str,
    user_id_type: str = "open_id",
    user_key: str = "",
) -> dict[str, Any]:
    """Hand a group over to a new owner (转让群主).

    Split out of ``update_chat_impl`` because it is the one settings change the *caller
    loses control by*: after this the previous owner is an ordinary member (or admin),
    so a tool that could do it as a side effect of a rename would be dangerous.

    The new owner **must already be in the group** — Feishu answers 232012 otherwise,
    which is translated to say "add them first" rather than left as a code. Only the
    current owner can do this (232017), so ``user_key`` normally has to be theirs.
    """
    cid = chat_id.strip()
    owner = new_owner_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    if not owner:
        return _error(
            "new_owner_id is required — 新群主的 id (默认 open_id, ou_ 开头); "
            "用 feishu_chat_list_members / feishu_contact_search 解析姓名。"
        )
    if user_id_type not in ("open_id", "union_id", "user_id"):
        return _error(f"user_id_type must be open_id, union_id, or user_id, got {user_id_type!r}.")
    res = await _invoke(
        _build_update_chat_request(cid, {"owner_id": owner}, user_id_type),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(
            {**res, "chat_id": cid, "new_owner_id": owner},
            {**_CHAT_ADMIN_ERROR_HINTS, **_CHAT_UPDATE_ERROR_HINTS},
        )
    return {"ok": True, "chat_id": cid, "new_owner_id": owner, "user_id_type": user_id_type}


def _build_delete_chat_request(chat_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/im/v1/chats/:chat_id"
    req.paths["chat_id"] = chat_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


_DISMISS_CONFIRM = "解散群"


async def dismiss_chat_impl(chat_id: str, confirm: str = "", user_key: str = "") -> dict[str, Any]:
    """Dismiss (解散) a group — irreversible, and its history is not kept.

    This is the most destructive call in the Feishu tool set: Feishu does not preserve
    the chat record, so nothing here or elsewhere can undo it. It therefore requires an
    explicit ``confirm="解散群"``, which exists so that a mis-parsed instruction ("清一下
    群") cannot dissolve a group — the agent has to have understood the request well
    enough to name the act.

    Only the owner (or the creating bot with ``im:chat:operate_as_owner``) may do this;
    232017 says so, and 232009 means somebody already dissolved it.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    if confirm.strip() != _DISMISS_CONFIRM:
        return _error(
            f"解散群是不可逆的 (飞书不保留群记录, 消息/文件全部无法找回)。确认要解散请传 "
            f"confirm='{_DISMISS_CONFIRM}'。先用 feishu_chat_get 核对这是不是要解散的那个群。",
            need_confirmation=True,
            chat_id=cid,
        )
    res = await _invoke(_build_delete_chat_request(cid), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid}, _CHAT_ADMIN_ERROR_HINTS)
    return {"ok": True, "chat_id": cid, "dismissed": True}


# ── 全员禁言 (chat moderation) ───────────────────────────────────────────────────
# A separate endpoint from every other group setting, which is the trap: the field named
# ``moderation_permission`` that ``feishu_chat_get`` *reads* cannot be written through the
# chat-update body. Writing it needs PUT .../moderation with ``moderation_setting``, plus
# — for the "只让某几个人能说话" case — the added/removed lists, which Feishu requires to
# be disjoint.
_MODERATION_VALUES = {
    "all_members": "all_members",
    "only_owner": "only_owner",
    "moderator_list": "moderator_list",
    "所有群成员": "all_members",
    "所有人可发言": "all_members",
    "解除禁言": "all_members",
    "取消禁言": "all_members",
    "全员禁言": "only_owner",
    "仅群主和管理员": "only_owner",
    "仅群主": "only_owner",
    "指定人员": "moderator_list",
    "指定成员可发言": "moderator_list",
}
_MODERATION_ERROR_HINTS = {
    232060: "该群已被封禁, 无法修改发言权限。",
    232092: "群里正在开会, 此时改不了发言权限; 会议结束后重试。",
}


def _build_chat_moderation_request(
    chat_id: str, setting: str, added: list[str], removed: list[str], user_id_type: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PUT
    req.uri = "/open-apis/im/v1/chats/:chat_id/moderation"
    req.paths["chat_id"] = chat_id
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    body: dict[str, Any] = {"moderation_setting": setting}
    if added:
        body["moderator_added_list"] = added
    if removed:
        body["moderator_removed_list"] = removed
    req.body = body
    return req


async def update_chat_moderation_impl(
    chat_id: str,
    setting: str,
    speaker_ids: list[str] | None = None,
    revoke_ids: list[str] | None = None,
    user_id_type: str = "open_id",
    user_key: str = "",
) -> dict[str, Any]:
    """Set who may speak in a group — 全员禁言 / 解除禁言 / 指定人员可发言.

    ``setting`` accepts Feishu's enums or the words a user says: ``"全员禁言"`` →
    ``only_owner`` (only owner+admins can post), ``"解除禁言"`` → ``all_members``,
    ``"指定人员"`` → ``moderator_list`` with ``speaker_ids`` naming who keeps the right.

    The two lists must be disjoint (Feishu rejects an id in both), and ids that are not
    in the group are dropped silently on Feishu's side — so ``requested`` is echoed back
    for comparison. Only the owner or the creating bot may call this (232017).
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    mapped = _MODERATION_VALUES.get((setting or "").strip())
    if not mapped:
        return _error(
            f"setting 的取值 {setting!r} 无效; 只能是 all_members (所有人可发言/解除禁言) / "
            "only_owner (全员禁言, 仅群主和管理员可发言) / moderator_list (仅指定人员可发言)。"
        )
    added = [i.strip() for i in (speaker_ids or []) if i and i.strip()]
    removed = [i.strip() for i in (revoke_ids or []) if i and i.strip()]
    both = sorted(set(added) & set(removed))
    if both:
        return _error(f"同一个 id 不能同时出现在 speaker_ids 和 revoke_ids 里: {', '.join(both)}。")
    if mapped == "moderator_list" and not added and not removed:
        return _error("setting='moderator_list' 时要用 speaker_ids 指明谁可以发言 (否则等于谁都不能说)。")
    if user_id_type not in ("open_id", "union_id", "user_id"):
        return _error(f"user_id_type must be open_id, union_id, or user_id, got {user_id_type!r}.")
    res = await _invoke(
        _build_chat_moderation_request(cid, mapped, added, removed, user_id_type),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid}, {**_CHAT_ADMIN_ERROR_HINTS, **_MODERATION_ERROR_HINTS})
    return {
        "ok": True,
        "chat_id": cid,
        "moderation_setting": mapped,
        "speakers_added": added,
        "speakers_revoked": removed,
        "user_id_type": user_id_type,
    }


# ── 群菜单 (chat menu) — the buttons along the bottom of a group ─────────────────
#
# Two shapes of the same thing, and the API makes them awkward in a way worth absorbing:
# a first-level menu either *does* something (``action_type="REDIRECT_LINK"`` + a URL) or
# *contains* children (``action_type="NONE"``, no icon allowed). Feishu enforces that,
# but only after the request lands, so the combination is checked here.
#
# Create **appends** — it never replaces — and caps at 3 first-level menus with 5
# children each. Children cannot be added to a first-level menu that already exists, so
# a menu with sub-items has to be created in one call: the whole tree is built from a
# compact ``[{name, url?, children?}]`` list rather than Feishu's nested wrapper objects,
# which are three levels of single-key dicts an agent gets wrong more often than not.
_MENU_ERROR_HINTS = {
    232011: "机器人不在该群里, 先把机器人加入群。",
    232025: "应用未启用机器人能力, 到开发者后台开启后再试。",
    232055: "机器人没有管理群菜单的权限 (该群限定群主/管理员才能改)。",
    232056: "菜单图标 image_key 不是本机器人上传的; 用 feishu_message_upload_image 重新上传。",
    232090: "群类型不支持群菜单 (仅普通群 group)。",
}
_MENU_MAX_TOP = 3
_MENU_MAX_CHILDREN = 5
_MENU_NAME_MAX = 120


def _menu_item(name: str, url: str, image_key: str) -> tuple[dict[str, Any], str]:
    """One ``chat_menu_item``; returns (item, error).

    A menu with no URL is a container (``action_type="NONE"``); with one it redirects.
    ``common_url`` alone covers every platform, which is what a caller means by "点开
    打开这个链接" — the per-platform overrides exist for apps that need them and are not
    worth the schema here.
    """
    label = name.strip()
    if not label:
        return {}, "菜单名称不能为空。"
    if len(label) > _MENU_NAME_MAX:
        return {}, f"菜单名称 {label!r} 超过 {_MENU_NAME_MAX} 字。"
    item: dict[str, Any] = {"name": label}
    link = url.strip()
    if link:
        if not link.startswith(("http://", "https://")):
            return {}, f"菜单 {label!r} 的 url 必须以 http:// 或 https:// 开头, 收到 {link!r}。"
        item["action_type"] = "REDIRECT_LINK"
        item["redirect_link"] = {"common_url": link}
    else:
        item["action_type"] = "NONE"
    if image_key.strip():
        item["image_key"] = image_key.strip()
    return item, ""


def _menu_tree_body(menus: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Feishu's nested ``menu_tree`` from a flat ``[{name, url, image_key, children}]``."""
    if not menus:
        return {}, _error("menus is required — 至少给一个菜单, 形如 [{'name': '帮助', 'url': 'https://...'}]。")
    if len(menus) > _MENU_MAX_TOP:
        return {}, _error(f"一个群最多 {_MENU_MAX_TOP} 个一级菜单, 收到 {len(menus)} 个。")
    top_levels: list[dict[str, Any]] = []
    for position, raw in enumerate(menus):
        if not isinstance(raw, dict):
            return {}, _error(f"menus[{position}] 不是对象; 形如 {{'name': '帮助', 'url': 'https://...'}}。")
        children_raw = raw.get("children") or []
        if not isinstance(children_raw, list):
            return {}, _error(f"menus[{position}].children 必须是列表。")
        if len(children_raw) > _MENU_MAX_CHILDREN:
            return {}, _error(f"menus[{position}] 最多 {_MENU_MAX_CHILDREN} 个二级菜单, 收到 {len(children_raw)} 个。")
        # A parent with children may not redirect or carry an icon — Feishu's rule.
        if children_raw and (str(raw.get("url", "")).strip() or str(raw.get("image_key", "")).strip()):
            return {}, _error(
                f"menus[{position}] 带了 children, 这种一级菜单只能是分组: 不能再给 url 或 image_key "
                "(点它只会展开子菜单)。"
            )
        item, err = _menu_item(str(raw.get("name", "")), str(raw.get("url", "")), str(raw.get("image_key", "")))
        if err:
            return {}, _error(f"menus[{position}]: {err}")
        children: list[dict[str, Any]] = []
        for child_position, child_raw in enumerate(children_raw):
            if not isinstance(child_raw, dict):
                return {}, _error(f"menus[{position}].children[{child_position}] 不是对象。")
            child_item, child_err = _menu_item(
                str(child_raw.get("name", "")), str(child_raw.get("url", "")), str(child_raw.get("image_key", ""))
            )
            if child_err:
                return {}, _error(f"menus[{position}].children[{child_position}]: {child_err}")
            children.append({"chat_menu_item": child_item})
        entry: dict[str, Any] = {"chat_menu_item": item}
        if children:
            entry["children"] = children
        top_levels.append(entry)
    return {"menu_tree": {"chat_menu_top_levels": top_levels}}, None


def _build_chat_menu_request(chat_id: str, method: HttpMethod, suffix: str, body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = method
    req.uri = f"/open-apis/im/v1/chats/:chat_id/menu_tree{suffix}"
    req.paths["chat_id"] = chat_id
    req.token_types = {AccessTokenType.TENANT}
    if body:
        req.body = body
    return req


def _menu_summary(data: Any) -> list[dict[str, Any]]:
    """Flatten Feishu's ``menu_tree`` reply into ``[{id, name, url, children}]``.

    The ids matter: deleting or reordering menus keys off ``chat_menu_top_level_id``,
    and it is only ever returned here.
    """
    tree = data.get("menu_tree") if isinstance(data, dict) else None
    top_levels = tree.get("chat_menu_top_levels") if isinstance(tree, dict) else None
    out: list[dict[str, Any]] = []
    for raw in top_levels or []:
        if not isinstance(raw, dict):
            continue
        item = raw.get("chat_menu_item") if isinstance(raw.get("chat_menu_item"), dict) else {}
        link = item.get("redirect_link") if isinstance(item.get("redirect_link"), dict) else {}
        children: list[dict[str, Any]] = []
        for child in raw.get("children") or []:
            if not isinstance(child, dict):
                continue
            child_item = child.get("chat_menu_item") if isinstance(child.get("chat_menu_item"), dict) else {}
            child_link = child_item.get("redirect_link") if isinstance(child_item.get("redirect_link"), dict) else {}
            children.append(
                {
                    "id": child.get("chat_menu_second_level_id", ""),
                    "name": child_item.get("name", ""),
                    "url": child_link.get("common_url", "") if isinstance(child_link, dict) else "",
                }
            )
        entry: dict[str, Any] = {
            "id": raw.get("chat_menu_top_level_id", ""),
            "name": item.get("name", ""),
            "url": link.get("common_url", "") if isinstance(link, dict) else "",
        }
        if children:
            entry["children"] = children
        out.append(entry)
    return out


async def get_chat_menu_impl(chat_id: str, user_key: str = "") -> dict[str, Any]:
    """Read a group's 群菜单 as ``[{id, name, url, children}]``.

    The prerequisite for changing one: ``chat_menu_top_level_id`` is needed to delete or
    reorder a menu, and creating appends rather than replaces — so knowing what is there
    is how you avoid ending up with two 「帮助」 buttons.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    res = await _invoke(_build_chat_menu_request(cid, HttpMethod.GET, "", {}), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid}, {**_CHAT_ADMIN_ERROR_HINTS, **_MENU_ERROR_HINTS})
    menus = _menu_summary(res["data"])
    return {"ok": True, "chat_id": cid, "menus": menus, "count": len(menus)}


async def add_chat_menu_impl(chat_id: str, menus: list[dict[str, Any]] | None = None, user_key: str = "") -> dict:
    """Append first-level menus (each optionally with sub-items) to a group's 群菜单.

    ``menus`` is a flat list — ``[{"name": "帮助", "url": "https://…"}]``, or with
    ``"children": [{"name": …, "url": …}]`` for a dropdown. A menu with children is a
    *group heading*: it may not itself have a url or icon (Feishu's rule), and children
    cannot be added to a first-level menu later, so the whole dropdown goes in one call.

    This **appends**: existing menus survive. Read ``get_chat_menu_impl`` first if the
    intent was to replace them, then delete the old ones.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    body, bad = _menu_tree_body(menus or [])
    if bad is not None:
        return bad
    res = await _invoke(_build_chat_menu_request(cid, HttpMethod.POST, "", body), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid}, {**_CHAT_ADMIN_ERROR_HINTS, **_MENU_ERROR_HINTS})
    created = _menu_summary(res["data"])
    return {"ok": True, "chat_id": cid, "menus": created, "count": len(created)}


async def delete_chat_menu_impl(chat_id: str, menu_ids: list[str] | None = None, user_key: str = "") -> dict[str, Any]:
    """Remove first-level menus (and their sub-items) from a group's 群菜单.

    Takes ``chat_menu_top_level_id`` values from ``get_chat_menu_impl`` — ids, not names,
    because two menus may share a name and deleting the wrong button is visible to
    everyone in the group.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    ids = [str(i).strip() for i in (menu_ids or []) if str(i).strip()]
    if not ids:
        return _error("menu_ids is required — 一级菜单 id 列表, 用 feishu_chat_menu_get 取 (不是菜单名)。")
    body = {"chat_menu_top_level_ids": ids}
    res = await _invoke(_build_chat_menu_request(cid, HttpMethod.DELETE, "", body), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid, "requested": ids}, {**_CHAT_ADMIN_ERROR_HINTS, **_MENU_ERROR_HINTS})
    remaining = _menu_summary(res["data"])
    return {"ok": True, "chat_id": cid, "deleted": ids, "menus": remaining, "count": len(remaining)}


# ── 群标签页 (chat tabs) — the pinned tabs across the top of a group ─────────────
#
# Feishu lists eleven ``tab_type`` values but only two can be *created*: ``doc`` and
# ``url``. The rest (pin / 会议纪要 / 任务 / 图片视频 …) are built-in tabs the API can only
# read. Trying to create one fails with an unhelpful parameter error, so unsupported
# types are refused here by name, with the two that work spelled out.
_TAB_ERROR_HINTS = {
    232046: "群标签页数量已达上限 (每个会话最多 20 个自定义标签页)。",
    232047: "标签页名称过长 (最多 60 字)。",
    232048: "tab_content 不合法; doc 类型要文档链接, url 类型要 http(s) 链接。",
    232050: "该会话类型不支持群标签页 (仅群组 group 和单聊 p2p)。",
    232051: "缺少该文档的权限; 先把文档共享给机器人 (或传本人 user_key)。",
    232055: "机器人没有管理群标签页的权限 (该群限定群主/管理员才能改)。",
}
_TAB_CREATABLE_TYPES = ("doc", "url")
_TAB_NAME_MAX = 60


def _build_chat_tabs_request(chat_id: str, method: HttpMethod, suffix: str, body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = method
    req.uri = f"/open-apis/im/v1/chats/:chat_id/chat_tabs{suffix}"
    req.paths["chat_id"] = chat_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    if body:
        req.body = body
    return req


def _tab_summary(data: Any) -> list[dict[str, Any]]:
    """Feishu's ``chat_tabs`` reply as ``[{tab_id, name, type, content}]``."""
    tabs = data.get("chat_tabs") if isinstance(data, dict) else None
    out: list[dict[str, Any]] = []
    for raw in tabs or []:
        if not isinstance(raw, dict):
            continue
        content = raw.get("tab_content") if isinstance(raw.get("tab_content"), dict) else {}
        out.append(
            {
                "tab_id": raw.get("tab_id", ""),
                "name": raw.get("tab_name", ""),
                "type": raw.get("tab_type", ""),
                "content": content.get("url") or content.get("doc") or "",
            }
        )
    return out


async def list_chat_tabs_impl(chat_id: str, user_key: str = "") -> dict[str, Any]:
    """List a group's 群标签页 as ``[{tab_id, name, type, content}]``.

    Includes the built-in tabs (pin / 会议纪要 / 任务 …) that cannot be created or removed
    through the API, so a ``tab_id`` from here is not necessarily deletable — only the
    ``doc`` and ``url`` ones this tool created are.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    res = await _invoke(
        _build_chat_tabs_request(cid, HttpMethod.GET, "/list_tabs", {}), user_key=user_key, prefer="tenant"
    )
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid}, {**_CHAT_ADMIN_ERROR_HINTS, **_TAB_ERROR_HINTS})
    tabs = _tab_summary(res["data"])
    return {"ok": True, "chat_id": cid, "tabs": tabs, "count": len(tabs)}


async def add_chat_tab_impl(
    chat_id: str,
    tab_name: str,
    tab_type: str = "url",
    content: str = "",
    user_key: str = "",
) -> dict[str, Any]:
    """Pin a document or a web page as a 群标签页 at the top of a group.

    Only ``doc`` (a Feishu doc/sheet/bitable link) and ``url`` (any web page) can be
    created — the other tab types Feishu documents are built-in and read-only, so asking
    for one is refused up front rather than failing as a parameter error.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    name = tab_name.strip()
    if not name:
        return _error("tab_name is required — 标签页显示的名字。")
    if len(name) > _TAB_NAME_MAX:
        return _error(f"tab_name 最多 {_TAB_NAME_MAX} 字, 收到 {len(name)} 字。")
    kind = (tab_type or "").strip().lower()
    if kind not in _TAB_CREATABLE_TYPES:
        return _error(
            f"tab_type 只能是 {' 或 '.join(_TAB_CREATABLE_TYPES)}, 收到 {tab_type!r}。"
            "其它标签页类型 (pin / 会议纪要 / 任务 / 图片视频 等) 是飞书内置的, API 只能读不能建。"
        )
    link = content.strip()
    if not link:
        return _error("content is required — doc 类型给飞书文档链接, url 类型给网页链接。")
    if not link.startswith(("http://", "https://")):
        return _error(f"content 必须以 http:// 或 https:// 开头, 收到 {link!r}。")
    body = {"chat_tabs": [{"tab_name": name, "tab_type": kind, "tab_content": {kind: link}}]}
    res = await _invoke(_build_chat_tabs_request(cid, HttpMethod.POST, "", body), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid}, {**_CHAT_ADMIN_ERROR_HINTS, **_TAB_ERROR_HINTS})
    tabs = _tab_summary(res["data"])
    return {"ok": True, "chat_id": cid, "tabs": tabs, "count": len(tabs)}


async def delete_chat_tabs_impl(chat_id: str, tab_ids: list[str] | None = None, user_key: str = "") -> dict[str, Any]:
    """Remove 群标签页 by ``tab_id`` (from ``list_chat_tabs_impl``).

    Built-in tabs cannot be removed this way; Feishu refuses them, which is reported as
    the error rather than silently counted as removed.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (oc_...); resolve a group name with feishu_chat_find first.")
    ids = [str(i).strip() for i in (tab_ids or []) if str(i).strip()]
    if not ids:
        return _error("tab_ids is required — 标签页 id 列表, 用 feishu_chat_tabs 取 (不是标签页名字)。")
    body = {"tab_ids": ids}
    res = await _invoke(
        _build_chat_tabs_request(cid, HttpMethod.DELETE, "/delete_tabs", body), user_key=user_key, prefer="tenant"
    )
    if not res["ok"]:
        return _with_hint({**res, "chat_id": cid, "requested": ids}, {**_CHAT_ADMIN_ERROR_HINTS, **_TAB_ERROR_HINTS})
    tabs = _tab_summary(res["data"])
    return {"ok": True, "chat_id": cid, "deleted": ids, "tabs": tabs, "count": len(tabs)}


async def upload_chat_avatar_impl(image_path: str, user_key: str = "") -> dict[str, Any]:
    """Upload a picture as a **group avatar** and return its ``image_key``.

    Separate from ``upload_image_impl`` for one reason that costs a debugging session to
    find: ``im/v1/images`` takes an ``image_type``, and a group avatar must be uploaded
    as ``"avatar"``. A ``message``-type key is accepted by the upload and then rejected
    by the chat-update call (232021), which reads as "bad avatar" rather than "wrong
    upload type".
    """
    data, name, bad = await _read_upload_bytes(image_path, _IMAGE_UPLOAD_MAX_BYTES, "avatar image")
    if bad is not None:
        return bad
    suffix = pathlib.Path(name).suffix.lower()
    if suffix and suffix not in _IMAGE_SUFFIXES:
        return _error(f"{name} is not an image Feishu accepts ({', '.join(sorted(_IMAGE_SUFFIXES))}).")
    res = await _invoke(
        lambda: _build_image_upload_request("avatar", name, data),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _UPLOAD_ERROR_HINTS)
    rdata = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "image_key": rdata.get("image_key", ""), "file_name": name, "size": len(data)}


# ── 会话列表 (chat list) — every group the caller is in ──────────────────────────
#
# The complement to ``find_chat_impl``: search answers "which group is called 产品评审",
# this answers "what groups are there at all" — needed when the user says 「我在哪些群」or
# when a sweep has to cover every group without a name to search by.
#
# Two things separate it from a bare feishu_api call. ``sort_type="ByActiveTimeDesc"`` is
# the one a person means by 「最近活跃的群」, but Feishu warns that paging through it can
# *skip groups*, since activity order shifts underfoot; so paging is only done under the
# stable creation-time order, and the active-time ordering is applied locally to a single
# page. And ``prefer`` decides *whose* list this is: the bot's groups (tenant) or the
# caller's own (user token) — the same endpoint, two entirely different answers, which is
# the mistake worth making impossible.
_CHAT_STATUS_LABELS = {"normal": "正常", "dissolved": "已解散", "dissolved_save": "已解散(保留记录)"}
_CHAT_LIST_PAGE_MAX = 100


def _build_chat_list_request(user_id_type: str, sort_type: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/chats"
    req.add_query("user_id_type", user_id_type)
    req.add_query("sort_type", sort_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def list_chats_impl(
    whose: str = "bot",
    limit: int = 100,
    user_key: str = "",
) -> dict[str, Any]:
    """List the groups the bot (``whose="bot"``) or the caller (``whose="me"``) is in.

    ``whose`` is the argument that matters: the endpoint is the same either way, but the
    *token* decides whose membership is listed, and "我在哪些群" answered with the bot's
    groups is a wrong answer that looks right. ``whose="me"`` needs the caller to have
    authorized (``user_key`` is then required).

    Pages through in creation order up to ``limit`` — deliberately not in activity order,
    because Feishu documents that paging an activity-ordered list can skip groups as the
    order shifts. Single-chat (p2p) conversations are never included; Feishu's chat list
    is groups only.
    """
    kind = (whose or "bot").strip().lower()
    if kind not in ("bot", "me"):
        return _error("whose must be 'bot' (机器人所在的群) or 'me' (调用者本人所在的群).")
    if kind == "me" and not user_key.strip():
        return _error("whose='me' 要知道你是谁 — 传 <feishu_context> 里的 sender_open_id 作 user_key。")
    cap = max(1, min(int(limit or 100), 1000))
    prefer = "user" if kind == "me" else "tenant"

    chats: list[dict[str, Any]] = []
    page_token = ""
    truncated = False
    while True:
        page_size = min(_CHAT_LIST_PAGE_MAX, cap - len(chats))
        res = await _invoke(
            _build_chat_list_request("open_id", "ByCreateTimeAsc", page_size, page_token),
            user_key=user_key,
            # identity is irrelevant to a read, but prefer="user" would otherwise ask.
            identity="user" if kind == "me" else "",
            prefer=prefer,
        )
        if not res["ok"]:
            return _with_hint(res, _CHAT_ADMIN_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        for raw in data.get("items") or []:
            if not isinstance(raw, dict):
                continue
            status = raw.get("chat_status", "") or ""
            chats.append(
                {
                    "chat_id": raw.get("chat_id", ""),
                    "name": raw.get("name", ""),
                    "description": raw.get("description", ""),
                    "owner_id": raw.get("owner_id", "") or "",
                    # No owner_id on a bot-owned group — say which case this is rather
                    # than leaving a blank the caller reads as "没有群主".
                    "owner_is_bot": not raw.get("owner_id"),
                    "external": bool(raw.get("external")),
                    "chat_status": status,
                    "status_label": _CHAT_STATUS_LABELS.get(status, status),
                }
            )
        page_token = str(data.get("page_token") or "")
        if not data.get("has_more") or not page_token or len(chats) >= cap:
            truncated = bool(data.get("has_more") and page_token and len(chats) >= cap)
            break
    return {
        "ok": True,
        "whose": kind,
        "chats": chats,
        "count": len(chats),
        "truncated": truncated,
        "active": len([c for c in chats if c["chat_status"] == "normal"]),
    }


# ── 消息搜索 (message search) — find messages by keyword across chats ────────────
#
# Feishu's only keyword search over message *content*, and it is user-token-only: it
# searches what **that person** can see, so there is no bot-wide variant to fall back on
# (the tenant token is refused outright). Same auth path as docs search / global user
# search: the caller must have authorized once.
#
# The response is the sharp edge. Feishu returns **message_ids only** — no text, no
# sender, no chat. A search result the agent can't read is useless, so each hit is
# hydrated through ``im/v1/messages/:message_id`` and the text extracted with the same
# ``_message_plain_text`` the history tools use. Hydration is capped and failures are
# kept as bare ids rather than dropped, so a partial result stays honest about what it
# could not read (a message in a chat the *bot* is not in, typically).
_MESSAGE_SEARCH_HINTS = {
    99991663: "缺少用户授权; 消息搜索只能以本人身份进行 (tenant token 不被接受)。",
    99991400: "搜索参数不合法; 检查 start_time/end_time 是否为秒级时间戳。",
}
_MESSAGE_SEARCH_HYDRATE_MAX = 50
_MESSAGE_SEARCH_TYPES = ("file", "image", "media")
_MESSAGE_SEARCH_FROM_TYPES = ("bot", "user")
_MESSAGE_SEARCH_CHAT_TYPES = ("group_chat", "p2p_chat")


def _build_message_search_request(body: dict[str, Any], page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/search/v2/message"
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    # User token only: this searches what the authorizing person can see.
    req.token_types = {AccessTokenType.USER}
    req.body = body
    return req


def _message_search_body(
    query: str,
    chat_ids: list[str],
    from_ids: list[str],
    message_type: str,
    from_type: str,
    chat_type: str,
    start_time: str,
    end_time: str,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The search body; returns (body, error). Only named filters are included."""
    body: dict[str, Any] = {"query": query}
    if chat_ids:
        body["chat_ids"] = chat_ids
    if from_ids:
        body["from_ids"] = from_ids
    if message_type.strip():
        kind = message_type.strip().lower()
        if kind not in _MESSAGE_SEARCH_TYPES:
            return {}, _error(
                f"message_type 只能是 {', '.join(_MESSAGE_SEARCH_TYPES)} (按附件类型筛), 收到 {message_type!r}。"
                "搜纯文本消息不要传这个参数。"
            )
        body["message_type"] = kind
    if from_type.strip():
        sender = from_type.strip().lower()
        if sender not in _MESSAGE_SEARCH_FROM_TYPES:
            return {}, _error(f"from_type 只能是 {' 或 '.join(_MESSAGE_SEARCH_FROM_TYPES)}, 收到 {from_type!r}。")
        body["from_type"] = sender
    if chat_type.strip():
        where = chat_type.strip().lower()
        if where not in _MESSAGE_SEARCH_CHAT_TYPES:
            return {}, _error(f"chat_type 只能是 group_chat (群聊) 或 p2p_chat (单聊), 收到 {chat_type!r}。")
        body["chat_type"] = where
    # Feishu wants second-level timestamps as strings here (not the ms other endpoints
    # take), and a wrong unit silently matches nothing instead of erroring.
    for field, raw in (("start_time", start_time), ("end_time", end_time)):
        if not raw.strip():
            continue
        digits = raw.strip()
        if not digits.isdigit():
            return {}, _error(f"{field} 必须是秒级 Unix 时间戳 (如 '1609296809'), 收到 {raw!r}。")
        if len(digits) >= 13:
            return {}, _error(
                f"{field}={raw!r} 看起来是毫秒时间戳; 这个接口要**秒级** (10 位), 传毫秒会搜不到任何东西。"
            )
        body[field] = digits
    return body, None


async def _hydrate_message(message_id: str, user_key: str) -> dict[str, Any]:
    """One search hit turned into ``{message_id, chat_id, sender, text, create_time}``.

    Failure is reported per-hit (``readable: False``) instead of aborting the search: a
    hit in a chat the bot cannot read is a normal outcome, and the id alone still tells
    the caller the message exists.
    """
    res = await _invoke(_build_get_message_request(message_id), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return {"message_id": message_id, "readable": False, "reason": res.get("message", "")}
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items")
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else data
    raw_sender = item.get("sender")
    sender = raw_sender if isinstance(raw_sender, dict) else {}
    raw_body = item.get("body")
    body = raw_body if isinstance(raw_body, dict) else {}
    return {
        "message_id": message_id,
        "readable": True,
        "chat_id": item.get("chat_id", "") or "",
        "sender_id": sender.get("id", ""),
        "sender_type": sender.get("sender_type", ""),
        "msg_type": body.get("message_type") or item.get("msg_type", "") or "",
        "create_time": item.get("create_time", "") or "",
        "text": _message_plain_text(item),
    }


async def search_messages_impl(
    query: str,
    chat_ids: list[str] | None = None,
    from_ids: list[str] | None = None,
    message_type: str = "",
    from_type: str = "",
    chat_type: str = "",
    start_time: str = "",
    end_time: str = "",
    limit: int = 20,
    user_key: str = "",
) -> dict[str, Any]:
    """Search message content by keyword across the caller's chats (全局消息搜索).

    Searches as **the caller**, so it finds what that person can see and needs their
    authorization — Feishu accepts no tenant token here, which is why there is no
    bot-wide variant. ``user_key`` is therefore required, not optional.

    Feishu returns message ids only; each hit is read back so the result carries the
    actual ``text``, ``chat_id`` and sender. Hits the bot cannot read come back with
    ``readable: false`` and their id, rather than being dropped — usually a chat the bot
    isn't in, which the caller may want to know about.

    Filters narrow rather than widen: ``chat_ids`` to particular groups, ``from_ids`` to
    particular senders, ``start_time``/``end_time`` as **second**-level timestamps.
    """
    keyword = (query or "").strip()
    if not keyword:
        return _error("query is required — 要搜的关键词。")
    key = user_key.strip()
    if not key:
        return _error(
            "user_key is required — 消息搜索只能以本人身份进行 (飞书不接受机器人 token), "
            "传 <feishu_context> 里的 sender_open_id。"
        )
    body, bad = _message_search_body(
        keyword,
        [c.strip() for c in (chat_ids or []) if c and c.strip()],
        [f.strip() for f in (from_ids or []) if f and f.strip()],
        message_type,
        from_type,
        chat_type,
        start_time,
        end_time,
    )
    if bad is not None:
        return bad
    cap = max(1, min(int(limit or 20), _MESSAGE_SEARCH_HYDRATE_MAX))

    ids: list[str] = []
    page_token = ""
    has_more = False
    while True:
        res = await _invoke(
            _build_message_search_request(body, min(cap - len(ids), _MESSAGE_SEARCH_HYDRATE_MAX), page_token),
            user_key=key,
            prefer="user",
            identity="user",
            capabilities=[],
        )
        if not res["ok"]:
            return _with_hint(res, _MESSAGE_SEARCH_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        for raw in data.get("items") or []:
            # Feishu documents items as a list of message_id strings; tolerate an object
            # form too rather than returning nothing if that ever changes.
            if isinstance(raw, str) and raw:
                ids.append(raw)
            elif isinstance(raw, dict) and raw.get("message_id"):
                ids.append(str(raw["message_id"]))
        page_token = str(data.get("page_token") or "")
        has_more = bool(data.get("has_more"))
        if not has_more or not page_token or len(ids) >= cap:
            break

    ids = ids[:cap]
    messages = [await _hydrate_message(mid, key) for mid in ids]
    return {
        "ok": True,
        "query": keyword,
        "filters": {k: v for k, v in body.items() if k != "query"},
        "messages": messages,
        "count": len(messages),
        "unreadable": len([m for m in messages if not m.get("readable")]),
        "has_more": has_more and len(ids) >= cap,
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
    verifier / 通道信息一并写进 pending 文件, 供后台 watcher (``auth_collect_impl``)、
    ``auth_check_impl`` 与 ``auth_complete_impl`` 取用。

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
    # 先撤掉上一轮的 watcher 并等它收尾, **再**选通道: 新一轮授权作废旧 state, 旧 watcher
    # 再守也只会等到一个过期的码, 而它守着的结果还会被 auth_collect 当成本次的状态报出去。
    #
    # 「等它收尾」在 loopback 模式下是硬要求 (实测过): 旧 watcher 占着 17860 时,
    # plan_receiver 的「端口空不空」判定会失败, 于是本可免复制的授权被静默降级成手工贴码。
    await _auth_watch.forget_and_wait(_norm_user_key(user_key))
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
        # 回调地址是内网 IP 时, 自动回流只对在内网的用户成立; 外网用户点完同意,
        # 浏览器跳不到这个地址, 回调永远不来。此时仍走自动通道 (内网用户照样免复制),
        # 但必须把这个前提说出来并备好后路, 否则外网用户就一直卡在「等回调」上。
        private = _oauth_rx.is_private_callback(plan.redirect_uri)
        result = {
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
                "发完链接**这一轮就收尾**, 顺带请用户点完后回你一句; 别在同一轮干等 "
                "(阻塞占住 turn 锁, 用户这期间说什么都得排队). 用户回话那一轮调 "
                "feishu_auth_check (同一个 user_key) 查一眼即可完成授权; 想让码自己回来、不指望用户"
                "再回话, 就在发完链接这一轮调 feishu_auth_collect —— 它把等待放到后台, 本轮照样立刻收尾.\n"
                "授权一次即缓存并自动续期; 只有当以后的任务需要**新的**权限时才会再请你授权一次."
            ),
            "next_step": "结束本轮; 用户回话后调 feishu_auth_check",
        }
        if private:
            result["callback_is_private"] = True
            result["fallback_hint"] = (
                f"注意: 回调地址 {plan.redirect_uri} 只有**内网**打得到. 用户若在外网, "
                "点「同意授权」后页面会打不开 (一直转圈或提示无法访问), 自动回流也就不会发生 —— "
                "这不是授权失败. 遇到这种情况, 让用户把浏览器**地址栏里那一整条网址**复制回来, "
                "整条交给 feishu_auth_complete 即可 (工具会自己从里面取 code, 用户不用找 code 在哪)."
            )
        return result
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
# 一张卡把「发链接」和「收回调」缝在一起: 按钮的 behaviors 同时挂 open_url (打开
# 授权页) 和 callback (回传给 Channel), 于是用户**点一次**就既看到飞书授权页、又
# 把「我点了」这件事告诉了 agent。
#
# 点击那一轮调的是 ``feishu_auth_collect`` 而**不是** feishu_auth_wait: 收到点击时
# 用户才刚要在浏览器里点「同意」, 码还没到; 若在这一轮原地等, 就又把 SessionAgent 的
# turn 锁占住几分钟, 用户这期间说什么都排队 —— 那正是这条链路要消灭的症状。collect
# 把等待交给后台任务, 本轮立刻收尾, 码回来时由后台私聊回告用户。
_AUTH_CARD_ACTION = "feishu_auth_confirm"
_AUTH_CARD_HANDLER = "feishu_auth_collect"
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
    workspace, where the code collector would find no pending authorization.
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
            "fallback": (
                "卡片没发出去: 可以把 authorize_url 直接发给用户, 再调 feishu_auth_collect 在后台等回调 (不阻塞)."
            ),
        }
    result = {
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
            "「点此授权」时飞书会把点击回调给你, 那一轮调 "
            f"{_AUTH_CARD_HANDLER}(user_key={key!r}) 把等待交给后台 (那一轮也立刻收尾), "
            "授权码自动回流后后台会换好 token 并私聊告诉用户可以继续.\n"
            f"{_AUTH_CARD_RETRY_NOTE}"
        ),
        "next_step": f"等卡片回调, 届时调 {_AUTH_CARD_HANDLER} (不阻塞)",
    }
    # 卡片按钮的 open_url 打开的就是这个 redirect 所属的授权页, 所以内网回调地址对
    # 卡片同样成立: 外网用户点完「同意授权」照样跳不回来。第 1 级也得带上后路。
    if started.get("callback_is_private"):
        result["callback_is_private"] = True
        result["fallback_hint"] = str(started.get("fallback_hint", ""))
    return result


# ── 授权方式的降级顺序 ────────────────────────────────────────────────────────
#
# 授权有三种成色, 优先级从高到低写在这里, 而不是散在提示词里靠模型自觉:
#
#   1. TIER_CARD    卡片授权 —— 点一下即授权, 且 agent 知道用户点了 (最省事)
#   2. TIER_LINK    网站授权, 免复制 code —— 有自动回调通道, 但要用户自己去点链接
#   3. TIER_MANUAL  网站授权, 要复制 code —— 兜底, 用户得从地址栏抄一串给回来
#
# 每一级都可能因为**环境**而不可用, 于是往下退一级:
#   1→2: 没有可私聊的 open_id (群场景/没拿到 sender), 或卡片没发出去 (缺 im 权限、
#        用户没和机器人建过会话、飞书限流…)。卡片发不出去时链接仍然能发。
#   2→3: 没有自动接收通道 (既没配 PSI_OAUTH_CALLBACK_BASE, 回环端口也不可用),
#        此时 code 只能由用户手抄 —— 前两级都在承诺「不用复制」, 做不到就必须说实话。
#
# 注意 2→3 的判定发生在**更早**: auto_receive 由 auth_start_impl 决定, 它同时决定了
# 卡片是否有意义 (没有自动回流的卡片点了也还要手抄, 那按钮是个谎), 所以 manual 环境
# 下第 1 级直接跳过, 不是"发卡失败"。
TIER_CARD = "card"
TIER_LINK = "link_auto"
TIER_MANUAL = "link_manual"

_TIER_LABEL = {
    TIER_CARD: "卡片授权(点一下即可)",
    TIER_LINK: "网站授权(不用复制 code)",
    TIER_MANUAL: "网站授权(需要复制 code)",
}

# ``auth_check_impl`` 的取件窗口: 只够跑完一次取件请求, 不做第二次轮询。取件箱 TTL 约
# 10 分钟, 所以「看一眼就走」不会丢码 —— 这一点是它敢不阻塞的根据。
_CHECK_TIMEOUT_SECONDS = 3.0


async def auth_request_impl(
    user_key: str,
    capabilities: str = "",
    reason: str = "",
    receive_id: str = "",
) -> dict[str, Any]:
    """Ask this user to authorize, using the best method the environment allows.

    Single entry point for "I need authorization": it walks the three tiers in order
    (card → link-without-copy → link-with-copy) and returns the first that actually
    works, so the caller never has to know which mechanisms this deployment supports.
    Each fallback records *why* it happened in ``downgraded_from`` / ``downgrade_reason``
    rather than silently presenting a worse experience as if it were the intended one.

    The tier also decides what the caller must do next, which is why it is reported
    explicitly as ``tier``/``next_step``:

    - ``card`` — finish the turn now; wait only when the click callback arrives.
    - ``link_auto`` — send ``authorize_url``, finish the turn, then ``feishu_auth_check``
      in the turn the user reports back. Never block in the sending turn.
    - ``link_manual`` — send ``authorize_url``, then ask for the code and pass it to
      ``feishu_auth_complete``.
    """
    key = (user_key or "").strip()
    if not key:
        return _error("user_key is required — it is whose authorization this is (the sender's open_id).")

    # Tier 1: the card. Only attempted when there is a private chat to send it to —
    # a card tapped in a group is routed to the tapper's own session, which cannot
    # see the pending authorization written here.
    target = (receive_id or "").strip() or key
    card_skip = (
        "" if target.startswith("ou_") else f"没有可私聊的 open_id (receive_id={target!r} 不是 ou_ 开头), 卡片无处可发"
    )
    if not card_skip:
        card = await auth_card_impl(key, capabilities, reason, target)
        if card.get("ok"):
            tiered = {**card, "tier": TIER_CARD, "tier_label": _TIER_LABEL[TIER_CARD]}
            if card.get("callback_is_private"):
                tiered["next_step"] = (
                    f"{card.get('next_step', '')}; 若用户在外网导致授权页跳不回来, "
                    "改让他把地址栏整条网址发回来交给 feishu_auth_complete"
                )
            return tiered
        # manual_required means there is no automatic callback channel at all, so
        # tier 2 cannot work either — go straight to tier 3 and say so.
        card_skip = str(card.get("message") or "卡片发送失败")
        if card.get("manual_required"):
            started = await auth_start_impl(capabilities, key)
            if not started.get("ok"):
                return started
            return {
                **started,
                "tier": TIER_MANUAL,
                "tier_label": _TIER_LABEL[TIER_MANUAL],
                "downgraded_from": TIER_CARD,
                "downgrade_reason": "本部署没有自动接收授权码的通道, 卡片和免复制链接都做不到",
                "next_step": "把 authorize_url 发给用户, 再让他把地址栏里的 code 交给 feishu_auth_complete",
            }

    # Tier 2/3: the plain link. auto_receive decides which of the two we actually got.
    started = await auth_start_impl(capabilities, key)
    if not started.get("ok"):
        return started
    tier = TIER_LINK if started.get("auto_receive") else TIER_MANUAL
    next_step = (
        "把 authorize_url 发给用户后**结束本轮**, 请他点完回你一句; 那一轮再调 feishu_auth_check 查一眼"
        if tier == TIER_LINK
        else "把 authorize_url 发给用户, 再让他把地址栏里的 code 交给 feishu_auth_complete"
    )
    # 第 2 级承诺「不用复制」, 但内网回调地址只能对内网用户兑现这句话。这里不降级
    # (内网用户仍是免复制的), 而是把 auth_start 带回来的后路一并交给调用方 —— 承诺
    # 兑现不了时得说实话, 这条规则对「地址不可达」同样适用。
    if tier == TIER_LINK and started.get("callback_is_private"):
        next_step += "; 若用户在外网导致页面打不开, 改让他把地址栏整条网址发回来交给 feishu_auth_complete"
    return {
        **started,
        "tier": tier,
        "tier_label": _TIER_LABEL[tier],
        "downgraded_from": TIER_CARD,
        "downgrade_reason": card_skip,
        "next_step": next_step,
    }


async def _read_pending(user_key: str) -> dict[str, Any]:
    """读回 ``auth_start`` 写下的 pending 记录; 缺失/损坏返回空 dict。"""
    with contextlib.suppress(OSError, ValueError):
        raw = await anyio.Path(_pending_auth_path(user_key)).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    return {}


async def _receive_code(pending: dict[str, Any], window_seconds: float) -> dict[str, str]:
    """按 pending 记下的通道取一次码; 窗口内没等到返回空 dict。

    ``auth_check_impl`` 与后台 watcher 共用这一步, 区别只在给多长
    的窗口 —— 通道选择的逻辑只该有一份, 否则改了一处漏一处 (回环端口的取法就曾这样重复)。

    ``window_seconds`` 不是本函数自己的超时, 而是**交给接收通道**的等待窗口 (poll_gateway /
    wait_loopback 各自用 ``move_on_after`` 收口), 所以这里不该再套一层 ``fail_after``: 那会
    把「窗口内没等到码」这件正常事变成异常。
    """
    if str(pending.get("mode") or "") == "gateway":
        return await _oauth_rx.poll_gateway(str(pending.get("state") or ""), window_seconds)
    port = _oauth_rx.loopback_port()
    with contextlib.suppress(ValueError):
        from urllib.parse import urlsplit  # noqa: PLC0415

        port = urlsplit(str(pending.get("redirect_uri") or "")).port or port
    return await _oauth_rx.wait_loopback(port, str(pending.get("state") or ""), window_seconds)


async def auth_check_impl(user_key: str = "") -> dict[str, Any]:
    """查一眼授权码到没到, 不阻塞 —— 到了就完成授权, 没到立刻返回。

    与后台 watcher (``auth_collect_impl``) 是同一条取件通道, 区别只在等待时长: 这里用一个极短的窗口
    「看一眼就走」, 所以不会占住 Session 的 turn 锁。Gateway 取件箱 TTL 约 10 分钟,
    用户晚点几分钟点完「同意授权」, 下一轮再查照样拿得到, 因此**推迟取码是安全的**,
    不需要谁在原地干等。

    ``pending=True`` 表示还没到 (不是失败): 收尾本轮, 等用户说「点好了」再查一次。

    后台 watcher 在跑时**不去抢码**: 取件箱取走即删, 两边同时取会有一个白等到超时。这
    种情况直接把 watcher 的状态报出来 —— 授权照样会完成, 只是完成的人不是这一轮。
    """
    watched = _auth_watch.status(_norm_user_key(user_key))
    if watched is not None:
        if watched.status == _auth_watch.STATUS_WATCHING:
            return {
                "ok": True,
                **watched.snapshot(),
                "background": True,
                "message": (
                    "后台正在等这位用户的授权码 (本轮不必也不该再查, 免得两边抢同一个码). "
                    "**收尾本轮**: 码一到后台会自动换 token 并私聊告知用户."
                ),
                "next_step": "结束本轮; 后台会自己完成授权并回告用户",
            }
        if watched.status == _auth_watch.STATUS_GRANTED:
            # 后台已经换好 token 并清掉了 pending 文件; 不报这一条的话下面会误判成
            # 「没有待完成的授权」, 把已经成功的事说成要重新发起。
            return {
                "ok": True,
                **watched.snapshot(),
                "collected_in_background": True,
                "result": watched.result,
                "message": f"授权已由后台完成: {watched.message}",
            }
    pending = await _read_pending(user_key)
    state = str(pending.get("state") or "")
    mode = str(pending.get("mode") or "manual")
    if not state:
        return _error("没有待完成的授权, 请先调 feishu_auth_request.")
    if mode == "manual":
        return _error(
            "当前环境无法自动接收授权码, 请让用户从浏览器地址栏复制 code 后交给 feishu_auth_complete. "
            "(想免掉复制: 调 feishu_auth_env_check 看确切缺哪一项配置, 它会给出修法.)",
            manual_required=True,
            next_step="feishu_auth_env_check",
        )
    got = await _receive_code(pending, _CHECK_TIMEOUT_SECONDS)
    if not got:
        return _error(
            "授权码还没到 —— 这不是失败, 只说明用户还没在授权页点「同意授权」. "
            "**本轮就此收尾**, 请用户点完后回你一句, 那一轮再调一次 feishu_auth_check. "
            "授权码在取件箱里可留存约 10 分钟, 晚点查照样能完成.",
            pending=True,
            retry_hint="等用户说点好了, 再调 feishu_auth_check (同一个 user_key)",
        )
    if got.get("error"):
        return _error(f"用户侧授权失败: {got['error']}")
    return await auth_complete_impl(got.get("code", ""), user_key)


async def _notify_auth_outcome(user_key: str, state: _auth_watch.WatchState) -> None:
    """把后台收码的结果私聊告诉用户。

    后台任务不在任何对话轮次里, 没有「工具返回值」可以让模型转述, 所以这条消息是用户
    唯一的信号: 不发, 授权就是悄悄成功 (或悄悄失败) 的, 用户只会以为机器人没反应。
    """
    if not user_key.startswith("ou_"):
        # 只有 open_id 能私聊。default 槽位 (本机单用户) 没有收件人, 不必也无法回告。
        return
    if state.status == _auth_watch.STATUS_GRANTED:
        text = "授权成功 ✅ 已经拿到你的授权, 刚才没做完的事我接着做。"
    elif state.status == _auth_watch.STATUS_TIMEOUT:
        text = "还没收到你的授权 —— 授权页上的「同意授权」可能没点到, 或者页面没跳回来。回我一句我再发一张新的授权卡。"
    else:
        text = f"授权没能完成: {state.message} 回我一句我们再试一次。"
    await send_message_impl(user_key, text, "open_id")


async def auth_collect_impl(user_key: str = "", timeout_seconds: int = 600) -> dict[str, Any]:
    """把「等授权码」交给后台任务, **本轮立刻返回** —— 卡片回调那一轮用这个。

    等待绝不能放在工具调用里: 工具调用发生在
    SessionAgent 的 turn 内, turn 持锁, 于是用户在这几分钟里说的话全排队 (表现就是
    「机器人卡死」); 这边起一个脱离本轮的任务去等, 工具立刻返回, 码回来时后台私聊回告。

    重复调用不会起第二个 watcher: 取件箱取走即删, 两个 watcher 会互相抢码。已经在收的
    直接返回它的进度, 已经收完的返回结果。
    """
    key = _norm_user_key(user_key)
    existing = _auth_watch.status(key)
    if existing is not None and existing.status != _auth_watch.STATUS_WATCHING:
        # 后台已经收完了 —— 直接把结论给出去, 别再起一个 watcher 去等一个已被取走的码。
        return {
            "ok": existing.status == _auth_watch.STATUS_GRANTED,
            **existing.snapshot(),
            "collected_in_background": True,
            "result": existing.result,
        }
    pending = await _read_pending(user_key)
    if not str(pending.get("state") or ""):
        return _error("没有待完成的授权, 请先调 feishu_auth_request.")
    if str(pending.get("mode") or "manual") == "manual":
        return _error(
            "当前环境无法自动接收授权码, 请让用户从浏览器地址栏复制 code 后交给 feishu_auth_complete. "
            "(想免掉复制: 调 feishu_auth_env_check 看确切缺哪一项配置, 它会给出修法.)",
            manual_required=True,
            next_step="feishu_auth_env_check",
        )

    async def _collect(watched_key: str, window_seconds: float) -> dict[str, Any]:
        # pending 在任务里重读: 从建任务到真正开跑之间, 用户可能又发起了一次授权。
        parked = await _read_pending(watched_key)
        got = await _receive_code(parked, window_seconds)
        if not got:
            # 回调地址只有内网可达时, 「再等等」对外网用户是死循环: 他的浏览器根本跳不到那个
            # 地址, 等到取件箱过期也不会有回调。这时唯一的出路是让他把地址栏整条网址贴回来。
            redirect = str(parked.get("redirect_uri") or "")
            if _oauth_rx.is_private_callback(redirect):
                return _error(
                    f"等不到授权回调: 本次回调地址 {redirect} 只有内网打得到. 用户若在外网, "
                    "点完「同意授权」后页面会打不开, 回调也就永远不会来 —— 再等无用. "
                    "问他一句「授权后那个打不开的页面, 地址栏里的网址是什么」, 把他发回来的"
                    "**整条网址**交给 feishu_auth_complete 即可完成授权 (不用让他自己找 code).",
                    timed_out=True,
                    callback_is_private=True,
                )
            return _error("等待授权回调超时", timed_out=True)
        if got.get("error"):
            return _error(f"用户侧授权失败: {got['error']}")
        return await auth_complete_impl(got.get("code", ""), watched_key)

    try:
        state, started = _auth_watch.start(
            key,
            _collect,
            notify=_notify_auth_outcome,
            timeout_seconds=timeout_seconds,
        )
    except RuntimeError as exc:
        # 起不了后台任务时**不假装**已经在收: 那会让 agent 收尾本轮, 而实际上没人在等,
        # 用户点完同意后永远等不到回音。退回可查询的路径, 由 agent 下一轮 check。
        logger.warning(f"Feishu auth background collect unavailable: {exc!r}")
        return _error(
            "无法把等待放到后台, 本轮不能干等 (会占住会话). **本轮收尾**, "
            "请用户点完「同意授权」后回你一句, 那一轮调 feishu_auth_check 取码.",
            pending=True,
            background=False,
            retry_hint="用户回话那一轮调 feishu_auth_check (同一个 user_key)",
        )
    return {
        "ok": True,
        **state.snapshot(),
        "background": True,
        "already_watching": not started,
        "message": (
            "已在后台等这位用户的授权码, **这一轮就此收尾, 不要再等也不要重复调用** —— "
            f"最多守 {int(state.remaining)} 秒, 码一到就自动换 token, 并私聊告知用户可以继续. "
            "这期间用户说什么都能正常回应 (等待不占会话). 想主动确认进度再调一次本工具即可."
        ),
        "next_step": "结束本轮; 后台会自己完成授权并回告用户",
    }


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


# Public aliases for the sibling helper modules (``_feishu_md``, ``_chart_*``): they need
# the same request plumbing, and re-deriving it there would let the two copies drift on
# things like which token types a docx write accepts.
build_descendant_request = _build_descendant_request


async def invoke_request(
    request: Any,
    user_key: str | None = None,
    prefer: str = "tenant",
    identity: str = "",
    capabilities: list[str] | None = None,
) -> dict[str, Any]:
    """Public ``_invoke`` for sibling helper modules.

    A delegating function rather than an alias so that replacing ``_invoke`` (tests, or
    any future wrapper) is seen through this door too — an alias would capture the
    original at import time and quietly bypass it.
    """
    return await _invoke(request, user_key=user_key, prefer=prefer, identity=identity, capabilities=capabilities)


def real_block_id(response: dict[str, Any], temporary_id: str) -> str:
    """The permanent ``block_id`` Feishu assigned to a temporary id we sent.

    ``/descendant`` answers with ``block_id_relations`` mapping each ``temporary_block_id``
    to the real one. Any follow-up edit (growing a table, filling a cell) has to address
    the real id: the temporary one is ours, meaningful only inside that one request.
    """
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    for rel in (data or {}).get("block_id_relations") or []:
        if isinstance(rel, dict) and str(rel.get("temporary_block_id", "")) == temporary_id:
            return str(rel.get("block_id", ""))
    return ""


async def list_all_blocks(document_id: str, user_key: str = "", identity: str = "") -> dict[str, Any]:
    """Every block of a docx, raw and unpaged — for code that needs the block *graph*.

    ``list_doc_blocks_impl`` is the agent-facing reader: it trims text to a preview and
    caps the count, both wrong here, where a table's cells have to be matched to the
    paragraphs inside them. Raw payloads, all pages, no cap.
    """
    doc = document_id.strip()
    if not doc:
        return _error("document_id is required.")
    items: list[dict[str, Any]] = []
    page_token = ""
    while True:
        res = await _invoke(
            _build_document_blocks_list_request(doc, _BLOCKS_LIST_PAGE_MAX, page_token),
            user_key=user_key,
            prefer="tenant",
            identity=identity,
        )
        if not res["ok"]:
            return res
        data = res["data"] if isinstance(res["data"], dict) else {}
        items.extend(b for b in (data.get("items") or []) if isinstance(b, dict))
        page_token = str(data.get("page_token") or "")
        if not page_token:
            return {"ok": True, "document_id": doc, "blocks": items}


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


# ── Embedded spreadsheets (block_type 30) and bitables (18) inside a doc ────────
#
# A native table block (31, above) is part of the document: it holds text, and nothing
# more. What people mean by "在文档里放一个可编辑的飞书表格" is usually the other thing —
# an embedded *spreadsheet*, with a formula bar, cell formats and filters, editable in
# place and openable as its own sheet. That is block_type 30, and Feishu provisions the
# backing spreadsheet itself: creating the block with a `row_size`/`column_size` returns
# `sheet.token` of the form "<spreadsheetToken>_<sheetId>" (verified live — an empty
# `sheet: {}` is rejected with 1770001 invalid param). Block 18 is the same story for a
# 多维表格, whose token is "<appToken>_<tableId>" and which needs a `view_type`.
#
# The point of splitting that token is that no new write path is needed: the two halves
# are exactly the (spreadsheet_token, sheet_id) pair the sheets/v2 values API already
# takes, so the existing write/append/format helpers fill an in-document sheet as-is.
# Writing past the declared size is fine — the worksheet grows to fit (measured: an 8-row
# write into a 5-row block left the block reporting 8 rows).
_SHEET_BLOCK_TYPE = 30
_BITABLE_BLOCK_TYPE = 18

# Largest row_size/column_size the *create block* call accepts. Measured against the live
# API: 9 passes, 10 is refused with 99992402 "field validation failed" whatever the other
# dimension is. Nothing in the docs mentions it, and the error names no field, so the
# number is empirical — a bigger grid is reached by writing into the sheet afterwards,
# which does grow it (30x4 written into a 9x4 block leaves the worksheet at 30x4).
_SHEET_BLOCK_CREATE_MAX = 9

# view_type 1 = grid (表格视图), the default a person sees when opening a new 多维表格.
_BITABLE_DEFAULT_VIEW = 1


def _column_letter(count: int) -> str:
    """Spreadsheet column label for the ``count``-th column (1 → A, 27 → AA)."""
    if count < 1:
        return "A"
    label = ""
    while count:
        count, rem = divmod(count - 1, 26)
        label = chr(ord("A") + rem) + label
    return label


def split_embedded_sheet_token(block_token: str) -> tuple[str, str]:
    """Split an embedded block's token into its ``(container_token, child_id)`` halves.

    A sheet block's token is ``"<spreadsheetToken>_<sheetId>"`` and a bitable block's is
    ``"<appToken>_<tableId>"``. Only the *first* underscore separates them: Feishu tokens
    are alphanumeric, but splitting from the right would break the moment one contains an
    underscore, so partition from the left. Returns ``("", "")`` when there is no
    separator, letting callers report a clear error instead of writing to a half-token.
    """
    head, sep, tail = (block_token or "").strip().partition("_")
    if not sep or not head or not tail:
        return "", ""
    return head, tail


def _embedded_block_token(block: dict[str, Any], key: str) -> str:
    """The ``token`` of an embedded block's payload (``"sheet"`` / ``"bitable"``), or ``""``."""
    payload = block.get(key)
    return str(payload.get("token", "")) if isinstance(payload, dict) else ""


def _embedded_sheet_result(document_id: str, child: dict[str, Any], *, rows: int, columns: int) -> dict[str, Any]:
    """Shape a created sheet block into the tool result, including its write coordinates.

    ``spreadsheet_token`` + ``sheet_id`` are returned because they are the whole point:
    they are what ``feishu_sheet_write`` needs to fill the embedded grid, and an agent
    that only got the ``block_id`` back would have no way to write into it.
    """
    token = _embedded_block_token(child, "sheet")
    spreadsheet, sheet_id = split_embedded_sheet_token(token)
    return {
        "ok": True,
        "document_id": document_id,
        "block_id": child.get("block_id", ""),
        "block_token": token,
        "spreadsheet_token": spreadsheet,
        "sheet_id": sheet_id,
        "range": f"{sheet_id}!A1" if sheet_id else "",
        "rows": rows,
        "columns": columns,
        "url": f"{_DOC_BASE_URL}/sheets/{spreadsheet}" if spreadsheet else "",
    }


def _first_child(res: dict[str, Any], block_type: int) -> dict[str, Any] | None:
    """Pick the created block of the wanted type out of a /children or /descendant reply."""
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    children = data.get("children") if isinstance(data, dict) else None
    if not isinstance(children, list):
        return None
    for child in children:
        if isinstance(child, dict) and child.get("block_type") == block_type:
            return child
    return None


async def append_doc_sheet_impl(
    document_id: str,
    rows: int = 10,
    columns: int = 5,
    values_json: str = "",
    header_row: bool = True,
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append an embedded, editable Feishu spreadsheet (block_type 30) to a docx body.

    When ``values_json`` is given, the grid is written into the new sheet, so one call
    produces a filled in-document spreadsheet. The write goes through the ordinary
    sheets/v2 path, which means ``=``-prefixed cells become live formulas — the reason to
    embed a sheet rather than use a plain table block.

    The block is created at most 9x9 (the API's undocumented creation cap) and grown to
    the requested/data size by the write that follows, including for an empty sheet, whose
    area is written as blank cells. So the size asked for is the size that appears.

    A failed *write* still returns the block's coordinates with ``ok: False``: the sheet
    exists in the document at that point, and silently dropping its token would leave an
    empty embed nobody can fill.
    """
    if not document_id.strip():
        return _error("document_id is required.")
    doc = document_id.strip()

    values: list[list[Any]] | None = None
    if values_json.strip():
        values, err = _parse_values_json(values_json)
        if err or values is None:
            return _error(err or "values_json produced no rows.")
    if rows < 1 or columns < 1:
        return _error("rows and columns must both be at least 1.")
    if rows > _SHEET_MAX_ROWS or columns > _SHEET_MAX_COLS:
        return _error(f"an embedded sheet is capped at {_SHEET_MAX_ROWS} rows x {_SHEET_MAX_COLS} columns.")

    # The wanted final size, which is usually *larger* than the block can be created at.
    # With data, the data decides: padding a 4-column table out to the default 5 would add
    # a stray empty column the caller never asked for.
    want_rows, want_columns = rows, columns
    if values:
        want_rows = len(values)
        want_columns = max((len(r) for r in values), default=0)
    # Creating the block is capped at 9x9 (measured: row_size or column_size of 10 is
    # refused with 99992402 field validation failed, 9 is accepted). The cap only applies
    # to *creation*: a subsequent ranged write grows the worksheet, so a big table starts
    # from a clamped block and is expanded by its own write.
    create_rows = min(want_rows, _SHEET_BLOCK_CREATE_MAX)
    create_columns = min(want_columns, _SHEET_BLOCK_CREATE_MAX)
    rows, columns = create_rows, create_columns

    result_extra: dict[str, Any] = {}
    if caption.strip():
        # Same convention as the table tools: a 表 caption goes above what it labels, and
        # it is written first so a failed caption never numbers a sheet that isn't there.
        text, fields = await _resolve_table_caption(doc, caption, auto_number, user_key, identity)
        result_extra.update(fields)
        note = await append_doc_content_impl(doc, text, user_key, identity)
        result_extra["caption_written"] = bool(note.get("ok"))
        if not note.get("ok"):
            result_extra["caption_error"] = note.get("message", "")

    block = {"block_type": _SHEET_BLOCK_TYPE, "sheet": {"row_size": rows, "column_size": columns}}
    res = await _invoke(_build_blocks_append_request(doc, [block]), user_key=user_key, prefer="user", identity=identity)
    if not res["ok"]:
        return res
    child = _first_child(res, _SHEET_BLOCK_TYPE)
    if child is None:
        return _error("Feishu created the block but returned no sheet block to write into.")
    out = {**_embedded_sheet_result(doc, child, rows=want_rows, columns=want_columns), **result_extra}
    needs_growing = values is None and (want_rows > create_rows or want_columns > create_columns)
    if values is None and not needs_growing:
        return out
    # Split again into plain strings rather than reading them back out of ``out``, whose
    # value type is the union of everything in the result dict.
    block_token = _embedded_block_token(child, "sheet")
    spreadsheet, sheet_id = split_embedded_sheet_token(block_token)
    if not spreadsheet or not sheet_id:
        return {
            **out,
            "ok": False,
            "message": (
                f"embedded sheet created but its token {block_token!r} could not be split into "
                "spreadsheet_token/sheet_id — write the values with feishu_sheet_write once you have them."
            ),
        }

    # An empty sheet asked to be bigger than the creation cap is grown by writing blank
    # cells over the wanted area — the same ranged write, just with nothing in it, so the
    # person gets the 20 empty rows they asked to type into rather than a silent 9.
    payload = values if values is not None else [[None] * want_columns for _ in range(want_rows)]
    # The range must span the grid. A bare "<sheetId>!A1" is accepted by Feishu and comes
    # back ok=True with an empty updatedRange having written *nothing* — data silently
    # lost — so the end cell is always spelled out.
    end = f"{_column_letter(want_columns)}{want_rows}"
    wrote = await write_sheet_impl(
        spreadsheet,
        f"{sheet_id}!A1:{end}",
        json.dumps(payload, ensure_ascii=False),
        user_key,
        identity,
    )
    if not wrote["ok"]:
        return {
            **out,
            "ok": False,
            "values_written": False,
            "message": f"Embedded sheet created but writing its values failed: {wrote.get('message', '')}",
            **({"need_auth": True} if wrote.get("need_auth") else {}),
        }
    if values is not None:
        out["values_written"] = True
    out["updated_cells"] = wrote.get("updated_cells")
    if header_row and values:
        # Bold header, matching what feishu_doc_append_table's header row looks like. A
        # style failure is reported but doesn't fail the call: the data is already there.
        styled = await format_sheet_impl(
            spreadsheet,
            f"{sheet_id}!A1:{_column_letter(len(values[0]))}1",
            json.dumps({"font": {"bold": True}}),
            user_key,
            identity,
        )
        out["header_styled"] = bool(styled.get("ok"))
    return out


async def append_doc_bitable_impl(
    document_id: str,
    view_type: int = _BITABLE_DEFAULT_VIEW,
    user_key: str = "",
    identity: str = "",
    caption: str = "",
    auto_number: bool = True,
) -> dict[str, Any]:
    """Append an embedded 多维表格 (bitable, block_type 18) to a docx body.

    Returns the new bitable's ``app_token`` and ``table_id`` — split out of the block's
    ``"<appToken>_<tableId>"`` token — so the existing ``feishu_bitable_*`` tools can add
    fields and records to it. Feishu creates the bitable itself; it starts with default
    fields, which ``feishu_bitable_create_field`` can extend.
    """
    if not document_id.strip():
        return _error("document_id is required.")
    doc = document_id.strip()
    result_extra: dict[str, Any] = {}
    if caption.strip():
        text, fields = await _resolve_table_caption(doc, caption, auto_number, user_key, identity)
        result_extra.update(fields)
        note = await append_doc_content_impl(doc, text, user_key, identity)
        result_extra["caption_written"] = bool(note.get("ok"))
        if not note.get("ok"):
            result_extra["caption_error"] = note.get("message", "")

    block = {"block_type": _BITABLE_BLOCK_TYPE, "bitable": {"view_type": int(view_type or _BITABLE_DEFAULT_VIEW)}}
    res = await _invoke(_build_blocks_append_request(doc, [block]), user_key=user_key, prefer="user", identity=identity)
    if not res["ok"]:
        return res
    child = _first_child(res, _BITABLE_BLOCK_TYPE)
    if child is None:
        return _error("Feishu created the block but returned no bitable block.")
    token = _embedded_block_token(child, "bitable")
    app_token, table_id = split_embedded_sheet_token(token)
    return {
        "ok": True,
        "document_id": doc,
        "block_id": child.get("block_id", ""),
        "block_token": token,
        "app_token": app_token,
        "table_id": table_id,
        "url": f"{_DOC_BASE_URL}/base/{app_token}" if app_token else "",
        **result_extra,
    }


async def append_doc_content_impl(
    document_id: str,
    content: str,
    user_key: str = "",
    identity: str = "",
) -> dict[str, Any]:
    """Append text/heading blocks (from plain text or light Markdown) to a docx body.

    Content that uses Markdown beyond headings — a ``|``-delimited table, a ``- `` list,
    ``**bold**``, a fenced code block — is routed through Feishu's own Markdown converter
    so it lands as *native blocks*: a real table you can drag, sort and edit, not the
    literal pipes and dashes this tool used to write. Plain prose keeps the local
    heading/paragraph mapping, which needs no round-trip.

    Pass ``user_key`` to write as that user (e.g. into a doc inside a user-owned wiki);
    empty uses the bot's tenant token.
    """
    if not document_id.strip():
        return _error("document_id is required.")
    if not (content or "").strip():
        return _error("content is empty — nothing to write.")
    # Imported lazily: _feishu_md imports this module, so a module-scope import would cycle.
    import _feishu_md as _md  # noqa: PLC0415

    if _md.has_rich_markdown(content):
        return await _md.append_markdown(document_id.strip(), content, user_key, identity)
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
    18: "bitable",
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


def _embedded_block_coordinates(raw: dict[str, Any], block_type: int) -> dict[str, Any]:
    """Write coordinates for an embedded sheet/bitable block, or ``{}`` for anything else.

    Keyed by what the caller does next: a sheet block yields the
    ``spreadsheet_token``/``sheet_id``/``range`` that ``feishu_sheet_*`` takes, a bitable
    block the ``app_token``/``table_id`` that ``feishu_bitable_*`` takes.
    """
    if block_type == _SHEET_BLOCK_TYPE:
        token = _embedded_block_token(raw, "sheet")
        spreadsheet, sheet_id = split_embedded_sheet_token(token)
        return {
            "block_token": token,
            "spreadsheet_token": spreadsheet,
            "sheet_id": sheet_id,
            "range": f"{sheet_id}!A1" if sheet_id else "",
        }
    if block_type == _BITABLE_BLOCK_TYPE:
        token = _embedded_block_token(raw, "bitable")
        app_token, table_id = split_embedded_sheet_token(token)
        return {"block_token": token, "app_token": app_token, "table_id": table_id}
    return {}


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
            entry = {
                "block_id": raw.get("block_id", ""),
                "block_type": block_type,
                "type_name": _BLOCK_TYPE_NAMES.get(block_type, str(block_type)),
                "parent_id": raw.get("parent_id", ""),
                "text": text if len(text) <= 200 else text[:200] + "…",
                "editable_text": block_type in _TEXTUAL_BLOCK_KEYS,
            }
            # An embedded sheet/bitable holds no text, so the fields above say nothing
            # about it: its content lives in a separate spreadsheet addressed by the
            # block's token. Surfacing the split token here is what makes an *existing*
            # in-document table editable — otherwise finding one and updating a cell
            # would be impossible, since only the create call ever returned its token.
            entry.update(_embedded_block_coordinates(raw, block_type))
            items.append(entry)
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


# ── Read status: who has read a message (已读 / 未读) ──────────────────────────
#
# GET /open-apis/im/v1/messages/:message_id/read_users answers only half the
# question: it returns the users who HAVE read the message and there is no
# "unread users" endpoint at all. So 未读 is computed here — pull the chat's
# roster and subtract the readers — because the alternative is every caller
# reporting "3 人已读" and staying silent about the 12 who haven't.
#
# That diff needs the message's chat_id, which the caller rarely has at hand, so
# it is resolved from the message itself (GET on the message) instead of being
# demanded as an argument. The sender is excluded from 未读: the bot obviously
# read its own message and Feishu never lists it as a reader.
#
# Two limits are invisible in the raw error text and are exactly what this API
# trips over: only the bot's OWN messages can be queried (230012), and only
# within 7 days of sending (230033).

_READ_STATUS_ERROR_HINTS = {
    230001: "请求参数不合法 (message_id 必须是 om_... 开头的消息 id)。",
    230002: "机器人不在该会话里, 先把机器人加入群再查询已读情况。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230012: "只能查询机器人自己发出的消息的已读情况; 别人发的消息查不了 (飞书不开放)。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230027: "缺少查询已读所需权限 (im:message / im:message:readonly / im:message:basic); 外部群不支持。",
    230033: "超出 7 天查询窗口: 只能查询发送后 7 天以内的消息。",
    230110: "该消息已被撤回或删除, 无法查询已读情况。",
}


def _build_read_users_request(message_id: str, user_id_type: str, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages/:message_id/read_users"
    req.paths["message_id"] = message_id
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", max(1, min(page_size, 100)))
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_get_message_request(message_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/messages/:message_id"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def _message_chat_and_sender(message_id: str, user_key: str = "") -> tuple[str, str]:
    """The ``(chat_id, sender_id)`` of a message, or ``("", "")`` if it can't be read.

    Used to locate the roster for an unread diff without making the caller pass a
    chat_id they'd have to dig up. Failure is not fatal to the caller: the read
    list is still worth returning without the unread half.
    """
    res = await _invoke(_build_get_message_request(message_id), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return "", ""
    data = res["data"] if isinstance(res["data"], dict) else {}
    items = data.get("items")
    item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else data
    sender = item.get("sender")
    sender_id = sender.get("id", "") if isinstance(sender, dict) else ""
    return item.get("chat_id", "") or "", sender_id or ""


async def read_status_impl(
    message_id: str,
    include_unread: bool = True,
    page_size: int = 100,
    user_key: str = "",
) -> dict[str, Any]:
    """Who has read a message the bot sent — and, by diff, who hasn't.

    Pages through the readers in full (the caller wants a roll-call, not page 1)
    and, when ``include_unread``, subtracts them from the chat's roster to get the
    people who still haven't. The sender is left out of both lists.

    Only the bot's own messages, sent within 7 days, can be queried at all — both
    limits come back as a ``hint`` rather than a bare ``2300xx``.
    """
    mid, bad = _require_message_id(message_id, "check the read status of")
    if bad is not None:
        return bad

    readers: list[dict[str, str]] = []
    page_token = ""
    while True:
        res = await _invoke(
            _build_read_users_request(mid, "open_id", page_size, page_token), user_key=user_key, prefer="tenant"
        )
        if not res["ok"]:
            return _with_hint(res, _READ_STATUS_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        raw_items = data.get("items")
        items: list[Any] = raw_items if isinstance(raw_items, list) else []
        for it in items:
            if isinstance(it, dict):
                readers.append({"open_id": it.get("user_id", ""), "read_time": it.get("timestamp", "")})
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break

    result: dict[str, Any] = {
        "ok": True,
        "message_id": mid,
        "read_users": readers,
        "read_count": len(readers),
    }
    if not include_unread:
        return result
    return {**result, **await _unread_from_roster(mid, readers, user_key)}


async def _unread_from_roster(message_id: str, readers: list[dict[str, str]], user_key: str) -> dict[str, Any]:
    """The unread half of a read-status answer: chat roster minus readers minus sender.

    Kept separate because it is best-effort — a p2p chat, a roster the bot may not
    list, or an unreadable message each cost the unread list but not the read one,
    so every failure returns a ``note`` instead of an error.
    """
    chat_id, sender_id = await _message_chat_and_sender(message_id, user_key)
    if not chat_id:
        return {"note": "未读名单需要消息所在会话的成员列表, 但这条消息读不到 (可能已撤回或机器人不可见)。"}
    roster = await list_chat_members_impl(chat_id)
    if not roster.get("ok"):
        return {
            "chat_id": chat_id,
            "note": f"已读名单已取到, 但群成员列表拉取失败, 无法算未读: {roster.get('message', '')}".strip(),
        }
    read_ids = {r["open_id"] for r in readers if r.get("open_id")}
    unread = [
        {"open_id": m["id"], "name": m.get("name", "")}
        for m in roster.get("members", [])
        if m.get("id") and m["id"] not in read_ids and m["id"] != sender_id
    ]
    return {
        "chat_id": chat_id,
        "unread_users": unread,
        "unread_count": len(unread),
        "member_count": roster.get("count", 0),
    }


# ── Pin / unpin a message (置顶) ───────────────────────────────────────────────
#
# POST /open-apis/im/v1/pins pins, DELETE /open-apis/im/v1/pins/:message_id
# unpins, GET /open-apis/im/v1/pins lists a group's pins (newest first).
#
# Two behaviours are worth not fighting: pinning an already-pinned message
# returns the existing pin rather than an error, and unpinning a message that was
# never pinned succeeds. Both are reported honestly as ok rather than dressed up.
#
# 230046 is the one that actually bites: many groups restrict 置顶 to the owner or
# admins, and the bot is usually neither. That needs a *person's* identity
# (``user_key`` + authorization), which the hint says outright instead of leaving
# a bare "no permission".

_PIN_ERROR_HINTS = {
    230001: "请求参数不合法 (message_id 必须是 om_... 开头的消息 id)。",
    230002: "机器人不在该群里, 先把机器人加入群再置顶。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230011: "该消息已被撤回, 无法置顶。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230027: "缺少 Pin 所需权限 (im:message / im:message.pins:write_only / im:message:send_as_bot); "
    "外部群还需开启对外共享。",
    230045: "会话不存在 (群可能已解散)。",
    230046: "该群限制只有群主/管理员能置顶: 用管理员本人身份操作 (传其 user_key 并完成授权), 或让群主放开权限。",
    230047: "同一条消息的置顶/取消置顶操作过于频繁 (上限 5 QPS), 稍后再试。",
    230048: "获取群 Pin 列表过于频繁, 稍后再试。",
    230050: "该消息对当前操作身份不可见, 无法置顶。",
    230054: "该消息类型不支持置顶。",
    230111: "该消息即将自动销毁, 不支持此操作。",
    232009: "群组已解散, 无法操作。",
}


def _build_pin_request(message_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/pins"
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"message_id": message_id}
    return req


def _build_unpin_request(message_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/im/v1/pins/:message_id"
    req.paths["message_id"] = message_id
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_list_pins_request(
    chat_id: str, start_time: str, end_time: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/im/v1/pins"
    req.add_query("chat_id", chat_id)
    if start_time:
        req.add_query("start_time", start_time)
    if end_time:
        req.add_query("end_time", end_time)
    req.add_query("page_size", max(1, min(page_size, 50)))
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _pin_record(item: Any) -> dict[str, Any]:
    """One pin as {message_id, chat_id, operator_id, operator_id_type, create_time}."""
    if not isinstance(item, dict):
        return {}
    return {
        "message_id": item.get("message_id", ""),
        "chat_id": item.get("chat_id", ""),
        "operator_id": item.get("operator_id", ""),
        "operator_id_type": item.get("operator_id_type", ""),
        "create_time": item.get("create_time", ""),
    }


async def pin_message_impl(message_id: str, user_key: str = "") -> dict[str, Any]:
    """Pin a message to the top of its chat.

    Idempotent by Feishu's own design: pinning an already-pinned message returns
    that existing pin, so a repeat call is reported as ok rather than as an error.
    """
    mid, bad = _require_message_id(message_id, "pin")
    if bad is not None:
        return bad
    res = await _invoke(_build_pin_request(mid), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _PIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    pin = data.get("pin") if isinstance(data.get("pin"), dict) else {}
    return {"ok": True, "pinned": True, **{**_pin_record(pin), "message_id": mid}}


async def unpin_message_impl(message_id: str, user_key: str = "") -> dict[str, Any]:
    """Remove a message's pin (取消置顶).

    Feishu also returns success when the message was never pinned, so this cannot
    confirm that a pin actually existed — only that none does now.
    """
    mid, bad = _require_message_id(message_id, "unpin")
    if bad is not None:
        return bad
    res = await _invoke(_build_unpin_request(mid), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _PIN_ERROR_HINTS)
    return {"ok": True, "message_id": mid, "pinned": False}


async def list_pins_impl(
    chat_id: str,
    start_time: str = "",
    end_time: str = "",
    page_size: int = 50,
    page_token: str = "",
    user_key: str = "",
) -> dict[str, Any]:
    """List a group's pinned messages, newest pin first.

    Only the pin records are returned (message_id + who pinned it + when); the
    pinned messages' own content is not included, so read it with
    ``feishu_message_list`` or the message id if the text is needed.
    """
    cid = chat_id.strip()
    if not cid:
        return _error("chat_id is required (the oc_... id of the group whose pins you want).")
    if not cid.startswith("oc_"):
        return _error(
            f"chat_id must be a group id starting with 'oc_', got {cid!r}. "
            "群 id 来自 feishu_chat_find 或 <feishu_context>; Pin 列表只支持按群查询。",
        )
    res = await _invoke(
        _build_list_pins_request(cid, start_time.strip(), end_time.strip(), page_size, page_token),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _PIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    raw_items = data.get("items")
    items: list[Any] = raw_items if isinstance(raw_items, list) else []
    pins = [_pin_record(it) for it in items]
    return {
        "ok": True,
        "chat_id": cid,
        "pins": pins,
        "count": len(pins),
        "has_more": bool(data.get("has_more")),
        "page_token": data.get("page_token", "") or "",
    }


# ── Forward a message to another chat (转发 / 合并转发) ────────────────────────
#
# POST /open-apis/im/v1/messages/:message_id/forward moves one message to another
# target; POST /open-apis/im/v1/messages/merge_forward bundles 1-100 messages from
# the SAME conversation into a single 合并转发 card.
#
# Forwarding preserves the original's attribution and content, which is the point:
# re-sending the text with feishu_message_send loses who said it and silently
# drops any attachment. The trade is that the content cannot be altered — to add
# a remark, forward and then send a comment separately.
#
# Both endpoints accept a thread_id (``omt_...``) as the target, which the shared
# id inference doesn't know (feishu_message_send cannot send to a thread), so
# forwarding resolves the type itself.

_FORWARD_ERROR_HINTS = {
    230001: "请求参数不合法 (message_id 必须是 om_..., receive_id 与其类型要匹配)。",
    230002: "机器人不在目标群里, 先把机器人加入目标群再转发。",
    230006: "应用未启用机器人能力, 到开发者后台开启后再试。",
    230013: "机器人对该用户不可用 (不在应用可用范围, 或该用户已离职)。",
    230018: "目标群当前设置不允许该操作 (如全员禁言)。",
    230019: "目标话题 (thread) 不存在。",
    230020: "转发过于频繁, 触发限流 (单个目标 5 QPS), 稍后再试。",
    230027: "缺少转发所需权限 (im:message / im:message:send_as_bot); 外部群还需开启对外共享。",
    230029: "目标用户已离职。",
    230034: "receive_id 不合法, 或与 receive_id_type 不匹配。",
    230035: "没有向目标会话发消息的权限 (可能被禁言, 或机器人被屏蔽)。",
    230038: "跨租户单聊不允许该操作。",
    230049: "原消息还在发送中, 稍等再转发。",
    230050: "原消息对当前身份不可见, 无法转发。",
    230053: "该用户已停止接收机器人消息。",
    230061: "该消息类型不支持转发 (红包/投票/语音/日程转让/系统消息等不可转发)。",
    230062: "没有权限转发到第三方加密群。",
    230063: "目标群 chat_id 不合法。",
    230064: "要转发的消息不合法 (合并转发的子消息不能再次转发)。",
    230065: "要转发的消息已被撤回。",
    230066: "密聊消息不支持转发。",
    230067: "合并转发的消息来源不合规 (不能跨多个话题, 也不能混合普通消息和话题回复)。",
    230069: "合并转发的消息必须来自同一个会话, 当前这批跨了不同群。",
    230070: "限制模式下不允许转发。",
    230074: "目标话题对当前身份不可见。",
    230110: "原消息已被删除, 无法转发。",
    232009: "群组已解散, 无法转发。",
}


def _infer_forward_target_type(receive_id: str, given: str) -> str:
    """Like ``_infer_receive_id_type``, but a ``omt_`` target is a thread.

    Forwarding is the only path that accepts ``thread_id``, and the prefix is
    unambiguous — inferring it here means "转发到这个话题里" works without the
    caller also spelling out the type.
    """
    rid = receive_id.strip()
    if rid.startswith("omt_"):
        return "thread_id"
    return _infer_receive_id_type(rid, given)


def _build_forward_request(message_id: str, receive_id: str, receive_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/messages/:message_id/forward"
    req.paths["message_id"] = message_id
    req.add_query("receive_id_type", receive_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"receive_id": receive_id}
    return req


def _build_merge_forward_request(message_ids: list[str], receive_id: str, receive_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/im/v1/messages/merge_forward"
    req.add_query("receive_id_type", receive_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    req.body = {"receive_id": receive_id, "message_id_list": message_ids}
    return req


def _require_receive_id(receive_id: str) -> tuple[str, dict[str, Any] | None]:
    """Normalize a forward target id, or say why it can't be one."""
    rid = receive_id.strip()
    if not rid:
        return "", _error(
            "receive_id is required — the target chat_id (oc_...), open_id (ou_...), "
            "union_id (on_...), email, or thread_id (omt_...) to forward to."
        )
    if rid.startswith("om_"):
        return "", _error(
            f"receive_id must be a *target* (chat/user/thread), got a message id {rid!r}. "
            "转发的目标是会话或人: 群用 chat_id (oc_...), 私聊用 open_id (ou_...), 话题用 thread_id (omt_...)。",
        )
    return rid, None


def _parse_message_ids(message_ids_json: str) -> tuple[list[str] | None, str | None]:
    """Parse a JSON array (or comma-separated list) of ``om_...`` ids; return (ids, error)."""
    raw = message_ids_json.strip()
    if not raw:
        return None, 'message_ids_json is required — a JSON array of om_... message ids, e.g. ["om_a", "om_b"].'
    try:
        parsed = json.loads(raw)
    except ValueError:
        # A bare comma-separated list is the likely hand-written form; accept it
        # rather than failing on the quoting.
        parsed = [part.strip() for part in raw.split(",") if part.strip()]
    if isinstance(parsed, str):
        parsed = [parsed]
    if not isinstance(parsed, list) or not parsed:
        return None, 'message_ids_json must be a non-empty JSON array of message ids, e.g. ["om_a", "om_b"].'
    ids = [str(x).strip() for x in parsed]
    bad = [x for x in ids if not x.startswith("om_")]
    if bad:
        return None, (
            f"these are not message ids: {bad}. 合并转发只接受 om_... 开头的消息 id "
            "(来自 feishu_message_list / feishu_message_send 的返回)。"
        )
    if len(ids) > 100:
        return None, f"合并转发一次最多 100 条消息, 收到 {len(ids)} 条。"
    return ids, None


async def forward_message_impl(
    message_id: str,
    receive_id: str,
    receive_id_type: str = "chat_id",
    user_key: str = "",
) -> dict[str, Any]:
    """Forward one message to another chat, user or thread, keeping its attribution.

    The target's type is inferred from its prefix (``oc_``/``ou_``/``on_``/``omt_``/
    an email), so the default ``receive_id_type`` does not have to be corrected for
    a DM or a thread — only a bare user_id needs it stated.
    """
    mid, bad = _require_message_id(message_id, "forward")
    if bad is not None:
        return bad
    rid, bad_target = _require_receive_id(receive_id)
    if bad_target is not None:
        return bad_target
    rid_type = _infer_forward_target_type(rid, receive_id_type)
    res = await _invoke(_build_forward_request(mid, rid, rid_type), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _FORWARD_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "forwarded": True,
        "source_message_id": mid,
        "message_id": data.get("message_id", ""),
        "chat_id": data.get("chat_id", ""),
        "thread_id": data.get("thread_id", ""),
        "receive_id": rid,
        "receive_id_type": rid_type,
    }


async def merge_forward_messages_impl(
    message_ids_json: str,
    receive_id: str,
    receive_id_type: str = "chat_id",
    user_key: str = "",
) -> dict[str, Any]:
    """Forward several messages as one 合并转发 bundle.

    All the ids must come from the *same* conversation (Feishu answers 230069
    otherwise). Ids it refuses individually come back in ``invalid_message_ids``
    instead of being lost, so a partial bundle can be explained.
    """
    ids, err = _parse_message_ids(message_ids_json)
    if err is not None:
        return _error(err)
    rid, bad_target = _require_receive_id(receive_id)
    if bad_target is not None:
        return bad_target
    rid_type = _infer_forward_target_type(rid, receive_id_type)
    res = await _invoke(_build_merge_forward_request(ids or [], rid, rid_type), user_key=user_key, prefer="tenant")
    if not res["ok"]:
        return _with_hint(res, _FORWARD_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    raw_message = data.get("message")
    message: dict[str, Any] = raw_message if isinstance(raw_message, dict) else data
    invalid = data.get("invalid_message_id_list")
    return {
        "ok": True,
        "forwarded": True,
        "source_message_ids": ids,
        "forwarded_count": len(ids or []),
        "message_id": message.get("message_id", ""),
        "chat_id": message.get("chat_id", ""),
        "receive_id": rid,
        "receive_id_type": rid_type,
        "invalid_message_ids": invalid if isinstance(invalid, list) else [],
    }


# ── 通讯录管理 (contact admin) — 共用错误码 hint ────────────────────────────────
#
# 这一批端点全部只吃 tenant_access_token (scope contact:contact / contact:group /
# contact:functional_role), 所以调用一律 prefer="tenant" —— 传 prefer="user" 会去问
# 「这东西归谁」, 而通讯录条目不存在归属问题, 那个问题问出来就是答不上来的。
#
# 最常见的两类失败根本不是参数写错:
#   40004 / 41050 / 42009 —— 应用的「通讯录权限范围」没覆盖到目标 (后台配的, 改代码没用)
#   42010            —— 有些接口 (建用户组) 硬要求范围 = 全部成员
# 所以 hint 直接说去哪儿改, 而不是复述一遍 "no permission"。
_CONTACT_ADMIN_ERROR_HINTS = {
    40002: "根部门 (department_id='0') 不支持这个操作。",
    40004: "目标部门不在应用的「通讯录权限范围」内; 去开发者后台 > 应用权限 > 通讯录范围里加上该部门。",
    40008: "部门信息为空。",
    40014: "父部门不存在或不在通讯录范围内。",
    40015: "部门不存在; 用 feishu_department_tree 核对 department_id。",
    41001: "手机号已被租户内其他账号占用。",
    41002: "邮箱已被租户内其他账号占用。",
    41003: "该手机号和邮箱分属两个不同账号。",
    41004: "手机号格式非法; 非中国大陆号码要带 + 国家码 (如 +81...)。",
    41005: "邮箱格式非法。",
    41011: "user_id 重复; 换一个或留空让飞书自动分配。",
    41017: "缺 department_ids; 建用户必须指定至少一个部门。",
    41025: "orders 里引用了该用户并不属于的部门。",
    41030: "leader_user_id 不能是这个用户自己。",
    41033: "department_ids 超过 50 个。",
    41050: "没有该用户的操作权限; 该用户所在部门不在应用的通讯录范围内。",
    41052: "资源转交人非法 (已离职/不存在/不在通讯录范围)。",
    41059: "employee_type 非法; 1 正式 2 实习 3 外包 4 劳务 5 顾问 (自定义类型用后台的枚举号)。",
    41060: "employee_type 对应的人员类型已停用。",
    41071: "member_id_type 非法。",
    41072: "member_id 与 member_id_type 不匹配 (例如 id_type 填 open_id 却传了 user_id)。",
    41073: "member_id 非法。",
    41074: "member_type 非法; 用户组成员目前只支持 'user'。",
    41201: "角色名已存在 (租户内必须唯一)。",
    41202: "role_id 不存在; 角色 id 只能从建角色的返回值或管理后台「组织架构 > 角色管理」拿。",
    41208: "角色数量已达租户上限 500。",
    41209: "单个角色成员数已达上限 1000。",
    41410: "主部门的 department_order 必须是最大的那个。",
    42002: "group_id 非法; 用 feishu_user_group(action='list') 重新取。",
    42005: "该成员已在这个用户组里 (无需重复添加)。",
    42006: "该用户已离职, 不能加入用户组。",
    42009: "该用户组不在应用的通讯录权限范围内。",
    42010: "这个接口要求应用的通讯录权限范围是「全部成员」, 当前不是; 去开发者后台改范围。",
    42012: "用户组成员数超限 (单组 10 万; 全部普通组之和不得超过租户人数的 10 倍)。",
    42016: "用户组数量已达租户上限 500。",
    42029: "该字段不支持通过 OpenAPI 修改, 只能去管理后台改。",
    43005: "order 重复; 同一父部门下 order 必须唯一。",
    43010: "部门层级过大, 不支持递归查询; 改成逐层查 (recursive=False) 或换更小的子部门。",
    43011: "部门里还有用户, 删不掉; 先把人挪走或设为离职。",
    43012: "部门里还有子部门, 删不掉; 先删子部门 (从最深一层往上删)。",
    43022: "同一父部门下已有同名部门。",
    43023: "没有操作该部门的权限。",
    43024: "并发冲突, 稍后重试。",
    43029: "部门名不能含斜杠 '/'。",
    43030: "并发冲突, 稍后重试。",
    44037: "租户管理员不能被离职; 先去管理后台撤掉他的管理员身份。",
    44042: "该用户正在恢复流程中, 稍后重试。",
    44062: "该租户的账号只能通过「成员生命周期引擎」处理, 不能走这个接口。",
    48001: "搜索参数非法。",
    1970011: "page_size 越界 (关联组织列表要求 1-100)。",
    1970012: "page_token 非法; 用上一页返回的 page_token。",
}


# ── Contact — 按手机号/邮箱定位用户 (users/batch_get_id) ────────────────────────
#
# 补上「只有一串手机号/邮箱, 要拿 open_id」这个缺口: feishu_contact_search 只能按
# *姓名* 搜且只吃 user token, 这个按联系方式精确命中且用 tenant token。
#
# 四个照着文档写也会踩的地方, 所以做成 Python 工具而不是留给 feishu_api:
#  1. 是 POST 不是 GET —— 查询条件在 body 里, 直觉上会写成 query 参数。
#  2. include_resigned 默认 false, 离职的人会**静默查不到** (不报错, 只是少一条),
#     于是「查无此人」和「已离职」看起来一模一样。默认改成 True 并回报 is_resigned,
#     让「查不到」真的只意味着查不到。
#  3. 响应只回显命中的那个键 (查邮箱回 email, 查手机回 mobile), 不回姓名 ——
#     所以这里顺手补一次 get_users_batch_impl 把姓名/部门带上, 否则拿到一串
#     ou_xxx 还得再调一次才知道是谁。
#  4. 不支持企业邮箱 (enterprise_email), 传了就是查不到; 非大陆手机号必须带 +国家码。
_BATCH_GET_ID_MAX = 50


def _build_batch_get_id_request(
    emails: list[str], mobiles: list[str], include_resigned: bool, user_id_type: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/contact/v3/users/batch_get_id"
    req.add_query("user_id_type", user_id_type)
    body: dict[str, Any] = {"include_resigned": include_resigned}
    if emails:
        body["emails"] = emails
    if mobiles:
        body["mobiles"] = mobiles
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _split_contacts(raw: str) -> list[str]:
    """逗号/空格/分号分隔的联系方式列表 -> 去重后的列表 (保持顺序)。"""
    parts = [p.strip() for p in re.split(r"[,;\s]+", raw or "") if p.strip()]
    return list(dict.fromkeys(parts))


async def find_users_by_contact_impl(
    mobiles: str = "",
    emails: str = "",
    include_resigned: bool = True,
    user_id_type: str = "open_id",
) -> dict[str, Any]:
    """按手机号/邮箱精确定位用户, 返回 open_id 及姓名。

    返回 users[]: {open_id/user_id, matched_by, matched_value, name, ...,
    is_resigned, is_activated} 以及 not_found[] —— 哪些号码/邮箱没查到。
    """
    mobile_list = _split_contacts(mobiles)
    email_list = _split_contacts(emails)
    if not mobile_list and not email_list:
        return _error("至少要给一个 mobiles 或 emails (逗号分隔)。")
    for label, items in (("mobiles", mobile_list), ("emails", email_list)):
        if len(items) > _BATCH_GET_ID_MAX:
            return _error(f"{label} 一次最多 {_BATCH_GET_ID_MAX} 个, 当前 {len(items)} 个; 分批调用。")

    res = await _invoke(
        _build_batch_get_id_request(email_list, mobile_list, include_resigned, user_id_type),
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)

    data = res["data"] if isinstance(res["data"], dict) else {}
    raw_list = data.get("user_list", []) if isinstance(data.get("user_list"), list) else []
    found: list[dict[str, Any]] = []
    seen_ids: list[str] = []
    matched_values: set[str] = set()
    for it in raw_list:
        if not isinstance(it, dict):
            continue
        uid = it.get("user_id", "") or ""
        # 飞书对查不到的条目也回一条 (只有 email/mobile 没有 user_id), 据此判定未命中。
        matched_by = "email" if it.get("email") else ("mobile" if it.get("mobile") else "")
        value = it.get("email", "") or it.get("mobile", "") or ""
        if not uid:
            continue
        # 只有真拿到 id 才算命中 —— 回显了号码但没有 user_id 恰恰是「查不到」,
        # 把它记成已命中会让 not_found 永远是空的。
        if value:
            matched_values.add(value)
        status = it.get("status", {}) if isinstance(it.get("status"), dict) else {}
        found.append(
            {
                "user_id": uid,
                "matched_by": matched_by,
                "matched_value": value,
                "is_resigned": bool(status.get("is_resigned")),
                "is_activated": bool(status.get("is_activated")),
                "is_frozen": bool(status.get("is_frozen")),
            }
        )
        seen_ids.append(uid)

    # 补姓名/部门: batch_get_id 只回 id, 不回姓名。拿不到就算了 (通常是缺
    # contact:contact.base:readonly), 联系方式->id 这个主要目的已经达成。
    if seen_ids and user_id_type == "open_id":
        detail = await get_users_batch_impl(",".join(seen_ids[:_BATCH_GET_ID_MAX]), user_id_type="open_id")
        if detail.get("ok"):
            by_id = {u.get("open_id", ""): u for u in detail.get("users", []) if isinstance(u, dict)}
            for entry in found:
                extra = by_id.get(entry["user_id"])
                if extra:
                    entry["name"] = extra.get("name", "")
                    entry["job_title"] = extra.get("job_title", "")
                    entry["department_ids"] = extra.get("department_ids", [])

    not_found = [v for v in (*mobile_list, *email_list) if v not in matched_values]
    result: dict[str, Any] = {
        "ok": True,
        "user_id_type": user_id_type,
        "users": found,
        "count": len(found),
        "not_found": not_found,
        "include_resigned": include_resigned,
    }
    if not_found:
        result["not_found_note"] = (
            "查不到的常见原因: 号码/邮箱本身不存在; 用了**企业邮箱**(该接口只认个人邮箱, "
            "企业邮箱一律查不到); 非中国大陆手机号没带 + 国家码; 或该用户所在部门不在应用的"
            "通讯录权限范围内。"
        )
    return result


# ── Contact — 部门树 / 部门详情 ────────────────────────────────────────────────
#
# 已有的 _child_department_ids 只取 id 且**吞掉错误** (取不到就当没有子部门), 那对
# list_department_members 是对的 —— 少一层子部门只是少几个人。但画组织架构树时同一个
# 吞法会把「43010 部门过大」变成一棵看起来完整、实际缺一大块的树, 所以这里单独走一条
# 会把错误抛出来的遍历。
#
# 另外两点:
#  - 飞书自己的 fetch_child=true 一次就能递归, 但**上限 1000 个部门**且超了报 43010。
#    这里默认逐层查 (fetch_child=false) 并自己按 max_depth 控制, 于是「部门太多」表现为
#    截断 + truncated=true, 而不是整棵树查失败。
#  - member_count 含子部门人数, primary_member_count 只含主部门在此的人 —— 两个都回,
#    因为「这个部门多少人」这个问题两种答案都有人要。
_DEPT_TREE_MAX_DEPTH = 10
_DEPT_PAGE_SIZE = 50


def _department_record(it: dict[str, Any], department_id_type: str) -> dict[str, Any]:
    """把飞书的部门对象收成稳定形状 (含主/副负责人拆分)。"""
    leaders = it.get("leaders", []) if isinstance(it.get("leaders"), list) else []
    primary = [lead.get("leaderID", "") for lead in leaders if isinstance(lead, dict) and lead.get("leaderType") == 1]
    deputy = [lead.get("leaderID", "") for lead in leaders if isinstance(lead, dict) and lead.get("leaderType") == 2]
    status = it.get("status", {}) if isinstance(it.get("status"), dict) else {}
    did = it.get("department_id", "") if department_id_type == "department_id" else it.get("open_department_id", "")
    return {
        "department_id": did,
        "open_department_id": it.get("open_department_id", ""),
        "custom_department_id": it.get("department_id", ""),
        "name": it.get("name", ""),
        "parent_department_id": it.get("parent_department_id", ""),
        "leader_user_id": it.get("leader_user_id", ""),
        "primary_leader_ids": [x for x in primary if x],
        "deputy_leader_ids": [x for x in deputy if x],
        "department_hrbps": it.get("department_hrbps", []) if isinstance(it.get("department_hrbps"), list) else [],
        "chat_id": it.get("chat_id", ""),
        "order": it.get("order", ""),
        "member_count": it.get("member_count", 0),
        "primary_member_count": it.get("primary_member_count", 0),
        "is_deleted": bool(status.get("is_deleted")),
    }


async def _child_departments(
    department_id: str, department_id_type: str, user_id_type: str
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """一个部门的直接子部门 (分页取全)。返回 (子部门列表, 错误 or None)。

    与 ``_child_department_ids`` 的区别: 这里把错误**返回给调用方**而不是当作
    「没有子部门」—— 画树时静默少一层比报错更难发现。
    """
    out: list[dict[str, Any]] = []
    page_token = ""
    while True:
        req = _build_dept_children_request(department_id, department_id_type, _DEPT_PAGE_SIZE, page_token)
        req.add_query("user_id_type", user_id_type)
        res = await _invoke(req, prefer="tenant")
        if not res["ok"]:
            return out, _with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        for it in data.get("items", []) if isinstance(data.get("items"), list) else []:
            if isinstance(it, dict):
                out.append(_department_record(it, department_id_type))
        page_token = data.get("page_token", "") or ""
        if not data.get("has_more") or not page_token:
            break
    return out, None


async def department_tree_impl(
    department_id: str = "0",
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    max_depth: int = 2,
    include_member_count: bool = True,
) -> dict[str, Any]:
    """列出一个部门下的子部门 (可多层), 返回嵌套的组织架构树。

    department_id "0" 是组织根。max_depth=1 只列直接子部门。
    """
    if max_depth < 1 or max_depth > _DEPT_TREE_MAX_DEPTH:
        return _error(f"max_depth 必须在 1 到 {_DEPT_TREE_MAX_DEPTH} 之间, 当前 {max_depth}。")

    total = 0
    truncated = False
    visited: set[str] = set()

    async def walk(did: str, depth: int) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        nonlocal total, truncated
        children, err = await _child_departments(did, department_id_type, user_id_type)
        if err is not None:
            return [], err
        nodes: list[dict[str, Any]] = []
        for child in children:
            cid = child.get("department_id", "")
            # 环形父子关系理论上不该出现, 但真出现就会无限递归。
            if cid and cid in visited:
                continue
            if cid:
                visited.add(cid)
            total += 1
            node = dict(child)
            if not include_member_count:
                node.pop("member_count", None)
                node.pop("primary_member_count", None)
            if depth < max_depth and cid:
                sub, sub_err = await walk(cid, depth + 1)
                if sub_err is not None:
                    return [], sub_err
                if sub:
                    node["children"] = sub
            elif depth >= max_depth and cid:
                # 到深度上限就不再往下走, 但明确标出「这下面可能还有」。
                truncated = True
            nodes.append(node)
        return nodes, None

    tree, error = await walk(department_id, 1)
    if error is not None:
        return error
    result: dict[str, Any] = {
        "ok": True,
        "root_department_id": department_id,
        "department_id_type": department_id_type,
        "max_depth": max_depth,
        "departments": tree,
        "count": total,
    }
    if truncated:
        result["truncated"] = True
        result["truncated_note"] = f"已到 max_depth={max_depth}, 更深的子部门未展开; 需要更深就调大 max_depth。"
    if not tree:
        result["note"] = (
            "没有子部门。若确信应该有, 检查应用的「通讯录权限范围」—— 用 tenant token 查根部门 "
            "('0') 的子部门要求范围设为「全部成员」, 否则会返回空而不是报错。"
        )
    return result


def _build_department_get_request(department_id: str, department_id_type: str, user_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/departments/:department_id"
    req.paths["department_id"] = department_id
    req.add_query("department_id_type", department_id_type)
    req.add_query("user_id_type", user_id_type)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


def _build_department_parent_request(
    department_id: str, department_id_type: str, user_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/departments/parent"
    req.add_query("department_id", department_id)
    req.add_query("department_id_type", department_id_type)
    req.add_query("user_id_type", user_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT, AccessTokenType.USER}
    return req


async def department_get_impl(
    department_id: str,
    department_id_type: str = "open_department_id",
    user_id_type: str = "open_id",
    include_children: bool = True,
    include_path: bool = True,
    user_key: str = "",
) -> dict[str, Any]:
    """一个部门的详细信息, 可带直接子部门和从根到它的路径。

    路径 (ancestors) 走 departments/parent, 飞书返回顺序是子->父, 这里翻成根->子
    再拼出 "公司/一级部门/二级部门" 的 path_text, 因为「这个部门在组织架构哪儿」
    是问部门详情时真正想知道的事。
    """
    did = (department_id or "").strip()
    if not did:
        return _error("department_id 是必填的 ('0' 表示组织根); 用 feishu_department_tree 找部门 id。")
    if did == "0":
        return _error(
            "根部门 ('0') 没有详情可查 (飞书返回 40002)。要看组织架构从根往下列, "
            "用 feishu_department_tree(department_id='0')。"
        )

    res = await _invoke(
        _build_department_get_request(did, department_id_type, user_id_type),
        user_key=user_key,
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint({**res, "department_id": did}, _CONTACT_ADMIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    raw = data.get("department") if isinstance(data.get("department"), dict) else data
    department = _department_record(raw if isinstance(raw, dict) else {}, department_id_type)

    result: dict[str, Any] = {"ok": True, "department": department}

    if include_path:
        ancestors: list[dict[str, Any]] = []
        page_token = ""
        while True:
            pres = await _invoke(
                _build_department_parent_request(did, department_id_type, user_id_type, 50, page_token),
                user_key=user_key,
                prefer="tenant",
            )
            if not pres["ok"]:
                # 拿不到路径不该让整个详情查询失败 —— 主体信息已经有了。
                result["path_error"] = _with_hint(pres, _CONTACT_ADMIN_ERROR_HINTS).get("message", "")
                break
            pdata = pres["data"] if isinstance(pres["data"], dict) else {}
            for it in pdata.get("items", []) if isinstance(pdata.get("items"), list) else []:
                if isinstance(it, dict):
                    ancestors.append(_department_record(it, department_id_type))
            page_token = pdata.get("page_token", "") or ""
            if not pdata.get("has_more") or not page_token:
                break
        if ancestors or "path_error" not in result:
            # 飞书按子->父返回且不含根部门; 翻转成根->子更像「路径」。
            ordered = list(reversed(ancestors))
            result["ancestors"] = ordered
            names = [a.get("name", "") for a in ordered if a.get("name")]
            result["path_text"] = "/".join([*names, department.get("name", "")])

    if include_children:
        children, err = await _child_departments(did, department_id_type, user_id_type)
        if err is not None:
            result["children_error"] = err.get("message", "")
        else:
            result["children"] = children
            result["children_count"] = len(children)

    return result


# ── Contact — 用户写操作 (创建 / 修改 / 离职) ───────────────────────────────────
#
# 三件事都只吃 tenant token (scope contact:contact), 所以 prefer="tenant": 通讯录条目
# 没有「归谁」的问题, 走 prefer="user" 会问一个答不上来的归属问题。
#
# 离职 (DELETE) 是这批里唯一不可逆的动作, 且飞书的默认行为有隐藏后果: 不传转交人时
# 文档/日历/问卷等资源默认转交给直属上级, 而**没有上级时日历和问卷是直接删掉的**。
# 所以离职要求显式 confirm, 同 feishu_chat_dismiss 的护栏 —— 让「把张三清一下」这种
# 模糊指令不足以让人离职。
_RESIGN_CONFIRM = "离职用户"
_EMPLOYEE_TYPES = {1: "正式", 2: "实习", 3: "外包", 4: "劳务", 5: "顾问"}


def _build_user_create_request(body: dict[str, Any], user_id_type: str, department_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/contact/v3/users"
    req.add_query("user_id_type", user_id_type)
    req.add_query("department_id_type", department_id_type)
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_user_patch_request(
    user_id: str, body: dict[str, Any], user_id_type: str, department_id_type: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/contact/v3/users/:user_id"
    req.paths["user_id"] = user_id
    req.add_query("user_id_type", user_id_type)
    req.add_query("department_id_type", department_id_type)
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_user_delete_request(user_id: str, body: dict[str, Any], user_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/contact/v3/users/:user_id"
    req.paths["user_id"] = user_id
    req.add_query("user_id_type", user_id_type)
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _user_summary(raw: Any) -> dict[str, Any]:
    """建/改用户后飞书回的 user 对象, 收成和 feishu_user_get 一致的形状。"""
    it = raw if isinstance(raw, dict) else {}
    status = it.get("status", {}) if isinstance(it.get("status"), dict) else {}
    return {
        "open_id": it.get("open_id", ""),
        "user_id": it.get("user_id", ""),
        "union_id": it.get("union_id", ""),
        "name": it.get("name", ""),
        "mobile": it.get("mobile", ""),
        "email": it.get("email", ""),
        "enterprise_email": it.get("enterprise_email", ""),
        "job_title": it.get("job_title", ""),
        "employee_type": it.get("employee_type", 0),
        "employee_no": it.get("employee_no", ""),
        "department_ids": it.get("department_ids", []),
        "leader_user_id": it.get("leader_user_id", ""),
        "is_resigned": bool(status.get("is_resigned")),
        "is_activated": bool(status.get("is_activated")),
    }


def _optional_user_fields(
    name: str,
    mobile: str,
    email: str,
    department_ids: str,
    employee_type: int,
    leader_user_id: str,
    job_title: str,
    employee_no: str,
    en_name: str,
    nickname: str,
    gender: int,
    city: str,
    work_station: str,
    enterprise_email: str,
) -> dict[str, Any]:
    """只把真正给了值的字段放进 body —— PATCH 的语义是「没传的不改」,
    所以塞一个空字符串会把原值**清掉**, 那是最容易犯的破坏性错误。"""
    body: dict[str, Any] = {}
    for key, value in (
        ("name", name),
        ("mobile", mobile),
        ("email", email),
        ("leader_user_id", leader_user_id),
        ("job_title", job_title),
        ("employee_no", employee_no),
        ("en_name", en_name),
        ("nickname", nickname),
        ("city", city),
        ("work_station", work_station),
        ("enterprise_email", enterprise_email),
    ):
        if value and value.strip():
            body[key] = value.strip()
    depts = _split_contacts(department_ids)
    if depts:
        body["department_ids"] = depts
    if employee_type:
        body["employee_type"] = employee_type
    if gender:
        body["gender"] = gender
    return body


async def user_create_impl(
    name: str,
    mobile: str,
    department_ids: str,
    employee_type: int = 1,
    email: str = "",
    leader_user_id: str = "",
    job_title: str = "",
    employee_no: str = "",
    en_name: str = "",
    nickname: str = "",
    gender: int = 0,
    city: str = "",
    work_station: str = "",
    enterprise_email: str = "",
    user_id_type: str = "open_id",
    department_id_type: str = "open_department_id",
) -> dict[str, Any]:
    """在通讯录里创建一个用户。name/mobile/department_ids/employee_type 是飞书的必填项。"""
    if not (name or "").strip():
        return _error("name 是必填的。")
    if not (mobile or "").strip():
        return _error("mobile 是必填的, 且租户内唯一; 非中国大陆号码要带 + 国家码 (如 +8190...)。")
    depts = _split_contacts(department_ids)
    if not depts:
        return _error(
            "department_ids 是必填的 (逗号分隔, 最多 50 个); 用 feishu_department_tree 找部门 id。"
            "根部门 '0' 不能作为用户部门。"
        )
    if len(depts) > 50:
        return _error(f"department_ids 最多 50 个, 当前 {len(depts)} 个。")
    if employee_type not in _EMPLOYEE_TYPES:
        allowed = ", ".join(f"{k}={v}" for k, v in _EMPLOYEE_TYPES.items())
        return _error(f"employee_type 应为 {allowed}; 自定义人员类型请传后台配置的枚举号 (当前传了 {employee_type})。")

    body = _optional_user_fields(
        name,
        mobile,
        email,
        department_ids,
        employee_type,
        leader_user_id,
        job_title,
        employee_no,
        en_name,
        nickname,
        gender,
        city,
        work_station,
        enterprise_email,
    )
    res = await _invoke(
        _build_user_create_request(body, user_id_type, department_id_type),
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {"ok": True, "created": True, "user": _user_summary(data.get("user"))}


async def user_update_impl(
    user_id: str,
    name: str = "",
    mobile: str = "",
    email: str = "",
    department_ids: str = "",
    employee_type: int = 0,
    leader_user_id: str = "",
    job_title: str = "",
    employee_no: str = "",
    en_name: str = "",
    nickname: str = "",
    gender: int = 0,
    city: str = "",
    work_station: str = "",
    enterprise_email: str = "",
    user_id_type: str = "open_id",
    department_id_type: str = "open_department_id",
) -> dict[str, Any]:
    """修改用户的部分信息 —— 只更新真正传了的字段, 没传的保持原样。"""
    uid = (user_id or "").strip()
    if not uid:
        return _error("user_id 是必填的; 用 feishu_contact_find 或 feishu_contact_search 解析到 id。")
    body = _optional_user_fields(
        name,
        mobile,
        email,
        department_ids,
        employee_type,
        leader_user_id,
        job_title,
        employee_no,
        en_name,
        nickname,
        gender,
        city,
        work_station,
        enterprise_email,
    )
    if not body:
        return _error(
            "没有要改的字段。至少给一个 (name/mobile/email/department_ids/employee_type/"
            "leader_user_id/job_title/employee_no/en_name/nickname/gender/city/work_station/"
            "enterprise_email)。注意 department_ids 是**整体替换**而不是追加。"
        )
    res = await _invoke(
        _build_user_patch_request(uid, body, user_id_type, department_id_type),
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint({**res, "user_id": uid}, _CONTACT_ADMIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    return {
        "ok": True,
        "updated": True,
        "updated_fields": sorted(body),
        "user": _user_summary(data.get("user")),
    }


async def user_resign_impl(
    user_id: str,
    confirm: str = "",
    docs_acceptor_user_id: str = "",
    calendar_acceptor_user_id: str = "",
    application_acceptor_user_id: str = "",
    department_chat_acceptor_user_id: str = "",
    external_chat_acceptor_user_id: str = "",
    email_processing_type: str = "",
    email_acceptor_user_id: str = "",
    user_id_type: str = "open_id",
) -> dict[str, Any]:
    """把用户设为离职 (飞书的 DELETE 用户) —— 不可逆, 需显式 confirm。"""
    uid = (user_id or "").strip()
    if not uid:
        return _error("user_id 是必填的; 先用 feishu_contact_find 按手机号/邮箱确认是本人。")
    if (confirm or "").strip() != _RESIGN_CONFIRM:
        return _error(
            f"让用户离职会移除其账号访问权限并转交其名下资源, 不可逆。确认要办离职请传 "
            f"confirm='{_RESIGN_CONFIRM}'。先用 feishu_user_get 核对这是不是要办的那个人。\n"
            "注意资源默认转交给**直属上级**; 若该用户没有上级, 文档/妙记/应用会留在他名下, "
            "而**日历和问卷会被直接删除** —— 想保住就显式传对应的 acceptor。",
            need_confirmation=True,
            user_id=uid,
        )

    body: dict[str, Any] = {}
    for key, value in (
        ("docs_acceptor_user_id", docs_acceptor_user_id),
        ("calendar_acceptor_user_id", calendar_acceptor_user_id),
        ("application_acceptor_user_id", application_acceptor_user_id),
        ("department_chat_acceptor_user_id", department_chat_acceptor_user_id),
        ("external_chat_acceptor_user_id", external_chat_acceptor_user_id),
    ):
        if value and value.strip():
            body[key] = value.strip()
    etype = (email_processing_type or "").strip()
    if etype:
        if etype not in {"1", "2", "3"}:
            return _error("email_processing_type 只能是 '1' 转交 / '2' 保留 / '3' 删除。")
        acceptor: dict[str, Any] = {"processing_type": etype}
        if etype == "1":
            if not (email_acceptor_user_id or "").strip():
                return _error("email_processing_type='1' (转交) 时必须给 email_acceptor_user_id。")
            acceptor["acceptor_user_id"] = email_acceptor_user_id.strip()
        body["email_acceptor"] = acceptor

    res = await _invoke(_build_user_delete_request(uid, body, user_id_type), prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, "user_id": uid}, _CONTACT_ADMIN_ERROR_HINTS)
    return {
        "ok": True,
        "resigned": True,
        "user_id": uid,
        "acceptors": body,
        "note": "已办离职。用 feishu_user_get 查该用户的 status 可确认; 误操作可在管理后台恢复离职成员。",
    }


# ── Contact — 部门写操作 (创建 / 修改 / 删除) ───────────────────────────────────
#
# 删部门要求显式 confirm, 同离职。飞书这边还有个前置条件值得提前说清: 部门必须**空**
# 才能删 —— 有人报 43011, 有子部门报 43012。所以删一棵子树只能从最深一层往上删, 这跟
# 文档块删除那个「必须从大序号往小删」是同一类顺序陷阱。
#
# 改部门有两个静默破坏点, 都在 hint 和文档里点出来:
#  - leaders / department_hrbps 传**空数组**是「清空」而不是「不改」;
#  - leader_user_id 与主负责人 (leaderType=1) 联动, 改一个另一个跟着变。
# 这里的做法是: 没传就完全不放进 body, 于是「不改」永远不会被误解成「清空」。
_DEPARTMENT_DELETE_CONFIRM = "删除部门"


def _build_department_create_request(body: dict[str, Any], user_id_type: str, department_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/contact/v3/departments"
    req.add_query("user_id_type", user_id_type)
    req.add_query("department_id_type", department_id_type)
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_department_patch_request(
    department_id: str, body: dict[str, Any], user_id_type: str, department_id_type: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/contact/v3/departments/:department_id"
    req.paths["department_id"] = department_id
    req.add_query("user_id_type", user_id_type)
    req.add_query("department_id_type", department_id_type)
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_department_delete_request(department_id: str, department_id_type: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/contact/v3/departments/:department_id"
    req.paths["department_id"] = department_id
    req.add_query("department_id_type", department_id_type)
    req.token_types = {AccessTokenType.TENANT}
    return req


async def department_create_impl(
    name: str,
    parent_department_id: str,
    leader_user_id: str = "",
    custom_department_id: str = "",
    order: str = "",
    create_group_chat: bool = False,
    user_id_type: str = "open_id",
    department_id_type: str = "open_department_id",
) -> dict[str, Any]:
    """创建一个部门。name 和 parent_department_id 是飞书的必填项 ('0' 表示挂在根下)。"""
    dept_name = (name or "").strip()
    if not dept_name:
        return _error("name 是必填的。")
    if "/" in dept_name:
        return _error("部门名不能含斜杠 '/' (飞书会报 43029)。")
    parent = (parent_department_id or "").strip()
    if not parent:
        return _error("parent_department_id 是必填的; 挂在组织根下传 '0', 或用 feishu_department_tree 找父部门 id。")

    body: dict[str, Any] = {"name": dept_name, "parent_department_id": parent}
    if leader_user_id.strip():
        body["leader_user_id"] = leader_user_id.strip()
    if order.strip():
        body["order"] = order.strip()
    if create_group_chat:
        body["create_group_chat"] = True
    cid = (custom_department_id or "").strip()
    if cid:
        # 飞书拒绝 od- 前缀 (那是系统生成的 open_department_id 的形状) 和 '0'/'1'。
        if cid.startswith("od-") or cid in {"0", "1"}:
            return _error("custom_department_id 不能以 'od-' 开头, 也不能是 '0' 或 '1' (飞书保留)。")
        body["department_id"] = cid

    res = await _invoke(
        _build_department_create_request(body, user_id_type, department_id_type),
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    raw = data.get("department") if isinstance(data.get("department"), dict) else data
    return {
        "ok": True,
        "created": True,
        "department": _department_record(raw if isinstance(raw, dict) else {}, department_id_type),
    }


async def department_update_impl(
    department_id: str,
    name: str = "",
    parent_department_id: str = "",
    leader_user_id: str = "",
    order: str = "",
    user_id_type: str = "open_id",
    department_id_type: str = "open_department_id",
) -> dict[str, Any]:
    """修改部门的部分信息 —— 只更新真正传了的字段。传 parent_department_id 即移动部门。"""
    did = (department_id or "").strip()
    if not did:
        return _error("department_id 是必填的; 用 feishu_department_tree 找部门 id。")
    if did == "0":
        return _error("根部门 ('0') 不能修改 (飞书返回 40002)。")

    body: dict[str, Any] = {}
    dept_name = (name or "").strip()
    if dept_name:
        if "/" in dept_name:
            return _error("部门名不能含斜杠 '/' (飞书会报 43029)。")
        body["name"] = dept_name
    for key, value in (
        ("parent_department_id", parent_department_id),
        ("leader_user_id", leader_user_id),
        ("order", order),
    ):
        if value and value.strip():
            body[key] = value.strip()
    if not body:
        return _error("没有要改的字段。至少给一个 (name / parent_department_id / leader_user_id / order)。")

    res = await _invoke(
        _build_department_patch_request(did, body, user_id_type, department_id_type),
        prefer="tenant",
    )
    if not res["ok"]:
        return _with_hint({**res, "department_id": did}, _CONTACT_ADMIN_ERROR_HINTS)
    data = res["data"] if isinstance(res["data"], dict) else {}
    raw = data.get("department") if isinstance(data.get("department"), dict) else data
    return {
        "ok": True,
        "updated": True,
        "updated_fields": sorted(body),
        "department": _department_record(raw if isinstance(raw, dict) else {}, department_id_type),
    }


async def department_delete_impl(
    department_id: str,
    confirm: str = "",
    department_id_type: str = "open_department_id",
) -> dict[str, Any]:
    """删除一个部门 —— 需显式 confirm; 部门必须先清空 (无用户、无子部门)。"""
    did = (department_id or "").strip()
    if not did:
        return _error("department_id 是必填的; 用 feishu_department_tree 找部门 id。")
    if did == "0":
        return _error("根部门 ('0') 不能删除 (飞书返回 40002)。")
    if (confirm or "").strip() != _DEPARTMENT_DELETE_CONFIRM:
        return _error(
            f"删除部门不可逆。确认要删请传 confirm='{_DEPARTMENT_DELETE_CONFIRM}'。"
            "先用 feishu_department_get 核对这是不是要删的那个部门 (看 path_text 和 member_count)。\n"
            "另外飞书要求部门**先清空**: 里面还有人报 43011, 还有子部门报 43012 —— "
            "删一棵子树要从最深一层往上删。",
            need_confirmation=True,
            department_id=did,
        )

    res = await _invoke(_build_department_delete_request(did, department_id_type), prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, "department_id": did}, _CONTACT_ADMIN_ERROR_HINTS)
    return {"ok": True, "deleted": True, "department_id": did}


# ── Contact — 用户组 (group) ────────────────────────────────────────────────────
#
# 用户组是「一批人」的命名集合, 可以被云文档权限、审批流等直接引用, 所以它和群聊
# (chat) 完全不是一回事 —— 用户组不能发消息, 群聊不能当权限主体。
#
# 端点分布得很不规整, 这也是做成 Python 工具的理由:
#   建组   POST   /contact/v3/group          (不是 /groups, 没有复数)
#   列表   GET    /contact/v3/group/simplelist
#   详情   GET    /contact/v3/group/:group_id
#   改/删  PATCH/DELETE /contact/v3/group/:group_id
#   成员   POST   /contact/v3/group/:group_id/member/add | remove
#   成员表 GET    /contact/v3/group/:group_id/member/simplelist
# 另外建组要求应用通讯录范围 = 全部成员 (否则 42010), 而它只在建组这一个动作上要求。
_GROUP_DELETE_CONFIRM = "删除用户组"
_GROUP_TYPES = {1: "普通用户组", 2: "动态用户组"}


def _build_group_create_request(body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = "/open-apis/contact/v3/group"
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_group_list_request(group_type: int, page_size: int, page_token: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/group/simplelist"
    req.add_query("type", group_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_group_get_request(group_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/group/:group_id"
    req.paths["group_id"] = group_id
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_group_patch_request(group_id: str, body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.PATCH
    req.uri = "/open-apis/contact/v3/group/:group_id"
    req.paths["group_id"] = group_id
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_group_delete_request(group_id: str) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.DELETE
    req.uri = "/open-apis/contact/v3/group/:group_id"
    req.paths["group_id"] = group_id
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_group_member_request(group_id: str, action: str, body: dict[str, Any]) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.POST
    req.uri = f"/open-apis/contact/v3/group/:group_id/member/{action}"
    req.paths["group_id"] = group_id
    req.body = body
    req.token_types = {AccessTokenType.TENANT}
    return req


def _build_group_member_list_request(
    group_id: str, member_type: str, member_id_type: str, page_size: int, page_token: str
) -> BaseRequest:
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/contact/v3/group/:group_id/member/simplelist"
    req.paths["group_id"] = group_id
    req.add_query("member_type", member_type)
    req.add_query("member_id_type", member_id_type)
    req.add_query("page_size", page_size)
    if page_token:
        req.add_query("page_token", page_token)
    req.token_types = {AccessTokenType.TENANT}
    return req


def _group_record(it: Any) -> dict[str, Any]:
    raw = it if isinstance(it, dict) else {}
    gtype = raw.get("type", 1)
    return {
        "group_id": raw.get("id", "") or raw.get("group_id", ""),
        "name": raw.get("name", ""),
        "description": raw.get("description", ""),
        "type": gtype,
        "type_text": _GROUP_TYPES.get(gtype, str(gtype)),
        "member_user_count": raw.get("member_user_count", 0),
        "member_department_count": raw.get("member_department_count", 0),
    }


async def user_group_manage_impl(
    action: str,
    group_id: str = "",
    name: str = "",
    description: str = "",
    custom_group_id: str = "",
    group_type: int = 1,
    confirm: str = "",
    page_size: int = 50,
    page_token: str = "",
) -> dict[str, Any]:
    """用户组的增删改查: action = create | list | get | update | delete。"""
    act = (action or "").strip().lower()
    valid = ("create", "list", "get", "update", "delete")
    if act not in valid:
        return _error(f"action 只能是 {', '.join(valid)} (当前 '{action}')。")

    if act == "create":
        gname = (name or "").strip()
        if not gname:
            return _error("建用户组必须给 name (租户内唯一, 最长 100 字)。")
        body: dict[str, Any] = {"name": gname, "type": group_type}
        if description.strip():
            body["description"] = description.strip()
        cgid = (custom_group_id or "").strip()
        if cgid:
            body["group_id"] = cgid
        res = await _invoke(_build_group_create_request(body), prefer="tenant")
        if not res["ok"]:
            return _with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        return {
            "ok": True,
            "created": True,
            "group_id": data.get("group_id", "") or cgid,
            "name": gname,
            "note": "动态用户组不能通过 API 创建, 只能在管理后台建; 这里建出来的是普通用户组。",
        }

    if act == "list":
        if page_size < 1 or page_size > 100:
            return _error("page_size 必须在 1 到 100 之间。")
        res = await _invoke(_build_group_list_request(group_type, page_size, page_token), prefer="tenant")
        if not res["ok"]:
            return _with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        raw_list = data.get("grouplist", []) if isinstance(data.get("grouplist"), list) else []
        groups = [_group_record(g) for g in raw_list]
        return {
            "ok": True,
            "type": group_type,
            "type_text": _GROUP_TYPES.get(group_type, str(group_type)),
            "groups": groups,
            "count": len(groups),
            "has_more": bool(data.get("has_more")),
            "page_token": data.get("page_token", ""),
        }

    gid = (group_id or "").strip()
    if not gid:
        return _error(f"action='{act}' 需要 group_id; 用 action='list' 取用户组列表。")

    if act == "get":
        res = await _invoke(_build_group_get_request(gid), prefer="tenant")
        if not res["ok"]:
            return _with_hint({**res, "group_id": gid}, _CONTACT_ADMIN_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        raw = data.get("group") if isinstance(data.get("group"), dict) else data
        group = _group_record(raw)
        # 详情接口不回 id, 用调用方给的补上, 免得返回一个 group_id 为空的对象。
        group["group_id"] = group["group_id"] or gid
        return {"ok": True, "group": group}

    if act == "update":
        body = {}
        if name.strip():
            body["name"] = name.strip()
        if description.strip():
            body["description"] = description.strip()
        if not body:
            return _error("改用户组至少要给 name 或 description。")
        res = await _invoke(_build_group_patch_request(gid, body), prefer="tenant")
        if not res["ok"]:
            return _with_hint({**res, "group_id": gid}, _CONTACT_ADMIN_ERROR_HINTS)
        return {"ok": True, "updated": True, "group_id": gid, "updated_fields": sorted(body)}

    # act == "delete"
    if (confirm or "").strip() != _GROUP_DELETE_CONFIRM:
        return _error(
            f"删除用户组不可逆, 且引用了它的云文档权限/审批流会随之失去这个主体。"
            f"确认要删请传 confirm='{_GROUP_DELETE_CONFIRM}'。"
            "先用 action='get' 核对名字和成员数。",
            need_confirmation=True,
            group_id=gid,
        )
    res = await _invoke(_build_group_delete_request(gid), prefer="tenant")
    if not res["ok"]:
        return _with_hint({**res, "group_id": gid}, _CONTACT_ADMIN_ERROR_HINTS)
    return {"ok": True, "deleted": True, "group_id": gid}


async def user_group_members_impl(
    group_id: str,
    action: str = "list",
    user_ids: str = "",
    member_id_type: str = "open_id",
    member_type: str = "user",
    page_size: int = 50,
    page_token: str = "",
) -> dict[str, Any]:
    """用户组成员: action = list | add | remove。

    add/remove 逐个调用飞书的单成员接口 (它一次只收一个), 并把每个人的结果分开回报,
    这样 10 个人里 1 个失败不会让另外 9 个的结果无从判断。
    """
    gid = (group_id or "").strip()
    if not gid:
        return _error("group_id 是必填的; 用 feishu_user_group(action='list') 取。")
    act = (action or "").strip().lower()
    if act not in {"list", "add", "remove"}:
        return _error(f"action 只能是 list, add, remove (当前 '{action}')。")

    if act == "list":
        if member_type not in {"user", "department"}:
            return _error("member_type 只能是 'user' 或 'department'。")
        if page_size < 1 or page_size > 100:
            return _error("page_size 必须在 1 到 100 之间。")
        res = await _invoke(
            _build_group_member_list_request(gid, member_type, member_id_type, page_size, page_token),
            prefer="tenant",
        )
        if not res["ok"]:
            return _with_hint({**res, "group_id": gid}, _CONTACT_ADMIN_ERROR_HINTS)
        data = res["data"] if isinstance(res["data"], dict) else {}
        raw_list = data.get("memberlist", []) if isinstance(data.get("memberlist"), list) else []
        members = [
            {"member_id": m.get("member_id", ""), "member_type": m.get("member_type", "")}
            for m in raw_list
            if isinstance(m, dict)
        ]
        return {
            "ok": True,
            "group_id": gid,
            "member_type": member_type,
            "members": members,
            "count": len(members),
            "has_more": bool(data.get("has_more")),
            "page_token": data.get("page_token", ""),
            "note": "一次只返回一类成员; 要看部门成员再用 member_type='department' 调一次。",
        }

    # add / remove —— 飞书只支持 user 类型成员, 且一次一个。
    if member_type != "user":
        return _error("增删用户组成员目前只支持 member_type='user' (飞书暂不支持部门类型)。")
    ids = _split_contacts(user_ids)
    if not ids:
        return _error("user_ids 是必填的 (逗号分隔)。")

    endpoint = "add" if act == "add" else "remove"
    succeeded: list[str] = []
    failed: list[dict[str, Any]] = []
    for uid in ids:
        body = {"member_type": "user", "member_id_type": member_id_type, "member_id": uid}
        res = await _invoke(_build_group_member_request(gid, endpoint, body), prefer="tenant")
        if res["ok"]:
            succeeded.append(uid)
        else:
            hinted = _with_hint(res, _CONTACT_ADMIN_ERROR_HINTS)
            # hint 才是「这人为什么加不进去」那句话 (42005 已在组内 / 42006 已离职),
            # 只留 message 会退化成一句看不出所以然的 "Feishu API error 42006"。
            failed.append(
                {
                    "member_id": uid,
                    "code": res.get("code"),
                    "message": hinted.get("hint") or hinted.get("message", ""),
                }
            )

    result: dict[str, Any] = {
        "ok": not failed,
        "group_id": gid,
        "action": act,
        "member_id_type": member_id_type,
        "succeeded": succeeded,
        "succeeded_count": len(succeeded),
        "failed": failed,
    }
    if failed and succeeded:
        result["partial"] = True
    if failed:
        result["message"] = f"{len(succeeded)} 个成功, {len(failed)} 个失败; 看 failed 里每个人的原因。"
    return result
