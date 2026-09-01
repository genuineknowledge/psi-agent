"""Chunk types exchanged between Channel clients and ``ChannelCore``."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileChunk:
    """A file to transfer — an input attachment (sent as ``[RECV:/path]``) or an
    output file detected from a ``[SEND:/path]`` marker in the reply.

    ``source`` says **where the bytes can be fetched from**; empty means *this*
    filesystem can read ``path`` directly. It is filled (by ``ChannelCore``) only
    for output files coming from a Session reached over TCP — i.e. one living in
    another container, whose ``path`` is meaningless here. Without it a client
    cannot tell "the file is missing" from "the file is somewhere else", and the
    latter used to surface as an unconsumed ``[SEND:]`` marker sent as plain text.

    Defaulted so every input-side construction site stays unchanged: only the
    outbound cross-container path has an address to report.
    """

    path: str
    source: str = ""


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


InputChunk = FileChunk | TextChunk
OutputChunk = FileChunk | TextChunk | ReasoningChunk
