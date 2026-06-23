from __future__ import annotations

from typing import Annotated

import anyio
import tyro
from tyro import conf

from psi_agent.ai import Ai
from psi_agent.channel.cli import ChannelCli
from psi_agent.channel.repl import ChannelRepl
from psi_agent.session import Session

ChannelGroup = Annotated[
    Annotated[ChannelRepl, conf.subcommand(name="repl")] | Annotated[ChannelCli, conf.subcommand(name="cli")],
    conf.subcommand(name="channel", description="User interface channels"),
]


def main() -> None:
    cmd = tyro.cli(Session | Ai | ChannelGroup)
    anyio.run(cmd.run)
