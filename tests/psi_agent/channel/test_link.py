from __future__ import annotations

import json

import pytest

from psi_agent.channel.link import ChannelLinkInfo, is_supported_channel_link, parse_channel_link
from psi_agent.errors import UserFacingError


@pytest.mark.parametrize(
    ("url", "provider", "protocol", "kind", "target"),
    [
        ("tg://resolve?domain=psi_agent", "telegram", "tg", "channel", "psi_agent"),
        ("https://t.me/psi_agent/42", "telegram", "https", "message", "psi_agent/42"),
        ("telegram://user?id=12345", "telegram", "telegram", "user", "12345"),
        ("https://t.me/+invite-code", "telegram", "https", "invite", "+invite-code"),
        ("whatsapp://send?phone=15551234567&text=hello", "whatsapp", "whatsapp", "contact", "15551234567"),
        ("https://wa.me/15551234567?text=hello", "whatsapp", "https", "contact", "15551234567"),
        ("https://chat.whatsapp.com/invite-code", "whatsapp", "https", "invite", "invite-code"),
        ("discord://-/channels/123/456/789", "discord", "discord", "message", "123/456/789"),
        ("https://discord.com/channels/@me/456", "discord", "https", "channel", "@me/456"),
        ("https://discord.gg/invite-code", "discord", "https", "invite", "invite-code"),
        ("slack://channel?team=T123&id=C456", "slack", "slack", "channel", "C456"),
        ("slack://user?team=T123&id=U456", "slack", "slack", "user", "U456"),
        ("https://slack.com/app_redirect?team=T123&channel=C456", "slack", "https", "channel", "C456"),
        ("https://app.slack.com/client/T123/C456/p789", "slack", "https", "message", "T123/C456/p789"),
        ("repl:///tmp/channel.sock", "repl", "repl", "session", "/tmp/channel.sock"),
        ("qq://group/123456", "qq", "qq", "group", "123456"),
        ("mqq://c2c?uin=12345", "qq", "mqq", "user", "12345"),
        ("https://qm.qq.com/cgi-bin/qm/qr?k=invite-code", "qq", "https", "invite", "invite-code"),
        ("https://bot.q.qq.com/wiki/agent-qqbot/", "qq", "https", "bot", "wiki/agent-qqbot"),
        ("wechat-bridge://qclaw/default", "wechat", "wechat-bridge", "bridge", "qclaw/default"),
        ("weixin://qclaw?id=session-1", "wechat", "weixin", "bridge", "session-1"),
        ("wecom://chat/corp-1/room-2", "wechat", "wecom", "wecom", "corp-1/room-2"),
        ("https://qclaw.qq.com/docs/206087648449069056", "wechat", "https", "bridge", "docs/206087648449069056"),
        ("https://mp.weixin.qq.com/s?__biz=MzA", "wechat", "https", "official_account", "MzA"),
        ("feishu://chat/oc_123", "feishu", "feishu", "channel", "oc_123"),
        ("lark://user/ou_123", "feishu", "lark", "user", "ou_123"),
        ("https://applink.feishu.cn/client/chat/open?chat_id=oc_123", "feishu", "https", "applink", "client/chat/open"),
        ("https://open.feishu.cn/app/cli_123", "feishu", "https", "app", "app/cli_123"),
        ("dingtalk://chat/chat-123", "dingtalk", "dingtalk", "channel", "chat-123"),
        ("dingtalk://robot?access_token=token-123", "dingtalk", "dingtalk", "robot", "token-123"),
        ("https://oapi.dingtalk.com/robot/send?access_token=token-123", "dingtalk", "https", "robot", "token-123"),
        ("https://im.dingtalk.com/chat/abc", "dingtalk", "https", "message", "chat/abc"),
    ],
)
def test_parse_channel_link_supported_protocols(
    url: str,
    provider: str,
    protocol: str,
    kind: str,
    target: str,
) -> None:
    link = parse_channel_link(url)

    assert link.provider == provider
    assert link.protocol == protocol
    assert link.kind == kind
    assert link.target == target
    assert link.raw == url
    assert is_supported_channel_link(url)


def test_parse_channel_link_rejects_unknown_protocol() -> None:
    with pytest.raises(UserFacingError, match="Unsupported channel link protocol"):
        parse_channel_link("ftp://example.com/channel")

    assert not is_supported_channel_link("ftp://example.com/channel")


def test_parse_channel_link_rejects_unknown_https_host() -> None:
    with pytest.raises(UserFacingError, match="Unsupported channel link host"):
        parse_channel_link("https://example.com/channel")


@pytest.mark.anyio
async def test_channel_link_info_writes_normalized_json(capsys: pytest.CaptureFixture[str]) -> None:
    await ChannelLinkInfo(url="https://discord.com/channels/@me/456").run()

    output = json.loads(capsys.readouterr().out)
    assert output["provider"] == "discord"
    assert output["kind"] == "channel"
    assert output["target"] == "@me/456"
