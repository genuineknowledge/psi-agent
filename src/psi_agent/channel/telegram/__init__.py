"""Telegram bot channel."""

from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger

from psi_agent._logging import setup_logging

from .client import run_telegram


@dataclass
class ChannelTelegram:
    """Telegram bot channel."""

    session_socket: str
    """Session socket path (Unix/TCP/Named Pipe)."""

    bot_token: str = ""
    """Telegram bot token (CLI arg > PSI_TELEGRAM_BOT_TOKEN env)."""

    interval: float = 1.0
    """SSE buffer merge window. 0 = no throttling."""

    allowed_user_ids: list[int] | None = None
    """Whitelist of Telegram user IDs. None = allow all."""

    proxy: str = ""
    """HTTP/SOCKS5 proxy URL for Telegram API (e.g. socks5://127.0.0.1:1080)."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        token = self.bot_token or os.environ.get("PSI_TELEGRAM_BOT_TOKEN", "")
        if not token:
            raise ValueError("No Telegram bot token provided. Set --bot-token or PSI_TELEGRAM_BOT_TOKEN.")

        logger.info(f"Starting Telegram bot, connecting to {self.session_socket}")

        await run_telegram(
            session_socket=self.session_socket,
            bot_token=token,
            interval=self.interval,
            allowed_user_ids=self.allowed_user_ids,
            proxy=self.proxy or os.environ.get("PSI_TELEGRAM_PROXY", ""),
        )
