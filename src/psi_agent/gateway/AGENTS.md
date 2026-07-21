# Gateway 层设计文档

## 概述

Gateway 是 psi-agent 的生命周期管理组件。它通过 OpenAPI REST 接口管理 AI 和 Session 的创建/删除/查询，并暴露面向 Web UI 的 Channel 端点。

Gateway 自身是一个独立的 aiohttp 进程，AI/Session 作为进程内 anyio task 运行。

## 架构

```
Gateway 进程
├── AIManager          — AI 实例注册表 + 生命周期管理
├── SessionManager     — Session 实例注册表 + 生命周期管理
├── TitleManager       — 会话标题 CRUD + AI 自动生成
├── WorkspaceManager   — 目录浏览
├── ChatManager        — SSE 流式对话管理
├── HistoryManager     — JSONL 历史读取
├── GatewayState       — 状态持久化到 state/latest.json
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
| `_session_manager.py` | `SessionManager` — Session 实例注册表 + 生命周期 + SessionInfo |
| `_title_manager.py` | 会话标题 CRUD + AI 自动生成 |
| `_state.py` | `GatewayState` dataclass — `state/latest.json` 的 load/save + 历史快照 `state/YYYYMMDD-HHMMSS.json` |
| `_spa_shell.py` | SPA 外壳注入 — `DEFAULT_APP_NAME`、`inject_app_name()`、`read_spa_index_template()`；`GET /spa/index.html` 替换 `__GATEWAY_APP_NAME__` |
| `server.py` | aiohttp Application + REST handlers |
| `_chat_manager.py` | SSE 流式对话管理（复用 ChannelCore） |
| `_history_manager.py` | JSONL 历史读取 |
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
3. state = GatewayState() + snapshot = await state.load()  — 加载持久化状态
4. anyio.create_task_group()                          — 手动管理 task group
5. 创建 AIManager + SessionManager + TitleManager
6. 恢复 AI（遍历 snapshot.ais → aim.create，失败 skip）
7. 恢复 Session（遍历 snapshot.sessions → sm.create，失败 skip）
8. 恢复标题（遍历 snapshot.titles → tm.set）
9. await create_app(aim, sm, tm, favicon_path=self.icon, app_name=self.app_name)  — 注册 REST 路由
10. 创建 _do_persist 闭包（快照三个 manager → state.save）
11. 注入 _persist（aim._persist = sm._persist = tm._persist = _do_persist）
12. await _do_persist()                                — 初始全量持久化
13. runner.setup() + create_site(runner, listen) + site.start()
14. if self.webview and self.icon is None: raise ValueError("--webview requires --icon")
15. if self.webview: wv = GatewayWebView(addr, has_tray=self.tray, icon=self.icon, app_name=self.app_name); wv.start()
16. if self.browser: webbrowser.open(addr)
17. if self.tray and self.icon is None: raise ValueError("--tray requires --icon")
18. if self.tray: GatewayTray(addr, self.icon, app_name=self.app_name, on_open=wv.show).start()
19. try: 三路等待 — tray.wait_stop() / wv.wait_closed() / sleep_forever()
20. finally: tray.stop()（如有）+ wv.stop()（如有）+ runner.cleanup() + tg.__aexit__()
```

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

AI 和 Session 之间通过 `_sockets.py` 抽象层以 Unix socket / Named Pipe 通信。

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

## AIManager

内存注册表，维护 `dict[str, _AiEntry]` + `anyio.Lock`。

每个 `_AiEntry` 包含：
- `scope: anyio.CancelScope` — 独立取消
- `info: AiInfo` — 包含 `id`、`socket`、`provider`、`model`、`api_key`、`base_url`

**`_persist` 回调**：构造函数参数，默认 no-op。Gateway.run() 在恢复完成后注入 persist 闭包（快照所有 manager → state.save），每次 create/delete/crash 后调用。

**create(provider, model, api_key, base_url, *, id="") 流程**：
1. 获取 lock
2. 若已有 **完全相同** 的配置（`provider`/`model`/`api_key`/`base_url`，base_url 忽略尾部 `/`），先停掉旧实例再创建；无显式 `id` 时复用旧 `ai_id`（避免 session 悬空）。显式 `id` 已存在且配置不同 → `ValueError`
3. `_socket_path(prefix, "ais", ai_id)` 生成 socket 路径
4. `_ensure_socket_dir(socket)` 创建父目录（anyio 异步）
5. 构造 `Ai(...)`（传入 api_key + base_url），创建 `CancelScope`，`task_group.start_soon`
6. 存入 `_entries`
7. `_wait_socket(socket)` 轮询等待 socket 出现
8. 成功后调用 `_persist`，返回 `AiInfo`
   失败则 rollback：pop entry + cancel scope + remove socket + 调用 `_persist`

**delete(ai_id) 流程**：
1. 获取 lock，断言存在
2. `del _entries[ai_id]` + `entry.scope.cancel()`
3. `_remove_socket(entry.info.socket)` + 调用 `_persist`

**get_socket(ai_id)**：AI 在 `_entries` 中则返回其 socket 路径；不在则通过 `_socket_path()` 计算路径返回（不抛 LookupError）。这使 Session 创建可以在 AI 尚未启动时预计算 socket 路径，支持启动恢复场景。

AI 运行时 crash 时，`_run_ai` 的 except 块从 `_entries` 中移除该 entry 并调用 `_persist`，确保持久化状态与内存一致。

## SessionManager

内存注册表，维护 `dict[str, _SessionEntry]` + `anyio.Lock`。

每个 `_SessionEntry` 包含：
- `scope: anyio.CancelScope` — 独立取消
- `info: SessionInfo` — 包含 `id`、`ai_id`、`workspace`、`channel_socket`

**`_persist` 回调**：同 AIManager，默认 no-op，Gateway.run() 注入。

**create(ai_id, *, id="", workspace="") 流程**：
1. 获取 lock，断言不重复
2. `aimanager.get_socket(ai_id)` 查 AI socket（AI 不存在时计算路径返回，不抛异常——支持启动恢复时 AI 尚未就绪）
3. `_socket_path(prefix, "channels", session_id)` 生成 channel socket
4. `_ensure_socket_dir(socket)` 创建父目录
5. 构造 `Session(...)`，创建 `CancelScope`，`task_group.start_soon`
6. 存入 `_entries`
7. `_wait_socket()` 轮询等待 channel socket 就绪
8. 成功后调用 `_persist`，返回 `SessionInfo`
   失败则 rollback：pop entry + cancel scope + remove socket + 调用 `_persist`

**delete(session_id)**：
1. 获取 lock，断言存在
2. `del _entries[session_id]` + `entry.scope.cancel()`
3. `_remove_socket(entry.info.channel_socket)` + 调用 `_persist`

Session 运行时 crash 时，`_run_session` 的 except 块从 `_entries` 中移除该 entry 并调用 `_persist`。

REST ``DELETE /sessions/{id}`` 在 SessionManager.delete 之后还会：
- 删除 workspace 下 ``histories/{id}.jsonl``（``HistoryManager.delete``，文件不存在则忽略）
- 清除 ``TitleManager`` 中该会话标题

**注意（有意为之）**：删除 AI **不会**级联删除依赖它的 Session。被删 AI 的 socket 失效后，挂在其上的 Session 仍存活但不可用——由前端负责不再访问这类失效 Session，后端不做级联清理。

## TitleManager

内存存储 `dict[str, str]`（session_id → title），维护会话标题映射。

**字段**：
- `_titles: dict[str, str]` — 标题映射
- `_persist: Callable[[], Awaitable[None]]` — 状态持久化回调，默认 no-op，Gateway.run() 注入

**set(session_id, title)** — **async**，设置标题后调用 `_persist`。

**generate(session_id, ai_socket, user_text, assistant_text)** — 通过 AI 自动生成标题，成功后写入 `_titles` 并调用 `_persist`。返回生成的 title 字符串，失败返回 None。

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/ais` | 创建 AI（201） |
| DELETE | `/ais/{ai_id}` | 删除 AI（200/404） |
| GET | `/ais` | 列出所有 AI |
| POST | `/sessions` | 创建 Session（201） |
| DELETE | `/sessions/{session_id}` | 删除 Session + history JSONL + 标题（200/404） |
| GET | `/sessions` | 列出所有 Session |
| POST | `/sessions/{session_id}/chat` | Web UI chat（SSE） |
| GET | `/sessions/{session_id}/history` | 获取会话历史（``is_displayable_chat_message`` 白名单 + 剥 `[SEND:]`/`[RECV:]`；assistant 行另附 ``sends`` 路径列表供交付物重水合） |
| GET | `/workspace/cwd` | Gateway 进程当前工作目录 |
| GET | `/workspace/places` | PathPicker 快捷位置（cwd / home / desktop / documents / downloads）+ 盘符 |
| GET | `/workspace/browse` | 浏览目录 `?path=...&kind=directory|file|all&q=...`，默认 `kind=directory` |
| GET | `/workspace/file` | 读取文件为 base64（`?path=...&root=...`）；``root`` 非空时路径须落在该目录下 |
| GET | `/titles` | 获取所有 session 标题 |
| POST | `/titles` | 设置 session 标题 `{id, title}` |
| POST | `/titles/generate` | AI 自动生成标题 `{id, user_text, assistant_text}` |
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
data: {"type": "text", "text": "Hello! "}
data: {"type": "blob", "name": "generated.png", "data": "base64..."}
data: [DONE]
```

**内部实现**：
- 查 `SessionManager.get_socket(session_id)` 获取 channel socket
- 复用 `channel._core.ChannelCore` 构造连接
- 输入：`TextChunk(text)`、blob（base64 解码后由 `_save_upload()` 落至 `~/Downloads/.psi/<date>/`，持久保留，转为 `FileChunk`）；multipart 文件上传通过 blob 通道走相同路径
- 输出：`TextChunk` → yield `{"type": "text"}`，`FileChunk` → 读取文件内容 base64 编码后 yield `{"type": "blob"}`

## Web Console (SPA)

Gateway 提供两套 Web 控制台：

| | `spa/`（v1） | `spa-v2/`（v2，默认） |
|--|--|--|
| 技术 | Vue 3 + Pinia | React 19 + Vite |
| 路由 | `/spa/` | `/spa-v2/` |
| 产品 | 会话气泡 | 任务卡 + 交付物宝箱 |

构建产物分别为 `spa/dist/`、`spa-v2/dist/`，由 Gateway 静态服务。**有 `spa-v2/dist` 时** `GET /` 重定向到 `/spa-v2/index.html`；否则回退 `/spa/index.html`。设计细节见各自目录下的 `AGENTS.md`。

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

服务端通过 `state/latest.json` 自动持久化 AI、Session、Title 状态，重启后自动恢复。对话历史仍通过 JSONL 文件独立持久化。浏览器 localStorage 仅保留 UI 状态（active ids、sidebar 折叠、主题偏好）和对话历史缓存。

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
- 可取消：`finally` 清理所有 task scope + `tg.__aexit__()`

## CLI 集成

```
psi-agent gateway [--listen http://127.0.0.1:PORT] [--socket-path psi] [--icon PATH] [--app-name NAME] [--browser/--no-browser] [--webview/--no-webview] [--tray/--no-tray] [--verbose]
```

默认 listen 为空，会自动绑定 127.0.0.1 随机高端口。`--browser` 开启自动打开浏览器。

`--icon PATH` 指定图标文件路径（png/jpg/ico 等）。设置后该图标会作为 Web Console 的 favicon（`GET /favicon.ico`）。

`--app-name NAME` 指定 Web 控制台显示名（浏览器标签、webview 窗口、托盘 tooltip/菜单）。默认 `Haitun Agent`；Gateway 在 `GET /spa/index.html` 时注入页面 `<title>`。

`--tray` 开启系统托盘图标，此时 **必须** 同时指定 `--icon`（否则报错）。托盘左键点击打开 Web Console，右键可退出 Gateway。托盘可用性与桌面环境有关，缺失时不阻塞启动。`--no-tray` 关闭托盘（默认）。仅设置 `--icon` 不开启 `--tray` 时，图标只用作 favicon。两者均不设置时不创建托盘，也不提供 favicon。

`--webview` 使用原生 pywebview 窗口展示 Web Console。与 `--browser` 互斥，两者同时设为 True 时报错。必须同时指定 `--icon`（否则报错）。关闭窗口行为取决于 `--tray`：有托盘时仅隐藏窗口，无托盘时退出 Gateway 进程。

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
- Mock AI server 通过 fixture 提供
