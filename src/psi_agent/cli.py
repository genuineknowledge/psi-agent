from __future__ import annotations

import sys
from typing import Annotated

import anyio
import tyro
from tyro import conf

from psi_agent.ai.anthropic_messages import AnthropicMessages
from psi_agent.ai.openai_completions import OpenAICompletions
from psi_agent.channel.cli import ChannelCli
from psi_agent.channel.link import ChannelLinkInfo
from psi_agent.channel.platform import (
    ChannelDingTalk,
    ChannelDiscord,
    ChannelFeishu,
    ChannelQQBridge,
    ChannelSlack,
    ChannelTelegram,
    ChannelWeChatBridge,
    ChannelWhatsApp,
)
from psi_agent.channel.qqbot import ChannelQQBot
from psi_agent.channel.repl import ChannelRepl
from psi_agent.channel.web import ChannelWeb
from psi_agent.channel.weixin_ilink import ChannelWeixinIlink
from psi_agent.doctor import Doctor
from psi_agent.errors import UserFacingError
from psi_agent.gateway.profile import ProfileGateway
from psi_agent.init import Init
from psi_agent.run import Run
from psi_agent.session import Session

AiGroup = Annotated[
    Annotated[OpenAICompletions, conf.subcommand(name="openai-completions")]
    | Annotated[AnthropicMessages, conf.subcommand(name="anthropic-messages")],
    conf.subcommand(name="ai", description="AI backend services"),
]

ChannelGroup = Annotated[
    Annotated[ChannelRepl, conf.subcommand(name="repl")]
    | Annotated[ChannelWeb, conf.subcommand(name="web")]
    | Annotated[ChannelCli, conf.subcommand(name="cli")]
    | Annotated[ChannelLinkInfo, conf.subcommand(name="link")]
    | Annotated[ChannelTelegram, conf.subcommand(name="telegram")]
    | Annotated[ChannelWhatsApp, conf.subcommand(name="whatsapp")]
    | Annotated[ChannelDiscord, conf.subcommand(name="discord")]
    | Annotated[ChannelSlack, conf.subcommand(name="slack")]
    | Annotated[ChannelQQBot, conf.subcommand(name="qqbot")]
    | Annotated[ChannelQQBridge, conf.subcommand(name="qq-bridge")]
    | Annotated[ChannelWeChatBridge, conf.subcommand(name="wechat-bridge")]
    | Annotated[ChannelWeixinIlink, conf.subcommand(name="weixin-ilink")]
    | Annotated[ChannelFeishu, conf.subcommand(name="feishu")]
    | Annotated[ChannelDingTalk, conf.subcommand(name="dingtalk")],
    conf.subcommand(name="channel", description="User interface channels"),
]

RunCommand = Annotated[Run, conf.subcommand(name="run")]
DoctorCommand = Annotated[Doctor, conf.subcommand(name="doctor")]
InitCommand = Annotated[Init, conf.subcommand(name="init")]
ProfileGatewayCommand = Annotated[ProfileGateway, conf.subcommand(name="gateway")]


def main() -> None:
    _configure_console_encoding()
    cmd = None
    try:
        cmd = tyro.cli(  # ty: ignore[no-matching-overload]
            Session | RunCommand | DoctorCommand | InitCommand | ProfileGatewayCommand | AiGroup | ChannelGroup
        )
        anyio.run(cmd.run)
    except UserFacingError as e:
        _print_error(str(e))
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        _print_error("Interrupted.")
        raise SystemExit(130) from None
    except Exception as e:
        if _verbose_requested(cmd):
            raise
        _print_error(
            "psi-agent ran into an unexpected problem.\n"
            f"Next step: run the same command with --verbose for technical details. ({type(e).__name__})"
        )
        raise SystemExit(1) from None


def _configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


def _verbose_requested(cmd: object | None) -> bool:
    return bool(getattr(cmd, "verbose", False) or "--verbose" in sys.argv)


def _print_error(message: str) -> None:
    sys.stderr.write(f"Error: {message}\n")
