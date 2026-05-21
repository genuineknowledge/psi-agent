# psi-agent

微内核式 Python Agent 框架。三个独立组件——ai、session、channel——通过 Unix domain socket 以 OpenAI-compatible HTTP/SSE 协议通信。

## 设计理念

- **微内核**: 核心极简，功能由 workspace 定义
- **无状态组件**: ai/session/channel 各自独立，通过 socket 任意组合
- **全异步**: 所有 IO 使用 anyio，永不使用 asyncio 原生 API 或 pathlib
- **充分日志**: loguru 全覆盖，每个 chunk 可追踪
- **现代 Python**: 3.14+，src-layout，无历史包袱
- **零抑制**: ruff 和 ty 不设 per-file-ignore，不堆 noqa

## 架构

```
Channel (REPL/CLI) ←→ Session ←→ AI (OpenAI/Anthropic)
                   Unix socket  Unix socket
```

三个组件各自独立启动，通过 socket 路径连接。Session 单一 history 全内存，无持久化。

## 快速开始

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
# 或单次 CLI
uv run psi-agent channel cli \
  --session-socket ./channel.sock \
  --message "列出当前目录的文件"
```

### REPL 操作

- `Enter` — 换行（多行输入）
- `Alt+Enter` — 发送消息
- `Ctrl+D` — 退出
- 思考过程以 dim 样式显示，正文正常显示

## CLI 结构

```
psi-agent
├── ai
│   ├── openai-completions    # OpenAI 兼容透传后端
│   └── anthropic-messages    # Anthropic→OpenAI 转换后端
├── session                    # Session + workspace 管理
└── channel
    ├── repl                   # 交互式 REPL（Rich + prompt_toolkit）
    └── cli                    # 单次消息 CLI（Rich 格式化输出）
```

## Workspace 结构

```
workspace/
├── tools/           # *.py 文件，每个定义一个 tool 函数
├── skills/          # */SKILL.md 技能文档（system_prompt_builder 遍历）
├── schedules/       # */TASK.md 定时任务（YAML 头 + cron + body）
└── systems/
    └── system.py    # async def system_prompt_builder() -> str
```

### Tool 定义

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

- 文件名 = 函数名
- async 函数、非私有
- 参数类型注解自动映射为 JSON Schema 类型
- Google-style `Args:` 解析为参数描述

### 定时任务

```markdown
---
name: daily-report
cron: "0 12 * * *"
---
请生成一份项目进展日报。
```

触发后的 AI 回复暂存，下一次 channel 请求时和新回复一起返回。

## 开发

```bash
uv run ruff check .          # lint
uv run ruff format --check . # 格式检查
uv run ty check              # 类型检查
uv run pytest -v             # 全量测试
uv build                     # 构建
```

### 集成测试

```bash
# 需要真实 API —— 设置环境变量
export PSI_TEST_OPENAI_API_KEY="sk-xxx"
export PSI_TEST_OPENAI_BASE_URL="https://api.llm.ustc.edu.cn/v1"
export PSI_TEST_OPENAI_MODEL="deepseek-v4-flash-ascend"
export PSI_TEST_ANTHROPIC_API_KEY="sk-xxx"
export PSI_TEST_ANTHROPIC_BASE_URL="https://api.llm.ustc.edu.cn/v1"
export PSI_TEST_ANTHROPIC_MODEL="deepseek-v4-flash-ascend"

uv run pytest -v
```

## 技术栈

| 领域 | 技术 |
|------|------|
| 异步 | anyio |
| HTTP | aiohttp |
| CLI | tyro |
| REPL | prompt-toolkit + Rich |
| 日志 | loguru |
| Lint/Format | ruff |
| 类型检查 | ty |
| 测试 | pytest + pytest-asyncio |
| 构建 | uv + hatchling + hatch-vcs |
| Python | >= 3.14 |

## 许可

MIT
