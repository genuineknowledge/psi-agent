"""Figure/table caption numbering for Feishu docs — "图 3：…" that is actually the third.

Captions used to be a free string the agent typed, so the number in "图2：缺陷分析" was
whatever the model remembered writing last. Across several tool calls (and across turns,
sessions, or a doc a person had already edited) that memory is unreliable, and reports
came back with two 图2 and no 图4, or numbers that disagreed with the prose referring to
them.

The number is derived from the document instead of remembered: read what the doc already
says, find the highest 图/表 number in it, and continue from there. That makes the
sequence correct under everything that breaks a counter — a restarted process, a second
agent writing into the same doc, a human who inserted a figure by hand — because the doc
is the one place all of those show up.

Figures and tables get independent sequences (图 1, 图 2 alongside 表 1, 表 2), per the
academic convention the caller asked for.
"""

# RUF001-003: this module is about Chinese caption typography, so the full-width colon in
# "图 3：…" and the full-width punctuation in the caption regex are the intended
# characters, not ASCII typos — substituting ASCII would write the wrong captions and
# stop matching the ones already in users' documents.
# ruff: noqa: RUF001, RUF002, RUF003

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import _feishu_impl as _f  # noqa: E402

FIGURE = "图"
TABLE = "表"

# CJK numerals appear in hand-written captions ("图一"), so they count toward the
# sequence even though we always *write* Arabic digits.
_CJK_DIGITS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}

# "图 3：…", "图3.", "(图 5)", "如图 7 所示" — a 图/表 followed by a number and then a
# delimiter. The trailing delimiter is what keeps "图书馆"/"表现" (ordinary words starting
# with these characters) out; no *leading* guard, deliberately, so in-prose references
# ("详见图 7") count too.
#
# The bias is toward over-counting on purpose. Missing an existing number produces a
# duplicate — the exact defect this module exists to fix — while counting one number too
# many merely leaves a gap in the sequence. Given a choice between two 图 3 and no 图 4,
# the gap is the cheaper error.
_CAPTION_RE = re.compile(
    r"(图|表)\s*([0-9]{1,3}|[一二三四五六七八九十]{1,3})\s*(?=[：:.、．,，。;；!！?？)）\]】\s]|$)",
    re.MULTILINE,
)


def _cjk_to_int(text: str) -> int:
    """ "十二" → 12, for the range a caption plausibly uses (1-99)."""
    if "十" not in text:
        return sum(_CJK_DIGITS.get(ch, 0) for ch in text[:1]) or 0
    tens, _, ones = text.partition("十")
    high = _CJK_DIGITS.get(tens, 1) if tens else 1
    low = _CJK_DIGITS.get(ones, 0) if ones else 0
    return high * 10 + low


def highest_number(content: str, kind: str) -> int:
    """The largest existing ``图``/``表`` number in a document's text (0 when none)."""
    highest = 0
    for found_kind, digits in _CAPTION_RE.findall(content or ""):
        if found_kind != kind:
            continue
        value = int(digits) if digits.isdigit() else _cjk_to_int(digits)
        highest = max(highest, value)
    return highest


async def next_number(document_id: str, kind: str, user_key: str = "", identity: str = "") -> dict[str, Any]:
    """The number this caption should carry: one past the highest already in the doc.

    Returns ``{"ok": True, "number": n}``, or ``{"ok": False, "reason": …}`` when the
    document couldn't be read. A failure here is not a failure of the caller: the chart
    or table still belongs in the doc, so callers fall back to writing the caption
    unnumbered and report why rather than aborting a good chart over a missing number.
    """
    doc = document_id.strip()
    if not doc:
        return {"ok": False, "reason": "no document_id"}
    read = await _f.read_doc_for_captions(doc, user_key=user_key, identity=identity)
    if not read.get("ok"):
        return {"ok": False, "reason": read.get("message", "could not read the document")}
    return {"ok": True, "number": highest_number(read.get("content", ""), kind) + 1}


def format_caption(kind: str, number: int, text: str, panel_titles: list[str] | None = None) -> str:
    """ "图 3：各区域经营概况" — plus a "(a) … ; (b) …" line when the figure has panels.

    The panel line is what makes a combined figure readable: the reader needs to know
    which sub-plot is which, and the caption is where a paper puts that.
    """
    body = (text or "").strip()
    head = f"{kind} {number}" if number > 0 else kind
    caption = f"{head}：{body}" if body else head
    named = [t.strip() for t in (panel_titles or []) if t and t.strip()]
    if len(named) > 1:
        tags = "；".join(f"({chr(ord('a') + i)}) {title}" for i, title in enumerate(named))
        caption = f"{caption}\n{tags}"
    return caption


def strip_own_number(text: str, kind: str) -> str:
    """Drop a leading "图2：" the caller wrote by hand, so it isn't numbered twice.

    Agents have been told for a while to put "图N：" in the caption themselves, and that
    habit (plus older skill docs in the wild) outlives this change. Left alone it would
    produce "图 3：图2：缺陷分析", so the hand-written prefix is removed and the
    doc-derived number is authoritative.
    """
    stripped = (text or "").strip()
    match = re.match(rf"^{kind}\s*(?:[0-9]{{1,3}}|[一二三四五六七八九十]{{1,3}})\s*[：:.、．]\s*", stripped)
    return stripped[match.end() :].strip() if match else stripped
