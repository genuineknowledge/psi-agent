"""Chunk types exchanged between Channel clients and ``ChannelCore``."""

from __future__ import annotations

from dataclasses import dataclass

from psi_agent._router_status import RouterStatus


@dataclass
class FileChunk:
    """A file to transfer — an input attachment (sent as ``[RECV:/path]``) or an
    output file detected from a ``[SEND:/path]`` marker in the reply."""

    path: str


@dataclass
class TextChunk:
    """A plain-text fragment — user input, or streamed assistant content."""

    text: str


@dataclass
class ReasoningChunk:
    """A streamed reasoning/thinking fragment. Output only — never sent as input.

    ``kind`` is optional provenance inside the compressed ``reasoning`` wire
    slot (``thinking`` / ``tool_call`` / ``tool_result``). Missing kind keeps
    legacy behaviour (CLI dim-prints everything).
    """

    text: str
    kind: str | None = None


@dataclass
class RouterStatusChunk:
    """A validated Router lifecycle snapshot. Output only and never persisted."""

    status: RouterStatus


InputChunk = FileChunk | TextChunk
OutputChunk = FileChunk | TextChunk | ReasoningChunk | RouterStatusChunk
