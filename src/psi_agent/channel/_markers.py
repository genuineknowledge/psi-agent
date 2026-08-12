"""File-transfer marker protocol between Channel and Session.

Pure (transport-free) encode/decode for the ``[RECV:/path]`` (input) and
``[SEND:/path]`` (output) markers exchanged over the message content. Kept
separate from ``ChannelCore`` so the wire protocol has a single authoritative
definition and can be unit-tested without any HTTP/SSE machinery.
"""

from __future__ import annotations

from loguru import logger

from psi_agent._send_markers import SEND_RE, iter_send_paths
from psi_agent.channel._types import FileChunk, InputChunk, TextChunk

# ``SEND_RE`` / ``iter_send_paths`` 的定义在 ``psi_agent._send_markers`` —— Session
# 侧的 Gateway 投影也要用, 放在本模块会让 Session import Channel 的私有模块。此处
# 重导出以保持既有 import 路径有效。
__all__ = ["RECV_MARKER", "SEND_RE", "SendMarkerScanner", "encode_input", "iter_send_paths"]

RECV_MARKER = "[RECV:{path}]"


def encode_input(chunks: list[InputChunk]) -> str:
    """Encode input chunks into a single user-message string.

    ``FileChunk`` becomes a ``[RECV:/path]`` marker (the Session reads the file);
    ``TextChunk`` contributes its text verbatim. Other chunk kinds are ignored.
    """
    parts: list[str] = []
    for chunk in chunks:
        if isinstance(chunk, FileChunk):
            marker = RECV_MARKER.format(path=chunk.path)
            logger.debug(f"FileChunk → {marker}")
            parts.append(marker)
        elif isinstance(chunk, TextChunk):
            parts.append(chunk.text)
    return "\n".join(parts)


class SendMarkerScanner:
    """Incrementally scans streamed output content for ``[SEND:/path]`` markers.

    Stateful across ``feed()`` calls so a marker split over multiple SSE deltas
    is still detected; each distinct path yields a ``FileChunk`` only once.
    """

    def __init__(self) -> None:
        self._full = ""
        self._scan_ptr = 0
        self._emitted: set[str] = set()

    def feed(self, text: str) -> list[FileChunk]:
        """Append a new content fragment, return newly-detected ``FileChunk``s."""
        out: list[FileChunk] = []
        self._full += text
        base = self._scan_ptr
        new = self._full[base:]
        for path, match_end in iter_send_paths(new):
            if path not in self._emitted:
                logger.debug(f"[SEND] detected → FileChunk({path})")
                out.append(FileChunk(path))
                self._emitted.add(path)
            self._scan_ptr = base + match_end
        return out
