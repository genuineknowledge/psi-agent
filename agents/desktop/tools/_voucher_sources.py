"""券源注册表加载器 —— 「去哪儿找地方消费券」的唯一数据源。

数据住 ``<agent>/sources/voucher-sources.yaml``。本模块是 ``voucher_clues`` 读它的
唯一入口: 工具里不得再留第二份源清单, 那正是《省钱场景交接》§7 坑 1「参数两处重复,
改一处必改另一处」的成因。

缓存按 ``(mtime_ns, size)`` 失效(与 ``_fact_cards`` 同一套期望): 改完数据文件不用重启
进程即可生效。数据文件不在 ``tools/`` 下, 内核不会替我们盯着它, 所以这道失效判据
必须自己写。

``city_codes`` 是**生成物** —— 由本地宝城市索引推导, 不是手编的。所以文件刻意带
``derived_from`` / ``derived_at``: 数据从哪来、什么时候来的, 必须与值一起走, 否则下游
没法判断它有多新。**并且它只说明「这个城市有子域」, 不代表那个专题页一定可访问** ——
实测存在拼图风控, 可用性只能在抓取那一刻判断。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
import yaml
from loguru import logger

_SOURCES_DIRNAME = "sources"
_FILE_NAME = "voucher-sources.yaml"

# path -> ((mtime_ns, size), data)
_cache: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}

# 用户会说「合肥市」「合肥地区」。归一后再查表, 免得表里明明有、却因为一个后缀说没有。
_CITY_SUFFIXES = ("自治州", "地区", "盟", "市")


def _roots() -> list[Path]:
    """能力包根 —— 就是本文件所在的那个包(理由见 ``_fact_cards._roots``)。"""
    return [Path(__file__).resolve().parents[1]]


def normalize_city(city: str) -> str:
    """把用户给的城市说法归一成查表用的键(去后缀, 不猜拼音)。"""
    text = (city or "").strip()
    for suffix in _CITY_SUFFIXES:
        if len(text) > len(suffix) and text.endswith(suffix):
            return text[: -len(suffix)]
    return text


async def load_sources() -> dict[str, Any]:
    """读券源注册表; 找不到抛 ``FileNotFoundError``。"""
    tried: list[str] = []
    for root in _roots():
        path_str = str(root / _SOURCES_DIRNAME / _FILE_NAME)
        try:
            stat = await anyio.Path(path_str).stat()
        except OSError:
            tried.append(path_str)
            continue
        signature = (stat.st_mtime_ns, stat.st_size)
        hit = _cache.get(path_str)
        if hit is not None and hit[0] == signature:
            return hit[1]
        text = await anyio.Path(path_str).read_text(encoding="utf-8")
        data = yaml.safe_load(text)
        if not isinstance(data, dict):
            raise ValueError(f"券源注册表 {path_str} 顶层必须是 mapping, 实际是 {type(data).__name__}")
        _cache[path_str] = (signature, data)
        logger.debug(f"Voucher sources loaded: {path_str} ({len(text)} chars)")
        return data
    raise FileNotFoundError(f"找不到券源注册表 {_FILE_NAME}; 已试: {tried}")


def aggregator(sources: dict[str, Any], key: str) -> dict[str, Any]:
    """取一个聚合站的接入定义; 缺字段直接报错, 不做默认值兜底。

    默认值在这里等于"第二份配置": 数据文件里改了、代码里没改, 两边就会各说各话。
    """
    group = sources.get("aggregators")
    if not isinstance(group, dict):
        raise ValueError("券源注册表缺少 aggregators")
    entry = group.get(key)
    if not isinstance(entry, dict):
        raise KeyError(key)
    missing = [f for f in ("name", "tier", "topic_url", "city_codes") if not entry.get(f)]
    if missing:
        raise ValueError(f"券源注册表 aggregators.{key} 缺少: {missing}")
    return entry


def official(sources: dict[str, Any], key: str) -> dict[str, Any]:
    """取一个官方入口定义。缺字段直接报错, 不兜默认值(理由同 :func:`aggregator`)。"""
    group = sources.get("official")
    if not isinstance(group, dict):
        raise ValueError("券源注册表缺少 official")
    entry = group.get(key)
    if not isinstance(entry, dict):
        raise KeyError(key)
    missing = [f for f in ("name", "tier", "list_url") if not entry.get(f)]
    if missing:
        raise ValueError(f"券源注册表 official.{key} 缺少: {missing}")
    return entry


def topic_url(entry: dict[str, Any], code: str) -> str:
    """按城市代码拼出专题页地址(模式来自数据文件, 不写在代码里)。"""
    pattern = str(entry["topic_url"])
    if "{code}" not in pattern:
        raise ValueError(f"topic_url 缺少 {{code}} 占位: {pattern!r}")
    return pattern.replace("{code}", code)


def city_code(entry: dict[str, Any], city: str) -> str | None:
    """城市名 -> 子域代码。查不到返回 ``None``(**不猜拼音** —— 猜出来的代码会 404,
    而 404 与"这个城市没有消费券"在返回体里长得一样, 正是最贵的那类错)。"""
    codes = entry.get("city_codes")
    if not isinstance(codes, dict):
        return None
    normalized = normalize_city(city)
    for candidate in (city.strip(), normalized):
        value = codes.get(candidate)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def known_city_count(sources: dict[str, Any], key: str) -> int:
    """注册表里登记了多少个城市 —— 用于给调用方一个"这不是全空"的量感。"""
    try:
        entry = aggregator(sources, key)
    except KeyError, ValueError:
        return 0
    codes = entry.get("city_codes")
    return len(codes) if isinstance(codes, dict) else 0
