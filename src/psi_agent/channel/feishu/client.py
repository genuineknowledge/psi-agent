"""Feishu bot client — handler, file download, streaming, main loop."""

from __future__ import annotations

import re
from contextlib import aclosing
from datetime import date
from typing import Any

import anyio
import platformdirs
from anyio.from_thread import BlockingPortal
from lark_channel import FeishuChannel, PolicyConfig
from lark_channel.api.im.v1.model.create_message_reaction_request import CreateMessageReactionRequest
from lark_channel.api.im.v1.model.create_message_reaction_request_body import CreateMessageReactionRequestBody
from lark_channel.api.im.v1.model.delete_message_reaction_request import DeleteMessageReactionRequest
from lark_channel.api.im.v1.model.emoji import Emoji
from lark_channel.api.im.v1.model.get_message_resource_request import GetMessageResourceRequest
from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import FileChunk, InputChunk, TextChunk

_EMOJI_PROCESSING = "Typing"
_EMOJI_FAILED = "CrossMark"


def _allowed(sender_id: str, allowed_ids: list[str] | None) -> bool:
    if allowed_ids is None:
        return True
    return sender_id in allowed_ids


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


async def _build_chunks(channel: Any, ctx: Any) -> list[InputChunk]:
    chunks: list[InputChunk] = []
    downloads_dir = anyio.Path(platformdirs.user_downloads_dir()) / ".psi" / str(date.today())
    await downloads_dir.mkdir(parents=True, exist_ok=True)
    downloads = str(downloads_dir)
    logger.debug(f"downloads_dir={downloads} raw_content_type={ctx.raw_content_type}")

    chunks.append(TextChunk(_context_header(ctx)))
    header_only = len(chunks)

    text = ctx.content_text or ""
    for m in re.finditer(r'<audio\s+key="([^"]+)"', text):
        audio_key = m.group(1)
        logger.debug(f"audio key={audio_key}")
        try:
            req = (
                GetMessageResourceRequest.builder().message_id(ctx.message_id).file_key(audio_key).type("file").build()
            )
            resp = await channel.client.im.v1.message_resource.aget(req)
            suffix = anyio.Path(resp.file_name or "").suffix
            path = str(anyio.Path(downloads) / f"{audio_key}{suffix}")
            await anyio.Path(path).write_bytes(resp.file.read())
            logger.debug(f"audio saved to {path}")
            chunks.append(FileChunk(path))
        except Exception as e:
            logger.error(f"audio download failed — {e}")

    if text:
        logger.debug(f"content_text ({len(text)} chars)")
        chunks.append(TextChunk(text))

    for r in ctx.resources:
        logger.debug(f"resource type={r.type} file_key={r.file_key} file_name={r.file_name}")
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
                message_id=ctx.message_id,
                dest_dir=downloads,
                file_name=name,
            )
            logger.debug(f"resource downloaded to {saved}")
            chunks.append(FileChunk(str(saved)))
        except Exception as e:
            logger.error(f"resource download failed — {e}")

    if len(chunks) == header_only:
        # Only the metadata header, no real content (text/audio/resource) —
        # treat as unsupported so the caller sends "Unsupported message type".
        logger.debug("no content chunks, dropping header")
        return []

    logger.debug(f"total {len(chunks)} chunk(s)")
    return chunks


async def _handle_and_stream(
    channel: Any,
    core: ChannelCore,
    allowed_ids: list[str] | None,
    ctx: Any,
) -> None:
    if not _allowed(ctx.sender_id, allowed_ids):
        logger.debug(f"sender {ctx.sender_id} blocked by whitelist")
        return

    logger.debug(f"sender={ctx.sender_id} chat={ctx.chat_id}")

    reaction_id = await _add_reaction(channel, ctx.message_id, _EMOJI_PROCESSING)
    failed = False
    try:
        try:
            try:
                chunks = await _build_chunks(channel, ctx)
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

            async def _produce(stream: Any) -> None:
                async with aclosing(core.post(chunks)) as gen:
                    async for chunk in gen:
                        if isinstance(chunk, TextChunk):
                            await stream.append(chunk.text)
                            logger.debug(f"stream.append ({len(chunk.text)} chars)")
                        elif isinstance(chunk, FileChunk):
                            logger.debug(f"received FileChunk ({chunk.path})")
                            await _send_file(channel, ctx.chat_id, chunk.path)

            try:
                await channel.stream(
                    ctx.chat_id,
                    {"markdown": _produce},
                    {"reply_to": ctx.message_id},
                )
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
    core: ChannelCore,
    allowed_ids: list[str] | None,
    event: Any,
) -> None:
    """处理文档评论 @机器人 事件 — 解析目标 → 取问题 → 喂 agent → 回复该评论。

    注册为 channel 的 ``comment`` 回调 (经 ``start_task_soon`` 调度), 与
    ``_handle_and_stream`` 一样绝不让异常冒泡, 以免拖垮事件循环。

    门槛: 仅当评论明确 @了机器人 (``mentioned_bot``) 才回复 — 与群聊
    ``require_mention`` 语义一致, 避免文档里每条评论都触发。回复写回被@的那条
    评论线程 (SDK ``reply_comment``: 全文评论新建评论, 锚定评论新增回复)。
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

        await channel.reply_comment(ctx, reply_text)
        logger.debug(f"comment {event.comment_id} replied ({len(reply_text)} chars)")
    except Exception as e:
        logger.error(f"Unhandled error in _handle_comment: {e!r}")


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
) -> None:
    policy = PolicyConfig(
        require_mention=require_mention,
        respond_to_mention_all=respond_to_mention_all,
    )
    channel = FeishuChannel(app_id=app_id, app_secret=app_secret, policy=policy)
    logger.debug(
        f"FeishuChannel created (app_id={app_id} require_mention={require_mention} "
        f"respond_to_mention_all={respond_to_mention_all})"
    )

    async with ChannelCore(session_socket, interval=interval) as core, BlockingPortal() as portal:

        async def _on_message(ctx: Any) -> None:
            portal.start_task_soon(_handle_and_stream, channel, core, allowed_user_ids, ctx)

        async def _on_comment(event: Any) -> None:
            portal.start_task_soon(_handle_comment, channel, core, allowed_user_ids, event)

        channel.on("message", _on_message)
        channel.on("reject", _log_reject)
        if respond_to_comments:
            channel.on("comment", _on_comment)
            logger.debug("comment subscription enabled (@bot in doc comments triggers reply)")
        try:
            await channel.start_background()
            logger.info(f"Feishu bot started (session={session_socket} interval={interval})")
            await _ensure_bot_identity(channel)
            await anyio.sleep_forever()
        finally:
            logger.info("Shutting down Feishu bot")
            with anyio.CancelScope(shield=True):
                try:
                    await channel.stop_background()
                except Exception as e:
                    logger.warning(f"Feishu stop_background failed: {e}")
            logger.info("Feishu bot shutdown complete")
