# Browser 操作工具集设计（haitun workspace）

日期：2026-07-09
分支：`add-browser-back`
状态：已批准，待实现

## 背景

给 haitun agent 增加浏览器操作能力，实现以下 12 个工具（名称与语义对齐
NousResearch Hermes Agent 的内置 browser toolset）：

`browser_navigate`、`browser_snapshot`、`browser_click`、`browser_type`、
`browser_press`、`browser_scroll`、`browser_back`、`browser_get_images`、
`browser_console`、`browser_vision`、`browser_dialog`、`browser_cdp`

### 溯源与路线选择

- 这 12 个工具名来自 **Hermes Agent 内置 browser toolset**，不是微软 Playwright
  MCP（Playwright MCP 的命名是 `browser_navigate_back` / `browser_press_key`
  等，且无 `browser_vision` / `browser_get_images` / `browser_cdp`）。
- Hermes 那套实现约上万行（`browser_tool.py` 单文件 ~206KB）+ Camofox 反检测
  Firefox + 多 provider（browser_use / browserbase / firecrawl）架构，与
  psi-agent 的工具进程模型深度耦合，无法直接搬迁。
- **采用路线 A**：在 psi-agent 里复刻这 12 个同名工具，后端用 Playwright
  Python 库自实现轻量 session 管理。纯 Python、无 Node 预装要求、可控可测、
  与现有 `_fetch_impl` / `_vision` 风格一致。

## 架构

在 `examples/haitun-workspace/tools/` 下新增两个文件，遵循现有
`fetch.py`（薄壳）+ `_fetch_impl.py`（实现）的分层：

- **`browser.py`** — 12 个薄壳 async 工具函数，每个带 Google-style docstring
  （工具描述 + Args），由 `ToolRegistry` 的 `compile+exec` 加载、
  `ToolFunction.from_callable()` 转 JSON Schema。
- **`_browser_impl.py`** — 实现层：module 级 `_Session` 单例管理 Playwright 的
  `browser` / `context` / `page`，生成 accessibility snapshot 的 `@eN` ref-id
  映射，实现各操作。

### session 保活机制（核心）

工具通过 `compile+exec` 加载进同一进程的 module `__dict__`，按 agent session
隔离（见 `src/psi_agent/session/tool_registry.py`）。因此 Playwright 浏览器实例
作为 `_browser_impl` 的 **module 级全局变量**，在同一 session 内跨工具调用自然
存活：`browser_navigate` 首次调用时 lazy 启动浏览器，后续
`browser_click` / `browser_type` / `browser_snapshot` 复用同一 page。此模式与
`_background_process_registry` 的有状态先例一致。

### 后端

Playwright Python（`async_api`），Chromium，默认 headless。ref-id 通过
accessibility / ARIA 快照生成，给每个可交互元素分配 `@eN` 编号，与 Hermes 的
snapshot→click 语义对齐。

## 工具签名与语义

所有函数 `async def`，返回 JSON 字符串（与 `fetch` / `describe_image` 一致）。
参数命名对齐 Hermes。

| 工具 | 签名 | 行为 |
|---|---|---|
| `browser_navigate` | `(url: str)` | lazy 启动浏览器并导航；初始化 session，必须先调 |
| `browser_snapshot` | `(full: bool = False)` | 返回 accessibility 树文本 + `@eN` ref-id；`full=False` 紧凑视图 |
| `browser_click` | `(ref: str)` | 点击 `@eN` 元素 |
| `browser_type` | `(ref: str, text: str)` | 清空后输入文本 |
| `browser_press` | `(key: str)` | 按键（Enter/Tab/快捷键） |
| `browser_scroll` | `(direction: str)` | 滚动（up/down/left/right） |
| `browser_back` | `()` | 后退 |
| `browser_get_images` | `()` | 列出页面图片 URL + alt |
| `browser_console` | `()` | 返回 console 输出 + JS 错误（累积捕获） |
| `browser_vision` | `(question: str = "")` | 截图存临时文件 → 复用 `_vision.describe_image_impl` 分析 |
| `browser_dialog` | `(action: str)` | 响应原生 JS 对话框（accept/dismiss） |
| `browser_cdp` | `(command: str, params: str = "")` | 发原始 CDP 命令（escape hatch），经 Playwright CDP session |

实现要点：

- **`browser_console`**：navigate 时挂 `page.on("console")` / `page.on("pageerror")`
  监听器，累积到 buffer，本工具读出。
- **`browser_dialog`**：navigate 时挂 `page.on("dialog")`，把 pending dialog 存起来，
  snapshot 里暴露 `pending_dialogs`，本工具决定 accept/dismiss。
- **`browser_vision`**：`page.screenshot()` 存临时 PNG → 调已有
  `describe_image_impl(path, question)` → 清理临时文件。复用 MiniMax vision，不引新依赖。
- **`browser_cdp`**：`params` 收 JSON 字符串，内部 parse（工具参数是扁平标量，不传 dict）。

## 错误处理与边界

- **统一返回结构**：每个工具返回 JSON，`{"ok": true/false, ...}`，失败带 `message`
  （与 `_fetch_impl._error` 同风格）。
- **未初始化保护**：除 `browser_navigate` 外，若 session 未启动，返回 `ok=false` +
  `"call browser_navigate first"`，不抛异常。
- **陈旧 ref 保护**：页面变化后旧 `@eN` 失效，`browser_click` / `browser_type` 捕获
  Playwright 错误，提示重新 `browser_snapshot`。
- **超时**：navigate 30s，交互 10s，超时返回 `ok=false` 而非挂起。
- **资源上限**：snapshot / get_images / console 输出截断到上限（仿 fetch 的 `max_chars`）。
- **清理**：内部 `_shutdown()` + `atexit` 关闭浏览器，避免僵尸 Chromium 进程
  （Windows 上尤其重要）。

## 依赖与打包

- **新依赖**：`playwright`（Python 库）加入 `pyproject.toml` dependencies。
- **浏览器内核**：首次需 `playwright install chromium`。写进 README / 工具 docstring；
  `browser_navigate` 检测到内核缺失时返回 `ok=false` + 安装指引，而非崩溃。
- **无 Node 要求**：Playwright Python 的 driver 自带，不需用户预装 Node（区别于
  Playwright MCP 的 `npx`）。这是路线 A 优于路线 B 的关键。
- **打包风险（已知，划出本次 scope）**：Playwright 自带 driver（Node 子进程）+
  Chromium 二进制，与 Nuitka/PyInstaller 单文件打包有已知张力（体积、driver 路径解析）。
  本次只保证**源码运行 + 测试通过**；打包适配作为后续单独事项。

## 测试

仿 `tests/test_schedule_manage.py` / `test_write_word.py` 模式，新增
`examples/haitun-workspace/tests/test_browser.py`：

- 用 Playwright 起本地 headless Chromium，导航到本地 `data:` URL 或临时 HTML 文件
  （不依赖外网）。
- 覆盖：navigate→snapshot→click→type 主链路、未初始化保护、陈旧 ref、console 捕获、
  dialog、get_images。
- `browser_vision` 的 vision API 调用 mock 掉（不打真实 MiniMax）。
- push 前跑 `ruff check` + `ruff format --check` + pytest（CI 两个 lint 都跑）。

## 非目标（YAGNI）

- 不实现 Camofox 反检测、多 provider（browserbase/firecrawl）、云浏览器。
- 不做多 tab 管理（Hermes 的 `browser_tabs` 不在清单内）。
- 不在本次处理 Nuitka/PyInstaller 打包适配。
