"""Feishu bot client — handler, file download, streaming, main loop."""

from __future__ import annotations

import asyncio
import re
from datetime import date
from typing import Any

import anyio
import platformdirs
from lark_channel import FeishuChannel
from lark_channel.api.im.v1.model.get_message_resource_request import GetMessageResourceRequest
from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import Chunk, FileChunk, TextChunk


def _allowed(sender_id: str, allowed_ids: list[str] | None) -> bool:
    if allowed_ids is None:
        return True
    return sender_id in allowed_ids


async def _send_file(channel: Any, chat_id: str, path: str) -> None:
    logger.debug(f"_send_file: path={path}")
    result = await channel.send(chat_id, {"image": {"source": path}})
    if result.success:
        logger.debug("_send_file: OK as image")
        return
    logger.debug("_send_file: image rejected, trying file")
    await channel.send(chat_id, {"file": {"source": path}})


async def _build_chunks(channel: Any, ctx: Any, downloads: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    logger.debug(f"_build_chunks: downloads_dir={downloads} raw_content_type={ctx.raw_content_type}")

    text = ctx.content_text or ""
    for m in re.finditer(r'<audio\s+key="([^"]+)"', text):
        audio_key = m.group(1)
        logger.debug(f"_build_chunks: audio key={audio_key}")
        path = f"{downloads}/{audio_key[-32:]}"
        try:
            req = (
                GetMessageResourceRequest.builder()
                .message_id(ctx.message_id)
                .file_key(audio_key)
                .type("file")
                .build()
            )
            resp = await channel.client.im.v1.message_resource.aget(req)
            await anyio.Path(path).write_bytes(resp.file.read())
            logger.debug(f"_build_chunks: audio saved to {path}")
            chunks.append(FileChunk(path))
        except Exception as e:
            logger.error(f"_build_chunks: audio download failed — {e}")

    if text:
        logger.debug(f"_build_chunks: content_text ({len(text)} chars)")
        chunks.append(TextChunk(text))

    for r in ctx.resources:
        logger.debug(f"_build_chunks: resource type={r.type} file_key={r.file_key} file_name={r.file_name}")
        try:
            saved = await channel.download_resource_to_file(
                r.file_key,
                resource_type=r.type,
                message_id=ctx.message_id,
                dest_dir=downloads,
            )
            logger.debug(f"_build_chunks: resource downloaded to {saved}")
            chunks.append(FileChunk(str(saved)))
        except Exception as e:
            logger.error(f"_build_chunks: resource download failed — {e}")

    logger.debug(f"_build_chunks: total {len(chunks)} chunk(s)")
    return chunks


async def _handle_and_stream(
    channel: Any,
    core: ChannelCore,
    allowed_ids: list[str] | None,
    ctx: Any,
) -> None:
    if not _allowed(ctx.sender_id, allowed_ids):
        logger.debug(f"_handle_message: sender {ctx.sender_id} blocked by whitelist")
        return

    logger.debug(f"_handle_message: sender={ctx.sender_id} chat={ctx.chat_id}")

    downloads = f"{platformdirs.user_downloads_dir()}/.psi/{date.today()}"
    await anyio.Path(downloads).mkdir(parents=True, exist_ok=True)

    try:
        chunks = await _build_chunks(channel, ctx, downloads)
    except Exception as e:
        logger.error(f"_handle_message: _build_chunks failed — {e}")
        await channel.send(ctx.chat_id, {"text": f"Error processing message: {e}"})
        return

    if not chunks:
        logger.debug("_handle_message: no chunks, unsupported type")
        return

    logger.debug(f"_handle_message: posting {len(chunks)} chunk(s) to ChannelCore")

    async def _produce(stream: Any) -> None:
        async for chunk in core.post(chunks):
            if isinstance(chunk, TextChunk):
                await stream.append(chunk.text)
                logger.debug(f"_handle_message: stream.append ({len(chunk.text)} chars)")
            elif isinstance(chunk, FileChunk):
                logger.debug(f"_handle_message: received FileChunk ({chunk.path})")
                await _send_file(channel, ctx.chat_id, chunk.path)

    try:
        await channel.stream(
            ctx.chat_id,
            {"markdown": _produce},
            {"reply_to": ctx.message_id},
        )
        logger.debug("_handle_message: stream completed")
    except Exception as e:
        logger.error(f"_handle_message: ChannelCore error — {e}")
        await channel.send(ctx.chat_id, {"text": f"Error: {e}"})


async def run_feishu(
    *,
    session_socket: str,
    app_id: str,
    app_secret: str,
    interval: float = 1.0,
    allowed_user_ids: list[str] | None = None,
) -> None:
    channel = FeishuChannel(app_id=app_id, app_secret=app_secret)
    logger.debug(f"run_feishu: FeishuChannel created (app_id={app_id})")

    async with ChannelCore(session_socket, interval=interval) as core:
        main_loop = asyncio.get_running_loop()

        async def _on_message(ctx: Any) -> None:
            asyncio.run_coroutine_threadsafe(_handle_and_stream(channel, core, allowed_user_ids, ctx), main_loop)

        channel.on("message", _on_message)
        logger.info(f"Feishu bot connecting (session={session_socket} interval={interval})")
        await channel.start_background()
        try:
            await anyio.Event().wait()
        finally:
            await channel.stop_background()
