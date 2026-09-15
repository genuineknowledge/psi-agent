"""守 feishu-web 流式渲染的四条性能约束不被"优化"掉。

为什么值得判据: 助手回复是**逐 delta** 落到 ``msg.text`` 的(``hooks/useChatTurn.ts`` 的
``onText``), 而 ``ChatThread`` 不做窗口化、``ChatMessageItem`` 也没 memo(props 里全是内联
回调, 加了恒失效)。所以「每个增量重渲染整棵树」是既定事实 —— 渲染侧唯一的防线就是下面四处,
删掉任意一处都不会报错、不会红测试, 只会让**长会话 + 长回复**时主线程被解析占满, 表现为
操作卡顿。实测背景(2026-09-15): 在飞书开发者后台的「网页远程调试工具」下操作网页应用时鼠标
严重卡顿 —— 远程调试桥要在主线程上做元素拾取与 DOM 快照, 主线程被占满时它的鼠标处理被饿死。

四条约束逐条对应下面四条用例, 判据是**源码形状**(本目录既有做法, 见
``test_feishu_web_dev_proxy.py`` / ``test_feishu_web_dev_strict_port.py`` —— feishu-web
没有 vitest, 前端断言只能静态核对):

1. ``MarkdownBubble`` 必须是 ``memo(...)`` 包起来的, 且解析结果走 ``useMemo``。
2. 正在生长的那条气泡必须用 ``useDeferredValue`` 削峰。
3. ``renderMd.ts`` 的 ``highlightAuto`` 必须带语言子集, 不能裸调用。
4. ``chat-thread.tsx`` 的贴底滚动必须经 ``requestAnimationFrame`` 合并到帧, 且要取消。

详细理由见 ``src/psi_agent/gateway/feishu/feishu-web/AGENTS.md`` 的
「流式渲染: 四处刻意为之的性能约束」。
"""

from __future__ import annotations

import re
from pathlib import Path

FEISHU_WEB_SRC = Path(__file__).resolve().parents[3] / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web" / "src"
MARKDOWN_TSX = FEISHU_WEB_SRC / "components" / "markdown.tsx"
CHAT_THREAD_TSX = FEISHU_WEB_SRC / "components" / "chat-thread.tsx"
RENDER_MD_TS = FEISHU_WEB_SRC / "services" / "renderMd.ts"


def test_judged_files_exist() -> None:
    """判据自身的存在性: 文件被挪走时, 下面几条会「因为没找到所以通过」。"""
    for path in (MARKDOWN_TSX, CHAT_THREAD_TSX, RENDER_MD_TS):
        assert path.is_file(), f"找不到 {path} —— 本文件的判据已失效, 请同步路径"


def test_markdown_bubble_is_memoized() -> None:
    """``MarkdownBubble`` 必须 memo 化, 且解析结果要缓起来。

    没有 memo 时, 每个流式增量都会把会话里**每一条**助手消息重新解析一遍
    (marked + highlight.js + KaTeX), 开销随历史条数与平均篇幅相乘增长。
    """
    text = MARKDOWN_TSX.read_text(encoding="utf-8")
    assert re.search(r"export\s+const\s+MarkdownBubble\s*=\s*memo\(", text), (
        "``MarkdownBubble`` 不再是 ``memo(...)`` 包起来的组件。改回普通函数组件后, 流式期间"
        "ChatThread 整棵树重渲染会把**历史每一条**助手消息的 Markdown 重新解析一遍, 长会话下"
        "直接打满主线程(表现: 页面操作卡顿)。见 feishu-web/AGENTS.md「流式渲染」一节。"
    )
    assert "useMemo(" in text, (
        "``markdown.tsx`` 里找不到 ``useMemo`` —— 解析结果必须按 text 缓存, 否则每次重渲染"
        "(哪怕 text 没变)都会重新解析整篇 Markdown。"
    )
    assert "useDeferredValue(" in text, (
        "``markdown.tsx`` 里找不到 ``useDeferredValue``。正在生长的那条气泡 memo 挡不住"
        "(text 每个 delta 都变), 而整篇解析是同步的纯主线程开销 —— 长回复下「每 delta 全量"
        "重解析」是 O(n²), 必须靠 useDeferredValue 让 React 跳过中间值。"
    )


def test_highlight_auto_uses_language_subset() -> None:
    """``hljs.highlightAuto`` 必须带语言子集。

    不传第二个参数时它会遍历 ``highlight.js/lib/common`` 里全部 30+ 种语法、各扫一遍全文,
    这是整个解析里最贵的一步, 而流式期间每个 delta 都要跑一遍。
    """
    text = RENDER_MD_TS.read_text(encoding="utf-8")
    call = re.search(r"hljs\.highlightAuto\(([^)]*)\)", text)
    assert call is not None, "``renderMd.ts`` 里找不到 ``hljs.highlightAuto(...)`` —— 判据失效"
    args = [a.strip() for a in call.group(1).split(",") if a.strip()]
    assert len(args) >= 2, (
        "``hljs.highlightAuto`` 是裸调用(没有语言子集)。它会遍历 lib/common 的全部语法各扫"
        "一遍全文 —— 流式期间每个 delta 都要付这个代价。请传 ``AUTO_LANGS`` 子集; 需要新语言"
        "就往那个常量里加(只影响着色, 不影响内容渲染)。"
    )


def test_scroll_is_coalesced_into_a_frame() -> None:
    """贴底滚动必须合并到帧, 且要取消 —— 否则每个 delta 强制一次布局。"""
    text = CHAT_THREAD_TSX.read_text(encoding="utf-8")
    assert "requestAnimationFrame(" in text, (
        "``chat-thread.tsx`` 的贴底滚动不再经 ``requestAnimationFrame``。那个 effect 的依赖里"
        "有 ``messages.at(-1)?.text``, 触发频率就是流式 delta 的频率, 而 ``scrollIntoView``"
        "每次都强制一次布局 —— 增量密时会把布局压满。"
    )
    assert "cancelAnimationFrame(" in text, (
        "``requestAnimationFrame`` 没有配对的 ``cancelAnimationFrame``: cleanup 里不取消上一帧"
        "的预约, 就等于没有合并效果(而且组件卸载后仍可能滚一次)。"
    )
