from __future__ import annotations

from psi_agent.channel._markers import SendMarkerScanner, encode_input
from psi_agent.channel._types import FileChunk, TextChunk


def test_encode_input_empty():
    assert encode_input([]) == ""


def test_encode_input_text_only():
    assert encode_input([TextChunk("hello")]) == "hello"


def test_encode_input_file_becomes_recv_marker():
    result = encode_input([FileChunk("/home/user/file.txt"), TextChunk("hello")])
    assert result == "[RECV:/home/user/file.txt]\nhello"


def test_encode_input_joins_with_newline():
    assert encode_input([TextChunk("a"), TextChunk("b")]) == "a\nb"


def test_scanner_no_marker_returns_empty():
    scanner = SendMarkerScanner()
    assert scanner.feed("just text, no markers") == []


def test_scanner_detects_send_marker():
    scanner = SendMarkerScanner()
    assert scanner.feed("Here is [SEND:/tmp/output.py] the file. more text") == [FileChunk("/tmp/output.py")]


def test_scanner_detects_spaced_send_marker():
    scanner = SendMarkerScanner()
    assert scanner.feed("Here is [ SEND:/tmp/output.py ] the file. more text") == [FileChunk("/tmp/output.py")]


def test_scanner_detects_lowercase_send_marker():
    scanner = SendMarkerScanner()
    assert scanner.feed("Here is [Send:/tmp/output.py] the file. more text") == [FileChunk("/tmp/output.py")]


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


def test_scanner_third_marker_after_trailing_text_regression():
    """A later marker must still be detected after an earlier feed left trailing text.

    The scan pointer must advance by ``base + match.end()`` (base = scan start),
    not ``orig_len + match.end()``; otherwise it overshoots by the trailing-text
    length and slices a subsequent marker mid-token, missing it.
    """
    scanner = SendMarkerScanner()
    assert scanner.feed("[SEND:/a.py]TAIL") == [FileChunk("/a.py")]
    assert scanner.feed("[SEND:/b.py]") == [FileChunk("/b.py")]
    assert scanner.feed("[SEND:/c.py]") == [FileChunk("/c.py")]


def test_scanner_tolerates_whitespace_inside_brackets():
    r"""``[ SEND:/path ]`` must deliver — models write the marker as prose.

    Regression: the strict ``\[SEND:(.+?)\]`` matched nothing here, so the file
    was written, announced in the reply, and silently never uploaded.
    """
    scanner = SendMarkerScanner()
    assert scanner.feed("[ SEND:/tmp/out.docx ]") == [FileChunk("/tmp/out.docx")]


def test_scanner_tolerates_space_before_colon():
    scanner = SendMarkerScanner()
    assert scanner.feed("[SEND :/tmp/a.pdf]") == [FileChunk("/tmp/a.pdf")]


def test_scanner_spaced_and_bare_markers_dedup_to_one_file():
    """Padding is not part of the path, so the same file is emitted once."""
    scanner = SendMarkerScanner()
    assert scanner.feed("[SEND:/tmp/a.docx]") == [FileChunk("/tmp/a.docx")]
    assert scanner.feed("[ SEND:/tmp/a.docx ]") == []


def test_scanner_windows_path_with_spaces_is_preserved():
    """Only the padding is stripped — interior spaces belong to the path."""
    scanner = SendMarkerScanner()
    result = scanner.feed(r"[ SEND:D:\Programs\Haitun Agent\报表.xlsx ]")
    assert result == [FileChunk(r"D:\Programs\Haitun Agent\报表.xlsx")]


def test_scanner_empty_path_is_ignored():
    scanner = SendMarkerScanner()
    assert scanner.feed("[ SEND: ]") == []


def test_scanner_advances_past_ignored_empty_marker():
    """An empty marker must not wedge the scan pointer and hide later files."""
    scanner = SendMarkerScanner()
    assert scanner.feed("[SEND: ] then [SEND:/tmp/b.txt]") == [FileChunk("/tmp/b.txt")]
