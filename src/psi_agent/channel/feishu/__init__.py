"""Feishu bot channel."""

from __future__ import annotations

import os
from dataclasses import dataclass

from loguru import logger

from psi_agent._logging import setup_logging

from .client import run_feishu


@dataclass
class ChannelFeishu:
    """Feishu bot channel."""

    session_socket: str
    """Session socket path (Unix/TCP/Named Pipe). 无 route_template 时全体共用, 有 route_template 时作兜底。"""

    route_template: str | None = None
    """按发送者 open_id 自动派生 per-user session socket 的模板, 含 ``{open_id}`` 占位符。

    例: ``\\\\.\\pipe\\psi\\session\\{open_id}`` (Windows 命名管道) / ``/tmp/psi/session/{open_id}.sock``
    (Unix socket) / ``http://127.0.0.1:9000/{open_id}`` (TCP)。None(默认)=所有用户共用
    ``session_socket``, 行为与今天完全一致(向后兼容)。per-user session 进程由外部预先拉起,
    channel 只连接不 spawn; 命名须与 ``gateway._manager._socket_path(prefix, "session", open_id)`` 对齐。
    open_id 取不到时回退共享 ``session_socket``。"""

    app_id: str = ""
    """Feishu app ID (CLI arg > PSI_FEISHU_APP_ID env)."""

    app_secret: str = ""
    """Feishu app secret (CLI arg > PSI_FEISHU_APP_SECRET env)."""

    interval: float = 1.0
    """SSE buffer merge window."""

    allowed_user_ids: list[str] | None = None
    """Whitelist of open_id/user_id. None = allow all."""

    require_mention: bool = True
    """Group chats: only reply when the bot is @-mentioned; DMs unaffected. False replies to every group message."""

    respond_to_mention_all: bool = False
    """Whether to treat @all as a valid mention (default False, so @all does not trigger the bot)."""

    respond_to_comments: bool = True
    """Doc comments: reply when the bot is @-mentioned in a comment. False disables comment subscription."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        app_id = self.app_id or os.environ.get("PSI_FEISHU_APP_ID", "")
        app_secret = self.app_secret or os.environ.get("PSI_FEISHU_APP_SECRET", "")
        if not app_id:
            raise ValueError("No Feishu app_id. Set --app-id or PSI_FEISHU_APP_ID.")
        if not app_secret:
            raise ValueError("No Feishu app_secret. Set --app-secret or PSI_FEISHU_APP_SECRET.")

        logger.info(f"Starting Feishu bot, connecting to {self.session_socket}")
        await run_feishu(
            session_socket=self.session_socket,
            app_id=app_id,
            app_secret=app_secret,
            interval=self.interval,
            allowed_user_ids=self.allowed_user_ids,
            require_mention=self.require_mention,
            respond_to_mention_all=self.respond_to_mention_all,
            respond_to_comments=self.respond_to_comments,
            route_template=self.route_template,
        )
