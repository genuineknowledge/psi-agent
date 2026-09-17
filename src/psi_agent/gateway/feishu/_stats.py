"""「本月执行」—— 一条聚合接口背后的口径与取数。

## 口径 (与页面上那一格必须逐字一致)

「本月执行」= **本自然月内跑过的会话数**, 按会话去重。一个会话算「跑过」, 满足任一条即可:

1. 它有**本月的 todo 段** —— ``{appdata}/todos/{sid}.segments.json`` 里某段的
   ``created_at`` / ``updated_at`` 落在本月;
2. 它没有本月的 todo 段, 但 **history 里有本月的 user/assistant 行** ——
   ``{appdata}/histories/{sid}.jsonl`` 里某行的 ``created_at`` 落在本月。

**为什么两条都要**: agent 直接回答、直接调工具的那些回合压根不写 todo。只看第 1 条, 就会把
它们算成「这个月没干活」, 而列表里它们的状态早就显示「已完成」了 —— 同一屏里两个数字互相
打架, 用户会以为这一格坏了(实测反馈过)。加上第 2 条之后, 两处口径才对得上。

只看 **user / assistant** 行: ``system`` 行在会话**创建时**就写下了, 把它算进去等于「只要
建过会话就算跑过」, 那是另一个指标。

## 为什么不再由前端聚合

前端原来对**每个会话**各打一次 ``/todo-segments`` 再自己数 —— 会话一多就是 N 次请求, 而且
「没写 todo 的会话」它根本看不到(段文件不存在, 拿不到任何时间戳)。现在一次请求拿走全部数字。

## 为什么只读文件尾部

「这个月跑过没有」只取决于**最后一条带时间的记录**: jsonl 是按时间追加的。实测最大的
history 有 6.6 MB, 为了一个计数把每个会话的全文读一遍不划算, 所以只读尾部 ``TAIL_BYTES``;
尾部第一行可能是半行, 丢掉即可。

## 时区

``created_at`` 是 UTC(``...Z``), 而「本自然月」是**用户日历上的月**。产品只服务飞书租户(中国
时区), 而中国没有夏令时 —— 所以默认按固定 **UTC+8** 切月, 精确且不依赖 tzdata; 别的时区用
``PSI_MONTH_TZ_OFFSET_HOURS`` 覆盖。
"""

from __future__ import annotations

import contextlib
import json
import os
import re
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any, Protocol

import anyio

from psi_agent._appdata import resolve_history_read_path

#: ``YYYY-MM``。**必须校验**: 这个值会参与切月, 放任意字符串进去只会得到空结果或异常。
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

#: 读 history 尾部的字节数。64 KiB 足够装下几十行(单行实测最大几 KB), 而计数只看最后一行。
TAIL_BYTES = 64 * 1024

#: 中国时区: 无夏令时, 固定偏移即精确 —— 不必依赖镜像里的 tzdata。
CN_TZ: tzinfo = timezone(timedelta(hours=8))


class _SegmentLister(Protocol):
    async def list_segments(self, session_id: str, *, appdata: str = "") -> list[dict[str, Any]]: ...


def stats_tz() -> tzinfo:
    """切月用的时区 —— 默认 UTC+8, 用 ``PSI_MONTH_TZ_OFFSET_HOURS`` 覆盖。"""
    raw = os.environ.get("PSI_MONTH_TZ_OFFSET_HOURS", "").strip()
    if not raw:
        return CN_TZ
    try:
        return timezone(timedelta(hours=float(raw)))
    except ValueError:
        return CN_TZ


def current_month(now: datetime | None = None, *, tz: tzinfo = CN_TZ) -> str:
    """当前自然月, ``YYYY-MM``。"""
    moment = now or datetime.now(UTC)
    return moment.astimezone(tz).strftime("%Y-%m")


def month_bounds(month: str, *, tz: tzinfo = CN_TZ) -> tuple[datetime, datetime]:
    """``YYYY-MM`` → ``[start, end)`` 两个 **UTC aware** 时刻。

    半开区间是刻意的: 用 ``<= end`` 会把下月 1 号 00:00 那一刻算进本月。跨时区时这一小时
    尤其容易错 —— 本月 1 号 00:00(+8) 就是上月最后一天 16:00Z。
    """
    if not MONTH_PATTERN.match(month):
        raise ValueError(f"month must be YYYY-MM, got {month!r}")
    year, mon = (int(part) for part in month.split("-"))
    start_local = datetime(year, mon, 1, tzinfo=tz)
    end_local = datetime(year + (mon // 12), (mon % 12) + 1, 1, tzinfo=tz)
    return start_local.astimezone(UTC), end_local.astimezone(UTC)


def parse_created_at(value: object) -> datetime | None:
    """history / 段落里的时间戳 → aware datetime; 解析不出来返回 ``None``。

    容忍两种形状: ISO 带 ``Z``(history 的写法)与带 ``+00:00`` 偏移(段落的写法, 由
    ``datetime.isoformat()`` 产出)。缺时间戳一律当「不知道」, 不猜成现在 —— 猜会把老会话算进本月。
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def in_window(value: object, start: datetime, end: datetime) -> bool:
    """时间戳是否落在 ``[start, end)`` 内。"""
    moment = parse_created_at(value)
    return moment is not None and start <= moment < end


async def tail_rows(path: anyio.Path, *, limit_bytes: int = TAIL_BYTES) -> list[dict[str, Any]]:
    """读文件尾部并解析成 JSON 行(新的在后), 供「最后一条记录是什么时候」这类判断用。

    文件比窗口短就直接读全; 尾部第一行可能是半行, **丢掉** —— 半行解析失败倒是其次, 真正
    的问题是它可能被误当成"这行没有时间戳", 于是把本月跑过的会话判成没跑过。
    """
    try:
        size = (await path.stat()).st_size
    except OSError:
        return []
    offset = max(0, size - max(1, limit_bytes))
    try:
        handle = await path.open("rb")
    except OSError:
        return []
    try:
        if offset:
            await handle.seek(offset)
        raw = await handle.read()
    except OSError:
        return []
    finally:
        with contextlib.suppress(Exception):
            await handle.aclose()
    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines()
    if offset and lines:
        lines = lines[1:]
    rows: list[dict[str, Any]] = []
    for line in lines:
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            rows.append(parsed)
    return rows


async def has_reply_in_window(path: anyio.Path, *, start: datetime, end: datetime) -> bool:
    """history 尾部里有没有**本月的 user/assistant 行**。

    只认这两个角色: ``system`` 行是建会话时写的, ``tool`` 行是回合内部产物 —— 把它们算进来
    等于把「建过会话」当成「跑过」。
    """
    for row in await tail_rows(path):
        if row.get("role") not in ("user", "assistant"):
            continue
        if in_window(row.get("created_at"), start, end):
            return True
    return False


async def session_ran_in_month(
    *,
    session_id: str,
    workspace: str,
    appdata_root: str,
    segments: list[dict[str, Any]],
    start: datetime,
    end: datetime,
) -> str | None:
    """这个会话本月跑过没有 → ``"checklist"`` / ``"reply"`` / ``None``。

    ``checklist`` = 有本月的 todo 段; ``reply`` = 没有段但 history 里有本月的问答。
    """
    for segment in segments:
        if in_window(segment.get("updated_at") or segment.get("created_at"), start, end):
            return "checklist"
    history = anyio.Path(
        await resolve_history_read_path(appdata_root=appdata_root, workspace=workspace, session_id=session_id)
    )
    if await has_reply_in_window(history, start=start, end=end):
        return "reply"
    return None


async def monthly_run_stats(
    *,
    sessions: list[tuple[str, str]],
    appdata_root: str,
    todom: _SegmentLister,
    start: datetime,
    end: datetime,
) -> dict[str, int]:
    """``[(session_id, workspace)]`` → ``{"count": n, "checklist": a, "reply": b}``。

    顺序执行而不是并发: 每个会话只是「读一个小 JSON + 读一个文件尾部」, 本地磁盘上是亚毫秒级;
    为它引一层并发限制器, 换来的是更难读的代码和一个新的失败面。
    """
    counts = {"count": 0, "checklist": 0, "reply": 0}
    for session_id, workspace in sessions:
        try:
            segments = await todom.list_segments(session_id, appdata=appdata_root)
        except Exception:
            segments = []
        bucket = await session_ran_in_month(
            session_id=session_id,
            workspace=workspace,
            appdata_root=appdata_root,
            segments=segments,
            start=start,
            end=end,
        )
        if bucket is None:
            continue
        counts["count"] += 1
        counts[bucket] += 1
    return counts
