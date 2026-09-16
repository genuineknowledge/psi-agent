"""任务总览 / 任务上下文 / 交付物 —— 2026-09-16 报上来的四件事, 逐条钉住。

## 四件事与它们的根因

1. **刚打开时该落在与飞书机器人共用的那条会话上**。
   用户从飞书工作台点进来, 想接着说的是刚才在 IM 里那句; 而列表顺序由后端定, ``list[0]``
   常常是网页新建的别的会话。判据: 首屏默认选择与列表排序都把 ``from_im`` 那条放最前。

2. **新建对话不该带着飞书 bot 那条的历史** —— 这是 **bug, 不是设计**。
   后端给新会话发新 uuid、写新 jsonl(``_web_create_session``), 不复制任何东西。前端这边
   ``useSessionHistory`` 把上一次的结果留在 state 里, 切会话那一瞬 ``App.tsx`` 的
   「历史 → 消息」effect 恰好跑一次, 于是上一条会话的转写被铺进新会话; 等真正的空结果回来,
   that effect 已被自己的 ``messages.length > 0`` 守卫挡住, 旧内容永久留下。
   判据: 历史行与它所属的会话 id **绑在一起存**, 陈旧数据在结构上不可见。

3. **左侧任务上下文永远不显示进度**。
   它由 ``/sessions/{id}/todos`` 与 ``/todo-segments`` 驱动, 而这两条既在云上反代白名单之外、
   骨架里又一行鉴权都没有 → 云上恒 404。改打 ``/feishu/`` 前缀下的对等物(见
   ``tests/integration/test_feishu_web_peer_routes.py``)。另外两个同源毛病: 换会话时
   ``selectedSegment`` 不重置, 面板会一直停在只读历史态; 交付物只算历史那份, 刚交付完的会话
   显示「0 份」而右侧抽屉显示 1 份。

4. **四个指标与导出**。
   「本月执行」写死 ``"128"``; 导出按钮**没有 onClick**(点了什么都不发生); 用户要的是两件事:
   一个装全部交付物、可挑着下载的宝箱, 与一个导出对话历史(原始 jsonl)的入口。

## 为什么是静态核对

feishu-web 没有 vitest(理由见 ``test_feishu_web_dev_strict_port.py``), 本目录的前端约定一律
用**源码形状**守 —— 与 ``test_feishu_web_im_session_ui.py`` 同款。行为面(401/403/404、下载
边界、导出字节)由 ``tests/integration/test_feishu_web_peer_routes.py`` 真起 HTTP 打。
"""

from __future__ import annotations

import re
from pathlib import Path

FEISHU_WEB = Path(__file__).resolve().parents[3] / "src" / "psi_agent" / "gateway" / "feishu" / "feishu-web"
SRC = FEISHU_WEB / "src"
API_TS = SRC / "api.ts"
APP_TSX = SRC / "App.tsx"
USE_SESSIONS = SRC / "hooks" / "useSessions.ts"
USE_TASKS = SRC / "hooks" / "useTasks.ts"
TASKS_VIEW = SRC / "components" / "tasks-view.tsx"
TASK_MODEL = SRC / "services" / "taskModel.ts"
CHEST = SRC / "components" / "deliverables-chest.tsx"
EXPORT_DIALOG = SRC / "components" / "export-history-dialog.tsx"
CHAT_VIEW = SRC / "components" / "chat-view.tsx"
NEW_TASK_PAGE = SRC / "components" / "new-task-page.tsx"
ARTIFACT_FILE_BODY = SRC / "components" / "artifact-file-body.tsx"
ARTIFACT_DRAWER = SRC / "components" / "artifact-drawer.tsx"
DELIVERY_PREVIEW_MODAL = SRC / "components" / "delivery-preview-modal.tsx"
USE_CHAT_TURN = SRC / "hooks" / "useChatTurn.ts"
STYLES_CSS = SRC / "styles.css"
ROUTES_PY = FEISHU_WEB.parent / "_routes.py"


def _code(path: Path) -> str:
    """读源码并去掉注释 —— 判据只该看代码, 不该看解释性注释(理由同 im_session_ui 那份)。"""
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    text = re.sub(r"(?m)^[ \t]*//.*$", "", text)
    return re.sub(r"(?m)^[ \t]*#.*$", "", text)  # Python 侧: 整行注释


def test_judged_files_exist() -> None:
    """缺文件时下面几条会「因为没找到所以通过」。"""
    for path in (
        API_TS,
        APP_TSX,
        USE_SESSIONS,
        USE_TASKS,
        TASKS_VIEW,
        TASK_MODEL,
        CHEST,
        EXPORT_DIALOG,
        ROUTES_PY,
    ):
        assert path.is_file(), f"找不到 {path} —— 本文件的判据已失效, 请同步路径"


# ---- 1. 首屏落在共用会话上, 且它排最前 ------------------------------------


def test_first_open_lands_on_the_im_shared_session() -> None:
    sessions = _code(USE_SESSIONS)

    assert "list.find((s) => s.from_im)?.id" in sessions, (
        "``useSessions`` 的首屏默认选择不再优先 ``from_im`` —— 用户从飞书工作台点进来, 期待"
        "接着说的是刚才在 IM 里那句, 而 ``list[0]`` 常常是网页新建的别的会话, 落在那里会让人"
        "以为自己的对话丢了。"
    )
    assert "current ||" in sessions, (
        "``setCurrentId`` 的初值不再是 ``current || …`` —— 刷新/重挂时会把用户已经选中的会话顶掉, 又跳回共用那条。"
    )


def test_im_shared_session_sorts_first() -> None:
    tasks = _code(USE_TASKS)

    assert "Number(b.fromIm) - Number(a.fromIm)" in tasks, (
        "``useTasks`` 不再把 ``from_im`` 那条排到最前。首屏列表会被网页新建的会话占满, 用户"
        "得自己去找那条与飞书机器人共用的会话。"
    )
    # 置顶仍然压过它: 那是用户自己排的序, 不能被默认规则顶掉。
    assert "sortTasksByPin(imFirst, pinnedIds)" in tasks, (
        "IM 优先的排序与置顶排序的先后关系变了。顺序必须是「置顶 > IM 共用 > 其余」: 置顶是"
        "用户显式排的, 默认规则不该压过它。"
    )


# ---- 2. 新建会话不继承飞书 bot 的历史(bug 复核) ---------------------------


def test_history_rows_are_bound_to_their_session_id() -> None:
    sessions = _code(USE_SESSIONS)

    assert "stored.id === sessionId ? stored.rows : []" in sessions, (
        "``useSessionHistory`` 又变回「一份 raw 数组」了。切会话那一瞬上一条会话的结果还在, "
        "``App.tsx`` 的历史 effect 会把**别的会话的转写**铺进新会话, 而真正的空结果回来时"
        "那个 effect 已被 ``messages.length > 0`` 挡住 —— 表现就是「新建对话后仍看得到与飞书"
        "bot 那条会话的历史」。判据必须是**结构上不可见**(按 id 派生), 不是靠 effect 先后。"
    )
    assert "const raw = stored.id" in sessions, "``raw`` 不再是按当前 id 派生的了。"
    assert "setRaw" not in sessions, (
        "``useSessionHistory`` 又把 ``setRaw`` 交出去了 —— 谁都能往里塞不属于当前会话的行, 上面那条结构性保证就没了。"
    )


# ---- 3. 任务上下文: 进度真的能到 ------------------------------------------


def test_todo_endpoints_go_through_the_authenticated_peer_routes() -> None:
    api = _code(API_TS)

    for suffix in ("todos", "todo-segments"):
        assert f"/feishu/sessions/${{encodeURIComponent(sessionId)}}/{suffix}" in api, (
            f"``api.ts`` 打的不再是 ``/feishu/sessions/{{id}}/{suffix}``。裸的 ``/sessions/{{id}}/"
            f"{suffix}`` 在云上**不在反代白名单里**(恒 404)、本地又一行鉴权都没有 —— "
            "左侧任务上下文的进度/步骤/历史子任务全由它驱动, 表现是永远「待继续」、进度恒 0。"
        )
    # 「没有裸路由」要按**字符串起始位置**判: ``/feishu/sessions/…`` 里本身就含
    # ``/sessions/…`` 这段子串, 直接 ``in`` 会永远命中(实测踩过)。
    for suffix in ("todos", "todo-segments"):
        bare = re.search(r"[`\"']/sessions/\$\{encodeURIComponent\(sessionId\)\}/" + suffix, api)
        assert bare is None, (
            f"``api.ts`` 里还有裸的 ``/sessions/{{id}}/{suffix}`` —— 云上不在反代白名单里(恒 "
            "404), 本地又一行鉴权都没有。改打 ``/feishu/sessions/{{id}}/…`` 对等物。"
        )
    assert "getTodoSegment" in api and "/todo-segments/${encodeURIComponent(segmentId)}" in api, (
        "``api.ts`` 的历史子任务详情没有改到 ``/feishu/`` 对等物上, 点开某一段仍然是 404。"
    )


def test_session_switch_resets_the_viewed_segment() -> None:
    app = _code(APP_TSX)

    assert re.search(r"useEffect\(\(\) => \{\s*setSelectedSegment\(\"live\"\);\s*\}, \[sessions\.currentId\]\)", app), (
        "``App.tsx`` 不再在换会话时把 ``selectedSegment`` 收回 ``live``。它是全局一个 id, 属于"
        "**上一个**会话: 从任务总览点另一条(那条走的是 ``setCurrentId``, 不经过 ``openChat``)"
        "之后, 左侧面板会一直停在只读历史态 —— 表现就是「进度永远不会更新」。"
    )


def test_new_deliverables_count_in_the_task_files() -> None:
    tasks = _code(USE_TASKS)

    assert "[...(deliverables[session.id]?.files ?? []), ...newDeliverables]" in tasks, (
        "``useTasks`` 不再把「本轮流式刚收到、还没写进历史」的交付物并进 ``files``。只算历史"
        "那一份的话, 刚交付完的会话在左侧「历史交付物」显示「0 份」而右侧抽屉显示 1 份 —— "
        "同一条会话两处口径不一致, 用户会以为文件没生成。"
    )


# ---- 4. 四个指标 / 宝箱 / 导出对话历史 ------------------------------------


def test_monthly_metric_is_computed_not_hardcoded() -> None:
    view = _code(TASKS_VIEW)
    tasks = _code(USE_TASKS)
    model = _code(TASK_MODEL)

    assert 'statCell("128"' not in view, (
        '「本月执行」又写死成 ``statCell("128", …)`` 了。四个指标里只有它是假的: 另外三个都是'
        "真算出来的计数, 用户会拿它当真实用量。"
    )
    assert 'statCell(String(monthlyRuns), "本月执行"' in view, "「本月执行」不再是 ``monthlyRuns`` 了。"
    assert "export function countMonthlyRuns(" in model, (
        "``taskModel.ts`` 里没有 ``countMonthlyRuns`` —— 口径必须住在纯函数里才解释得清、也才"
        "测得了(按会话去重, 取 todo 段的时间戳)。"
    )
    assert "getFullYear() === year && at.getMonth() === month" in model, (
        "``countMonthlyRuns`` 不再按**自然月**判断了 —— 改成滚动 30 天之类的口径前先想清楚: "
        "它旁边的三个指标都是「当前状态」, 这一格是「这个月」, 口径要能一句话说清。"
    )
    assert "countMonthlyRuns(segments)" in tasks, "``useTasks`` 没有把 todo 段喂给 ``countMonthlyRuns``。"


def test_export_button_is_not_dead() -> None:
    view = _code(TASKS_VIEW)

    assert "onClick={onExportHistory}" in view, (
        "任务总览的「导出对话历史」按钮没有 onClick —— 上一版那个「导出」就是这样一具死按钮, 点了什么都不发生。"
    )
    assert "onClick={onOpenChest}" in view, "「宝箱」按钮没有 onClick。"
    assert re.search(r'className="ht-btn">\s*<Download size=\{14\} />导出</button>', view) is None, (
        "死掉的「导出」按钮又回来了(它没有任何 onClick)。导出对话历史与宝箱是两个入口, 见下面两条用例。"
    )


def test_chest_and_history_export_are_separate_entries() -> None:
    chest = _code(CHEST)
    dialog = _code(EXPORT_DIALOG)
    app = _code(APP_TSX)

    # 宝箱 = 全部交付物, 挑着下载; 逐个取, 不打包 zip(jszip 在本仓只是传递依赖)。
    assert "fetchDeliverable(item.sessionId, item.path)" in chest, (
        "宝箱不再按「会话 + 路径」向带鉴权的下载路由取文件 —— 那条路由是唯一会读磁盘的入口, "
        "鉴权与白名单都挂在它身上, 不能绕。"
    )
    assert "downloadable(item)" in chest, (
        "宝箱不再区分「只有文件名、拿不到路径」的条目 —— 那种点了会 400/403, 静默藏掉又会让"
        "用户以为文件丢了, 所以留着但置灰。"
    )
    assert "fetchSessionHistoryFile(session.id)" in dialog, (
        "导出对话历史不再打 ``/feishu/sessions/{id}/export`` —— 那条回的是磁盘上的**原始 "
        "jsonl**(含工具调用参数与 thinking_ms), 而 ``/history`` 是投影过的显示行, 拿回去与"
        "原始记录对不上。"
    )
    assert 'type="checkbox"' in dialog, "导出对话历史不再是勾选式的了(用户要能挑哪几条导出)。"

    assert "<DeliverablesChest" in app and "<ExportHistoryDialog" in app, (
        "``App.tsx`` 没有再挂这两个浮层 —— 组件写了但没人渲染。"
    )


def test_chest_entry_is_an_icon_with_a_tooltip() -> None:
    """宝箱 = 全部交付物, **只给图标**(与 ToC 同一个图标), 名字挂在鼠标提示上。

    页头那一行右边已经有「新建任务」, 再塞一个带字的按钮会把「导出对话历史」挤掉; 而宝箱
    与对话顶栏那颗是同一件事, 用同一个图标才认得出。判据: 图标是 ``TreasureVisual``(ToC
    的 ``TreasureButton`` 里也是它), 文案只出现在 ``title``/``aria-label`` 上。
    """
    view = _code(TASKS_VIEW)

    assert 'aria-label="所有交付物"' in view and 'title="所有交付物"' in view, (
        "宝箱那颗图标按钮的鼠标提示不再是「所有交付物」—— 只有图标没有名字时, 提示就是它唯一的"
        "说明(tooltip 与 aria-label 要一起写, 否则读屏用户只听到「按钮」)。"
    )
    assert ">宝箱" not in view, "宝箱按钮又带上了文字。它要与对话顶栏那颗图标保持一致: 图标 + 悬停提示, 不写文字。"
    assert "<TreasureVisual state={deliverableTotal > 0" in view, (
        "宝箱不再是 ``TreasureVisual`` —— 与 ToC 的交付物图标就不是同一个了。"
    )


def test_no_duplicate_chest_button_in_the_detail_panel() -> None:
    """详情面板里不再重复一个「打开宝箱」按钮 —— 同一件事在一屏出现两次只会让人犹豫。"""
    view = _code(TASKS_VIEW)

    assert "打开宝箱" not in view, (
        "``tasks-view.tsx`` 里又出现了「打开宝箱(全部交付物)」按钮。全部交付物的入口是页头"
        "那颗宝箱图标, 面板里只留「打开新交付物」。"
    )
    assert view.count("onClick={onOpenChest}") == 1, (
        "``onOpenChest`` 的**按钮**不再是 1 个 —— 全部交付物的入口只该有一处(页头那颗图标);"
        "面板里再来一个, 同一件事就在一屏里出现两次。"
        "(数的是 ``onClick={onOpenChest}``: 属性声明与解构里各还会出现一次同名标识符。)"
    )


def test_org_session_is_hidden_from_the_task_list() -> None:
    """组织共享会话**不进**任务列表 —— 它在网页应用里只能只读查看, 混在用户任务里只有干扰。

    2026-09-16 产品决定(实测反馈「感觉没有什么用」)。隐藏 ≠ 放开: 归属判定一字未改, 直打
    ``/feishu/sessions/{id}/…`` 仍然是「历史可读、写入 403」。两处用的是同一个
    ``is_org_session`` 判据, 免得「列表藏起来了」与「闸还在不在」各说各话。
    """
    routes = _code(ROUTES_PY)

    assert 'owned = [r for r in rows if not is_org_session(r.id, r.workspace or "")]' in routes, (
        "``_web_list_sessions`` 不再过滤组织共享会话 —— 它又会出现在任务列表里(只读、无标题、永远停在待开始)。"
    )
    assert "return _json([_web_session_data(r, from_im=r.id == bot_sid) for r in owned])" in routes, (
        "列表返回的不是过滤后的那份 —— 过滤写了但没用上。"
    )
    # 闸还在: 隐藏它不等于放开写。
    assert "raise _AccessDeniedError(403, ORG_SESSION_READ_ONLY)" in routes, (
        "组织共享会话的只读闸被一起删掉了 —— 隐藏它只是不显示, 不代表谁都能往里写。"
    )


def test_org_session_read_only_ui_remains_as_a_guard() -> None:
    """只读那套界面留着当**兜底**: 列表里看不到它了, 但直打深链的人仍会撞上 403。

    历史: 2026-09-16 上午先做的是「在列表里把它标成只读」(那时它还在列表里), 下午按实测
    反馈把它从列表里去掉。界面那套(角标 + 关掉输入框 + 中文文案)因此变成**兜底**而不是死代码
    —— 万一它从另一个入口露出来, 用户不该再次「打完字才收到一句拒绝」。
    """
    api = _code(API_TS)
    model = _code(TASK_MODEL)
    tasks = _code(USE_TASKS)
    view = _code(TASKS_VIEW)
    chat = _code(CHAT_VIEW)
    app = _code(APP_TSX)
    routes = _code(ROUTES_PY)

    assert "read_only?: boolean" in api, (
        "``SessionInfo`` 里没有 ``read_only`` —— 后端已在下发, 前端不接就没法把关掉输入框的判据拿到手里。"
    )
    assert 'data["read_only"] = is_org_session(' in routes, (
        "``_web_session_data`` 不再下发 ``read_only`` —— 深链进来的人又会打完字才吃 403。"
    )
    assert "readOnly: session.read_only === true" in tasks, "``useTasks`` 没有把 ``read_only`` 传进 Task。"
    assert "readOnly: src.readOnly" in model, "``buildTask`` 没有把只读标记带出来。"
    # 角标**要挂在 readOnly 这个闸上**: 光有 ``ht-badge-ro`` 这个类名不算数(把闸改成
    # ``false`` 时样式还在源码里, 判据却什么都测不到 —— 变异复核时实测踩过)。
    assert "{t.readOnly && (" in view, "任务列表里没有按 ``readOnly`` 打只读角标。"
    assert 'className="ht-badge-ro"' in view, "只读角标的样式类名不见了。"
    assert "selected.readOnly" in view, "详情面板对只读会话没有任何说明。"
    # 输入框整块换成说明, 而不是「能打字但发不出去」。
    assert "readOnly ? (" in chat and "focus-chat-readonly" in chat, (
        "``chat-view.tsx`` 没有在只读时把输入区换成说明 —— 用户会打完字才吃到一个 403。"
    )
    assert "readOnly={currentTask?.readOnly" in app, "``App.tsx`` 没有把只读标记传给 ChatView。"


def test_peer_routes_are_registered_and_authorized() -> None:
    """只读那一族的五条路由都在, 且共用同一份准入判定。"""
    routes = _code(ROUTES_PY)

    for suffix in ("todos", "todo-segments", "files", "export"):
        assert f'"/feishu/sessions/{{session_id}}/{suffix}"' in routes, (
            f"``_routes.py`` 里没有 ``/feishu/sessions/{{session_id}}/{suffix}`` 这条路由 —— "
            "前端会打它, 本地就会 404, 不用等上云。"
        )
    assert "def _authorize_session(" in routes, (
        "会话级路由的准入判定不再是共用的 ``_authorize_session`` —— 后加的路由会各写一份, "
        "而漏掉的那份就是一条没有归属校验的越权口。"
    )
    # 下载那条是这一族里唯一读磁盘的: 必须过白名单, 不能只看路径存在。
    assert "_session_deliverable_paths(" in routes and "not a deliverable of this session" in routes, (
        "下载路由不再校验「这份文件是这条会话声明过的交付物」。只判路径存在的话, 任何登录用户"
        "都能拿别人的任意路径去读服务器上的文件。"
    )


def test_new_task_failure_is_visible() -> None:
    """新建会话失败必须**显示出来** —— 静默失败会把人推到别的会话里去。

    实测那条路径: 建会话失败 → ``createFromDraft`` 直接 return → 新建页什么都不显示 →
    用户以为「点了没反应」→ 退回去在列表里找一条接着打字 → 正好落在只读的组织共享会话上。
    所以 ``create()`` 要把原因交出来, 新建页要把它渲染出来。
    """
    sessions = _code(USE_SESSIONS)
    page = _code(NEW_TASK_PAGE)
    app = _code(APP_TSX)

    assert "Promise<{ id: string; error: string }>" in sessions, (
        "``useSessions.create`` 不再把失败原因交出来(只返回空 id) —— 调用方就没得显示。"
    )
    assert "setCreateError(error ||" in app, "``createFromDraft`` 建会话失败时没有设置错误文案。"
    assert "error={createError || undefined}" in app, "``App.tsx`` 没有把错误传给新建页。"
    assert '{error && <div className="ht-error" role="alert">{error}</div>}' in page, (
        "``new-task-page.tsx`` 不显示建会话失败的原因 —— 用户看到的是「点了发送没反应」。"
    )


def test_running_and_settled_are_real_states() -> None:
    """状态不能只由 todo 推: 跑完一轮却没写过 todo 的会话必须显示「运行中 / 已完成」。

    实测反馈: 一条会话正常跑完(甚至产生了回复), 任务总览里仍是「待开始 / 0%」—— 因为
    ``summary.total`` 恒为 0, 而旧实现把 total==0 一律读成「待开始」。C 端不是这么算的:
    它另有两个信号(``streaming`` / ``turnSettled``, 见 spa-v2 的 ``taskProgress.ts``)。
    """
    model = _code(TASK_MODEL)

    assert "streaming: boolean" in model and "turnSettled: boolean" in model, (
        "``TaskSource`` 里没有 ``streaming`` / ``turnSettled`` —— 只靠 todo 判不出运行中与已完成。"
    )
    assert 'if (ctx.streaming) return "运行中";' in model, (
        "``statusOf`` 不再给出「运行中」—— 列表上就没有任何标记能看出某条正在干活。"
    )
    assert 'if (!total) return ctx.turnSettled ? "已完成" : "待开始";' in model, (
        "无 todo 的会话又被一律判成「待开始」了 —— 那正是「干完活还显示待开始」的来源。"
    )
    # 无 todo 轨道时不编造百分比: 运行中转圈, 落定才是 100%。
    assert "return { progress: ctx.turnSettled ? 100 : 0, indeterminate: ctx.streaming };" in model, (
        "``progressOf`` 对无 todo 的会话不再区分「运行中(不确定态)」与「已落定(100%)」。"
    )
    assert ': src.turnSettled\n      ? "done"' in model, "``buildTask`` 的 phase 不再看 ``turnSettled``。"
    assert '"正在处理"' in model and '"本轮已完成"' in model, (
        "无清单时的活动文案不是 C 端那套(正在处理 / 正在整理交付 / 本轮已完成 / 待继续)。"
    )


def test_todos_are_polled_while_a_turn_runs() -> None:
    """回合进行中要**轮询**那条会话的 todo/子任务, 否则左侧任务上下文整轮都不动。

    实测反馈: 「执行过程中一直待继续/0%, 做完才跳成已完成」。C 端为此专门有个 2.5 秒的
    轮询(``HaiTunAgentWorkspace`` 里的注释就是「While Agent runs, poll todos so middle
    step updates mid-turn」), 间隔取的是同一个值。
    """
    tasks = _code(USE_TASKS)
    app = _code(APP_TSX)
    turn = _code(USE_CHAT_TURN)

    assert "window.setInterval(() => void refreshOne(sendingSessionId), 2500)" in tasks, (
        "``useTasks`` 不再在回合进行中轮询那条会话的 todo —— 执行过程中左侧上下文不会更新。"
    )
    assert "void refreshOne(sendingSessionId);" in tasks, (
        "按下发送后没有**立刻**拉一次 —— 第一次轮询要等 2.5 秒, 而工具的第一次写入往往在那之前。"
    )
    assert "sendingSessionId: turn.sendingSessionId" in app, "``App.tsx`` 没有把「哪条在跑」传给 useTasks。"
    assert "sendingSessionId" in turn and "settledBySession" in turn, (
        "``useChatTurn`` 没有暴露按会话的运行中/已落定信号。"
    )
    assert "settled: true" in turn, "回合结束时没有标记 settled —— 总览里的状态回不到「已完成」。"
    assert "settled: false" in turn, "新一轮开始时没有清掉 settled —— 上一轮的结果会被当成本轮。"


def test_settled_state_survives_a_reload() -> None:
    """「跑完过一轮」要能从**历史**恢复: 只靠内存信号的话, 刷新页面就退回「待开始」。"""
    app = _code(APP_TSX)
    tasks = _code(USE_TASKS)

    assert "replied = messages.some(" in app, (
        "``App.tsx`` 不再从历史判断「有没有助手回复过」—— 刷新后状态会退回待开始。"
    )
    assert "deliverables[session.id]?.replied === true" in tasks, (
        "``useTasks`` 没有用历史的 ``replied`` 兜底, 只信内存里的回合信号。"
    )


def test_deliverable_preview_uses_the_authenticated_route() -> None:
    """预览不能走 ``/workspace/file`` —— 那条在云上/调试隧道里恒 404(实测「预览文件 404」)。"""
    body = _code(ARTIFACT_FILE_BODY)
    drawer = _code(ARTIFACT_DRAWER)
    api = _code(API_TS)

    assert "readDeliverable(sessionId, path)" in body, (
        "``artifact-file-body.tsx`` 不再走带鉴权的交付物路由 —— 云上点开文件就是 404。"
    )
    assert "export async function readDeliverable(sessionId: string, path: string)" in api, (
        "``api.ts`` 里没有 ``readDeliverable``。"
    )
    assert "fetchDeliverable(sessionId, path)" in api, "``readDeliverable`` 没走 ``fetchDeliverable`` 那条对等路由。"
    assert "<ArtifactFileBody sessionId={sessionId}" in drawer, (
        "``artifact-drawer.tsx`` 没有把 session id 传给预览 —— 交付物路由要它做归属校验。"
    )
    assert "readWorkspaceFile" not in api, (
        "``api.ts`` 里还留着 ``readWorkspaceFile``(/workspace/file) —— 它在云上不可达, 留着只会被再次误用。"
    )


def test_task_row_opens_on_double_click() -> None:
    """任务行双击 = 进对话。此前「打开」只有详情面板里那个按钮一个入口。"""
    view = _code(TASKS_VIEW)

    assert "onDoubleClick={() => onOpenChat(t.id)}" in view, (
        "任务行没有双击打开 —— 想进对话得先点行、再把视线挪到右侧面板、再点「继续对话」。"
    )
    assert 'title="双击打开对话"' in view, (
        "双击这件事没有任何提示 —— 用户不会去试(行的主要用途就是进去接着聊, 值得说出来)。"
    )
    assert "onClick={() => onSelect(t.id)}" in view, "单击不再选中了 —— 单击留作「先看看右侧详情」, 双击才进对话。"


def test_preview_drawer_is_an_opaque_side_panel() -> None:
    """预览必须是**贴着右边的实底侧栏**, 不是一层透明的浮层。

    实测现象(2026-09-16): 预览打开后整个页面从它里面透出来, 像一层贴纸。根因是那条规则写作
    ``.file-preview.preview-drawer`` —— 选择器要求 ``file-preview`` 这个类, 而两个组件
    (ArtifactDrawer / DeliveryPreviewModal)render 的都是 ``preview-drawer``(+``wide``),
    整条规则一条都没命中: 没底色、没宽度、没阴影。CSS 的失败方式就是**安静地不生效**。
    """
    css = _code(STYLES_CSS)
    block = re.search(r"\.preview-drawer \{[^}]*\}", css)

    assert block is not None, "``styles.css`` 里没有 ``.preview-drawer`` 这条基线规则。"
    rule = block.group(0)
    assert "background: #fff" in rule, "侧栏没有实底 —— 页面会从预览里透出来。"
    assert "width: min(720px, 92vw)" in rule, "侧栏没有宽度, 内容多宽它就多宽(会盖住整屏)。"
    assert "box-shadow" in rule, "侧栏与页面之间没有分隔阴影, 看起来像一层浮贴纸。"
    assert "animation: drawer-in" in rule, "侧栏没有滑入动画(C 端是 330ms 曲线)。"
    assert ".file-preview.preview-drawer" not in css, (
        "``.file-preview.preview-drawer`` 又出现了 —— 那个前缀类没有任何组件在用, 写着它的规则"
        "不会生效(这正是「预览透明」的根因)。规则要挂在组件实际 render 的类上。"
    )
    # 遮罩: 压暗 + 轻虚化, 与 C 端 ``.drawer-backdrop`` 同一手法。
    scrim = re.search(r"\.preview-scrim \{[^}]*\}", css)
    assert scrim is not None and "backdrop-filter: blur" in scrim.group(0), (
        "遮罩没有虚化 —— 与 C 端 `.drawer-backdrop` 的手法不一致。"
    )
    # 「随时可关」的三条路都在: 头部 X、点遮罩、Esc。
    assert "onClick={onClose}" in _code(ARTIFACT_DRAWER), "交付物抽屉没有关闭按钮/遮罩点击关闭。"
    assert "onClick={onClose}" in _code(DELIVERY_PREVIEW_MODAL), "单文件预览没有关闭按钮/遮罩点击关闭。"
    assert "if (previewFile) {" in _code(APP_TSX), "Esc 关不掉单文件预览。"


def test_title_and_delete_routes_go_through_the_write_family() -> None:
    """标题 / 生成标题 / 删除三条也换到带鉴权的对等物上 —— 它们此前在云上是静默失败的。

    裸的 ``POST /titles`` 与 ``DELETE /sessions/{id}`` 都不在反代白名单里且一行鉴权都没有,
    云上的表现是「列表里永远是未命名任务」与「删除按钮点了没反应」。
    """
    api = _code(API_TS)
    routes = _code(ROUTES_PY)

    assert 'requestJson<unknown>("/feishu/titles", jsonPost({ id, title }))' in api, (
        "``setTitle`` 不再走 ``/feishu/titles`` —— 云上那条无鉴权裸路由被白名单挡着, 改不了名。"
    )
    assert '"/feishu/titles/generate"' in api, (
        "``generateTitle`` 不再走 ``/feishu/titles/generate`` —— 云上生成不了标题, 列表里那条"
        "会话就永远是「未命名任务」。"
    )
    assert '`/feishu/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }' in api, (
        "``deleteSession`` 不再走 ``DELETE /feishu/sessions/{id}``。"
    )
    for bare in ('"/titles"', '"/titles/generate"', "`/sessions/${encodeURIComponent(id)}`"):
        assert bare not in api, f"``api.ts`` 里还有裸路由 {bare} —— 云上过不了白名单。"

    assert '"/feishu/sessions/{session_id}", _web_delete_session' in routes, (
        "``_routes.py`` 里没注册 ``DELETE /feishu/sessions/{session_id}``。"
    )
    assert '"/feishu/titles", _web_set_title' in routes, "``_routes.py`` 里没注册 ``POST /feishu/titles``。"
    assert '"/feishu/titles/generate", _web_generate_title' in routes, (
        "``/feishu/titles/generate`` 没注册 —— 它是一条**精确路径**, 只在 deploy 侧加白名单"
        "是不够的(本地与容器里都会 404)。"
    )


def test_the_im_shared_session_delete_gate_is_server_side() -> None:
    """删除的硬闸必须在**后端按会话 id**判, 不能只靠前端藏按钮。

    前端藏的是「显示层的闸」: 直打接口照样能删。而判据也不能改成读前端传来的 ``from_im`` ——
    那等于让调用方自己声明自己有没有权限, 改个 body 字段就绕过。
    """
    routes = _code(ROUTES_PY)

    assert "fm.session_id_for(identity.open_id)" in routes, (
        "删除路由不再用 ``fm.session_id_for(identity.open_id)`` 认出「与机器人共用那条」—— "
        "那条承载的是 IM 里的同一份上下文, 删掉等于把机器人那侧一起扔掉。"
    )
    assert "cannot be deleted" in routes, "删除路由的硬闸没了(错误文案是这条判据的锚点)。"
    assert "_delete_session(request)" in routes, (
        "删除路由不再复用骨架的 ``_delete_session`` —— 会话/历史/todo/标题/摘要五处要一起清, "
        "复制一份实现必然有一处先漏。"
    )
