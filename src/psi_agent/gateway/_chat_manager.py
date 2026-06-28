from __future__ import annotations

import base64
import contextlib
import os
import tempfile
import uuid
from collections.abc import AsyncIterator
from typing import Any

import anyio
from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import Chunk, FileChunk, TextChunk


class ChatManager:
    async def handle(
        self,
        channel_socket: str,
        body: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        tmp_files: list[str] = []
        chunks: list[Chunk] = []

        raw_chunks: list[dict[str, Any]] = body.get("chunks", [])
        for c in raw_chunks:
            t = c.get("type")
            if t == "text":
                chunks.append(TextChunk(text=c["text"]))
            elif t == "file":
                chunks.append(FileChunk(path=c["path"]))
            elif t == "blob":
                name = c.get("name", "file.bin")
                data = base64.b64decode(c["data"])
                path = self._temp_path(tmp_files, name)
                await anyio.Path(path).write_bytes(data)
                chunks.append(FileChunk(path=path))

        try:
            async with ChannelCore(session_socket=channel_socket, interval=0.0) as core:
                async for chunk in core.post(chunks):
                    if isinstance(chunk, TextChunk):
                        yield {"type": "text", "text": chunk.text}
                    elif isinstance(chunk, FileChunk):
                        yield await self._file_blob(chunk.path)
        finally:
            await self._cleanup(tmp_files)

    def _temp_path(self, tmp_files: list[str], name: str) -> str:
        suffix = os.path.splitext(name)[1] or ".bin"
        p = os.path.join(tempfile.gettempdir(), f"gw-blob-{uuid.uuid4().hex}{suffix}")
        tmp_files.append(p)
        return p

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

    async def _cleanup(self, paths: list[str]) -> None:
        for p in paths:
            with contextlib.suppress(OSError):
                await anyio.Path(p).unlink()
