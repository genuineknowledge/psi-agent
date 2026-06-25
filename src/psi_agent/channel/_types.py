from __future__ import annotations

from dataclasses import dataclass


@dataclass
class FileChunk:
    path: str


@dataclass
class TextChunk:
    text: str


Chunk = FileChunk | TextChunk
