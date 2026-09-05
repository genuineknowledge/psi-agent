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
    assert rc.kind is None
    rc2 = ReasoningChunk("x", kind="tool_call")
    assert rc2.kind == "tool_call"


def test_reasoning_chunk_carries_structured_tool_name():
    """工具名必须是独立字段, 不能靠正则从 ``text`` 里抠。

    ``text`` 是 ``[Tool Call: name({...})]``, 参数里可能带括号和引号, 解析是脆的;
    而渲染侧要拿工具名去查白名单别名, 抠错就会 fallback 到兜底文案。
    """
    rc = ReasoningChunk("[Tool Call: read({})]", kind="tool_call", tool_name="read")
    assert rc.tool_name == "read"
    # 旧流没有该字段, 缺省必须是 None 而不是空串: 空串会被当成「有名字但为空」。
    assert ReasoningChunk("x", kind="tool_call").tool_name is None


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
