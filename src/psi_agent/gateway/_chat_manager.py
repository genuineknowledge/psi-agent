from __future__ import annotations

import base64
import os
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path
from typing import Any

import anyio
from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import FileChunk, InputChunk, ReasoningChunk, TextChunk


class ChatManager:
    async def handle(
        self,
        channel_socket: str,
        body: dict[str, Any],
    ) -> AsyncGenerator[dict[str, Any]]:
        """Send chat chunks to a Session and yield SSE-ready dicts.

        Args:
            channel_socket: The Session channel socket path.
            body: A dict with key ``"chunks"`` mapping to a list of chunk
                objects. Each chunk has a ``"type"`` field:

                - ``{"type": "text", "text": "..."}`` — a text message
                - ``{"type": "blob", "name": "...", "data": "<base64>"}``
                  — an inline binary (decoded and persisted to
                  ``~/Downloads/.psi/<date>/``)

        Yields:
            Dicts suitable for SSE output:

            - ``{"type": "text", "text": "..."}``
            - ``{"type": "reasoning", "text": "..."}``
            - ``{"type": "blob", "name": "...", "data": "<base64>"}``
            - ``{"type": "error", "error": "..."}`` — on blob read failure
        """
        chunks: list[InputChunk] = []

        raw_chunks = body.get("chunks", [])
        if not isinstance(raw_chunks, list):
            raw_chunks = []
        for c in raw_chunks:
            if not isinstance(c, dict):
                continue
            t = c.get("type")
            match t:
                case "text":
                    text = c.get("text")
                    if isinstance(text, str):
                        chunks.append(TextChunk(text=text))
                case "blob":
                    data_b64 = c.get("data")
                    if not isinstance(data_b64, str):
                        continue
                    try:
                        data = base64.b64decode(data_b64)
                    except ValueError as e:
                        logger.warning(f"Skipping invalid base64 blob: {e}")
                        continue
                    path = await self._save_upload(c.get("name", "file.bin"), data)
                    chunks.append(FileChunk(path=path))
                case _:
                    raise ValueError(f"Unknown chunk type: {t!r}")
            logger.debug(f"Inbound chunk type={t!r} (total {len(chunks)})")

        logger.info(f"Chat: posting {len(chunks)} chunk(s) to {channel_socket}")
        async with ChannelCore(session_socket=channel_socket, interval=0.0) as core:
            async for chunk in core.post(chunks):
                if isinstance(chunk, TextChunk):
                    yield {"type": "text", "text": chunk.text}
                elif isinstance(chunk, ReasoningChunk):
                    yield {"type": "reasoning", "text": chunk.text}
                elif isinstance(chunk, FileChunk):
                    yield await self._file_blob(chunk.path)

    async def _save_upload(self, name: str, data: bytes) -> str:
        """Persist an inbound file to ~/Downloads/.psi/{date}/ and return its path.

        Used for both multipart uploads (via the chat handler) and inline base64
        blobs. Files are kept (no cleanup) so the user can find them in Downloads.
        """
        path = self._downloads_path(name)
        await anyio.Path(path).parent.mkdir(parents=True, exist_ok=True)
        await anyio.Path(path).write_bytes(data)
        logger.debug(f"Saved inbound file to {path} ({len(data)} bytes)")
        return path

    @staticmethod
    def _downloads_path(name: str) -> str:
        date = datetime.now().strftime("%Y-%m-%d")
        base = os.path.join(str(Path.home()), "Downloads", ".psi", date)
        return os.path.join(base, os.path.basename(name))

    async def _file_blob(self, path: str) -> dict[str, str]:
        try:
            content = await anyio.Path(path).read_bytes()
            name = os.path.basename(path)
            return {
                "type": "blob",
                "name": name,
                "data": base64.b64encode(content).decode(),
            }
        except Exception as e:
            logger.warning(f"Failed to read file blob {path}: {e}")
            return {"type": "error", "error": str(e)}
