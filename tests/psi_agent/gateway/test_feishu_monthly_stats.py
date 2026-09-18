"""「本月执行」的口径与取数 —— 切月、尾部读、两个桶。

这一格以前是前端算的(N 个会话打 N 次 ``/todo-segments``), 而且只看 todo 段: agent 直接回答
的那些回合压根不写 todo, 于是被算成「这个月没干活」, 而列表里它们的状态早就显示「已完成」。
现在一次请求拿到数字, 两个来源都是判据覆盖到的。

为什么这些用例值得写:

* **切月**是这一格里唯一容易错到"看不出来"的地方 —— ``created_at`` 是 UTC, 而月是用户日历上
  的月。北京时间 9 月 1 日 00:30 在 UTC 里还是 8 月 31 日 16:30: 用 UTC 切月, 那一整个早上的
  会话会掉进上个月, 而页面上只是少了一个数字。
* **尾部读**: history 实测最大 6.6 MB, 为了一个计数读全文不划算。但尾部第一行往往是半行 ——
  把它当成"这行没有时间戳"就会漏掉本月跑过的会话, 所以要显式丢掉并有用例钉住。
* **两个桶**: 只看 todo 段那一版就是缺了 ``reply`` 这个桶。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest

from psi_agent.gateway.feishu._stats import (
    CN_TZ,
    current_month,
    has_reply_in_window,
    in_window,
    iter_rows_backwards,
    month_bounds,
    monthly_run_stats,
    parse_created_at,
    session_ran_in_month,
    stats_tz,
)

_SEPT = month_bounds("2026-09", tz=CN_TZ)
_AUG_END = datetime(2026, 8, 31, 15, 59, tzinfo=UTC)  # 北京时间 8/31 23:59
_SEPT_START = datetime(2026, 8, 31, 16, 0, tzinfo=UTC)  # 北京时间 9/1 00:00
_OCT_START = datetime(2026, 9, 30, 16, 0, tzinfo=UTC)  # 北京时间 10/1 00:00


def test_month_bounds_are_half_open_in_utc() -> None:
    """月界按 **UTC+8** 切, 且是 ``[start, end)`` —— 用 ``<= end`` 会把下月 1 号 00:00 算进本月。"""
    start, end = _SEPT

    assert start == _SEPT_START, f"9 月起点应是北京时间 9/1 00:00 = UTC 8/31 16:00, 实得 {start}"
    assert end == _OCT_START, f"9 月终点应是北京时间 10/1 00:00 = UTC 9/30 16:00, 实得 {end}"
    # 边界两侧各一分钟。
    assert in_window("2026-08-31T15:59:00Z", start, end) is False, "北京时间 8/31 23:59 属于 8 月"
    assert in_window("2026-08-31T16:00:00Z", start, end) is True, "北京时间 9/1 00:00 属于 9 月"
    assert in_window("2026-09-30T15:59:00Z", start, end) is True
    assert in_window("2026-09-30T16:00:00Z", start, end) is False, "北京时间 10/1 00:00 已不属于 9 月"


def test_bad_month_is_rejected_not_silently_empty() -> None:
    """形状不对要抛 —— 回 0 会把「参数写错了」伪装成「本月什么都没跑」。"""
    for bad in ("2026-13", "2026-9", "202609", "", "2026-09-01"):
        with pytest.raises(ValueError):
            month_bounds(bad, tz=CN_TZ)


def test_current_month_uses_the_product_timezone() -> None:
    """UTC 里还是 8 月、北京时间已是 9 月 1 日凌晨 —— 这一格必须算 9 月。"""
    moment = datetime(2026, 8, 31, 17, 0, tzinfo=UTC)  # 北京时间 9/1 01:00

    assert current_month(moment, tz=CN_TZ) == "2026-09"
    assert current_month(moment, tz=UTC) == "2026-08"


def test_stats_tz_defaults_to_china_and_can_be_overridden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PSI_MONTH_TZ_OFFSET_HOURS", raising=False)
    assert stats_tz() == CN_TZ
    monkeypatch.setenv("PSI_MONTH_TZ_OFFSET_HOURS", "0")
    assert stats_tz() == UTC
    monkeypatch.setenv("PSI_MONTH_TZ_OFFSET_HOURS", "不是数字")
    assert stats_tz() == CN_TZ, "环境变量写坏了要退回默认, 而不是让整条接口 500"


def test_created_at_parses_both_spellings_and_refuses_the_unknown() -> None:
    """history 写 ``Z``, 段落写 ``+00:00``; 缺时间戳一律当「不知道」, 不猜成现在。"""
    assert parse_created_at("2026-09-16T11:29:44.286Z") == datetime(2026, 9, 16, 11, 29, 44, 286000, UTC)
    assert parse_created_at("2026-09-16T11:29:44+00:00") == datetime(2026, 9, 16, 11, 29, 44, tzinfo=UTC)
    assert parse_created_at("2026-09-16T19:29:44+08:00") == datetime(2026, 9, 16, 11, 29, 44, tzinfo=UTC)
    for unknown in (None, "", "   ", "昨天", 1726484984, {}):
        assert parse_created_at(unknown) is None
    assert in_window(None, *_SEPT) is False, "没有时间戳的会话不该被算进本月"


@pytest.mark.anyio
async def test_iter_rows_backwards_reads_lines_newest_first(tmp_path: Path) -> None:
    """从尾部按**行**回读: 新的在前, 半行拼得回来, 坏行跳过。"""
    path = tmp_path / "history.jsonl"
    rows = [{"role": "user", "content": f"第 {i} 行", "created_at": "2026-09-16T00:00:00Z"} for i in range(200)]
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")

    got = [row async for row in iter_rows_backwards(anyio.Path(path))]

    assert [row["content"] for row in got] == [f"第 {i} 行" for i in reversed(range(200))]


@pytest.mark.anyio
async def test_a_row_bigger_than_the_old_tail_window_cannot_hide_the_month(tmp_path: Path) -> None:
    """**被修掉的那个错数**: 单行比尾部窗口还大时, 本月跑过的会话曾被判成「没跑过」。

    旧实现读尾部 ``TAIL_BYTES`` (64 KiB) 再在窗口里找问答行 —— 而**单行可以比窗口大** (实测该
    目录 23 个 history 里 21 个存在 >64 KiB 的单行, 最大 system 行 288,631 字节), 一行就把窗口
    吃光。下面第一段断言是**控制实验**: 那条问答行确实落在旧窗口之外, 否则这条用例什么都没测。
    """
    path = tmp_path / "h.jsonl"
    big = "x" * (200 * 1024)
    path.write_text(
        json.dumps({"role": "user", "created_at": "2026-09-02T00:00:00Z"})
        + "\n"
        + json.dumps({"role": "system", "content": big})
        + "\n",
        encoding="utf-8",
    )

    old_window = path.read_bytes()[-64 * 1024 :]
    assert b'"role": "user"' not in old_window, "构造失败: 旧窗口本来就能看见问答行, 这条用例没测到东西"

    assert await has_reply_in_window(anyio.Path(path), start=_SEPT[0], end=_SEPT[1]) is True


@pytest.mark.anyio
async def test_a_later_month_row_does_not_veto_an_earlier_month(tmp_path: Path) -> None:
    """查**往月**时, 末尾那些更晚的行要跳过而不是一票否决 —— 「只看最后一条」在这里是错的。

    ``?month=`` 允许查任意一月, 所以判据只能是「窗口内有没有」: 从尾部往前, 第一条早于窗口结束
    时刻的问答行才定论。
    """
    path = tmp_path / "h.jsonl"
    path.write_text(
        json.dumps({"role": "user", "created_at": "2026-08-02T00:00:00Z"})
        + "\n"
        + json.dumps({"role": "assistant", "created_at": "2026-09-03T00:00:00Z"})
        + "\n",
        encoding="utf-8",
    )
    august = month_bounds("2026-08", tz=CN_TZ)

    assert await has_reply_in_window(anyio.Path(path), start=august[0], end=august[1]) is True, (
        "8 月确实跑过 (8/2 那条问答), 末尾 9 月那条不该把它否掉"
    )
    assert await has_reply_in_window(anyio.Path(path), start=_SEPT[0], end=_SEPT[1]) is True
    assert await has_reply_in_window(anyio.Path(path), start=_OCT_START, end=month_bounds("2026-10")[1]) is False


@pytest.mark.anyio
async def test_has_reply_in_window_only_counts_user_and_assistant(tmp_path: Path) -> None:
    """``system`` 行是建会话时写的, ``tool`` 行是回合内部产物 —— 都不是「跑过一轮」。"""

    async def write(rows: list[dict[str, Any]]) -> anyio.Path:
        path = tmp_path / f"h{len(rows)}-{rows[0].get('role')}.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return anyio.Path(path)

    system_only = await write([{"role": "system", "created_at": "2026-09-02T00:00:00Z"}])
    tool_only = await write([{"role": "tool", "created_at": "2026-09-02T00:00:00Z"}])
    last_month = await write([{"role": "user", "created_at": "2026-08-01T00:00:00Z"}])
    this_month = await write([{"role": "assistant", "created_at": "2026-09-02T00:00:00Z"}])

    assert await has_reply_in_window(system_only, start=_SEPT[0], end=_SEPT[1]) is False
    assert await has_reply_in_window(tool_only, start=_SEPT[0], end=_SEPT[1]) is False
    assert await has_reply_in_window(last_month, start=_SEPT[0], end=_SEPT[1]) is False
    assert await has_reply_in_window(this_month, start=_SEPT[0], end=_SEPT[1]) is True


@pytest.mark.anyio
async def test_session_bucket_prefers_checklist_then_falls_back_to_history(tmp_path: Path) -> None:
    """有本月的 todo 段算 ``checklist``; 没有段但有本月的问答算 ``reply``; 都没有就是没跑过。"""
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    ws = str(tmp_path / "ws")

    (histories / "s-checklist.jsonl").write_text(
        json.dumps({"role": "user", "created_at": "2026-09-03T00:00:00Z"}) + "\n", encoding="utf-8"
    )
    (histories / "s-reply.jsonl").write_text(
        json.dumps({"role": "user", "created_at": "2026-09-03T00:00:00Z"}) + "\n", encoding="utf-8"
    )
    (histories / "s-old.jsonl").write_text(
        json.dumps({"role": "user", "created_at": "2026-07-03T00:00:00Z"}) + "\n", encoding="utf-8"
    )

    async def bucket(session_id: str, segments: list[dict[str, Any]]) -> str | None:
        return await session_ran_in_month(
            session_id=session_id,
            workspace=ws,
            appdata_root=str(appdata),
            segments=segments,
            start=_SEPT[0],
            end=_SEPT[1],
        )

    assert await bucket("s-checklist", [{"id": "a", "updated_at": "2026-09-05T00:00:00+00:00"}]) == "checklist"
    # 段和 history **都不是**本月 → 没跑过。用 s-old: 它的 history 是 7 月的。
    assert await bucket("s-old", [{"id": "a", "created_at": "2026-08-05T00:00:00+00:00"}]) is None, (
        "段是上个月的、history 也是上个月的 → 不该算进本月"
    )
    assert await bucket("s-checklist", [{"id": "a", "created_at": "2026-08-05T00:00:00+00:00"}]) == "reply"
    assert await bucket("s-reply", []) == "reply"
    assert await bucket("s-old", []) is None
    # 段和 history 都指向本月 → 只算一次, 且归 checklist(有清单的那档)。
    assert await bucket("s-checklist", [{"id": "a", "created_at": "2026-09-01T00:00:00+00:00"}]) == "checklist"


@pytest.mark.anyio
async def test_monthly_run_stats_dedupes_and_counts_both_buckets(tmp_path: Path) -> None:
    """总数按**会话**去重, 并给出两个桶 —— 页面上的提示要能说清这个数是怎么来的。"""
    appdata = tmp_path / "appdata"
    histories = appdata / "histories"
    histories.mkdir(parents=True)
    ws = str(tmp_path / "ws")
    for name, when in (
        ("s1", "2026-09-03T00:00:00Z"),
        ("s2", "2026-09-04T00:00:00Z"),
        ("s3", "2026-07-04T00:00:00Z"),
    ):
        (histories / f"{name}.jsonl").write_text(
            json.dumps({"role": "user", "created_at": when}) + "\n", encoding="utf-8"
        )

    class FakeTodos:
        """s1 有本月的段(而且有两段 —— 去重必须把它算成一个会话)。"""

        async def list_segments(self, session_id: str, *, appdata: str = "") -> list[dict[str, Any]]:
            if session_id == "s1":
                return [
                    {"id": "a", "updated_at": "2026-09-05T00:00:00+00:00"},
                    {"id": "b", "updated_at": "2026-09-06T00:00:00+00:00"},
                ]
            return []

    stats = await monthly_run_stats(
        sessions=[("s1", ws), ("s2", ws), ("s3", ws), ("s4", ws)],
        appdata_root=str(appdata),
        todom=FakeTodos(),
        start=_SEPT[0],
        end=_SEPT[1],
    )

    assert stats == {"count": 2, "checklist": 1, "reply": 1, "sessions": 4, "unlisted": 0}, stats
    assert stats["count"] == stats["checklist"] + stats["reply"], "两个桶必须恰好拼出总数"
    assert stats["sessions"] == 4, "人口 = 传进来的会话数, 与 /feishu/sessions 同一份"


@pytest.mark.anyio
async def test_segment_counts_for_every_month_its_timestamps_touch(tmp_path: Path) -> None:
    """段的 ``created_at`` **与** ``updated_at`` 任一落在窗口内都算 —— 两个桶的语义必须一致。

    只判 ``updated_at or created_at`` 的后果是: 一段 8 月开、9 月还在改, 查 8 月时会被漏掉
    (段明明 8 月就开过), 而它与 ``reply`` 桶的数要相加成一个数 —— 语义不对齐, 和数就没法解释。
    """
    appdata = tmp_path / "appdata"
    (appdata / "histories").mkdir(parents=True)
    august = month_bounds("2026-08", tz=CN_TZ)
    opened_aug_updated_sep = [
        {"id": "a", "created_at": "2026-08-05T00:00:00+00:00", "updated_at": "2026-09-20T00:00:00+00:00"}
    ]

    async def bucket(session_id: str, segments: list[dict[str, Any]], window: tuple[datetime, datetime]) -> str | None:
        return await session_ran_in_month(
            session_id=session_id,
            workspace=str(tmp_path / "ws"),
            appdata_root=str(appdata),
            segments=segments,
            start=window[0],
            end=window[1],
        )

    assert await bucket("s1", opened_aug_updated_sep, august) == "checklist", "8 月开过 → 8 月跑过"
    assert await bucket("s1", opened_aug_updated_sep, _SEPT) == "checklist", "9 月改过 → 9 月也跑过"
    assert await bucket("s1", opened_aug_updated_sep, month_bounds("2026-10", tz=CN_TZ)) is None, (
        "10 月既没开过也没改过 → 不该算进 10 月"
    )


@pytest.mark.anyio
async def test_unlisted_sessions_are_reported_not_silently_dropped(tmp_path: Path) -> None:
    """磁盘上有本月活动、却不在会话注册表里 → **报出来**, 不让「两个数字对不上」无处可查。

    人口只能来自注册表 (归属判定只有它答得出), 于是注册表里没有的会话会被排除 —— 用户自己数
    ``todos/`` 与页面上那一格因此可能不一致。差异本身进响应 (``unlisted``), 而不是被静默吞掉。
    """
    appdata = tmp_path / "appdata"
    (appdata / "histories").mkdir(parents=True)
    own_sid = "feishu-ou_alice"
    (appdata / "histories" / f"{own_sid}.jsonl").write_text(
        json.dumps({"role": "user", "created_at": "2026-09-03T00:00:00Z"}) + "\n", encoding="utf-8"
    )

    class FakeFm:
        def session_id_for(self, key: str) -> str:
            return f"feishu-{key}" if key else ""

        def workspace_for(self, key: str) -> str:
            return str(tmp_path / "ws" / key)

    class EmptyTodos:
        async def list_segments(self, session_id: str, *, appdata: str = "") -> list[dict[str, Any]]:
            return []

    orphan = await monthly_run_stats(
        sessions=[],
        appdata_root=str(appdata),
        todom=EmptyTodos(),
        start=_SEPT[0],
        end=_SEPT[1],
        fm=FakeFm(),
        open_id="ou_alice",
    )
    assert orphan == {"count": 0, "checklist": 0, "reply": 0, "sessions": 0, "unlisted": 1}, orphan

    listed = await monthly_run_stats(
        sessions=[(own_sid, str(tmp_path / "ws" / "ou_alice"))],
        appdata_root=str(appdata),
        todom=EmptyTodos(),
        start=_SEPT[0],
        end=_SEPT[1],
        fm=FakeFm(),
        open_id="ou_alice",
    )
    assert listed == {"count": 1, "checklist": 0, "reply": 1, "sessions": 1, "unlisted": 0}, listed
    # 别人 (uuid 网页会话) 的活动无从归属 → 宁可少报, 不把别人的算给这个人。
    stranger = await monthly_run_stats(
        sessions=[],
        appdata_root=str(appdata),
        todom=EmptyTodos(),
        start=_SEPT[0],
        end=_SEPT[1],
        fm=FakeFm(),
        open_id="ou_bob",
    )
    assert stranger["unlisted"] == 0, stranger


@pytest.mark.anyio
async def test_a_broken_session_store_does_not_zero_the_whole_metric(tmp_path: Path) -> None:
    """一个会话的段文件读坏了, 不该让整格数字消失 —— 那一格是概览, 不是账本。"""
    appdata = tmp_path / "appdata"
    (appdata / "histories").mkdir(parents=True)
    ws = str(tmp_path / "ws")

    class ExplodingTodos:
        async def list_segments(self, session_id: str, *, appdata: str = "") -> list[dict[str, Any]]:
            raise OSError("磁盘读失败")

    stats = await monthly_run_stats(
        sessions=[("s1", ws)],
        appdata_root=str(appdata),
        todom=ExplodingTodos(),
        start=_SEPT[0],
        end=_SEPT[1],
    )

    assert stats == {"count": 0, "checklist": 0, "reply": 0, "sessions": 1, "unlisted": 0}


def test_august_boundary_sample_is_where_utc_would_have_been_wrong() -> None:
    """把「用 UTC 切月会错」这件事写成一个可执行的例子, 免得将来有人"简化"回去。"""
    moment = "2026-08-31T17:00:00Z"  # 北京时间 9/1 01:00
    utc_start, utc_end = month_bounds("2026-08", tz=UTC)
    assert in_window(moment, utc_start, utc_end) is True, "按 UTC 切月, 这条会话会落在 8 月"
    assert in_window(moment, *_SEPT) is True, "按产品时区(+8)切月, 它才是 9 月"
    assert _SEPT_START - timedelta(hours=8) == datetime(2026, 8, 31, 8, 0, tzinfo=UTC)
