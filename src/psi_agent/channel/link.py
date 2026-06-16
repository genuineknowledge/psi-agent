from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qsl, unquote, urlparse

from psi_agent._logging import setup_logging
from psi_agent.errors import UserFacingError

ChannelProvider = Literal[
    "telegram",
    "whatsapp",
    "discord",
    "slack",
    "repl",
    "qq",
    "wechat",
    "feishu",
    "dingtalk",
]

_SUPPORTED_PROVIDER_HINT = (
    "Use a Telegram, WhatsApp, Discord, Slack, REPL, QQ, WeChat, Feishu, or DingTalk channel link."
)


@dataclass(frozen=True)
class ChannelLink:
    """Normalized representation of a user-facing channel link."""

    provider: ChannelProvider
    protocol: str
    raw: str
    kind: str
    target: str
    params: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "protocol": self.protocol,
            "kind": self.kind,
            "target": self.target,
            "params": self.params,
            "raw": self.raw,
        }


@dataclass
class ChannelLinkInfo:
    """Validate and normalize a supported channel link."""

    url: str
    """Telegram, WhatsApp, Discord, Slack, REPL, QQ, WeChat, Feishu, or DingTalk channel link."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        link = parse_channel_link(self.url)
        sys.stdout.write(json.dumps(link.to_dict(), ensure_ascii=False))
        sys.stdout.write("\n")


def parse_channel_link(link: str) -> ChannelLink:
    raw = link.strip()
    if not raw:
        raise UserFacingError("Channel link is empty.", _SUPPORTED_PROVIDER_HINT)

    parsed = urlparse(raw)
    scheme = parsed.scheme.lower()
    if not scheme:
        raise UserFacingError("Channel link is missing a protocol.", _SUPPORTED_PROVIDER_HINT)

    if scheme in {"tg", "telegram"}:
        return _parse_telegram(raw, parsed)
    if scheme == "whatsapp":
        return _parse_whatsapp(raw, parsed)
    if scheme == "discord":
        return _parse_discord(raw, parsed)
    if scheme == "slack":
        return _parse_slack(raw, parsed)
    if scheme == "repl":
        return _parse_repl(raw, parsed)
    if scheme in {"qq", "mqq", "tencent"}:
        return _parse_qq(raw, parsed)
    if scheme in {"wechat", "weixin", "wecom", "wechat-bridge", "weixin-bridge"}:
        return _parse_wechat(raw, parsed)
    if scheme in {"feishu", "lark"}:
        return _parse_feishu(raw, parsed)
    if scheme in {"dingtalk", "ding"}:
        return _parse_dingtalk(raw, parsed)
    if scheme in {"http", "https"}:
        return _parse_web_link(raw, parsed)

    raise UserFacingError(f"Unsupported channel link protocol: {scheme}", _SUPPORTED_PROVIDER_HINT)


def is_supported_channel_link(link: str) -> bool:
    try:
        parse_channel_link(link)
    except UserFacingError:
        return False
    return True


def _parse_web_link(raw: str, parsed) -> ChannelLink:
    host = _host(parsed)
    if host in {"t.me", "telegram.me"}:
        return _parse_telegram(raw, parsed)
    if host in {"wa.me", "api.whatsapp.com", "web.whatsapp.com", "whatsapp.com", "chat.whatsapp.com"}:
        return _parse_whatsapp(raw, parsed)
    if host in {
        "qclaw.qq.com",
        "mp.weixin.qq.com",
        "work.weixin.qq.com",
        "qy.weixin.qq.com",
        "weixin.qq.com",
        "wechat.com",
    }:
        return _parse_wechat(raw, parsed)
    if host in {"discord.com", "discordapp.com", "discord.gg"}:
        return _parse_discord(raw, parsed)
    if host == "slack.com" or host == "app.slack.com" or host.endswith(".slack.com"):
        return _parse_slack(raw, parsed)
    if host in {"qm.qq.com", "qun.qq.com", "bot.q.qq.com"} or host.endswith(".qq.com"):
        return _parse_qq(raw, parsed)
    if (
        host in {"applink.feishu.cn", "open.feishu.cn", "feishu.cn", "larksuite.com", "applink.larksuite.com"}
        or host.endswith(".feishu.cn")
        or host.endswith(".larksuite.com")
    ):
        return _parse_feishu(raw, parsed)
    if host in {"dingtalk.com", "im.dingtalk.com", "oapi.dingtalk.com"} or host.endswith(".dingtalk.com"):
        return _parse_dingtalk(raw, parsed)
    raise UserFacingError(f"Unsupported channel link host: {host or '<missing>'}", _SUPPORTED_PROVIDER_HINT)


def _parse_telegram(raw: str, parsed) -> ChannelLink:
    params = _query_params(parsed)
    scheme = parsed.scheme.lower()
    parts = _path_parts(parsed)

    if scheme in {"tg", "telegram"}:
        action = parsed.netloc.lower()
        if action in {"resolve", "msg_url"}:
            return _channel_link("telegram", scheme, raw, "channel", _require_target(params.get("domain"), raw), params)
        if action in {"join", "joinchat"}:
            return _channel_link("telegram", scheme, raw, "invite", _require_target(params.get("invite"), raw), params)
        if action == "user":
            return _channel_link("telegram", scheme, raw, "user", _require_target(params.get("id"), raw), params)
        target = _first_non_empty(parsed.netloc, "/".join(parts), params.get("domain"), params.get("id"))
        return _channel_link("telegram", scheme, raw, "channel", _require_target(target, raw), params)

    if not parts:
        raise UserFacingError("Telegram link is missing a channel, user, or invite target.", _SUPPORTED_PROVIDER_HINT)

    first = parts[0]
    if first in {"joinchat", "+"} and len(parts) > 1:
        return _channel_link("telegram", scheme, raw, "invite", parts[1], params)
    if first.startswith("+"):
        return _channel_link("telegram", scheme, raw, "invite", first, params)
    if first == "c" and len(parts) >= 3:
        return _channel_link("telegram", scheme, raw, "message", "/".join(parts[1:3]), params)
    if len(parts) >= 2:
        return _channel_link("telegram", scheme, raw, "message", "/".join(parts[:2]), params)
    return _channel_link("telegram", scheme, raw, "channel", first, params)


def _parse_whatsapp(raw: str, parsed) -> ChannelLink:
    params = _query_params(parsed)
    scheme = parsed.scheme.lower()
    host = _host(parsed)
    parts = _path_parts(parsed)

    if scheme == "whatsapp":
        action = parsed.netloc.lower() or (parts[0].lower() if parts else "")
        target = _first_non_empty(
            params.get("phone"),
            params.get("code"),
            "/".join(parts[1:] if parsed.netloc else parts),
        )
        kind = "invite" if action == "chat" or params.get("code") else "contact"
        return _channel_link("whatsapp", scheme, raw, kind, _require_target(target, raw), params)

    if host == "chat.whatsapp.com":
        return _channel_link("whatsapp", scheme, raw, "invite", _require_target(parts[0] if parts else "", raw), params)
    if host == "wa.me":
        target = _require_target(parts[0] if parts else "", raw)
        return _channel_link("whatsapp", scheme, raw, "contact", target, params)

    target = _first_non_empty(params.get("phone"), parts[0] if parts and parts[0] != "send" else "")
    return _channel_link("whatsapp", scheme, raw, "contact", _require_target(target, raw), params)


def _parse_discord(raw: str, parsed) -> ChannelLink:
    params = _query_params(parsed)
    scheme = parsed.scheme.lower()
    host = _host(parsed)
    parts = _path_parts(parsed)

    if scheme == "discord" and parsed.netloc.lower() == "channels":
        parts = ["channels", *parts]
    if scheme == "discord" and parsed.netloc.lower() in {"invite", "join"}:
        parts = ["invite", *parts]

    if host == "discord.gg":
        return _channel_link("discord", scheme, raw, "invite", _require_target(parts[0] if parts else "", raw), params)

    if parts and parts[0] in {"invite", "join"}:
        target = _require_target(parts[1] if len(parts) > 1 else "", raw)
        return _channel_link("discord", scheme, raw, "invite", target, params)
    if parts and parts[0] == "channels":
        target_parts = parts[1:4]
        kind = "message" if len(target_parts) >= 3 else "channel"
        return _channel_link("discord", scheme, raw, kind, _require_target("/".join(target_parts), raw), params)

    target = _first_non_empty("/".join(parts), parsed.netloc)
    return _channel_link("discord", scheme, raw, "channel", _require_target(target, raw), params)


def _parse_slack(raw: str, parsed) -> ChannelLink:
    params = _query_params(parsed)
    scheme = parsed.scheme.lower()
    host = _host(parsed)
    parts = _path_parts(parsed)

    if scheme == "slack":
        action = parsed.netloc.lower()
        target = _first_non_empty(params.get("channel"), params.get("id"), "/".join(parts))
        kind = "user" if action == "user" else "channel"
        return _channel_link("slack", scheme, raw, kind, _require_target(target, raw), params)

    if host == "app.slack.com" and len(parts) >= 3 and parts[0] == "client":
        target = "/".join(parts[1:4])
        kind = "message" if len(parts) >= 4 else "channel"
        return _channel_link("slack", scheme, raw, kind, target, params)
    if parts and parts[0] == "app_redirect":
        target = _first_non_empty(params.get("channel"), params.get("id"), params.get("team"))
        return _channel_link("slack", scheme, raw, "channel", _require_target(target, raw), params)
    if "shared_invite" in parts:
        return _channel_link("slack", scheme, raw, "invite", _require_target("/".join(parts), raw), params)
    if parts and parts[0] == "archives":
        target = "/".join(parts[1:3])
        kind = "message" if len(parts) >= 3 else "channel"
        return _channel_link("slack", scheme, raw, kind, _require_target(target, raw), params)

    target = _first_non_empty(params.get("channel"), "/".join(parts))
    return _channel_link("slack", scheme, raw, "channel", _require_target(target, raw), params)


def _parse_repl(raw: str, parsed) -> ChannelLink:
    params = _query_params(parsed)
    target = _first_non_empty(params.get("session_socket"), parsed.path, parsed.netloc)
    return _channel_link("repl", parsed.scheme.lower(), raw, "session", _require_target(target, raw), params)


def _parse_qq(raw: str, parsed) -> ChannelLink:
    params = _query_params(parsed)
    scheme = parsed.scheme.lower()
    host = _host(parsed)
    parts = _path_parts(parsed)
    action = (parsed.netloc or (parts[0] if parts else "")).lower()

    if host == "qm.qq.com":
        target = _first_non_empty(params.get("k"), params.get("group_code"), params.get("uin"), "/".join(parts))
        return _channel_link("qq", scheme, raw, "invite", _require_target(target, raw), params)
    if host == "qun.qq.com":
        target = _first_non_empty(params.get("gc"), params.get("group_code"), "/".join(parts))
        return _channel_link("qq", scheme, raw, "group", _require_target(target, raw), params)
    if host == "bot.q.qq.com":
        return _channel_link("qq", scheme, raw, "bot", _require_target("/".join(parts) or parsed.netloc, raw), params)

    if action in {"group", "qun"}:
        target = _require_target(_qq_target(parsed, parts, params), raw)
        return _channel_link("qq", scheme, raw, "group", target, params)
    if action in {"c2c", "user", "uin"}:
        target = _require_target(_qq_target(parsed, parts, params), raw)
        return _channel_link("qq", scheme, raw, "user", target, params)
    if action in {"channel", "guild"}:
        target = _require_target(_qq_target(parsed, parts, params), raw)
        return _channel_link("qq", scheme, raw, "channel", target, params)
    if action in {"bot", "agent"}:
        target = _require_target(_qq_target(parsed, parts, params), raw)
        return _channel_link("qq", scheme, raw, "bot", target, params)

    target = _first_non_empty(
        params.get("uin"),
        params.get("openid"),
        params.get("group_code"),
        "/".join(parts),
        parsed.netloc,
    )
    return _channel_link("qq", scheme, raw, "channel", _require_target(target, raw), params)


def _parse_wechat(raw: str, parsed) -> ChannelLink:
    params = _query_params(parsed)
    scheme = parsed.scheme.lower()
    host = _host(parsed)
    parts = _path_parts(parsed)
    action = (parsed.netloc or (parts[0] if parts else "")).lower()

    if host == "qclaw.qq.com":
        target = _first_non_empty(params.get("id"), params.get("channel"), "/".join(parts), host)
        return _channel_link("wechat", scheme, raw, "bridge", _require_target(target, raw), params)
    if host == "mp.weixin.qq.com":
        target = _first_non_empty(params.get("__biz"), params.get("id"), "/".join(parts))
        return _channel_link("wechat", scheme, raw, "official_account", _require_target(target, raw), params)
    if host in {"work.weixin.qq.com", "qy.weixin.qq.com"}:
        target = _first_non_empty(params.get("corp_id"), params.get("id"), "/".join(parts), host)
        return _channel_link("wechat", scheme, raw, "wecom", _require_target(target, raw), params)
    if host in {"weixin.qq.com", "wechat.com"}:
        target = _first_non_empty(params.get("id"), params.get("channel"), "/".join(parts), host)
        return _channel_link("wechat", scheme, raw, "channel", _require_target(target, raw), params)

    if scheme == "wecom":
        target = _first_non_empty(
            params.get("id"),
            params.get("corp_id"),
            _target_after_action(parsed, parts),
            parsed.netloc,
        )
        return _channel_link("wechat", scheme, raw, "wecom", _require_target(target, raw), params)
    if scheme in {"wechat-bridge", "weixin-bridge"}:
        path_target = "/".join([parsed.netloc, *parts]) if parsed.netloc else "/".join(parts)
        target = _first_non_empty(params.get("id"), params.get("channel"), path_target)
        return _channel_link("wechat", scheme, raw, "bridge", _require_target(target, raw), params)
    if action in {"qclaw", "clawbot", "bridge"}:
        target = _first_non_empty(params.get("id"), params.get("channel"), _target_after_action(parsed, parts))
        return _channel_link("wechat", scheme, raw, "bridge", _require_target(target, raw), params)
    if action in {"official", "official_account", "mp"}:
        target = _first_non_empty(params.get("__biz"), params.get("id"), _target_after_action(parsed, parts))
        return _channel_link("wechat", scheme, raw, "official_account", _require_target(target, raw), params)

    target = _first_non_empty(params.get("id"), params.get("channel"), "/".join(parts), parsed.netloc)
    return _channel_link("wechat", scheme, raw, "channel", _require_target(target, raw), params)


def _parse_feishu(raw: str, parsed) -> ChannelLink:
    params = _query_params(parsed)
    scheme = parsed.scheme.lower()
    host = _host(parsed)
    parts = _path_parts(parsed)
    action = (parsed.netloc or (parts[0] if parts else "")).lower()

    if host in {"applink.feishu.cn", "applink.larksuite.com"}:
        target = _first_non_empty(params.get("app_id"), params.get("url"), "/".join(parts))
        return _channel_link("feishu", scheme, raw, "applink", _require_target(target, raw), params)
    if host == "open.feishu.cn":
        target = _first_non_empty(params.get("app_id"), "/".join(parts), host)
        return _channel_link("feishu", scheme, raw, "app", _require_target(target, raw), params)
    if host.endswith(".feishu.cn") or host.endswith(".larksuite.com") or host in {"feishu.cn", "larksuite.com"}:
        target = _first_non_empty(params.get("chat_id"), params.get("open_id"), "/".join(parts), host)
        kind = "message" if len(parts) >= 2 else "channel"
        return _channel_link("feishu", scheme, raw, kind, _require_target(target, raw), params)

    if action in {"chat", "channel", "group"}:
        target = _first_non_empty(params.get("chat_id"), params.get("id"), _target_after_action(parsed, parts))
        return _channel_link("feishu", scheme, raw, "channel", _require_target(target, raw), params)
    if action in {"user", "open_id"}:
        target = _first_non_empty(params.get("open_id"), params.get("id"), _target_after_action(parsed, parts))
        return _channel_link("feishu", scheme, raw, "user", _require_target(target, raw), params)
    if action in {"bot", "app"}:
        target = _first_non_empty(params.get("app_id"), params.get("id"), _target_after_action(parsed, parts))
        return _channel_link("feishu", scheme, raw, "app", _require_target(target, raw), params)

    target = _first_non_empty(
        params.get("chat_id"),
        params.get("open_id"),
        params.get("app_id"),
        "/".join(parts),
        parsed.netloc,
    )
    return _channel_link("feishu", scheme, raw, "channel", _require_target(target, raw), params)


def _parse_dingtalk(raw: str, parsed) -> ChannelLink:
    params = _query_params(parsed)
    scheme = parsed.scheme.lower()
    host = _host(parsed)
    parts = _path_parts(parsed)
    action = (parsed.netloc or (parts[0] if parts else "")).lower()

    if host == "oapi.dingtalk.com" and parts[:2] == ["robot", "send"]:
        return _channel_link("dingtalk", scheme, raw, "robot", _require_target(params.get("access_token"), raw), params)
    if host in {"dingtalk.com", "im.dingtalk.com"} or host.endswith(".dingtalk.com"):
        target = _first_non_empty(params.get("chat_id"), params.get("corp_id"), "/".join(parts), host)
        kind = "message" if len(parts) >= 2 else "channel"
        return _channel_link("dingtalk", scheme, raw, kind, _require_target(target, raw), params)

    if action in {"robot", "bot"}:
        target = _first_non_empty(params.get("access_token"), params.get("id"), _target_after_action(parsed, parts))
        return _channel_link("dingtalk", scheme, raw, "robot", _require_target(target, raw), params)
    if action in {"chat", "channel", "group"}:
        target = _first_non_empty(params.get("chat_id"), params.get("id"), _target_after_action(parsed, parts))
        return _channel_link("dingtalk", scheme, raw, "channel", _require_target(target, raw), params)
    if action in {"user", "staff"}:
        target = _first_non_empty(params.get("staff_id"), params.get("id"), _target_after_action(parsed, parts))
        return _channel_link("dingtalk", scheme, raw, "user", _require_target(target, raw), params)

    target = _first_non_empty(
        params.get("chat_id"),
        params.get("access_token"),
        params.get("id"),
        "/".join(parts),
        parsed.netloc,
    )
    return _channel_link("dingtalk", scheme, raw, "channel", _require_target(target, raw), params)


def _channel_link(
    provider: ChannelProvider,
    protocol: str,
    raw: str,
    kind: str,
    target: str,
    params: dict[str, str],
) -> ChannelLink:
    return ChannelLink(provider=provider, protocol=protocol, raw=raw, kind=kind, target=target, params=params)


def _host(parsed) -> str:
    hostname = parsed.hostname or ""
    if hostname.startswith("www."):
        hostname = hostname[4:]
    return hostname.lower()


def _path_parts(parsed) -> list[str]:
    return [unquote(part) for part in parsed.path.split("/") if part]


def _query_params(parsed) -> dict[str, str]:
    return dict(parse_qsl(parsed.query, keep_blank_values=True))


def _first_non_empty(*values: str | None) -> str:
    for value in values:
        if value:
            return value
    return ""


def _qq_target(parsed, parts: list[str], params: dict[str, str]) -> str:
    return _first_non_empty(
        params.get("openid"),
        params.get("uin"),
        params.get("group_code"),
        params.get("id"),
        _target_after_action(parsed, parts),
    )


def _target_after_action(parsed, parts: list[str]) -> str:
    return "/".join(parts if parsed.netloc else parts[1:])


def _require_target(target: str | None, raw: str) -> str:
    if target:
        return target
    raise UserFacingError(f"Channel link is missing a target: {raw}", _SUPPORTED_PROVIDER_HINT)
