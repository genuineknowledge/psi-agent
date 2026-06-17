from __future__ import annotations

from typing import Annotated

import anyio
import tyro
from tyro import conf

from psi_agent.ai import AiBackend
from psi_agent.channel.cli import ChannelCli
from psi_agent.channel.repl import ChannelRepl
from psi_agent.session import Session

AiGroup = Annotated[
    AiBackend,
    conf.subcommand(name="ai", description="AI backend services"),
]

ChannelGroup = Annotated[
    Annotated[ChannelRepl, conf.subcommand(name="repl")] | Annotated[ChannelCli, conf.subcommand(name="cli")],
    conf.subcommand(name="channel", description="User interface channels"),
]


def main() -> None:
    cmd = tyro.cli(Session | AiGroup | ChannelGroup)  # ty: ignore[no-matching-overload]
    anyio.run(cmd.run)
