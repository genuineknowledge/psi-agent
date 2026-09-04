"""feishu_text_similarity - deterministic text similarity for copy-paste detection.

CEO's first worry about the TODO board is ctrl+C / ctrl+V 应付: two people pasting the
same item text (or a copy with a couple of characters changed). This tool answers "are
these two texts suspiciously similar" with a deterministic number and the matched
fragment as evidence - no model, no extra dependency.

Metric: longest-common-substring based normalization,
    similarity = 2 * lcs_len / (len(text_a) + len(text_b))
computed with difflib.SequenceMatcher.find_longest_match (autojunk off, so a long
common run is always found). `matched_fragment` is the matched run itself, so a caller
can show *what* was copied as evidence.

The tool only produces evidence (similarity + fragment). It never rules anything: hits
above the threshold route to the calling skill's 存疑/待跟进 bucket (the skill spells it
with a fullwidth solidus), never to a 失实 verdict - that stays a human-confirmed red
line. Default threshold 0.85 is a suggestion pending review; callers may pass their own.
"""

from __future__ import annotations

import json
from difflib import SequenceMatcher
from typing import Any

DEFAULT_THRESHOLD = 0.85


def text_similarity(text_a: str, text_b: str) -> tuple[float, str]:
    """Return (similarity in [0.0, 1.0], longest common substring fragment).

    Two empty texts score 0.0 with an empty fragment; two identical texts score 1.0
    with the whole text as the fragment.
    """
    a = text_a or ""
    b = text_b or ""
    if not a and not b:
        return (0.0, "")
    matcher = SequenceMatcher(None, a, b, autojunk=False)
    match = matcher.find_longest_match(0, len(a), 0, len(b))
    if match.size <= 0:
        return (0.0, "")
    fragment = a[match.a : match.a + match.size]
    similarity = min(1.0, 2.0 * match.size / (len(a) + len(b)))
    return (similarity, fragment)


async def feishu_text_similarity(text_a: str = "", text_b: str = "", threshold: float = DEFAULT_THRESHOLD) -> str:
    """Return JSON {similarity, similar, matched_fragment} comparing two texts.

    Deterministic longest-common-substring normalized similarity; `similar` is
    similarity >= threshold. The result is evidence only - the caller decides what a
    hit means (the truthfulness skill routes hits to 存疑/待跟进, never to 失实).
    """
    similarity, fragment = text_similarity(text_a, text_b)
    payload: dict[str, Any] = {
        "similarity": round(similarity, 4),
        "similar": similarity >= threshold,
        "matched_fragment": fragment,
    }
    return json.dumps(payload, ensure_ascii=False)
