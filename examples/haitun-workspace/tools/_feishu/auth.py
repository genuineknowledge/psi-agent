"""Feishu OAuth authorization flows — start, card, poll, collect, complete, identity.

Split out of ``_feishu_impl.py`` by domain. The shared client/token layer stays
there: this module reaches it through ``_core`` so that everything patched on
``_feishu_impl`` (``_invoke``, ``_get_client``, ``_get_valid_uat``, ...) keeps
taking effect here. ``_feishu_impl`` re-exports every public name below, so tool
entrypoints keep importing it and nothing else has to change.
"""

from __future__ import annotations

import contextlib
import json
import os
import pathlib
import re
from typing import Any

import _feishu_auth_watch as _auth_watch
import _feishu_impl as _core
import _oauth_receiver as _oauth_rx
import anyio
from loguru import logger

# Granted alongside every request so a token can be refreshed instead of
# re-authorized; never itself a capability the caller has to ask for.
_OFFLINE_SCOPE = "offline_access"
# What a caller gets when it names no capabilities: the read-only docs/drive pair
# plus docx/wiki writing — the set this tool granted unconditionally before
# capabilities existed, so an un-updated caller keeps working.
_DEFAULT_CAPABILITIES = ("docs_read", "drive_read", "docx_write", "wiki_write")


def _parse_capabilities(capabilities: str) -> tuple[list[str], str]:
    """Split a comma/space-separated capability list into (keys, error).

    Unknown keys are refused *before* the authorize URL is built — sending them to
    Feishu would fail the whole page with error 20043, which reads to the user as
    "authorization is broken" rather than "that capability doesn't exist".
    """
    raw = [c.strip() for c in re.split(r"[,\s]+", capabilities or "") if c.strip()]
    if not raw:
        return list(_DEFAULT_CAPABILITIES), ""
    unknown = [c for c in raw if c not in _core._SCOPE_CATALOG]
    if unknown:
        return [], (
            f"未知的权限能力键: {', '.join(unknown)}. 只能用这些: {', '.join(_core.scope_catalog_keys())}. "
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
    scopes = [s for c in capabilities if c in _core._SCOPE_CATALOG for s in _core._SCOPE_CATALOG[c]]
    return " ".join([*dict.fromkeys(scopes), _OFFLINE_SCOPE])


def _write_json_map(path: str, data: dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def _record_granted_capabilities(user_key: str, capabilities: list[str]) -> None:
    """Add ``capabilities`` to what ``user_key`` has authorized (union, never shrink)."""
    path = _core._granted_scopes_path()
    data = _core._read_json_map(path)
    key = _core._norm_user_key(user_key)
    stored = data.get(key)
    existing = stored if isinstance(stored, list) else []
    merged = {c for c in [*existing, *capabilities] if c in _core._SCOPE_CATALOG}
    data[key] = [c for c in _core.scope_catalog_keys() if c in merged]
    with contextlib.suppress(OSError):
        _write_json_map(path, data)


def set_identity(user_key: str, identity: str) -> str:
    """Remember this user's ownership choice. Returns "" or an error message."""
    choice = (identity or "").strip().lower()
    if choice not in _core._IDENTITY_CHOICES:
        return f"identity must be one of {', '.join(_core._IDENTITY_CHOICES)} (got {identity!r})."
    path = _core._identity_path()
    data = _core._read_json_map(path)
    data[_core._norm_user_key(user_key)] = choice
    with contextlib.suppress(OSError):
        _write_json_map(path, data)
    return ""


def _pending_auth_path(user_key: str = "") -> str:
    """Per-user pending-auth file so concurrent authorizations don't clobber each other."""
    key = _core._norm_user_key(user_key)
    # Keep filenames filesystem-safe: only allow word chars + dash, replace the
    # rest (incl. path separators and dots, so a crafted open_id can't traverse).
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", key)
    return str(pathlib.Path(_core._uat_store_path()).parent / f"pending_auth_{safe}.json")


# Authorization-code flow endpoints (China/feishu.cn — the device flow's v2
# endpoint 404s here). Browser authorize on accounts.feishu.cn; token exchange
# and refresh on open.feishu.cn/authen/v1.
_AUTHORIZE_URL = "https://accounts.feishu.cn/open-apis/authen/v1/authorize"
_TOKEN_URL = "https://open.feishu.cn/open-apis/authen/v1/access_token"


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
    creds = _core._config()
    if creds is None:
        return _core._error("Feishu app not configured. Set PSI_FEISHU_APP_ID / PSI_FEISHU_APP_SECRET.")
    requested, err = _parse_capabilities(capabilities)
    if err:
        return _core._error(err, capability_keys=_core.scope_catalog_keys())
    from urllib.parse import urlencode  # noqa: PLC0415

    app_id, _ = creds
    already = _core.granted_capabilities(user_key)
    union = [c for c in _core.scope_catalog_keys() if c in {*already, *requested}]
    state = os.urandom(24).hex()
    verifier, challenge = _new_pkce_pair()
    # 先撤掉上一轮的 watcher 并等它收尾, **再**选通道: 新一轮授权作废旧 state, 旧 watcher
    # 再守也只会等到一个过期的码, 而它守着的结果还会被 auth_collect 当成本次的状态报出去。
    #
    # 「等它收尾」在 loopback 模式下是硬要求 (实测过): 旧 watcher 占着 17860 时,
    # plan_receiver 的「端口空不空」判定会失败, 于是本可免复制的授权被静默降级成手工贴码。
    await _auth_watch.forget_and_wait(_core._norm_user_key(user_key))
    plan = _oauth_rx.plan_receiver(_explicit_redirect_uri())
    await anyio.Path(_core._pending_auth_path(user_key)).write_text(
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
        return _core._error("user_key is required — it is whose authorization this is (the sender's open_id).")
    target = (receive_id or "").strip() or key
    if not target.startswith("ou_"):
        return _core._error(
            "授权卡只能私聊发给本人 (receive_id 必须是 ou_ 开头的 open_id): 待完成的授权记录存在"
            "发卡方 workspace, 而群里点卡片会落到点击者自己的私聊会话, 那边读不到这条记录. "
            "群场景请先私聊该用户.",
            receive_id=target,
        )
    started = await auth_start_impl(capabilities, key)
    if not started.get("ok"):
        return started
    if not started.get("auto_receive"):
        return _core._error(
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
    sent = await _core.send_card_impl(
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
        return _core._error("user_key is required — it is whose authorization this is (the sender's open_id).")

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
        raw = await anyio.Path(_core._pending_auth_path(user_key)).read_text(encoding="utf-8")
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
    watched = _auth_watch.status(_core._norm_user_key(user_key))
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
        return _core._error("没有待完成的授权, 请先调 feishu_auth_request.")
    if mode == "manual":
        return _core._error(
            "当前环境无法自动接收授权码, 请让用户从浏览器地址栏复制 code 后交给 feishu_auth_complete. "
            "(想免掉复制: 调 feishu_auth_env_check 看确切缺哪一项配置, 它会给出修法.)",
            manual_required=True,
            next_step="feishu_auth_env_check",
        )
    got = await _receive_code(pending, _CHECK_TIMEOUT_SECONDS)
    if not got:
        return _core._error(
            "授权码还没到 —— 这不是失败, 只说明用户还没在授权页点「同意授权」. "
            "**本轮就此收尾**, 请用户点完后回你一句, 那一轮再调一次 feishu_auth_check. "
            "授权码在取件箱里可留存约 10 分钟, 晚点查照样能完成.",
            pending=True,
            retry_hint="等用户说点好了, 再调 feishu_auth_check (同一个 user_key)",
        )
    if got.get("error"):
        return _core._error(f"用户侧授权失败: {got['error']}")
    return await _core.auth_complete_impl(got.get("code", ""), user_key)


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
    await _core.send_message_impl(user_key, text, "open_id")


async def auth_collect_impl(user_key: str = "", timeout_seconds: int = 600) -> dict[str, Any]:
    """把「等授权码」交给后台任务, **本轮立刻返回** —— 卡片回调那一轮用这个。

    等待绝不能放在工具调用里: 工具调用发生在
    SessionAgent 的 turn 内, turn 持锁, 于是用户在这几分钟里说的话全排队 (表现就是
    「机器人卡死」); 这边起一个脱离本轮的任务去等, 工具立刻返回, 码回来时后台私聊回告。

    重复调用不会起第二个 watcher: 取件箱取走即删, 两个 watcher 会互相抢码。已经在收的
    直接返回它的进度, 已经收完的返回结果。
    """
    key = _core._norm_user_key(user_key)
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
        return _core._error("没有待完成的授权, 请先调 feishu_auth_request.")
    if str(pending.get("mode") or "manual") == "manual":
        return _core._error(
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
                return _core._error(
                    f"等不到授权回调: 本次回调地址 {redirect} 只有内网打得到. 用户若在外网, "
                    "点完「同意授权」后页面会打不开, 回调也就永远不会来 —— 再等无用. "
                    "问他一句「授权后那个打不开的页面, 地址栏里的网址是什么」, 把他发回来的"
                    "**整条网址**交给 feishu_auth_complete 即可完成授权 (不用让他自己找 code).",
                    timed_out=True,
                    callback_is_private=True,
                )
            return _core._error("等待授权回调超时", timed_out=True)
        if got.get("error"):
            return _core._error(f"用户侧授权失败: {got['error']}")
        return await _core.auth_complete_impl(got.get("code", ""), watched_key)

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
        return _core._error(
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
        return _core._error(err, identity_options=list(_core._IDENTITY_CHOICES))
    choice = _core.get_identity(user_key)
    owner = "你本人 (产出归你)" if choice == _core._IDENTITY_USER else "机器人 (产出归机器人)"
    missing = (
        _core.missing_capabilities(user_key, list(_DEFAULT_CAPABILITIES)) if choice == _core._IDENTITY_USER else []
    )
    return {
        "ok": True,
        "identity": choice,
        "capabilities": _core.granted_capabilities(user_key),
        "message": (
            f"已记住: 之后飞书写入操作用{owner}. 需要改的时候再调一次这个工具即可."
            + ("\n注意: 用你本人身份需要授权, 首次写入时会请你授权一次." if missing else "")
        ),
    }


async def identity_get_impl(user_key: str = "") -> dict[str, Any]:
    """Report this user's remembered ownership choice and granted capabilities."""
    choice = _core.get_identity(user_key)
    return {
        "ok": True,
        "identity": choice,
        "asked": bool(choice),
        "capabilities": _core.granted_capabilities(user_key),
        "capability_keys": _core.scope_catalog_keys(),
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
    return [c for c in caps if c in _core._SCOPE_CATALOG]


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
        return _core._error("No code provided.")
    pending = await _read_pending(user_key)
    app_token = await _core._get_app_access_token()
    if app_token is None:
        return _core._error("Feishu app not configured or app_access_token fetch failed.")
    body: dict[str, Any] = {"grant_type": "authorization_code", "code": _extract_code(code)}
    # PKCE verifier 与 redirect_uri 必须与 authorize 阶段一致 (飞书: 不一致报 20071)。
    if pending.get("code_verifier"):
        body["code_verifier"] = pending["code_verifier"]
    if pending.get("redirect_uri"):
        body["redirect_uri"] = pending["redirect_uri"]
    payload = await _core._post_json(
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
    uat = _core._uat_from_token_response(payload)
    if not uat.access_token:
        return _core._error("Token exchange returned no access_token.")
    await _core._get_token_store().set(_core._norm_user_key(user_key), uat)
    # Which capabilities this grant covers was decided in auth_start_impl and parked
    # in the pending-auth file; read it back before unlinking so the union survives.
    granted = await _pending_capabilities(user_key)
    _record_granted_capabilities(user_key, granted)
    with contextlib.suppress(OSError):
        await anyio.Path(_core._pending_auth_path(user_key)).unlink()
    return {
        "ok": True,
        "open_id": uat.open_id or "",
        "scopes": uat.scopes,
        "capabilities": _core.granted_capabilities(user_key),
        "message": (
            "授权成功, 已缓存 user_access_token 并会自动续期 -- 已获得的权限会被记住, "
            "之后同类操作不会再让你授权 (只有需要新权限时才会再问一次)."
        ),
    }
