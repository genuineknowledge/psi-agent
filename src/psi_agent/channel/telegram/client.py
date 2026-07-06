"""Telegram bot client — handler, file send, main loop."""

from __future__ import annotations

from contextlib import aclosing
from datetime import date

import anyio
import platformdirs
from loguru import logger
from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import FileChunk, InputChunk, TextChunk


def _allowed(user_id: int, allowed_ids: list[int] | None) -> bool:
    if allowed_ids is None:
        return True
    return user_id in allowed_ids


async def _send_file(update: Update, path: str) -> None:
    if update.message is None:
        return
    logger.debug(f"path={path}")
    try:
        await update.message.reply_photo(path)
        logger.debug("reply_photo OK")
    except Exception as e:
        logger.debug(f"reply_photo failed ({e}), fallback to reply_document")
        await update.message.reply_document(path)


async def _build_chunks(update: Update) -> list[InputChunk]:
    if update.message is None:
        return []

    chunks: list[InputChunk] = []
    downloads = anyio.Path(platformdirs.user_downloads_dir()) / ".psi" / str(date.today())
    await downloads.mkdir(parents=True, exist_ok=True)
    logger.debug(f"downloads_dir={downloads}")

    if update.message.text:
        logger.debug(f"text ({len(update.message.text)} chars)")
        chunks.append(TextChunk(update.message.text))
    elif update.message.caption:
        logger.debug(f"caption ({len(update.message.caption)} chars)")
        chunks.append(TextChunk(update.message.caption))

    if update.message.photo:
        photo = update.message.photo[-1]
        logger.debug(f"photo file_unique_id={photo.file_unique_id} size={photo.width}x{photo.height}")
        try:
            tfile = await photo.get_file()
            path = str(downloads / f"{photo.file_unique_id}.jpg")
            await tfile.download_to_drive(path)
            logger.debug(f"photo downloaded to {path}")
            chunks.append(FileChunk(path))
        except Exception as e:
            logger.error(f"photo download failed — {e}")

    if update.message.document:
        doc = update.message.document
        logger.debug(f"document file_name={doc.file_name} file_size={doc.file_size}")
        try:
            tfile = await doc.get_file()
            if doc.file_name:
                stem = anyio.Path(doc.file_name).stem
                ext = anyio.Path(doc.file_name).suffix
                name = f"{stem}-{doc.file_unique_id}{ext}"
            else:
                name = doc.file_unique_id
            path = str(downloads / name)
            await tfile.download_to_drive(path)
            logger.debug(f"document downloaded to {path}")
            chunks.append(FileChunk(path))
        except Exception as e:
            logger.error(f"document download failed — {e}")

    logger.debug(f"total {len(chunks)} chunk(s)")
    return chunks


async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or update.effective_user is None:
        return

    user_id = update.effective_user.id
    core: ChannelCore = context.bot_data["core"]
    allowed_ids: list[int] | None = context.bot_data["allowed_ids"]

    if not _allowed(user_id, allowed_ids):
        logger.debug(f"user {user_id} blocked by whitelist")
        return

    logger.debug(f"user_id={user_id}")

    sent = await update.message.reply_text("...")
    logger.debug("placeholder sent")

    try:
        chunks = await _build_chunks(update)
    except Exception as e:
        logger.error(f"_build_chunks failed — {e}")
        await sent.edit_text(f"Error: {e}")
        return

    if not chunks:
        logger.debug("no chunks, unsupported type")
        await sent.edit_text("Unsupported message type")
        return

    logger.debug(f"posting {len(chunks)} chunk(s) to ChannelCore")
    accumulated = ""
    try:
        async with aclosing(core.post(chunks)) as stream:
            async for chunk in stream:
                if isinstance(chunk, TextChunk):
                    accumulated += chunk.text
                    if accumulated.strip():
                        await sent.edit_text(accumulated)
                        logger.debug(f"edit_text ({len(accumulated)} chars)")
                elif isinstance(chunk, FileChunk):
                    logger.debug(f"received FileChunk ({chunk.path})")
                    await _send_file(update, chunk.path)
            logger.debug("stream completed")
    except Exception as e:
        logger.error(f"Message handling error — {e!r}")
        await sent.edit_text(f"Error: {e}")
        return

    try:
        await sent.edit_text(accumulated, parse_mode="Markdown")
    except Exception as e:
        logger.debug(f"Markdown edit failed — {e}")


async def run_telegram(
    *,
    session_socket: str,
    bot_token: str,
    interval: float = 1.0,
    allowed_user_ids: list[int] | None = None,
    proxy: str = "",
) -> None:
    builder = Application.builder().token(bot_token)
    if proxy:
        logger.debug(f"proxy={proxy}")
        builder = builder.proxy(proxy).get_updates_proxy(proxy)
    app = builder.build()
    if not isinstance(app, Application):
        raise TypeError("Failed to build Application")

    async with ChannelCore(session_socket, interval=interval) as core:
        app.bot_data["core"] = core
        app.bot_data["allowed_ids"] = allowed_user_ids

        app.add_handler(MessageHandler(filters.ALL, _handle_message))
        logger.debug("handler registered (all messages)")

        try:
            await app.initialize()
            await app.start()
            if app.updater is None:
                raise RuntimeError("Application has no updater")
            await app.updater.start_polling()
            logger.info(f"Telegram bot polling started (session={session_socket} interval={interval})")
            await anyio.sleep_forever()
        finally:
            logger.info("Shutting down Telegram bot")
            with anyio.CancelScope(shield=True):
                if app.updater is not None:
                    try:
                        await app.updater.stop()
                    except Exception as e:
                        logger.warning(f"Telegram updater.stop failed: {e}")
                try:
                    await app.stop()
                except Exception as e:
                    logger.warning(f"Telegram app.stop failed: {e}")
                try:
                    await app.shutdown()
                except Exception as e:
                    logger.warning(f"Telegram app.shutdown failed: {e}")
            logger.info("Telegram bot shutdown complete")
