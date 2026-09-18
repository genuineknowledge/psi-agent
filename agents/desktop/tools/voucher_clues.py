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

# 通用搜索工具(serper MCP)。**复用**它, 不自己抓搜索引擎 HTML —— 理由见 _search_clues。
import search as web_search
from loguru import logger

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_SOURCE_KEY = "bendibao"
_OFFICIAL_KEY = "mofcom_promotion"

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
                # `published` 是**文章发布日**, 不是券的有效期 —— 字段名刻意写全, 免得被读成
                # "这张券的日期"。实测有一篇 published 是 147 天前, 里面的券却只有 2 天有效期。
                "published": published,
                "age_days": age,
                # 同理: 这是**线索**的时效, 只说明"这篇文章多新", 不说明"券还能不能用"。
                "clue_freshness": _freshness(age),
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


# --------------------------------------------------------------------------- #
# 路 1: 实时检索(主路径)
# --------------------------------------------------------------------------- #
#
# 为什么这是**主路径**: 实测同样的时刻, 西安/广州/天津三城在"城市聚合站"那条路上
# 全部取不到(风控 / 连不上), 而检索**各拿到 10 条** —— 而且直接找到了那三城的
# 本地宝页面。也就是说检索既不需要城市代码表, 也不吃专题页的风控。
# 易变的地方政策本来就该现查, 而不是靠一张预先建的源表。


def _collect_links(node: Any, out: list[dict[str, Any]]) -> None:
    """从搜索结果里**结构无关地**挑出条目: 递归找同时带 ``link`` 与 ``title`` 的对象。

    刻意不写死 ``data["organic"]`` —— serper 有十几个纵向(网页/新闻/购物/学术...),
    各自的包裹键不同, 写死一个就会在换纵向时静默返回空。认"形状"比认"键名"稳。
    """
    if isinstance(node, dict):
        link, title = node.get("link"), node.get("title")
        if isinstance(link, str) and link.startswith("http") and isinstance(title, str) and len(title) > 6:
            out.append({"title": title, "url": link, "date": node.get("date")})
        for value in node.values():
            _collect_links(value, out)
    elif isinstance(node, list):
        for value in node:
            _collect_links(value, out)


#: serper 的 ``date`` 是英文月份写法(实测 ``"Aug 27, 2026"``), 不是 ISO。
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _date_hint(raw: Any, today: date) -> tuple[str | None, int | None]:
    """把搜索结果带的 ``date`` 归一成 ``YYYY-MM-DD``; 认不出就留空(**不猜**)。

    两种格式都要认: ``YYYY-MM-DD`` 与 serper 实测的英文月份写法 ``"Aug 27, 2026"``。
    只认前者的话, 通用搜索明明给了日期却会被整批丢掉 —— 时效分层就白做了(实测踩过)。
    """
    if not isinstance(raw, str):
        return None, None
    text = raw.strip()
    match = _DATE_RE.search(text)
    if match:
        published = match.group(0)
    else:
        alt = re.match(r"([A-Za-z]{3})[A-Za-z]*\s+(\d{1,2}),?\s+(\d{4})", text)
        if alt is None:
            return None, None
        month = _MONTHS.get(alt.group(1).lower())
        if not month:
            return None, None
        published = f"{alt.group(3)}-{month:02d}-{int(alt.group(2)):02d}"
    try:
        return published, (today - date.fromisoformat(published)).days
    except ValueError:
        return None, None


async def _search_clues(city: str, category: str, templates: list[str]) -> tuple[list[dict[str, Any]], str]:
    """实时检索某市的消费券线索 —— **复用通用搜索工具**, 不自己抓搜索引擎 HTML。

    为什么不自己抓: ``search.py``(通用搜索工具)的模块说明里写着 —— 搜不了的 agent 会退化成
    "scraping search-engine HTML, which returns plausible-looking garbage rather than an
    honest error"。本工具最初正是这么干的, 那是重复实现, 也正是它警告的那条路。

    通用搜索不可用时, 返回空 + **如实的原因** —— 不退化去抓 HTML 充数。
    """
    fn = getattr(web_search, "serper_google_search", None)
    if fn is None:
        return [], "通用搜索工具未注册(serper MCP 不可用)"

    today = date.today()
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for template in templates:
        query = str(template).format(city=city, category=category).strip()
        if not query:
            continue
        try:
            raw = await fn(q=query, num="10")
        except Exception as exc:
            return [], f"通用搜索调用失败: {type(exc).__name__}: {exc}"
        text = str(raw)
        if text.startswith("Error: ") or "API_KEY is empty" in text:
            return [], f"通用搜索不可用: {text.strip()[:140]}"
        try:
            data = json.loads(text)
        except ValueError:
            return [], "通用搜索返回的不是 JSON, 无法解析"

        rows: list[dict[str, Any]] = []
        _collect_links(data, rows)
        for row in rows:
            if row["url"] in seen:
                continue
            seen.add(row["url"])
            published, age = _date_hint(row.get("date"), today)
            found.append(
                {
                    "title": row["title"][:120],
                    "url": row["url"][:300],
                    "published": published,
                    "age_days": age,
                    "clue_freshness": _freshness(age),
                    "via": "search",
                }
            )
        if found:
            break
    return found, ("" if found else "检索没有返回可用条目")


# --------------------------------------------------------------------------- #
# 路 2: 官方列表(权威层)
# --------------------------------------------------------------------------- #

_OFFICIAL_LINK_RE = re.compile(r'href="([^"]*/news/123/[^"]*)"[^>]*>(.*?)</a>', re.S | re.I)


async def _official_clues(entry: dict[str, Any], city: str, pages: int, today: date) -> list[dict[str, Any]]:
    """抓官方列表的前 N 页, 按城市名过滤。

    官方列表是**全国**的, 所以"过滤后 0 条"是常态而不是异常 —— 调用方要看得出
    "这一路查过了, 只是这个城市本期没有", 而不是以为没查。
    """
    pattern = str(entry.get("list_url") or "")
    if "{n}" not in pattern:
        return []
    wanted = _sources.normalize_city(city)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for n in range(1, max(1, pages) + 1):
        page = await _fetch(pattern.replace("{n}", str(n)))
        if page is None:
            break
        for m in _OFFICIAL_LINK_RE.finditer(page):
            href, inner = m.group(1), _strip_tags(m.group(2))
            if wanted not in inner or len(inner) < 6 or href in seen:
                continue
            seen.add(href)
            date_match = _DATE_RE.search(inner)
            published = date_match.group(0) if date_match else None
            title = _TIME_RE.sub("", _DATE_RE.sub("", inner)).strip(" -|·")
            age: int | None = None
            if published:
                try:
                    age = (today - date.fromisoformat(published)).days
                except ValueError:
                    published, age = None, None
            out.append(
                {
                    "title": title[:120],
                    "url": (str(entry.get("host_url") or "") + href if href.startswith("/") else href)[:300],
                    "published": published,
                    "age_days": age,
                    "clue_freshness": _freshness(age),
                    "via": "official",
                }
            )
    return out


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

    `clue_freshness` 是**线索的**时效, 不是券的 —— 字段名刻意写全, 免得被读成"券的时效":
    - `current`(文章 30 天内) / `recent`(180 天内) / `stale`(更早) / `undated`(**没日期, 不等于新**)。
    - `published` 是**文章发布日**。实测有一篇 `published` 是 147 天前的文章, 里面的券发放窗口
      只有 12 天、单张有效期只有 2 天 —— 早就过期了。**「文章还新」推不出「券还能领」**,
      所以打开原文后 `valid_from` / `valid_to` 是**必读项**; 读不到就说 `[Cannot Confirm]`,
      不许拿文章日期替它填。

    **三条路一起走, 每一路的成败都报在 `paths` 里**:
    - `paths.search`     -> 通用搜索工具(serper)。**任意城市都能走**, 但结果多无日期;
    - `paths.official`   -> 官方列表(全国性)。过滤到本城市后 0 条是**常态**, 不是异常;
    - `paths.aggregator` -> 城市聚合站。要城市代码表, 且会撞拼图风控。

    一路失败**不影响**另两路。只有三路全空才 `ok=false`(`reason=no_clues_found`),
    那时要**逐路看 `paths` 各自发生了什么**, 不要据此断言"这个城市没有券"。
    聚合站撞风控时它那一格会带 `blocked: true` 与 `message`(给用户看的规范话术):
    **停下告知用户, 不重试、不换城市代码硬试。**
    城市不在代码表里只影响聚合站那一路 —— 检索路不受此限, 别把两件事混成一件事。

    拿到线索之后: 打开链接读原文, 才能知道面额/门槛/品类/有效期; 这些属性必须带来源与日期,
    交给 `saving_facts` 组装。**「能不能用」「能减多少」是本体的事, 不要在这里算。**
    """
    try:
        sources = await _sources.load_sources()
    except (OSError, ValueError) as exc:
        return _fail("sources_unavailable", detail=str(exc))

    registered = _sources.known_city_count(sources, _SOURCE_KEY)
    today = date.today()
    keyword = (category or "").strip()
    paths: dict[str, Any] = {}
    collected: list[dict[str, Any]] = []

    # 路 1: 实时检索 —— **主路径**。任何城市都能走, 不依赖城市代码表, 也不吃专题页风控。
    search_raw = sources.get("search")
    search_cfg: dict[str, Any] = search_raw if isinstance(search_raw, dict) else {}
    templates = [str(x) for x in (search_cfg.get("query_templates") or [])]
    if templates:
        search_clues, search_why = await _search_clues(city, keyword, templates)
    else:
        search_clues, search_why = [], "注册表里没配检索模板"
    paths["search"] = {
        "ok": bool(search_clues),
        "count": len(search_clues),
        "note": search_why or "来自通用搜索工具(serper); 搜索结果多带日期, 但日期是**文章**的发布日期",
    }
    collected.extend(search_clues)

    # 路 2: 官方列表 —— 权威层。它是**全国**列表, 过滤到本城市后 0 条是常态, 不是异常。
    try:
        official_entry: dict[str, Any] | None = _sources.official(sources, _OFFICIAL_KEY)
    except KeyError, ValueError:
        official_entry = None
    if official_entry is None:
        paths["official"] = {"ok": False, "count": 0, "note": "注册表里没有官方入口定义"}
    else:
        official_clues = await _official_clues(official_entry, city, int(official_entry.get("pages") or 2), today)
        paths["official"] = {
            "ok": bool(official_clues),
            "count": len(official_clues),
            "source": official_entry.get("name"),
            "note": (
                "来自官方列表" if official_clues else "官方列表查过了, 但本期没有这个城市的条目(该列表是全国性的)"
            ),
        }
        collected.extend(official_clues)

    # 路 3: 城市聚合站 —— 结构化层(有券的档位), 但要城市代码表, 且会撞拼图风控。
    code: str | None = None
    aggregator_entry: dict[str, Any] | None = None
    try:
        aggregator_entry = _sources.aggregator(sources, _SOURCE_KEY)
    except KeyError, ValueError:
        paths["aggregator"] = {"ok": False, "count": 0, "note": "注册表里没有可用的聚合站定义"}
    if aggregator_entry is not None:
        code = _sources.city_code(aggregator_entry, city)
        if not code:
            paths["aggregator"] = {
                "ok": False,
                "count": 0,
                "note": f"按城市组织的聚合站表里没有 {city!r}(**不要猜城市代码**); 检索路不受此限",
            }
        else:
            url = _sources.topic_url(aggregator_entry, code)
            page = await _fetch(url)
            if page is None:
                paths["aggregator"] = {"ok": False, "count": 0, "url": url, "note": "取不到(超时 / 非 200)"}
            elif _is_blocked(page):
                logger.info(f"Voucher source {url} returned a human-verification page")
                paths["aggregator"] = {
                    "ok": False,
                    "count": 0,
                    "url": url,
                    "blocked": True,
                    "message": _BLOCKED_MESSAGE,
                    "note": "该站要求人工验证; **不要重试、不要换城市代码硬试** —— 另两路不受影响",
                }
            else:
                aggregator_clues = _parse_clues(page, today)
                for clue in aggregator_clues:
                    clue["via"] = "aggregator"
                paths["aggregator"] = {"ok": bool(aggregator_clues), "count": len(aggregator_clues), "url": url}
                collected.extend(aggregator_clues)

    # 合并去重(按 URL), 新的在前、**没日期的排最后**(而不是当最新)
    seen_urls: set[str] = set()
    merged: list[dict[str, Any]] = []
    for clue in collected:
        if clue["url"] in seen_urls:
            continue
        seen_urls.add(clue["url"])
        merged.append(clue)
    merged.sort(key=lambda c: (c["age_days"] is None, c["age_days"] if c["age_days"] is not None else 0))

    matched = [c for c in merged if keyword in c["title"]] if keyword else list(merged)

    if not merged:
        return _fail(
            "no_clues_found",
            query={"city": city, "category": keyword},
            paths=paths,
            registered_cities=registered,
            note=(
                "三条路都查过, 都没拿到这个城市的券线索。这是「此刻这三条路都取不到」, "
                "**不是「这个城市没有券」** —— 看 paths 里每一路各自发生了什么, 不要据此下结论。"
            ),
        )

    by_freshness: dict[str, int] = {"current": 0, "recent": 0, "stale": 0, "undated": 0}
    by_via: dict[str, int] = {}
    for clue in matched:
        by_freshness[clue["clue_freshness"]] = by_freshness.get(clue["clue_freshness"], 0) + 1
        by_via[clue["via"]] = by_via.get(clue["via"], 0) + 1

    verified = [str(c) for c in (sources.get("verified_working") or [])]
    payload: dict[str, Any] = {
        "ok": True,
        "query": {"city": city, "category": keyword, "city_code": code},
        "paths": paths,
        "registered_cities": registered,
        "city_verified_working": city in verified,
        "totals": {
            "parsed": len(merged),
            "matched": len(matched),
            "by_clue_freshness": by_freshness,
            "by_via": by_via,
        },
        "categories_seen": _categories_seen(matched),
        "clues": matched[: max(0, int(max_results))],
        "note": (
            "这些是**线索**, 不是结论: 面额 / 门槛 / 适用范围 / 有效期都要打开链接读原文才知道。"
            "`published` / `clue_freshness` 说的是**这篇文章多新, 不是券的有效期** —— "
            "实测有一篇 147 天前的文章(recent), 里面的券发放窗口只有 12 天、单张有效期只有 2 天, 早就过期了。"
            "**检索来的线索没有日期(`undated`)**: 那不代表它新, 只代表时效还不知道, 必须打开原文看。"
            "所以打开原文后, **发放窗口与有效期(valid_from / valid_to)是必读项**: "
            "读不到就标 [Cannot Confirm], **不许因为文章还新就说「能领」**。"
            "把这些页面交给模型提取券属性(带来源与日期), 再用 `saving_facts` 组装成事实契约。"
            "「能不能用」「能减多少」由本体判定, **不要在这里算**。"
        ),
    }
    return json.dumps(payload, ensure_ascii=False) if return_json else str(payload)
