from __future__ import annotations

import sys
from typing import Annotated

import anyio
import tyro
from tyro import conf

from psi_agent._run import Run
from psi_agent.ai import Ai
from psi_agent.channel.cli import ChannelCli
from psi_agent.channel.feishu import ChannelFeishu
from psi_agent.channel.repl import ChannelRepl
from psi_agent.channel.telegram import ChannelTelegram
from psi_agent.gateway import Gateway
from psi_agent.router import AiRouter
from psi_agent.session import Session

ChannelGroup = Annotated[
    Annotated[ChannelRepl, conf.subcommand(name="repl")]
    | Annotated[ChannelCli, conf.subcommand(name="cli")]
    | Annotated[ChannelTelegram, conf.subcommand(name="telegram")]
    | Annotated[ChannelFeishu, conf.subcommand(name="feishu")],
    conf.subcommand(name="channel", description="User interface channels"),
]


def parse_command(args: list[str] | None = None) -> Run | Ai | AiRouter | Session | ChannelGroup | Gateway:
    resolved_args = sys.argv[1:] if args is None else args
    if resolved_args[:2] == ["ai", "router"]:
        return tyro.cli(AiRouter, args=resolved_args[2:])
    return tyro.cli(Run | Ai | Session | ChannelGroup | Gateway, args=resolved_args)


def main() -> None:
    cmd = parse_command()
    anyio.run(cmd.run)


if __name__ == "__main__":
    main()
