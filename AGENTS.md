# AGENTS.md

本文档面向后续开发者（人或 AI Agent），说明 psi-agent 的设计思路、代码结构、开发约定以及我们在开发过程中沉淀的最佳实践。

## 设计理念

psi-agent 是一个**微内核**式的 agent 框架。核心理念是：

1. **最小化核心**: 框架本身只提供通信协议、组件组合和 tool/Schedule 加载机制
2. **功能由 workspace 定义**: agent 的能力（tools、system prompt、定时任务）完全由 workspace 目录中的文件定义
3. **组件无状态**: AI 后端不保存任何状态；Session 只维护一个内存中的 history；Channel 不管理历史
4. **组合优于继承**: 三个独立组件通过 Unix socket 任意组合
5. **一切异步**: 所有 IO 操作使用 `anyio`，永不使用 `asyncio` 原生 API 或 `pathlib`
6. **零抑制**: 不堆 `noqa`，不设 `per-file-ignores`。代码本身应符合规则

## 架构决策记录

以下是设计过程中有意为之的关键决策：

**为什么用 Unix socket 而非 TCP？**
Socket 文件天然隔离——不同项目用不同文件路径，互不干扰。没有端口冲突，没有防火墙问题。本地组件通信不需要网络栈开销。

**为什么 AI 是 Server、Session 是 Client？**
AI 后端无状态，不保存任何信息。多个 Session 可以共享同一个 AI backend。如果反过来（Session 是 Server），每个 Session 都要自行配置上游 API，违反"组合"原则。

**为什么 Session 不持久化 history？**
微内核原则：核心只做通信和路由。history 是实现细节，不应成为框架的一部分。未来可以加可选的持久化插件，但内核本身体积不应膨胀。

**为什么 socket 文件不自动 unlink？**
支持热换 Server。每个 `session.post()` 新建 TCP/Unix 连接，由 `UnixConnector` 按路径重新 connect。只要新的服务进程绑定到同一 socket 路径，客户端无需重启即可继续通信。auto-unlink 会破坏这个能力——socket 文件需要保留，由新进程手动接管。

## 技术栈

| 领域 | 技术 |
|------|------|
| 异步 | `anyio`（禁止使用 `asyncio` 原生 API、`pathlib`） |
| HTTP | `aiohttp`（Unix socket + TCP） |
| CLI | `tyro`（Union dataclasses + 嵌套子命令） |
| REPL | `prompt-toolkit`（multiline async prompt）+ `rich`（终端格式化） |
| 日志 | `loguru` |
| Lint/Format | `ruff` |
| 类型检查 | `ty`（Astral 出品，Rust 实现） |
| 测试 | `pytest` + `pytest-asyncio`（anyio mode） |
| 构建 | `uv` + `hatchling` + `hatch-vcs` |
| Python | >= 3.14 |

## 代码结构

```
src/
└── psi_agent/
    ├── cli.py                  # tyro CLI 入口，定义 top-level Union
    ├── _yaml.py               # 共享 YAML header 解析（scheduler + workspace system.py）
    ├── _logging.py              # loguru 配置，verbose→DEBUG
    ├── ai/
    │   ├── AGENTS.md                # AI 层设计文档
    │   ├── __init__.py               # AiBackend dataclass + run()
    │   ├── common.py                 # ErrorResponse + serve_ai_backend
    │   └── server.py                 # 统一 handler（any-llm-sdk）
    ├── session/
    │   ├── AGENTS.md                # Session 层设计文档
    │   ├── __init__.py             # Session dataclass + run()，workspace 加载入口
    │   ├── server.py               # channel 端 aiohttp server，单锁串行
    │   ├── agent.py                # 核心 agent loop（history + tool call + streaming）
    │   ├── protocol.py             # Session 层协议类型（ChatCompletionChunk 等）
    │   ├── tools.py                # workspace tools 加载（async anyio.Path）
    │   └── scheduler.py            # cron-based 定时任务（croniter）
    └── channel/
        ├── AGENTS.md                # Channel 层设计文档
        ├── repl/                   # 交互式 REPL（Rich Console + prompt_toolkit multiline）
        └── cli/                    # 单次消息 CLI（Rich 格式化输出）
```

项目使用 **src-layout**（`src/psi_agent/`），由 `uv sync` 安装为 editable package。

各层的详细设计文档见：
- **AI 层**: `src/psi_agent/ai/AGENTS.md` — provider 配置、请求透传、错误处理
- **Session 层**: `src/psi_agent/session/AGENTS.md` — workspace 启动、agent loop、tool 加载调用、schedule 机制
- **Channel 层**: `src/psi_agent/channel/AGENTS.md` — 终端输出、REPL/CLI 约定

## 核心通信协议

所有组件通过 **aiohttp Unix socket** 以 **OpenAI Chat Completions HTTP/SSE** 格式通信：

- **AI socket**: Session 作为客户端访问，`POST /v1/chat/completions`
- **Channel socket**: Session 作为服务端，`POST /v1/chat/completions`

SSE 流中的特殊字段：
- `delta.content` — AI 最终文本回复
- `delta.reasoning_content` — 聚合了 AI thinking + tool_call 意图 + tool_call 结果
- `delta.tool_calls` — 部分 tool call 定义（流式累积）

错误响应有两种形式：

1. **非流式（HTTP 层面）**：请求解析失败等，在 `response.prepare()` 之前返回
   ```json
   {"error": {"message": "...", "type": "...", "code": "..."}}
   ```

2. **流式（SSE 层面）**：已 commit HTTP 200 后发生的错误（上游异常、连接断开等），使用 ChatCompletionChunk 格式
   ```json
   {"id": "error", "choices": [{"index": 0, "delta": {"content": "[Upstream Error 401]: ..."}, "finish_reason": "error"}]}
   ```
   所有层统一使用 `finish_reason="error"` 标记流式错误，Session 检测到后不写入 conversation history。

## 日志约定

- 所有模块使用 `from loguru import logger`
- 默认 INFO 级别，`--verbose` 开启 DEBUG
- DEBUG 必须覆盖：每个 SSE chunk、tool 执行、锁获取/释放
- 格式：`时间 | 级别 | 模块:函数:行号 - 消息`
- Channel 客户端使用 `rich.console.Console` 做终端输出，**禁止使用 `print()`**

## 关键注意事项（踩坑经验）

以下是开发过程中遇到的、容易忽略或出错的点：

1. **Socket 文件残留**：进程退出后 `.sock` 文件不会自动删除。重启时必须先 `rm` 或 `unlink()`。测试中 `tmp_path` 自动清理，生产环境需自行管理

2. **`anyio.Path` vs `pathlib.Path`**：两者不兼容。`anyio.Path` 的 IO 方法（`exists()`, `read_text()`, `glob()`）需要 `await`。需要 `pathlib.Path` 时用 `Path(str(anyio_path))` 转换，反之用 `anyio.Path(str(pathlib_path))`

3. **stderr PIPE 阻塞**：`subprocess.PIPE` 必须消费完内容，否则子进程 hang。已全面改用 `anyio.open_process`，其 stderr 为异步流

4. **Subprocess 替代方案**：任何时候都不要在 async 函数中直接调用 `subprocess.Popen` / `subprocess.run` / `time.sleep` / `Path.exists()`。对应替代：
   | 同步 API | 异步替代 |
   |----------|----------|
   | `subprocess.Popen()` | `await anyio.open_process()` |
   | `subprocess.run()` | `await anyio.run_process()` |
   | `time.sleep()` | `await anyio.sleep()` |
   | `Path.exists()` | `await anyio.Path().exists()` |

5. **System prompt 容错**：`system_prompt_builder()` 可能抛异常或返回 None。启动阶段必须 catch 异常，不影响 session 启动（此时 system_prompt 为 None）

6. **Tool 函数必须 awaitable**：`load_tools_from_workspace` 只加载 `async def` 函数。普通函数会被跳过并打印 warning

## 测试约定

- **框架**: `pytest` + `pytest-asyncio`（`asyncio_mode = "auto"`，anyio backend）
- **异步测试**: `@pytest.mark.anyio`
- **测试目录结构**: 镜像 `src/psi_agent/`
- **集成测试**: 独立目录 `tests/integration/`，用 `tests/__init__.py` 和 `tests/integration/__init__.py` 使 test 目录成为 package
- **无需 conftest path hack**: `uv sync` 将 psi-agent 安装为 editable package，`import psi_agent` 直接可用
- **Mock AI socket**: `aiohttp.web.Application` + `UnixSite`/`SockSite`（获取随机端口用预绑定 socket）
- **真实 API 测试**: 通过环境变量 `PSI_TEST_OPENAI_*` / `PSI_TEST_ANTHROPIC_*` 注入凭证，未设置时自动 skip
- **所有 async 操作使用 anyio**: 禁止在 async 上下文中直接调用 `subprocess`、`time.sleep`、`pathlib.Path` 方法。详见上方"关键注意事项"第 4 条

### 集成测试 Mock Server

- `MockAIServer` 在 conftest.py 中定义，通过 pytest fixture 提供
- Mock server **对每个请求返回完全相同的 chunks 列表**。需要 per-request 差异化响应时，使用 inline mock server + `nonlocal` 计数器

示例——per-request 差异化：

```python
req_count = 0
async def handler(request):
    nonlocal req_count
    req_count += 1
    if req_count == 1:
        # 返回 tool_calls
    else:
        # 返回最终文本
```

- 集成测试中 `assert _wait_for_socket()` 会轮询直到 socket 创建。注意 socket 创建 ≠ 服务就绪，需要额外 `await anyio.sleep(0.3)` 确保 accept 就绪

## Lint / Type Check 约定

- **ruff**: `select = ["E", "F", "I", "W", "UP", "ASYNC", "SIM", "C4", "B", "RUF", "N", "T20", "PLC"]`
- **ty**: 全局 `ty check .`
- **per-file-ignores**: **零条**。所有代码通过自身符合规则，不靠抑制
- **仅 2 处 ty:ignore**（无法避免）：
  - `cli.py:29` — tyro.cli() 的 `Annotated[Union[...]]` 类型推断局限
  - `conftest.py:109` — pytest async generator fixture 的返回类型局限（`yield` 导致函数被推断为 AsyncGenerator，与标注的 MockAIServer 冲突）

`cast` 不能解决 conftest 的问题——`cast` 是表达式级工具，无法修改 async generator 函数的返回类型。`# ty: ignore` 是正确的标准解法。

## 类型注解约定

- 使用 `from __future__ import annotations` 在所有文件
- `X | None` 而非 `Optional[X]`
- `list[X]` 而非 `List[X]`（Python 3.14 原生）
- 禁止使用 raw `any`——始终用 `typing.Any`
- `anyio.abc.ByteStream` → 用 `Any` 代替（ty 不识别的第三方类型）

## 开发命令

```bash
uv run ruff check .              # lint 检查
uv run ruff check --fix .        # auto-fix
uv run ruff format .             # 格式化
uv run ruff format --check .     # 格式检查
uv run ty check                  # 类型检查
uv run pytest -v                 # 全部测试
uv run psi-agent --help          # CLI 帮助
uv build                         # 构建
```

## 未来扩展方向

- [ ] 单进程中运行多个 session 实例（利用 anyio task group）
- [ ] workspace.py 统一 workspace 管理
- [ ] 更多 channel 类型（WebSocket、HTTP API 等）
- [ ] 更多 AI 后端（Gemini、本地模型等）
- [ ] Session history 持久化（可选）
- [ ] Channel 广播/多客户端队列
