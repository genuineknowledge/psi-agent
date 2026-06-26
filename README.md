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
 用户 ←→ Channel (REPL/CLI) ── TCP/Unix/Named Pipe ── Session ── TCP/Unix/Named Pipe ── AI
```

## 快速开始

> 需要 Python >= 3.14

### 单模型启动

最小可运行示例只需要一组 `ai`、`session` 和 `channel` 进程：

```bash
uv sync

uv run psi-agent ai \
  --provider openai \
  --session-socket ./deepseek-v4-pro.sock \
  --model deepseek-v4-pro \
  --api-key $DEEPSEEK_API_KEY \
  --base-url $DEEPSEEK_BASE_URL

uv run psi-agent session \
  --workspace ./examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./deepseek-v4-pro.sock

uv run psi-agent channel repl \
  --session-socket ./channel.sock \
  --models deepseek-v4-pro
```

REPL 操作：`Enter` 换行，`Alt+Enter` 发送，`Ctrl+D` 退出。

## 多模型路由

当一个 channel 需要在多个模型之间动态选择时，可以在 `channel repl` 或 `channel cli` 中传入多个 `--models`。候选模型建议按照“更轻量 / 更快”到“更强 / 更慢”的顺序排列，路由器会根据任务内容、语言环境和地域背景选择最合适的模型。

`channel` 只负责决定“本次请求使用哪个模型”，不负责模型到 socket 的绑定；真正的连接映射由 `session` 侧维护。

### 具体示例

下面这个例子展示了一个三模型路由的完整启动方式。`qwen3.6-chat`、`deepseek-v4-pro` 和 `gpt-4o` 分别对应不同的 AI 后端，`channel` 会根据请求内容在三者之间自动选择：

```bash
# 启动三个 AI 后端
uv run psi-agent ai \
  --session-socket ./qwen3.6-chat.sock \
  --model qwen3.6-chat \
  --provider openai \
  --api-key $QWEN_API_KEY \
  --base-url $QWEN_BASE_URL

uv run psi-agent ai \
  --session-socket ./deepseek-v4-pro.sock \
  --model deepseek-v4-pro \
  --provider openai \
  --api-key $DS_API_KEY \
  --base-url $DS_BASE_URL

uv run psi-agent ai \
  --session-socket ./gpt-4o.sock \
  --model gpt-4o \
  --provider openai \
  --api-key $OPENAI_API_KEY \
  --base-url https://api.openai.com/v1

# Session 显式绑定模型名与 socket
uv run psi-agent session \
  --workspace ./examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./deepseek-v4-pro.sock \
  --model-ai-sockets qwen3.6-chat ./qwen3.6-chat.sock deepseek-v4-pro ./deepseek-v4-pro.sock gpt-4o ./gpt-4o.sock

# Channel 传入候选模型列表
uv run psi-agent channel repl \
  --session-socket ./channel.sock \
  --models qwen3.6-chat deepseek-v4-pro gpt-4o
```

在这个配置下，涉及中国大陆、中文语境、国内平台或国内政策法规的问题，通常会优先路由到 `qwen3.6-chat`；涉及海外、英文语境或国外平台的问题，通常会优先路由到 `gpt-4o`；更偏复杂推理、代码分析或通用强能力任务时，通常会优先路由到 `deepseek-v4-pro`。

### 显式映射

如果你的部署中不同模型对应不同后端，推荐在 `session` 中显式绑定模型名与 AI socket：

```bash
uv run psi-agent session \
  --workspace ./examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./deepseek-v4-pro.sock \
  --model-ai-sockets qwen3.6-chat ./qwen3.6-chat.sock deepseek-v4-pro ./deepseek-v4-pro.sock
```

`--model-ai-sockets` 用于显式指定“模型名 -> AI socket”的对应关系，优先级高于默认的 `--ai-socket`。如果同一个模型存在更明确的后端地址，显式映射会覆盖自动推导结果。

### 自动映射

如果模型名和 socket 文件名保持一致，也可以只提供模型名列表，由 Session 自动生成同目录下的 sibling socket 路径：

```bash
uv run psi-agent session \
  --workspace ./examples/a-simple-bash-only-workspace \
  --channel-socket ./channel.sock \
  --ai-socket ./models/deepseek-v4-pro.sock \
  --model-names qwen3.6-chat deepseek-v4-pro
```

启用 `--model-names` 后，Session 会基于 `--ai-socket` 所在目录自动展开模型映射，例如 `qwen3.6-chat -> ./models/qwen3.6-chat.sock`，`deepseek-v4-pro -> ./models/deepseek-v4-pro.sock`。

### 一次性调用

如果只需要发送单条消息，可以使用 `channel cli`：

```bash
uv run psi-agent channel cli \
  --session-socket ./channel.sock \
  --models qwen3.6-chat deepseek-v4-pro \
  --message "请梳理中国新能源汽车产业链的主要环节"
```

`channel cli` 的模型选择逻辑与 REPL 保持一致，适合脚本化或单次验证场景。

## CLI 一览

```
psi-agent
├── ai                        # 统一 AI 后端（支持 50+ provider）
├── session                    # Session + workspace 管理
└── channel
    ├── repl                   # 交互式 REPL
    └── cli                    # 单次消息
```

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

MIT License. 详见 [LICENSE](LICENSE.md)。
