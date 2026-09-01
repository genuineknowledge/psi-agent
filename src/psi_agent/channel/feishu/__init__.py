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
    """Gateway REST 基址 (如 ``http://127.0.0.1:8080``), 面向**动态任意用户/群**场景。

    设置后, channel 把每条消息的 ``open_id``/``chat_id``/``chat_type`` 经 Gateway
    ``POST /feishu/route`` 幂等地换成对应 session 的 ``channel_socket`` 再连——路由/spawn 决策
    全在 Gateway (``FeishuManager``), channel 只连接不 spawn、退出时也不删。路由键分两类:
    **私聊按发送者 open_id** (一人一个独立会话/历史), **群聊按 chat_id** (``chat_type`` 为
    group/topic 时整群共用一个 session, 于是机器人在群里对全体成员有连贯上下文, 且群与群、群与
    私聊互不串味)。每个键各有独立 workspace 子目录。Gateway 不可达或路由失败时回退共享
    ``session_socket`` (用户总能得到回复, 只是不隔离)。None(默认)=不启用, 全体共用
    ``session_socket``。所挂 AI 及 workspace 由 Gateway 侧 ``--feishu-ai-id`` /
    ``--feishu-workspace-root`` 决定, channel 无需关心。"""

    agent: str = ""
    """Agent package root containing ``channel_events/`` (event defs for this Channel).

    Empty → ``PSI_AGENT`` env, else cwd. Same package as Session ``--agent`` when
    Feishu shares the tob workspace. Event defs live here (not under ``src/psi_agent/channel``).
    """

    app_id: str = ""
    """Feishu app ID (CLI arg > PSI_FEISHU_APP_ID env)."""

    app_secret: str = ""
    """Feishu app secret (CLI arg > PSI_FEISHU_APP_SECRET env)."""

    interval: float = 1.0
    """SSE buffer merge window."""

    idle_drain: float = 5.0
    """上游静默这么多秒后把缓冲里的尾巴先发出去 (0 = 关掉, 回到停顿多久就等多久)。

    ``interval`` 的窗口是惰性的, 只在下一个 delta 到达时才检查, 所以上游在回复末尾长时间
    不出字时最后一段会一直卡在缓冲里, 用户看到一句话断在中间。
    """

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

    appdata: str = ""
    """AppData root shared with Feishu card-sending workspace tools."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        app_id = self.app_id or os.environ.get("PSI_FEISHU_APP_ID", "")
        app_secret = self.app_secret or os.environ.get("PSI_FEISHU_APP_SECRET", "")
        if not app_id:
            raise ValueError("No Feishu app_id. Set --app-id or PSI_FEISHU_APP_ID.")
        if not app_secret:
            raise ValueError("No Feishu app_secret. Set --app-secret or PSI_FEISHU_APP_SECRET.")

        logger.info(f"Starting Feishu bot, connecting to {self.session_socket}")
        agent_root = self.agent or os.environ.get("PSI_AGENT", "") or ""
        await run_feishu(
            session_socket=self.session_socket,
            app_id=app_id,
            app_secret=app_secret,
            interval=self.interval,
            idle_drain=self.idle_drain,
            allowed_user_ids=self.allowed_user_ids,
            require_mention=self.require_mention,
            respond_to_mention_all=self.respond_to_mention_all,
            respond_to_comments=self.respond_to_comments,
            gateway_url=self.gateway_url,
            appdata=self.appdata,
            agent_root=agent_root,
        )
