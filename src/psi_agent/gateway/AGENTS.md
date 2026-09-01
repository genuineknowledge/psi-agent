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
├── GatewayState       — 状态持久化到 AppData `state/latest.json`（legacy cwd 双读）
├── aiohttp REST Server  — OpenAPI CRUD + Web UI chat
├── desktop/           — **ToC 专属层**（托盘、webview、登录、盘符、SPA 静态资源）
│   ├── _routes.py       — `register_desktop_routes()` + ToC 专属 handler（`/ui/*` `/workspace/*` `/auth/*`）
│   ├── GatewayWebView   — 原生 webview 窗口 (pywebview)
│   ├── GatewayTray      — 系统托盘图标 (pystray)
│   ├── AttentionHub / UIPrefs / WorkspaceManager / AuthManager + AuthStore
│   ├── spa/             — Vue 3 SPA v1 前端项目 (Vite + SFC)
│   └── spa-v2/          — React SPA v2（默认）
├── feishu/            — **ToB 专属层**（`FeishuManager` + `/feishu/*`，`OAuthRelay` + `/oauth/*`）
│   ├── _routes.py       — `register_feishu_routes()` + `/feishu/*`（`route` `routes` 免登 `auth/*` `app-id` `defaults` 按身份过滤的 `sessions`/`titles`/`summaries`） + `/oauth/callback` `/oauth/code`
│   ├── OAuthRelay       — OAuth 回调中继（state → code 一次性信箱，免用户手工复制授权码）
│   └── feishu-web/      — ToB 前端（Vite + React 19）；**当前只是脚手架，零业务**
└── _openapi*.py       — OpenAPI schema：公共片段在骨架，两份产品片段在各自子包，装配留骨架
```

**目录分三层**（A5 落位，A7 收口）：骨架层（本目录顶层）只有 `server.py` 骨架 + `_defaults` / `_state` / `_openapi*` 装配（**6 个 `.py`**）；`desktop/` 与 `feishu/` 各自持产品专属模块**以及各自的路由装配函数**（`desktop/_routes.py` / `feishu/_routes.py`）。判据是「这段代码认识哪些概念」而不是「当前谁在调用」——`WorkspaceManager` 认识 Windows 盘符、`_tray` 认识 pystray，飞书容器里一个都用不上。

**判据还有第二条：先问存在性。** `_oauth_manager` 是个反例——它 69 行里零飞书字样，按「认识哪些概念」该留骨架，但〔实测〕取件方全在 `agents/feishu/tools/` 一侧（`_oauth_receiver` / `_oauth_setup` / `feishu_auth` / `_feishu/auth`），ToC 那两处只是注释、零调用（ToC 登录走手机号 + 验证码，不经过 OAuth 跳转）。两条判据冲突时按存在性走：**当前只有一个消费者就跟着那个消费者放**，等第二条线真要用再往上提。

### 依赖方向

**单向：产品包 → 骨架。骨架不 import 任何产品包。** 产品包由 `register_*_routes()` 往 `create_core_app()` 产出的 app 上贴，反过来骨架对两条产品线一无所知；`desktop/_routes.py` 与 `feishu/_routes.py` 复用骨架的 `_json` / `_error` / `_read_json`，方向正是允许的那一向。这条边由判据命令守：

```bash
git grep -nE "^\s*(from|import)\s+.*gateway\.(desktop|feishu)" -- \
    src/psi_agent/gateway/server.py src/psi_agent/gateway/_defaults.py \
    src/psi_agent/gateway/_state.py \
    src/psi_agent/gateway/_openapi_core.py            # 必须无输出
```

A5 把模块文件搬进两个子包后，这条曾**不成立**：两个 `register_*_routes()` 还留在 `server.py`，于是骨架为了给它们备料反向 import 了 7 个产品符号（`AttentionHub` / `AuthManager` / `is_cloud_free_model` / `inject_app_name` / `UIPrefs` / `WorkspaceManager` + `FeishuManager`）。缺口不在纪律上而在文件归属上——装配函数放在骨架，骨架就**必须**认识两条产品线。A7 把两个函数连同各自专属 handler 搬进产品包收掉，唯一调用点 `__init__.py` 改从两个产品包 import。

**`_openapi.py` 是刻意的例外**（故不在上面判据的文件清单里）：它要同时认识三份 path 片段才能拼，放进任一产品包会让那个包被另一条线反向依赖，所以装配留骨架、`import` 两份产品片段。区别在于它只碰**数据**（dict 常量），不碰产品行为。

## 模块

> **AI / Session / Router 三类实例的注册表与生命周期已搬到 `psi_agent.runtime`**（`_manager` / `_ai_manager` / `_router_manager` / `_session_manager` / `_scheduler_manager` / `_title_manager` / `_summary_manager` / `_chat_manager` / `_history_manager` / `_todo_manager` 共 10 个文件）。它们只认识内核，不认识网页界面 / 飞书 / 托盘 / 登录，因此不该和这些东西装在同一个包里。清单见 `runtime/AGENTS.md`；本文档余下各小节仍是这些 manager **行为细节与 REST 侧接线**的正本——拆开会让两边都读不完整。依赖方向单一：gateway → runtime，由 `git grep -n "from psi_agent.gateway" -- src/psi_agent/runtime/` 必须无输出来守。

| 文件 | 职责 |
|------|------|
| `__init__.py` | `Gateway` dataclass + `run()` 入口 |
| `_defaults.py` | **只持 ToC 品牌字面量**（`haitun交付` / `agents/feishu` / 短名搜索目录 `agents`）+ 两个薄包装 `resolve_default_agent` / `resolve_default_workspace` — CLI / `GET /defaults` 用；机制在包外 ``psi_agent._workspace_paths``（见下方[工作区路径的机制与字面量分家](#工作区路径的机制与字面量分家)）。再导出 ``psi_agent._appdata`` 路径助手与 ``ensure_workspace_dir``（老调用方不破） |
| `feishu/_feishu_manager.py` | `FeishuManager` — 飞书会话 → Session 路由表（私聊按 `open_id`、群聊按 `chat_id`；复用 SessionManager 按需 spawn）+ FeishuRoute |
| `feishu/_oauth_manager.py` | `OAuthRelay` — OAuth 回调中继（`state → code` 一次性信箱，带 TTL；供 `GET /oauth/callback` + `GET /oauth/code`），让授权码免用户手工复制。**住 `feishu/` 是按存在性判据**：取件方〔实测〕全在 `agents/feishu/tools/` 一侧，ToC 登录走手机号 + 验证码不经过 OAuth 跳转（模块头有实测清单） |
| `desktop/_auth_manager.py` | `AuthManager` — 云端账号服务的**转发层** + 登录态持有者；不持供应商密钥、不做授权判定（发码与鉴权全在云端）。两段式注册的 `tempToken` 扣在进程内不下发给页面，改回 `registrationRequired: true`；把云端 `Retry-After` 响应头抄进 body 供倒计时用；云端 `GET /sessions` 回**裸数组**，`_call` 装 `items` 信封、`list_devices` 统一成 `{"devices": [...]}`。`resolve_endpoint()` 定地址（显式参数 > `PSI_AUTH_ENDPOINT` > 内置默认；显式空串=关闭）。`bearer_token()` 是 token 的**唯一进程内取值口**，只给免费模型换算力用，不接任何下行响应（见 [免费模型的 key 替换](#免费模型的-key-替换)）。连接池 / 预热 / 重试边界见 [AuthManager 连接复用](#authmanager-连接复用) |
| `desktop/_auth_store.py` | 本机凭证落盘 `{appdata}/auth.enc.json`（0600）+ `device_key`；密钥存 OS 钥匙串，钥匙串不可用则降级明文并记 warning、`credentialEncrypted: false` 如实上报。`load_token()` 读到明文且钥匙串此时可用会**就地重新加密**（用户装上 keyring 重启后凭证真的转密文，而不只是黄条消失）；`credentialEncrypted` 报的是**盘上真实形态**，没碰过盘时才退回“钥匙串可用性”做预测 |
| `_state.py` | `GatewayState` — `{appdata}/state/latest.json` + 时间戳快照；缺则双读 cwd `state/latest.json` |
| `desktop/_ui_prefs.py` | `UIPrefs` — SPA 的一次性 UI 标记（问卷是否已填），落 `{appdata}/ui-prefs.json`，读写经 `GET/POST /ui/prefs/survey`。**刻意不放 `localStorage`**：安装包不传 `--listen`，Gateway 每次启动 `_random_port()`，而 `localStorage` 按 origin（含端口）分桶 → 上次写的标记下次读不到，弹窗每次重启都再弹一遍。也不放 `_state.py`（那是 5 个固定 key 的 manager 快照 + 每启动一份时间戳副本，UI 偏好两头都不属于）；不像 `_auth_store` 加密（存布尔不存凭证）。按**机器**存不按登录用户：认证是旁挂且可整套关掉的，绑 `user_id` 会让纯本地模式无处落脚 |
| `desktop/_spa_shell.py` | SPA 外壳注入 — `DEFAULT_APP_NAME`、`inject_app_name()`、`read_spa_index_template()`；`GET /spa/index.html` 替换 `__GATEWAY_APP_NAME__` |
| `server.py` | aiohttp Application + **骨架**装配 `create_core_app()`（内核 manager + 两条线都要的路由，**不认识**飞书/托盘/盘符/登录）与核心 handler（`/ais` `/routers` `/sessions*` `/titles*` `/summaries*` `/defaults` `/openapi.json`，与 `_openapi_core.py` 的 path 集合同源）。旧的单个 `create_app()` 收 17 个参数并**无条件**建 `FeishuManager` 与 `WorkspaceManager` —— 桌面端容器里建飞书管理器、飞书容器里建 Windows 盘符枚举器 |
| `desktop/_routes.py` | **ToC 装配** `register_desktop_routes()` + ToC 专属 handler（SPA 外壳与静态、`/ui/*`、`/workspace/*`、`/auth/*` 与 `_refresh_free_models`）。A7 从 `server.py` 搬来：装配函数留在骨架里时，骨架必须反向 import 6 个本包符号 + 飞书 1 个，「骨架不认识产品线」只剩纪律没有结构 |
| `feishu/_routes.py` | **ToB 装配** `register_feishu_routes()` + `/feishu/*` 一族 handler（`route` `routes`、免登 `auth/login` `auth/me` `auth/logout`、`app-id`、`defaults`、按身份过滤的 `sessions`/`titles`/`summaries`）。A7 同上从 `server.py` 搬来。**免登三条必须带 `/feishu/` 前缀**：裸 `/auth/me` `/auth/logout` 被 `desktop/` 占着，同 app 重复注册不报错、先注册者胜出，占了就静默失效。`defaults` 只回 `{ai_id}`（= `--feishu-ai-id`）：网页应用与机器人共用一个模型的唯一来源，前端因此不碰 `/ais`。装配时还会检查 `PSI_FEISHU_DEV_OPEN_ID` 打一条**启动期** WARNING（页面上那条常驻通栏已撤） |
| `desktop/_workspace_manager.py` | 目录浏览 + 快捷路径列表 + cwd 查询 |
| `desktop/spa/` | Vue 3 SPA v1（对话气泡），构建输出 `spa/dist/`；路径 `/spa/` |
| `desktop/spa-v2/` | React SPA v2（任务工作台 + 宝箱），构建输出 `spa-v2/dist/`；**默认** `GET /` → `/spa-v2/`（无 dist 时回退 v1） |
| `feishu/feishu-web/` | ToB 前端（Vite + React 19），构建输出 `feishu-web/dist/`；路径 `/feishu-web/`。**A6 只落脚手架**：能构建 / 能起 dev server / 能连本机 gateway（页面里一次 `fetch('/defaults')` 就是连通性判据），页面是占位、零业务。登录、会话列表、对话收发由后续开发。`vite.config.ts` 的 `base` 与后端 `add_static` 前缀是同一字面量，改一边忘另一边会静默 404。详见 `feishu-web/AGENTS.md` |
| `desktop/_tray.py` | 系统托盘图标（pystray + Pillow），由 `--tray` 参数开启，`--icon` 参数指定图标文件，左键打开浏览器或恢复 webview 窗口，右键菜单控制；`request_attention()` 脉冲高亮图标 |
| `desktop/_webview.py` | 原生 webview 窗口（pywebview），`--webview` 参数开启。窗口关闭信号通过 `threading.Event` 传递给主 loop；`request_attention()` 在 Windows 上 FlashWindowEx |
| `desktop/_attention.py` | `AttentionHub`：SPA `POST /ui/attention` → 绑定的 tray/webview 注意力提示（best-effort）。`schedule_notify()` 用 daemon thread 异步触发，**禁止**在 aiohttp handler 里同步等 tray（pystray 可能卡死事件循环） |
| `_openapi.py` | `GET /openapi.json` schema 装配 — `build_openapi_spec(desktop=, feishu=, oauth=)` 把下面四份片段按开关拼起来；`OPENAPI_SPEC` 是「全都要」的那份（path key 集合与拆分前一致）。**按 path key 分份、不按当前谁在调用**：路由注册分开后各线只贴自己那份。`render_openapi(...)` 由 handler 按 `app["openapi_desktop"]` / `app["openapi_feishu"]` / `app["openapi_oauth"]` 三面旗子传参（旗子由各 `register_*_routes` 立），所以 spec 报的就是本进程真注册了的那批 path |
| `_openapi_core.py` | 两条线都注册的 16 个 path（`/ais` `/routers` `/sessions*` `/titles*` `/summaries*` `/defaults`）+ 公共 schema 与 `responses.Error`。`/oauth/*` 曾在这里，已随 `OAuthRelay` 挪去 `feishu/_openapi.py` |
| `desktop/_openapi.py` | ToC 专属 6 个 path（`/ui/attention` `/ui/prefs/survey` `/workspace/*`）；背后 `AttentionHub` / `UIPrefs` / `WorkspaceManager` 都认识桌面概念。无专属 schema |
| `feishu/_openapi.py` | ToB 专属 `FEISHU_PATHS` 2 个 path（`/feishu/route` `/feishu/routes`）+ 三个 `FeishuRoute*` schema；另有 `OAUTH_PATHS` 2 个（`/oauth/callback` `/oauth/code`）——代码归本包但**与挂了哪些 gateway 正交**，由独立开关控制，每种 `--gateway` 组合都报（路由侧同理，见 [OAuthRelay](#oauthrelay)） |

## Gateway 启动流程

```
1. setup_logging(verbose)                             — 第一行
2. if self.browser and self.webview: raise ValueError  — 互斥校验
3. resolve default_agent / default_workspace（见 `_defaults.py`）
4. state = GatewayState.from_appdata(appdata_root) + snapshot = await state.load()  — AppData state（legacy 双读）
5. anyio.create_task_group()                          — 手动管理 task group
6. 创建 AIManager + RouterManager + SessionManager（注入 `_default_agent` / `_default_workspace`）+ TitleManager + SummaryManager
6b. 创建 AuthManager（地址非空时；从 `{appdata}/auth.enc.json` 恢复登录态）— **旁挂**：不注入 Session、不写 ContextVar、不进 `_do_persist` 快照（凭证不落 `state/latest.json`）。随后把 `aim._resolve_key` 接上 `make_key_resolver(...)`，再 `nudge_warm()` 预热云端连接（注入 `tg`，详见 [AuthManager 连接复用](#authmanager-连接复用)）
   - **必须排在第 7 步之前**：交给 `Ai` 的 key 在 socket 构造时就定了，建晚了恢复出来的免费模型会带着哨兵起来，第一次对话必然 401（见 [免费模型的 key 替换](#免费模型的-key-替换)）
7. 恢复 AI / Router / Session（Session 恢复时带 `agent`，缺省用 Gateway default）/ titles / summaries
8. 创建 SchedulerManager（`--scheduler-ai-id`，空则回落 `--feishu-ai-id`）
9. await create_core_app(aim, sm, tm, rm=..., default_agent=..., default_workspace=..., appdata=..., schedm=...)  — 骨架（`server.py`）：内核 manager + 两条线都要的路由（含 `GET /defaults`）
9b. await register_desktop_routes(app, favicon_path=..., app_name=..., attention=..., authm=...)  — ToC（`desktop/_routes.py`）：SPA 静态 + `/ui/*` + `/workspace/*`（`authm` 非 None 才注册 `/auth/*`）。**仅当 `--gateway` 含 `desktop`**
9c. register_feishu_routes(app, feishu_ai_id=..., feishu_workspace_root=...)  — ToB（`feishu/_routes.py`）：`FeishuManager` + `/feishu/*` + `/feishu-web/`。**仅当 `--gateway` 含 `feishu`**；只挂 ToB 时另注册 `GET /` → 302 `/feishu-web/index.html`
9d. register_oauth_routes(app) — `/oauth/callback` + `/oauth/code`。**与挂了哪些 gateway 正交，每种组合都贴**：含 `feishu` 时由 9c 内部调用，只挂 ToC 时由 `Gateway.run` 自己调，恰好一处调到
   - **`--gateway` 必填，没有默认值**：挂哪些面是部署方的决定，内核不替它猜。少挂一面不报错，只是某个前端 404，排查方向完全跑偏——必填把这个静默失败提前成启动期的显式失败（不传即非 0 退出）。各调用方都得显式写：装机版 `--gateway desktop`（`.github/inno-setup/haitun.c`），云端 `launch-gateway.sh` `--gateway feishu`
   - `--gateway` 只决定挂哪些 HTTP 面；agent 包选哪个是**独立的一维**，走 `--default-agent`（见 [路径默认值](#路径默认值)），不另造一套。两维可自由组合，见 [gateway 与 agent 是两个独立维度](#gateway-与-agent-是两个独立维度)
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

路径助手：``psi_agent._appdata``（Session / Gateway / haitun 共用；**刻意**放在 gateway 包外以免循环导入）。``gateway._defaults`` 再导出同名助手（`_todo_manager` 已直接从 ``_appdata`` 取，再导出只为包外工具 `agents/feishu/tools/_todo_store.py:23` 留着）。Gateway 启动把解析后的根写入 ``PSI_APPDATA``，**同进程**工具与 ``GET /defaults.appdata`` 一致。**注意这个「同进程」是硬限制**：``os.environ`` 只对本进程及其之后 fork 的子进程有效，而飞书 channel 通常是**兄弟进程**（各自 `psi-agent gateway` / `psi-agent channel feishu`），继承不到这个 env。因此需要共享 AppData 的兄弟进程必须**要么**由启动脚本给**每一个**进程都传 `--appdata`/设 `PSI_APPDATA`，**要么**像 channel 那样经 ``GET /defaults`` 现问（见 `channel/AGENTS.md`「AppData 根向 Gateway 现问」）——`GET /defaults` 由此不只服务「建 Session 的调用方」，也是**跨进程 AppData 根的唯一权威**。**禁止**把 AppData 根塞进 Session ContextVar。

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
| `--default-agent` | 新建 Session 的 Agent 包目录。**非空**：先试值本身是目录，再试 `agents/<值>`（短名，如 `desktop`），都不是则**报错退出**并列出可选包名（详见下方[两形解析](#工作区路径的机制与字面量分家)）。**空**则软默认：① `cwd/agents/feishu`（仓库开发）；② cwd 自身含 `tools/`+`skills/`（Inno 安装布局 `{app}` 即能力包）；仍空则 Session `agent=""`（与 workspace 同根兼容）。Windows 安装包 `haitun.exe` **显式**传 `--default-agent {app}`（绝对路径，走第一档不变） |
| `--default-workspace` | 新建 Session / `GET /defaults` 的用户工作区；空 → 软默认 `{Desktop}/haitun交付`（**只宣布路径**；目录在 `SessionManager.create` / 开始对话时才 mkdir。`platformdirs.user_desktop_dir`）。安装包 `haitun.exe` **显式**传该路径（运行时解析桌面，不写死用户名） |
| `--appdata` | AppData 记忆区根；空 → `PSI_APPDATA` → `platformdirs`（**禁止**手写死 `%AppData%`） |
| `--scheduler-ai-id` | 调度 Session 挂载的 AI 实例；空 → 回落 `--feishu-ai-id`；两者都空则有 `schedules/` 的 workspace 只记 warning 不启动调度 |
| `--auth-endpoint` | 云端账号服务地址。**空 ≠ 关闭**：空则取内置默认（正式账号服务），装了包即能登录。要关掉整套认证（不创建 `AuthManager`、不注册 `/auth/*`、不读写本机凭证）须显式 `PSI_AUTH_ENDPOINT=""`。前缀另由 `PSI_AUTH_PREFIX` 覆盖（默认 `/auth`） |

`POST /sessions` 可显式带 `agent` / `workspace`；省略时用上述默认。`SessionInfo` 与 `state/latest.json` 持久化含 `agent`。

### 工作区路径的机制与字面量分家

`_defaults.py` 曾经既定义机制又写死品牌名，于是 `SessionManager`（只想 mkdir）不得不 `from psi_agent.gateway._defaults import ensure_workspace_dir` —— 一个**创建 Session 的 manager 反向依赖了产品线包**。现在拆成两层：

| 层 | 位置 | 内容 |
|----|------|------|
| 机制 | ``psi_agent/_workspace_paths.py``（**gateway 包外**） | `resolve_user_workspace(explicit, *, default_name)` / `ensure_workspace_dir(path)` / `resolve_agent_package(explicit, *, repo_candidate="", short_name_root="", label=...)`。桌面路径运算、mkdir、`tools/`+`skills/` 探测、短名查找 |
| 字面量 | `gateway/_defaults.py` | `DEFAULT_USER_WORKSPACE_NAME = "haitun交付"`、`DEFAULT_AGENT_REPO_CANDIDATE = "agents/feishu"`、`DEFAULT_AGENT_SHORT_NAME_ROOT`（从上一个**推导**，不写第二份 `agents`），以及两个薄包装 `resolve_default_workspace` / `resolve_default_agent` |

**`--default-agent` 的解析：非空值两形，解析不到就报错。** 先试值本身是目录（`/abs/path`、`agents/feishu` 两种老写法照旧），再试 `agents/<值>`（故 `--default-agent desktop` 选中 `agents/desktop`），都不是则 `FileNotFoundError` 退出并列出 `agents/` 下真实可选包名。顺序不能反：反了会让老写法在 `agents/` 下有同名目录时悄悄换地方。**空值仍是合法第三态**（回落 Session 自己的 workspace），走软默认链不报错。

原先第一档非空就 `.resolve()` 不查存在性，`--default-agent desktop` 静默指向 `{cwd}/desktop`；启动期根本不碰这个路径，日志干净、端口正常监听，错要等建 Session 才暴露成「这个 Session 没有 tools/skills」，排查方向完全跑偏。故每一档都**无条件 INFO 打印**解析结果与命中哪一档（根 `AGENTS.md` 坑 22 那半条教训：凡「两处必须一致」的路径，各方都应在启动时无条件打印自己的解析结果）。

判据是**这段代码认识谁**：`_workspace_paths` 不认识托盘、webview、Windows 盘符、桌面登录，也**不认识任何品牌名** —— 缺省文件夹名由调用方作为关键字参数传入，该模块自己没有缺省值。代价是多两个关键字参数；换来的是 workspace 改名只碰 `gateway/`。

**坑：monkeypatch 的目标跟着机制走，不跟着包装走。** 打 `platformdirs.user_desktop_dir` 必须打 ``psi_agent._workspace_paths.platformdirs.user_desktop_dir``；照旧打 `gateway._defaults.platformdirs` 会 `AttributeError`（那个模块已不 import platformdirs）。`tests/psi_agent/test_workspace_paths.py` 另有一条断言：中立模块正文（去掉模块 docstring）里不许出现 `haitun` / `交付` / `examples/`，防止品牌缺省值日后又漏回中立层。

对外兼容：`_defaults.py` 继续再导出 `ensure_workspace_dir`，`resolve_default_*` 签名与返回值不变，`GET /defaults`、CLI、包外工具的调用点一行都没改。

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
- **Favicon 复用托盘图标**：`--icon` 设置时，`register_desktop_routes(..., favicon_path=self.icon)` 注册 `GET /favicon.ico`，用 `web.FileResponse` 直接返回该图标文件（content-type 由扩展名推断）。`--icon` 未设置时不注册该路由，浏览器请求 `/favicon.ico` 得 404（无 favicon）。SPA `index.html` 含 `<link rel="icon" href="/favicon.ico">`
- **应用名称 `app_name`**：`Gateway.app_name`（CLI `--app-name`，默认 `Haitun Agent`）经 `register_desktop_routes(..., app_name=...)` 写入 `app["app_name"]`；`GET /spa/index.html` 在静态路由之前注入 `<title>`（占位符 `__GATEWAY_APP_NAME__`）。同源传给 `GatewayWebView` 窗口标题与 `GatewayTray` tooltip/菜单文案。与 Session 标题 API（`/titles`、`TitleManager`）无关。

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
5. `_spawn(info)`：构造 `Ai(...)`（传入 api_key + base_url + `max_context_tokens`），创建 `CancelScope`，`task_group.start_soon`。**key 解析只在这一处发生**（见下「免费模型的 key 替换」），`create` 与 `refresh_where` 共用，不会出现「新建时替换了、重建时忘了替换」
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

### 免费模型的 key 替换

实现在 `desktop/_free_model.py`。C 端默认免费模型走云端转发（`https://account.genuineknowledge.cn/llm/v1`），供应商 key 只在云端的 litellm 容器里，客户端凭**登录态**换算力。但 token 全程由 Gateway 持有并加密落盘，前端拿不到也不该拿（`authFlow.ts` 更要求登录组件源码不出现 token 字面量，理由是 XSS）。

所以 SPA 填哨兵值 `haitun-default`，Gateway 在 `_spawn` 里换成真 token：

| 关注点 | 做法 |
| --- | --- |
| 注入点 | `AIManager._resolve_key: Callable[[str, str], str]`，默认 `_key_as_is` 原样返回。`__init__.py` 在创建 `AuthManager` 后接上 `make_key_resolver(authm.bearer_token, authm.endpoint)` |
| 替换条件 | **两条同时成立**：`api_key` 是哨兵，且 `base_url` 与认证服务**同源**（scheme + host + port）。token 只能发给签发它的那台主机 —— 否则改一份 `state/latest.json` 就能把凭证送去任意域名 |
| token 去哪了 | **只活在 `Ai` 实例里**。`AiInfo.api_key` 仍是哨兵，所以不进 `state/latest.json`（那里 api_key 是明文）、不经 `/ais` 下发给 SPA。`test_free_model.py` 断言整个 `asdict(AiInfo)` 里不出现 token |
| 取值口 | `AuthManager.bearer_token()` 是唯一的进程内取值口，**不接任何下行响应**（不进 `status()`、不进 `/ais`、不进快照） |
| 未登录 | **仍然拉起 socket，key 为空**（不拉起的表现是模型列表少一项，更难懂）。但这条路的报错很难看：空 key **走不到云端**，any-llm 的 openai provider 在本地就抛 `No openai API key provided ... set the OPENAI_API_KEY environment variable`。所以未登录的真正兜底在前端 —— SPA v2 启动即**硬门禁**（`spa-v2` 的 `authGate`，登录窗关不掉），这一支只在门禁被绕过或认证关闭时走到 |
| 认证关闭 | `PSI_AUTH_ENDPOINT=""` 时不创建 `AuthManager`，`_resolve_key` 保持默认，一切原样透传 |

**`AuthManager` 必须建在恢复 AI 之前**（`__init__.py`）：交给 `Ai` 的 key 在 socket 构造时就定了，建晚了恢复出来的 socket 会带着哨兵起来，第一次对话必然 401。

**`refresh_where(predicate) -> list[str]`**：登录态变了要原地重建匹配的 socket。
- **为什么需要**：进 `_config_key` 去重键的是 `AiInfo.api_key`，而那里存的是哨兵 —— 去重键**看不见 token 变化**，换了登录态不会自然重建。不补这个机制的后果：换账号后仍拿已吊销的旧 token 请求，一路 401；登出后仍能继续用，更糟
- **原地重建，不是删了重加**：同一个 `AiInfo` 原样放回，模型列表 / Session 的 `backend_id` / 快照全都不动，用户看不到模型消失又出现。变的只有 `Ai` 手里那份 key
- **接线**（`server.py:_refresh_free_models`）：`_auth_verify` / `_auth_complete` 在 `status == 200` 时调；`_auth_logout` **无条件**调 —— `logout()` 即使云端不可达也会走 `logout_local()`，本机已经登出了

哨兵值 `haitun-default` 是**跨边界契约**，共三处：`desktop/_free_model.py`、`desktop/spa-v2/src/services/bootstrapAi.ts`、`desktop/spa/src/bootstrapAi.js`。任改一处就静默失效（带着哨兵去请求，云端回 401）。

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
1. 解析 `workspace`（缺省用 Gateway `_default_workspace`）→ ``psi_agent._workspace_paths.ensure_workspace_dir`` mkdir（**刻意为之**：`GET /defaults` 只宣布路径，目录到此才创建。取自 gateway 包外，本 manager 不反向依赖产品线包）
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
- `_ai_id: str` — 飞书 Session 默认挂载的 AI 实例 id（`register_feishu_routes(..., feishu_ai_id=...)` 注入，来自 `Gateway.feishu_ai_id`）。经只读属性 `default_ai_id` 对外暴露，`GET /feishu/defaults` 读的就是它 —— 网页应用与机器人的模型因此出自同一个字段
- `_workspace_root: str` — 各会话独立 workspace 的父目录（来自 `Gateway.feishu_workspace_root`；空则以 cwd 为父）
- `_routes: dict[str, str]` — 路由键 → session_id 映射（内存态）
- `_lock: anyio.Lock` — 首次路由才走，频率低，可接受串行

**派生规则**：
- 加 `feishu-` 前缀与 SPA 手建 session 命名空间隔离；`sanitize` 用正则 `[^A-Za-z0-9._-] → _`（飞书 id 本身即安全字符，此为防御层）
- 路由键加 `chat:` 前缀隔离两个命名空间（open_id 里不会有冒号）
- **私聊侧把 `-` 转义成 `_`（刻意为之，勿"简化"掉）**：`sanitize` 的白名单**允许** `-`，若不转义，某人 open_id 恰为 `chat-oc_x` 时派生出的 `feishu-chat-oc_x` 会与群 `oc_x` 的 session id **逐字节相同**——两个陌生人共享同一份上下文与 workspace，是隐私事故而非美观问题。`_session_id` 与 `_workspace_for` 两处必须同步转义，否则 session 分开了 workspace 还是同一个目录。飞书真实 open_id 不含 `-`，这纯属防御层
- **`chat_id` 为空时不按群路由（刻意为之）**：判定要求 `chat_type in {group, topic}` **且** `chat_id` 非空，否则退回按 `open_id`。宁可这条消息不隔离，也不要建出 `feishu-chat-` 这种无主 session。判定与 Channel 侧共用 `psi_agent/_feishu_routing.py` 的 `is_group_chat()` / `route_key()`（此前两侧各写一遍，判定漂移是隐私事故），本层只保留 `_sanitize_open_id` 的 `-` → `_` 转义——那只服务 session_id / workspace 派生，Channel 不派生这些

**route(open_id, *, chat_id="", chat_type="", ai_id=None, workspace=None) → (channel_socket, session_id) 流程**（持 lock）：
1. `route_key()`（共享模块）定键 → `_session_id` 派生 sid；键为空 → `raise ValueError`（群聊不要求 `open_id`，私聊要求）
2. 命中 `_routes` 且 `_sm.has(sid)` → 直接返回 `get_socket`
3. 否则 `_sm.has(sid)`（重启后 Session 被 state 恢复，或 SPA 侧同名建过）→ **adopt** 该 Session，写回 `_routes`
4. 否则 `mkdir(workspace)` + `_sm.create(ai_id=ai_id or _ai_id, id=sid, workspace=ws)`；捕获 `ValueError("already exists")` 竞态 → 回退 `get_socket`
5. `ai_id` 最终为空 → `raise ValueError`（handler 转 400）

**内存态自愈（有意为之）**：`_routes` 不持久化。因 session_id 由路由键确定性派生，Gateway 重启后 Session 经 state 恢复，下次 `route()` 走 adopt 分支自愈，无需额外持久化。

**list_routes() → list[FeishuRoute]**：`[{open_id, chat_id, session_id}]`，供观测（`GET /feishu/routes`）。群聊记录填 `chat_id` 而 `open_id` 留空，私聊反之——一条记录只有一个键有值。

**未定义（已知留白）**：群 Session 的 workspace 只有一份，而 `user_access_token`（UAT）按发送者 `open_id` 存。群里多人时「以谁的身份写文档」由 workspace 侧工具按每条消息的 `sender_open_id` 决定（见 `agents/feishu/TOOLS.md`），Gateway 不做约定。

## OAuthRelay

OAuth 回调中继（`feishu/_oauth_manager.py`，路由与 handler 在 `feishu/_routes.py`）：让**授权码自己回到发起方**，免用户从地址栏手工复制 code。

**为什么在 Gateway**：授权码流程里第三方只把 `code` 拼在 `redirect_uri` 上跳一次浏览器；若没人监听那个地址，用户只能自己抄 code。Gateway 本就是 HTTP 服务且用户浏览器可达（配 `PSI_OAUTH_CALLBACK_BASE` 后连手机端也可达），是回调的天然落点——这也是飞书多用户部署唯一可行的一条通道（浏览器与 agent 不同机）。

**为什么在 `feishu/` 而不是骨架层**：〔实测〕取件方全在 ToB 一侧（`agents/feishu/tools/` 下 `_oauth_receiver` / `_oauth_setup` / `feishu_auth` / `_feishu/auth`），`desktop/_auth_manager.py` 那两处只是注释、零调用——ToC 登录走手机号 + 验证码，不经过 OAuth 跳转。这段代码本身零飞书字样，所以按「认识哪些概念」会判它留骨架；两条判据冲突时按**先问存在性**走。

**归属（代码住哪）与可达性（哪个进程有这两条）是两件事。** 加 `--gateway` 之前这两件事被「唯一入口两面都贴」这个前提粘在一起；参数一加，那个前提就不成立了。现在由独立的 `register_oauth_routes()` 保证可达性——含 `feishu` 的组合由 `register_feishu_routes` 内部调，只挂 ToC 时由 `Gateway.run` 自己调，**每种组合恰好一次**。少这两条的表现是用户点完授权拿 404（回调地址登记在第三方应用后台，不随本进程挂了哪些 gateway 而变），不是某个功能没开。spec 侧同理自成 `OAUTH_PATHS`，挂在 feishu 开关上会出现「路由在、spec 里没有」的错报。

**刻意不做的事**：Gateway 不碰 token 交换——不知道 app_secret、不知道 PKCE verifier、不知道是哪个飞书用户。那些都留在发起方（workspace 工具），中继只搬运一次性 code，故本模块**零持久化、无跨用户鉴权**（`state` 是发起方生成的高熵随机串，本身即取件码）。

**字段/行为**：
- `_pending: dict[str, _Pending]` — `state → {code, error, created_at}`，进程内存
- `deliver(state, *, code="", error="")` — 回调到达即挂到 `state` 名下；`state` 空 → `raise ValueError`
- `take(state) → _Pending | None` — 发起方取件，命中即返回并**删除**（一次性），未到达返回 `None`
- TTL 600s（飞书 code 本身 5 分钟有效），每次 `deliver`/`take` 顺带清理过期项；`_MAX_PENDING=256` 满则淘汰最旧一条，防内存无界增长

## AuthManager 连接复用

云端账号服务在境外，RTT 约 210ms。登录要点 3–4 次，冷连接每次付 TCP 1 RTT + TLS 1 RTT + 请求 1 RTT。三处针对性设计（详见 `docs/superpowers/specs/2026-08-14-auth-connection-reuse-design.md`）：

**1. 连接池配置** —— `_ensure_session()` 显式传 connector，不吃 aiohttp 默认值：

| 值 | 本地 | aiohttp 默认 | 为什么默认值不行 |
|---|---|---|---|
| `keepalive_timeout` | 120s | 15s | 撑不过登录任一步的间隔：输手机号 5–20s、等短信 30–90s。默认值下每一步都是冷连接 —— 代码里复用 `self._session` 成立，网络层一次也没复用上 |
| `ttl_dns_cache` | 600s | 10s | 云端地址不变，没必要反复解析 |
| `ssl` | `psi_agent._tls.client_ssl_context()` | 系统默认组列表 | **默认组列表下部分网络根本连不上**：OpenSSL 3.5 默认带后量子混合密钥交换（X25519MLKEM768），ClientHello 撑过 ~1400 字节被分片，路径上有设备把分片握手包丢了。表现为「所有 `/auth/*` 全超时而 curl 秒回」（curl 走 Schannel、不发这个密钥份额）。详见 `psi_agent/_tls.py` |

120s 的上界由服务端空闲超时定（取值须更短，否则池里攒着对端已关的连接）。2026-08-14 实测空闲 10/30/60/90/120/180s **全部复用**，服务端超时比 180s 还长，故 120s 稳在安全侧、客户端先于服务端回收。也不再往上加：登录全程最大间隔约 90s，120s 已完整覆盖。

**不设 `enable_cleanup_closed`**：那是为老 SSL 实现「服务端关连接但客户端 transport 不自觉」的泄漏兜底，代价是常驻一个清理循环。本模块的复用已由 `ServerDisconnectedError` 重试兜住，不需要它。

**2. 连接预热** —— `nudge_warm()` 往 task group 里塞一个 `GET /me` 就返回，让首次点击也落在热连接上。两个触发点：

- Gateway 启动创建 AuthManager 后（`__init__.py`）
- `GET /auth/status`（`server.py`）—— SPA 挂载登录面板必然探这个端点，是最自然的预热时机，**前端一行都不用改**。该端点本身只读内存不打云端，加预热也不让它变慢

预热**不带 token**（`_call(..., auth=...)` 不传），因为它只为建立 TCP+TLS 连接，带上反而可能因 token 过期收 401 而误清本机凭证。节流 5s，防 SPA 连发。`_tg is None` 就不预热，功能不受影响；`_warm()` 用 `try/except/finally` 吞掉所有异常并复位 `_warming` —— 异常逃出 `start_soon` 会拆掉整个 task group 连带弄死 Gateway，不复位则一次失败永久堵死后续预热。

**3. 只有幂等 GET 能重试** —— `_call(retry=True)` 仅用于 `/me` 与 `/sessions`。四个业务 POST（send-code / verify / complete / bind）与 DELETE **一次都不重试**：验证码被消费两次，前端会在本该成功的时候显示「验证码不正确」；DELETE 重试撞 404 会告诉界面「设备不存在」，而第一次其实已经成功了。

重试只捕 `ServerDisconnectedError`，**不能扩到 `ClientOSError` / `ClientConnectionError`** —— `ServerDisconnectedError` 与 `ClientConnectorError`（DNS 失败、连接被拒）都是它们的子类，罩上去会把真正连不通的情况也重试一遍，白等一个超时周期。这条边界有专门的测试守着（`tests/psi_agent/gateway/test_auth_connection.py`）。

**效果**（2026-08-14 真机）：冷连接均值 814ms → 热连接 218ms，单次省 597ms。热连接与实测 RTT 226ms 相等，已贴到一个 RTT 的物理下限。

## TitleManager

内存存储 `dict[str, str]`（session_id → title），维护会话标题映射。

**字段**：
- `_titles: dict[str, str]` — 标题映射
- `_persist: Callable[[], Awaitable[None]]` — 状态持久化回调，默认 no-op，Gateway.run() 注入

**set(session_id, title)** — **async**，设置标题后调用 `_persist`。

**generate(session_id, ai_socket, user_text, assistant_text)** — 通过 AI 自动生成标题，成功后写入 `_titles` 并调用 `_persist`。返回生成的 title 字符串，失败返回 None。

`_title_manager` / `_summary_manager` 的 SSE `data:` 行解析用 `psi_agent.protocol.parse_sse_data()`。

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
| GET | `/sessions/{session_id}/history` | 获取会话历史（AppData ``histories/`` 优先 + legacy 双读；``is_displayable_chat_message`` 白名单 + 剥 `[SEND:]`/`[RECV:]`；assistant 行另附 ``sends``；JSONL ``reasoning``（思考散文）透出供 SPA「已思考」。**刻意为之**：无正文的 tool_calls 轮不进气泡，但其 ``reasoning`` 折叠进下一（或上一）条可展示 assistant；结构化 ``tool_calls`` 另投影为 ``tools: [{name, arguments}]``（**不**塞进 ``reasoning``），SPA 单独渲染「已调用 N 个工具」）（`is_displayable_chat_message` / `strip_transfer_markers` / `extract_send_paths` 等符号经 `psi_agent.session` 的公开导出取得，见 `session/AGENTS.md`「History 展示白名单」） |
| GET | `/sessions/{session_id}/todos` | 读取 todos（AppData ``todos/{id}.json`` 优先，否则 legacy workspace ``.psi/todos``）；返回 ``{todos, summary}``，文件缺失则为空列表 |
| GET | `/sessions/{session_id}/todo-segments` | 子任务分段列表（``todos/{id}.segments.json``，新→旧）；``merge=false`` 开新段；返回 ``[{id,label,closed_at,summary,…}]`` |
| GET | `/sessions/{session_id}/todo-segments/{segment_id}` | 单段含 ``todos[]``（历史 checklist 回放） |
| POST | `/sessions/{session_id}/todo-segments/{segment_id}` | P1：改段标题 ``{label}``（spa-v2 可用回合 summary 覆盖） |
| POST | `/feishu/route` | 幂等路由一次飞书会话到其 Session（首次按需 spawn）`{open_id, chat_id?, chat_type?, ai_id?, workspace?}` → 201 `{open_id, chat_id, session_id, channel_socket}`。`chat_type` 为 `group`/`topic` 且 `chat_id` 非空 → 按 `chat_id` 整群共用一个 Session；否则按 `open_id` 一人一个。缺路由键（私聊无 open_id）/ 无 ai_id → 400 |
| GET | `/feishu/routes` | 列出所有飞书会话 → Session 路由 `[{open_id, chat_id, session_id}]`（群聊记录只有 `chat_id`，私聊只有 `open_id`） |
| GET | `/oauth/callback` | OAuth 重定向落地点：收下 `?code=&state=` 交给 `OAuthRelay` 暂存，回一张「授权成功」页；缺 state → 400。用户因此**不必**手工复制 code |
| GET | `/oauth/code` | 发起方（workspace 工具，通常在另一进程）按 `?state=` 取件，命中返回 `{state, code}` 并作废（一次性）；回调带错误则 `{state, error}`；未到达 → 404 |
| GET | `/auth/status` | 登录态 + 链路自检信息 `{endpoint, prefix, loggedIn, deviceKey, platform, credentialEncrypted}`；**不含 token**。SPA 据此决定显示登录引导还是身份信息。顺带触发连接预热 —— 该端点只读内存不打云端，预热是后台任务，不拖慢本响应 |
| POST | `/auth/send-code` | 请云端发验证码 `{phone}` 或 `{email}`（二选一，缺则 400） |
| POST | `/auth/verify` | 校验验证码 `{code, phone?/email?}`。老用户当场登录；新用户回 `{registrationRequired: true, isNewUser: true}`，其 `tempToken` 由 Gateway 扣在进程内**不下发**。前端判 `registrationRequired` 决定是否进建号屏 —— 扣掉凭证就必须留这个替代信号，否则新用户被当成登录失败 |
| POST | `/auth/complete` | 两段式注册第二段 `{displayName?}`；`tempToken` 取自上一步暂存，用后即弃 |
| POST | `/auth/bind` | 已登录态绑定手机号/邮箱 `{code, phone?/email?}`；已归他人 → 409 `identity_taken` |
| DELETE | `/auth/identities/{provider}` | 解绑一种登录方式（`phone`/`email`）；解绑最后一个 → 409 `last_identity` |
| GET | `/auth/me` | 当前账号 + 已绑定的登录方式 |
| POST | `/auth/logout` | 撤销云端本会话并清本机凭证；云端不可达也清本机（否则点了登出仍显示已登录） |
| GET | `/auth/devices` | 已登录设备列表，统一为 `{"devices": [...]}`。上游 `GET /sessions` 回裸数组，须在 manager 侧归一化 —— 早先「非 dict 即坏响应」把整个列表吃掉，界面上设备数恒为 0 |
| DELETE | `/auth/devices/{device_id}` | 踢掉某台设备，该设备下次请求即 401 |
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
| GET | `/ui/prefs/survey` | 问卷弹窗是否已关闭过 → `{"done": bool}`（按机器，落 `{appdata}/ui-prefs.json`） |
| POST | `/ui/prefs/survey` | 记录问卷弹窗已关闭；body `{"done": bool}`，缺省/非 bool 视作 `true`（唯一调用方是"关闭"动作） |
| GET | `/openapi.json` | OpenAPI schema。只含本进程真注册了的那些面的片段（`--gateway desktop feishu` 两面都贴时与拆分前一致；单挂一面时另一面的 path 不再出现，`/oauth/*` 每种组合都在） |
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
data: {"type": "text", "text": "Hello! "}
data: {"type": "blob", "name": "generated.png", "data": "base64...", "path": "C:/Users/.../Downloads/.psi/.../generated.png"}
data: [DONE]
```

| `type` | 字段 | 说明 |
|--------|------|------|
| `text` | `text` | 助手正文（`TextChunk`） |
| `reasoning` | `text` + 可选 `kind` | 过程流（thinking / tool 进度仍走同一槽）；`kind` 为 `thinking` \| `tool_call` \| `tool_result`（Session yield 打标）。**≠** JSONL 消息 provenance 的 `kind`（`chat` / `schedule.*`） |
| `blob` | `name` + `data` + 可选 `path` | 交付物 base64（`FileChunk`）；`path` 为磁盘绝对路径，供 spa-v2「在文件夹中显示」 |

**内部实现**：
- 查 `SessionManager.get_socket(session_id)` 获取 channel socket
- 复用 `channel._core.ChannelCore` 构造连接
- 输入：`TextChunk(text)`、blob（base64 解码后由 `_save_upload()` 落至 `~/Downloads/.psi/<date>/`，持久保留，转为 `FileChunk`）；multipart 文件上传通过 blob 通道走相同路径
- **落盘到用户真实家目录是刻意的**（交付物要持久保留、用户能在文件管理器里找到），**因此凡碰 `_save_upload` / blob 入站的测试都必须先重定向家目录**，否则会往开发者真实的 `~/Downloads/.psi/` 里堆测试垃圾。`_downloads_path` 走 `Path.home()`，而它在 Windows 上读 `USERPROFILE`、在 POSIX 上才读 `HOME`——`monkeypatch.setenv("HOME", ...)` 在 Windows 上**完全不生效**。正确做法是 patch 函数本身：`monkeypatch.setattr(Path, "home", lambda: tmp_path)`，见 `tests/psi_agent/gateway/test_chat_manager.py` 的 `fake_home` fixture 与 `tests/integration/test_gateway.py::test_gateway_blob_send`
- 输出：`TextChunk` → `{"type":"text"}`；`ReasoningChunk` → `{"type":"reasoning","text":…}`（有 `chunk.kind` 则附带）；`FileChunk` → 读盘 base64 → `{"type":"blob","name","data","path"}`

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

**跨启动的 UI 标记也不能放 localStorage**（问卷弹窗踩过）：安装包拉起 Gateway 不带 `--listen`，端口每次 `_random_port()`，而 localStorage 按 origin（scheme+host+**port**）分桶，于是上次运行写的标记下次一律读不到。上表那几个 key 只是"读不到就回默认值"的 UI 状态，代价可以接受；凡是"一次性、之后不该再触发"的标记必须走服务端（见 `_ui_prefs.py`），否则表现为每次重启都重来一遍。

**启动加载流程**：
```
GET /ais + GET /sessions → 恢复上次 AI/Session → 无 AI 时由 SPA 自行 POST /ais（打开即用，见 desktop/spa/AGENTS.md）
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
psi-agent gateway --gateway {desktop,feishu} [{desktop,feishu} ...] [--listen http://127.0.0.1:PORT] [--socket-path psi] [--icon PATH] [--app-name NAME] [--browser/--no-browser] [--webview/--no-webview] [--tray/--no-tray] [--feishu-ai-id ID] [--feishu-workspace-root DIR] [--default-agent DIR] [--default-workspace DIR] [--appdata DIR] [--auth-endpoint URL] [--verbose]
```

默认 listen 为空，会自动绑定 127.0.0.1 随机高端口。`--browser` 开启自动打开浏览器。

`--listen` **必须带 scheme**〔实测〕：`_sockets.create_site()` 把非 `http://` 非 pipe 的地址当 Unix socket，Windows 上直接抛 `ValueError: Unix-socket transport is not supported on Windows`。`--listen 127.0.0.1:18080` 报这个错，`--listen http://127.0.0.1:18080` 才对。

### `--gateway` —— 挂哪些 gateway 的 HTTP 面

**可组合的列表，空格分隔**，取值来自 `ALL_GATEWAYS`（`gateway/__init__.py`）。tyro 渲染成 `--gateway [{desktop,feishu} [{desktop,feishu} ...]]`。

| 取值 | 挂载的面 | `GET /` |
| --- | --- | --- |
| `desktop feishu` | ToC（`/spa/` `/spa-v2/` `/ui/*` `/workspace/*` `/auth/*`）+ ToB（`/feishu/*` `/feishu-web/`） | ToC 降级链：spa-v2 → spa |
| `desktop` | 只 ToC。`/feishu/*` `/feishu-web/` 不注册（404） | 同上 |
| `feishu` | 只 ToB。`/spa*` `/ui/*` `/workspace/*` `/auth/*` 不注册（404） | 302 → `/feishu-web/index.html` |

开发时只写一个值单挂一面，省掉另一面的前端与 manager。值得记的几点：

- **必填，且不要加回默认值**：挂哪些面是**部署方的决定**，内核不替它猜。曾经默认全集 `desktop feishu`，那是个「看起来安全实则最危险」的默认——少挂一面不报错，只是某个前端 404，出问题时排查方向完全跑偏。必填把这个静默失败变成启动期的显式失败。判据在 `tests/psi_agent/gateway/test_gateway_selection.py`
- **为什么不按环境给默认值**（比如云上默认 `feishu`）：内核里**没有「产品线」这个概念**。要让内核默认 `feishu`，内核就得先知道自己跑在云上——那等于把产品线概念从参数名里赶出去，又从环境判断偷偷放回来。哪一面该挂是**部署脚本**的事，各调用方显式写：装机版 `--gateway desktop`（`.github/inno-setup/haitun.c`），云端 `launch-gateway.sh` `--gateway feishu`
- **实现用 `tyro.MISSING` 而非省略默认值**〔实测〕：该字段前面的字段都带默认值，真省掉会撞 dataclass 的「非默认字段不能跟在默认字段后」而 `TypeError`。`tyro.MISSING` 在 tyro 眼里是必填，对 dataclass 而言又是个普通默认值，不必为一个约束重排字段顺序（字段顺序就是 `--help` 的显示顺序）。**副作用**：`dataclasses.fields()` 上该字段的 `default` 是 `tyro.MISSING`（`tyro._singleton.PropagatingMissingType`）而 **不是** `dataclasses.MISSING`，只查后者会误判成「有默认值」
- **不传时的实际表现**〔实测，tyro 1.0.15 / Python 3.14.7〕：退出码 **2**，打一个 `Required options` 框，`Missing from <prog> gateway:` 后跟 `--gateway [{desktop,feishu} [{desktop,feishu} ...]]` 与该字段 docstring——缺什么、可选值是什么都在里面。**因此该字段的 docstring 刻意写短**：tyro 把它整段渲进这个报错框，写长了会把「你少给了一个参数」淹在几十行说明里；设计理由留在字段上方的注释里
- **CLI 可见的文本一律英文**（本仓约定）：字段 docstring、报错文案、启动 INFO 都会显示给命令行用户，统一英文；**代码注释、本文件与其他 `AGENTS.md` 保持中文**，别顺手翻译。判据是把 `--help` 抓出来跑 CJK 正则命中 0——抓的时候要设宽 `COLUMNS`（如 400），否则输出被终端宽度截断、漏掉后面的字段。设计理由与实测记录搬到字段上方的 `#` 注释里（不进 `--help`），是搬位置不是删
- **列表而非枚举**：「有哪些 gateway」将来会变，而 `both` 这种词只在恰好两个时成立，加第三个就得改枚举。上一版是个三选一枚举（`{both,desktop,feishu}`），改掉的正是这个
- **两个边界 tyro 都不管，代码自己拦**〔实测〕：`--gateway` 后不跟值得到 `[]` 且**退出码 0**——必填拦不到这一种（tyro 认为参数「给过了」），所以 `resolve_gateways()` 那条空列表拦截仍是唯一防线，别以为必填替掉了它；它抛 `ValueError` 退出，**不**自己补一个取值（该挂哪面只有部署方知道，内核猜出来的那面一样是静默的）；`--gateway feishu feishu` 重复值照收不报错 → 去重保序（意图无歧义，但注册两次会叠同名路由）。校验点与 `--browser` / `--webview` 互斥那条并列，都在建 socket / 恢复 state 之前失败
- **逗号形式不支持**〔实测〕：`--gateway desktop,feishu` 报错，只认空格分隔
- **`GET /` 指向 `index.html` 而非目录**〔实测〕：`add_static(..., show_index=False)` 对 `/feishu-web/` 这个裸目录回 **403**（ToC 侧靠在 `add_static` 之前另注册三条 `→ index.html` 的 handler 绕过）。跳目录会让 ToB 单挂时的首页变成 403；直接跳文件即可，不给飞书侧补那三条——补了会改动两面全挂时的路由集合，而那一条要求逐条不变

骨架 REST（`/ais` `/sessions` …）与 `/oauth/*` 在**每种组合下都在**：前者是各面共用的内核面，后者的回调地址登记在第三方应用后台，不随本进程挂了哪些 gateway 而变（见装配步骤 9d）。

#### gateway 与 agent 是两个独立维度

**内核里没有「产品线」这个概念**——产品线是人为划分，不该进内核词汇表。架构上 gateway 与 agent 可**自由组合**，这两维之间没有绑定关系：

- `--gateway` 只决定挂哪些 **HTTP 面**（路由 + 前端 + 该面的 manager）。
- `--default-agent` 只决定新 Session 用哪个 **agent 包**（tools / skills / system），见[路径默认值](#路径默认值)。

所以交叉组合是合法的，不予阻止。例如 `psi-agent gateway --gateway desktop --default-agent <ToB 的 agent 包>`：挂 ToC 的桌面前端，但会话跑 ToB 那套能力包——调试 ToB 工具时不必起飞书那一整套。反向同理。

这也是**为什么这个参数不按产品线命名**：那种名字暗示 gateway 与 agent 是绑定的（实际从来没有），还把 `both` 这种只在恰好两个时成立的词固化进了枚举。`psi-agent gateway --gateway feishu` 读起来有重复感，是已知且认可的取舍，不为此再改名。

### 两个 Gateway 同时跑

要么给不同 `--socket-path`，要么给不同 `--default-workspace`。冲突不来自共享前缀，而是**同一个完整管道名**〔实测〕：同 workspace 的调度 Session id 由 workspace 路径的 sha256 确定性派生（`runtime/_scheduler_manager.py`），两个进程必然算出同一个名字；`_session_manager` 的去重只在进程内，抓不到跨进程重名。Windows 上表现为 `PermissionError(13, ...)` / `[WinError 5] 拒绝访问`。

〔实测记录〕`--gateway feishu --socket-path gw-feishu --default-workspace A`（:18081）与 `--gateway desktop --socket-path gw-desktop --default-workspace B`（:18082）并存，两侧 `GET /defaults` 均 200，两份日志 0 处 `PermissionError` / `WinError 5`。

`--icon PATH` 指定图标文件路径（png/jpg/ico 等）。设置后该图标会作为 Web Console 的 favicon（`GET /favicon.ico`）。

`--app-name NAME` 指定 Web 控制台显示名（浏览器标签、webview 窗口、托盘 tooltip/菜单）。默认 `Haitun Agent`；Gateway 在 `GET /spa/index.html` 时注入页面 `<title>`。

`--tray` 开启系统托盘图标，此时 **必须** 同时指定 `--icon`（否则报错）。托盘左键点击打开 Web Console，右键可退出 Gateway。托盘可用性与桌面环境有关，缺失时不阻塞启动。`--no-tray` 关闭托盘（默认）。仅设置 `--icon` 不开启 `--tray` 时，图标只用作 favicon。两者均不设置时不创建托盘，也不提供 favicon。

`--webview` 使用原生 pywebview 窗口展示 Web Console。与 `--browser` 互斥，两者同时设为 True 时报错。必须同时指定 `--icon`（否则报错）。关闭窗口行为取决于 `--tray`：有托盘时仅隐藏窗口，无托盘时退出 Gateway 进程。

`--feishu-ai-id` / `--feishu-workspace-root` 见上文 `FeishuManager`（私聊按 `open_id`、群聊按 `chat_id` 各建独立会话）。

### Windows 安装包 launcher（`haitun.exe`）

Inno 安装后 `{app}` **就是** tob workspace（`tools/` / `skills/` / `systems/` 在根下），不是仓库的 `agents/feishu` 嵌套布局。`.github/inno-setup/haitun.c` 编译的 `haitun.exe` 必须显式传：

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
