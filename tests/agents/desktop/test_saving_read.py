# ruff: noqa: RUF001  # 断言的是「页面原文必须原样透传」, 所以下面的全角标点是**数据**而不是标点错误。
"""`saving_read` 的回归判据 —— 页面事实层。

三层判据, 按重要性排:

1. **"读不到" != "没有"**。同一段选择器读到 0 张券有两种成因: 券包**真的空**(模块容器
   还在、里面没券), 还是**页面结构变了 / 根本不是那一页**(连容器都找不到)。两者在计数上
   完全一样, 只有 `markers` 能区分。所以本文件最重要的一条不变式是:

       **凡是 `ok=false` 的返回, 都不许带 `count` / `coupons` 字段。**

   少一个字段只是少一个信息; 多一个 `count: 0` 就是一条会被下游当真的假事实 —— 这与本仓
   facts 契约里「`MISSING` 不得被静默填成 `false`」是同一条纪律。
2. **事实与判定分开**。工具只交页面上写着的东西(`折 9.5` / `满49可用` / 券编号), 原样透传,
   不做归一、不给建议。能不能用在这单上、和国补怎么叠加、到手多少, 全是本体的活。
3. **不导航、不重试**。导航是 agent 的活(它看得见登录墙, 也要在验证码前停下); 本工具只读
   "现在这一页", 读不成一律降级成 `unknown`。

测试全程不碰真实浏览器: `_browser_eval.evaluate` 被换成一个喂固定页面结果的替身。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

# 运行期靠同级 conftest 把 tools 目录挂上 sys.path(裸名导入); ty 不认那个插入,
# 只能按包路径解析。与 test_saving_login.py 同套写法。
if TYPE_CHECKING:
    from agents.desktop.tools import _browser_eval
    from agents.desktop.tools import saving_read as _saving_read
else:
    import _browser_eval
    import saving_read as _saving_read

WORKSPACE_ROOT = Path(__file__).resolve().parents[3] / "agents" / "desktop"

_WALLET_URL = "https://quan.jd.com/user_quan.action"

# 真实的券包页面长这样(选择器与字段名都取自实际页面)。
_COUPON_FULL = {
    "unit": "¥",
    "amount": "5",
    "kind": "东券",
    "threshold": "满49可用",
    "stackable": "可叠加",
    "validity": "2026.09.16-2026.09.19",
    "restrictions": [{"label": "限品类", "value": "家用电器"}, {"label": "券编号", "value": "ABC123"}],
    "use_url": "//search.jd.com/Search?coupon_batch=1&coupon_id=2",
    "expired": False,
}
# 折扣券: 门槛里带封顶。**刻意保留原样** —— 归一成金额是本体的事。
_COUPON_DISCOUNT = {
    "unit": "折",
    "amount": "9.5",
    "kind": "东券",
    "threshold": "满1可用，最多减￥100",
    "stackable": "",
    "validity": "2026.08.14 10:44-2026.09.30 23:59",
    "restrictions": [],
    "use_url": "",
    "expired": False,
}


def _page_text(**over: Any) -> str:
    """拼一段 `browser_evaluate` 会拿回来的文本(含 MCP 常见的 `### Result` 前缀)。"""
    payload: dict[str, Any] = {
        "url": _WALLET_URL,
        "title": "我的京东--优惠券",
        "markers": {".mod-coupon": 1, ".coupon-items": 1},
        "count": 0,
        "coupons": [],
    }
    payload.update(over)
    return "### Result\n" + json.dumps(payload, ensure_ascii=False)


async def _call(**kwargs: Any) -> dict[str, Any]:
    return json.loads(await _saving_read.saving_read(**kwargs))


@pytest.fixture
def page(monkeypatch: pytest.MonkeyPatch) -> Any:
    """把"读页面"换成喂一段固定结果 —— 测试不启动浏览器、不连 MCP。"""

    def _install(result: str | Exception) -> None:
        async def _fake_evaluate(function: str) -> str:
            assert function.strip(), "生成的 JS 不该是空的"
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(_browser_eval, "evaluate", _fake_evaluate)

    return _install


# --------------------------------------------------------------------------- #
# 1. 最重要的一条: 读不到 != 没有
# --------------------------------------------------------------------------- #


async def test_a_populated_wallet_comes_back_as_facts(page: Any) -> None:
    page(_page_text(count=2, coupons=[_COUPON_FULL, _COUPON_DISCOUNT]))

    payload = await _call(platform="jd", target="coupons")
    assert payload["ok"] is True
    assert payload["count"] == 2
    assert payload["empty"] is False
    assert payload["source"] == "saving_read:jd:coupons"
    assert payload["verified_at"], "带核验日期, 下游才知道这份选择器多新"


async def test_an_empty_wallet_is_a_fact_not_a_failure(page: Any) -> None:
    """容器在、券不在 —— 这就是「券包为空」这条事实, 不是读取失败。"""
    page(_page_text(count=0, coupons=[]))

    payload = await _call(platform="jd", target="coupons")
    assert payload["ok"] is True
    assert payload["empty"] is True
    assert payload["count"] == 0
    assert "事实" in payload["note"]


async def test_missing_markers_is_unknown_and_must_not_look_empty(page: Any) -> None:
    """**最关键的一条**: 容器都找不到, 说明结构变了 —— 绝不能报成「券包为空」。"""
    page(_page_text(markers={}, count=0, coupons=[]))

    payload = await _call(platform="jd", target="coupons")
    assert payload["ok"] is False
    assert payload["reason"] == "page_shape_changed"
    assert payload["missing_markers"] == [".mod-coupon", ".coupon-items"]
    assert "count" not in payload
    assert "coupons" not in payload
    assert "空" in payload["note"]


async def test_partial_markers_still_count_as_shape_changed(page: Any) -> None:
    """两个容器缺一个也算 —— 只要有一条读不出, 就不能断言"没有券"。"""
    page(_page_text(markers={".mod-coupon": 1}, count=0))

    payload = await _call(platform="jd", target="coupons")
    assert payload["reason"] == "page_shape_changed"
    assert payload["missing_markers"] == [".coupon-items"]


#: 每一种"读不成"的情形。它们都必须**只报原因, 不报事实**。
FAILURE_CASES = [
    ("shape", _page_text(markers={}, count=0), "page_shape_changed"),
    ("login", _page_text(url="https://passport.jd.com/new/login.aspx"), "logged_out"),
    ("other_page", _page_text(url="https://www.jd.com/"), "wrong_page"),
    ("no_url", _page_text(url=""), "wrong_page"),
    ("unparsable", "这不是 JSON", "unparsable_extract"),
]


@pytest.mark.parametrize(("name", "text", "reason"), FAILURE_CASES, ids=[c[0] for c in FAILURE_CASES])
async def test_no_failure_ever_reports_fact_fields(page: Any, name: str, text: str, reason: str) -> None:
    """**不变式**: `ok=false` 一律不带 `count` / `coupons` / `empty`。

    这就是「把读不到说成没有」的防线: 下游只看字段在不在, 不会被一个 0 骗到。
    """
    page(text)

    payload = await _call(platform="jd", target="coupons")
    assert payload["ok"] is False, name
    assert payload["reason"] == reason, name
    for field in ("count", "coupons", "empty"):
        assert field not in payload, f"{name} 的返回里不该有 {field}"


async def test_browser_failures_also_report_no_facts(page: Any) -> None:
    page(_browser_eval.BrowserEvalError("MCP 断了"))

    payload = await _call(platform="jd", target="coupons")
    assert payload["ok"] is False
    assert payload["reason"] == "browser_unavailable"
    assert "count" not in payload


async def test_a_closed_window_stops_and_does_not_reopen(page: Any) -> None:
    """用户关掉窗口是要被尊重的: 报原因、要求停下告知用户, 不擅自重开。"""
    page(_browser_eval.BrowserGoneError("浏览器窗口已被关闭"))

    payload = await _call(platform="jd", target="coupons")
    assert payload["ok"] is False
    assert payload["reason"] == "browser_closed"
    assert "停下" in payload["note"]
    assert "count" not in payload


# --------------------------------------------------------------------------- #
# 2. 事实与判定分开: 页面写什么就交什么, 原样
# --------------------------------------------------------------------------- #


async def test_field_values_pass_through_unchanged(page: Any) -> None:
    """折扣券的门槛里带封顶, 同样原样透传 —— 工具不做归一、不猜金额。"""
    page(_page_text(count=2, coupons=[_COUPON_FULL, _COUPON_DISCOUNT]))

    coupons = (await _call(platform="jd", target="coupons"))["coupons"]
    assert coupons[0] == _COUPON_FULL
    assert coupons[1] == _COUPON_DISCOUNT
    assert coupons[1]["unit"] == "折"
    assert "最多减" in coupons[1]["threshold"]


async def test_the_note_sends_judgement_to_the_ontology(page: Any) -> None:
    """越权推断最容易发生在刚拿到原始数据的那一刻, 所以 note 必须把线画出来。"""
    page(_page_text(count=1, coupons=[_COUPON_FULL]))

    note = (await _call(platform="jd", target="coupons"))["note"]
    assert "本体" in note
    assert "不要在这里推断" in note


async def test_malformed_coupon_entries_do_not_break_the_read(page: Any) -> None:
    page(_page_text(count=3, coupons=[_COUPON_FULL, "垃圾", None]))

    payload = await _call(platform="jd", target="coupons")
    assert payload["ok"] is True
    assert payload["count"] == 1
    assert payload["coupons"] == [_COUPON_FULL]


# --------------------------------------------------------------------------- #
# 3. 登录墙 / 走错页: 都要给出下一步该去哪
# --------------------------------------------------------------------------- #


async def test_a_login_redirect_points_at_the_gate(page: Any) -> None:
    page(_page_text(url="https://passport.jd.com/new/login.aspx"))

    payload = await _call(platform="jd", target="coupons")
    assert payload["reason"] == "logged_out"
    assert payload["gate"] == "https://order.jd.com/center/list.action"
    assert "saving_login" in payload["note"]


async def test_a_wrong_page_hands_back_the_url_to_open(page: Any) -> None:
    page(_page_text(url="https://www.jd.com/"))

    payload = await _call(platform="jd", target="coupons")
    assert payload["reason"] == "wrong_page"
    assert payload["expected"]["url"] == _WALLET_URL
    assert payload["page"]["url"] == "https://www.jd.com/"


async def test_a_neighbouring_path_on_the_same_host_is_not_the_page(page: Any) -> None:
    page(_page_text(url="https://quan.jd.com/somewhere_else.action"))

    payload = await _call(platform="jd", target="coupons")
    assert payload["reason"] == "wrong_page"
    assert payload["basis"].startswith("path:")


# --------------------------------------------------------------------------- #
# 4. 读定义的发现与配置错
# --------------------------------------------------------------------------- #


async def test_listing_targets_tells_the_agent_where_to_navigate() -> None:
    payload = await _call(platform="jd")

    assert payload["ok"] is True
    row = next(r for r in payload["targets"] if r["target"] == "coupons")
    assert row["url"] == _WALLET_URL
    assert row["verified_at"]
    assert "amount" in row["gives"]
    assert payload["gate"]


async def test_a_platform_without_reads_says_how_to_add_one() -> None:
    """淘宝目前没有可读页面定义 —— 这是缺配置, 不是缺能力。"""
    payload = await _call(platform="taobao", target="coupons")
    assert payload["ok"] is False
    assert "reads" in payload["reason"]
    assert "taobao.yaml" in payload["note"]


async def test_unknown_target_lists_known_ones() -> None:
    payload = await _call(platform="jd", target="nope")
    assert payload["ok"] is False
    assert payload["known_targets"] == ["coupons"]


async def test_unknown_platform_lists_available() -> None:
    payload = await _call(platform="拼多多", target="coupons")
    assert payload["ok"] is False
    assert "jd" in " ".join(payload["known"])


@pytest.mark.parametrize(
    "broken",
    [
        {"markers": [".a"], "fields": {"x": {"sel": ".x"}}},  # 缺 item
        {"item": ".a", "fields": {"x": {"sel": ".x"}}},  # 缺 markers
        {"item": ".a", "markers": [".m"]},  # 缺 fields
        {"item": ".a", "markers": [], "fields": {"x": {"sel": ".x"}}},  # markers 空
    ],
)
def test_incomplete_read_definitions_are_a_config_error(broken: dict[str, Any]) -> None:
    """markers 是承重的 —— 缺了就没法区分"空"和"读不到", 只能算配置错。"""
    assert _saving_read._spec(broken) is None


def test_spec_drops_documentation_only_keys() -> None:
    """yaml 里的 `note` 是给人看的, 不该混进送给页面执行的 spec。"""
    spec = _saving_read._spec({"item": ".a", "markers": [".m"], "fields": {"x": {"sel": ".x", "note": "说明文字"}}})
    assert spec is not None
    assert spec["fields"]["x"] == {"sel": ".x"}


def test_a_field_without_a_selector_is_ignored_rather_than_breaking_the_read() -> None:
    spec = _saving_read._spec(
        {"item": ".a", "markers": [".m"], "fields": {"x": {"note": "忘了写 sel"}, "y": {"sel": ".y"}}}
    )
    assert spec is not None
    assert set(spec["fields"]) == {"y"}


async def test_an_incomplete_definition_fails_before_touching_the_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置错要**在碰页面之前**就失败 —— 否则会拿一个半截 spec 去读, 读回来还像成功。"""

    async def _explode(function: str) -> str:
        raise AssertionError("不该走到页面读取")

    monkeypatch.setattr(_browser_eval, "evaluate", _explode)
    monkeypatch.setattr(_saving_read, "_spec", lambda _read: None)

    payload = await _call(platform="jd", target="coupons")
    assert payload["ok"] is False
    assert "不完整" in payload["reason"]


# --------------------------------------------------------------------------- #
# 5. 出厂内容: 读定义与生成器
# --------------------------------------------------------------------------- #


def test_shipped_jd_read_definition_is_complete() -> None:
    """jd.yaml 的读定义必须自洽 —— 改坏了要让测试红, 而不是等到线上读到空。"""
    data = yaml.safe_load((WORKSPACE_ROOT / "platforms" / "jd.yaml").read_text(encoding="utf-8"))
    read = data["reads"]["coupons"]

    assert read["url"] and read["host"] and read["path_prefix"]
    assert read["verified_at"], "页面会改版, 读定义必须带核验日期"
    assert _saving_read._spec(read) is not None
    # 事实契约的字段名: 改这里等于改与本体之间的契约
    assert set(read["fields"]) == {
        "unit",
        "amount",
        "kind",
        "threshold",
        "stackable",
        "validity",
        "restrictions",
        "use_url",
        "expired",
    }
    assert set(read["markers"]) >= {".mod-coupon"}  # 空券包时仍然存在的模块容器


def test_generated_js_carries_the_declared_spec() -> None:
    """选择器是数据, 所以它们必须真的进到送给页面的那段 JS 里。"""
    data = yaml.safe_load((WORKSPACE_ROOT / "platforms" / "jd.yaml").read_text(encoding="utf-8"))
    spec = _saving_read._spec(data["reads"]["coupons"])
    assert spec is not None

    js = _saving_read._build_js(spec)
    marker = "JSON.stringify(run("
    embedded = json.loads(js[js.index(marker) + len(marker) : js.rindex("))")])

    assert embedded["item"] == ".coupon-item"
    assert embedded["fields"]["amount"] == {"sel": ".c-price strong"}
    assert embedded["fields"]["restrictions"] == {"sel": ".c-range .range-item", "many": True, "kv": ["label", ".txt"]}
    assert embedded["fields"]["expired"] == {"sel": ".overdue-site", "presence": True}


def test_generated_js_escapes_selectors_instead_of_breaking_on_quotes() -> None:
    """选择器里的引号由 json.dumps 负责转义 —— 手写拼串在这里就会碎。"""
    spec = _saving_read._spec({"item": 'div[data-x="1"]', "markers": [".m"], "fields": {"x": {"sel": 'a[href="y"]'}}})
    assert spec is not None

    js = _saving_read._build_js(spec)
    marker = "JSON.stringify(run("
    embedded = json.loads(js[js.index(marker) + len(marker) : js.rindex("))")])
    assert embedded["item"] == 'div[data-x="1"]'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', 1),
        ('### Result\n{"a": 1}\n', 1),
        ('```json\n{"a": 1}\n```', 1),
        ("no json", None),
        ("[1, 2]", None),
        ('{"a": ', None),
    ],
)
def test_payload_parsing_tolerates_mcp_wrapping(text: str, expected: int | None) -> None:
    """`browser_evaluate` 的包装形式随上游版本变, 解析要宽松, 但不能把非 JSON 认成 JSON。"""
    data = _saving_read._parse_payload(text)
    assert (data or {}).get("a") == expected
    if expected is None:
        assert data is None


async def test_return_json_false_gives_plain_text(page: Any) -> None:
    page(_page_text(count=1, coupons=[_COUPON_FULL]))

    text = await _saving_read.saving_read(platform="jd", target="coupons", return_json=False)
    assert "coupons" in text
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)
