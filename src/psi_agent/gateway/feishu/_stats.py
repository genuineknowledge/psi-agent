"""「本月执行」—— 一条聚合接口背后的口径与取数。

## 口径 (与页面上那一格必须逐字一致)

「本月执行」= **本自然月内跑过的会话数**, 按会话去重。一个会话算「跑过」, 满足任一条即可:

1. 它有**本月的 todo 段** —— ``{appdata}/todos/{sid}.segments.json`` 里某段的
   ``created_at`` / ``updated_at`` 落在本月;
2. 它没有本月的 todo 段, 但 **history 里有本月的 user/assistant 行** ——
   ``{appdata}/histories/{sid}.jsonl`` 里某行的 ``created_at`` 落在本月。

**两个桶的时间戳语义必须一致**, 因为它们要相加成一个数: 桶 1 判「段在本月开过**或**改过」
(两个时间戳任一落在窗口内), 桶 2 判「本月有问答行」—— 同一条规则, **窗口内有过活动**。
两个坑都踩过: 只判 ``updated_at or created_at`` 会让「上个月开的段, 被问到上个月」时漏判
(段确实在上月开过, 而 updated_at 已经跑到本月); 两个桶各按各的语义取数, 则和数本身没法解释。

**为什么两条都要**: agent 直接回答、直接调工具的那些回合压根不写 todo。只看第 1 条, 就会把
它们算成「这个月没干活」, 而列表里它们的状态早就显示「已完成」了 —— 同一屏里两个数字互相
打架, 用户会以为这一格坏了(实测反馈过)。加上第 2 条之后, 两处口径才对得上。

只看 **user / assistant** 行: ``system`` 行在会话**创建时**就写下了, 把它算进去等于「只要
建过会话就算跑过」, 那是另一个指标。

## 为什么不再由前端聚合

前端原来对**每个会话**各打一次 ``/todo-segments`` 再自己数 —— 会话一多就是 N 次请求, 而且
「没写 todo 的会话」它根本看不到(段文件不存在, 拿不到任何时间戳)。现在一次请求拿走全部数字。

## 人口 (分母) 与会话注册表

要遍历的会话来自**会话注册表** (``state/latest.json`` 恢复出来的那些), 不是磁盘上的数据文件。
归属判定 (这条会话是不是这个人的) 只有注册表答得出 —— 一条 ``histories/*.jsonl`` 里既没有
workspace 也没有主人。代价是: 注册表里没有、而 ``todos/`` 里却有本月段的会话会被**静默排除**,
于是出现「用户自己数 ``todos/`` 得 7, 这一格写 4」。

处置**不是**把磁盘上的文件也算进来 (那会把别人的会话算给这个人, 也会让这一格与
``/feishu/sessions`` 的人口不一致), 而是把**差异本身**报出去: 响应里的 ``unlisted`` 就是
「有本月活动、但不在注册表里、且**能确认属于本人**」的会话数。能确认的范围是私聊会话
(``feishu-<open_id>`` 由 open_id 确定性派生, 故可判); 网页新建的 uuid 会话缺 workspace,
无从归属 —— 宁可少报, 也不把别人的算给你。

## 为什么从尾部按行回读

「这个月跑过没有」只取决于**第一条早于窗口结束时刻的问答行**: jsonl 是按时间追加的, 从尾部
往前读, 遇到的第一条 ``created_at < end`` 的 user/assistant 行就定了 —— 它在窗口内即 True,
早于窗口起点即 False (再往前只会更早)。所以正常情况下只读文件末尾的几行。

**判据不是「尾部固定字节数」**。原先读尾部 ``TAIL_BYTES`` (64 KiB) 再在窗口里找, 而**单行可以
比窗口还大**: 实测该目录 23 个 history 里 21 个存在 >64 KiB 的单行, 最大 system 行 288,631
字节。一行就能吃光窗口, 于是本月跑过的会话会被判成没跑过, 而屏幕上那条任务写着「已完成」
—— 一个只在「末行恰好是大行」时才发作的错数。现在按**行**回读(不固定字节数), 只保留一个防
病态文件的内存闸门 ``MAX_BACKSCAN_BYTES``。

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
from collections.abc import AsyncGenerator
from contextlib import aclosing
from datetime import UTC, datetime, timedelta, timezone, tzinfo
from typing import Any, Protocol

import anyio
from loguru import logger

from psi_agent._appdata import resolve_history_read_path

#: ``YYYY-MM``。**必须校验**: 这个值会参与切月, 放任意字符串进去只会得到空结果或异常。
MONTH_PATTERN = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

#: 从尾部往前读的**块**大小。它不是判据 —— 判据是「按行」, 块多大只影响系统调用次数。
BACKSCAN_CHUNK_BYTES = 64 * 1024

#: 回读上限, 只防病态文件 (单行几十 MB 之类) 把内存吃光。**不是判据的一部分**:
#: 实测最大的单行是 288,631 字节 (system 行), 而这里是它的 29 倍, 任何真实 history 都够不到。
MAX_BACKSCAN_BYTES = 8 * 1024 * 1024

#: 中国时区: 无夏令时, 固定偏移即精确 —— 不必依赖镜像里的 tzdata。
CN_TZ: tzinfo = timezone(timedelta(hours=8))


class _SegmentLister(Protocol):
    async def list_segments(self, session_id: str, *, appdata: str = "") -> list[dict[str, Any]]: ...


class _SessionIdSource(Protocol):
    """本模块要用到的 ``FeishuManager`` 两处 —— 按协议取而不是 import 那个类。

    判据只有「这条文件是不是本人的」一条, 因此只需 id / workspace 两个派生函数; 按协议取让
    本模块的用例不必建一个真的 ``FeishuManager`` (那要 SessionManager + task group)。
    参数名与 ``FeishuManager`` 一致 (``key``, 值是私聊的 open_id): 本协议是**它**的结构描述,
    名字对不上就白描述一场。
    """

    def session_id_for(self, key: str) -> str: ...

    def workspace_for(self, key: str) -> str: ...


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


async def iter_rows_backwards(
    path: anyio.Path,
    *,
    max_bytes: int = MAX_BACKSCAN_BYTES,
) -> AsyncGenerator[dict[str, Any]]:
    """把 jsonl 从**末尾往前**逐行解析出来 (新的在前), 供「第一条满足条件的行」类判据用。

    按行而不是按固定字节窗口: 单行可以比窗口大 (实测有 288,631 字节的 system 行), 固定窗口
    在这种情况下会**整段被一行吃掉**, 于是判据退化成「没找到」。块的边界与行的边界无关 ——
    跨块的行由 ``pending`` 拼回来, 故任何一行多大都读得完整 (上限见 ``MAX_BACKSCAN_BYTES``)。

    坏行 / 非对象行跳过: 历史里混进一行坏 JSON 不该让整个计数失败。

    调用方**必须在找到答案时立刻停**: 本生成器是惰性的, 但停在半途要用 ``aclosing()``
    关掉它 (见根 AGENTS.md「消费 async generator 必须用 aclosing()」), 否则底下那个文件句柄
    要等 GC。
    """
    try:
        size = (await path.stat()).st_size
        handle = await path.open("rb")
    except OSError:
        return
    try:
        position = size
        scanned = 0
        pending = b""  # 已读部分**开头**那段不完整的行, 等更早的块来补齐
        while position > 0 and scanned < max_bytes:
            step = min(BACKSCAN_CHUNK_BYTES, position, max_bytes - scanned)
            position -= step
            await handle.seek(position)
            block = await handle.read(step)
            scanned += step
            parts = (block + pending).split(b"\n")
            pending = parts[0]
            for raw in reversed(parts[1:]):
                row = _parse_row(raw)
                if row is not None:
                    yield row
        if position == 0:
            row = _parse_row(pending)
            if row is not None:
                yield row
        else:
            logger.warning(
                f"[feishu] 统计回读达到上限 {max_bytes} 字节仍未读完 {str(path)!r} —— "
                f"结果可能少算, 请检查该 history 是否有异常大的单行"
            )
    finally:
        with contextlib.suppress(Exception):
            await handle.aclose()


def _parse_row(raw: bytes) -> dict[str, Any] | None:
    """一行字节 → 对象; 空行 / 坏 JSON / 非对象一律 ``None``。"""
    if not raw.strip():
        return None
    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


async def has_reply_in_window(path: anyio.Path, *, start: datetime, end: datetime) -> bool:
    """history 里**本月有没有跑过** —— 判据是窗口内有没有 user/assistant 行。

    从尾部往前读, 在**第一条早于窗口结束时刻**的 user/assistant 行上定论: 落在窗口内即
    True, 早于窗口起点即 False (jsonl 按时间追加, 再往前只会更早)。

    只认这两个角色: ``system`` 行是建会话时写的, ``tool`` 行是回合内部产物 —— 把它们算进来
    等于把「建过会话」当成「跑过」。
    """
    async with aclosing(iter_rows_backwards(path)) as rows:
        async for row in rows:
            if row.get("role") not in ("user", "assistant"):
                continue
            moment = parse_created_at(row.get("created_at"))
            if moment is None:
                continue
            if moment >= end:
                # 比窗口还晚 (查的是往月时的常见情形): 继续往前找更早那批。
                continue
            return start <= moment < end
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

    段这一侧判的是**两个时间戳任一落在窗口内** (开过**或**改过), 与 ``reply`` 的「窗口内有过
    活动」同一条规则 —— 只看 ``updated_at or created_at`` 会漏掉「上月开、上月被问到」的段。
    """
    for segment in segments:
        if in_window(segment.get("created_at"), start, end) or in_window(segment.get("updated_at"), start, end):
            return "checklist"
    history = anyio.Path(
        await resolve_history_read_path(appdata_root=appdata_root, workspace=workspace, session_id=session_id)
    )
    if await has_reply_in_window(history, start=start, end=end):
        return "reply"
    return None


async def unlisted_run_count(
    *,
    open_id: str,
    fm: _SessionIdSource,
    appdata_root: str,
    todom: _SegmentLister,
    known: set[str],
    start: datetime,
    end: datetime,
) -> int:
    """有本月活动、**不在会话注册表里**、且能确认属于 *open_id* 的会话数 (0 或 1)。

    只覆盖私聊会话: 它的 id 由 open_id 确定性派生 (``fm.session_id_for``), 所以「这条
    ``todos/`` / ``histories/`` 文件是不是本人的」判得出来; 网页新建的 uuid 会话缺 workspace,
    注册表里没有它时无从归属 —— **宁可少报**, 不把别人的活动算给这个人。

    报这个数的理由见模块头「人口 (分母) 与会话注册表」: 用户自己数 ``todos/`` 与页面上那一格
    对不上时, 差异必须有处可查, 而不是被静默吞掉。
    """
    session_id = fm.session_id_for(open_id)
    if not session_id or session_id in known:
        return 0
    try:
        segments = await todom.list_segments(session_id, appdata=appdata_root)
    except Exception:
        segments = []
    bucket = await session_ran_in_month(
        session_id=session_id,
        workspace=fm.workspace_for(open_id),
        appdata_root=appdata_root,
        segments=segments,
        start=start,
        end=end,
    )
    return 1 if bucket else 0


async def monthly_run_stats(
    *,
    sessions: list[tuple[str, str]],
    appdata_root: str,
    todom: _SegmentLister,
    start: datetime,
    end: datetime,
    fm: _SessionIdSource | None = None,
    open_id: str = "",
) -> dict[str, int]:
    """``[(session_id, workspace)]`` → 口径里的各个数字。

    ``count`` / ``checklist`` / ``reply`` 是那一格要的三项; ``sessions`` 是**人口** (注册表里
    本人可见的会话数, 与 ``/feishu/sessions`` 同一份); ``unlisted`` 是有本月活动却不在人口里、
    且能确认属于本人的会话数 —— 后两项存在的唯一目的是让「两个数字为什么不一样」可查。

    顺序执行而不是并发: 每个会话只是「读一个小 JSON + 读一个文件尾部」, 本地磁盘上是亚毫秒级;
    为它引一层并发限制器, 换来的是更难读的代码和一个新的失败面。
    """
    counts = {"count": 0, "checklist": 0, "reply": 0, "sessions": len(sessions), "unlisted": 0}
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
    if fm is not None and open_id:
        counts["unlisted"] = await unlisted_run_count(
            open_id=open_id,
            fm=fm,
            appdata_root=appdata_root,
            todom=todom,
            known={session_id for session_id, _ in sessions},
            start=start,
            end=end,
        )
    return counts
