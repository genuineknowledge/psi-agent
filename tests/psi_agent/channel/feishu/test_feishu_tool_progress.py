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

import anyio
import pytest
from lark_channel.channel.outbound.streaming.markdown_stream import MarkdownStreamController

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import ReasoningChunk, TextChunk
from psi_agent.channel.feishu import _live_feedback, client
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
    """构造与生产同形的 tool_call chunk。

    文本里带完整参数 (正如流上那样) **且**同样的参数也放进 ``tool_args`` —— 生产的
    ``ChannelCore._to_chunk`` 两处都填 (见 ``channel/_core.py``)。只填一处会让判据测
    的是一个流上不存在的形状。
    """
    return ReasoningChunk(
        text=f"[Tool Call: {name}({args_text})]",
        kind="tool_call",
        tool_name=name,
        tool_args=args_text,
    )


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


# -- live 模式 (PSI_FEISHU_LIVE_FEEDBACK=1) -------------------------------------
#
# 判据都落在 ``_stream_reply`` + 真控制器上, 与上面那批同层 —— live 改的就是这一层
# 的渲染出口, 拿 ProcessTimeline 单独测只能证明「拼字符串对了」, 证不了「卡片何时
# 建、建了几张」, 而那恰恰是本次唯一的回归风险。


def _core_with_delays(*items: Any) -> ChannelCore:
    """``(chunk, 之后睡多久)`` 序列 —— 让门计时器有真实时间可等。

    没有停顿的话整个回合在毫秒内走完, 计时器永远来不及开门, 「慢回合亮过程」这
    条判据就只能靠改常量假装, 测不到真实时序。
    """

    async def _post(_chunks: list[Any]) -> Any:
        for chunk, pause in items:
            yield chunk
            if pause:
                await anyio.sleep(pause)

    return cast(ChannelCore, SimpleNamespace(post=_post))


@pytest.fixture
def live_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_live_feedback.ENV_FLAG, "1")


@pytest.mark.anyio
async def test_live_fast_silent_turn_still_creates_no_card(live_on: None):
    """live 开着, 但静默回合照旧一张卡都不建 —— 补丁破掉的正是这条。

    补丁在 tool_call 时就建卡, 于是普通聊天里模型最终回 NO_REPLY 时会冒出一张
    只有过程、没有答案的卡。这里的门槛是时间: 静默回合毫秒级走完, 计时器还没到
    点就被撤了。
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

    assert rec.create_calls == [], "live 静默回合建了卡片"
    assert rec.updates == [], f"live 静默回合发出了内容: {rec.updates!r}"
    assert "NO_REPLY" not in rec.everything


@pytest.mark.anyio
async def test_live_gate_is_what_suppresses_the_card_not_something_else(live_on: None, monkeypatch: pytest.MonkeyPatch):
    """同一条静默序列, 只把门槛调短就该建卡 —— 证明上一条不是「live 压根没生效」。

    缺这条差分, 上一条判据无法区分「门挡住了」和「live 分支根本没走到」: 后者同样
    是 0 张卡、全绿, 而它意味着整个 live 机制是死代码。
    """
    monkeypatch.setattr(_live_feedback, "CARD_DELAY_SECONDS", 0.01)
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_with_delays(
        (_tool_call("todo"), 0.15),
        (_tool_result("todo"), 0),
        (TextChunk("NO_REPLY"), 0),
    )

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    assert len(rec.create_calls) == 1, "门槛调短后仍没建卡, 说明 live 分支没生效"
    # 已知取舍: 跑过门槛的静默回合会留下一张只有过程的卡 (见 CARD_DELAY_SECONDS
    # 的 docstring)。但**正文半段仍然一个字都不能漏** —— 这才是不可让的那条。
    assert "NO_REPLY" not in rec.everything, "live 模式把未过嗅探的 NO_REPLY 发上了卡片"
    assert "todo" in rec.everything


@pytest.mark.anyio
async def test_live_slow_turn_shows_thinking_and_real_tool_name_with_args(
    live_on: None, monkeypatch: pytest.MonkeyPatch
):
    """慢回合的过程块: 思考原文 + 工具**真名** + 参数 + 返回, 都要真上卡片。

    这条锁的是 live 相对老路径的全部增量。特意用表外工具 ``python_run``——老路径
    对它只会显示 ``GENERIC_TOOL_LABEL`` 那句没有信息量的兜底 (实测单回合 143 秒),
    所以「真名出现」同时也证明它没有退回别名表。
    """
    monkeypatch.setattr(_live_feedback, "CARD_DELAY_SECONDS", 0.01)
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_with_delays(
        (ReasoningChunk(text="先跑一段代码验证。", kind=None, tool_name=None), 0.05),
        (_tool_call("python_run", '{"code": "print(6*7)"}'), 0.05),
        (_tool_result("python_run", "42"), 0),
    )

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    shown = rec.everything
    assert "先跑一段代码验证。" in shown, "思考没上卡片"
    assert "python_run" in shown, "工具真名没上卡片 (疑似退回了别名表)"
    assert "print(6*7)" in shown, "参数没上卡片"
    assert "42" in shown, "返回没上卡片"
    assert GENERIC_TOOL_LABEL not in shown, "live 路径不该再走兜底文案"


@pytest.mark.anyio
async def test_live_final_render_drops_the_process_block_and_keeps_only_the_answer(
    live_on: None, monkeypatch: pytest.MonkeyPatch
):
    """正文流完后过程脚手架撤掉, 卡片只剩答案 —— 过程中途必须真出现过。"""
    monkeypatch.setattr(_live_feedback, "CARD_DELAY_SECONDS", 0.01)
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_with_delays(
        (ReasoningChunk(text="查一下再回答。", kind=None, tool_name=None), 0.05),
        (_tool_call("search_content", '{"q": "rsi"}'), 0.05),
        (_tool_result("search_content", "找到 10 条"), 0),
        (TextChunk("结论是这样。"), 0),
    )

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    assert "查一下再回答。" in rec.everything, "过程压根没出现过, 这条判据没吃劲"
    assert rec.final == "结论是这样。", f"终态不是纯正文: {rec.final!r}"
    assert "🔧" not in rec.final


# -- 判据: 参数字面含 ")]" 也完整显示 -------------------------------------------


@pytest.mark.anyio
async def test_live_shows_full_args_when_they_contain_the_closing_bracket_pair(
    live_on: None, monkeypatch: pytest.MonkeyPatch
):
    """参数里**字面**含 ``)]`` 时仍完整显示 —— 结构化字段替掉正则的全部理由。

    旧实现从 ``chunk.text`` 里正则抠 ``[Tool Call: name(args)]``, 非贪婪匹配在参数
    内部第一个 ``)]`` 上就收尾: 实测这条参数只显示到 ``{"command": "echo``, 后面
    连引号都没闭合。现在名字与参数走 ``tool_name`` / ``tool_args`` 两个字段, 文本
    长什么样都不再参与解析。

    ``echo )]`` 是最小复现: shell 里反引号/括号是家常, 而 ``)]`` 恰好是那条正则的
    收尾符。
    """
    monkeypatch.setattr(_live_feedback, "CARD_DELAY_SECONDS", 0.01)
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    args_text = '{"command": "echo )]"}'
    core = _core_with_delays(
        (_tool_call("bash", args_text), 0.05),
        (_tool_result("bash", ")]"), 0),
    )

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    shown = rec.everything
    assert "bash" in shown, "工具名没上卡片, 这条判据没吃劲"
    # 整条参数逐字符都在 —— 断在 "echo" 后面正是旧正则的截断点, 所以这里比的是
    # **完整串**而不是某个子串在不在。
    assert args_text in shown, f"参数被截断了 (旧正则的盲区): {shown!r}"
    assert '{"command": "echo\n' not in shown, "参数在 echo 后被截断"
