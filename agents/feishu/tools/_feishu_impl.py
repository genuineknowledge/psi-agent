"""Private helper for the Feishu tools — authenticated client + request execution.

Wraps the ``lark_channel`` SDK (already a project dependency): builds one
authenticated ``Client`` from ``PSI_FEISHU_APP_ID`` / ``PSI_FEISHU_APP_SECRET``,
caches it module-level, and runs ``BaseRequest`` objects through the SDK's native
async ``arequest``. Drive-comment requests reuse the SDK's ready-made builders;
docx/doc/sheet raw-content and create-reply requests are hand-built the same way
the SDK's own ``api/drive/comment.py`` does it.

**What lives where.** This file is the shared layer every Feishu domain needs — the
client, the tenant/user token dance (``_invoke``), rate-limit retries, capability
inference, and the UAT/identity stores. The per-domain implementations were split
out to ``_feishu/{message,doc,sheet,bitable,contact,approval,auth,calendar,drive,
attendance,task}.py`` and are re-exported at the bottom of this file, so importing
this module still reaches every name it always did and each domain can be edited,
reviewed, and tested on its own.

Three rules keep that split honest. A domain module reaches back here as ``_core``
rather than copying anything down, so patching ``_feishu_impl._invoke`` (or
``_get_client``, ``_get_valid_uat``, ...) still takes effect everywhere. A domain
that needs another domain's helper goes through ``_core`` too, never by importing
its sibling — one namespace stays authoritative for every name. And anything that
rebinds a module-level variable (``global _client``, ``global _token_store``) stays
in the same file as that variable: ``global`` binds in its own module, so moving
such a function would reset a fresh name of its own and leave the real cache alone.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import pathlib
import random
import re  # noqa: F401  (re-exported: callers reach it as _f.<name>)
from typing import Any

import _feishu_auth_watch as _auth_watch  # noqa: F401  (re-exported: callers reach it as _f.<name>)
import _oauth_receiver as _oauth_rx  # noqa: F401  (re-exported: callers reach it as _f.<name>)
import _runtime_paths as _paths
import anyio
from lark_channel.api.drive import comment as _comment  # noqa: F401  (re-exported: callers reach it as _f.<name>)
from lark_channel.core.enum import (  # noqa: F401  (re-exported: callers reach it as _f.<name>)
    AccessTokenType,
    HttpMethod,
)
from lark_channel.core.model import BaseRequest  # noqa: F401  (re-exported: callers reach it as _f.<name>)
from loguru import logger  # noqa: F401  (re-exported: callers reach it as _f.<name>)

from psi_agent.channel.feishu._card_store import (
    save_card_snapshot,  # noqa: F401  (re-exported: callers reach it as _f.<name>)
)
from psi_agent.session.runtime_context import get_session_id  # noqa: F401  (re-exported: callers reach it as _f.<name>)

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
    "它把等待放到后台, 那一轮同样立刻收尾 (**别播报等待状态**), 码到了后台自己换 token, "
    "然后自动起一轮把原来那件事做完并回话.\n"
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
    retry_rate_limits: bool = True,
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

    if retry_rate_limits:
        return await _retrying_rate_limits(send)
    return await send()


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
    # Mindnote (思维笔记) reading only works with a user-level token; the tenant
    # token alone gets 404. This capability backs feishu_worktree_read.
    "mindnote_read": ("mindnote:node:read",),
}


def scope_catalog_keys() -> list[str]:
    """The capability keys a caller may ask to be authorized for."""
    return sorted(_SCOPE_CATALOG)


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


_REFRESH_URL = "https://open.feishu.cn/open-apis/authen/v1/refresh_access_token"
_APP_TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/app_access_token/internal"


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


# ── Domain implementations ──────────────────────────────────────────────────
#
# Everything below this line lives in ``_feishu/<domain>.py``; the imports exist so
# that ``import _feishu_impl as _f`` keeps reaching every name it always did, and so
# that tests can go on patching them on this module. Placed at the bottom because
# those modules import this one back (for the client/token layer above) — by the time
# they run, every name they need is already defined.

from _feishu.approval import (  # noqa: E402,F401
    _build_approval_definition_request,
    _build_create_instance_request,
    _build_instance_get_request,
    _parse_approval_attachments,
    _parse_approval_form_schema,
    create_approval_instance_impl,
    get_approval_definition_impl,
    get_approval_instance_impl,
)
from _feishu.attendance import (  # noqa: E402,F401
    _build_user_tasks_query_request,
    _fmt_check_time,
    query_attendance_impl,
)
from _feishu.auth import (  # noqa: E402,F401
    _AUTH_CARD_ACTION,
    _AUTH_CARD_HANDLER,
    _AUTH_CARD_RETRY_NOTE,
    _AUTHORIZE_URL,
    _CHECK_TIMEOUT_SECONDS,
    _DEFAULT_CAPABILITIES,
    _OFFLINE_SCOPE,
    _TIER_LABEL,
    _TOKEN_URL,
    TIER_CARD,
    TIER_LINK,
    TIER_MANUAL,
    _auth_card_content,
    _auth_error_hint,
    _explicit_redirect_uri,
    _extract_code,
    _new_pkce_pair,
    _notify_auth_outcome,
    _parse_capabilities,
    _pending_auth_path,
    _pending_capabilities,
    _read_pending,
    _receive_code,
    _record_granted_capabilities,
    _scope_string,
    _write_json_map,
    auth_card_impl,
    auth_check_impl,
    auth_collect_impl,
    auth_complete_impl,
    auth_request_impl,
    auth_start_impl,
    identity_get_impl,
    identity_set_impl,
    set_identity,
)
from _feishu.bitable import (  # noqa: E402,F401
    _BITABLE_FIELD_TYPES,
    _INDEX_FIELD_TYPES,
    _SEARCH_OPERATORS,
    _UNCREATABLE_FIELD_TYPE,
    _as_field_map,
    _build_batch_create_records_request,
    _build_batch_delete_records_request,
    _build_batch_update_records_request,
    _build_create_table_request,
    _build_list_fields_request,
    _build_list_records_request,
    _build_search_records_request,
    _build_update_field_request,
    _build_update_record_request,
    _check_bitable_columns,
    _dropped_fields,
    _parse_resp_body,
    _parse_search_filter,
    _validate_bitable_fields,
    clear_bitable_table_impl,
    create_bitable_records_impl,
    create_bitable_table_impl,
    list_bitable_fields_impl,
    search_bitable_records_impl,
    update_bitable_field_impl,
    update_bitable_record_impl,
    update_bitable_records_impl,
)
from _feishu.contact import (  # noqa: E402,F401
    _BATCH_GET_ID_MAX,
    _CONTACT_ADMIN_ERROR_HINTS,
    _DEPT_PAGE_SIZE,
    _DEPT_TREE_MAX_DEPTH,
    _build_batch_get_id_request,
    _build_batch_users_request,
    _build_department_get_request,
    _build_department_parent_request,
    _build_dept_children_request,
    _build_find_by_department_request,
    _build_group_member_list_request,
    _build_group_member_request,
    _child_department_ids,
    _child_departments,
    _department_record,
    _members_of_department,
    _split_contacts,
    department_get_impl,
    department_tree_impl,
    find_users_by_contact_impl,
    get_users_batch_impl,
    list_department_members_impl,
    user_group_members_impl,
)
from _feishu.doc import (  # noqa: E402,F401
    _BITABLE_BLOCK_TYPE,
    _BITABLE_DEFAULT_VIEW,
    _BLOCK_TYPE_NAMES,
    _BLOCKS_BATCH,
    _BLOCKS_LIST_PAGE_MAX,
    _DOC_BASE_URL,
    _FILE_BLOCK_TYPE,
    _HEADING_KEYS,
    _IMAGE_BLOCK_TYPE,
    _SHEET_BLOCK_CREATE_MAX,
    _SHEET_BLOCK_TYPE,
    _TABLE_BLOCK_TYPE,
    _TABLE_CELL_BLOCK_TYPE,
    _TEXTUAL_BLOCK_KEYS,
    _UPLOAD_ALL_MAX_BYTES,
    _append_table_descendants,
    _block_plain_text,
    _build_block_children_list_request,
    _build_block_delete_request,
    _build_block_text_patch_request,
    _build_blocks_append_request,
    _build_blocks_batch_delete_request,
    _build_descendant_request,
    _build_doc_raw_request,
    _build_doc_search_request,
    _build_document_blocks_list_request,
    _build_docx_create_request,
    _build_docx_raw_request,
    _build_file_block_create_request,
    _build_file_block_patch_request,
    _build_image_block_create_request,
    _build_image_block_patch_request,
    _build_list_spaces_request,
    _build_list_wiki_nodes_request,
    _build_media_upload_all_request,
    _build_reply_create_request,
    _build_wiki_node_create_request,
    _build_wiki_space_create_request,
    _column_letter,
    _content_to_blocks,
    _discard_image_block,
    _embedded_block_coordinates,
    _embedded_block_token,
    _embedded_sheet_result,
    _first_child,
    _line_to_block,
    _locate_child_index,
    _NamedBytes,
    _parse_rows,
    _raw_get,
    _resolve_table_caption,
    _table_descendants,
    _text_block,
    _upload_into_file_block,
    _upload_into_image_block,
    add_comment_impl,
    append_doc_bitable_impl,
    append_doc_content_impl,
    append_doc_file_impl,
    append_doc_flowchart_impl,
    append_doc_image_impl,
    append_doc_sheet_impl,
    append_doc_swimlane_impl,
    append_doc_table_impl,
    build_descendant_request,
    create_docx_impl,
    create_wiki_doc_with_content_impl,
    create_wiki_node_impl,
    create_wiki_space_impl,
    delete_doc_blocks_impl,
    invoke_request,
    list_all_blocks,
    list_doc_blocks_impl,
    list_wiki_nodes_impl,
    list_wiki_spaces_impl,
    read_doc_for_captions,
    read_doc_impl,
    real_block_id,
    reply_comment_impl,
    search_docs_impl,
    split_embedded_sheet_token,
    update_doc_block_impl,
    upload_media_impl,
)
from _feishu.drive import (  # noqa: E402,F401
    _DOWNLOAD_URIS,
    _EXPORT_FATAL,
    _EXPORT_FORMATS,
    _EXPORT_POLL_DELAYS,
    _await_export_file_token,
    _build_export_create_request,
    _build_export_download_request,
    _build_export_query_request,
    _build_media_download_request,
    _build_message_resource_request,
    _download_export_bytes,
    _download_media_as_tenant,
    _download_media_as_user,
    _download_media_bytes,
    _download_msg_resource_as_tenant,
    _download_msg_resource_as_user,
    _download_msg_resource_bytes,
    _download_url_bytes,
    _export_format_error,
    _media_resp_to_bytes,
    download_file_impl,
    export_doc_impl,
    get_message_image_impl,
)
from _feishu.leave import (  # noqa: E402,F401
    _APPROVED,
    _DATE_FORMATS,
    _MAX_INSTANCES,
    _MAX_SPAN_DAYS,
    _build_list_instances_request,
    _epoch_ms,
    _leave_span,
    _list_instance_codes,
    _overlap_days,
    _parse_date,
    _range_bounds,
    _wanted_names,
    _widgets,
    query_leave_impl,
)
from _feishu.mentor_ledger import (  # noqa: E402,F401
    _LEDGER_SCHEMA_FIELDS,
    _build_list_tables_request,
    mentor_ledger_ensure_impl,
)
from _feishu.message import (  # noqa: E402,F401
    _ANNOUNCEMENT_ERROR_HINTS,
    _AT_TAG_RE,
    _CARD_EDIT_ERROR_HINTS,
    _CHAT_ADMIN_ERROR_HINTS,
    _CHAT_SETTING_FIELDS,
    _CHAT_UPDATE_ERROR_HINTS,
    _CHAT_WHO,
    _EDIT_ERROR_HINTS,
    _EMOJI_ALIASES,
    _EMOJI_CANONICAL,
    _FILE_TYPE_BY_SUFFIX,
    _FILE_TYPES,
    _FILE_UPLOAD_MAX_BYTES,
    _FORWARD_ERROR_HINTS,
    _IMAGE_SUFFIXES,
    _IMAGE_UPLOAD_MAX_BYTES,
    _MEDIA_MSG_TYPES,
    _MENU_ERROR_HINTS,
    _MESSAGE_SEARCH_CHAT_TYPES,
    _MESSAGE_SEARCH_FROM_TYPES,
    _MESSAGE_SEARCH_HINTS,
    _MESSAGE_SEARCH_HYDRATE_MAX,
    _MESSAGE_SEARCH_TYPES,
    _MODERATION_ERROR_HINTS,
    _PIN_ERROR_HINTS,
    _POST_BLOCK_TAGS,
    _REACTION_ERROR_HINTS,
    _READ_STATUS_ERROR_HINTS,
    _RECALL_ERROR_HINTS,
    _SEND_MEDIA_ERROR_HINTS,
    _TAB_ERROR_HINTS,
    _UPLOAD_ERROR_HINTS,
    _announcement_meta,
    _build_announcement_blocks_request,
    _build_announcement_children_request,
    _build_announcement_delete_request,
    _build_announcement_get_request,
    _build_chat_members_request,
    _build_edit_card_request,
    _build_edit_message_request,
    _build_file_upload_request,
    _build_get_chat_request,
    _build_get_message_request,
    _build_image_upload_request,
    _build_list_messages_request,
    _build_list_reactions_request,
    _build_message_search_request,
    _build_post_at_content,
    _build_post_content,
    _build_read_users_request,
    _build_remove_reaction_request,
    _build_send_message_request,
    _chat_details,
    _chat_settings,
    _ensure_update_multi,
    _extract_and_strip_at_tags,
    _extract_post_text,
    _hydrate_message,
    _infer_receive_id_type,
    _message_chat_and_sender,
    _message_plain_text,
    _message_search_body,
    _normalize_emoji_type,
    _post_node,
    _reaction_record,
    _read_upload_bytes,
    _require_message_id,
    _resolve_sender_name,
    _unread_from_roster,
    _with_hint,
    clear_chat_announcement_impl,
    edit_card_impl,
    edit_message_impl,
    find_member_id_impl,
    get_chat_impl,
    list_chat_members_impl,
    list_reactions_impl,
    read_chat_announcement_impl,
    read_status_impl,
    read_thread_impl,
    remove_reaction_impl,
    search_messages_impl,
    send_card_impl,
    send_media_message_impl,
    send_message_impl,
    send_post_message_impl,
    set_chat_announcement_impl,
    start_topic_impl,
    upload_chat_avatar_impl,
    upload_file_impl,
    upload_image_impl,
)
from _feishu.sheet import (  # noqa: E402,F401
    _SHEET_CELL_TYPES,
    _SHEET_MAX_COLS,
    _SHEET_MAX_ROWS,
    _build_sheet_append_request,
    _build_sheet_meta_request,
    _build_sheet_style_request,
    _build_sheet_values_request,
    _build_sheet_write_request,
    _flatten_sheet_cell,
    _label_grid,
    _parse_values_json,
    _read_sheet,
    _sheet_result,
    _sheet_values_to_text,
    _validate_sheet_values,
    append_sheet_impl,
    find_sheet_columns_impl,
    format_sheet_impl,
    read_sheet_grid_impl,
    read_sheet_range_impl,
    write_sheet_impl,
)
from _feishu.task import (  # noqa: E402,F401
    _build_create_task_request,
    _due_to_ms,
    create_task_impl,
)
from _feishu.worktree import (  # noqa: E402,F401
    _node_text,
    build_people,
    read_worktree_impl,
)
