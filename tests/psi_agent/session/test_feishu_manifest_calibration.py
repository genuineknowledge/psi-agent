"""The manifest, checked against what production actually calls.

``test_feishu_pack_manifest`` already checks that every declared name exists.
That is not enough, and 2026-09-15 showed why in both directions at once. The
manifest had been written from a 3-hour traffic window months earlier; measured
against production's 232 registered names and 24h of ``Executing tool:`` lines:

* it declared ``serper_google_search``, which production cannot register at all
  (``search.py`` generates it at import via ``@mcp(keep=...)`` and the serper
  package fails to install there) — and the model, seeing the name in its array,
  called it twice and got ``Tool not found`` both times;
* it omitted **25 tools that were being called**, ``python_run`` among them at 83
  calls — the third hottest tool in the deployment after ``bash`` and
  ``feishu_api``. Shipping the manifest as it stood would have taken them off the
  model's array on the turn the file landed.

Both failures come from the same place: the list was measured once and then
stopped being measured, and nothing compared it to traffic. So this file pins the
*shape* of the calibration rather than the names in it — a hardcoded expected
list is what the old manifest already was, and would restate the bug as a
criterion (it would keep passing while drifting, then point somewhere wrong).
The hot-path names are asserted individually because those are the ones whose
absence is a capability regression a reader can recognise.
"""

from __future__ import annotations

from pathlib import Path

from psi_agent.session.tool_exposure import DISCOVERY_TOOLS, MANIFEST_NAME, parse_manifest_text

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FEISHU_TOOLS = _REPO_ROOT / "agents" / "feishu" / "tools"

# The tools 2026-09-15 production traffic ran through most, with their 24h call
# counts. Not the whole hot set — the ones whose omission is a *capability* story
# rather than a volume one, so a reader can tell what breaks. ``fetch`` and
# ``bash`` are here as the baseline the others are ranked against.
_HOT_IN_PRODUCTION = {
    "bash": 575,
    "feishu_api": 165,
    "python_run": 83,
    "fetch": 69,
    "feishu_doc_export": 57,
    "feishu_doc_update_block": 57,
    "background_output": 34,
    "write_word_from_markdown": 27,
}

# Tools that were called in the same window and had no line in the manifest
# before this calibration. Kept as a named group because each is a chain the
# model cannot finish without: authorisation recovery, background task readback,
# and the deployment's actual web search.
_ADDED_FROM_TRAFFIC = {
    "x_search",
    "feishu_file_download",
    "feishu_auth_request",
    "feishu_auth_collect",
    "feishu_auth_check",
    "feishu_auth_env_check",
    "background_start",
    "background_stop",
    "background_list",
    "memory_add",
    "history_recall",
}


def _declared() -> frozenset[str]:
    return parse_manifest_text((_FEISHU_TOOLS / MANIFEST_NAME).read_text(encoding="utf-8"))


def test_the_hot_tools_are_declared() -> None:
    """A tool this hot that is not in the array costs a ``tool_search`` round trip
    on the turn that needs it, every session. ``python_run`` was the miss that
    mattered: 83 calls, absent from the pre-calibration list."""
    declared = _declared()
    missing = sorted(name for name in _HOT_IN_PRODUCTION if name not in declared)
    assert missing == [], f"生产热点工具不在清单里: {[(n, _HOT_IN_PRODUCTION[n]) for n in missing]}"


def test_the_traffic_measured_additions_stay_declared() -> None:
    """These were added *because* traffic showed them in use. A later edit dropping
    one would silently re-open the gap this calibration closed."""
    dropped = sorted(_ADDED_FROM_TRAFFIC - _declared())
    assert dropped == [], f"按实测流量补进来的工具又被删了: {dropped}"


def test_the_unregisterable_search_tool_is_not_declared() -> None:
    """``serper_google_search`` is generated at import and its dependency does not
    install in production, so declaring it hands the model a name that resolves to
    ``Tool not found``. A ghost the existence check cannot catch: the repo really
    does generate it, which is why this is a separate criterion from
    ``test_feishu_pack_manifest``'s."""
    assert "serper_google_search" not in _declared(), (
        "serper_google_search 生产装不上 (实测两次 Tool not found), 清单声明它等于让模型调用必失败"
    )
    assert "x_search" in _declared(), "生产的联网搜索真名是 x_search, 删掉 serper 后必须留下它"


def test_the_manifest_is_still_a_narrowing() -> None:
    """The calibration grew the manifest by 24 names, and a manifest that keeps
    growing toward the full surface stops buying anything. 232 registered tools in
    production, 89 declared: the check is a ratio so both sides can move."""
    declared = len(_declared())
    assert declared < 120, f"清单已涨到 {declared} 条, 收窄的意义在流失"
    assert declared > 60, f"清单只有 {declared} 条, 热点链路多半被砍掉了"


def test_discovery_stays_reachable() -> None:
    """The escape hatch. With 143 of 232 tools out of the array, ``tool_search`` is
    how the model reaches them; the kernel exposes it unconditionally, and the
    manifest naming it too is what makes the file readable on its own."""
    assert _declared() >= DISCOVERY_TOOLS, "清单必须自带发现工具, 否则收窄就从「难发现」变成「不可达」"
