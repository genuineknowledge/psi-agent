"""Live 反馈: 把「思考 + 工具调用」和正文拼成整块渲进卡片的同一个元素。

**为什么是整块重写而不是 SDK 的打字机。** 卡片只有**一个** markdown 元素 ——
``MarkdownStreamController`` 的卡片 spec 把 ``elements`` 硬编码成单个 markdown
(``markdown_stream.py:136-143``), 所以状态行、过程块、正文共用同一个 ``_content``
字符串, 没有第二个元素可以放过程。而 ``append`` 会跑
``merge_streaming_text(prev, chunk)`` 去重, prev 里有过程块时它吃掉的是用户的字。
两者不可能共存, 故 live 模式**放弃 ``append``**, 每 ``RENDER_INTERVAL_SECONDS``
用 ``set_content`` 整块重写一次。代价是打字机变成 0.7 秒一跳 —— 这是已经拍过板
的取舍 (见 ``docs/飞书机器人反馈机制-生产临时改动与正式修复方案.md`` 第三节),
换来的是工具跑几十秒时用户看得见它在动。

**为什么不搬到独立元素 / 折叠面板。** 独立元素要换 ``CardStreamController``
(整卡 JSON + ``patch_card``), 那条路没有流式打字机; 折叠面板同理。都是产品取舍
而不是实现细节, 已否掉, 勿"优化"回去。

**为什么 live 模式报工具真名 + 参数 + 返回, 不走 ``TOOL_ALIASES``。**
别名表要人手维护, 漏一个就退化成 ``GENERIC_TOOL_LABEL`` 那句没有信息量的
「正在调用工具」—— 实测本次最贵的 ``python_run`` (单回合 143 秒) 恰好在表外。
**这与非 live 路径「一个字都不能来自 reasoning 文本」的纪律相反, 是刻意的**:
非 live 那条路 (``_tool_status``) 逐字节未变, 判据也还在锁它。live 是 opt-in 的
``PSI_FEISHU_LIVE_FEEDBACK=1``, 开它就等于接受「过程细节给用户看」。

**参数从结构化字段来, 不从文本抠。** 工具名与参数走 ``ReasoningChunk.tool_name``
/ ``tool_args`` (session 侧 ``AgentChunk`` 同名字段, 经 ``\\x1f`` 编码进
``StreamBuffer`` 的 key —— 见 ``channel/_core.py:_buffer_key``)。曾经这里有一条
``[Tool Call: name(args)]`` 的正则, 它在参数**字面**含 ``)]`` 时提前收尾、显示不全
(实测 ``{"command": "echo )]"}`` 只显示到 ``{"command": "echo``)。那条正则已删,
**不要为了"兜底"把它加回来**: 两条路并存意味着盲区还在, 只是多了一层遮掩。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

ENV_FLAG = "PSI_FEISHU_LIVE_FEEDBACK"
"""总开关。生产由 ``launch-gateway.sh`` 导出; 缺省即完全走原路径。"""

ARG_CAP = 300
"""单个工具参数的显示上限。"""

RESULT_CAP = 300
"""单个工具返回的显示上限。"""

TOTAL_CAP = 25000
"""整块过程的兜底闸: 超了丢最老的事件。

**这个数没有实测依据。** 补丁里唯一的观测是一行 DEBUG, 而生产钉死 INFO, 实测
40 分钟输出 0 条 —— 所以真实峰值至今不知道。本模块把那行提到 INFO (见
``client.py`` 的 ``live render``), 量到之后再校准这个数。
"""

RENDER_INTERVAL_SECONDS = 0.7
"""两次 ``set_content`` 之间的最小间隔。工具边界与门开时强制刷新, 不受它节流。"""

CARD_DELAY_SECONDS = 3.0
"""首个过程事件后要满多少秒才允许建卡 —— 「静默回合不弹卡」全靠这道门。

``_ensure_started`` 在首次 ``append``/``set_content`` 时就建卡并发出去, 卡片建了
撤不回来; 而「这一回合最终会不会回 NO_REPLY」在工具还在跑时**无从得知**。所以
门槛只能是时间: 按钮回调、普通聊天里模型直接回 NO_REPLY 这类静默回合都在毫秒级
走完, 天然来不及建卡; 真跑几十秒的回合才会亮出过程。

**已知取舍**: 跑满 3 秒之后才决定静默的回合仍会留下一张只有过程、没有答案的卡。
这是刻意的 —— 用户已经等了 3 秒以上, 「它做了什么」比「一片空白」有用, 且这与
「静默回合不抹掉过程块」是同一条规则 (抹了点按钮就变空卡)。
"""

_RESULT_RE = re.compile(r"\[Tool Result: (.*?)\]\s*$", re.DOTALL)


def live_feedback_enabled() -> bool:
    """``PSI_FEISHU_LIVE_FEEDBACK=1`` 才开。

    每回合现读而不是 import 时读一次: 生产靠改启动脚本 + 重启切换, 但用例要能
    ``monkeypatch.setenv`` 逐条切, import 时快照会让它们全部读到同一个值。
    """
    return os.environ.get(ENV_FLAG) == "1"


def parse_tool_result(text: str) -> str | None:
    """抠出 ``[Tool Result: ...]`` 的内容; 不是该形状时返回 ``None``。"""
    match = _RESULT_RE.search(text or "")
    return match.group(1) if match else None


def clip(text: str, cap: int) -> str:
    """压平空白并掐到 ``cap`` 字, 超出时如实标明原长。

    只用于参数与返回 —— 思考正文**一个字不掐**, 掐了就等于把用户正在读的东西
    从中间截断。
    """
    flat = " ".join((text or "").split())
    if len(flat) <= cap:
        return flat
    return flat[:cap] + f"…(共 {len(flat)} 字)"


@dataclass
class _Think:
    """一段连续思考。"""

    text: str


@dataclass
class _Tool:
    """一次工具调用; ``result is None`` 即还在跑。"""

    name: str
    args: str
    result: str | None = None


class ProcessTimeline:
    """按发生顺序攒「思考 / 工具」事件, 渲成一整块 markdown 引用。

    交织成一条时间线而不是分成「思考区」+「工具区」两段: 模型的实际节奏是
    想一下、调一个、再想一下, 拆开看不出因果。
    """

    def __init__(self, *, total_cap: int = TOTAL_CAP) -> None:
        self._events: list[_Think | _Tool] = []
        self._total_cap = total_cap

    def __len__(self) -> int:
        return len(self._events)

    def add_thinking(self, text: str) -> None:
        """追加思考。连续思考并进同一个事件, 于是它在时间线上是「一段」。

        不并的话每个 token 一条事件, ``TOTAL_CAP`` 的裁剪会按 token 丢, 一段话
        被从中间挖掉。
        """
        if not text:
            return
        last = self._events[-1] if self._events else None
        if isinstance(last, _Think):
            last.text += text
        else:
            self._events.append(_Think(text))

    def add_tool_call(self, name: str, args: str = "") -> None:
        self._events.append(_Tool(name=name or "?", args=clip(args, ARG_CAP)))

    def add_tool_result(self, name: str, result: str) -> None:
        """就近配对: 同名**最后一个**还没有返回的调用。

        流上没有配对 id, 所以只能按名字 + 「还没回」两个条件找。配不上时补一条
        独立事件, 而不是丢掉 —— 丢掉会让用户看到一个永远转着的工具。
        """
        clipped = clip(result, RESULT_CAP)
        for event in reversed(self._events):
            if isinstance(event, _Tool) and event.name == (name or "?") and event.result is None:
                event.result = clipped
                return
        self._events.append(_Tool(name=name or "?", args="", result=clipped))

    def render(self) -> str:
        """渲成过程块; 没有事件时返回空串。

        超过 ``total_cap`` 时逐个丢最老的事件 —— 迭代而不是递归: 一个长回合的
        事件数没有上界, 递归写法在事件足够多时会撞 recursion limit, 而它恰好只在
        最长的那种回合里发生 (也就是最需要它工作的时候)。

        只剩一个事件仍超限时**如实返回超限的那一块**, 不再往下丢: 丢光就是整块
        消失, 用户看到的是过程凭空不见, 比一块过长的引用更难理解。
        """
        while True:
            block = self._render_once()
            if len(block) <= self._total_cap or len(self._events) <= 1:
                return block
            self._events.pop(0)

    def _render_once(self) -> str:
        parts: list[str] = []
        for event in self._events:
            if isinstance(event, _Think):
                text = event.text.strip()
                if text:
                    parts.append("\n".join(f"> {ln}" if ln.strip() else ">" for ln in text.split("\n")))
            else:
                head = f"> 🔧 **{event.name}**"
                if event.args:
                    head += f"  `{event.args}`"
                parts.append(f"{head}  …" if event.result is None else f"{head}\n>\n> ✅ {event.result}")
        return "\n>\n".join(parts)
