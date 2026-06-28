from __future__ import annotations

from psi_agent.channel._markers import SendMarkerScanner, encode_input
from psi_agent.channel._types import FileChunk, ReasoningChunk, TextChunk


def test_encode_input_empty():
    assert encode_input([]) == ""


def test_encode_input_text_only():
    assert encode_input([TextChunk("hello")]) == "hello"


def test_encode_input_file_becomes_recv_marker():
    result = encode_input([FileChunk("/home/user/file.txt"), TextChunk("hello")])
    assert result == "[RECV:/home/user/file.txt]\nhello"


def test_encode_input_joins_with_newline():
    assert encode_input([TextChunk("a"), TextChunk("b")]) == "a\nb"


def test_encode_input_ignores_reasoning_chunk():
    assert encode_input([ReasoningChunk("x"), TextChunk("y")]) == "y"


def test_scanner_no_marker_returns_empty():
    scanner = SendMarkerScanner()
    assert scanner.feed("just text, no markers") == []


def test_scanner_detects_send_marker():
    scanner = SendMarkerScanner()
    assert scanner.feed("Here is [SEND:/tmp/output.py] the file. more text") == [FileChunk("/tmp/output.py")]


def test_scanner_dedup_within_feed():
    scanner = SendMarkerScanner()
    assert scanner.feed("[SEND:/a.py] chunk1 [SEND:/a.py] chunk2") == [FileChunk("/a.py")]


def test_scanner_dedup_across_feeds():
    scanner = SendMarkerScanner()
    assert scanner.feed("[SEND:/a.py] first") == [FileChunk("/a.py")]
    assert scanner.feed("[SEND:/a.py] second") == []


def test_scanner_marker_split_across_feeds():
    scanner = SendMarkerScanner()
    assert scanner.feed("here is [SEND:/tm") == []
    assert scanner.feed("p/out.py] end") == [FileChunk("/tmp/out.py")]
