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

from psi_agent._logging import setup_logging
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
        setup_logging(verbose=True)
        await _run_config(self.config)


async def _run_config(config_path: Path) -> None:
    """Launch all components defined in a YAML config file."""
    logger.info(f"Loading config from {config_path}")
    try:
        content = await anyio.Path(str(config_path)).read_text()
    except OSError as e:
        logger.error(f"Failed to read config file {config_path}: {e}")
        raise
    try:
        raw = yaml.safe_load(content)
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse config YAML: {e}")
        raise
    logger.debug(f"Raw config: {raw}")

    if not isinstance(raw, list):
        msg = f"Config must be a list of component definitions, got {type(raw).__name__}"
        logger.error(msg)
        raise ValueError(msg)

    if not raw:
        logger.warning("Config contains no component definitions, nothing to run")
        return

    logger.info(f"Config contains {len(raw)} component definitions")

    components: list[Any] = []
    for item in raw:
        try:
            kind = item.pop("type")
        except KeyError:
            logger.error(f"Config item missing 'type' key: {item}")
            raise
        match kind:
            case "ai":
                try:
                    c = Ai(**item)
                except Exception as e:
                    logger.error(f"Failed to construct Ai from config item: {item}, error: {e}")
                    raise
                logger.info(
                    f"Configured ai: provider={item.get('provider')!r}, "
                    f"model={item.get('model')!r}, socket={item.get('session_socket')!r}"
                )
            case "session":
                try:
                    c = Session(**item)
                except Exception as e:
                    logger.error(f"Failed to construct Session from config item: {item}, error: {e}")
                    raise
                logger.info(
                    f"Configured session: workspace={item.get('workspace')!r}, ai_socket={item.get('ai_socket')!r}"
                )
            case "channel":
                try:
                    name = item.pop("name")
                except KeyError:
                    logger.error(f"Channel config item missing 'name' key: {item}")
                    raise
                match name:
                    case "cli":
                        try:
                            c = ChannelCli(**item)
                        except Exception as e:
                            logger.error(f"Failed to construct ChannelCli from config item: {item}, error: {e}")
                            raise
                    case "repl":
                        try:
                            c = ChannelRepl(**item)
                        except Exception as e:
                            logger.error(f"Failed to construct ChannelRepl from config item: {item}, error: {e}")
                            raise
                    case "telegram":
                        try:
                            c = ChannelTelegram(**item)
                        except Exception as e:
                            logger.error(f"Failed to construct ChannelTelegram from config item: {item}, error: {e}")
                            raise
                    case "feishu":
                        try:
                            c = ChannelFeishu(**item)
                        except Exception as e:
                            logger.error(f"Failed to construct ChannelFeishu from config item: {item}, error: {e}")
                            raise
                    case _:
                        msg = f"Unknown channel name: {name}"
                        logger.error(msg)
                        raise ValueError(msg)
                logger.info(f"Configured channel: name={name}, socket={item.get('session_socket')!r}")
            case _:
                msg = f"Unknown component type: {kind}"
                logger.error(msg)
                raise ValueError(msg)
        components.append(c)

    logger.info(f"Starting {len(components)} component(s)")
    try:
        async with anyio.create_task_group() as tg:
            for i, c in enumerate(components):
                logger.debug(f"Starting component {i + 1}: {type(c).__name__}")
                tg.start_soon(c.run)
    except Exception as e:
        logger.error(f"Component crashed, shutting down all components: {e}")
        raise
    else:
        logger.info("All components completed")
