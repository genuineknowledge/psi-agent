# -*- coding: utf-8 -*-
"""review_search v3（简化版）：检索并返回候选文章（多源 + 降级 + 兜底 + 防编造）。

职责单一：给定品类/预算/约束，返回 N 篇真实抓取到的候选文章。
- 类型判断 / 型号提取 / 排序：交给模型（模型擅长语义，不重复造轮子）。
- 防编造：只返回实际抓取到的文章；模型基于文章提取型号，不得编造（配合 R6）。
- 多源：每品类源列表，主源失败自动切备源。
- 降级：channel 不够时补 bing/ddg，轻量过滤官网/大全/百科。
- 兜底：全部失败返回 C 端友好话术。
"""
import json, re
from typing import Optional

USER_FALLBACK = (
    "当前我没能实时核实到符合你条件的最新机型。为了给你准确的建议，你可以："
    "① 把你看中的 1-2 个商品链接发我，我帮你核对能不能享受补贴、到手价大概多少；"
    "② 再告诉我更具体的预算/用途/品牌偏好，我帮你缩小范围。"
)
NOTE_ARTICLES = (
    "以下是本次真实检索到的候选文章（已去重，均来自实际抓取，未编造）。"
    "请阅读这些文章并提取具体型号、配置、参考价，在回答中标注每条的来源与日期。"
)

# 每品类源列表（多源冗余：主源失败切备源）
_CATEGORY_SOURCES = {
    "笔记本": ["https://detail.zol.com.cn/notebook/", "https://product.pconline.com.cn/notebook/"],
    "电脑":   ["https://detail.zol.com.cn/notebook/", "https://product.pconline.com.cn/notebook/"],
    "游戏本": ["https://detail.zol.com.cn/notebook/", "https://product.pconline.com.cn/notebook/"],
    "手机":   ["https://product.pconline.com.cn/mobile/"],
    "平板":   ["https://product.pconline.com.cn/pad/"],
    "耳机":   ["https://product.pconline.com.cn/headphone/"],
}
_NOISE = ("登录", "QQ", "微博", "点评", "奖品", "收藏", "购物车", "注册", "下载", "客户端",
          "论坛", "首页", "帮助", "官网", "大全", "百科", "报价", "频道", "主页", "品牌")


def _category_sources(category: str) -> list[str]:
    for key, urls in _CATEGORY_SOURCES.items():
        if key in category:
            return urls
    return []


def _build_query(category: str, budget_min: float, budget_max: Optional[float],
                 constraints: str, region: str) -> str:
    parts = [category]
    if budget_min or budget_max:
        parts.append("预算%d-%d元" % (int(budget_min), int(budget_max or budget_min + 5000)))
    if constraints:
        parts.append(constraints)
    if region:
        parts.append(region)
    parts.append("推荐")
    return " ".join(parts)


async def _http_get(url: str, timeout: float = 20) -> Optional[str]:
    try:
        import aiohttp
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
        async with aiohttp.ClientSession(headers=headers) as sess:
            async with sess.get(url, timeout=aiohttp.ClientTimeout(total=timeout), ssl=False) as resp:
                if resp.status != 200:
                    return None
                return await resp.text()
    except Exception:
        return None


async def _fetch_channel_page(url: str) -> list[dict]:
    html = await _http_get(url, timeout=25)
    if not html or len(html) < 1000:
        return []
    items = []
    seen = set()
    for m in re.finditer(r'<a[^>]*href="([^"]+)"[^>]*title="([^"]{8,60})"', html):
        link, title = m.group(1), m.group(2)
        if any(x in title for x in _NOISE):
            continue
        base = title[:-2] if title.endswith("报价") else title
        if base in seen:
            continue
        seen.add(base)
        full = "https://detail.zol.com.cn" + link if link.startswith("/") and "pconline" not in link else link
        items.append({"title": title, "url": full, "snippet": ""})
    return items


async def _channel_fetch(category: str) -> list[dict]:
    for url in _category_sources(category):
        items = await _fetch_channel_page(url)
        if items:
            return items
    return []


async def _bing_rss(query: str) -> list[dict]:
    import urllib.parse
    url = "https://www.bing.com/search?format=rss&q=" + urllib.parse.quote(query)
    html = await _http_get(url)
    if not html:
        return []
    items = []
    for m in re.finditer(r"<item>.*?</item>", html, re.S):
        seg = m.group(0)
        tm = re.search(r"<title>(.*?)</title>", seg, re.S)
        lm = re.search(r"<link>(.*?)</link>", seg, re.S)
        title = re.sub(r"<[^>]+>", "", tm.group(1)).strip() if tm else ""
        url_ = lm.group(1).strip() if lm else ""
        if title and len(title) > 6:
            items.append({"title": title, "url": url_, "snippet": ""})
    return items[:10]


async def _ddg_html(query: str) -> list[dict]:
    import urllib.parse
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    html = await _http_get(url)
    if not html:
        return []
    items = []
    for m in re.finditer(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
        link, title = m.group(1), re.sub(r"<[^>]+>", "", m.group(2)).strip()
        if title and len(title) > 6:
            items.append({"title": title, "url": link, "snippet": ""})
    return items[:10]


def _light_filter(articles: list[dict]) -> list[dict]:
    return [a for a in articles if not any(x in (a.get("title", "") or "") for x in
            ("官网", "大全", "百科", "报价", "首页", "频道", "主页", "论坛", "品牌", "下载"))]


async def review_search(
    category: str,
    budget_min: float = 0.0,
    budget_max: Optional[float] = None,
    constraints: str = "",
    region: str = "",
    max_results: int = 8,
) -> str:
    """检索并返回真实候选文章列表（JSON 字符串）。型号/类型/排序由模型基于文章完成。"""
    query = _build_query(category, budget_min, budget_max, constraints, region)
    reasons: list[str] = []
    articles: list[dict] = []

    got = await _channel_fetch(category)
    if got:
        articles.extend(got)
    else:
        reasons.append("channel: 无有效结果")

    if len(articles) < max_results:
        for src_name, fetcher in [("bing_rss", _bing_rss), ("ddg", _ddg_html)]:
            try:
                more = _light_filter(await fetcher(query))
            except Exception as e:
                reasons.append("%s: %s" % (src_name, type(e).__name__))
                continue
            if more:
                for a in more:
                    a["source"] = src_name
                    articles.append(a)
                if len(articles) >= max_results:
                    break
            else:
                reasons.append("%s: 无有效结果" % src_name)

    seen, uniq = set(), []
    for a in articles:
        if a["title"] not in seen:
            seen.add(a["title"])
            uniq.append(a)
    articles = uniq[:max_results]

    if articles:
        body = {"query": {"category": category, "budget": [budget_min, budget_max],
                          "constraints": constraints, "region": region},
                "sources_tried": 1 + (1 if len(articles) >= max_results else 2),
                "sources_succeeded": 1,
                "articles": articles, "note": NOTE_ARTICLES, "fallback": None}
    else:
        body = {"query": {"category": category, "budget": [budget_min, budget_max],
                          "constraints": constraints, "region": region},
                "sources_tried": 3, "sources_succeeded": 0,
                "articles": [], "note": "",
                "fallback": {"reasons": reasons, "user_message": USER_FALLBACK}}
    return json.dumps(body, ensure_ascii=False)


if __name__ == "__main__":
    import asyncio
    print(asyncio.run(review_search("游戏本", 5000, 7000, "1级能效 国补", "安徽")))
