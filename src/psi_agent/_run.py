"""Batch runner: start AI, Session, and Channel components from a single config.

Usage: ``psi-agent run config.yml``

Reads a YAML file containing a list of component definitions and runs
them concurrently via ``anyio.create_task_group()``.

Config format (``run-config.yml``):

    - type: ai
      provider: openai
      session_socket: ./ai.sock
      model: gpt-4o-mini
      api_key: ${OPENAI_API_KEY}
      base_url: https://api.openai.com/v1

    - type: session
      workspace: ./examples/a-simple-bash-only-workspace  # optional, defaults to .
      channel_socket: ./channel.sock
      ai_socket: ./ai.sock

    - type: channel
      name: repl                    # "cli" or "repl"
      session_socket: ./channel.sock
      message: "hello world"       # only for cli

Servers (ai, session) run forever; channel components may return
(CLI exits after response, REPL runs until Ctrl+D).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any

import anyio
import yaml
from loguru import logger
from tyro import conf

from psi_agent.ai import Ai
from psi_agent.channel.cli import ChannelCli
from psi_agent.channel.feishu import ChannelFeishu
from psi_agent.channel.repl import ChannelRepl
from psi_agent.channel.telegram import ChannelTelegram
from psi_agent.session import Session


@dataclass
class Run:
    """Launch components defined in a YAML config file."""

    config: Annotated[Path, conf.Positional]
    """Path to a YAML config file listing components to run."""

    async def run(self) -> None:
        await _run_config(self.config)


async def _run_config(config_path: Path) -> None:
    """Launch all components defined in a YAML config file."""
    content = await anyio.Path(str(config_path)).read_text()
    raw = yaml.safe_load(content)

    if not isinstance(raw, list):
        raise ValueError("Config must be a list of component definitions")

    components: list[Any] = []
    for item in raw:
        kind = item.pop("type")
        match kind:
            case "ai":
                components.append(Ai(**item))
            case "session":
                components.append(Session(**item))
            case "channel":
                name = item.pop("name")
                match name:
                    case "cli":
                        components.append(ChannelCli(**item))
                    case "repl":
                        components.append(ChannelRepl(**item))
                    case "telegram":
                        components.append(ChannelTelegram(**item))
                    case "feishu":
                        components.append(ChannelFeishu(**item))
                    case _:
                        raise ValueError(f"Unknown channel name: {name}")
            case _:
                raise ValueError(f"Unknown component type: {kind}")
        logger.info(f"Configured: {kind}")

    async with anyio.create_task_group() as tg:
        for c in components:
            tg.start_soon(c.run)
