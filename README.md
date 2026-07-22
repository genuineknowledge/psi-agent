# psi-agent

> [English](README_en.md)

用 Python socket 拼装 AI agent 的微内核框架。

你只需要写 Python 函数和 Markdown——剩下的 socket 通信、tool calling、SSE 流式、定时任务、对话持久化、Web 管理全部替你做了。

## 为什么选它

- **简单组合**：ai、session、channel 三个独立进程，socket 对插即用。没有中心配置，不依赖数据库
- **Workspace 即 Agent**：在 `workspace/` 下丢几个 Python 函数就是 tools，写个 system prompt 就是 agent 人格，加个 cron 就是定时任务
- **流式交互**：REPL/CLI 实时显示 AI 思考过程（dim 样式）和最终回复
- **一键启动**：`psi-agent run config.yml` 一个命令拉起全套 AI + Session + Channel
- **Web 管理中枢**：`psi-agent gateway` 启动 REST API + Web Console SPA，可视化创建/管理/对话

## 架构

```
 用户 ←→ Channel (REPL/CLI/Telegram/Feishu) ── TCP/Unix/Named Pipe ── Session ── TCP/Unix/Named Pipe ── AI
 用户 ←→ Web UI → HTTP → Gateway ── TCP/Unix/Named Pipe ── Session ── TCP/Unix/Named Pipe ── AI
```

AI 层无状态、Session 层维护对话历史、Channel 层是纯 UI 客户端——三个核心组件独立进程，通过 socket 通信，协议为 OpenAI Chat Completions HTTP/SSE。Gateway 为 Web UI 提供 HTTP 接入，内部复用 Channel socket 与 Session 通信。

对开发者：三个组件可独立启动、任意组合，适合调试和定制。对使用者：`psi-agent run config.yml` 一键拉起全部，`psi-agent gateway` 在浏览器里可视化管理一切。

## 快速开始

**需要 Python >= 3.14**

### 方式一：逐个启动

三个终端，三步跑起来：

```bash
# 安装
uv sync

# 终端 1：启动 AI 后端（--provider/--model/--api-key/--base-url 均可选，不传则读 PSI_AI_* 环境变量）
uv run psi-agent ai \
  --provider openai \
  --session-socket ./ai.sock \
  --model gpt-4o-mini \
  --api-key sk-xxxx \
  --base-url https://api.openai.com/v1

# 终端 2：启动 Session（--workspace 可选，默认当前目录）
uv run psi-agent session \
  --workspace ./examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./ai.sock

# 终端 3：REPL 交互
uv run psi-agent channel repl --session-socket ./channel.sock
```

REPL 操作：`Enter` 换行，`Alt+Enter`（或 `Escape+Enter`）发送，`Ctrl+D` 退出。

也可以一句命令搞定：

```bash
uv run psi-agent channel cli \
  --session-socket ./channel.sock \
  --message "列出当前目录的文件"
```

### 方式二：YAML 批量启动

写一个 `config.yml`，一个命令拉起全部：

```bash
uv run psi-agent run config.yml
```

`config.yml` 格式：

```yaml
- type: ai
  provider: openai
  session_socket: ./ai.sock
  model: gpt-4o-mini
  api_key: sk-xxxx                 # AI 参数均可选：不填则回退 PSI_AI_* 环境变量
  base_url: https://api.openai.com/v1

- type: session
  workspace: ./examples/a-simple-bash-only-workspace  # 可选，默认 .
  session_id: mychat         # 可选，不填自动生成 UUID
  channel_socket: ./channel.sock
  ai_socket: ./ai.sock

- type: channel
  name: repl                        # cli / repl / telegram / feishu
  session_socket: ./channel.sock
```

Servers（ai、session）持续运行，channel 组件按需退出（CLI 发完退出，REPL 到 Ctrl+D）。

> **注意**：`psi-agent run` 因 AI 和 Session 持续运行而不会自动退出，需 `Ctrl+C` 停止。批量模式始终启用 DEBUG 级别日志，YAML 中各组件 `verbose` 字段会被忽略。

### 方式三：Gateway Web 管理中枢

启动 Gateway 后，在浏览器里可视化管理一切：

```bash
uv run psi-agent gateway                         # 默认 127.0.0.1 随机端口
uv run psi-agent gateway --listen http://127.0.0.1:8080   # 指定端口
```

打开浏览器访问印出的地址，就会看到一个 Material Design 3 的 Web Console。界面里可以：

- **链接大模型**：选择 50+ provider，填 API key
- **创建会话**：选 workspace 目录，启动 Session
- **对话**：Markdown + LaTeX 渲染、文件上传/下载、流式输出
- **管理**：侧边栏切换会话、双击改名、删除确认
- **自动标题**：首次对话后 AI 自动生成会话标题

注意 `--listen` 参数需要 `http://` 前缀，裸 `IP:PORT` 会被误判为 Unix socket 路径。

Gateway 还支持系统托盘图标（`--tray --icon icon.png`）、自动打开浏览器（`--browser`）、原生 webview 窗口（`--webview`）和自定义 socket 路径前缀（`--socket-path psi`，控制 AI/Session Unix socket 的 `/tmp/{prefix}/ais/...` 和 `/tmp/{prefix}/channels/...` 路径）。

### 方式四：Nix 一键运行

装了 Nix（开启 flakes）就无需手动准备 Python、node 或依赖，直接跑：

```bash
nix run github:genuineknowledge/psi-agent -- gateway --listen http://127.0.0.1:8080
```

flake 是 headless 的（不含 `--webview`/`--tray` 的原生窗口特性），Web Console 在普通浏览器里照常使用。运行时所需的 `uv`、`node`/`npx`、`bash`、`git` 已经打进可执行文件的 PATH，agent 在 workspace 里拉起子进程时能直接找到。

Flake 输出：

- `packages.default`（= `psi-agent`）：注入了运行时工具 PATH 的可执行文件，`nix run` 的入口
- `packages.psi-agent-unwrapped`：不含 PATH 注入的纯 Python 环境
- `packages.psi-agent-spa`：单独构建的 Vue 前端产物
- `devShells.default`：开发环境（见「开发」）

## CLI 一览

```
psi-agent
├── run                       # YAML 批量启动（psi-agent run config.yml）
├── ai                        # 统一 AI 后端（支持 50+ provider）
├── gateway                   # 生命周期管理 + REST API + Web Console
├── session                    # Session + workspace 管理
└── channel
    ├── repl                   # 交互式 REPL
    ├── cli                    # 单次消息
    ├── telegram               # Telegram bot
    └── feishu                 # 飞书 bot
```

## 传输协议

所有组件通过地址前缀自动检测传输类型：

| 地址格式 | 传输 |
|----------|------|
| `./ai.sock`（裸文件系统路径，相对/绝对路径均可） | Unix socket |
| `http://127.0.0.1:8080` | TCP |
| `\\.\pipe\name`（Windows） | Named Pipe |

AI 和 Session 组件无需关心通信介质——由 `_sockets.py` 统一处理。

组件间的协议错误有两种形式：

- **非流式（HTTP 层面）**：请求解析失败时返回 HTTP 错误状态码和 JSON body：
  ```json
  {"error": {"message": "...", "type": "...", "param": null, "code": 400}}
  ```
- **流式（SSE 层面）**：已 commit HTTP 200 后的错误通过 ChatCompletionChunk 格式返回（`[DONE]` 的发送因层而异，见下文）：
  ```
  data: {"id": "error", "choices": [{"index": 0, "delta": {"content": "[Upstream Error]: ..."}, "finish_reason": "error"}]}
  ```
  `finish_reason="error"` 是 psi-agent 内部扩展标记，不暴露给外部。`[DONE]` 由 Gateway 层始终发送；Session 层仅在成功时发送；AI 层不发送 `[DONE]`（流结束由响应流终止标识）。

## 环境变量

| 变量 | 用途 |
|------|------|
| `PSI_AI_PROVIDER` | AI provider（openai / anthropic / gemini ...） |
| `PSI_AI_MODEL` | 模型名 |
| `PSI_AI_API_KEY` | API key |
| `PSI_AI_BASE_URL` | 上游 base URL |
| `PSI_TELEGRAM_BOT_TOKEN` | Telegram bot token |
| `PSI_TELEGRAM_PROXY` | Telegram SOCKS5 代理 |
| `PSI_FEISHU_APP_ID` | 飞书 app ID |
| `PSI_FEISHU_APP_SECRET` | 飞书 app secret |

CLI 参数优先于环境变量。AI 参数（provider、model、api_key、base_url）及 channel 认证参数均可选，未传时回退到环境变量。Socket 路径参数（--session-socket、--channel-socket、--ai-socket）为必填。

## 定义你自己的 Agent

一个 workspace 就是一个 agent：

```
my-workspace/
├── tools/                    # 非 _ 开头的 .py 文件——所有非 _ 的 async def 加载为 tool
│   └── bash.py               # async def bash(command: str) -> str
├── skills/                   # */SKILL.md 技能文档（system_prompt_builder 自行遍历）
├── schedules/                # 定时任务
│   └── daily-report/
│       └── TASK.md           # YAML 头 (name, cron) + Markdown body
└── systems/
    └── system.py             # async def system_prompt_builder() / system_prompt_rebuild_checker()
```

### Tools

Tool 就是一个 async 函数——非 `_` 开头的 `.py` 文件中，所有非 `_` 开头的 `async def` 都会加载为 tool：

```python
# tools/bash.py
import anyio

async def bash(command: str) -> str:
    """Execute a bash command.
    Args:
        command: The command to run.
    """
    result = await anyio.run_process(["/bin/bash", "-c", command])
    return result.stdout.decode().strip()
```

- 参数类型支持：`str`、`int`、`float`、`bool`、`list[X]`、`X | None`
- 文档字符串用 Google-style（`Args:` 段落），自动转为 tool description
- Tool 在每次对话回合前自动热重载——修改文件无需重启 Session

### System Prompt

在 `systems/system.py` 中定义两个可选异步函数：

```python
async def system_prompt_builder() -> str:
    """构造 system prompt，返回字符串。"""
    return "You are a helpful assistant."

async def system_prompt_rebuild_checker() -> bool:
    """每次对话回合前调用。返回 True 则重建 system prompt。"""
    return False
```

- `builder` 在首次对话时惰性调用
- `checker` 每次回合前调用，可用于监控文件变更后自动刷新 prompt
- 两个都是可选的，缺失时用合理默认值

### 定时任务

在 `schedules/<name>/TASK.md` 中定义，YAML 头部指定 name 和 cron 表达式，body 在 cron 触发时作为消息发给 AI：

```markdown
---
name: daily-report
cron: "0 12 * * *"
---
请生成一份项目进展日报。
```

- 每个 schedule 有独立 CancelScope，支持热重载
- 每个 schedule 独立加载——IO 错误、YAML 解析问题、cron 验证失败只跳过该 schedule
- Schedule 触发时自动获取 session lock，串行处理

### Skills

在 `skills/` 下任意目录中创建 `SKILL.md` 文件：

```
skills/
└── my-skill/
    └── SKILL.md       # 任意 Markdown 内容
```

`system_prompt_builder` 应按约定遍历 `skills/*/SKILL.md`、解析 YAML 头并将内容注入 system prompt。psi-agent 框架本身不直接解析 skill 文件——由 workspace 自行定义如何使用。

### 对话历史持久化

Session 自动将对话历史持久化到 `workspace/histories/{session_id}.jsonl`：

- `--session-id` 不传时自动生成 UUID，传入自定义 ID 可 resume 历史会话（仅允许 `[a-zA-Z0-9_-]`）
- JSONL 逐行存储，原子写入（临时文件 + `anyio.Path.replace`）
- **回合级原子性**：user message 立即落盘作为崩溃基线，AI 响应仅成功完成时落盘，异常时不写入

```bash
# 新建会话
uv run psi-agent session --session-id mychat --channel-socket ./channel.sock --ai-socket ./ai.sock

# 下次 resume
uv run psi-agent session --session-id mychat --channel-socket ./channel.sock --ai-socket ./ai.sock
```

## Gateway REST API

Gateway 暴露以下 REST 端点（详细信息见 [Gateway 层设计文档](src/psi_agent/gateway/AGENTS.md)）：

| Method | Endpoint | 说明 |
|--------|----------|------|
| POST | `/ais` | 创建 AI 实例 |
| DELETE | `/ais/{ai_id}` | 删除 AI |
| GET | `/ais` | 列出所有 AI |
| POST | `/sessions` | 创建 Session |
| DELETE | `/sessions/{session_id}` | 删除 Session |
| GET | `/sessions` | 列出所有 Session |
| POST | `/sessions/{session_id}/chat` | Web UI 对话（SSE 流式） |
| GET | `/sessions/{session_id}/history` | 获取会话历史 |
| GET | `/titles` | 获取所有会话标题 |
| POST | `/titles` | 设置会话标题 |
| POST | `/titles/generate` | AI 自动生成标题 |
| GET | `/workspace/browse` | 浏览目录（`?path=...`） |
| GET | `/workspace/cwd` | 获取工作目录 |
| GET | `/openapi.json` | OpenAPI schema |
| GET | `/favicon.ico` | favicon（仅当 `--icon` 设置时有效，否则返回 404） |

### Web Console 聊天协议

`POST /sessions/{session_id}/chat` 接受 Chunk 列表，返回 SSE 流：

**Request:**
```json
{
  "chunks": [
    {"type": "text", "text": "Hello!"},
    {"type": "blob", "name": "image.png", "data": "base64..."}
  ]
}
```

**Response (SSE):**
```
data: {"type": "reasoning", "text": "Let me think..."}
data: {"type": "text", "text": "Hello! "}
data: {"type": "blob", "name": "generated.png", "data": "base64..."}
data: {"type": "error", "error": "..."}
data: [DONE]
```

## 高级：Telegram / 飞书 Bot

### Telegram

```bash
uv run psi-agent channel telegram \
  --session-socket ./channel.sock \
  --bot-token $PSI_TELEGRAM_BOT_TOKEN \
  --allowed-user-ids 123456789 \
  --proxy socks5://127.0.0.1:1080
```

- 支持文本、图片、文档收发
- 流式输出：通过 `edit_text` 增量累积实现打字机效果
- 用户白名单：`--allowed-user-ids` 可选
- SOCKS5 代理：`--proxy` CLI arg 或 `PSI_TELEGRAM_PROXY` 环境变量

### 飞书

```bash
uv run psi-agent channel feishu \
  --session-socket ./channel.sock \
  --app-id $PSI_FEISHU_APP_ID \
  --app-secret $PSI_FEISHU_APP_SECRET \
  --allowed-user-ids "ou_abc123"
```

- WebSocket 长连接，SDK 后台线程 + anyio portal 桥接
- 卡片流式渲染：`stream.append()` 逐段更新飞书卡片
- 处理状态表情：处理中显示 `Typing`，完成移除，失败显示 `CrossMark`
- 支持文本、图片、文件、音频
- 文档评论回复：`--respond-to-comments`（默认开）文档评论区 @机器人 时，用 agent 的回答回复该评论（需后台订阅 `drive.notice.comment_add_v1`）
- 按用户独立会话（两种模式，二选一）：
  - **动态任意用户** `--gateway-url` + `--ai-id`：任意飞书用户 DM 机器人时，channel 按其 open_id 经 Gateway `POST /sessions` 幂等开通一个独立 session（复用 Gateway `SessionManager` 生命周期，channel 只连接不 spawn），拿回 socket 再连——每人隔离会话/历史。Gateway 不可达/创建失败时回退共享 `--session-socket`。例：`--gateway-url http://127.0.0.1:8760 --ai-id my-ai`
  - **固定已知用户** `--route-template`（含 `{open_id}` 占位符）：按 open_id 派生各自 session socket，per-user session 进程需外部**预先**拉起（channel 只连接不 spawn），命名须与派生路径对齐。例：`--route-template "./sessions/{open_id}.sock"`
  - 两者都不设：全体共用 `--session-socket`（默认行为）。共享 workspace 时历史按 `histories/{open_id}.jsonl` 隔离

## 示例 Workspace

`examples/` 下有多个示例 workspace，覆盖从极简到生产级的各种场景——从单 tool、定时任务、MCP 接入，到多层 system prompt、持久化记忆、Telegram/飞书 bot 等。详见 `examples/` 目录。

## 开发

```bash
uv run ruff check .          # lint
uv run ruff format --check . # 格式
uv run ty check              # 类型
uv run pytest -v             # 测试
```

用 Nix 的话，`nix develop` 会进入带 Python 3.14、`uv` 和 `node` 的开发 shell，之后照常用上面的 `uv run ...` 命令即可。

## 贡献者

<a href="https://github.com/genuineknowledge/psi-agent/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=genuineknowledge/psi-agent" />
</a>

## 许可

MIT License. 详见 [LICENSE](LICENSE.md)。
