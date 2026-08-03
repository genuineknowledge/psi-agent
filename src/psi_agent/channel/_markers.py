"""File-transfer marker protocol between Channel and Session.

Pure (transport-free) encode/decode for the ``[RECV:/path]`` (input) and
``[SEND:/path]`` (output) markers exchanged over the message content. Kept
separate from ``ChannelCore`` so the wire protocol has a single authoritative
definition and can be unit-tested without any HTTP/SSE machinery.
"""

from __future__ import annotations

import re

from loguru import logger

from psi_agent.channel._types import FileChunk, InputChunk, TextChunk

RECV_MARKER = "[RECV:{path}]"
# 刻意为之: tolerate whitespace inside the brackets and around the path, and
# accept any letter case. Models routinely emit ``[ SEND:/tmp/a.docx ]`` or
# ``[Send:…]`` (a marker they wrote as prose, often after a Chinese sentence),
# and a strict ``\[SEND:(.+?)\]`` silently matches nothing — the file is
# written, announced, and never delivered. The path itself is stripped by the
# caller, so leading/trailing spaces cannot reach the FS.
SEND_RE = re.compile(r"\[\s*SEND\s*:(.+?)\]", re.IGNORECASE)


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
        for match in SEND_RE.finditer(new):
            # Strip before dedup so ``[SEND:/a]`` and ``[ SEND:/a ]`` are one file.
            path = match.group(1).strip()
            self._scan_ptr = base + match.end()
            if not path:
                logger.debug("[SEND] empty path, ignored")
                continue
            if path not in self._emitted:
                logger.debug(f"[SEND] detected → FileChunk({path})")
                out.append(FileChunk(path))
                self._emitted.add(path)
        return out
