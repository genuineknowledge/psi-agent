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


def test_org_session_is_shown_as_read_only() -> None:
    """组织共享会话必须在界面上标出来, 并把输入框关掉。

    现象(2026-09-16 实测): 用户以为自己在一条新对话里, 打了字发出去, 只收到一句
    ``org session is read-only`` —— 那条会话是组织共享的调度会话, 本来就只读。它在列表里与
    用户自己的会话长得一模一样, 不标出来就只能靠撞一次 403 才知道。
    """
    api = _code(API_TS)
    model = _code(TASK_MODEL)
    tasks = _code(USE_TASKS)
    view = _code(TASKS_VIEW)
    chat = _code(CHAT_VIEW)
    app = _code(APP_TSX)
    routes = _code(ROUTES_PY)

    assert "read_only?: boolean" in api, (
        "``SessionInfo`` 里没有 ``read_only`` —— 后端已经在 ``/feishu/sessions`` 里下发了, "
        "前端不接就没法把只读会话标出来。"
    )
    assert 'data["read_only"] = is_org_session(' in routes, (
        "``_web_session_data`` 不再下发 ``read_only`` —— 前端拿不到这个判据, 只读会话又会和用户自己的会话长得一模一样。"
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
