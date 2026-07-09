# 浏览器自动化工具接入方案（add-browser-back）

## 目标
给 haitun agent 补齐"浏览器自动化"能力（gap 分析中的 P0 缺失项），覆盖
`browser_navigate / click / type / snapshot / back / press / console / scroll /
get_images / vision / dialog / cdp` 一批工具。

## 已定决策
- **后端**：Playwright MCP（`@playwright/mcp`），复用项目已有的 `_mcp.py` MCP 桥接机制。
- **浏览器**：复用系统 **Edge**（`--browser msedge`）。
- **形态**：直接暴露 Playwright MCP 原生工具（不逐一改名对齐 Hermes 的 12 个名字）。
- **额外能力**：`--caps vision,devtools`（vision=截图；devtools=原生 CDP 工具，几乎零成本补回 browser_cdp）。

## 已实测验证的关键假设
1. `npx -y @playwright/mcp@latest --help` 确认 flag：`--port`（SSE/HTTP 常驻）、
   `--browser msedge`、`--caps vision,pdf,devtools`、`--shared-browser-context`、
   `--headless`、`--cdp-endpoint`、`--user-data-dir`。
2. 本地起 server 后 `tools/list` 拿到 23 个原生工具（browser_navigate、browser_snapshot、
   browser_click、browser_type、browser_press_key、browser_navigate_back、
   browser_console_messages、browser_handle_dialog、browser_take_screenshot、
   browser_tabs、browser_network_requests、browser_evaluate、browser_hover 等）。
3. **状态持久性**（成败关键）：`--shared-browser-context` 下，连接 A `browser_navigate`
   到 example.com 后断开，独立连接 B 的 `browser_snapshot` 仍返回 example.com 页面。
   证明浏览器状态可跨独立客户端连接保持——正契合 `_mcp.py` 每次调用重连的模式。
   （不加该 flag 时第二个连接报 "Browser is already in use"。）

## 待处理的两个问题
1. **前缀双写**：`_mcp.py:29` 强制 `函数名_` 前缀，`browser()` 会产出
   `browser_browser_navigate`。Playwright 工具名已自带 `browser_`，需让前缀可配。
2. **常驻 server 生命周期**：需在导入时按需拉起 SSE server、探测端口就绪、复用与清理。

## 实施步骤

### 1. 改造 `tools/_mcp.py`（最小侵入，不影响 serper）
- 在 `mcp()` 里让 prefix 可配：配置 dict 支持可选键 `prefix`；
  未提供时沿用旧的 `func.__name__ + "_"`（serper 行为不变）。
- `_resolve()` 保留 `prefix` 到返回的 config，`mcp()` 读取它决定前缀。

### 2. 新增 `tools/_browser_impl.py`
- 单例管理一个常驻 Playwright MCP SSE server 子进程：
  - 命令：`npx -y @playwright/mcp@latest --port <p> --browser msedge
    --shared-browser-context --caps vision,devtools`（headless 由 env 控制）。
  - 端口：优先固定端口，占用则探测空闲端口。
  - 就绪探测：轮询 stdout "Listening on" 或 HTTP endpoint 可连。
  - 返回 endpoint URL（`http://127.0.0.1:<p>/mcp`）。
- 进程随解释器退出清理（atexit / 注册到已有 background 机制）。
- node/npx 缺失时给出清晰报错（不 crash 导入）。

### 3. 新增 `tools/browser.py`
```python
@mcp
def browser() -> dict:
    """Browser automation via Playwright MCP (system Edge)."""
    return {"transport": "http", "url": <endpoint from _browser_impl>, "prefix": ""}
```
导入时自动生成全部原生 `browser_*` 工具，无双前缀。

### 4. 文档
- `AGENTS.md` 工具表加一行；注明前置条件（node/npx；首用复用系统 Edge）。

### 5. 测试 `tests/test_browser.py`
- mock MCP 会话（仿 test_fetch/test_xfyun 的 fake session 风格），
  验证：工具被发现、前缀为空、config 组装正确。**不实际起浏览器**。

### 6. 打包（无需改动，记录理由）
- Playwright MCP 是 **Node 包**（npx 运行），非 Python 依赖 →
  Nuitka（`--include-package`）/ PyInstaller（`--collect-submodules`）**都无需新增条目**。
- inno-setup 已捆绑 msys2 nodejs，运行时有 node/npx。
- 无新增 Python 依赖，`pyproject.toml` 不改。

## 验证方式
- `uv run pytest examples/haitun-workspace/tests/test_browser.py`
- `ruff check` + `ruff format --check`（CI 两者都跑）
- 手动 smoke：起 agent，navigate→snapshot→click 一条链路（可选，需真实 Edge）。

## 风险
- 依赖用户机器有 node/npx 与 Edge；缺失时工具导入需优雅降级而非 crash 整个 tools 加载。
- 首次 `npx` 会联网拉包；离线环境需预置 `@playwright/mcp`。
