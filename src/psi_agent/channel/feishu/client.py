"""Feishu bot client — handler, file download, streaming, main loop."""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, aclosing
from datetime import date
from pathlib import Path
from typing import Any, Protocol

import aiohttp
import anyio
import platformdirs
from anyio.from_thread import BlockingPortal
from lark_channel import FeishuChannel, PolicyConfig
from lark_channel.api.im.v1.model.create_message_reaction_request import CreateMessageReactionRequest
from lark_channel.api.im.v1.model.create_message_reaction_request_body import CreateMessageReactionRequestBody
from lark_channel.api.im.v1.model.delete_message_reaction_request import DeleteMessageReactionRequest
from lark_channel.api.im.v1.model.emoji import Emoji
from lark_channel.api.im.v1.model.get_message_resource_request import GetMessageResourceRequest
from lark_channel.core.enum import AccessTokenType, HttpMethod
from lark_channel.core.model import BaseRequest
from lark_channel.event.custom import CustomizedEventProcessor
from loguru import logger

from psi_agent._appdata import resolve_appdata_root
from psi_agent._feishu_routing import is_group_chat, route_key
from psi_agent.channel._core import ChannelCore
from psi_agent.channel._errors import ChannelError
from psi_agent.channel._types import FileChunk, InputChunk, ReasoningChunk, TextChunk
from psi_agent.channel.feishu._agent_events import register_feishu_agent_events

from ._card_action import CardActionBatcher, handle_card_action

_EMOJI_PROCESSING = "Typing"
_EMOJI_FAILED = "CrossMark"
_SILENT_REPLY_TOKEN = "NO_REPLY"


class ResolveCore(Protocol):
    """把一次飞书会话解析成对应 Session 的 ``ChannelCore``。

    ``chat_id``/``chat_type`` 是可选的会话事实: 群消息带上后由 Gateway 按 ``chat_id`` 路由
    (整群共用一个 session); 缺省 (文档评论、审批推送等无 IM 会话的场景) 即按 ``open_id`` 路由。
    """

    def __call__(self, open_id: str | None, *, chat_id: str = "", chat_type: str = "") -> Awaitable[ChannelCore]: ...


def _allowed(sender_id: str | None, allowed_ids: list[str] | None) -> bool:
    if allowed_ids is None:
        return True
    return sender_id in allowed_ids


class _CoreRegistry:
    """按 socket 路径缓存并复用 ``ChannelCore``; 懒创建、并发安全、随 stack 统一关闭。

    ``ChannelCore.__aenter__`` 仅建 connector + ``ClientSession``(socket 是懒连接, 缺失只在
    ``post()`` 时报错), 但 ``stack.enter_async_context(...)`` 构成挂起点: 两个经
    ``portal.start_task_soon`` 并发进来的同用户消息可能都 miss 缓存并各建一个 core → 泄露一个
    ``ClientSession``。用 double-checked 锁消除此竞态。创建罕见且全程无网络, 单把全局锁足够。
    所有 core 进同一 ``AsyncExitStack``, 退出时逐个 shielded 关闭。
    """

    def __init__(self, interval: float, stack: AsyncExitStack) -> None:
        self._interval = interval
        self._stack = stack
        self._cores: dict[str, ChannelCore] = {}
        self._lock = anyio.Lock()

    async def get(self, socket: str) -> ChannelCore:
        core = self._cores.get(socket)  # 快路径(无 await, dict 读原子)
        if core is not None:
            return core
        async with self._lock:  # 慢路径: double-checked
            core = self._cores.get(socket)
            if core is None:
                core = await self._stack.enter_async_context(ChannelCore(socket, interval=self._interval))
                self._cores[socket] = core
                logger.debug(f"created ChannelCore for socket={socket!r} (total={len(self._cores)})")
            return core


_GATEWAY_TIMEOUT = aiohttp.ClientTimeout(total=10)


class _GatewayRouteProvider:
    """给一次会话 → 幂等返回其 Gateway session 的 ``channel_socket``; 面向动态任意用户/群。

    路由决策权归 **Gateway** —— 首次见到某会话时经 Gateway REST ``POST /feishu/route``
    (``FeishuManager`` 按需 spawn 独立 Session, ``ai_id``/``workspace`` 由 Gateway 侧配置决定),
    拿回 ``channel_socket`` 缓存复用; channel 只连接不 spawn、退出时也不删。

    本地缓存键与 Gateway 的路由键保持一致: 群聊 (``chat_type`` 为 group/topic 且 ``chat_id``
    非空) 按 ``chat_id`` 缓存, 于是同群不同发送者复用同一 socket, 也只打 Gateway 一次; 其余按
    ``open_id`` 缓存。并发安全: 快路径 dict 读 + 慢路径 ``anyio.Lock`` double-checked, 同一键的
    并发消息串行到一次路由。路由失败向上抛(由调用方回退共享 socket), 且**不写缓存**, 下条消息
    会重试 Gateway。
    """

    def __init__(self, base_url: str, http: aiohttp.ClientSession) -> None:
        self._base = base_url.rstrip("/")
        self._http = http
        self._sockets: dict[str, str] = {}  # 路由键 -> channel_socket
        self._lock = anyio.Lock()

    async def ensure(self, open_id: str, *, chat_id: str = "", chat_type: str = "") -> str:
        # 本地缓存键与 Gateway FeishuManager 的路由键共用 psi_agent._feishu_routing,
        # 必须严格一致 —— 否则同群不同发言者会各打一次 Gateway。
        key = route_key(open_id, chat_id, chat_type)
        hit = self._sockets.get(key)  # 快路径
        if hit is not None:
            return hit
        async with self._lock:  # 慢路径: double-checked
            hit = self._sockets.get(key)
            if hit is not None:
                return hit
            socket = await self._route(open_id, chat_id, chat_type)
            self._sockets[key] = socket
            logger.debug(f"routed {key!r} -> socket={socket!r}")
            return socket

    async def _route(self, open_id: str, chat_id: str, chat_type: str) -> str:
        """POST /feishu/route 拿回该会话的 channel_socket (Gateway 幂等 spawn/复用)。"""
        async with self._http.post(
            f"{self._base}/feishu/route",
            json={"open_id": open_id, "chat_id": chat_id, "chat_type": chat_type},
            timeout=_GATEWAY_TIMEOUT,
        ) as resp:
            if resp.status == 201:
                data = await resp.json()
                return str(data["channel_socket"])
            body = await resp.text()
            raise RuntimeError(f"Gateway POST /feishu/route failed (status={resp.status}): {body}")


async def _resolve_shared_appdata(base_url: str, http: aiohttp.ClientSession) -> str:
    """Ask the Gateway for its AppData root via ``GET /defaults``.

    The Gateway exports ``PSI_APPDATA`` into its own environment, but the channel is a
    *sibling* process rather than a child, so it inherits nothing. A launcher that passes
    ``--appdata`` to only one of the two therefore leaves them on different roots, and
    card snapshots get written where the callback handler never looks — every click then
    falls through to the generic fallback card and reports an unmatched handler. Asking
    the Gateway keeps a single authority for the path.

    Returns ``""`` on any failure, so the caller keeps its own resolution order
    (explicit flag → ``PSI_APPDATA`` → platformdirs) and startup never hinges on this.
    """
    try:
        async with http.get(f"{base_url.rstrip('/')}/defaults", timeout=_GATEWAY_TIMEOUT) as resp:
            if resp.status != 200:
                logger.warning(f"Gateway GET /defaults returned status={resp.status}, keeping local AppData root")
                return ""
            data = await resp.json()
    except Exception as e:
        logger.warning(f"Gateway GET /defaults failed, keeping local AppData root — {e!r}")
        return ""
    appdata = data.get("appdata") if isinstance(data, dict) else None
    return appdata.strip() if isinstance(appdata, str) else ""


async def _send_file(channel: Any, chat_id: str, path: str) -> None:
    logger.debug(f"path={path}")
    result = await channel.send(chat_id, {"image": {"source": path}})
    if result.success:
        logger.debug("OK as image")
        return
    logger.debug("image rejected, trying file")
    await channel.send(chat_id, {"file": {"source": path}})


async def _add_reaction(channel: Any, message_id: str, emoji_type: str) -> str | None:
    logger.debug(f"message_id={message_id} emoji={emoji_type}")
    try:
        req = (
            CreateMessageReactionRequest.builder()
            .message_id(message_id)
            .request_body(
                CreateMessageReactionRequestBody.builder()
                .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
                .build()
            )
            .build()
        )
        resp = await channel.client.im.v1.message_reaction.acreate(req)
        if resp.data and resp.data.reaction_id:
            logger.debug(f"OK reaction_id={resp.data.reaction_id}")
            return resp.data.reaction_id
        logger.warning(f"no reaction_id in response ({emoji_type})")
    except Exception as e:
        logger.warning(f"failed ({emoji_type}) — {e}")
    return None


async def _remove_reaction(channel: Any, message_id: str, reaction_id: str) -> None:
    logger.debug(f"message_id={message_id} reaction_id={reaction_id}")
    try:
        req = DeleteMessageReactionRequest.builder().message_id(message_id).reaction_id(reaction_id).build()
        await channel.client.im.v1.message_reaction.adelete(req)
        logger.debug("OK")
    except Exception as e:
        logger.warning(f"failed — {e}")


def _context_header(ctx: Any) -> str:
    """构造一段飞书消息元数据前缀, 注入到发给 agent 的文本最前面。

    只输出客观的消息元数据(chat_id / chat_type / message_id / sender)——
    刻意不含任何具体 workspace 工具名, 保持 channel 层与 workspace 工具解耦
    (遵守微内核理念: 框架只传协议事实, 功能由 workspace 定义)。agent 如何用
    ``chat_id`` 拉群历史 / 读文档的引导, 放在 workspace 的 TOOLS.md 里。
    """
    chat_type = getattr(ctx, "chat_type", "") or "unknown"
    lines = [
        "<feishu_context>",
        f"chat_id: {getattr(ctx, 'chat_id', '') or ''}",
        f"chat_type: {chat_type}",
        f"message_id: {getattr(ctx, 'message_id', '') or ''}",
        f"sender_open_id: {getattr(ctx, 'sender_id', '') or ''}",
    ]
    sender_name = getattr(ctx, "sender_name", None)
    if sender_name:
        lines.append(f"sender_name: {sender_name}")
    thread_id = getattr(ctx, "thread_id", None) or getattr(ctx, "reply_to_message_id", None)
    if thread_id:
        lines.append(f"thread_id: {thread_id}")
    lines.append("</feishu_context>")
    return "\n".join(lines)


def _comment_context_header(event: Any, ctx: Any) -> str:
    """构造文档评论的元数据前缀, 注入到发给 agent 的问题文本最前面。

    与 ``_context_header`` 同理: 只输出客观协议事实 (file_token / file_type /
    comment_id / operator / quote), 刻意不含任何 workspace 工具名, 保持 channel
    层与 workspace 工具解耦。agent 如何用 file_token 读文档全文的引导放在
    workspace 的 TOOLS.md 里。``quote`` 是评论锚定的原文片段 (全文评论时为空)。
    """
    operator = getattr(event, "operator", None)
    lines = [
        "<feishu_comment_context>",
        f"file_token: {getattr(event, 'file_token', '') or ''}",
        f"file_type: {getattr(event, 'file_type', '') or ''}",
        f"comment_id: {getattr(event, 'comment_id', '') or ''}",
        f"operator_open_id: {getattr(operator, 'open_id', '') or ''}",
    ]
    quote = getattr(ctx, "quote", "") or ""
    if quote:
        lines.append(f"quote: {quote}")
    lines.append("</feishu_comment_context>")
    return "\n".join(lines)


class AttachmentDownloadError(ChannelError):
    """部分附件下载失败 —— 整批 fail-closed, 不把残缺批次交给 agent。

    飞书把「同时发多份文件」实现成多条消息, lark_channel 的 merge_batch 会合并成
    一条虚拟消息 (id 取最后一条、resources 全批拼接)。附件下载要求 message_id 与
    file_key 属于同一条原始消息, 所以下载必须按 batched_sources 分组。若仍有附件
    失败, 残缺批次会让模型看到文件名却拿不到文件, 进而编造本地路径 —— 因此这里
    直接抛错, 由调用方如实告诉用户哪些文件没收到。
    """

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__("以下文件未接收: " + ", ".join(missing) + " —— 请重新发送")


async def _build_chunks(channel: Any, ctx: Any) -> list[InputChunk]:
    chunks: list[InputChunk] = []
    downloads_dir = anyio.Path(platformdirs.user_downloads_dir()) / ".psi" / str(date.today())
    await downloads_dir.mkdir(parents=True, exist_ok=True)
    downloads = str(downloads_dir)
    logger.debug(f"downloads_dir={downloads} raw_content_type={ctx.raw_content_type}")

    chunks.append(TextChunk(_context_header(ctx)))
    header_only = len(chunks)

    # 摊成源消息列表: 单条消息时 merge_batch 直接返回原消息、不设 batched_sources
    # (该字段是 Optional, 默认 None), 所以必须兜底成 [ctx]。
    sources = list(getattr(ctx, "batched_sources", None) or [ctx])
    missing: list[str] = []

    # 逐条源消息扫音频: audio key 只能配它自己那条消息的 message_id。
    for src in sources:
        src_id = src.message_id
        for m in re.finditer(r'<audio\s+key="([^"]+)"', getattr(src, "content_text", "") or ""):
            audio_key = m.group(1)
            logger.debug(f"audio key={audio_key} message_id={src_id}")
            try:
                req = GetMessageResourceRequest.builder().message_id(src_id).file_key(audio_key).type("file").build()
                resp = await channel.client.im.v1.message_resource.aget(req)
                suffix = anyio.Path(resp.file_name or "").suffix
                path = str(anyio.Path(downloads) / f"{audio_key}{suffix}")
                await anyio.Path(path).write_bytes(resp.file.read())
                logger.debug(f"audio saved to {path}")
                chunks.append(FileChunk(path))
            except Exception as e:
                logger.error(f"audio download failed message_id={src_id} key={audio_key} — {e}")
                missing.append(f"语音({audio_key})")

    # 合并后的整段文本给 agent (含各条消息的渲染), 与逐条下载并不冲突。
    text = ctx.content_text or ""
    if text:
        logger.debug(f"content_text ({len(text)} chars)")
        chunks.append(TextChunk(text))

    # 附件同理逐条下载 —— 不能读 ctx.resources, 那是全批拼接结果, 既丢了归属也会重复。
    for src in sources:
        src_id = src.message_id
        for r in getattr(src, "resources", None) or []:
            logger.debug(f"resource type={r.type} file_key={r.file_key} file_name={r.file_name} message_id={src_id}")
            try:
                if r.file_name:
                    stem = anyio.Path(r.file_name).stem
                    ext = anyio.Path(r.file_name).suffix
                    name = f"{stem}-{r.file_key}{ext}"
                else:
                    name = None
                saved = await channel.download_resource_to_file(
                    r.file_key,
                    resource_type=r.type,
                    message_id=src_id,
                    dest_dir=downloads,
                    file_name=name,
                )
                logger.debug(f"resource downloaded to {saved}")
                chunks.append(FileChunk(str(saved)))
            except Exception as e:
                logger.error(f"resource download failed message_id={src_id} file_key={r.file_key} — {e}")
                missing.append(r.file_name or r.file_key)

    if missing:
        # fail-closed: 宁可整批重传, 也不给 agent 一个「文本提到 3 份、实际只有 1 份」的批次。
        logger.error(f"attachment batch incomplete, {len(missing)} missing: {missing}")
        raise AttachmentDownloadError(missing)

    if len(chunks) == header_only:
        # Only the metadata header, no real content (text/audio/resource) —
        # treat as unsupported so the caller sends "Unsupported message type".
        logger.debug("no content chunks, dropping header")
        return []

    logger.debug(f"total {len(chunks)} chunk(s)")
    return chunks


async def _stream_reply(
    channel: Any,
    core: ChannelCore,
    chat_id: str,
    chunks: list[InputChunk],
    *,
    reply_to: str | None,
    suppress_silent_reply: bool = False,
) -> None:
    """Stream agent text and files into one Feishu chat."""

    async def _produce(stream: Any) -> None:
        silent_candidate = ""
        checking_silent_reply = suppress_silent_reply

        async def flush_silent_candidate() -> None:
            nonlocal silent_candidate
            if not silent_candidate:
                return
            candidate = silent_candidate
            silent_candidate = ""
            normalized = candidate.strip()
            if not normalized:
                logger.debug("suppressed whitespace-only Feishu card action reply")
            elif normalized == _SILENT_REPLY_TOKEN:
                logger.debug("suppressed standalone NO_REPLY from Feishu card action")
            else:
                await stream.append(candidate)
                logger.debug(f"stream.append ({len(candidate)} chars)")

        try:
            async with aclosing(core.post(chunks)) as gen:
                async for chunk in gen:
                    if isinstance(chunk, TextChunk):
                        if checking_silent_reply:
                            silent_candidate += chunk.text
                            normalized = silent_candidate.strip()
                            if not normalized or _SILENT_REPLY_TOKEN.startswith(normalized):
                                continue
                            await flush_silent_candidate()
                            checking_silent_reply = False
                        else:
                            await stream.append(chunk.text)
                            logger.debug(f"stream.append ({len(chunk.text)} chars)")
                    elif isinstance(chunk, ReasoningChunk):
                        if suppress_silent_reply and chunk.kind == "tool_result":
                            await flush_silent_candidate()
                            checking_silent_reply = True
                    elif isinstance(chunk, FileChunk):
                        logger.debug(f"received FileChunk ({chunk.path})")
                        await _send_file(channel, chat_id, chunk.path)
        except Exception:
            await flush_silent_candidate()
            raise
        await flush_silent_candidate()

    options = {"reply_to": reply_to} if reply_to else {}
    await channel.stream(chat_id, {"markdown": _produce}, options)


async def _handle_and_stream(
    channel: Any,
    resolve_core: ResolveCore,
    allowed_ids: list[str] | None,
    ctx: Any,
) -> None:
    if not _allowed(ctx.sender_id, allowed_ids):
        logger.debug(f"sender {ctx.sender_id} blocked by whitelist")
        return

    # 白名单通过后才解析 core, 被拦用户不建连接 (防非白名单 open_id 刷出大量 ClientSession)。
    # 群聊按 chat_id 路由到该群的 session (整群共用), 私聊按发送者 open_id 路由到其个人
    # session —— 判定归 Gateway, 这里只如实上报会话事实。
    chat_type = getattr(ctx, "chat_type", "") or ""
    core = await resolve_core(ctx.sender_id, chat_id=ctx.chat_id, chat_type=chat_type)
    logger.debug(f"sender={ctx.sender_id} chat={ctx.chat_id} type={chat_type} socket={core.session_socket}")

    reaction_id = await _add_reaction(channel, ctx.message_id, _EMOJI_PROCESSING)
    failed = False
    try:
        try:
            try:
                chunks = await _build_chunks(channel, ctx)
            except AttachmentDownloadError as e:
                # 附件缺失是用户可自行处理的情况 (重发即可), 所以点名文件、不套通用报错前缀。
                logger.error(f"attachment download incomplete — {e}")
                failed = True
                await channel.send(ctx.chat_id, {"text": str(e)})
                return
            except Exception as e:
                logger.error(f"_build_chunks failed — {e}")
                failed = True
                await channel.send(ctx.chat_id, {"text": f"Error processing message: {e}"})
                return

            if not chunks:
                logger.debug("no chunks, unsupported type")
                await channel.send(ctx.chat_id, {"text": "Unsupported message type"})
                return

            logger.debug(f"posting {len(chunks)} chunk(s) to ChannelCore")

            try:
                await _stream_reply(channel, core, ctx.chat_id, chunks, reply_to=ctx.message_id)
                logger.debug("stream completed")
            except Exception as e:
                logger.error(f"Message handling error — {e!r}")
                failed = True
                await channel.send(ctx.chat_id, {"text": f"Error: {e}"})
        finally:
            if reaction_id:
                await _remove_reaction(channel, ctx.message_id, reaction_id)
            if failed:
                await _add_reaction(channel, ctx.message_id, _EMOJI_FAILED)
    except Exception as e:
        logger.error(f"Unhandled error in _handle_and_stream: {e!r}")


async def _collect_reply(core: ChannelCore, chunks: list[InputChunk]) -> str:
    """把 agent 的流式回复累积成单个字符串。

    文档评论 API 是一次性写入 (不支持像 IM 卡片那样的增量流式), 故这里把所有
    ``TextChunk`` 拼成一段完整文本再回复。``FileChunk`` 在评论区无处安放, 记
    DEBUG 后忽略 (评论只接受纯文本)。
    """
    parts: list[str] = []
    async with aclosing(core.post(chunks)) as gen:
        async for chunk in gen:
            if isinstance(chunk, TextChunk):
                parts.append(chunk.text)
            elif isinstance(chunk, FileChunk):
                logger.debug(f"comment reply ignoring FileChunk ({chunk.path})")
    return "".join(parts).strip()


async def _handle_comment(
    channel: Any,
    resolve_core: ResolveCore,
    allowed_ids: list[str] | None,
    event: Any,
) -> None:
    """处理文档评论 @机器人 事件 — 解析目标 → 取问题 → 喂 agent → 新建评论回复。

    注册为 channel 的 ``comment`` 回调 (经 ``start_task_soon`` 调度), 与
    ``_handle_and_stream`` 一样绝不让异常冒泡, 以免拖垮事件循环。

    门槛: 仅当评论明确 @了机器人 (``mentioned_bot``) 才回复 — 与群聊
    ``require_mention`` 语义一致, 避免文档里每条评论都触发。

    回复**一律新建独立评论**(强制 ``ctx.is_whole = True``), 不挂在原评论线程下:
    SDK ``reply_comment`` 对非全文评论走 PUT 覆盖用户那条 @机器人 的 reply
    (数据丢失), 详见下方回复处的注释。
    """
    try:
        if not getattr(event, "mentioned_bot", False):
            logger.debug(f"comment {getattr(event, 'comment_id', '?')} did not mention bot, skipping")
            return

        operator = getattr(event, "operator", None)
        operator_open_id = getattr(operator, "open_id", None)
        if not _allowed(operator_open_id, allowed_ids):
            logger.debug(f"comment operator {operator_open_id} blocked by whitelist")
            return

        # 白名单通过后才解析 core, 被拦用户不建连接 (与 _handle_and_stream 同款);
        # 按评论发起者 open_id 路由到其 per-user session。
        core = await resolve_core(operator_open_id)

        logger.debug(f"comment file_token={event.file_token} file_type={event.file_type} comment_id={event.comment_id}")

        target = await channel.resolve_comment_target(file_token=event.file_token, file_type=event.file_type)
        if not getattr(target, "supported", False):
            logger.warning(
                f"comment target unsupported (file_type={event.file_type} "
                f"reason={getattr(target, 'reason', None)}) — cannot reply"
            )
            return

        ctx = await channel.get_comment_context(
            target=target,
            comment_id=event.comment_id,
            event_reply_id=getattr(event, "reply_id", None),
        )

        question = getattr(ctx, "question", "") or ""
        chunks: list[InputChunk] = [TextChunk(_comment_context_header(event, ctx))]
        if question:
            chunks.append(TextChunk(question))
        else:
            logger.warning(f"comment {event.comment_id} has empty question text")

        try:
            reply_text = await _collect_reply(core, chunks)
        except Exception as e:
            logger.error(f"comment agent call failed — {e!r}")
            reply_text = f"Error processing comment: {e}"

        if not reply_text:
            reply_text = "(no response)"

        # 一律新建评论, 绝不覆盖用户的原评论。
        #
        # SDK `reply_comment` 对 `is_whole=False`(锚定文字的评论)走
        # PUT .../replies/:reply_id —— 那是"更新覆盖"某条 reply, 且
        # `target_reply_id` 恰是用户 @机器人 的那条 reply, 会把用户原话
        # 抹掉(数据丢失)。SDK 未提供"在已有评论下无损追加 reply"的接口,
        # 故强制走 `is_whole=True` 分支(POST .../comments 新建整条评论),
        # 代价是回复另起一条评论而非挂在原线程下, 但零数据丢失。
        ctx.is_whole = True
        await channel.reply_comment(ctx, reply_text)
        logger.debug(f"comment {event.comment_id} replied ({len(reply_text)} chars)")
    except Exception as e:
        logger.error(f"Unhandled error in _handle_comment: {e!r}")


# ── Approval status-change push (event-driven, no polling) ────────────────────
#
# Feishu pushes an ``approval_instance`` event over the app's event channel (the
# same WebSocket the bot runs) once a definition is subscribed via
# ``feishu_approval_subscribe``. The event carries only instance_code /
# approval_code / status — no target — so we fetch the instance detail to resolve
# the applicant's open_id, then feed the change into that applicant's own session
# and DM them the agent's reply. lark-channel-sdk 1.2.0 has no typed processor for
# this event, so it's wired as a customized-event handler (same escape hatch the
# SDK itself uses for drive doc comments).

_APPROVAL_EVENT_TYPE = "approval_instance"

# Human-facing labels for the Feishu instance status enum.
_APPROVAL_STATUS_LABELS = {
    "PENDING": "审批中",
    "APPROVED": "已通过",
    "REJECTED": "已拒绝",
    "CANCELED": "已撤销",
    "DELETED": "已删除",
    "REVERTED": "已撤回",
}


class _SeenEvents:
    """有界去重集 — 卡片按 message_id、审批按 (instance_code, status) 去重。

    ``OrderedDict`` 当 FIFO: 超过 ``maxlen`` 淘汰最旧键, 内存有界。非线程安全,
    只在 portal 的事件循环里单线程访问, 无需加锁。"""

    def __init__(self, maxlen: int = 512) -> None:
        self._maxlen = maxlen
        self._seen: OrderedDict[str, None] = OrderedDict()

    def add_if_new(self, key: str) -> bool:
        """True 表示首见 (已记下); False 表示重复。"""
        if key in self._seen:
            return False
        self._seen[key] = None
        if len(self._seen) > self._maxlen:
            self._seen.popitem(last=False)
        return True


def _build_instance_get_request(instance_code: str) -> BaseRequest:
    """GET 审批实例详情 (tenant token) — channel 层不能 import workspace 工具,
    故按 workspace ``_feishu_impl`` 同款手搓 BaseRequest。"""
    req = BaseRequest()
    req.http_method = HttpMethod.GET
    req.uri = "/open-apis/approval/v4/instances/:instance_id"
    req.paths["instance_id"] = instance_code
    req.add_query("user_id_type", "open_id")
    req.token_types = {AccessTokenType.TENANT}
    return req


def _parse_instance_detail(resp: Any) -> dict[str, Any]:
    """从 SDK arequest 响应里取审批实例详情, 只保留推送要用的字段。"""
    raw = getattr(resp, "raw", None)
    content = getattr(raw, "content", None) if raw is not None else None
    if not content:
        return {}
    try:
        body = json.loads(bytes(content).decode("utf-8"))
    except ValueError, UnicodeDecodeError:
        return {}
    if not isinstance(body, dict) or body.get("code") != 0:
        return {}
    data = body.get("data")
    if not isinstance(data, dict):
        return {}
    return {
        "applicant_open_id": data.get("user_id", "") or data.get("open_id", ""),
        "approval_name": data.get("approval_name", ""),
        "status": data.get("status", ""),
    }


async def _fetch_instance_detail(channel: Any, instance_code: str) -> dict[str, Any]:
    try:
        resp = await channel.client.arequest(_build_instance_get_request(instance_code))
    except Exception as e:
        logger.warning(f"approval instance {instance_code} detail fetch failed — {e!r}")
        return {}
    return _parse_instance_detail(resp)


def _approval_event_header(instance_code: str, approval_code: str, status: str, approval_name: str) -> str:
    """构造审批事件的元数据前缀, 注入到发给 agent 的主动输入最前面。

    与 ``_context_header`` 同理只输出协议事实, 不含具体 workspace 工具名, 保持
    channel 层与 workspace 解耦。agent 如何用 instance_code 读详情的引导放 TOOLS.md。"""
    label = _APPROVAL_STATUS_LABELS.get(status, status)
    lines = [
        "<feishu_approval_event>",
        f"instance_code: {instance_code}",
        f"approval_code: {approval_code}",
        f"approval_name: {approval_name}",
        f"status: {status} ({label})",
        "</feishu_approval_event>",
    ]
    return "\n".join(lines)


_APPROVAL_INSTRUCTION = (
    "上面是你订阅的一条审批状态变更事件 (由飞书主动推送, 非用户提问)。请用一句自然的话"
    "把这条审批的最新状态告知申请人本人 (可先读实例详情补充关键信息), 直接输出要发给他的话, 不要多余寒暄。"
)


async def _handle_approval_event(
    channel: Any,
    resolve_core: ResolveCore,
    allowed_ids: list[str] | None,
    seen: _SeenEvents,
    event: Any,
) -> None:
    """处理审批实例状态变更事件 — 反查申请人 → 喂其 session → DM 推送 agent 回复。

    经 ``portal.start_task_soon`` 调度, 与 ``_handle_comment`` 一样异常绝不冒泡。
    事件不带推送目标, 故先反查实例详情拿 applicant open_id; 命中白名单后按其
    open_id 路由到本人 session, 私聊推送 (receive_id_type=open_id)。飞书会重推同一
    事件, 用 (instance_code, status) 去重。"""
    try:
        payload = getattr(event, "event", None)
        if not isinstance(payload, dict):
            payload = getattr(event, "__dict__", {}).get("event") if hasattr(event, "__dict__") else None
        if not isinstance(payload, dict):
            logger.debug("approval event has no dict payload, skipping")
            return

        instance_code = payload.get("instance_code", "") or ""
        approval_code = payload.get("approval_code", "") or ""
        status = payload.get("status", "") or ""
        if not instance_code:
            logger.debug("approval event missing instance_code, skipping")
            return

        if not seen.add_if_new(f"{instance_code}:{status}"):
            logger.debug(f"approval event {instance_code}:{status} already seen, skipping")
            return

        detail = await _fetch_instance_detail(channel, instance_code)
        applicant = detail.get("applicant_open_id", "")
        if not applicant:
            logger.warning(f"approval {instance_code} — no applicant open_id resolved, cannot push")
            return
        if not _allowed(applicant, allowed_ids):
            logger.debug(f"approval applicant {applicant} blocked by whitelist")
            return

        core = await resolve_core(applicant)
        approval_name = detail.get("approval_name", "")
        status = status or detail.get("status", "")
        logger.debug(f"approval push instance={instance_code} status={status} applicant={applicant}")

        chunks: list[InputChunk] = [
            TextChunk(_approval_event_header(instance_code, approval_code, status, approval_name)),
            TextChunk(_APPROVAL_INSTRUCTION),
        ]
        try:
            reply_text = await _collect_reply(core, chunks)
        except Exception as e:
            logger.error(f"approval agent call failed — {e!r}")
            return
        if not reply_text:
            logger.debug(f"approval {instance_code} produced empty reply, skipping push")
            return

        await channel.send(applicant, {"text": reply_text}, {"receive_id_type": "open_id"})
        logger.debug(f"approval {instance_code} pushed to {applicant} ({len(reply_text)} chars)")
    except Exception as e:
        logger.error(f"Unhandled error in _handle_approval_event: {e!r}")


def _register_approval_processor(channel: Any, on_event: Callable[[Any], None]) -> bool:
    """把审批事件处理器注入已建好的 dispatcher (SDK 无 typed processor, 走 customized)。

    必须在 ``start_background()`` 之后调用: ``start_background`` 会重建 dispatcher
    (channel.py 会 ``self._dispatcher = self._build_dispatcher()``), 提前注册会被覆盖。
    p1/p2 两种 schema 都注册 (与 SDK 对 drive 评论的处理一致)。任何 SDK 内部结构
    缺失/改名都降级为告警, 绝不拖垮启动。返回是否至少注册成功一个 schema。"""
    dispatcher = getattr(channel, "dispatcher", None)
    proc_map = getattr(dispatcher, "_processorMap", None)
    if not isinstance(proc_map, dict):
        logger.warning("approval events unavailable — dispatcher has no _processorMap")
        return False
    registered = False
    for schema in ("p1", "p2"):
        key = f"{schema}.{_APPROVAL_EVENT_TYPE}"
        if key in proc_map:  # don't clobber an SDK-provided processor
            continue
        try:
            proc_map[key] = CustomizedEventProcessor(on_event)
            registered = True
        except Exception as e:  # pragma: no cover - defensive
            logger.warning(f"approval processor register failed for {key} — {e!r}")
    if registered:
        logger.debug("approval_instance event processor registered (p1/p2)")
    return registered


def _log_reject(event: Any) -> None:
    """记录被准入策略拒绝的消息 (如群里没 @机器人的普通发言)。
    注册为 channel 的 ``reject`` 回调; 自身异常绝不冒泡, 以免拖垮事件循环。
    ``policy_no_mention`` 是最常见原因 — 群聊 require_mention 生效但消息没 @机器人。
    """
    try:
        message_id = getattr(event, "message_id", None)
        reason = getattr(event, "reason", None)
        logger.debug(f"policy reject message={message_id} reason={reason}")
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"_log_reject failed — {e}")


async def _ensure_bot_identity(channel: Any) -> None:
    """确保机器人 open_id 已解析 — 群聊 @机器人 检测的前置依赖。

    ``FeishuChannel`` 启动时会自动拉取 bot 身份, 但网络抖动或飞书后台未开启
    "机器人" 能力会导致失败。此时 ``bot_open_id`` 为 None, 策略门会把群里每条
    消息都判为 "未 @机器人" 而拒绝 (表现为 "群里 @ 了也不回复")。这里在启动后
    兜底重试一次并给出明确日志。
    """
    try:
        if channel.bot_identity is not None:
            identity = channel.bot_identity
        else:
            identity = await channel.resolve_bot_identity()
    except Exception as e:
        logger.warning(f"bot identity resolve failed — {e}")
        identity = None

    if identity is not None:
        logger.info(
            f"Feishu bot identity resolved — open_id={getattr(identity, 'open_id', None)} "
            f"name={getattr(identity, 'name', None)}"
        )
    else:
        logger.warning(
            "Feishu bot identity unresolved — 群聊 @机器人 检测将不可用, "
            "请确认飞书后台已开启机器人能力 (否则群里 @ 也不会触发回复)"
        )


async def run_feishu(
    *,
    session_socket: str,
    app_id: str,
    app_secret: str,
    interval: float = 1.0,
    allowed_user_ids: list[str] | None = None,
    require_mention: bool = True,
    respond_to_mention_all: bool = False,
    respond_to_comments: bool = True,
    gateway_url: str | None = None,
    appdata: str = "",
    agent_root: str = "",
) -> None:
    policy = PolicyConfig(
        require_mention=require_mention,
        respond_to_mention_all=respond_to_mention_all,
    )
    channel = FeishuChannel(app_id=app_id, app_secret=app_secret, policy=policy)
    logger.debug(
        f"FeishuChannel created (app_id={app_id} require_mention={require_mention} "
        f"respond_to_mention_all={respond_to_mention_all} gateway_url={gateway_url!r})"
    )

    # AsyncExitStack 持有所有 per-user ChannelCore + Gateway REST 的 http session; 与 portal 的进出
    # 顺序: portal 后进先出、先于 stack 关闭, 保证在飞的 handler 仍能用到活着的 core / http,
    # 与旧版 "core 在 stop_background 之后才关" 的取消安全性等价。
    async with AsyncExitStack() as stack, BlockingPortal() as portal:
        registry = _CoreRegistry(interval, stack)

        provider: _GatewayRouteProvider | None = None
        if gateway_url:
            http = await stack.enter_async_context(aiohttp.ClientSession())
            provider = _GatewayRouteProvider(gateway_url, http)
            if not appdata.strip():
                # Only when nothing was passed explicitly: an operator-supplied --appdata
                # still wins, and this must run before the card-action handler closes over
                # the value below.
                appdata = await _resolve_shared_appdata(gateway_url, http)
        logger.info(f"AppData root: {await resolve_appdata_root(appdata)}")

        async def resolve_core(open_id: str | None, *, chat_id: str = "", chat_type: str = "") -> ChannelCore:
            socket = session_socket  # 默认兜底 (无路由键、无 gateway、或路由失败都走这)
            is_group = is_group_chat(chat_id, chat_type)
            if provider is not None and (open_id or is_group):
                try:
                    socket = await provider.ensure(open_id or "", chat_id=chat_id, chat_type=chat_type)
                except Exception as e:  # gateway 不可达 / 路由失败 → 回退共享 socket
                    logger.warning(
                        f"Gateway route failed for open_id={open_id!r} chat_id={chat_id!r}, "
                        f"falling back to shared socket {session_socket!r} — {e!r}"
                    )
                    socket = session_socket
            return await registry.get(socket)

        async def _on_message(ctx: Any) -> None:
            portal.start_task_soon(_handle_and_stream, channel, resolve_core, allowed_user_ids, ctx)

        async def _on_card_action(event: Any) -> None:
            portal.start_task_soon(
                handle_card_action,
                channel,
                resolve_core,
                allowed_user_ids,
                card_action_seen.add_if_new,
                _stream_reply,
                event,
                appdata,
                card_action_batcher,
            )

        async def _on_comment(event: Any) -> None:
            portal.start_task_soon(_handle_comment, channel, resolve_core, allowed_user_ids, event)

        card_action_seen = _SeenEvents(maxlen=10_000)
        card_action_batcher = CardActionBatcher()
        approval_seen = _SeenEvents()

        def _on_approval(event: Any) -> None:
            # Runs on the SDK dispatcher thread — hop onto the anyio loop via the portal.
            try:
                portal.start_task_soon(
                    _handle_approval_event, channel, resolve_core, allowed_user_ids, approval_seen, event
                )
            except Exception as e:  # portal closing during shutdown — never crash the WS thread
                logger.warning(f"approval event schedule failed — {e!r}")

        channel.on("message", _on_message)
        channel.on("cardAction", _on_card_action)
        channel.on("reject", _log_reject)
        if respond_to_comments:
            channel.on("comment", _on_comment)
            logger.debug("comment subscription enabled (@bot in doc comments triggers reply)")
        try:
            await channel.start_background()
            logger.info(f"Feishu bot started (session={session_socket} interval={interval})")
            # Inject the approval processor AFTER start_background — it rebuilds the
            # dispatcher, so an earlier registration would be discarded.
            _register_approval_processor(channel, _on_approval)
            await _ensure_bot_identity(channel)
            # Agent-package channel_events/feishu → unified POST /events
            if agent_root.strip():
                root = await anyio.Path(agent_root).expanduser()
            else:
                root = await anyio.Path.cwd()
            root_resolved = Path(await root.resolve())
            # TaskGroup owns synthetic producers; cancel with Channel shutdown.
            async with anyio.create_task_group() as events_tg:
                stats = await register_feishu_agent_events(
                    channel=channel,
                    agent_root=root_resolved,
                    resolve_core=resolve_core,
                    portal_start=portal.start_task_soon,
                    task_group=events_tg,
                )
                logger.info(
                    f"Feishu agent channel_events root={root_resolved} "
                    f"platform_processors={stats.platform_processors} "
                    f"synthetic_producers={stats.synthetic_producers}"
                )
                await anyio.sleep_forever()
        finally:
            logger.info("Shutting down Feishu bot")
            with anyio.CancelScope(shield=True):
                try:
                    await channel.stop_background()
                except Exception as e:
                    logger.warning(f"Feishu stop_background failed: {e}")
            logger.info("Feishu bot shutdown complete")
