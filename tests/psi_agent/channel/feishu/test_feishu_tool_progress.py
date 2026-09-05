"""飞书卡片上的工具进度状态行 —— 判据全部走 ``_stream_reply`` + 真的控制器。

**为什么不 mock 控制器。** 这一段的风险全在 ``append`` / ``set_content`` 与
``merge_streaming_text`` 的相互作用, 以及 ``_ensure_started`` 的懒建卡时机 ——
换成 ``AsyncMock`` 这三件事全都消失, 判据会假绿。所以这里装的是真的
``MarkdownStreamController``, 只把它底下四个 cardkit HTTP 调用换成记录器:
断言落在「发给飞书的卡片内容」上, 与用户看到的东西同层。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from lark_channel.channel.outbound.streaming.markdown_stream import MarkdownStreamController

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import ReasoningChunk, TextChunk
from psi_agent.channel.feishu import client
from psi_agent.channel.feishu._tool_status import GENERIC_TOOL_LABEL


class _CardRecorder:
    """替掉控制器底下的四个 cardkit HTTP 调用, 其余逻辑照真跑。"""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.updates: list[str] = []
        self.finished = 0

    async def create_card_instance(self, spec: dict[str, Any]) -> str:
        self.create_calls.append(spec)
        return "card_1"

    async def send_card_by_reference(self, to: str, card_id: str, **kw: Any) -> Any:
        return SimpleNamespace(message_id="om_sent")

    async def update_card_element_content(self, card_id: str, element_id: str, content: str, seq: int) -> None:
        self.updates.append(content)

    async def finish_streaming_card(self, card_id: str, seq: int) -> None:
        self.finished += 1

    # -- 断言用的视图 ---------------------------------------------------------
    @property
    def final(self) -> str:
        """飞书最后收到的那份内容 —— 用户停下来时看到的东西。"""
        return self.updates[-1] if self.updates else ""

    @property
    def everything(self) -> str:
        """所有发出去过的内容拼一起 —— 用来断言某个串**从未**出现在卡片上。"""
        return "\n".join(self.updates)


def _recording_channel(recorder: _CardRecorder) -> tuple[MagicMock, list[str]]:
    """``channel.stream`` 真去建控制器并跑 ``_produce``; 同时记下 set_content 的次数。"""
    channel = MagicMock()
    channel.send = AsyncMock()
    set_content_args: list[str] = []

    async def _stream(chat_id: str, payload: dict, options: dict | None = None) -> None:
        ctl = MarkdownStreamController(
            to=chat_id,
            receive_id_type="chat_id",
            reply_to=None,
            reply_in_thread=None,
            create_card_instance=recorder.create_card_instance,
            send_card_by_reference=recorder.send_card_by_reference,
            update_card_element_content=recorder.update_card_element_content,
            finish_streaming_card=recorder.finish_streaming_card,
        )
        real_set_content = ctl.set_content

        async def _spy(full: str) -> None:
            set_content_args.append(full)
            await real_set_content(full)

        # 只包一层记录再转交真实实现 —— 计数用, 行为不变。
        setattr(ctl, "set_content", _spy)  # noqa: B010
        await ctl.run(payload["markdown"])

    channel.stream = AsyncMock(side_effect=_stream)
    return channel, set_content_args


def _core_yielding(*chunks: Any) -> ChannelCore:
    async def _post(_chunks: list[Any]) -> Any:
        for c in chunks:
            yield c

    return cast(ChannelCore, SimpleNamespace(post=_post))


def _tool_call(name: str, args_text: str = "{}") -> ReasoningChunk:
    """构造与生产同形的 tool_call chunk —— 文本里带完整参数, 正如流上那样。"""
    return ReasoningChunk(text=f"[Tool Call: {name}({args_text})]", kind="tool_call", tool_name=name)


def _tool_result(name: str, result: str = "ok") -> ReasoningChunk:
    return ReasoningChunk(text=f"[Tool Result: {result}]", kind="tool_result", tool_name=name)


# -- 判据 1: 状态行出现 --------------------------------------------------------


@pytest.mark.anyio
async def test_tool_call_renders_chinese_alias_on_the_card():
    """收到 tool_call 后, 卡片上要出现该工具的中文别名。"""
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(_tool_call("search_content"))

    await client._stream_reply(channel, core, "oc_1", [], reply_to=None, sender_open_id="ou_1")

    assert "正在检索代码库" in rec.everything


# -- 判据 2: 不泄漏参数 --------------------------------------------------------


@pytest.mark.anyio
async def test_status_line_never_leaks_tool_arguments():
    """带私密路径的参数一个字都不能上卡片。

    ``reasoning`` 文本里就带着完整 ``json.dumps(args)``, 直接贴上去是最省事也最
    危险的写法 —— 这条判据钉住「只显示别名」。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    secret = "/home/zhouyi/.private/salary-2026.xlsx"
    core = _core_yielding(
        _tool_call("read", f'{{"path": "{secret}"}}'),
        _tool_result("read", f"月薪明细 {secret} 共 42 行"),
    )

    await client._stream_reply(channel, core, "oc_1", [], reply_to=None, sender_open_id="ou_1")

    assert secret not in rec.everything
    assert "salary" not in rec.everything
    assert "月薪明细" not in rec.everything
    # 别名本身还是要在 —— 否则「不泄漏」靠的是「什么都没渲染」, 判据就没吃劲。
    assert "正在读取文件" in rec.everything


# -- 判据 3: 兜底不泄漏工具名 --------------------------------------------------


@pytest.mark.anyio
async def test_unmapped_tool_uses_generic_label_without_its_name():
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(_tool_call("internal_payroll_probe"))

    await client._stream_reply(channel, core, "oc_1", [], reply_to=None, sender_open_id="ou_1")

    assert GENERIC_TOOL_LABEL in rec.everything
    assert "internal_payroll_probe" not in rec.everything
    assert "payroll" not in rec.everything


# -- 判据 4: 正文开始后状态行被抹掉 --------------------------------------------


@pytest.mark.anyio
async def test_body_text_erases_the_status_line():
    """正文一出字, 状态行就得消失, 只留正文。"""
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(
        _tool_call("feishu_doc_read"),
        _tool_result("feishu_doc_read"),
        TextChunk("根据你提供的三份文档,"),
        TextChunk("我整理出以下要点…"),
    )

    await client._stream_reply(channel, core, "oc_1", [], reply_to=None, sender_open_id="ou_1")

    # 中途出现过状态行(不然这条判据测的是「从来没渲染」)。
    assert "正在读飞书文档" in rec.everything
    # 终态只剩正文。
    assert rec.final == "根据你提供的三份文档,我整理出以下要点…"
    assert "⏳" not in rec.final
    assert "正在读飞书文档" not in rec.final


# -- 判据 5: append 与 set_content 混用不吞字、不重复 --------------------------


@pytest.mark.anyio
async def test_mixing_append_and_set_content_neither_swallows_nor_duplicates():
    """``merge_streaming_text`` 会按「prev 的后缀 == chunk 的前缀」去重。

    实测 ``merge('abc','cdef') == 'abcdef'`` —— 它真的会吃字。所以状态行在
    ``_content`` 里时不能再走 ``append``: 否则 prev 是「状态行+正文」, 拿它去和新
    正文找重叠, 吃掉的就是用户的字。首段刻意以状态行的尾字符 ``…`` 开头, 正是
    会被吃掉的那种输入。

    判据写成**差分**而不是「等于拼接结果」: ``merge`` 对相邻正文 chunk 本身就会
    去重 (与本改动无关, 是 SDK 既有行为), 拿绝对值断言会把那份既有行为也算进来,
    于是实现写对了照样红。差分只问一件事: 状态行在场与不在场, 正文一模一样。
    """
    parts = ["…先说结论,", "这三份文档", "都指向同一个结论"]

    async def _render(with_tools: bool) -> str:
        rec = _CardRecorder()
        channel, _ = _recording_channel(rec)
        head = (_tool_call("read"), _tool_result("read")) if with_tools else ()
        core = _core_yielding(*head, *[TextChunk(p) for p in parts])
        await client._stream_reply(channel, core, "oc_1", [], reply_to=None, sender_open_id="ou_1")
        return rec.final

    with_status = await _render(True)
    without_status = await _render(False)

    assert with_status == without_status, f"状态行改写了正文: {with_status!r} != {without_status!r}"
    # 同时钉住基线本身是完整的 —— 否则两边一起坏掉也能相等。
    assert without_status == "".join(parts)


@pytest.mark.anyio
async def test_body_erases_a_status_line_that_is_still_showing():
    """正文到达时状态行**还挂着**的那条路径 —— 工具没回结果就出正文。

    与上一条分开是必须的: 上一条的序列里 ``tool_result`` 已经先把状态行抹掉了,
    于是 ``append_body`` 里那次抹除根本没执行, 断言是靠另一条路过的 —— 实测把
    那次抹除删掉, 上一条照样全绿。这条把 ``tool_result`` 去掉, 让抹除成为唯一
    能让状态行消失的路。

    这个流形不是臆造: 工具在 agent 侧并发执行, 而部分上游会在工具还在跑时就开始
    吐 content; 结果 chunk 也可能因流被切断而永远不到。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(
        _tool_call("search_content"),
        TextChunk("先给你一个初步结论。"),
    )

    await client._stream_reply(channel, core, "oc_1", [], reply_to=None, sender_open_id="ou_1")

    assert "正在检索代码库" in rec.everything, "状态行压根没出现过, 这条判据没吃劲"
    assert rec.final == "先给你一个初步结论。"
    assert "⏳" not in rec.final


@pytest.mark.anyio
async def test_status_line_returning_mid_body_keeps_body_intact():
    """多轮: 正文出了一段后又调工具, 状态行回来时不能动已发出的正文。"""
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(
        TextChunk("先查一下。"),
        _tool_call("bash"),
        _tool_result("bash"),
        TextChunk("查完了,结论是这样。"),
    )

    await client._stream_reply(channel, core, "oc_1", [], reply_to=None, sender_open_id="ou_1")

    assert "正在执行命令" in rec.everything
    assert rec.final == "先查一下。查完了,结论是这样。"


# -- 判据 6/7: NO_REPLY 抑制与静默回合不建卡 -----------------------------------


@pytest.mark.anyio
async def test_silent_turn_sends_nothing_at_all():
    """静默回合(NO_REPLY)结束后, 卡片上不该有任何内容。"""
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(
        _tool_call("todo"),
        _tool_result("todo"),
        TextChunk("NO_REPLY"),
    )

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    assert rec.updates == [], f"静默回合发出了内容: {rec.updates!r}"
    assert "NO_REPLY" not in rec.everything


@pytest.mark.anyio
async def test_silent_turn_creates_no_card():
    """静默回合不许建卡 —— 这是独立于「不发内容」的第二条回归路径。

    ``_ensure_started`` 在首次 append/set_content 时建卡。状态行本身就是「要写字」,
    所以一旦无条件渲染状态行, 用户点个按钮就会跳出一张写着「正在整理待办…」的卡。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(
        _tool_call("todo"),
        _tool_result("todo"),
        TextChunk("NO_REPLY"),
    )

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    assert rec.create_calls == [], "静默回合建了卡片"


@pytest.mark.anyio
async def test_suppressed_turn_with_real_reply_still_delivers_it():
    """抑制开着但回复不是 NO_REPLY 时, 正文照旧送达 —— 抑制机器不能被状态行带坏。"""
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(
        _tool_call("todo"),
        _tool_result("todo"),
        TextChunk("已经帮你勾掉了。"),
    )

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    assert rec.final == "已经帮你勾掉了。"


@pytest.mark.anyio
async def test_tool_result_still_rearms_the_silent_check():
    """``tool_result`` 作为「上一次卡片动作办完了」的时钟信号必须还在。

    抑制机器靠它把 ``checking_silent_reply`` 重新打开; 渲染逻辑征用或打乱这个
    chunk, 第二段 NO_REPLY 就会原样出现在群里。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(
        TextChunk("第一件事办好了。"),
        _tool_call("todo"),
        _tool_result("todo"),
        TextChunk("NO_REPLY"),
    )

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    assert "NO_REPLY" not in rec.everything
    assert rec.final == "第一件事办好了。"


# -- 判据 8: 状态行不做字符级更新 ----------------------------------------------


@pytest.mark.anyio
async def test_status_line_updates_scale_with_tools_not_characters():
    """``set_content`` 会强制立刻发一次 HTTP, 所以它只能在工具边界调。

    断言它的次数与工具调用同阶, 而不是与正文字符数同阶: 4 次工具边界 + 1 次
    「正文起头抹掉状态行」, 与 40 个正文 chunk 无关。
    """
    rec = _CardRecorder()
    channel, set_content_calls = _recording_channel(rec)
    body = [TextChunk(f"第{i}句。") for i in range(40)]
    core = _core_yielding(
        _tool_call("read"),
        _tool_result("read"),
        _tool_call("bash"),
        _tool_result("bash"),
        *body,
    )

    await client._stream_reply(channel, core, "oc_1", [], reply_to=None, sender_open_id="ou_1")

    assert len(set_content_calls) <= 6, f"set_content 被调了 {len(set_content_calls)} 次, 疑似按字符更新"
    assert rec.final == "".join(c.text for c in body)


@pytest.mark.anyio
async def test_concurrent_tool_calls_report_a_count():
    """并发时状态行报个数, 不铺开列名 —— 走到飞书渲染这一层确认。"""
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(
        _tool_call("read"),
        _tool_call("bash"),
        _tool_call("feishu_doc_read"),
    )

    await client._stream_reply(channel, core, "oc_1", [], reply_to=None, sender_open_id="ou_1")

    assert "另有 2 个工具在跑" in rec.everything
