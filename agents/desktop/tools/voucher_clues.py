"""voucher_clues v1: 地方消费券的**线索层** —— 「某市现在有什么券」。

放在 A2 链路里的位置:

    用户说城市 -> **本工具给线索(标题 + 日期 + 链接)** -> 模型读原文提取券属性
              -> saving_facts 校验成事实契约 -> 本体判定 -> agent 表达

## 三条纪律

**1. 只给线索, 不给结论。** 返回的是「有哪些页面」, 不是「有什么券」。
面额 / 门槛 / 适用范围 / 有效期必须打开原文才知道, 那是模型的活(它读得懂中文页面),
本工具**不猜、不提取** —— 从标题里反推"满500减50"正是最容易编出来的地方。

**2. 日期是承重的。** 实测: 合肥专题页最新一条是 2026-04-24, 而当时已是 2026-09-17。
不带日期地返回 48 条, 模型会把 2024 年的电影消费券当成现行的。所以每条都带
``date`` / ``age_days`` / ``freshness``, 并给出分层计数。

**3. 抓不到就说抓不到。** 券源站点会**拼图风控**(实测: 同一批城市里一半返回
「请完成拼图验证以继续访问」)。命中风控时**停下并告知**, 不重试、不换入口硬试 ——
与本仓 ``saving_login(blocked)`` 的风控范式同源。

## 数据在哪

``<agent>/sources/voucher-sources.yaml``: 「去哪儿找券」。城市表是由站点索引**推导**
的生成物(带 ``derived_from`` / ``derived_at``), 不是手编清单。查不到的城市**不猜拼音** ——
猜出来的代码会 404, 而 404 与「这个城市没有消费券」在返回体里长得一样, 那是最贵的一类错。

## 刻意不做的事

- **不做检索降级**(不内置 bing/ddg): 那些通道实测不稳(同一查询两次, 一次 10 条一次 0 条),
  且在 ``review_search`` 里已有一份。抓不到时把地址交回给 agent, 让它用自己的
  ``web_search`` / ``web_fetch`` / 浏览器去补 —— 通用能力不该在专用工具里重造一遍。
- **不判「能不能用」**: 那是本体的事。
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

import _voucher_sources as _sources
import aiohttp
from loguru import logger

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_SOURCE_KEY = "bendibao"

# 时效分层。券是**短周期**的东西: 30 天外基本已经发完了。
_CURRENT_DAYS = 30
_RECENT_DAYS = 180

# 风控页的特征词。命中就停 —— 猜错(把正常页当风控)的代价是白停一次;
# 猜漏(把风控页当正常页)的代价是"解析出 0 条"被当成"这个城市没有券"。
_BLOCKED_MARKERS = ("拼图", "请完成验证", "访问验证", "人机验证", "安全检查", "滑动验证")

_BLOCKED_MESSAGE = (
    "这个券源站点要求人工验证了(可能是访问过于频繁, 也可能是我被识别成了自动访问)。"
    "为了不给你添麻烦, 我停下了, 也不会自动重试。你可以: "
    "① 自己打开这个页面看一眼, 把看到的券发我(截图或文字都行); "
    "② 或者过一会儿再让我试一次。"
)

# 标题里出现的品类词 —— 用来给"这个城市有什么类型的券"一个量感, **不做过滤**。
_CATEGORY_HINTS = (
    "餐饮",
    "汽车",
    "家电",
    "家居",
    "百货",
    "超市",
    "商超",
    "加油",
    "文旅",
    "旅游",
    "电影",
    "住宿",
    "体育",
    "图书",
    "数码",
    "手机",
    "电动",
    "医药",
    "养老",
    "托育",
)

_LINK_RE = re.compile(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_TAG_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
_DATE_RE = re.compile(r"(20\d\d)-(\d{2})-(\d{2})")
# 专题页的条目文字是「标题 + 发布日期 + 发布时间」。日期之后的 HH:MM 也要去掉,
# 否则标题尾部会拖一个 "14:41" 进模型上下文。
_TIME_RE = re.compile(r"\d{1,2}:\d{2}(?::\d{2})?")
_KEYWORD = "消费券"


def _fail(reason: str, **extra: Any) -> str:
    payload: dict[str, Any] = {"ok": False, "reason": reason}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _strip_tags(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _TAG_RE.sub(" ", html))).strip()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _freshness(age_days: int | None) -> str:
    """把"多久以前"落成三个桶。``undated`` 单独一档: 没日期**不等于**新。"""
    if age_days is None:
        return "undated"
    if age_days <= _CURRENT_DAYS:
        return "current"
    if age_days <= _RECENT_DAYS:
        return "recent"
    return "stale"


def _is_blocked(html: str) -> bool:
    head = html[:4000]
    return any(m in head for m in _BLOCKED_MARKERS)


async def _fetch(url: str, total_seconds: float = 25.0) -> str | None:
    """取页面文本。失败返回 ``None`` —— 让调用方把"取不到"与"取到了但没有"分开。"""
    try:
        limit = aiohttp.ClientTimeout(total=total_seconds)
        async with (
            aiohttp.ClientSession(headers={"User-Agent": _USER_AGENT}) as sess,
            sess.get(url, timeout=limit, ssl=False) as resp,
        ):
            if resp.status != 200:
                logger.warning(f"voucher source {url} -> HTTP {resp.status}")
                return None
            return await resp.text(errors="replace")
    except Exception as exc:  # 网络层什么都可能抛, 一律降级成"取不到"
        logger.warning(f"voucher source {url} fetch failed: {type(exc).__name__}: {exc}")
        return None


def _parse_clues(html: str, today: date) -> list[dict[str, Any]]:
    """从专题页抽出「标题 + 日期 + 链接」。

    只认**链接文字里含「消费券」**的条目: 专题页混着大量导航与推荐位,
    按链接文字筛比按容器选择器筛更抗改版(改版会让选择器空, 而空看起来像"没有券")。
    """
    seen: set[str] = set()
    clues: list[dict[str, Any]] = []
    for match in _LINK_RE.finditer(html):
        href = (match.group(1) or "").strip()
        text = _strip_tags(match.group(2))
        if _KEYWORD not in text or len(text) < 6:
            continue
        date_match = _DATE_RE.search(text)
        published = date_match.group(0) if date_match else None
        title = _TIME_RE.sub("", _DATE_RE.sub("", text)).strip(" -|·")
        if not title or title in seen:
            continue
        seen.add(title)
        age: int | None = None
        if published:
            try:
                age = (today - date.fromisoformat(published)).days
            except ValueError:
                published, age = None, None
        clues.append(
            {
                "title": title[:120],
                "date": published,
                "age_days": age,
                "freshness": _freshness(age),
                "url": href[:300],
            }
        )
    # 新的在前; 没日期的排最后(而不是当最新)。
    clues.sort(key=lambda c: (c["age_days"] is None, c["age_days"] if c["age_days"] is not None else 0))
    return clues


def _categories_seen(clues: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for clue in clues:
        title = clue["title"]
        for hint in _CATEGORY_HINTS:
            if hint in title:
                counts[hint] = counts.get(hint, 0) + 1
                break
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


async def voucher_clues(
    city: str,
    category: str = "",
    max_results: int = 20,
    return_json: bool = True,
) -> str:
    """找「某市现在有什么消费券」的**线索**(标题 + 日期 + 链接)。只给线索, 不给结论。

    city: 城市名(合肥 / 合肥市)。按城市组织, 省份名(安徽)不在表里。
    category: 可选。对**标题**做关键词过滤(如 汽车 / 餐饮 / 家电)。
              这是关键词匹配, 不是语义判断 —— 返回里带 `totals.parsed` 与匹配数, 好让你知道滤掉了多少。
    max_results: 最多返回多少条(默认 20, 已按时间倒序)。
    return_json: 默认返回 JSON 文本。

    返回里的 `freshness` 三档要看:
    - `current`(30 天内) / `recent`(180 天内) / `stale`(更早) / `undated`(**没有日期, 不等于新**)。

    `ok=false` 时**不带** `clues` 字段, 按 reason 处理:
    - `blocked`         -> 券源要人工验证。**停下, 把 message 原样告诉用户, 不重试。**
    - `unknown_city`    -> 表里没这个城市。**不要猜拼音**: 换用户所在地的市级名称, 或走检索。
    - `source_unreachable` -> 取不到。可自己用 web_search / web_fetch / 浏览器去找, 但**别编**。

    拿到线索之后: 打开链接读原文, 才能知道面额/门槛/品类/有效期; 这些属性必须带来源与日期,
    交给 `saving_facts` 组装。**「能不能用」「能减多少」是本体的事, 不要在这里算。**
    """
    try:
        sources = await _sources.load_sources()
    except (OSError, ValueError) as exc:
        return _fail("sources_unavailable", detail=str(exc))

    try:
        entry = _sources.aggregator(sources, _SOURCE_KEY)
    except (KeyError, ValueError) as exc:
        return _fail("sources_malformed", detail=str(exc))

    registered = _sources.known_city_count(sources, _SOURCE_KEY)
    code = _sources.city_code(entry, city)
    if not code:
        return _fail(
            "unknown_city",
            query={"city": city},
            registered_cities=registered,
            note=(
                f"券源是按**城市**组织的, 表里没有 {city!r}。请改用用户所在地的**市级**名称"
                "(如 合肥 / 北京), 省份名不在表里。"
                "**不要自己猜城市代码** —— 猜错会 404, 而 404 与「这个城市没有券」在返回体里长得一样。"
                "要加城市: 往 sources/voucher-sources.yaml 的 city_codes 加一条(加城市 = 加数据)。"
            ),
        )

    url = _sources.topic_url(entry, code)
    page = await _fetch(url)
    if page is None:
        return _fail(
            "source_unreachable",
            query={"city": city, "city_code": code},
            source={"key": _SOURCE_KEY, "name": entry.get("name"), "url": url},
            note=(
                "券源取不到(超时 / 非 200 / 网络问题)。这不是「这个城市没有券」。"
                "可以用 web_search 或浏览器自己找, 或稍后再试一次; **但查不到就要说查不到, 不要编券**。"
            ),
        )

    if _is_blocked(page):
        logger.info(f"Voucher source {url} returned a human-verification page")
        return _fail(
            "blocked",
            query={"city": city, "city_code": code},
            source={"key": _SOURCE_KEY, "name": entry.get("name"), "url": url},
            message=_BLOCKED_MESSAGE,
            note="**停下, 不要自动重试, 不要换城市代码硬试。** 把 message 原样告诉用户, 等他的选择。",
        )

    today = date.today()
    all_clues = _parse_clues(page, today)
    keyword = (category or "").strip()
    matched = [c for c in all_clues if keyword in c["title"]] if keyword else list(all_clues)

    by_freshness: dict[str, int] = {"current": 0, "recent": 0, "stale": 0, "undated": 0}
    for clue in matched:
        by_freshness[clue["freshness"]] = by_freshness.get(clue["freshness"], 0) + 1

    verified = [str(c) for c in (sources.get("verified_working") or [])]
    payload = {
        "ok": True,
        "query": {"city": city, "city_code": code, "category": keyword},
        "source": {
            "key": _SOURCE_KEY,
            "name": entry.get("name"),
            "tier": entry.get("tier"),
            "url": url,
            "fetched_at": _now_iso(),
            "derived_at": sources.get("derived_at"),
        },
        "registered_cities": registered,
        "city_verified_working": city in verified,
        "totals": {"parsed": len(all_clues), "matched": len(matched), "by_freshness": by_freshness},
        "categories_seen": _categories_seen(matched),
        "clues": matched[: max(0, int(max_results))],
        "note": (
            "这些是**线索**, 不是结论: 面额 / 门槛 / 适用范围 / 有效期都要打开链接读原文才知道。"
            "先看 `date` 与 `freshness` —— `stale` 的很可能早就发完了, `undated` 也不能当新。"
            "把这些页面交给模型提取券属性(带来源与日期), 再用 `saving_facts` 组装成事实契约。"
            "「能不能用」「能减多少」由本体判定, **不要在这里算**。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)
