from __future__ import annotations

from psi_agent.channel._types import FileChunk, TextChunk


def test_file_chunk_construction():
    fc = FileChunk("/tmp/foo.txt")
    assert fc.path == "/tmp/foo.txt"


def test_text_chunk_construction():
    tc = TextChunk("hello world")
    assert tc.text == "hello world"


def test_chunk_union_isinstance():
    fc = FileChunk("/a.txt")
    tc = TextChunk("hi")

    assert isinstance(fc, FileChunk)
    assert isinstance(tc, TextChunk)
    assert not isinstance(fc, TextChunk)
    assert not isinstance(tc, FileChunk)
