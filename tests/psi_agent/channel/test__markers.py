from __future__ import annotations

from psi_agent.channel._markers import SendMarkerScanner, encode_input, iter_send_paths
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


def test_scanner_ignores_empty_path_marker():
    """``[SEND:]`` 是模型笔误, 不是传输请求。

    Channel 侧对 FileChunk 无空路径过滤 —— Feishu/Telegram 的 _send_file 会拿
    空 source path 直接发起上传。故空路径必须在解码处就被丢掉。
    """
    scanner = SendMarkerScanner()
    assert scanner.feed("oops [SEND:] nothing here") == []


def test_scanner_ignores_whitespace_only_path_marker():
    scanner = SendMarkerScanner()
    assert scanner.feed("oops [ SEND:   ] nothing here") == []


def test_scanner_still_detects_real_path_after_empty_marker():
    """空标记不得吃掉扫描指针, 后续真实标记仍须被发现。"""
    scanner = SendMarkerScanner()
    assert scanner.feed("[SEND:] then [SEND:/real.py] end") == [FileChunk("/real.py")]


def test_iter_send_paths_yields_path_and_match_end():
    text = "a [SEND:/x.py] b"
    assert list(iter_send_paths(text)) == [("/x.py", text.index("]") + 1)]


def test_iter_send_paths_skips_empty_and_keeps_order():
    paths = [path for path, _ in iter_send_paths("[SEND:/a] [SEND:] [SEND:/b]")]
    assert paths == ["/a", "/b"]


def test_iter_send_paths_unclosed_marker_does_not_swallow_next_line():
    """An unclosed ``[SEND:`` must not eat the real marker on a later line.

    The path class excludes ``\\n`` for this reason: with a newline-permissive
    class the first (unclosed) marker matches all the way to the ``]`` below,
    losing ``/tmp/report.pdf`` and handing ``_send_file`` a multi-line string.
    """
    text = "结果如下 [SEND: 写错了\n真正的文件是 [SEND:/tmp/report.pdf]"
    assert [path for path, _ in iter_send_paths(text)] == ["/tmp/report.pdf"]
