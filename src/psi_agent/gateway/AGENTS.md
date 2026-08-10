# Gateway 层设计文档

## 概述

Gateway 是 psi-agent 的生命周期管理组件。它通过 OpenAPI REST 接口管理 AI 和 Session 的创建/删除/查询，并暴露面向 Web UI 的 Channel 端点。

Gateway 自身是一个独立的 aiohttp 进程，AI/Session 作为进程内 anyio task 运行。

## 架构

```
Gateway 进程
├── AIManager          — AI 实例注册表 + 生命周期管理
├── RouterManager      — Gateway 内部语义路由服务注册表 + 生命周期管理
├── SessionManager     — Session 实例注册表 + 生命周期管理
├── SchedulerManager   — 每 workspace 一个全量激活的调度 Session（触发其 schedules/，对 SPA 隐藏）
├── TitleManager       — 会话标题 CRUD + AI 自动生成
├── SummaryManager     — 任务摘要 CRUD + AI 生成（spa-v2）
├── WorkspaceManager   — 目录浏览
├── ChatManager        — SSE 流式对话管理
├── HistoryManager     — JSONL 历史读取（AppData `histories/` + legacy 双读）
├── TodoManager        — 会话 todo 列表只读（AppData `todos/` + legacy workspace 双读）
├── OAuthRelay         — OAuth 回调中继（state → code 一次性信箱，免用户手工复制授权码）
├── GatewayState       — 状态持久化到 AppData `state/latest.json`（legacy cwd 双读）
├── aiohttp REST Server  — OpenAPI CRUD + Web UI chat
├── spa/               — Vue 3 SPA 前端项目 (Vite + SFC)
├── GatewayWebView     — 原生 webview 窗口 (pywebview)
├── GatewayTray        — 系统托盘图标 (pystray)
└── _openapi.py       — OpenAPI schema 提供
```

## 模块

| 文件 | 职责 |
|------|------|
| `__init__.py` | `Gateway` dataclass + `run()` 入口 |
| `_manager.py` | 共享 helpers（_new_uuid/_noop/_socket_path/_ensure_socket_dir/_remove_socket/_wait_socket） |
| `_ai_manager.py` | `AIManager` — AI 实例注册表 + 生命周期 + AiInfo |
| `_router_manager.py` | `RouterManager` — Router 实例注册表、类型化 AI/Router 依赖解析和生命周期管理 |
| `_session_manager.py` | `SessionManager` — Session 实例注册表 + 生命周期 + SessionInfo（含 `agent`、`active_schedules` / `deactive_schedules`） |
| `_scheduler_manager.py` | `SchedulerManager` — 每个 workspace 恰好一个**全量激活**（`active_schedules=("*",)`）的调度 Session，按需 spawn，对 SPA / state 隐藏 |
| `_defaults.py` | `resolve_default_agent` / `resolve_default_workspace`；再导出 ``psi_agent._appdata`` 路径助手 — CLI / `GET /defaults` 用 |
| `_feishu_manager.py` | `FeishuManager` — 飞书会话 → Session 路由表（私聊按 `open_id`、群聊按 `chat_id`；复用 SessionManager 按需 spawn）+ FeishuRoute |
| `_oauth_manager.py` | `OAuthRelay` — OAuth 回调中继（`state → code` 一次性信箱，带 TTL；供 `GET /oauth/callback` + `GET /oauth/code`），让授权码免用户手工复制 |
| `_title_manager.py` | 会话标题 CRUD + AI 自动生成 |
| `_summary_manager.py` | 任务摘要 CRUD + AI 自动生成（spa-v2；与 title 同级持久化） |
| `_state.py` | `GatewayState` — `{appdata}/state/latest.json` + 时间戳快照；缺则双读 cwd `state/latest.json` |
| `_spa_shell.py` | SPA 外壳注入 — `DEFAULT_APP_NAME`、`inject_app_name()`、`read_spa_index_template()`；`GET /spa/index.html` 替换 `__GATEWAY_APP_NAME__` |
| `server.py` | aiohttp Application + REST handlers |
| `_chat_manager.py` | SSE 流式对话管理（复用 ChannelCore） |
| `_history_manager.py` | JSONL 历史读取（``{appdata}/histories/{session_id}.jsonl``，legacy ``{workspace}/histories/`` 双读；delete 两侧都清） |
| `_todo_manager.py` | 会话 todo 列表读取（``{appdata}/todos/{session_id}.json``，legacy ``{workspace}/.psi/todos/`` 双读） |
| `_workspace_manager.py` | 目录浏览 + 快捷路径列表 + cwd 查询 |
| `spa/` | Vue 3 SPA v1（对话气泡），构建输出 `spa/dist/`；路径 `/spa/` |
| `spa-v2/` | React SPA v2（任务工作台 + 宝箱），构建输出 `spa-v2/dist/`；**默认** `GET /` → `/spa-v2/`（无 dist 时回退 v1） |
| `_tray.py` | 系统托盘图标（pystray + Pillow），由 `--tray` 参数开启，`--icon` 参数指定图标文件，左键打开浏览器或恢复 webview 窗口，右键菜单控制；`request_attention()` 脉冲高亮图标 |
| `_webview.py` | 原生 webview 窗口（pywebview），`--webview` 参数开启。窗口关闭信号通过 `threading.Event` 传递给主 loop；`request_attention()` 在 Windows 上 FlashWindowEx |
| `_attention.py` | `AttentionHub`：SPA `POST /ui/attention` → 绑定的 tray/webview 注意力提示（best-effort）。`schedule_notify()` 用 daemon thread 异步触发，**禁止**在 aiohttp handler 里同步等 tray（pystray 可能卡死事件循环） |
| `_openapi.py` | `GET /openapi.json` schema 生成 |

## Gateway 启动流程

```
1. setup_logging(verbose)                             — 第一行
2. if self.browser and self.webview: raise ValueError  — 互斥校验
3. resolve default_agent / default_workspace（见 `_defaults.py`）
4. state = GatewayState.from_appdata(appdata_root) + snapshot = await state.load()  — AppData state（legacy 双读）
5. anyio.create_task_group()                          — 手动管理 task group
6. 创建 AIManager + RouterManager + SessionManager（注入 `_default_agent` / `_default_workspace`）+ TitleManager + SummaryManager
7. 恢复 AI / Router / Session（Session 恢复时带 `agent`，缺省用 Gateway default）/ titles / summaries
8. 创建 SchedulerManager（`--scheduler-ai-id`，空则回落 `--feishu-ai-id`）
9. await create_app(..., default_agent=..., default_workspace=..., appdata=..., schedm=...)  — 注册 REST（含 `GET /defaults`）
10. 为每个已恢复 Session 的 workspace `schedm.ensure(...)` — 按需拉起调度 Session（无 `schedules/` 则跳过）
11. 创建 _do_persist 闭包（快照 managers → state.save，sessions 含 `agent`；`list_all()` 默认已排除调度 Session）
12. 注入 _persist + 初始全量持久化
13. runner.setup() + create_site + site.start() + tray/webview/browser 等待与 finally 清理
```

## 默认 agent / workspace / AppData（三区路径；记忆区搬家已完成）

### 路径分层（看 PR 先看这段）

```text
调用方（spa / 飞书 / haitun sessions_create / …）
    │  GET /defaults  → 得知默认 agent、workspace、appdata
    │  POST /sessions { workspace?, agent? }
    ▼
Gateway SessionManager（缺省补 --default-agent / --default-workspace；注入 _appdata）
    │  Session(workspace=…, agent=…, appdata=…)
    ▼
Session（#472 / 第 4C）
    │  启动时：tools / system 从 agent_path 加载
    │         schedules 从 workspace_path 加载（每个 Session 都读到，但只触发激活名单里的）
    │         history 写 `{appdata}/histories/`（legacy 双读）
    │  回合内：runtime_scope 写入 get_agent()/get_workspace() ContextVar
    ▼
workspace 工具（haitun `_runtime_paths`）按 ContextVar 解析相对路径  ← ✅ 第 3 步
AppData 记忆区根（`--appdata` / `PSI_APPDATA` / platformdirs）     ← ✅ 第 4A
todos → `{appdata}/todos/`（双读旧 `{workspace}/.psi/todos/`）   ← ✅ 第 4B
history → `{appdata}/histories/`（双读旧 `{workspace}/histories/`） ← ✅ 第 4C
Gateway state → `{appdata}/state/`（双读旧 cwd `state/`）          ← ✅ 第 4D
schedules → `{workspace}/schedules/`（归 workspace，非 agent 包 / 非 AppData）
```

路径助手：``psi_agent._appdata``（Session / Gateway / haitun 共用；**刻意**放在 gateway 包外以免循环导入）。``gateway._defaults`` 再导出同名助手。Gateway 启动把解析后的根写入 ``PSI_APPDATA``，**同进程**工具与 ``GET /defaults.appdata`` 一致。**注意这个「同进程」是硬限制**：``os.environ`` 只对本进程及其之后 fork 的子进程有效，而飞书 channel 通常是**兄弟进程**（各自 `psi-agent gateway` / `psi-agent channel feishu`），继承不到这个 env。因此需要共享 AppData 的兄弟进程必须**要么**由启动脚本给**每一个**进程都传 `--appdata`/设 `PSI_APPDATA`，**要么**像 channel 那样经 ``GET /defaults`` 现问（见 `channel/AGENTS.md`「AppData 根向 Gateway 现问」）——`GET /defaults` 由此不只服务「建 Session 的调用方」，也是**跨进程 AppData 根的唯一权威**。**禁止**把 AppData 根塞进 Session ContextVar。

| 已合 | 内容 |
|------|------|
| ✅ #472 | Session 可选 `agent`；加载能力包；ContextVar **API** |
| ✅ #482 | Gateway CLI + `GET /defaults` + `POST /sessions.agent`；调用方接线 |
| ✅ 第 3 步 | haitun 工具读 `get_workspace()` / `get_agent()`（`_runtime_paths`） |
| ✅ 第 4A | 解析并暴露 AppData 根：`GET /defaults.appdata`、CLI `--appdata`、env `PSI_APPDATA` |
| ✅ 第 4B | todos：**写** `{appdata}/todos/{session_id}.json`；**读**优先 AppData，缺则双读 legacy |
| ✅ 第 4C | history：**写** `{appdata}/histories/{session_id}.jsonl`；**读**优先 AppData，缺则双读 legacy |
| ✅ 第 4D | Gateway state：**写** `{appdata}/state/latest.json`；**读**优先 AppData，缺则双读 cwd `state/latest.json` |

**可读验收**：新 todos/history/state 落在 AppData；仅有 legacy 文件时仍可读；再次写入落 AppData。三区路径（agent / workspace / AppData）记忆区侧至此完成。

| CLI | 含义 |
|-----|------|
| `--default-agent` | 新建 Session 的 Agent 包目录；空则软默认：① `cwd/examples/haitun-workspace`（仓库开发）；② cwd 自身含 `tools/`+`skills/`（Inno 安装布局 `{app}` 即能力包）；仍空则 Session `agent=""`（与 workspace 同根兼容）。Windows 安装包 `haitun.exe` **显式**传 `--default-agent {app}` |
| `--default-workspace` | 新建 Session / `GET /defaults` 的用户工作区；空 → 软默认 `{Desktop}/haitun交付`（**只宣布路径**；目录在 `SessionManager.create` / 开始对话时才 mkdir。`platformdirs.user_desktop_dir`）。安装包 `haitun.exe` **显式**传该路径（运行时解析桌面，不写死用户名） |
| `--appdata` | AppData 记忆区根；空 → `PSI_APPDATA` → `platformdirs`（**禁止**手写死 `%AppData%`） |
| `--scheduler-ai-id` | 调度 Session 挂载的 AI 实例；空 → 回落 `--feishu-ai-id`；两者都空则有 `schedules/` 的 workspace 只记 warning 不启动调度 |

`POST /sessions` 可显式带 `agent` / `workspace`；省略时用上述默认。`SessionInfo` 与 `state/latest.json` 持久化含 `agent`。

**谁对接这套接口（调用方 = 谁 POST /sessions 或等价 spawn）**

| 调用方 | 怎么用 |
|--------|--------|
| **spa-v2** | `GET /defaults` 启动选工作区；`POST /sessions` 显式带 `agent` |
| **spa v1** | `POST /sessions` 带 `agent`（从 `/defaults`）；切换 backend 重建时保留 `agent` |
| **飞书** `POST /feishu/route` → `FeishuManager` → `SessionManager.create` | 不传 `agent` 时自动吃 Gateway `_default_agent` |
| **haitun** `sessions_create` / session 工具 | `GET /defaults` 后 `POST /sessions` 带 `agent` |
| **state 恢复** | snapshot 的 `agent`；缺省回落到 Gateway default |
| **OpenAPI / 其它客户端** | 同一 REST；可显式传或依赖服务端默认 |
| **调度 Session** | 不由外部调用方创建——`SchedulerManager.ensure()` 在上述任一调用方建会话后按 workspace 去重地 spawn（见下节）。`POST /sessions` 传 `active_schedules` / `deactive_schedules` / `scheduler` 无效，三者都不在 REST 入参里 |

## SchedulerManager（定时任务归 workspace，触发权归 session × schedule）

定时任务的正确归属是 **workspace**，而**触发权**的粒度是 **(session × schedule)**。Gateway 一个进程跑多个 Session，飞书更是按会话 spawn 独立 Session（私聊按 `open_id` 每人一个、群聊按 `chat_id` 每群一个）；每个 Session 都读得到 `{workspace}/schedules` 的全部条目，但一条 schedule 必须**恰好被一个 Session 激活**，否则一条提醒就会被在线会话数乘一遍。

`SchedulerManager` 负责那个「恰好一个」：`ensure(workspace)` 幂等地为一个 workspace 拿到/创建唯一的**全量激活**（`active_schedules=("*",)`）调度 Session，用户会话则一律传空名单。**「重复触发」在构造期就不存在**——不需要运行时抢锁，也没有「持有者退出后谁接管」的选主问题。

粒度是逐条而非整个 Session 一个布尔：布尔只能表达「全触发 / 全不触发」，表达不了「A 条归调度 Session、B 条归某个用户会话」。Gateway 默认用 `("*",)` 把整个 workspace 交给调度 Session，但 Session 层的名单机制允许更细的划分（见 `session/AGENTS.md`）。

| | |
|--|--|
| **去重键** | workspace 路径，经 `await anyio.Path(...).resolve()` + `os.path.normcase` 归一（Windows 大小写 / 斜杠差异不产出两个调度 Session）。不用 `os.path.realpath`——同步 IO，违反「一切异步」 |
| **session id** | `scheduler-<workspace sha256 前16位>`，确定性派生 → 重启后 `ensure` 重建同名，无需持久化 |
| **激活名单** | `active_schedules=("*",)`（`ACTIVATE_ALL`）——整个 workspace 的定时任务都归它，**含之后新建的**（枚举白名单覆盖不到 `refresh()` 新发现的条目）；用户会话为 `()`。要把某几条让给用户会话，用 `deactive_schedules=(名字…)` 从通配符里挖掉，别改成枚举 |
| **按需 spawn** | 仅当 workspace 真有 `schedules/*/TASK.md` 时才建。否则 N 个从不用定时任务的飞书用户 / 群会各挂一个空调度 Session（每个都付 tools 加载成本）。用户建第一个定时任务后，下一次 `ensure` 把它拉起来 |
| **之后新建的任务** | 由调度 Session 自己的 `_watch_dir` 协程每 30s `refresh()` 感知，**不**依赖再次 `ensure`（`ensure` 幂等命中缓存后直接返回，不会重载磁盘）。详见 `session/AGENTS.md`「动态重载」 |
| **谁调 `ensure`** | `POST /sessions`（建会话后）、`POST /feishu/route`（路由用户/群后）、`Gateway.run` 启动恢复 state 后 |
| **AI 实例** | `--scheduler-ai-id`，空则回落 `--feishu-ai-id`；两者都空时不 spawn（记 warning）——`fire=prompt` 需要 AI 后端，spawn 一个连不上上游的 Session 更糟 |
| **失败不扩散** | `ensure` 捕获全部异常，只记 warning 返回 `""`。调度起不来不该拖垮建会话 / 收消息的主链路 |
| **对 SPA / state 隐藏** | 见上方 `list_all(include_scheduler=False)` |

Session 侧的对应契约（逐条激活、未激活条目仍加载、display 结果不再回流用户）见 `session/AGENTS.md` 的「调度归属 workspace，触发权归属 (session × schedule)」。

## 系统托盘 (GatewayTray)

Gateway 启动时可通过 `--tray` 开启系统托盘，图标由 `--icon` 指定。`--tray` 未设置时不创建托盘；`--icon` 未设置时仅不提供 favicon，不影响其他功能。`--webview` 同样要求 `--icon`，用于设置 webview 窗口图标。

**交互**：
| 操作 | 行为 |
|------|------|
| 左键点击 | 打开浏览器或恢复 webview 窗口访问 Gateway 地址 |
| 右键 → "打开控制台" | 同上 |
| 右键 → "退出" | 关闭托盘并终止 Gateway 进程 |

**实现细节**：
- `GatewayTray` 在独立 daemon 线程中运行 pystray event loop
- 图标从用户指定的图片文件加载（`Image.open(icon_path)`），支持 png/jpg/ico 等 Pillow 支持的格式
- 有托盘时 `Gateway.run()` 使用 `anyio.to_thread.run_sync(tray.wait_stop, abandon_on_cancel=True)` 等待退出信号
- 有 webview 无托盘时 `Gateway.run()` 使用 `anyio.to_thread.run_sync(wv.wait_closed, abandon_on_cancel=True)`，窗口关闭即退出
- 无托盘无 webview 时 `Gateway.run()` 使用 `anyio.sleep_forever()`，通过外部 cancel 退出
- 托盘"退出"设置 `threading.Event`，主循环检测到后进入 `finally` 正常 shutdown
- 托盘启动失败（无桌面环境、图标文件无效等）不阻塞 Gateway 启动，仅记录 warning
- `self.browser` 参数（默认 False）：设为 True 时启动时自动打开一次浏览器，托盘提供后续手动"重新打开"
- `self.webview` 参数（默认 False）：设为 True 时替代 `--browser`，使用原生 webview 窗口展示 Web Console。与 `--browser` 互斥。必须同时指定 `--icon`（否则报错）。`--tray` 开启时关闭窗口仅隐藏到托盘（托盘左键可恢复）；否则关闭窗口即终止 Gateway
- **Favicon 复用托盘图标**：`--icon` 设置时，`create_app(..., favicon_path=self.icon)` 注册 `GET /favicon.ico`，用 `web.FileResponse` 直接返回该图标文件（content-type 由扩展名推断）。`--icon` 未设置时不注册该路由，浏览器请求 `/favicon.ico` 得 404（无 favicon）。SPA `index.html` 含 `<link rel="icon" href="/favicon.ico">`
- **应用名称 `app_name`**：`Gateway.app_name`（CLI `--app-name`，默认 `Haitun Agent`）经 `create_app(..., app_name=...)` 写入 `app["app_name"]`；`GET /spa/index.html` 在静态路由之前注入 `<title>`（占位符 `__GATEWAY_APP_NAME__`）。同源传给 `GatewayWebView` 窗口标题与 `GatewayTray` tooltip/菜单文案。与 Session 标题 API（`/titles`、`TitleManager`）无关。

## Socket 路径约定

AI 和 Session 之间通过 `_sockets.py` 抽象层以 Unix socket（仅 POSIX）/ Named Pipe（仅 Windows）通信。`_socket_path()` 的平台分支是**必须**的：`_sockets` 对平台与地址不匹配的组合主动抛 `ValueError`（Windows 上的裸路径、非 Windows 上的 `\\.\pipe\...`），详见根 `AGENTS.md`「关键注意事项」第 17 条。

```python
def _socket_path(prefix: str, kind: str, entity_id: str) -> str:
    if sys.platform == "win32":
        return rf"\\.\pipe\{prefix}\{kind}\{entity_id}"
    return f"/tmp/{prefix}/{kind}/{entity_id}.sock"
```

| 资源 | Linux | Windows |
|------|-------|---------|
| AI socket | `/tmp/{socket_path}/ais/{ai_id}.sock` | `\\.\pipe\{socket_path}\ais\{ai_id}` |
| Channel socket | `/tmp/{socket_path}/channels/{session_id}.sock` | `\\.\pipe\{socket_path}\channels\{session_id}` |

**测试里断言 socket 路径不能写死 `.sock`**：由上表可见 Windows 上路径没有该后缀。CI 三个 job 全是 `ubuntu-latest`，写死 `.sock` 在 CI 里永远绿、在每台 Windows 开发机上必然失败。测试请用平台判定（见 `tests/psi_agent/gateway/test_manager.py` 的 `_is_socket_path`）。

### `_wait_socket` 超时（120s，刻意为之）

`_wait_socket` 有 `timeout_sec` 上限（默认 `_SOCKET_READY_TIMEOUT_SECONDS = 120.0`），超时抛 `TimeoutError`，由 `create()` 捕获走 rollback。

这里有段反复：#79 最初是 30s → #248 显式移除、改为无限等待（`while True`）→ 现在加回 120s。**加回的理由**：无限等待时，一个永远起不来的服务会把调用方**永久挂住**——而调用方是 `AIManager.create()` / `SessionManager.create()`，它们又跑在 Gateway 的 REST 请求里，于是这条 HTTP 请求永不返回，`create()` 里那套 rollback（pop entry + cancel scope + remove socket + `_persist`）**一行都执行不到**，注册表停在半成品状态。有上限才能把「起不来」变成一个调用方能报告、能回滚的失败。

上限取 120s 而非 30s 是**刻意的**：#248 移除超时想解决的是慢机器上误杀正常启动（冷启动、Windows Defender 扫描、swap），120s 对此足够宽松；只有真正起不来时才触发。所以这不是简单 revert #248，而是**同时**满足两边：慢启动不误杀 + 死服务不挂死。`docs/superpowers/` 下的历史 spec/plan 已同步。

## AIManager

内存注册表，维护 `dict[str, _AiEntry]` + `anyio.Lock`。

每个 `_AiEntry` 包含：
- `scope: anyio.CancelScope` — 独立取消
- `info: AiInfo` — 包含 `id`、`socket`、`provider`、`model`、`api_key`、`base_url`、`max_context_tokens`

**`_persist` 回调**：构造函数参数，默认 no-op。Gateway.run() 在恢复完成后注入 persist 闭包（快照所有 manager → state.save），每次 create/delete/crash 后调用。

**create(provider, model, api_key, base_url, *, id="", max_context_tokens=-1) 流程**：
1. 获取 lock
2. 无显式 ``id`` 且已有 **完全相同** 配置（`provider`/`model`/`api_key`/`base_url`，base_url 忽略尾部 `/`）→ **直接返回已有** `AiInfo`，不新建实例（防模型池堆同款）。带显式 ``id``（如 Session 复活悬空 `ai_id`）时仍可再建一条同配置不同 id——spa-v2 模型池按配置指纹折叠展示。显式 `id` 已存在 → `ValueError`
3. `_socket_path(prefix, "ais", ai_id)` 生成 socket 路径
4. `_ensure_socket_dir(socket)` 创建父目录（anyio 异步）
5. 构造 `Ai(...)`（传入 api_key + base_url + `max_context_tokens`），创建 `CancelScope`，`task_group.start_soon`
   - `max_context_tokens` 是 compaction 阈值：`-1`（默认）保持 `Ai` 自身的解析
     （`PSI_MAX_CONTEXT_TOKENS`，否则 100K），`0` 表示禁用。**必须显式透传**——漏传会让
     该参数永远停在兜底值、Gateway 侧无法配置。阈值应显著小于模型真实上下文窗口，详见
     `ai/AGENTS.md`
   - 恢复路径（`Gateway.run()` 读 state 快照）用 `cfg.get("max_context_tokens", -1)`，
     故本字段出现之前写下的快照无需迁移
6. 存入 `_entries`
7. `_wait_socket(socket)` 轮询等待 socket 出现（默认 120s 上限，超时抛 `TimeoutError` 走 rollback）
8. 成功后调用 `_persist`，返回 `AiInfo`
   失败则 rollback：pop entry + cancel scope + remove socket + 调用 `_persist`

**delete(ai_id) 流程**：
1. 获取 lock，断言存在
2. `del _entries[ai_id]` + `entry.scope.cancel()`
3. `_remove_socket(entry.info.socket)` + 调用 `_persist`

**get_socket(ai_id)**：AI 在 `_entries` 中则返回其 socket 路径；不在则通过 `_socket_path()` 计算路径返回（不抛 LookupError）。这使 Session 创建可以在 AI 尚未启动时预计算 socket 路径，支持启动恢复场景。

AI 运行时 crash 时，`_run_ai` 的 except 块从 `_entries` 中移除该 entry 并调用 `_persist`，确保持久化状态与内存一致。

## RouterManager

Router 通过 `POST /routers` 单独启动。每个 upstream 使用
`backend_type + backend_id + description` 显式引用已启动的普通 AI 或已存在 Router；
`RouterManager` 在启动服务时分别通过 `AIManager.get_socket()` 或自身 `get_socket()`
解析地址，再调用 `psi_agent.router.Router`。Gateway 不重复实现选择、广播、回退或 SSE 代理。

`mode=routing` 时 `router_ai_id` 是 Selector，并允许同一 AI 同时作为候选；
`mode=aggregation` 时它是专用 Aggregator，禁止以 AI upstream 身份复用；
`mode=fallback` 时没有控制 AI，`router_ai_id` 与 `router_timeout` 均为 `None`。
Gateway 只允许引用创建时已存在的 Router，因此 UI/API 按叶到根构建依赖图；Router 不支持
原地修改依赖。删除前扫描所有活动 Router，仍被引用时抛 `RouterDependencyError`，REST 返回
HTTP 409，保证不会留下悬空依赖。

Gateway state 加载旧 Router upstream 时把 `ai_id + description` 单向迁移为
`backend_type="ai" + backend_id + description`，加载本身不覆写文件；下一次正常保存只写
规范格式。旧 `default_ai_id` 继续忽略，`max_context_length` 单向迁移为
`max_context_chars`。Routing/Aggregation-backed Session 的标题/摘要使用控制 AI；Fallback-backed
Session 没有控制 AI，改为调用 Fallback 自己的公开 Socket。状态恢复顺序固定为
AI → Router（按持久化顺序）→ Session；依赖缺失的 Router 记录 warning 并跳过。

## SessionManager

内存注册表，维护 `dict[str, _SessionEntry]` + `anyio.Lock`。

每个 `_SessionEntry` 包含：
- `scope: anyio.CancelScope` — 独立取消
- `info: SessionInfo` — 包含 `id`、`backend_type`、`backend_id`、`workspace`、`channel_socket`、`agent`、`active_schedules`（本会话实际触发的定时任务名，`("*",)` = 全部）、`deactive_schedules`（从中排除的，黑名单优先）

**`SessionInfo.scheduler`** 是由 `active_schedules` 派生的 property（`"*" in active_schedules`），只用于过滤与展示；真实归属信息在 `active_schedules` / `deactive_schedules` 本身。让出几条（非空黑名单）不改变它仍是该 workspace 调度 Session 的事实。

**`list_all(include_scheduler=False)`**：默认**不返回**全量调度 Session。因此 `GET /sessions` 与 `state/latest.json`（快照走 `list_all()`）都自动排除它——刻意为之：调度 Session 不是用户会话，列在 SPA 里只会让人误删。只激活部分条目的会话**仍是用户会话**，照常出现在列表里。内部去重 / 运维需要看到调度 Session 时传 `include_scheduler=True`。

`backend_type="ai"` 时通过 `AIManager` 解析 socket；`backend_type="router"` 时
通过 `RouterManager` 解析 socket。旧 REST 请求中的 `ai_id` 仍兼容为直接 AI
模式，响应也为直接 AI Session 保留 `ai_id` 字段，供 SPA 完成后续迁移。

**`_persist` 回调**：同 AIManager，默认 no-op，Gateway.run() 注入。

**create(ai_id, *, id="", workspace="") 流程**：
1. 解析 `workspace`（缺省用 Gateway `_default_workspace`）→ ``ensure_workspace_dir`` mkdir（**刻意为之**：`GET /defaults` 只宣布路径，目录到此才创建）
2. 获取 lock，断言不重复
3. `aimanager.get_socket(ai_id)` 查 AI socket（AI 不存在时计算路径返回，不抛异常——支持启动恢复时 AI 尚未就绪）
4. `_socket_path(prefix, "channels", session_id)` 生成 channel socket
5. `_ensure_socket_dir(socket)` 创建父目录
6. 构造 `Session(...)`，创建 `CancelScope`，`task_group.start_soon`
7. 存入 `_entries`
8. `_wait_socket()` 轮询等待 channel socket 就绪（默认 120s 上限，超时抛 `TimeoutError` 走 rollback）
9. 成功后调用 `_persist`，返回 `SessionInfo`
   失败则 rollback：pop entry + cancel scope + remove socket + 调用 `_persist`

**delete(session_id)**：
1. 获取 lock，断言存在
2. `del _entries[session_id]` + `entry.scope.cancel()`
3. `_remove_socket(entry.info.channel_socket)` + 调用 `_persist`

Session 运行时 crash 时，`_run_session` 的 except 块从 `_entries` 中移除该 entry 并调用 `_persist`。

REST ``DELETE /sessions/{id}`` 在 SessionManager.delete 之后还会：
- 删除 AppData 与 legacy workspace 下的 ``histories/{id}.jsonl``（``HistoryManager.delete``，文件不存在则忽略）
- 清除 ``TitleManager`` 中该会话标题
- 清除 ``SummaryManager`` 中该会话任务摘要

## TodoManager

只读：从 AppData（优先）或 legacy workspace 读取 Agent ``todo`` tool 写入的清单。

- **新路径**：``{appdata}/todos/{session_id}.json``（``appdata`` 来自 Gateway ``--appdata`` / ``PSI_APPDATA`` / platformdirs）
- **Legacy 双读**：``{workspace}/.psi/todos/{session_id}.json``（仅当 AppData 文件不存在）
- ``get(workspace, session_id, *, appdata="")`` → ``{todos: [{id, content, status}], summary: {…}}``
- 文件缺失 / JSON 损坏 → 空列表（不 404；路由层仅在 session 不存在时 404）
- spa-v2 任务卡中间步据此显示 ``N/M``（当前步/总数）

**子任务分段（``*.segments.json``）**：workspace ``todo`` 工具在写 live 清单时同步维护 ``{appdata}/todos/{session_id}.segments.json``。

| 写入 | 分段行为 |
|------|----------|
| ``merge=false`` | 关闭当前 open 段（快照为替换前 live），再开新段 |
| ``merge=true`` | 只更新 open 段的 ``todos`` 快照，不新增段 |

Gateway：``list_segments`` / ``get_segment`` 只读；``set_segment_label`` 允许 spa-v2 用回合摘要覆盖段标题（P1）。**刻意为之**：无 ``todo`` 写入则无分段——不以 user 消息切段。

**注意（有意为之）**：删除 AI **不会**级联删除依赖它的 Session。被删 AI 的 socket 失效后，挂在其上的 Session 仍存活但不可用——由前端负责不再访问这类失效 Session，后端不做级联清理。

## FeishuManager

「飞书会话 → Session」路由表，让同一飞书机器人对不同飞书**会话**提供**各自独立**的渠道。会话是**动态**的（事先不知道有哪些人、哪些群），故某个键首次路由时按需 spawn 一个 Session。本组件是 gateway 侧「飞书会话 → Session」的**唯一权威**——飞书 channel 只把 `open_id`/`chat_id`/`chat_type` 三个**客观事实**交给 Gateway 换 socket，既不自己挑路由键，也不决定 `ai_id`/`workspace`（对比早期把路由塞进 channel 内部调 `/sessions` 的做法）。

**路由键分两支（这是本组件的核心语义）**：

| 场景 | `chat_type` | 路由键 | session_id | workspace | 效果 |
|------|-------------|--------|-----------|-----------|------|
| 私聊 | `p2p` / 缺失 | 发送者 `open_id` | `feishu-<open_id>` | `<root>/<open_id>` | 一人一个，历史/记忆互相隔离 |
| 群聊 | `group` / `topic` | `chat:<chat_id>` | `feishu-chat-<chat_id>` | `<root>/chat-<chat_id>` | **整群共用一个**，机器人在群里对全体成员有连贯上下文 |

群聊按 `chat_id` 而非按发言者聚合，是因为群里的对话本身就是共享的：A 问完 B 追问「那第二点呢」，机器人必须看得见 A 那轮。要区分是谁在说话，靠 `_context_header` 每条消息注入的 `sender_open_id`（见 `channel/AGENTS.md`），不靠拆 session。群与群、群与私聊之间互不串味。

**字段**：
- `_sm: SessionManager` — 复用其 spawn/查询能力管理 Session 生命周期
- `_ai_id: str` — 飞书 Session 默认挂载的 AI 实例 id（`create_app(..., feishu_ai_id=...)` 注入，来自 `Gateway.feishu_ai_id`）
- `_workspace_root: str` — 各会话独立 workspace 的父目录（来自 `Gateway.feishu_workspace_root`；空则以 cwd 为父）
- `_routes: dict[str, str]` — 路由键 → session_id 映射（内存态）
- `_lock: anyio.Lock` — 首次路由才走，频率低，可接受串行

**派生规则**：
- 加 `feishu-` 前缀与 SPA 手建 session 命名空间隔离；`sanitize` 用正则 `[^A-Za-z0-9._-] → _`（飞书 id 本身即安全字符，此为防御层）
- 路由键加 `chat:` 前缀隔离两个命名空间（open_id 里不会有冒号）
- **私聊侧把 `-` 转义成 `_`（刻意为之，勿"简化"掉）**：`sanitize` 的白名单**允许** `-`，若不转义，某人 open_id 恰为 `chat-oc_x` 时派生出的 `feishu-chat-oc_x` 会与群 `oc_x` 的 session id **逐字节相同**——两个陌生人共享同一份上下文与 workspace，是隐私事故而非美观问题。`_session_id` 与 `_workspace_for` 两处必须同步转义，否则 session 分开了 workspace 还是同一个目录。飞书真实 open_id 不含 `-`，这纯属防御层
- **`chat_id` 为空时不按群路由（刻意为之）**：`_is_group` 要求 `chat_type in {group, topic}` **且** `chat_id` 非空，否则退回按 `open_id`。宁可这条消息不隔离，也不要建出 `feishu-chat-` 这种无主 session

**route(open_id, *, chat_id="", chat_type="", ai_id=None, workspace=None) → (channel_socket, session_id) 流程**（持 lock）：
1. `_route_key` 定键 → `_session_id` 派生 sid；键为空 → `raise ValueError`（群聊不要求 `open_id`，私聊要求）
2. 命中 `_routes` 且 `_sm.has(sid)` → 直接返回 `get_socket`
3. 否则 `_sm.has(sid)`（重启后 Session 被 state 恢复，或 SPA 侧同名建过）→ **adopt** 该 Session，写回 `_routes`
4. 否则 `mkdir(workspace)` + `_sm.create(ai_id=ai_id or _ai_id, id=sid, workspace=ws)`；捕获 `ValueError("already exists")` 竞态 → 回退 `get_socket`
5. `ai_id` 最终为空 → `raise ValueError`（handler 转 400）

**内存态自愈（有意为之）**：`_routes` 不持久化。因 session_id 由路由键确定性派生，Gateway 重启后 Session 经 state 恢复，下次 `route()` 走 adopt 分支自愈，无需额外持久化。

**list_routes() → list[FeishuRoute]**：`[{open_id, chat_id, session_id}]`，供观测（`GET /feishu/routes`）。群聊记录填 `chat_id` 而 `open_id` 留空，私聊反之——一条记录只有一个键有值。

**未定义（已知留白）**：群 Session 的 workspace 只有一份，而 `user_access_token`（UAT）按发送者 `open_id` 存。群里多人时「以谁的身份写文档」由 workspace 侧工具按每条消息的 `sender_open_id` 决定（见 `examples/haitun-workspace/TOOLS.md`），Gateway 不做约定。

## OAuthRelay

OAuth 回调中继（`_oauth_manager.py`）：让**授权码自己回到发起方**，免用户从地址栏手工复制 code。

**为什么在 Gateway**：授权码流程里第三方只把 `code` 拼在 `redirect_uri` 上跳一次浏览器；若没人监听那个地址，用户只能自己抄 code。Gateway 本就是 HTTP 服务且用户浏览器可达（配 `PSI_OAUTH_CALLBACK_BASE` 后连手机端也可达），是回调的天然落点——这也是飞书多用户部署唯一可行的一条通道（浏览器与 agent 不同机）。

**刻意不做的事**：Gateway 不碰 token 交换——不知道 app_secret、不知道 PKCE verifier、不知道是哪个飞书用户。那些都留在发起方（workspace 工具），中继只搬运一次性 code，故本模块**零持久化、无跨用户鉴权**（`state` 是发起方生成的高熵随机串，本身即取件码）。

**字段/行为**：
- `_pending: dict[str, _Pending]` — `state → {code, error, created_at}`，进程内存
- `deliver(state, *, code="", error="")` — 回调到达即挂到 `state` 名下；`state` 空 → `raise ValueError`
- `take(state) → _Pending | None` — 发起方取件，命中即返回并**删除**（一次性），未到达返回 `None`
- TTL 600s（飞书 code 本身 5 分钟有效），每次 `deliver`/`take` 顺带清理过期项；`_MAX_PENDING=256` 满则淘汰最旧一条，防内存无界增长

## TitleManager

内存存储 `dict[str, str]`（session_id → title），维护会话标题映射。

**字段**：
- `_titles: dict[str, str]` — 标题映射
- `_persist: Callable[[], Awaitable[None]]` — 状态持久化回调，默认 no-op，Gateway.run() 注入

**set(session_id, title)** — **async**，设置标题后调用 `_persist`。

**generate(session_id, ai_socket, user_text, assistant_text)** — 通过 AI 自动生成标题，成功后写入 `_titles` 并调用 `_persist`。返回生成的 title 字符串，失败返回 None。

## SummaryManager

与 TitleManager 对称：session_id → 任务摘要（1～2 句，spa-v2「任务摘要」/ 任务卡正文）。

- `GET/POST /summaries`、`POST /summaries/generate`（body 同 titles：`id` + `user_text` + `assistant_text`）
- 生成提示要求概括目标与进展，**禁止**复述原文大段、禁止 Markdown 符号
- 持久化进 AppData `state/latest.json` 的 `summaries` 数组；删除 Session 时一并清除

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/ais` | 创建 AI（201） |
| POST | `/routers` | 创建并启动 Router（201） |
| DELETE | `/routers/{router_id}` | 停止并删除 Router（200/404；仍被 Router 引用时 409） |
| GET | `/routers` | 列出所有 Router |
| DELETE | `/ais/{ai_id}` | 删除 AI（200/404） |
| GET | `/ais` | 列出所有 AI |
| POST | `/sessions` | 创建 Session（201）；可选 `agent` / `workspace`（缺省用 Gateway defaults） |
| DELETE | `/sessions/{session_id}` | 删除 Session + history JSONL + 标题（200/404） |
| GET | `/sessions` | 列出所有 Session（含 `agent`） |
| POST | `/sessions/{session_id}/chat` | Web UI chat（SSE） |
| GET | `/sessions/{session_id}/history` | 获取会话历史（AppData ``histories/`` 优先 + legacy 双读；``is_displayable_chat_message`` 白名单 + 剥 `[SEND:]`/`[RECV:]`；assistant 行另附 ``sends``；JSONL ``reasoning``（思考散文）透出供 SPA「已思考」。**刻意为之**：无正文的 tool_calls 轮不进气泡，但其 ``reasoning`` 折叠进下一（或上一）条可展示 assistant；结构化 ``tool_calls`` 另投影为 ``tools: [{name, arguments}]``（**不**塞进 ``reasoning``），SPA 单独渲染「已调用 N 个工具」） |
| GET | `/sessions/{session_id}/todos` | 读取 todos（AppData ``todos/{id}.json`` 优先，否则 legacy workspace ``.psi/todos``）；返回 ``{todos, summary}``，文件缺失则为空列表 |
| GET | `/sessions/{session_id}/todo-segments` | 子任务分段列表（``todos/{id}.segments.json``，新→旧）；``merge=false`` 开新段；返回 ``[{id,label,closed_at,summary,…}]`` |
| GET | `/sessions/{session_id}/todo-segments/{segment_id}` | 单段含 ``todos[]``（历史 checklist 回放） |
| POST | `/sessions/{session_id}/todo-segments/{segment_id}` | P1：改段标题 ``{label}``（spa-v2 可用回合 summary 覆盖） |
| POST | `/feishu/route` | 幂等路由一次飞书会话到其 Session（首次按需 spawn）`{open_id, chat_id?, chat_type?, ai_id?, workspace?}` → 201 `{open_id, chat_id, session_id, channel_socket}`。`chat_type` 为 `group`/`topic` 且 `chat_id` 非空 → 按 `chat_id` 整群共用一个 Session；否则按 `open_id` 一人一个。缺路由键（私聊无 open_id）/ 无 ai_id → 400 |
| GET | `/feishu/routes` | 列出所有飞书会话 → Session 路由 `[{open_id, chat_id, session_id}]`（群聊记录只有 `chat_id`，私聊只有 `open_id`） |
| GET | `/oauth/callback` | OAuth 重定向落地点：收下 `?code=&state=` 交给 `OAuthRelay` 暂存，回一张「授权成功」页；缺 state → 400。用户因此**不必**手工复制 code |
| GET | `/oauth/code` | 发起方（workspace 工具，通常在另一进程）按 `?state=` 取件，命中返回 `{state, code}` 并作废（一次性）；回调带错误则 `{state, error}`；未到达 → 404 |
| GET | `/defaults` | 默认 `agent` + `workspace` + `appdata`（建 Session 调用方可读；`appdata` 为记忆区根：todos / history / Gateway state） |
| GET | `/workspace/cwd` | Gateway 进程当前工作目录 |
| GET | `/workspace/places` | PathPicker 快捷位置（cwd / home / desktop / documents / downloads）+ 盘符 |
| GET | `/workspace/browse` | 浏览目录 `?path=...&kind=directory|file|all&q=...`，默认 `kind=directory` |
| GET | `/workspace/file` | 读取文件为 base64（`?path=...&root=...`）；``root`` 非空时路径须落在该目录下 |
| POST | `/workspace/reveal` | 在本机文件管理器中显示路径（Windows `explorer /select`；macOS `open -R`；Linux `xdg-open` 父目录）。body `{path}`；路径须已存在。供 spa-v2 交付物「在文件夹中显示」 |
| GET | `/titles` | 获取所有 session 标题 |
| POST | `/titles` | 设置 session 标题 `{id, title}` |
| POST | `/titles/generate` | AI 自动生成标题 `{id, user_text, assistant_text}` |
| GET | `/summaries` | 获取所有 session 任务摘要 |
| POST | `/summaries` | 设置任务摘要 `{id, summary}` |
| POST | `/summaries/generate` | AI 生成任务摘要 `{id, user_text, assistant_text}` |
| POST | `/ui/attention` | 会话在后台完成时闪烁托盘/webview（best-effort，需 `--tray` / `--webview`） |
| GET | `/openapi.json` | OpenAPI schema |
| GET | `/favicon.ico` | 托盘图标（仅当 `--icon` 设置时注册，返回该图标文件） |

AI 和 Session 的 `id` 字段可选，不传自动生成 UUID。

错误响应格式：`{"error": "message"}` + HTTP 状态码（404/400/500）。

**注意**：`GET /workspace/browse` 对 `path` 不加限制，可列举本机任意目录——这是 PathPicker 选 workspace 的预期功能。`GET /workspace/places` 返回快捷位置与盘符。

## Web UI Chat 协议

`POST /sessions/{session_id}/chat` 接受 `Chunk` 列表，返回 SSE 流。

**Request**：
```json
{
  "chunks": [
    {"type": "text", "text": "Hello, what's in this image?"}
  ]
}
```

**Response (SSE)**：
```
data: {"type": "reasoning", "text": "[Tool Call: read({…})]", "kind": "tool_call"}
data: {"type": "router_status", "version": 1, "trace_id": "...", "mode": "fallback", "phase": "attempting", "depth": 0, "attempt": 2, "total": 3}
data: {"type": "text", "text": "Hello! "}
data: {"type": "blob", "name": "generated.png", "data": "base64...", "path": "C:/Users/.../Downloads/.psi/.../generated.png"}
data: [DONE]
```

| `type` | 字段 | 说明 |
|--------|------|------|
| `text` | `text` | 助手正文（`TextChunk`） |
| `reasoning` | `text` + 可选 `kind` | 过程流（thinking / tool 进度仍走同一槽）；`kind` 为 `thinking` \| `tool_call` \| `tool_result`（Session yield 打标）。**≠** JSONL 消息 provenance 的 `kind`（`chat` / `schedule.*`） |
| `router_status` | `version`、`trace_id`、`mode`、`phase`、`depth` + 模式相关计数 | 已验证且面向 UI 安全的 Router 生命周期快照；瞬时事件，不写入 history |
| `blob` | `name` + `data` + 可选 `path` | 交付物 base64（`FileChunk`）；`path` 为磁盘绝对路径，供 spa-v2「在文件夹中显示」 |

**内部实现**：
- 查 `SessionManager.get_socket(session_id)` 获取 channel socket
- 复用 `channel._core.ChannelCore` 构造连接
- 输入：`TextChunk(text)`、blob（base64 解码后由 `_save_upload()` 落至 `~/Downloads/.psi/<date>/`，持久保留，转为 `FileChunk`）；multipart 文件上传通过 blob 通道走相同路径
- **落盘到用户真实家目录是刻意的**（交付物要持久保留、用户能在文件管理器里找到），**因此凡碰 `_save_upload` / blob 入站的测试都必须先重定向家目录**，否则会往开发者真实的 `~/Downloads/.psi/` 里堆测试垃圾。`_downloads_path` 走 `Path.home()`，而它在 Windows 上读 `USERPROFILE`、在 POSIX 上才读 `HOME`——`monkeypatch.setenv("HOME", ...)` 在 Windows 上**完全不生效**。正确做法是 patch 函数本身：`monkeypatch.setattr(Path, "home", lambda: tmp_path)`，见 `tests/psi_agent/gateway/test_chat_manager.py` 的 `fake_home` fixture 与 `tests/integration/test_gateway.py::test_gateway_blob_send`
- 输出：`TextChunk` → `{"type":"text"}`；`ReasoningChunk` → `{"type":"reasoning","text":…}`（有 `chunk.kind` 则附带）；`RouterStatusChunk` → `{"type":"router_status", ...status.to_dict()}`；`FileChunk` → 读盘 base64 → `{"type":"blob","name","data","path"}`

## Web Console (SPA)

Gateway 提供两套 Web 控制台：

| | `spa/`（v1） | `spa-v2/`（v2，默认） |
|--|--|--|
| 技术 | Vue 3 + Pinia | React 19 + Vite |
| 路由 | `/spa/` | `/spa-v2/` |
| 产品 | 会话气泡 | 任务卡 + 交付物宝箱 |

构建产物分别为 `spa/dist/`、`spa-v2/dist/`，由 Gateway 静态服务。**有 `spa-v2/dist` 时** `GET /` 重定向到 `/spa-v2/index.html`；否则回退 `/spa/index.html`。设计细节见各自目录下的 `AGENTS.md`。

**（踩坑）目录入口路由须先于 `add_static` 注册**：`GET /spa-v2/`、`GET /spa/` 的 redirect 必须写在 `add_static(..., show_index=False)` 之前。否则 aiohttp 先命中静态目录、禁止列目录 → 浏览器看到 `403: Forbidden`（`/spa-v2/index.html` 仍可能 200）。

CI 打包（PyInstaller / Nuitka）会分别 `npm ci && npm run build` 两个前端，并用 `--add-data` / `--include-data-dir` 同时打进 `spa/dist` 与 `spa-v2/dist`，安装包默认打开即为 v2。

### 技术栈（v1 概要）

| 资源 | 版本锁定 | 用途 |
|------|----------|------|
| Vue 3 | `npm` 包 | 响应式 UI 框架（Composition API `<script setup>`） |
| marked | `npm` 包 | Markdown 渲染 |
| KaTeX | `npm` 包 | LaTeX 数学公式渲染 |
| Material Symbols | `npm` 包（woff2 文件随 dist 分发） | UI 图标 |
| Vite 6 | `npm` devDependency | 构建工具 |

**无 CDN 依赖**：所有第三方库通过 `npm install` + Vite 打包进 JS/CSS bundle，Material Symbols woff2 字体文件随 `dist/` 分发。

### 项目结构

```
spa/
├── package.json / vite.config.js
├── index.html                     # Vite 入口
├── src/
│   ├── App.vue                    # 根组件（三栏布局 + 弹窗 + 遮罩）
│   ├── main.js                    # createApp + mount
│   ├── store.js                   # reactive() store，provide/inject
│   ├── utils.js                   # renderMd, htmlEscape, mimeType
│   ├── api.js                     # fetch 封装
│   ├── providers.js               # PROVIDERS 配置
│   ├── components/
│   │   ├── Sidebar.vue            # 会话列表 + 新建/双击改名/删除
│   │   ├── ChatArea.vue           # 消息列表 + 自动滚动 + 空状态
│   │   ├── MessageBubble.vue      # 单条消息气泡（Markdown + 复制按钮 + 文件附件）
│   │   ├── ThinkingBubble.vue     # 等待首 token 的脉冲动画
│   │   ├── InputBar.vue           # textarea + 文件上传 + 发送按钮
│   │   ├── ModelPanel.vue         # 模型管理浮层（自定义下拉替代原生 datalist）
│   │   ├── AiDialog.vue           # 链接大模型弹窗
│   │   ├── SessDialog.vue         # 创建会话弹窗（含 FileBrowser）
│   │   ├── FileBrowser.vue        # 目录浏览
│   │   ├── ConfirmDialog.vue      # 通用确认弹窗
│   │   └── Snackbar.vue           # MD3 toast 提示
│   ├── composables/
│   │   ├── useSSE.js              # SSE 流式读取
│   │   ├── useKeyboard.js         # visualViewport 键盘适配
│   │   └── useTheme.js            # 暗色/亮色切换
│   └── styles/
│       ├── tokens.css             # MD3 颜色/形状/elevation token
│       ├── components.css         # MD3 组件基类（按钮、输入框、弹窗）
│       └── layout.css             # 页面布局 + 响应式
└── dist/                          # `vite build` 输出 (gitignore)
```

### 数据流与响应式

```
用户输入 → sendMessage()
  → store.messages.push({role:'user', ...})
  → FormData → fetch POST /chat (SSE)
  → reader.read() 逐 chunk
  → asst.text += chunk.text → asst.html = renderMd(text)
  → await nextTick()  ← 触发 Vue 重渲染
  → saveHistory() → localStorage
  → generateTitle()  ← 首次对话后自动生成标题
```

**关键教训**：
- `addMessage()` 必须 return `this.messages[this.messages.length-1]`（reactive proxy），不能 return 原始 plain object。否则后续修改不触发 Vue 重渲染
- `nextTick` **必须 await**，否则 Vue 批处理未 flush 时 DOM 不会更新
- 用户手动上滚时暂停自动滚动（`userHasScrolledUp`），回到底部时恢复

### SSE 解析约定

```javascript
buf = buf.replace(/\r\n/g, '\n');  // 统一换行
while ((idx = buf.indexOf('\n')) >= 0) {
  const line = buf.slice(0, idx).trim();
  if (line.startsWith('data:')) {
    const p = line.slice(5).trim();
    if (p === '[DONE]' || !p) continue;
    try { /* JSON.parse */ } catch {
      if (!p.startsWith('{') && !p.startsWith('[')) /* 纯文本 fallback */
    }
  }
}
```

### 主题系统

MD3 暗色/亮色双主题，通过 `:root.light-mode` CSS 变量切换。默认亮色模式，主题偏好存 localStorage。

**调色关键**：
- 暗色模式 outline-variant：`rgba(255,255,255,0.08)` — 半透明替代实色，边框融入背景
- 亮色模式 outline-variant：`#c4c6d0` — 清晰可见但不过分
- 所有颜色必须引用 `var(--md-*)` 不写硬编码

### localStorage 维护

**持久化原则**：服务器是唯一数据源（AI/Session 列表从远端 GET），localStorage 仅保留 UI 状态和对话历史。不做客户端本地缓存镜像。

| Key | 内容 | 来源 |
|-----|------|------|
| `gw-active-ids` | 当前选中的 AI + Session ID | 客户端 UI 状态 |
| `gw-hist-<id>` | 每个 session 的对话历史（文件 blob 合并服务端文本） | 客户端缓存 |
| `gw-sidebar-state` | 侧边栏折叠状态 | 客户端 UI 状态 |
| `gw-theme` | 主题偏好 | 客户端 UI 状态 |

Session 标题由服务端 `/titles` 端点维护，不在浏览器 localStorage 存储。

**启动加载流程**：
```
GET /ais + GET /sessions → 恢复上次 AI/Session → 无 AI 时由 SPA 自行 POST /ais（打开即用，见 spa/AGENTS.md）
→ 仍无 AI 则弹窗 Hub「大模型」→ 恢复 titles / sidebar / theme / active IDs
```
Chat SSE 在长空闲时写 `: keepalive` 注释，**不得**对上游 `agen.__anext__()` 使用 `fail_after`（会拆掉 ChatManager，导致前端「正在同步」挂死）。打开即用默认模型 / 域名由 SPA 维护，Gateway 不内置默认 AI。

服务端通过 AppData `{appdata}/state/latest.json` 自动持久化 AI、Session、Title 状态（legacy cwd `state/` 双读），重启后自动恢复。对话历史经 AppData `histories/` JSONL 独立持久化。浏览器 localStorage 仅保留 UI 状态（active ids、sidebar 折叠、主题偏好）和对话历史缓存。

### 移动端键盘适配（visualViewport）

```javascript
window.visualViewport.addEventListener('resize', syncInputPosition);
window.visualViewport.addEventListener('scroll', syncInputPosition);
window.addEventListener('resize', syncInputPosition);  // 横竖屏切换
```

**同步更新元素**：`input-wrapper` bottom、`topbar` top、`messages` top + padding、`sidebar` top、`overlay` top。
桌面端清空所有动态内联样式。键盘弹起时自动滚底。

**关键 CSS**：
```html
<meta name="viewport" content="..., interactive-widget=resizes-visual">
```
```css
html { overscroll-behavior: none; }  /* 禁止下拉刷新/弹性滚动 */
```

### 移动端适配

```
桌面 (>768px)                 移动端 (≤768px)
┌─────────────────┐          ┌─────────────────┐
│ #sidebar        │          │ #mobile-topbar  │  ← position:fixed
│ (固定左栏)       │          │ (汉堡菜单 + 标题) │
│                 │          ├─────────────────┤
├─────────────────┤          │                 │
│ #chat           │          │ sidebar 变为     │
│ .sidebar-toggle  │          │ 抽屉 (slide-in)  │
│ .theme-toggle   │          │ from left        │
│                 │          ├─────────────────┤
│ #messages       │          │ #messages       │
│                 │          │ (padding 动态)   │
│ #input-area     │          │ #input-wrapper  │  ← bottom跟随键盘
└─────────────────┘          └─────────────────┘
```

**关键技术**：
- `100dvh` 替代 `100vh`：移动端浏览器地址栏会影响 `100vh`，`dvh` 动态跟随
- `window.visualViewport` API：监听软键盘弹出
- `@media (hover: none)`：触摸设备上删除/复制按钮始终可见
- 手机端 sidebar 改为 `position:fixed` + `translateX(-100%)` 抽屉式，汉堡菜单切换
- 桌面端的 `.sidebar-toggle-btn` / `.theme-toggle-btn` 在手机端 `display:none`，由 `#mobile-topbar` 替代

### 动态模型获取

AI 创建对话框支持从 provider 的 `/models` API 实时拉取可用模型列表，通过自定义 Vue 下拉组件（非原生 `<datalist>`，以解决跨浏览器行为不一致问题）。

```
填 API key + Base URL → fetch /models → 解析 response → fetchedModels → 自定义下拉列表
```

**注意**：不同 provider 的响应格式不同（`{data: [...]}` vs `{models: [...]}`），需同时处理。

### 模型管理 Panel

```html
.model-chip (点击展开) → .model-panel (浮层)
  ├── .model-panel-header (标题 + "链接新模型"按钮)
  └── .model-panel-item (v-for ais, 选中/删除)
```

**设计要点**：
- Chip 状态：`.open` class 触发箭头旋转 + 背景色变化
- 浮层点击外部关闭：`.model-panel-backdrop` (`position:fixed; inset:0; z-index:49`)
- 每个 model item 有 hover 删除按钮 + 选中 ✓ 标记
- 支持键盘导航（上下箭头 + Enter）和输入过滤

### Thinking 动画

```css
.thinking-bubble { /* 三个脉冲圆点，等待首 token 时显示 */ }
.thinking-dot  { animation: thinking-pulse 1.4s ease-in-out infinite; }
.thinking-dot:nth-child(2) { animation-delay: 0.2s; }
.thinking-dot:nth-child(3) { animation-delay: 0.4s; }
```

### 设计陷阱及纠正

1. **不要用 innerHTML 拼接 HTML** — 用 Vue 的 `v-for` + `v-model`
2. **不要用 `confirm()` / `alert()`** — 用自定义 dialog + snackbar 组件
3. **Session 改名 ≠ 修改 workspace** — workspace 是后端路径参数，改名只改前端 title 映射表
4. **AI 删除确认** — 可在模型管理面板中删除，需二次确认
5. **Vue `nextTick` 不 await 就不渲染** — SSE 流式不工作的头号根因
6. **`addMessage` 返回 reactive proxy** — `return this.messages[this.messages.length-1]` 而非原始 object
7. **移动端高度用 `100dvh`** — `100vh` 在 iOS Safari 地址栏收缩时不准确
8. **不要做 localStorage AI/Session 缓存镜像** — 服务端是唯一数据源。只存 UI 状态 + 对话历史
9. **`visualViewport` 同时监听 resize + scroll + window.resize** — 覆盖键盘弹出、滚动偏移、横竖屏切换三种场景
10. **`white-space: normal`** — 消息气泡内 `<p>` 用 `normal` 而非 `pre-wrap`，避免末尾多余空白行

## 设计约束

遵循 psi-agent 全局约束：

- `setup_logging` 第一行
- 零 `sys.exit`，错误用 `raise`
- 全部 anyio，禁止 `asyncio` / `pathlib` / `time.sleep`
- 所有 IO 操作使用 anyio 异步接口，禁止 `os.makedirs`、`os.unlink` 等同步文件操作。Socket 父目录创建使用 `await anyio.Path(...).mkdir(parents=True, exist_ok=True)`
- 零 noqa / per-file-ignores
- `from __future__ import annotations`
- `X | None` 非 `Optional[X]`
- 参数透传原则（chat endpoint 额外字段穿透到 ChannelCore→Session）
- 可取消：`finally` 清理所有 task scope + `tg.__aexit__()`（**先取消或清空常驻任务再退**，否则 `__aexit__(None, None, None)` 会等它们结束而永久阻塞；详见「测试策略 → 测试约定」）

## CLI 集成

```
psi-agent gateway [--listen http://127.0.0.1:PORT] [--socket-path psi] [--icon PATH] [--app-name NAME] [--browser/--no-browser] [--webview/--no-webview] [--tray/--no-tray] [--feishu-ai-id ID] [--feishu-workspace-root DIR] [--default-agent DIR] [--default-workspace DIR] [--appdata DIR] [--verbose]
```

默认 listen 为空，会自动绑定 127.0.0.1 随机高端口。`--browser` 开启自动打开浏览器。

`--icon PATH` 指定图标文件路径（png/jpg/ico 等）。设置后该图标会作为 Web Console 的 favicon（`GET /favicon.ico`）。

`--app-name NAME` 指定 Web 控制台显示名（浏览器标签、webview 窗口、托盘 tooltip/菜单）。默认 `Haitun Agent`；Gateway 在 `GET /spa/index.html` 时注入页面 `<title>`。

`--tray` 开启系统托盘图标，此时 **必须** 同时指定 `--icon`（否则报错）。托盘左键点击打开 Web Console，右键可退出 Gateway。托盘可用性与桌面环境有关，缺失时不阻塞启动。`--no-tray` 关闭托盘（默认）。仅设置 `--icon` 不开启 `--tray` 时，图标只用作 favicon。两者均不设置时不创建托盘，也不提供 favicon。

`--webview` 使用原生 pywebview 窗口展示 Web Console。与 `--browser` 互斥，两者同时设为 True 时报错。必须同时指定 `--icon`（否则报错）。关闭窗口行为取决于 `--tray`：有托盘时仅隐藏窗口，无托盘时退出 Gateway 进程。

`--feishu-ai-id` / `--feishu-workspace-root` 见上文 `FeishuManager`（私聊按 `open_id`、群聊按 `chat_id` 各建独立会话）。

### Windows 安装包 launcher（`haitun.exe`）

Inno 安装后 `{app}` **就是** haitun-workspace（`tools/` / `skills/` / `systems/` 在根下），不是仓库的 `examples/haitun-workspace` 嵌套布局。`.github/inno-setup/haitun.c` 编译的 `haitun.exe` 必须显式传：

```text
psi-agent.exe gateway --tray --browser --icon haitun.ico --verbose
  --default-agent "{app}"
  --default-workspace "{Desktop}/haitun交付"
```

`{app}` / 桌面路径在运行时解析（安装目录 + `SHGetFolderPath`），**禁止**写死本机用户路径。`--appdata` 可不传（软默认 `platformdirs`；**刻意为之**不显式传，安装包与 CLI 共用同一解析）。另：Gateway 软默认在 cwd 含 `tools/`+`skills/` 时也会把 cwd 当 agent（兜底直接跑 `psi-agent.exe`）。

`--feishu-ai-id ID` 指定飞书 Session（经 `POST /feishu/route` 按需 spawn）默认挂载的 AI 实例 id。未配时若请求也不带 `ai_id`，`/feishu/route` 返回 400。`--feishu-workspace-root DIR` 指定各飞书会话独立 workspace 的父目录（私聊每个 open_id 得 `<root>/<open_id>`，群聊每个 chat_id 得 `<root>/chat-<chat_id>`）；空则以 Gateway 进程 cwd 为父。两者均为飞书多会话独立渠道服务（配合飞书 channel 的 `--gateway-url`，见 `channel/AGENTS.md`）。

Gateway 不在 `_run.py` 的批量启动中。

## 测试策略

### 单元测试
- `AIManager` / `SessionManager` CRUD + 并发
- `_socket_path()` 跨平台路径生成
- 请求/响应类型序列化

### 集成测试
- Gateway process + Mock AI + 真实 Session + 最小 workspace
- 通过 REST API 驱动完整生命周期
- SSE 测试复用 `read_sse()` 工具

### 测试约定
- `@pytest.mark.anyio` 标记所有异步测试
- 集成测试使用 free port（预绑定 socket）避免端口冲突
- `anyio.create_task_group()` + `__aenter__`/`__aexit__` 手动管理 task 生命周期
- **退任务组前必须先取消或清空常驻任务，否则断言失败会退化成永久挂死**：manager 通过 `start_soon` 起的是常驻 server，永不自己返回。而 `await tg.__aexit__(None, None, None)` 传三个 `None` 即「正常退出」语义，anyio **不取消**子任务而是等它们结束 → 永久阻塞。于是测试体内**任何**异常都从「失败」变成「挂死」，连 traceback 都看不到（曾让 `test_manager.py` 在 Windows 上整个文件跑不完）。两种正确写法：
  - `tg.cancel_scope.cancel()` 再 `__aexit__`——见 `test_manager.py` 的 `_close()`，用例本身不关心优雅关闭时首选；
  - 显式 `delete()` 掉每个 spawn 出来的 Session/AI 再 `__aexit__`——见 `test_feishu_manager.py` 的 `_drain()` 与 `tests/integration/test_gateway.py`，用例要断言 delete 路径时用。
- Mock AI server 通过 fixture 提供
