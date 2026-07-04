# psi-agent

> [English](README_en.md)

用 Python socket 拼装 AI agent 的微内核框架。

你只需要写 Python 函数和 Markdown——剩下的 socket 通信、tool calling、SSE 流式、定时任务、对话持久化、Web 管理全部替你做了。

## 为什么选它

- **简单组合**：ai、session、channel 三个独立进程，socket 对插即用。没有中心配置，不依赖数据库
- **Workspace 即 Agent**：在 `workspace/` 下丢几个 Python 函数就是 tools，写个 system prompt 就是 agent 人格，加个 cron 就是定时任务
- **流式交互**：REPL 实时显示 AI 思考过程（dim 样式）和最终回复，所见即所得
- **一键启动**：`psi-agent run config.yml` 一个命令拉起全套 AI + Session + Channel
- **Web 管理中枢**：`psi-agent gateway` 启动 REST API + Web Console SPA，可视化创建/管理/对话

## 架构

```
 用户 ←→ Channel (REPL/CLI/Telegram/Feishu/Web UI) ── TCP/Unix/Named Pipe ── Session ── TCP/Unix/Named Pipe ── AI
```

AI 层无状态、Session 层维护对话历史、Channel 层是纯 UI 客户端。三个组件独立进程，通过 socket 通信——协议为 OpenAI Chat Completions HTTP/SSE。

对开发者：三个组件可独立启动、任意组合，适合调试和定制。对使用者：`psi-agent run config.yml` 一键拉起全部，`psi-agent gateway` 在浏览器里可视化管理一切。

## 快速开始

> 需要 Python >= 3.14

### 方式一：逐个启动

三个终端，三步跑起来：

```bash
# 安装
uv sync

# 终端 1：启动 AI 后端（--api-key 可选，不传则读 PSI_AI_API_KEY 环境变量）
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

REPL 操作：`Enter` 换行，`Alt+Enter` 发送，`Ctrl+D` 退出。

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
  api_key: sk-xxxx                 # 不填则读 PSI_AI_API_KEY 环境变量
  base_url: https://api.openai.com/v1

- type: session
  workspace: ./examples/a-simple-bash-only-workspace  # 可选，默认 .
  channel_socket: ./channel.sock
  ai_socket: ./ai.sock

- type: channel
  name: repl                        # cli / repl / telegram / feishu
  session_socket: ./channel.sock
```

Servers（ai、session）持续运行，channel 组件按需退出（CLI 发完退出，REPL 到 Ctrl+D）。

### 方式三：Gateway Web 管理中枢

启动 Gateway 后，在浏览器里可视化管理一切：

```bash
uv run psi-agent gateway
```

打开浏览器访问印出的地址（默认 127.0.0.1 随机端口），就会看到一个 Material Design 3 的 Web Console。界面里可以：

- **链接大模型**：选择 50+ provider，填 API key
- **创建会话**：选 workspace 目录，启动 Session
- **对话**：Markdown + LaTeX 渲染、文件上传/下载、流式输出
- **管理**：侧边栏切换会话、双击改名、删除确认
- **自动标题**：首次对话后 AI 自动生成会话标题

> **安全提示**：Gateway 默认仅监听 `127.0.0.1`，请勿用 `--listen 0.0.0.0` 对外暴露——这会开放任意目录列举。

Gateway 还支持系统托盘图标（`--tray icon.png`）和抑制自动打开浏览器（`--no-browser`）。

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
| `./ai.sock`（裸文件系统路径） | Unix socket |
| `http://127.0.0.1:8080` | TCP |
| `\\.\pipe\name`（Windows） | Named Pipe |

AI 和 Session 组件无需关心通信介质——由 `_sockets.py` 统一处理。

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

CLI 参数优先于环境变量。所有参数均可选，未传时回退到环境变量。

## 定义你自己的 Agent

一个 workspace 就是一个 agent：

```
my-workspace/
├── tools/                    # 每个 .py 文件定义若干 tool（所有非 _ 的 async def）
│   └── bash.py               # async def bash(command: str) -> str
├── skills/                   # */SKILL.md 技能文档（system_prompt_builder 自行遍历）
├── schedules/                # 定时任务
│   └── daily-report/
│       └── TASK.md           # YAML 头 (name, cron) + Markdown body
└── systems/
    └── system.py             # async def system_prompt_builder() -> str
```

### Tool

Tool 就是一个 async 函数，文件中所有非 `_` 开头的 `async def` 都会加载为 tool：

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

- `--session-id` 不传时自动生成 UUID，传入自定义 ID 可 resume 历史会话
- JSONL 逐行存储，原子写入（tempfile + `os.replace`）
- **回合级原子性**：仅成功完成的回合落盘，异常时自动回滚

```bash
# 新建会话
uv run psi-agent session --session-id mychat --channel-socket ./c.sock --ai-socket ./ai.sock

# 下次 resume
uv run psi-agent session --session-id mychat --channel-socket ./c.sock --ai-socket ./ai.sock
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
| GET | `/favicon.ico` | favicon（需 `--tray`） |

### Web Consle 聊天协议

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
data: {"type": "text", "text": "Hello! "}
data: {"type": "blob", "name": "generated.png", "data": "base64..."}
data: [DONE]
```

## 高级：Telegram / 飞书 Bot

### Telegram

```bash
uv run psi-agent channel telegram \
  --session-socket ./channel.sock \
  --bot-token $TELEGRAM_BOT_TOKEN \
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
  --app-id $FEISHU_APP_ID \
  --app-secret $FEISHU_APP_SECRET \
  --allowed-user-ids "ou_abc123"
```

- WebSocket 长连接，SDK 后台线程 + anyio portal 桥接
- 卡片流式渲染：`stream.append()` 逐段更新飞书卡片
- 处理状态表情：处理中显示 `Typing`，完成移除，失败显示 `CrossMark`
- 支持文本、图片、文件、音频

## 示例 Workspace

`examples/` 下有多个示例 workspace，覆盖从极简到生产级的各种场景——从单 tool、定时任务、MCP 接入，到多层 system prompt、持久化记忆、Telegram/飞书 bot 等。详见 `examples/` 目录。

## 开发

```bash
uv run ruff check .          # lint
uv run ruff format --check . # 格式
uv run ty check              # 类型
uv run pytest -v             # 测试
```

## 贡献者

<a href="https://github.com/genuineknowledge/psi-agent/graphs/contributors">
  <img src="https://contrib.rocks/image?repo=genuineknowledge/psi-agent" />
</a>

## 许可

MIT License. 详见 [LICENSE](LICENSE.md)。
