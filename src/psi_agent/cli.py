from __future__ import annotations

from typing import Annotated

import tyro
from tyro import conf

from psi_agent.ai.anthropic_messages import AnthropicMessages
from psi_agent.ai.openai_completions import OpenAICompletions
from psi_agent.channel.cli import ChannelCli
from psi_agent.channel.repl import ChannelRepl
from psi_agent.session import Session

AiGroup = Annotated[
    Annotated[OpenAICompletions, conf.subcommand(name="openai-completions")]
    | Annotated[AnthropicMessages, conf.subcommand(name="anthropic-messages")],
    conf.subcommand(name="ai", description="AI backend services"),
]

ChannelGroup = Annotated[
    Annotated[ChannelRepl, conf.subcommand(name="repl")] | Annotated[ChannelCli, conf.subcommand(name="cli")],
    conf.subcommand(name="channel", description="User interface channels"),
]


def main() -> None:
    import anyio

    cmd = tyro.cli(Session | AiGroup | ChannelGroup)  # ty: ignore[no-matching-overload]
    anyio.run(cmd.run)
