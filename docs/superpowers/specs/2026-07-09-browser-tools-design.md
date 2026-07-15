# Browser 操作工具集设计（haitun workspace）

日期：2026-07-09
分支：`add-browser-back`
状态：已实现并端到端验证通过

> **路线修订说明**：本文件初稿曾描述"路线 A"（在 tools/ 用 Playwright Python 库
> 复刻 12 个 Hermes 同名工具、无 Node）。经与既有实现核对，本分支实际采用并已落地的
> 是 **路线 B**：接 `npx @playwright/mcp` 服务器 + 复用系统 Edge，通过 `_mcp.py` 桥接
> 自动暴露 Playwright MCP 原生 `browser_*` 工具。本文件已改写为反映路线 B。详细踩坑记录
> 另见工作区 `docs/browser-tools-plan.md`。

## 背景

给 haitun agent 增加浏览器操作能力（gap 分析中的 P0 缺失项）。用户初始诉求是覆盖一批
`browser_*` 工具（navigate / click / type / snapshot / back / press / console /
scroll / get_images / vision / dialog / cdp）。

这批工具名源自 NousResearch Hermes Agent 的内置 browser toolset。但 Hermes 那套实现
上万行 + Camofox + 多 provider，无法搬迁。经评估，采用 **Playwright MCP** 作后端：
它暴露的原生工具（`browser_navigate` / `browser_snapshot` / `browser_click` /
`browser_type` / `browser_press_key` / `browser_navigate_back` /
`browser_console_messages` / `browser_handle_dialog` / `browser_take_screenshot` …
共约 40 个）在语义上覆盖用户诉求，且 `--caps vision,devtools` 补回截图与原生 CDP。
工具名沿用 Playwright 原生命名，不逐一改名对齐 Hermes 的 12 个名字。

## 架构

复用项目已有的 `_mcp.py` MCP 桥接（与 serper search 同一条管线）：

- **`tools/browser.py`** — `@mcp` 装饰器，返回
  `{transport:"http", url, prefix:"", terminate_on_close:False}`；导入时自动把
  Playwright MCP 的 `browser_*` 工具生成为一等 workspace 工具。
- **`tools/_browser_impl.py`** — 单例管理一个常驻 `npx @playwright/mcp` 服务器子进程：
  按需启动、探测 "Listening on" 就绪、返回 HTTP endpoint、`atexit` 清理整棵进程树。
- **`tools/_mcp.py`（改造）** — 新增两个可选配置键：`prefix`（默认沿用 `<func>_`，
  serper 行为不变）和 `terminate_on_close`（默认 True）。

### 状态跨调用保持（成败关键）

`_mcp` 每次工具调用新建/关闭一个 HTTP 连接。要让 `browser_navigate` 打开的页面被后续
`browser_snapshot` / `browser_click` 看到，必须两个条件同时满足：
① server 带 `--shared-browser-context`（否则第二连接报 "Browser is already in use"）；
② client 传 `terminate_on_close=False`（否则连接关闭时发 DELETE，Playwright MCP 把 tab
绑在 HTTP session 上，一删页面就回 about:blank）。

## 四个已实测踩过的坑（均已在实现中修复）

1. **双前缀**：`_mcp.py` 默认给工具名加 `<函数名>_`，`browser()` 会产出
   `browser_browser_navigate`。Playwright 工具名已自带 `browser_`，故传 `prefix=""`。
2. **状态跨调用丢失**：见上（`--shared-browser-context` + `terminate_on_close=False`）。
3. **Windows 上 localhost ≠ 127.0.0.1**：server 绑 `localhost` 只监听 IPv6 `::1`，
   连 `127.0.0.1` 返回 ConnectError。endpoint 必须用 `localhost`。
4. **孤儿 node 进程**：npx spawn 一个 Node 子进程（真正的 server），只 terminate npx
   会留孤儿泄漏。退出用 `taskkill /F /T /PID`（Win）/ `killpg`（POSIX）杀整棵树。

另：`--output-mode stdout` 必加，否则 snapshot 写文件而 agent 读不到。

## 环境依赖与打包

- **运行时依赖**：Node.js / `npx`（首次运行会联网拉 `@playwright/mcp` 包）+ 系统浏览器
  （默认 Edge）。Node 缺失时 `browser_*` 工具在加载期被 registry 优雅跳过（记日志，不致命）。
- **无新增 Python 依赖**：Playwright MCP 是 Node 包，非 Python 依赖 →
  `pyproject.toml` 不改，Nuitka / PyInstaller 都无需新增条目。inno-setup 已捆绑 msys2 nodejs。
- **可选 env**：`BROWSER_CHANNEL`（`msedge`/`chrome`）、`BROWSER_HEADLESS`（`1`/`0`）、
  `BROWSER_CAPS`（默认 `vision,devtools`）、`BROWSER_MCP_PACKAGE`、`BROWSER_STARTUP_TIMEOUT`。

## 测试

`tests/test_browser.py`（7 个用例，不起真实浏览器/npx）：验证 `_mcp` 的 `prefix=""` 不
双写、默认前缀保持 serper 行为、生成的工具是带签名的 async 函数、`_build_command` 组装、
npx 缺失时清晰报错。

端到端冒烟（Node + Edge 在场时，已于 2026-07-09 实测通过）：启动 server → 40 工具发现 →
navigate example.com → 独立第二连接 snapshot 仍见 example.com（证明状态跨连接保持）→ 清理。

push 前跑 `ruff check` + `ruff format --check`（CI 两者都跑）+ pytest。

## 非目标（YAGNI）

- 不实现 Hermes 的 Camofox 反检测、多 provider（browserbase/firecrawl）、云浏览器。
- 不把工具逐一改名对齐 Hermes 的 12 个名字（直接用 Playwright 原生名）。
- 不在本次处理 Nuitka/PyInstaller 打包适配（无新增 Python 依赖，本就无需改动）。
