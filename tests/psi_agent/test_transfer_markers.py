"""Cross-layer consistency of the ``[SEND:]`` / ``[RECV:]`` marker regexes.

Three call sites parse these markers (upload scanner, bubble strip, attachment
projection) in two packages that cannot import each other. These tests pin the
*relationships* between them rather than just the current patterns, so that
editing one regex without the other fails here instead of in production, where
the symptom is a silently undelivered file or a leaked absolute path.
"""

from __future__ import annotations

from psi_agent._transfer_markers import RECV_RE, SEND_RE, TRANSFER_MARKER_RE, send_paths
from psi_agent.channel._markers import SEND_RE as CHANNEL_SEND_RE
from psi_agent.session.history_display import (
    extract_send_paths,
    strip_transfer_markers,
)

# Markers that must be detected, with the path the caller should receive.
ACCEPTED: list[tuple[str, str]] = [
    ("[SEND:/tmp/a.docx]", "/tmp/a.docx"),
    ("[ SEND:/tmp/a.docx ]", "/tmp/a.docx"),
    ("[SEND :/tmp/a.docx]", "/tmp/a.docx"),
    ("[\tSEND\t:/tmp/a.docx\t]", "/tmp/a.docx"),
    (r"[ SEND:D:\Haitun Agent\report.xlsx ]", r"D:\Haitun Agent\report.xlsx"),
    ("[SEND:relative/path.md]", "relative/path.md"),
    # Case variants: models write the keyword however they like (PR #599).
    ("[Send:/tmp/a.docx]", "/tmp/a.docx"),
    ("[ send: /tmp/a.docx ]", "/tmp/a.docx"),
]

# Text that must NOT yield a path (either no match, or an empty one that the
# extractor drops).
REJECTED: list[str] = [
    "[SEND:]",
    "[ SEND: ]",
    "[SEND]",
    "[RECV:/tmp/a.png]",
    "no marker at all",
    "[SEND:\n/tmp/a.docx\n]",
]


def test_channel_reexports_the_shared_pattern() -> None:
    """The Channel must not hold its own copy of the pattern."""
    assert CHANNEL_SEND_RE is SEND_RE


def test_accepted_markers_agree_across_layers() -> None:
    for text, expected in ACCEPTED:
        assert send_paths(text) == [expected], text
        assert extract_send_paths(text) == [expected], text
        assert strip_transfer_markers(text) == "", text


def test_rejected_markers_agree_across_layers() -> None:
    for text in REJECTED:
        assert send_paths(text) == [], text
        assert extract_send_paths(text) == [], text


def test_path_never_spans_a_newline() -> None:
    r"""A marker broken across lines must not match.

    Allowing ``\n`` in the path class makes the *strip* pattern able to swallow
    whole paragraphs between a stray ``[SEND:`` and a far-away ``]``, deleting
    real reply text from the bubble.
    """
    text = "文件已生成\n[SEND:\n/tmp/a.docx\n]"
    assert send_paths(text) == []
    assert extract_send_paths(text) == []
    assert strip_transfer_markers(text) == text.strip()


def test_stray_open_bracket_does_not_eat_prose() -> None:
    """An unclosed marker must leave the following lines intact."""
    text = "报表在这里 [SEND: 第一段\n第二段 ] 结束"
    assert strip_transfer_markers(text) == text.strip()


def test_empty_marker_does_not_swallow_a_following_real_marker() -> None:
    """Regression: ``[SEND:]`` used to consume the next real marker.

    With a ``+`` quantifier the engine cannot match ``[SEND:]`` at its own
    ``]``, so it ran on to the next ``]`` in the message and captured the
    intervening prose *plus* the following marker as one bogus path — losing a
    file the user was told they would receive.
    """
    text = "报表好了[SEND:] 正式的在这里 [SEND:/tmp/real.docx]"
    assert send_paths(text) == ["/tmp/real.docx"]
    assert extract_send_paths(text) == ["/tmp/real.docx"]


def test_strip_covers_everything_detect_accepts() -> None:
    """Strip ⊇ detect: an uploaded file's marker is never left in the bubble."""
    for text, _ in ACCEPTED:
        body = f"见附件\n{text}"
        assert extract_send_paths(body), body
        assert strip_transfer_markers(body) == "见附件", body


def test_recv_and_send_share_one_shape() -> None:
    """Both directions tolerate the same padding, so neither leaks a path."""
    for marker, pattern in (("SEND", SEND_RE), ("RECV", RECV_RE)):
        for text in (f"[{marker}:/tmp/a.bin]", f"[ {marker}:/tmp/a.bin ]", f"[{marker} :/tmp/a.bin]"):
            assert pattern.fullmatch(text), text
            assert TRANSFER_MARKER_RE.fullmatch(text), text


def test_recv_and_send_share_case_insensitivity() -> None:
    """Case tolerance must not be SEND-only, or a ``[Recv:…]`` path leaks."""
    for marker, pattern in (("Send", SEND_RE), ("recv", RECV_RE)):
        text = f"[{marker}:/tmp/a.bin]"
        assert pattern.fullmatch(text), text
        assert TRANSFER_MARKER_RE.fullmatch(text), text
