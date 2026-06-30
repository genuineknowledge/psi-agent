"""Feishu bot client — handler, file download, streaming, main loop."""

from __future__ import annotations

import re
from contextlib import aclosing
from datetime import date
from typing import Any

import anyio
import platformdirs
from anyio.from_thread import BlockingPortal
from lark_channel import FeishuChannel
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


async def _build_chunks(channel: Any, ctx: Any, downloads: str) -> list[InputChunk]:
    chunks: list[InputChunk] = []
    logger.debug(f"downloads_dir={downloads} raw_content_type={ctx.raw_content_type}")

    text = ctx.content_text or ""
    for m in re.finditer(r'<audio\s+key="([^"]+)"', text):
        audio_key = m.group(1)
        logger.debug(f"audio key={audio_key}")
        path = str(anyio.Path(downloads) / audio_key[-32:])
        try:
            req = (
                GetMessageResourceRequest.builder().message_id(ctx.message_id).file_key(audio_key).type("file").build()
            )
            resp = await channel.client.im.v1.message_resource.aget(req)
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
            saved = await channel.download_resource_to_file(
                r.file_key,
                resource_type=r.type,
                message_id=ctx.message_id,
                dest_dir=downloads,
            )
            logger.debug(f"resource downloaded to {saved}")
            chunks.append(FileChunk(str(saved)))
        except Exception as e:
            logger.error(f"resource download failed — {e}")

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
            downloads_dir = anyio.Path(platformdirs.user_downloads_dir()) / ".psi" / str(date.today())
            await downloads_dir.mkdir(parents=True, exist_ok=True)
            downloads = str(downloads_dir)

            try:
                chunks = await _build_chunks(channel, ctx, downloads)
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
                logger.error(f"Message handling error — {e}")
                failed = True
                await channel.send(ctx.chat_id, {"text": f"Error: {e}"})
        finally:
            if reaction_id:
                await _remove_reaction(channel, ctx.message_id, reaction_id)
            if failed:
                await _add_reaction(channel, ctx.message_id, _EMOJI_FAILED)
    except Exception as e:
        logger.error(f"Unhandled error in _handle_and_stream: {e!r}")


async def run_feishu(
    *,
    session_socket: str,
    app_id: str,
    app_secret: str,
    interval: float = 1.0,
    allowed_user_ids: list[str] | None = None,
) -> None:
    channel = FeishuChannel(app_id=app_id, app_secret=app_secret)
    logger.debug(f"FeishuChannel created (app_id={app_id})")

    async with ChannelCore(session_socket, interval=interval) as core, BlockingPortal() as portal:

        async def _on_message(ctx: Any) -> None:
            portal.start_task_soon(_handle_and_stream, channel, core, allowed_user_ids, ctx)

        channel.on("message", _on_message)
        logger.info(f"Feishu bot connecting (session={session_socket} interval={interval})")
        try:
            await channel.start_background()
            await anyio.Event().wait()
        finally:
            logger.info("Shutting down Feishu bot")
            with anyio.CancelScope(shield=True):
                try:
                    await channel.stop_background()
                except Exception as e:
                    logger.warning(f"Feishu stop_background failed: {e}")
            logger.info("Feishu bot shutdown complete")
