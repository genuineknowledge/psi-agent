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
    """Session socket path (Unix/TCP/Named Pipe). 无 gateway_url 时全体共用, 有 gateway_url 时作兜底。"""

    gateway_url: str | None = None
    """Gateway REST 基址 (如 ``http://127.0.0.1:8080``), 面向**动态任意用户**场景。

    设置后, 任意飞书用户首次发消息时 channel 按其 open_id 经 Gateway ``POST /feishu/route`` 幂等地
    拿到其独立 session 的 ``channel_socket`` 再连——路由/spawn 决策全在 Gateway (``FeishuManager``),
    channel 只连接不 spawn、退出时也不删。每人由此获得隔离的会话/历史 (独立 workspace 子目录)。
    Gateway 不可达或路由失败时回退共享 ``session_socket`` (用户总能得到回复, 只是不隔离)。
    None(默认)=不启用, 行为与今天完全一致 (全体共用 ``session_socket``)。所挂 AI 及 workspace 由
    Gateway 侧 ``--feishu-ai-id`` / ``--feishu-workspace-root`` 决定, channel 无需关心。"""

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
            gateway_url=self.gateway_url,
        )
