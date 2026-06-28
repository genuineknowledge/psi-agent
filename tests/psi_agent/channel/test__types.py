from __future__ import annotations

from typing import get_args

from psi_agent.channel._types import FileChunk, InputChunk, OutputChunk, ReasoningChunk, TextChunk


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


def test_reasoning_chunk_construction():
    rc = ReasoningChunk("thinking...")
    assert rc.text == "thinking..."


def test_reasoning_chunk_union_isinstance():
    rc = ReasoningChunk("hmm")
    tc = TextChunk("hi")
    fc = FileChunk("/a.txt")

    assert isinstance(rc, ReasoningChunk)
    assert not isinstance(rc, TextChunk)
    assert not isinstance(rc, FileChunk)
    assert not isinstance(tc, ReasoningChunk)
    assert not isinstance(fc, ReasoningChunk)


def test_input_chunk_excludes_reasoning():
    args = get_args(InputChunk)
    assert FileChunk in args
    assert TextChunk in args
    assert ReasoningChunk not in args


def test_output_chunk_includes_reasoning():
    args = get_args(OutputChunk)
    assert FileChunk in args
    assert TextChunk in args
    assert ReasoningChunk in args
