# psi-agent 微内核 Agent 框架 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 psi-agent 微内核 agent 框架，包含 ai/session/channel 三个独立组件，通过 Unix socket 以 OpenAI HTTP/SSE 协议通信。

**Architecture:** 单 Python package src-layout（`src/psi_agent/`），tyro Union dataclasses 驱动 CLI 子命令。所有 IO 异步（anyio）。组件通过 aiohttp Unix socket 通信。Session 解析 workspace 目录结构并按需执行 tool。

**Tech Stack:** Python >= 3.14, anyio, aiohttp, tyro, loguru, prompt-toolkit, rich, ruff, ty, hatch-vcs, any-llm-sdk, pytest + pytest-asyncio(anyio mode)

**Design Spec:** `docs/superpowers/specs/2026-05-20-psi-agent-design.md`

---

### Task 1: 项目脚手架

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `psi_agent/__init__.py`

- [x] **Step 1: 创建 pyproject.toml**

```toml
[build-system]
requires = ["hatchling", "hatch-vcs"]
build-backend = "hatchling.build"

[project]
name = "psi-agent"
dynamic = ["version"]
description = "A microkernel-style agent framework"
requires-python = ">=3.14"
dependencies = [
    "anyio>=4.0",
    "aiohttp>=3.9",
    "loguru>=0.7",
    "tyro>=0.9",
    "croniter>=6.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.11",
]

[project.scripts]
psi-agent = "psi_agent.cli:main"

[tool.hatch.version]
source = "vcs"

[tool.ruff]
target-version = "py314"
line-length = 120

[tool.ruff.lint]
select = ["E", "F", "I", "W", "UP", "ASYNC", "SIM", "C4", "B", "RUF", "N", "T20", "PLC"]

[tool.ruff.format]
quote-style = "double"
indent-style = "space"

[tool.pytest.ini_options]
asyncio_mode = "auto"
asyncio_default_fixture_loop_scope = "function"
testpaths = ["tests"]
```

- [x] **Step 2: 创建 .gitignore**

```
__pycache__/
*.pyc
*.pyo
.venv/
*.egg-info/
dist/
.ruff_cache/
.pytest_cache/
*.sock
```

- [x] **Step 3: 创建 psi_agent/__init__.py**

```python
"""psi-agent: A microkernel-style agent framework."""
```

- [x] **Step 4: 创建测试目录结构，添加 conftest.py**

```bash
mkdir -p tests/psi_agent/ai tests/psi_agent/session tests/psi_agent/channel
```

```python
# tests/conftest.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [x] **Step 5: 安装依赖并设置初始 git tag**

```bash
uv sync
git add -A && git commit -m "chore: project scaffold"
git tag v0.1.0
uv build
```

Expected: `uv build` 成功生成 `dist/psi_agent-0.1.0.tar.gz`

---

### Task 2: 日志模块

**Files:**
- Create: `psi_agent/_logging.py`
- Create: `tests/psi_agent/test__logging.py`

- [x] **Step 1: 写测试**

```python
# tests/psi_agent/test__logging.py
from loguru import logger

from psi_agent._logging import setup_logging


def test_setup_logging_default_info():
    logger.remove()
    handler_id = setup_logging(verbose=False)
    assert handler_id is not None
    logger.remove(handler_id)


def test_setup_logging_verbose_debug():
    logger.remove()
    handler_id = setup_logging(verbose=True)
    assert handler_id is not None
    logger.remove(handler_id)
```

- [x] **Step 2: 实现 _logging.py**

```python
# psi_agent/_logging.py
import sys

from loguru import logger


def setup_logging(*, verbose: bool = False) -> int:
    logger.remove()
    level = "DEBUG" if verbose else "INFO"
    handler_id = logger.add(
        sys.stderr,
        level=level,
        format=(
            "<green>{time:HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    return handler_id
```

- [x] **Step 3: 跑测试 + 提交**

```bash
uv run pytest tests/psi_agent/test__logging.py -v
git add psi_agent/_logging.py tests/psi_agent/test__logging.py && git commit -m "feat: add loguru-based logging setup"
```

---

### Task 3: OpenAI 协议类型

**Files:** `psi_agent/session/protocol.py`, `tests/psi_agent/session/test_protocol.py`

实现 `ToolFunction`, `DeltaMessage`, `StreamChoice`, `ChatCompletionChunk`, `Schedule` 等 dataclass。核心功能：
- `ToolFunction.from_callable()` 从 async 函数 inspect 出 name/description/parameters（JSON Schema）
- `ChatCompletionChunk.to_sse()` 生成 SSE 格式字符串
- `StreamChoice.to_dict()` / `DeltaMessage.to_dict()` 条件序列化
- 错误响应使用 inline dict（`{"error": {...}}`）

具体代码参考 spec 文档第 5 节。TDD：先写测试覆盖序列化/SSE生成/from_callable，再实现。

---

### Task 4: AI 层 — 统一后端（原 openai-completions + anthropic-messages）

> ⚠️ v0.3.0 已替换为单一 `Ai`（见"后续质量改进"）

**Files (原):** `psi_agent/ai/openai_completions/`, `psi_agent/ai/anthropic_messages/`

**Files (现):** `psi_agent/ai/__init__.py`, `psi_agent/ai/server.py`

**Dataclass:** `Ai(session_socket, provider, model, api_key, base_url, verbose)` with `async run()`

**Server:** 使用 [any-llm-sdk](https://github.com/mozilla-ai/any-llm) 的 `acompletion()` 转发到上游，支持 50+ provider，Anthropic→OpenAI SSE 格式转换自动处理。

---

### Task 5: Session — Tool 加载

**Files:** `psi_agent/session/__init__.py`, `psi_agent/session/tools.py`, `tests/psi_agent/session/test_tools.py`

`load_tools_from_workspace(tools_dir: Path) -> tuple[dict[str, ToolFunction], dict[str, Callable[..., Any]]]`:
1. 遍历 `tools_dir/*.py`
2. 对每个 `.py` 文件，用 `importlib` 动态加载模块
3. 找到文件中所有非 `_` 开头的 `async def` 函数
4. 用 `ToolFunction.from_callable()` 构建 OpenAI tool 定义
5. 跳过非 async、私有（`_` 开头）函数

---

### Task 6: Session — Scheduler

**Files:** `psi_agent/session/scheduler.py`, `tests/psi_agent/session/test_scheduler.py`

`load_schedules_from_workspace(schedules_dir: Path) -> list[Schedule]`:
1. 遍历 `schedules/*/TASK.md`
2. 解析 YAML front matter（`name`, `cron`）
3. 用 `croniter` 解析 cron 表达式
4. `Schedule` 是纯配置数据类（`name, cron, task_content`），运行时 cron 状态由 `run_one_schedule` 维护

`run_one_schedule` 独立 anyio task，每个 schedule 一个。

---

### Task 7: Session — Agent Loop

**Files:** `psi_agent/session/agent.py`, `tests/psi_agent/session/test_agent.py`

`SessionAgent` 类：
- `self.history: list[dict]` — 单一 history
- `self._tool_funcs: dict[str, callable]` — 已注册的实际 tool 函数
- `self._pending_schedule_chunks: list[ChatCompletionChunk]` — 暂存的 schedule 响应

`async run(user_message: dict, extra_params: dict | None = None) -> AsyncIterator[ChatCompletionChunk]`:
1. 惰性构建 system prompt（首次 run，history 尚无 system 消息）
2. 如果有 `_pending_schedule_chunks`，先 yield 所有暂存 chunk，清空暂存
3. 将 user_message 追加到 history
4. 构建 OpenAI 请求（history + tools），POST ai_socket，流式读取
5. 积累 tool_calls fragments
6. 收到 `finish_reason="stop"` → 结束
7. 收到 `finish_reason="tool_calls"` → 流式累积的 tool_calls 按 index 排序 → 执行 tool → 结果追加到 history → yield reasoning_content chunks → 回到步骤 4
8. 最多 `max_tool_rounds` 轮 tool call（CLI 可配置，默认 128）

---

### Task 8: Session — Channel-facing HTTP Server

**Files:** `psi_agent/session/server.py`, `psi_agent/session/__init__.py` (更新)

aiohttp Unix socket server 监听 `channel_socket`:
- `POST /chat/completions`: 解析请求，用 `SessionAgent.run()` 处理，SSE 流式返回
- 用 `anyio.Lock` 确保单请求，后续请求 FIFO 排队
- 请求 body 中的 messages 只取最后一条 user 消息

`Session.run()`: 通过 `SessionAgent.create()` 加载 workspace，启动 channel server（`serve_session`）和 schedule tasks（`run_one_schedule`），每个 schedule 一个独立 anyio task。

---

### Task 9: Channel — REPL

**Files:** `psi_agent/channel/__init__.py`, `psi_agent/channel/repl/__init__.py`, `psi_agent/channel/repl/client.py`, `tests/psi_agent/channel/test_repl.py`

`ChannelRepl.run()`:
1. 连接 `session_socket` Unix socket
2. 交互式循环：显示 `> `，读取行
3. `POST /chat/completions`（不发送 history）
4. SSE 流式读取，实时打印 reasoning_content（dimmed）和 content
5. Ctrl+C/D 退出

---

### Task 10: Channel — CLI

**Files:** `psi_agent/channel/cli/__init__.py`, `psi_agent/channel/cli/client.py`, `tests/psi_agent/channel/test_cli.py`

`ChannelCli.run()`:
1. 连接 `session_socket`，发送 `--message`
2. SSE 流式读取，显示结果后退出

---

### Task 11: CLI 入口

**Files:** `psi_agent/cli.py`

```python
from tyro import conf
from typing import Annotated

from psi_agent.ai import Ai
from psi_agent.session import Session
from psi_agent.channel.repl import ChannelRepl
from psi_agent.channel.cli import ChannelCli

ChannelGroup = Annotated[
    ChannelRepl | ChannelCli,
    conf.subcommand(name="channel", description="User interface channels"),
]

def main() -> None:
    import anyio
    cmd = tyro.cli(Session | Ai | ChannelGroup)
    anyio.run(cmd.run)
```

---

### Task 12: 示例 Workspace + README + AGENTS.md

**Files:**
- `examples/a-simple-bash-only-workspace/tools/bash.py`
- `examples/a-simple-bash-only-workspace/skills/hyw/SKILL.md`
- `examples/a-simple-bash-only-workspace/schedules/test-task/TASK.md`
- `examples/a-simple-bash-only-workspace/systems/system.py`
- `README.md`（中文）
- `AGENTS.md`（中文）

---

### Task 13: 基础集成测试 + Ruff + Ty 检查

- [x] 端到端集成测试：启动 AI server → 启动 session → CLI channel 发送消息 → 验证流式响应
- [x] `uv run ruff check .` 无错误
- [x] `uv run ruff format --check .` 无差异

---

### Task 14: 全面 Corner Case 集成测试

**Files:**
- Create: `tests/integration/conftest.py`
- Create: `tests/integration/test_ai_error_handling.py`
- Create: `tests/integration/test_session_concurrency.py`
- Create: `tests/integration/test_session_tools.py`
- Create: `tests/integration/test_session_workspace.py`
- Create: `tests/integration/test_channel_error.py`
- Create: `tests/integration/test_channel_repl_cli.py`
- Create: `tests/integration/test_end_to_end.py`

#### Sub-task 15a: conftest.py — 共享 Fixtures

- [x] `temp_workspace` — 创建临时 workspace（含 tools/、systems/system.py、schedules/）
- [x] `mock_ai_server` — 启动 mock AI aiohttp TCP server，支持可配置响应序列
- [x] `running_session` — 启动完整 session 进程，返回 channel socket path
- [x] `read_sse` — 从 socket 读 SSE 流的工具函数

#### Sub-task 15b: AI 层异常处理（8 tests）

- [x] 空 messages 数组 → 400 error JSON
- [x] 缺失 stream 字段 → 默认 streaming 正常
- [x] 非 JSON body → 400 error JSON
- [x] 上游连接拒绝 → 502 error JSON
- [x] 上游 SSE 流中途断开 → 正常结束不 hang
- [x] 上游 401/403 → 透传错误
- [x] Anthropic 空 content blocks → 正常返回 stop chunk
- [x] Anthropic 多 tool_use block 并行 → tool_calls 正确分 index

#### Sub-task 15c: Session 并发/锁（4 tests）

- [x] 第二个请求排队等待，第一个完成后顺序处理
- [x] 锁释放后第三个请求正常处理
- [x] schedule 触发时锁被占用 → 排队等待
- [x] 两次连续请求 share 同一个 history → 验证 history 累积

#### Sub-task 15d: Tool 执行 corner case（6 tests）

- [x] tool 抛出异常 → 以错误文本作为 tool result
- [x] tool 返回非字符串（int） → 转为字符串
- [x] tool 返回 None → 转为 "None"
- [x] tool 无参数 → properties/required 均为空
- [x] tool 参数类型为 list[str] → `{"type": "array", "items": {"type": "string"}}`
- [x] AI 无限 tool_call 循环 → 10 轮后返回 `[Max tool rounds reached]`

#### Sub-task 15e: Workspace 兼容性（5 tests）

- [x] tools/ 不存在 → 空 tools 列表，agent 正常运行
- [x] schedules/ 不存在 → 空 schedules 列表
- [x] systems/system.py 不存在 → system_prompt=None
- [x] system_prompt_builder() 抛异常 → catch，不影响启动
- [x] 完整 workspace + 无 tool_call 的正常对话

#### Sub-task 15f: Channel 错误处理（4 tests）

- [x] session socket 不存在 → 打印友好错误，exit code 1
- [x] session 中途崩溃 → 打印错误退出
- [x] 并发请求排队，按 FIFO 顺序处理
- [x] CLI --message 为空 → 正常处理

#### Sub-task 15g: Channel REPL/CLI（4 tests）

- [x] CLI 发送消息 → stdout 包含回复
- [x] REPL 多条消息 → history 在 session 端累积
- [x] SSE 流中 reasoning_content 和 content 交错 → 各自独立显示
- [x] 收到多个 choices → 迭代所有 choice

#### Sub-task 15h: 端到端全链路 mock（3 tests）

- [x] mock AI → session → CLI → 全链路 SSE 正确
- [x] mock AI 返回 tool_call + 最终文本 → tool 执行 → reasoning_content + content
- [x] channel 发 2 条消息 → session history 正确累积

---

## 实现备注

实现过程中进行的质量修正：

| 修正 | 说明 |
|------|------|
| REPL 改用 `prompt-toolkit` | `PromptSession.prompt_async()` 替换阻塞式 `input()`，消除 ASYNC250 |
| Session 全链路 `anyio.Path` | `Path.resolve()`、`is_dir()`、`read_text()`、`glob()` 等全部改为 async，消除 ASYNC240 |
| 合并嵌套 `async with` | 3 处 `ClientSession` + `session.post()` 合并为单语句，消除 SIM117 |
| `SockSite` 替换 `TCPSite._server` | 测试中通过预绑定 socket 获取随机端口，消除 `unresolved-attribute` |
| Dev deps 补充 | 添加 `ty>=0.0.38` 类型检查、`prompt-toolkit>=3.0` 异步 REPL |
| Ruff 规则扩展 | select 从 7 组扩展到 11 组：加 `B`、`RUF`、`N`、`T20` |
| SessionAgent TCP 支持 | ai_socket 参数同时支持 Unix socket 路径和 `http://` URL |

## 后续质量改进

| 改进 | 说明 |
|------|------|
| src-layout | `psi_agent/` → `src/psi_agent/`，删除 `tests/conftest.py`，用 `tests/__init__.py` + editable install 替代 |
| Rich 终端 | 15 处 `print()` → `console.print()`（`style="dim"` 思考过程、`[red]` 错误），T201 per-file-ignore 删除 |
| per-file-ignore 清零 | 5 条 → 0 条（通过 anyio 等效替代消除 ASYNC220/221/240/251 + 移除 E402） |
| Ruff 规则补充 | select 加 `"PLC"`（`PLC0415` 禁止非顶级 import），29 处修复 |
| MIT 许可证 | `LICENSE.md` + pyproject.toml license/author 字段 |
| GitHub CI | `.github/workflows/ci.yml`（push/PR → lint → test，tag → PyPI publish via Trusted Publisher） |
| Dependabot | `.github/dependabot.yml`（pip + github-actions，weekly） |
| 最终抑制 | **2 处 ty:ignore**（tyro overload + pytest fixture），**0 ruff noqa**，**0 per-file-ignore** |
| 并发/调度重构 | FIFO 排队替代 503、每 schedule 独立 anyio task、去重 _yaml.py、统一 SSE 错误格式、CLI 环境变量支持（model/base_url/api_key）、`response.prepare()` 移入 lock、Socket 手动清理策略 |
| AI 层抽象 | `SSEChunk` dataclass 替代 `build_error_sse_chunk` + anthropic 中 4 处裸 dict（7 个构造点 → 统一类型）；`serve_ai_backend()` 消除两个 `serve_*` 函数的 30 行重复 |
| AI 后端统一 | 采用 Mozilla any-llm-sdk，删除 `openai_completions/` 和 `anthropic_messages/` 子包（~500 行手写转换），单一 `Ai` 支持 50+ provider；净减 700 行 |

### 最终测试覆盖

- **单元测试**: ~78 tests（覆盖除 `cli.py` 和 channel 客户端外的所有源模块）
- **集成测试**: ~24 tests（AI 层、Session 并发/tool/workspace、Channel、端到端）
- **总计**: 126 tests（`-m "not schedule"`，+ 2 schedule 仅本地）


---

## 版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-05-20 | v0.1.0 | 15 任务实施计划：脚手架、协议类型、AI 层、Session 层（tools/scheduler/agent/server）、Channel 层（REPL/CLI）、CLI 入口、示例 Workspace |
| 2026-05-21 | v0.1.1 | 集成测试计划扩展：15 个 corner case 类别（40+ 测试场景）；实现备注补充（Rich、src-layout、per-file-ignore 清零） |
| 2026-05-22 | v0.1.2 | 最终测试覆盖数（133 tests）；质量修正汇总（src-layout、Rich、ruff 规则、AGPLv3、CI/CD、Dependabot） |
| 2026-05-23 | v0.2.0 | 并发模型重构（FIFO queuing）、调度器重构（per-task）、统一 SSE 错误格式、CLI 环境变量支持、_yaml.py 去重；测试数 133→137 |
| 2026-05-24 | v0.2.1 | 内部模块规范化：`logging.py` → `_logging.py`、`protocol.py` → `_protocol.py` |
| 2026-05-24 | v0.2.2 | 协议类型拆分：`_protocol.py` 拆为 `session/protocol.py` + `ai/common.py`，ErrorResponse 独立实现，提取 `build_error_sse_chunk` |
| 2026-05-24 | v0.2.3 | AI 层抽象：`SSEChunk` dataclass 替代 `build_error_sse_chunk` + 4 处裸 dict；`serve_ai_backend()` 消除两个 `serve_*` 的 15 行重复 |
| 2026-06-17 | v0.3.0 | 统一 AI 后端：采用 any-llm-sdk，删除 `openai_completions/` 和 `anthropic_messages/` 子包，单一 `Ai` 支持 50+ provider |
