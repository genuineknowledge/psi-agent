"""守 feishu-web 的 ToB 界面约定(2026-09-15 定, 三条都是**产品决定**, 不是实现细节)。

1. **侧栏品牌标必须是真图标**(海豚图), 不能是自绘色块。
   原先 ``desktop-shell.tsx`` 用 ``<span className="ht-app-mark" />``, 样式里是一个蓝绿渐变
   方块(``linear-gradient(135deg, #3370ff, #12a594)``), 与页面其余位置的品牌标
   (``brandMark()`` → ``.brand-logo-art`` → 海豚 PNG)不一致 —— 在飞书客户端左上角看起来
   就是"图标不对"。判据: 那一处必须走 ``brandMark``, 且 ``.ht-app-mark`` 不能回来。

2. **与飞书机器人共用那条会话有固定显示名「海豚一号」**。
   它 ``from_im``, 后端对它没有标题, 原先落到 "未命名任务" —— 在列表里看不出它是谁, 用户
   会以为那是个空任务。判据: 名字由 ``taskModel.IM_SESSION_TITLE`` 单点给出, 且
   ``buildTask`` 与 ``App.tsx`` 都走 ``displayTitle``, 不许各自再写一份 ``|| "未命名任务"``
   (两份判据必然有一份先漏, 表现是同一会话在列表与顶栏显示不同名字)。

3. **共用那条不可删除**。
   它承载的是与飞书机器人**同一条**对话, 删掉等于把机器人那侧的上下文一起扔掉, 而用户在 IM
   里还会继续用到它。判据: ``tasks-view.tsx`` 的两个删除入口(列表行内、详情面板)都对
   ``fromIm`` 加闸, 且删除调用点总数不增加。

为什么是静态核对: feishu-web 没有 vitest(理由见 ``test_feishu_web_dev_strict_port.py``),
本目录的前端约定一律用**源码形状**守 —— 与 ``test_feishu_web_stream_render.py`` 同款。
"""

from __future__ import annotations

import re
from pathlib import Path

FEISHU_WEB = Path(__file__).resolve().parents[3] / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web"
FEISHU_WEB_SRC = FEISHU_WEB / "src"
DESKTOP_SHELL = FEISHU_WEB_SRC / "components" / "desktop-shell.tsx"
TASKS_VIEW = FEISHU_WEB_SRC / "components" / "tasks-view.tsx"
TASK_MODEL = FEISHU_WEB_SRC / "services" / "taskModel.ts"
APP_TSX = FEISHU_WEB_SRC / "App.tsx"
STYLES_CSS = FEISHU_WEB_SRC / "styles.css"


def _code(path: Path) -> str:
    """读源码并**去掉注释** —— 判据只该看代码, 不该看解释性注释。

    这一层是必需的, 不是洁癖: 上面那几条"不许再出现"的断言, 恰好都会被**解释为什么删掉它**
    的那段注释命中(``styles.css`` 里那句"原先这里是 .ht-app-mark…"、``App.tsx`` 里那句
    "…会先闪一下 ``|| \"未命名任务\"``…")。判据因此会因为这些注释而假红 —— 实测踩过一次。
    """
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)  # 块注释(含 CSS 的)
    return re.sub(r"(?m)^[ \t]*//.*$", "", text)  # 整行的 // 注释


def test_judged_files_exist() -> None:
    """判据自身的存在性: 文件被挪走时, 下面几条会「因为没找到所以通过」。"""
    for path in (DESKTOP_SHELL, TASKS_VIEW, TASK_MODEL, APP_TSX, STYLES_CSS):
        assert path.is_file(), f"找不到 {path} —— 本文件的判据已失效, 请同步路径"


def test_sidebar_brand_uses_the_real_mark() -> None:
    """左上角品牌标必须用 ``brandMark`` 的海豚图, 不是自绘色块。"""
    shell = _code(DESKTOP_SHELL)
    css = _code(STYLES_CSS)

    assert 'brandMark("sidebar")' in shell, (
        '``desktop-shell.tsx`` 的侧栏品牌标不再走 ``brandMark("sidebar")``。改用回自绘的 '
        "``.ht-app-mark`` 色块会让左上角与应用其余位置的品牌标(海豚 logo)不一致 —— "
        "在飞书客户端里看起来就是「图标不对」。改显示名/尺寸请改 ``.brand-logo-sidebar``。"
    )
    assert ".brand-logo-sidebar" in css, (
        '``styles.css`` 里找不到 ``.brand-logo-sidebar`` —— 它是 ``brandMark("sidebar")`` '
        "真正需要的那条尺寸规则, 缺了图标会塌成 0 尺寸(看起来就是没图标)。"
    )
    assert "ht-app-mark" not in css, (
        "``.ht-app-mark``(蓝绿渐变方块)又回到 ``styles.css`` 了。那个自绘色块就是被替换掉的错误图标, 不要恢复。"
    )


def test_im_session_title_is_single_sourced() -> None:
    """共用会话的固定名只有一个来源, 且列表与顶栏都走它。"""
    model = _code(TASK_MODEL)
    app = _code(APP_TSX)

    assert 'IM_SESSION_TITLE = "海豚一号"' in model, (
        '``taskModel.ts`` 里找不到 ``IM_SESSION_TITLE = "海豚一号"``。与飞书机器人共用的那条'
        "会话必须显示固定名「海豚一号」, 而不是落到「未命名任务」。"
    )
    assert "export function displayTitle(" in model, (
        "``taskModel.ts`` 里没有 ``displayTitle`` —— 会话显示名必须走这一个入口, 否则列表与"
        "顶栏会各判一次 ``from_im``, 漏一处就会显示两个不同的名字。"
    )
    assert "title: displayTitle(src.session, src.title)" in model, (
        "``buildTask`` 没有走 ``displayTitle`` —— 任务列表里的标题会退回「未命名任务」。"
    )
    assert 'src.title || "未命名任务"' not in model, (
        '``buildTask`` 里又出现了 ``src.title || "未命名任务"`` 的裸兜底 —— 共用会话会在'
        "列表里显示成「未命名任务」。请走 ``displayTitle``。"
    )
    assert '|| "未命名任务"' not in app, (
        '``App.tsx`` 里又出现了 ``|| "未命名任务"`` 的兜底。顶栏与对话区应当统一用 '
        "``currentTitle``(内部走 ``displayTitle``), 否则共用会话在首屏会先闪一下"
        "「未命名任务」再变成「海豚一号」。"
    )


def test_shared_session_cannot_be_deleted() -> None:
    """两个删除入口都要加闸, 且不能新增第三个入口。

    闸门有两个, 缺一不可: ``fromIm``(与机器人共用那条)与 ``readOnly``(组织共享会话 ——
    后端对它的写一律 403, 摆一个点了必失败的按钮只会让人以为界面坏了)。
    """
    view = _code(TASKS_VIEW)

    assert "{!t.fromIm && !t.readOnly && (" in view, (
        "``tasks-view.tsx`` 列表行内的删除按钮没有对 ``fromIm`` / ``readOnly`` 加闸 —— "
        "共用那条删了等于把机器人侧上下文一起扔掉; 组织共享那条后端本来就不许任何人写。"
    )
    assert "{!selected.fromIm && !selected.readOnly && (" in view, (
        "``tasks-view.tsx`` 详情面板的「删除」按钮没有对 ``fromIm`` / ``readOnly`` 加闸。"
    )
    assert view.count("onDelete(") == 2, (
        "``tasks-view.tsx`` 里的删除调用点不再是 2 处。新增删除入口时必须同时加 ``fromIm`` / "
        "``readOnly`` 两道闸, 所以这条计数是刻意的 —— 见到它红, 先确认新入口也加了闸, "
        "再把这个数字改掉。"
    )
