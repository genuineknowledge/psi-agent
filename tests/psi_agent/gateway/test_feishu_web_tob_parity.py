"""守 ToB 前端补齐的「输入与任务体感」五项 (2026-09-15 与 ToC spa-v2 对齐时加)。

这五项都是从 ``desktop/spa-v2`` 移植来的**功能**, 不是把 ToC 的组件树搬过来 —— 移植时有两处
刻意的适配(见下面第 3、5 条), 那两处最容易被后来人"顺手改回去", 所以各有一条判据。

1. **拖拽 / 粘贴文件进输入框** (``clipboardFiles`` + ``composerFileDrop``)。
2. **排队发送**: 回合进行中按 Enter 攒一条, 本回合结束后自动发出 (``queuedSend``)。
3. **任务置顶** (``pinnedTasks``): 纯前端 localStorage。**刻意不走 ToC 的 ``appdataScope``**
   —— 那个指纹来自 ``GET /defaults`` 下发的 appdata, 而 ToB 的 ``/feishu/defaults`` 只回
   ``{ai_id}``。所以这里有一条判据: 这个模块**不许**碰任何后端接口。
4. **思考耗时** (``messageTiming``): 数据源两处 —— 历史的 ``thinking_ms`` 与刚跑完那一回合的
   前端计时。
5. **顶部状态区提示** (``task-status-tip``): 锚在 ``.cend2-quick`` 的状态按钮上(ToC 找的是
   ``.quick-actions``), 且**"已看过"落 localStorage** —— ToC 用内存标记(每次刷新都再弹一遍),
   那对天天用的业务页面是噪音。**ToB 的状态按钮只有一个**(圆点): C 端是两个(思考状态 /
   执行状态), 而这两个在 ToB 都由同一个 ``sending`` 驱动, 同时亮同时灭, 摆一起只会让人以为
   其中一个坏了(实测反馈「有点重复了」)。

**另有一条政策判据**: 与 ToC 对齐**不包括**模型配置页 / 用户中心 / workspace 选择器 ——
``feishu-web/AGENTS.md`` 的三条产品决定明令不许搬("网页应用没有「模型」这个概念…别把那套搬
过来"、"前端不传 workspace")。这条判据把"不许长出来"变成可执行的, 而不是靠人记得。

为什么是静态核对: feishu-web 没有 vitest(理由见 ``test_feishu_web_dev_strict_port.py``)。
"""

from __future__ import annotations

import re
from pathlib import Path

FEISHU_WEB = Path(__file__).resolve().parents[3] / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web"
SRC = FEISHU_WEB / "src"
SERVICES = SRC / "services"
COMPONENTS = SRC / "components"


def _code(path: Path) -> str:
    """读源码并去掉注释 —— 判据只该看代码。

    (同 ``test_feishu_web_im_session_ui.py``: 解释性注释会被"不许再出现"的断言命中而假红。)
    """
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"(?m)^[ \t]*//.*$", "", text)


def test_ported_files_exist() -> None:
    """判据自身的存在性: 文件被删/改名时下面几条会「因为没找到所以通过」。"""
    for name in ("clipboardFiles.ts", "composerFileDrop.ts", "queuedSend.ts", "pinnedTasks.ts", "messageTiming.ts"):
        assert (SERVICES / name).is_file(), f"找不到 services/{name}"
    assert (COMPONENTS / "task-status-tip.tsx").is_file(), "找不到 components/task-status-tip.tsx"


def test_composer_accepts_drag_and_paste() -> None:
    """拖拽与粘贴必须都接到 ``onAddFiles`` 上, 且拖拽覆盖层不能吃掉 drop 事件。"""
    view = _code(COMPONENTS / "chat-view.tsx")
    css = _code(SRC / "styles.css")
    assert "useComposerFileDrop(" in view, "``chat-view.tsx`` 不再接拖拽落点 —— 拖文件进输入框这条功能没了。"
    assert "filesFromClipboard(" in view, "``chat-view.tsx`` 不再从剪贴板取文件 —— 截图/文件粘贴这条功能没了。"
    assert ".focus-chat-dropzone" in css and "pointer-events: none" in css, (
        "拖拽覆盖层的样式不见了, 或者丢了 ``pointer-events: none``。少了它覆盖层会挡住 drop 事件, "
        "表现是「松开鼠标什么也没发生」。"
    )


def test_queued_send_flushes_on_turn_end() -> None:
    """排队消息必须**在回合结束时**发出去, 而不是被丢掉或立刻发。"""
    app = _code(SRC / "App.tsx")
    view = _code(COMPONENTS / "chat-view.tsx")
    assert "buildQueuedSend(" in app, "``App.tsx`` 里没有 ``buildQueuedSend`` —— 排队这条功能没了。"
    assert "wasSendingRef" in app, (
        "``App.tsx`` 里没有记 ``sending`` 的上一次值的 ref。排队消息靠 ``turn.sending`` 的"
        "**下降沿**发出; 直接看当前值会在回合进行中被反复触发, 或者在回合结束时一次都不触发。"
    )
    assert "queuedSendsRef" in app, (
        "``App.tsx`` 里没有 ``queuedSendsRef`` —— 回合结束的那个 effect 只该在下降沿读一次排队"
        "内容, 用 state 会让 effect 因为排队状态自身变化而重跑。"
    )
    assert "focus-chat-queued" in view, "``chat-view.tsx`` 里没有排队芯片 —— 用户看不到自己排了什么。"


def test_pinned_tasks_stay_client_side() -> None:
    """置顶是纯前端偏好: 不许碰后端, 也不许走 ToC 的 appdataScope。"""
    pinned = _code(SERVICES / "pinnedTasks.ts")
    tasks_hook = _code(SRC / "hooks" / "useTasks.ts")
    view = _code(COMPONENTS / "tasks-view.tsx")

    assert 'from "../api"' not in pinned and "appdataScope" not in pinned, (
        "``pinnedTasks.ts`` 碰了后端或 ToC 的 ``appdataScope``。ToB 的 ``/feishu/defaults`` 只回 "
        "``{ai_id}``, 拿不到 appdata 指纹; 置顶本来就只是本机偏好, 一旦走后端就变成「要同步的状态」。"
    )
    assert "sortTasksByPin(" in tasks_hook, "``useTasks`` 没有按置顶排序 —— 置顶了也不会浮到最前。"
    assert "ht-row-pin" in view, "``tasks-view.tsx`` 里没有置顶按钮。"


def test_thinking_duration_has_both_sources() -> None:
    """耗时要能从历史与刚跑完那一回合两个来源拿到。"""
    model = _code(SERVICES / "messageTiming.ts")
    history_map = _code(SERVICES / "historyMap.ts")
    turn_hook = _code(SRC / "hooks" / "useChatTurn.ts")
    item = _code(COMPONENTS / "chat-message-item.tsx")

    assert "formatThinkingDuration(" in model, "``messageTiming.ts`` 里没有时长格式化。"
    assert "thinking_ms" in history_map, (
        "``historyMap.ts`` 没有把后端的 ``thinking_ms`` 映射进来 —— 刷新后所有历史消息的耗时都会消失。"
    )
    assert "turnT0" in turn_hook, (
        "``useChatTurn.ts`` 没有量本回合耗时。SSE 不下发 ``thinking_ms``, 不自己量的话刚跑完的那条"
        "要等下次拉历史才有耗时, 看起来像坏了。"
    )
    assert "thinkingHeaderWithDuration(" in item, "``chat-message-item.tsx`` 没有把耗时显示出来。"


def test_status_tip_anchor_and_persistence() -> None:
    """提示条的锚点必须与顶栏一致, 且"已看过"要落盘。"""
    tip = _code(COMPONENTS / "task-status-tip.tsx")
    topbar = _code(COMPONENTS / "chat-topbar.tsx")
    app = _code(SRC / "App.tsx")

    assert "agent-status-tooltip-wrap" in topbar, (
        "``chat-topbar.tsx`` 的状态按钮丢了 ``agent-status-tooltip-wrap`` 类 —— 提示条靠它取"
        "包围盒, 丢了就永远锚不到(表现为提示从不出现)。"
    )
    assert topbar.count("agent-status-tooltip-wrap") == 1, (
        "顶栏的状态按钮不再是 1 个。C 端是两个(思考状态 / 执行状态), 而 ToB 这两个都由同一个 "
        "``sending`` 驱动 —— 同时亮同时灭, 摆两个只会让人以为其中一个坏了(实测反馈「有点重复了」)。"
        "要再加回来, 前提是先有**两个不同的信号**, 而不是拿同一个布尔量喂两个图标。"
    )
    assert "Clock" not in topbar, "``chat-topbar.tsx`` 又把那只时钟加回来了 —— 它与圆点表示同一件事(见上一条)。"
    assert ".cend2-quick .agent-status-tooltip-wrap" in tip, (
        "``task-status-tip.tsx`` 的查询选择器与顶栏的类名不再一致 —— 提示会锚不到位置。"
    )
    assert "localStorage" in app and "STATUS_TIP_SEEN_KEY" in app, (
        "``App.tsx`` 里没有把「提示已看过」落 localStorage。ToC 用的是内存标记(每次刷新再弹一遍), "
        "ToB 是天天用的业务页面, 那样会变成噪音。"
    )


def test_toc_only_pages_stay_out() -> None:
    """与 ToC 对齐**不包括**模型配置 / 用户中心 / workspace 选择器 —— 产品决定明令不许搬。"""
    forbidden = [
        "UserHub.tsx",
        "HubModelsPanel.tsx",
        "HubLoginPanel.tsx",
        "HubSettingsPanel.tsx",
        "HubAdvancedPanel.tsx",
        "HubAdvancedSettingsPanel.tsx",
        "HubOtpInput.tsx",
        "PathPickerDialog.tsx",
        "WorkspaceGate.tsx",
        "FirstRunGuide.tsx",
    ]
    for name in forbidden:
        assert not (COMPONENTS / name).exists(), (
            f"``feishu-web/src/components/{name}`` 出现了。ToB 是另一种产品: AI 由部署者用 "
            "``--feishu-ai-id`` 定死(网页应用没有「模型」这个概念)、身份由飞书免登给定、workspace 由"
            "后端派生且前端不传 —— 见 feishu-web/AGENTS.md 的产品决定一节。要真做这些是独立的产品"
            "决定, 不是「补功能」。"
        )
    for name in ("modelPresets.ts", "providers.ts", "bootstrapAi.ts", "authFlow.ts", "workspaceMatch.ts"):
        assert not (SERVICES / name).exists(), f"``feishu-web/src/services/{name}`` 出现了: 同上。"
