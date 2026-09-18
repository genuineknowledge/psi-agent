"""`voucher_clues` 的回归判据 —— 地方消费券的线索层。

三层判据, 按重要性排:

1. **只给线索, 不给结论**。返回的是「有哪些页面」, 不是「有什么券」。所以本工具
   **不提取面额/门槛** —— 从标题反推 "满500减50" 是最容易编出来的地方。
2. **日期是承重的**。实测: 合肥专题页最新一条是 2026-04-24, 而当时已是 2026-09。
   不带日期地返回 48 条, 模型会把 2024 年的电影券当成现行的。所以每条都带
   `published` / `age_days` / `clue_freshness`, 且**没日期的排最后**(而不是当最新)。
3. **抓不到就说抓不到**。命中拼图风控 -> `blocked` 并要求停下; 取不到 -> `source_unreachable`;
   城市不在表里 -> `unknown_city`(**不猜拼音** —— 猜出来的代码 404, 而 404 与"这个城市
   没有券"在返回体里长得一样)。

不变式(与 `saving_read` 同源): **凡是 `ok=false` 的返回都不带 `clues` 字段。**
少一个字段只是少一个信息; 多一个空列表就是一条会被当真的假事实。

测试全程不联网: `_fetch` 被换成喂固定页面的替身。
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

# 运行期靠同级 conftest 把 tools 目录挂上 sys.path(裸名导入); ty 不认那个插入,
# 只能按包路径解析。与 test_saving_login.py 同套写法。
if TYPE_CHECKING:
    from agents.desktop.tools import _voucher_sources
    from agents.desktop.tools import voucher_clues as _voucher_clues
else:
    import _voucher_sources
    import voucher_clues as _voucher_clues

WORKSPACE_ROOT = Path(__file__).resolve().parents[3] / "agents" / "desktop"
SOURCES_FILE = WORKSPACE_ROOT / "sources" / "voucher-sources.yaml"

TODAY = date(2026, 9, 18)


def _page(*rows: str) -> str:
    """拼一个像专题页的 HTML: 每条是一段带日期的链接文字。"""
    links = "".join(f'<a href="http://m.hf.bendibao.com/live/{i}.shtm">{row}</a>' for i, row in enumerate(rows))
    return f"<html><body><div class='list'>{links}</div></body></html>"


def _iso(days_ago: int) -> str:
    return (TODAY - timedelta(days=days_ago)).isoformat()


async def _call(**kwargs: Any) -> dict[str, Any]:
    return json.loads(await _voucher_clues.voucher_clues(**kwargs))


@pytest.fixture
def page(monkeypatch: pytest.MonkeyPatch) -> Any:
    """把 `_fetch` 换成喂固定页面 —— 测试不联网。"""

    def _install(html: str | None) -> None:
        async def _fake(url: str, total_seconds: float = 25.0) -> str | None:
            return html

        monkeypatch.setattr(_voucher_clues, "_fetch", _fake)

    return _install


# --------------------------------------------------------------------------- #
# 1. 时效: 日期是承重的
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("age", "expected"),
    [
        (0, "current"),
        (30, "current"),
        (31, "recent"),
        (180, "recent"),
        (181, "stale"),
        (900, "stale"),
        (None, "undated"),
    ],
)
def test_freshness_buckets(age: int | None, expected: str) -> None:
    assert _voucher_clues._freshness(age) == expected


def test_undated_is_its_own_bucket_not_the_newest() -> None:
    """没日期 **不等于** 新 —— 这是本文件最容易被写错的一条。"""
    assert _voucher_clues._freshness(None) == "undated"
    assert _voucher_clues._freshness(None) != "current"


def test_clues_are_sorted_newest_first_and_undated_last() -> None:
    html = _page(
        f"合肥餐饮消费券 {_iso(200)}",
        f"合肥汽车消费券 {_iso(3)}",
        "合肥百货消费券",  # 没日期
    )
    clues = _voucher_clues._parse_clues(html, TODAY)
    assert [c["published"] for c in clues[:2]] == [_iso(3), _iso(200)]
    assert clues[-1]["published"] is None
    assert clues[-1]["clue_freshness"] == "undated"


def test_publish_time_is_stripped_from_the_title() -> None:
    """专题页条目是「标题 + 日期 + 时间」, 时间不该拖进模型上下文。"""
    clues = _voucher_clues._parse_clues(_page(f"2026合肥瑶海区五一餐饮消费券领券方式 {_iso(5)} 10:29"), TODAY)
    assert clues[0]["title"] == "2026合肥瑶海区五一餐饮消费券领券方式"
    assert "10:29" not in clues[0]["title"]


# --------------------------------------------------------------------------- #
# 2. 解析: 只认"链接文字里有消费券"的条目
# --------------------------------------------------------------------------- #


def test_non_coupon_links_are_ignored() -> None:
    html = _page("合肥本地宝首页", f"合肥餐饮消费券 {_iso(5)}", "联系我们")
    clues = _voucher_clues._parse_clues(html, TODAY)
    assert [c["title"] for c in clues] == ["合肥餐饮消费券"]


def test_duplicate_titles_are_collapsed() -> None:
    """同一条券常有「领券方式」「使用方式」两篇, 但完全同名的要去重。"""
    html = _page(f"合肥餐饮消费券 {_iso(5)}", f"合肥餐饮消费券 {_iso(6)}")
    clues = _voucher_clues._parse_clues(html, TODAY)
    assert len(clues) == 1


def test_category_counts_come_from_titles() -> None:
    html = _page("合肥汽车消费券", "合肥餐饮消费券", "合肥餐饮消费券第二期", "合肥百货消费券")
    clues = _voucher_clues._parse_clues(html, TODAY)
    assert _voucher_clues._categories_seen(clues) == {"餐饮": 2, "汽车": 1, "百货": 1}


# --------------------------------------------------------------------------- #
# 3. 风控: 命中就停
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("marker", ["请完成拼图验证以继续访问", "请完成验证", "安全检查中", "人机验证"])
def test_verification_pages_are_detected(marker: str) -> None:
    assert _voucher_clues._is_blocked(f"<html><body>{marker}</body></html>")


def test_a_normal_page_is_not_mistaken_for_a_challenge() -> None:
    assert not _voucher_clues._is_blocked(_page("合肥餐饮消费券 2026-04-24"))


# --------------------------------------------------------------------------- #
# 4. 端到端(离线)
# --------------------------------------------------------------------------- #


async def test_happy_path_returns_clues_with_freshness(page: Any) -> None:
    # 用桶内安全的余量(5 / 100 天), 而不是卡在 30 / 180 边界上 —— 端到端这条走的是真实时钟,
    # 卡边界会因为跑测试的日期不同而随机红。
    page(_page(f"合肥汽车消费券 {_iso(5)}", f"合肥餐饮消费券 {_iso(100)}"))

    data = await _call(city="合肥")
    assert data["ok"] is True
    assert data["query"]["city"] == "合肥"
    assert data["query"]["city_code"] == "hf"
    assert data["paths"]["aggregator"]["ok"] is True
    assert data["totals"]["parsed"] == 2
    assert data["totals"]["by_clue_freshness"] == {"current": 1, "recent": 1, "stale": 0, "undated": 0}
    assert data["categories_seen"] == {"汽车": 1, "餐饮": 1}


async def test_category_is_a_keyword_filter_and_reports_what_it_dropped(page: Any) -> None:
    """过滤是关键词匹配, 不是语义判断 —— 所以要同时报 matched 与 parsed。"""
    page(_page(f"合肥汽车消费券 {_iso(5)}", f"合肥餐饮消费券 {_iso(5)}"))

    data = await _call(city="合肥", category="汽车")
    assert data["totals"]["parsed"] == 2
    assert data["totals"]["matched"] == 1
    assert [c["title"] for c in data["clues"]] == ["合肥汽车消费券"]


async def test_max_results_caps_the_list_but_not_the_counts(page: Any) -> None:
    page(_page(*[f"合肥餐饮消费券第{i}期 {_iso(5)}" for i in range(10)]))

    data = await _call(city="合肥", max_results=3)
    assert len(data["clues"]) == 3
    assert data["totals"]["parsed"] == 10


async def test_the_note_sends_judgement_to_the_ontology(page: Any) -> None:
    """本工具只给线索; 越权推断最容易发生在刚拿到列表的那一刻。"""
    page(_page(f"合肥汽车消费券 {_iso(5)}"))

    note = (await _call(city="合肥"))["note"]
    assert "线索" in note
    assert "本体" in note
    assert "不要在这里算" in note


#: 每一种"三路都拿不到"的情形。它们都必须**只报原因, 不报线索**。
FAILURE_CASES = [
    ("blocked", "<html>请完成拼图验证以继续访问</html>"),
    ("unreachable", None),
]


@pytest.mark.parametrize(("name", "html"), FAILURE_CASES, ids=[c[0] for c in FAILURE_CASES])
async def test_no_failure_ever_reports_clues(page: Any, name: str, html: str | None) -> None:
    """**不变式**: `ok=false` 一律不带 `clues` —— 空的线索列表会被当成"这个城市没有券"。"""
    page(html)

    data = await _call(city="合肥")
    assert data["ok"] is False, name
    assert data["reason"] == "no_clues_found", name
    assert "clues" not in data, name
    assert "totals" not in data, name
    # 三路各自发生了什么要能看见 —— 否则"都取不到"会被读成"没有券"
    assert {"search", "official", "aggregator"} <= set(data["paths"]), data["paths"]
    assert "不是" in data["note"]


async def test_a_challenge_page_does_not_kill_the_other_paths(page: Any) -> None:
    """聚合站撞风控**不再让整个调用失败** —— 另两路不受影响, 风控如实标在它自己那一格里。"""
    page("<html>请完成拼图验证以继续访问</html>")

    data = await _call(city="合肥")
    agg = data["paths"]["aggregator"]
    assert agg["blocked"] is True
    assert "不要重试" in agg["note"]
    assert "截图" in agg["message"]  # 给用户看的规范话术


async def test_an_unreachable_source_is_not_reported_as_no_vouchers(page: Any) -> None:
    page(None)

    data = await _call(city="合肥")
    assert data["reason"] == "no_clues_found"
    assert "不是「这个城市没有券」" in data["note"]
    assert data["paths"]["aggregator"]["ok"] is False


async def test_an_unknown_city_says_so_and_never_guesses_a_code(page: Any) -> None:
    """城市不在表里时,**只影响聚合站那一路** —— 检索路不受城市代码表限制, 不能跟着一起死。"""
    page(None)
    data = await _call(city="霍尔果斯")
    assert data["paths"]["aggregator"]["ok"] is False
    assert "不要猜城市代码" in data["paths"]["aggregator"]["note"]
    assert data["registered_cities"] > 100
    assert "clues" not in data


async def test_a_province_name_is_not_a_city(page: Any) -> None:
    """表是按城市组织的 —— 省份名要如实报"聚合站没有", 而不是随便挑一个城市。"""
    page(None)
    data = await _call(city="安徽")
    assert data["paths"]["aggregator"]["ok"] is False


async def test_the_search_path_reports_honestly_when_the_generic_tool_is_unavailable(page: Any) -> None:
    """本机没配 SERPER_API_KEY: 检索路必须**如实说"用不了"**, 而不是退化去抓搜索引擎 HTML。

    这正是通用搜索工具(search.py)模块说明里点名的那条退化路径 ——
    "scraping search-engine HTML, which returns plausible-looking garbage"。
    """
    page(None)
    data = await _call(city="合肥")
    search = data["paths"]["search"]
    assert search["ok"] is False
    assert search["note"], "检索路失败也必须留下原因"
    assert "不可用" in search["note"]


async def test_the_three_paths_are_reported_even_on_success(page: Any) -> None:
    """成功时也要报三路状态 —— 只看 clues 分不清"哪条路给的"。"""
    page(_page(f"合肥汽车消费券 {_iso(5)}"))
    data = await _call(city="合肥")
    assert set(data["paths"]) == {"search", "official", "aggregator"}
    assert data["paths"]["aggregator"]["ok"] is True
    assert data["totals"]["by_via"]["aggregator"] == 1


async def test_return_json_false_gives_plain_text(page: Any) -> None:
    page(_page(f"合肥汽车消费券 {_iso(5)}"))

    text = await _voucher_clues.voucher_clues(city="合肥", return_json=False)
    assert "clues" in text
    with pytest.raises(json.JSONDecodeError):
        json.loads(text)


# --------------------------------------------------------------------------- #
# 5. 出厂数据: 券源注册表
# --------------------------------------------------------------------------- #


def _sources_data() -> dict[str, Any]:
    return yaml.safe_load(SOURCES_FILE.read_text(encoding="utf-8"))


def test_the_registry_says_where_its_data_came_from() -> None:
    """城市表是**生成物**。数据从哪来、什么时候来的必须与值一起走, 否则判断不了它多新。"""
    data = _sources_data()
    assert data["derived_from"].startswith("https://")
    assert data["derived_at"]
    assert data["verified_at"]


def test_the_city_table_is_derived_and_large_enough_to_be_useful() -> None:
    codes = _sources_data()["aggregators"]["bendibao"]["city_codes"]
    assert len(codes) > 300
    for city in ("合肥", "北京", "上海", "杭州", "武汉"):
        assert city in codes


def test_verified_cities_are_actually_in_the_table() -> None:
    """README 式的自洽检查: 声称"实测能抓"的城市, 必须在表里存在。"""
    data = _sources_data()
    codes = data["aggregators"]["bendibao"]["city_codes"]
    verified = data["verified_working"]
    assert verified, "至少要有实测过的城市"
    assert set(verified) <= set(codes)


def test_city_code_lookup_normalizes_the_suffix() -> None:
    entry = _sources_data()["aggregators"]["bendibao"]
    assert _voucher_sources.city_code(entry, "合肥") == "hf"
    assert _voucher_sources.city_code(entry, "合肥市") == "hf"


def test_city_code_lookup_never_guesses() -> None:
    """猜拼音的代价: 猜出来的代码会 404, 而 404 与"这个城市没有券"长得一样。"""
    entry = _sources_data()["aggregators"]["bendibao"]
    assert _voucher_sources.city_code(entry, "霍尔果斯") is None
    assert _voucher_sources.city_code(entry, "hefei") is None


def test_topic_url_pattern_comes_from_data_not_code() -> None:
    entry = _sources_data()["aggregators"]["bendibao"]
    assert _voucher_sources.topic_url(entry, "hf") == "https://m.hf.bendibao.com/news/zhuantixiaofeiquan/"


def test_a_malformed_source_definition_fails_loudly() -> None:
    """缺字段要报错, 不要在代码里兜默认值 —— 那等于第二份配置。"""
    with pytest.raises(ValueError, match="缺少"):
        _voucher_sources.aggregator({"aggregators": {"x": {"name": "X"}}}, "x")


async def test_the_note_forbids_inferring_validity_from_the_article_date(page: Any) -> None:
    """踩过的坑: 文章是 `recent`(147 天前), 里面的券却只有 2 天有效期, 早就过期了。

    所以字段名写成 `published` / `clue_freshness`(线索的时效), 且 note 必须把
    "发放窗口要从原文读" 说死 —— 否则模型会拿文章日期当券的有效期。
    """
    page(_page(f"合肥汽车消费券 {_iso(5)}"))

    data = await _call(city="合肥")
    note = data["note"]
    assert "不是券的有效期" in note
    assert "valid_from" in note and "valid_to" in note
    assert "不许因为文章还新就说「能领」" in note

    clue = data["clues"][0]
    assert "published" in clue and "clue_freshness" in clue
    assert "date" not in clue, "字段名不能叫 date —— 会被读成「券的日期」"
    assert "freshness" not in clue, "字段名不能叫 freshness —— 会被读成「券的时效」"


# --------------------------------------------------------------------------- #
# 6. 官方入口: 核过的清单, 不是照记忆写的
# --------------------------------------------------------------------------- #


def test_the_registry_records_a_verified_official_entry() -> None:
    """§4「来源清单优先于数据」—— 官方入口要能长期维护, 就得连同**证据**一起存。

    只存一个 URL 不存证据, 下一个人没法判断它还算不算数, 只能重新核一遍 ——
    那这张清单就白维护了。
    """
    official = _sources_data().get("official")
    assert official, "注册表里没有 official 段"
    entry = official["mofcom_promotion"]
    assert entry["tier"] == "official"
    assert "{n}" in entry["list_url"], "分页占位符丢了就没法翻页"
    assert entry["verified_at"]
    assert len(entry["evidence"]) >= 3, "至少要记下几条实测证据"
    assert entry["coverage"], "必须写明覆盖范围 —— 官方源也不保证每个城市都有"


def test_failed_candidates_are_recorded_so_nobody_reprobes_them() -> None:
    """死路也要记 —— 否则后来的人会把同一条路再走一遍。"""
    rejected = _sources_data().get("rejected")
    assert rejected, "至少该记下探过但不行的候选"
    for item in rejected:
        assert item["url"].startswith("https://")
        assert item["why"], f"{item['url']} 没说为什么不行"
        assert item["verified_at"]


def test_both_source_tiers_are_declared_apart() -> None:
    """官方源与聚合站性质不同(一个权威但未必覆盖你要的城市, 一个按城市更细但有滞后),
    各自标 tier —— 报告时才分得开, 也才不会把聚合站的话当成官方口径。"""
    data = _sources_data()
    tiers = {data["official"]["mofcom_promotion"]["tier"]} | {v["tier"] for v in data["aggregators"].values()}
    assert tiers == {"official", "aggregator"}
