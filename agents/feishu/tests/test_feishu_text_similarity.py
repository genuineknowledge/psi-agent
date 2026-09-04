"""Validate the deterministic feishu_text_similarity tool (copy-paste detection).

CEO's first worry is ctrl+C / ctrl+V 应付 on the TODO board. The answer has to be a
*deterministic* similarity number plus the matched fragment as evidence, so it is
implemented as a pure function (longest-common-substring normalization) behind a thin
async tool shell. These tests pin the metric itself - identical ~ 1.0, disjoint ~ 0.0,
partial in the middle with the right fragment - and the default threshold that decides
`similar`, because a silent change to the metric would make every copy-paste hit either
fire on innocuous text or miss real pastes.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

WORKSPACE_ROOT = Path(__file__).resolve().parents[1]
TOOLS_DIR = WORKSPACE_ROOT / "tools"

if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_sim = importlib.import_module("feishu_text_similarity")


def test_identical_text_scores_one_with_full_fragment() -> None:
    text = "训模型 调参 上线 观察留存"
    similarity, fragment = _sim.text_similarity(text, text)
    assert similarity == 1.0
    assert fragment == text


def test_disjoint_text_scores_zero() -> None:
    similarity, fragment = _sim.text_similarity("a" * 20, "b" * 20)
    assert similarity == 0.0
    assert fragment == ""


def test_partial_overlap_uses_longest_common_substring() -> None:
    # "abcdef" vs "xyzabc123": the longest common run is "abc" (len 3)
    similarity, fragment = _sim.text_similarity("abcdef", "xyzabc123")
    assert fragment == "abc"
    assert similarity == 2 * 3 / (6 + 9)


def test_almost_identical_scores_above_threshold() -> None:
    # a 100-char copy with one trailing difference must still count as similar
    base = "某功能上线" * 20
    edited = base + "x"
    similarity, _ = _sim.text_similarity(base, edited)
    assert similarity >= _sim.DEFAULT_THRESHOLD


def test_empty_inputs_score_zero() -> None:
    similarity, fragment = _sim.text_similarity("", "")
    assert similarity == 0.0
    assert fragment == ""


def test_default_threshold_is_085() -> None:
    assert _sim.DEFAULT_THRESHOLD == 0.85


def test_threshold_decides_similar_flag() -> None:
    text = "复制的条目文本" * 20
    same, _ = _sim.text_similarity(text, text)
    assert same >= 0.85
    disjoint, _ = _sim.text_similarity("a" * 30, "b" * 30)
    assert disjoint < 0.85


@pytest.mark.anyio
async def test_tool_shell_returns_json_with_similar_flag() -> None:
    payload = json.loads(await _sim.feishu_text_similarity("a" * 10, "a" * 10))
    assert payload["similar"] is True
    assert payload["similarity"] == 1.0
    assert payload["matched_fragment"] == "a" * 10

    payload2 = json.loads(await _sim.feishu_text_similarity("aaaa", "bbbb"))
    assert payload2["similar"] is False
    assert payload2["matched_fragment"] == ""
