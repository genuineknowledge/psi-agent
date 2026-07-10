from __future__ import annotations

import sys
from typing import Annotated

import anyio
import tyro
from tyro import conf

from psi_agent._run import Run
from psi_agent.ai import Ai, AiRouter
from psi_agent.channel.cli import ChannelCli
from psi_agent.channel.feishu import ChannelFeishu
from psi_agent.channel.repl import ChannelRepl
from psi_agent.channel.telegram import ChannelTelegram
from psi_agent.gateway import Gateway
from psi_agent.session import Session

ChannelGroup = Annotated[
    Annotated[ChannelRepl, conf.subcommand(name="repl")]
    | Annotated[ChannelCli, conf.subcommand(name="cli")]
    | Annotated[ChannelTelegram, conf.subcommand(name="telegram")]
    | Annotated[ChannelFeishu, conf.subcommand(name="feishu")],
    conf.subcommand(name="channel", description="User interface channels"),
]


def main() -> None:
    argv = sys.argv[1:]
    if len(argv) >= 2 and argv[0] == "ai" and argv[1] == "router":
        cmd = tyro.cli(AiRouter, args=argv[2:])
    else:
        cmd = tyro.cli(Run | Ai | Session | ChannelGroup | Gateway, args=argv)
    anyio.run(cmd.run)


if __name__ == "__main__":
    main()
