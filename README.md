# psi-agent

> [English](README_en.md)

用 Python socket 拼装 AI agent 的微框架。

你只需要写 Python 函数和 Markdown——剩下的 socket 通信、tool calling、SSE 流式、定时任务框架全替你做了。

## 为什么选它

- **简单组合**：ai、session、channel 三个独立进程，socket 对插即用。没有中心配置，不依赖数据库
- **Workspace 即 Agent**：在 `workspace/` 下丢几个 Python 函数就是 tools，写个 system prompt 就是 agent 人格，加个 cron 就是定时任务
- **流式交互**：REPL 实时显示 AI 思考过程（dim 样式）和最终回复，所见即所得

## 架构

```
用户 ←→ Channel (REPL/CLI) ──Unix socket── Session ──Unix socket── AI (OpenAI/Anthropic)
```

## 快速开始

> 需要 Python >= 3.14

三个终端，三步跑起来：

```bash
# 安装
uv sync

# 终端 1：启动 AI 后端
uv run psi-agent ai openai-completions \
  --session-socket ./ai.sock \
  --model tencent/hy3-preview:free \
  --api-key sk-or-v1-xxxx \
  --base-url https://openrouter.ai/api/v1

# 终端 2：启动 Session
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

## CLI 一览

```
psi-agent
├── ai
│   ├── openai-completions    # OpenAI 兼容透传
│   └── anthropic-messages    # Anthropic→OpenAI 转换
├── session                    # Session + workspace 管理
└── channel
    ├── repl                   # 交互式 REPL
    └── cli                    # 单次消息
```

## 定义你自己的 Agent

一个 workspace 就是一个 agent：

```
my-workspace/
├── tools/                    # 每个 .py 文件定义一个 tool
│   └── bash.py               # async def bash(command: str) -> str
├── skills/                   # */SKILL.md 技能文档（system_prompt_builder 自行遍历）
├── schedules/                # 定时任务
│   └── daily-report/
│       └── TASK.md           # YAML 头 (name, cron) + Markdown body
└── systems/
    └── system.py             # async def system_prompt_builder() -> str
```

Tool 就是一个 async 函数，**函数名 = 文件名**，参数类型自动映射为 JSON Schema：

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

定时任务的 body 会在 cron 触发时作为消息发送给 AI：

```markdown
---
name: daily-report
cron: "0 12 * * *"
---
请生成一份项目进展日报。
```

更多细节见 `examples/a-simple-bash-only-workspace/`。

## 开发

```bash
uv run ruff check .          # lint
uv run ruff format --check . # 格式
uv run ty check              # 类型
uv run pytest -v             # 测试
```

## 作者

Hao Zhang <hzhangxyz@outlook.com>

## 许可

GNU Affero General Public License v3.0 or later. 详见 [LICENSE](LICENSE.md)。
