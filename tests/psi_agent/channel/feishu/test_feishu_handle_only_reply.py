"""模型整条正文抄成省略句柄时, 用户不能什么都收不到。

**生产实测 (2026-09-18, 会话 ``feishu-ou_6c30c11b…``)**: 第 2990 行 assistant
整条正文就是 ``[已省略 2251 字符, 句柄 assistant#508736]`` —— 那一轮 LLM 推理
1224 字符、可见正文 0 字符。用户发了份简历, 等 5 分钟, 什么都没收到。同形状
9-12 也发生过一次 (第 2717 行)。

``VisibleMarkerFilter`` 把句柄剥掉这件事**是对的、不改**; 缺的是剥完为空之后没
人处理 —— 只记一条 DEBUG 就 return, ``body`` 保持空, 收尾 ``final=bool(body)``
于是一个字都不写。

**「剥完为空」与「真 NO_REPLY」语义相反, 判据必须能分开**:

- 真 NO_REPLY: 模型明确判断本轮不用回, 静默是正确行为 → 仍然静默、仍然不建卡。
- 句柄抄写: 模型**以为自己说了话**, 只是退化成占位符 → 必须兜底 + 记 ERROR。

判据全部走真的 ``_stream_reply`` + 真的 ``MarkdownStreamController`` (理由同
``test_feishu_tool_progress``: 换 AsyncMock 会让懒建卡时机与 ``merge_streaming_text``
的相互作用整个消失, 而缺陷恰在那一层)。**live 与非 live 两条路各有判据** ——
live 默认已经是开, 生产就是 live 开着的环境, 只测非 live 等于没测到现场。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest
from loguru import logger

from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import TextChunk
from psi_agent.channel.feishu import _live_feedback, client

from .test_feishu_tool_progress import _CardRecorder, _recording_channel, _tool_call, _tool_result

#: 生产第 2990 行那条, 逐字节照抄 (句柄格式见 ``history_display._ELISION_HANDLE_RE``)。
PROD_HANDLE = "[已省略 2251 字符, 句柄 assistant#508736]"


def _core_yielding(*chunks: Any) -> ChannelCore:
    async def _post(_chunks: list[Any]) -> Any:
        for c in chunks:
            yield c

    return cast(ChannelCore, SimpleNamespace(post=_post))


class _LogCapture:
    """自挂 loguru sink —— ``caplog`` 抓不到 loguru, 阴性用例会因此假绿。

    同时记 level 与消息: 「有没有记日志」和「记的是不是 ERROR」是两件事, 只比
    消息文本的话把 ERROR 降成 DEBUG 也照样全绿。
    """

    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []
        self._id: int | None = None

    def __enter__(self) -> _LogCapture:
        self._id = logger.add(
            lambda m: self.records.append((m.record["level"].name, m.record["message"])),
            level="DEBUG",
        )
        return self

    def __exit__(self, *_: Any) -> None:
        if self._id is not None:
            logger.remove(self._id)

    def at(self, level: str) -> list[str]:
        return [msg for lvl, msg in self.records if lvl == level]

    @property
    def errors(self) -> list[str]:
        return self.at("ERROR")


@pytest.fixture
def live_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_live_feedback.ENV_FLAG, "0")


@pytest.fixture
def live_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_live_feedback.ENV_FLAG, "1")


# -- 正向: 整条正文是句柄 -------------------------------------------------------


@pytest.mark.anyio
async def test_handle_only_reply_still_sends_something_to_the_user(live_off: None):
    """整条正文就是句柄 → 用户必须收到东西, 不能是空卡/无消息。

    这就是生产那一轮的形状。判据落在「飞书收到的内容」上而不是某个内部变量 ——
    用户体感就是这一层。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(_tool_call("read_file"), _tool_result("read_file"), TextChunk(PROD_HANDLE))

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    assert rec.final.strip(), "整条正文是句柄时用户什么都没收到 —— 正是生产那个缺陷"


@pytest.mark.anyio
async def test_handle_only_reply_never_leaks_the_handle_itself(live_off: None):
    """兜底话术里不许出现句柄 —— 「句柄」是内部概念, 用户看不懂。"""
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(TextChunk(PROD_HANDLE))

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    assert "已省略" not in rec.everything, "句柄原文漏到了卡片上"
    assert "assistant#508736" not in rec.everything
    assert "句柄" not in rec.everything, "把内部概念暴露给了用户"


@pytest.mark.anyio
async def test_handle_only_reply_logs_one_error_with_triage_material(live_off: None):
    """至少 1 条 ERROR, 且带得走查的料 —— 这条链现在完全没有日志。

    要有剥掉的字符数和句柄原文: 少了它们下次复发照样零线索 (9-10/9-12/9-18 三次
    复发, 每次都只能靠翻 5MB 的 JSONL 手工比对 ``len=``)。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(TextChunk(PROD_HANDLE))

    with _LogCapture() as log:
        await client._stream_reply(
            channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
        )

    assert log.errors, f"剥完为空没有记 ERROR; 全部日志: {log.records!r}"
    joined = "\n".join(log.errors)
    assert str(len(PROD_HANDLE)) in joined, f"ERROR 里没有剥掉的字符数: {joined!r}"
    assert "assistant#508736" in joined, f"ERROR 里没有句柄原文: {joined!r}"


# -- 阴性: 真 NO_REPLY 必须照旧静默 ---------------------------------------------


@pytest.mark.anyio
async def test_real_no_reply_stays_silent_and_builds_no_card(live_off: None):
    """真 NO_REPLY 仍然静默、仍然不建卡 —— 既有行为, 弄红即回归。

    与上面那批共用同一条兜底通道, 所以必须同文件立判据: 兜底做宽一格就会把
    「点个按钮」变成「跳出一张卡」。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(_tool_call("todo"), _tool_result("todo"), TextChunk("NO_REPLY"))

    await client._stream_reply(
        channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
    )

    assert rec.updates == [], f"真 NO_REPLY 发出了内容: {rec.updates!r}"
    assert rec.create_calls == [], "真 NO_REPLY 建了卡片"


@pytest.mark.anyio
async def test_real_no_reply_logs_no_error(live_off: None):
    """真 NO_REPLY 不该记 ERROR —— 它是正常行为, 不是故障。

    没这条的话「凡是可见正文为空就报 ERROR」也能让上面全绿, 而那会让每个静默
    回合都喊一次狼来了, ERROR 从此没人看。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(_tool_call("todo"), _tool_result("todo"), TextChunk("NO_REPLY"))

    with _LogCapture() as log:
        await client._stream_reply(
            channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
        )

    assert not log.errors, f"真 NO_REPLY 记了 ERROR: {log.errors!r}"


# -- 边界: 句柄夹在长正文里 -----------------------------------------------------


@pytest.mark.anyio
async def test_handle_inside_a_long_body_is_stripped_without_any_fallback(live_off: None):
    """句柄夹在正文中间 → 剥掉句柄、正文照发、**不触发兜底**。

    生产那份文件 6 处句柄里有 4 处是这种形状 (2818/3007/3020/3021), 用户能正常
    收到正文。兜底判宽一格, 每条正常回复都会被缀上一句道歉。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    # **句柄必须单独成 chunk**, 不能和正文拼在一个 chunk 里: 拼在一起时它在
    # ``marker_filter.feed`` 内部就被剥掉, ``withheld`` 一个字都不会填, 于是
    # 「兜底判宽了」这件事测不出来 —— 实测把守卫从 ``withheld and not body``
    # 放宽成 ``withheld`` 时, 单 chunk 版本照旧全绿。生产那 4 处 (2818/3020…)
    # 也是分块到达的。
    core = _core_yielding(TextChunk("先说结论。"), TextChunk(PROD_HANDLE), TextChunk("后面还有正文。"))

    with _LogCapture() as log:
        await client._stream_reply(
            channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
        )

    assert "先说结论。" in rec.final
    assert "后面还有正文。" in rec.final
    assert "已省略" not in rec.everything, "句柄没被剥掉"
    assert not log.errors, f"正常回复被误判成句柄抄写: {log.errors!r}"
    assert client._HANDLE_ONLY_FALLBACK not in rec.everything, "正常回复被缀上了兜底话术"


# -- 跨 chunk 分裂 --------------------------------------------------------------


@pytest.mark.anyio
async def test_handle_split_across_chunks_is_still_recognised_as_handle_only(live_off: None):
    """句柄切成两个 chunk 到达 → 仍算「整条是句柄」。

    走 ``VisibleMarkerFilter`` 自己的 carry 机制 (前半段没有 ``]``, 被 hold 住),
    不在这里另写一遍匹配 —— 两份匹配逻辑必然漂移。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    head, tail = PROD_HANDLE[:12], PROD_HANDLE[12:]
    core = _core_yielding(TextChunk(head), TextChunk(tail))

    with _LogCapture() as log:
        await client._stream_reply(
            channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
        )

    assert rec.final.strip(), "跨 chunk 的句柄没走到兜底, 用户什么都没收到"
    assert "已省略" not in rec.everything
    assert log.errors, "跨 chunk 的句柄抄写没记 ERROR"


# -- live 那条路 (生产现在就是 live 开着) ---------------------------------------


@pytest.mark.anyio
async def test_live_handle_only_reply_still_sends_something(live_on: None):
    """live 开着时同样要兜底 —— live 有独立分支, 非 live 全绿证不了这条。

    ``append_body`` 的 live 半段走 ``set_content`` 整块重写而不是 ``append``,
    收尾也另走 ``_render_live(final=…)``; 两条路各有一次「visible 空掉就 return」。
    """
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(_tool_call("read_file"), _tool_result("read_file"), TextChunk(PROD_HANDLE))

    with _LogCapture() as log:
        await client._stream_reply(
            channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
        )

    assert rec.final.strip(), "live 路径下整条句柄仍然什么都没发"
    assert "已省略" not in rec.everything
    assert log.errors, "live 路径下没记 ERROR"


@pytest.mark.anyio
async def test_live_real_no_reply_still_silent(live_on: None):
    """live 下真 NO_REPLY 照旧不建卡 —— live 的门是靠 cancel 计时器实现的, 兜底
    写在收尾处很容易绕过那道门。"""
    rec = _CardRecorder()
    channel, _ = _recording_channel(rec)
    core = _core_yielding(_tool_call("todo"), _tool_result("todo"), TextChunk("NO_REPLY"))

    with _LogCapture() as log:
        await client._stream_reply(
            channel, core, "oc_1", [], reply_to=None, suppress_silent_reply=True, sender_open_id="ou_1"
        )

    assert rec.updates == [], f"live 真 NO_REPLY 发出了内容: {rec.updates!r}"
    assert rec.create_calls == [], "live 真 NO_REPLY 建了卡片"
    assert not log.errors, f"live 真 NO_REPLY 记了 ERROR: {log.errors!r}"
