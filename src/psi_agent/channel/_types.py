"""Chunk types exchanged between Channel clients and ``ChannelCore``."""

from __future__ import annotations

from dataclasses import dataclass


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
    """A streamed reasoning/thinking fragment. Output only — never sent as input."""

    text: str


InputChunk = FileChunk | TextChunk
OutputChunk = FileChunk | TextChunk | ReasoningChunk
