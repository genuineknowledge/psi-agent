# AGENTS.md

本文档面向后续开发者（人或 AI Agent），说明 psi-agent 的设计思路、代码结构、开发约定以及我们在开发过程中沉淀的最佳实践。

## 设计理念

psi-agent 是一个**微内核**式的 agent 框架。核心理念是：

1. **最小化核心**: 框架本身只提供通信协议、组件组合和 tool/Schedule 加载机制
2. **功能由 workspace 定义**: agent 的能力（tools、system prompt、定时任务）完全由 workspace 目录中的文件定义
3. **组件无状态**: AI 后端不保存任何状态；Session 只维护一个内存中的 history；Channel 不管理历史
4. **组合优于继承**: 三个独立组件通过 socket 任意组合
5. **一切异步**: 所有 IO 操作使用 `anyio`，永不使用 `asyncio` 原生 API 或 `pathlib`
6. **零抑制**: 不堆 `noqa`，不设 `per-file-ignores`。代码本身应符合规则
7. **显式单 choice 模型**: Session 和 AI 之间每 SSE chunk 保证恰好 1 个 choice。多 choice 作为错误处理，0 choice 静默跳过（心跳）
8. **zero `sys.exit`**: 所有 `run()` 方法必须可作为协程嵌入任意 event loop 中运行，不仅限于 tyro CLI 上下文。错误用 `raise`，禁止 `sys.exit(1)`
9. **`setup_logging` 第一行**: 每个组件的 `async def run(self)` 方法中，`setup_logging(verbose=self.verbose)` 必须是第一行可执行语句，先于任何 token 解析或参数校验
10. **参数透传**: Channel 请求中除 `messages` 外的不认识参数全部穿透到 AI 层，不丢失
11. **类型精确化**: 避免裸 `tuple`/`dict`。尽量用 `tuple[X, Y]` 或具体类型（如 `aiohttp.BaseConnector`）
12. **关键字参数风格统一**: `__init__` 参数顺序 ≡ 初始化赋值顺序。所有 connector 使用显式 `path=`/`ssl=` 等关键字
13. **可取消**: 所有 `run()` 协程必须可在外部被 cancel，`finally` 块清理资源（close socket / stop bot / shutdown updater）

## 架构决策记录

以下是设计过程中有意为之的关键决策：

**为什么用 Unix socket 而非 TCP？**
Socket 文件天然隔离——不同项目用不同文件路径，互不干扰。没有端口冲突，没有防火墙问题。本地组件通信不需要网络栈开销。

**为什么 AI 是 Server、Session 是 Client？**
AI 后端无状态，不保存任何信息。多个 Session 可以共享同一个 AI backend。如果反过来（Session 是 Server），每个 Session 都要自行配置上游 API，违反"组合"原则。

**为什么 Session history 持久化为 JSONL？**
JSONL 格式零依赖，逐行追加读写简单。文件按 `workspace/histories/{session_id}.jsonl` 存储（workspace 默认当前目录），`session_id` 可由 CLI 传入以 resume 之前的会话。`SessionAgent.run()` 每次调用通过 ``async with self._conversation`` 进入上下文管理器——``Conversation`` 的 ``add / commit / rollback`` 实现回合级原子性。仅在回合成功完成（stop / tool_calls 全部执行 / unexpected finish / max rounds）时落盘；异常时 ``__aexit__`` 自动 ``rollback()`` 恢复内存到快照，磁盘不落地任何新消息。

**为什么 socket 文件不自动 unlink？**
支持热换 Server。每个 `session.post()` 新建 TCP/Unix 连接，由 `UnixConnector` 按路径重新 connect。只要新的服务进程绑定到同一 socket 路径，客户端无需重启即可继续通信。auto-unlink 会破坏这个能力——socket 文件需要保留，由新进程手动接管。

## 技术栈

| 领域 | 技术 |
|------|------|
| 异步 | `anyio`（禁止使用 `asyncio` 原生 API、`pathlib`） |
| HTTP | `aiohttp`（Unix socket / TCP / Named Pipe） |
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
    ├── _sockets.py             # 共享 socket 工具（prefix-based transport 解析）
    ├── _run.py                 # YAML 配置批量启动（psi-agent run config.yml）
    ├── _logging.py              # loguru 配置，verbose→DEBUG
    ├── ai/
    │   ├── AGENTS.md                # AI 层设计文档
    │   ├── __init__.py               # Ai + serve_ai
    │   └── server.py                 # handler（请求处理）
    ├── router/
    │   ├── AGENTS.md                 # 语义路由层设计文档
    │   ├── __init__.py               # AiRouter + serve_router 生命周期
    │   ├── models.py                 # upstream 配置与路由决策类型
    │   ├── selector.py               # description-only 路由选择
    │   └── server.py                 # 默认回退与 SSE 字节流代理
    ├── session/
    │   ├── AGENTS.md                # Session 层设计文档
    │   ├── __init__.py             # Session dataclass + run()，入口编排
    │   ├── server.py               # serve_session — aiohttp HTTP/SSE scaffold
    │   ├── channel_adapter.py       # ChannelAdapter — 纯无状态编解码（parse_request + write）
    │   ├── agent.py                # SessionAgent — agent loop + 编排（委托给 4 个组件）
    │   ├── tool_registry.py        # ToolRegistry — 工具集（加载/重载/查询）
    │   ├── conversation.py         # Conversation — 对话历史 + 持久化
    │   ├── system_prompt.py        # SystemPrompt — 系统 prompt 生命周期
    │   ├── schedule_registry.py    # ScheduleRegistry — 定时任务集
    │   ├── ai_client.py            # AiClient — AI 侧协议适配（HTTP/SSE → AiDelta）
    │   ├── protocol.py             # Session 层类型
    ├── channel/
    │   ├── AGENTS.md                # Channel 层设计文档
    │   ├── __init__.py              # package marker
    │   ├── _types.py               # FileChunk, TextChunk, ReasoningChunk, InputChunk, OutputChunk
    │   ├── _errors.py              # ChannelError 异常基类
    │   ├── _markers.py             # [RECV:]/[SEND:] 标记协议（纯函数 encode_input + 有状态扫描器 SendMarkerScanner）
    │   ├── _stream.py              # SSE 解析 iter_sse_events + interval 缓冲 StreamBuffer（与传输解耦）
    │   ├── _core.py                # ChannelCore — 连接管理 + post() 编排
    │   ├── repl/                   # 交互式 REPL thin client
    │   ├── cli/                    # 单次消息 CLI thin client
    │   ├── telegram/               # Telegram bot channel
    │   ├── feishu/                 # Feishu bot channel
    └── gateway/
        ├── AGENTS.md                # Gateway 层设计文档
        ├── __init__.py              # Gateway dataclass + run()
        ├── _manager.py             # 共享类型 + helpers
        ├── _ai_manager.py         # AIManager
        ├── _session_manager.py    # SessionManager
        ├── _title_manager.py       # 会话标题 CRUD + AI 生成
        ├── _state.py               # GatewayState — 状态持久化 (state/latest.json)
        ├── server.py               # aiohttp REST handlers
        ├── _chat_manager.py        # SSE 流式对话管理
        ├── _history_manager.py     # JSONL 历史读取
        ├── _workspace_manager.py   # 目录浏览
        ├── _openapi.py             # OpenAPI schema 生成
        ├── _tray.py                # 系统托盘图标 (pystray)
        ├── _webview.py            # 原生 webview 窗口 (pywebview)
        └── spa/                    # Vue 3 SPA 前端（Vite + SFC）
```

项目使用 **src-layout**（`src/psi_agent/`），由 `uv sync` 安装为 editable package。

各层的详细设计文档见：
- **AI 层**: `src/psi_agent/ai/AGENTS.md` — provider 配置、请求透传、错误处理
- **Router 层**: `src/psi_agent/router/AGENTS.md` — description-only 选择、默认回退、SSE 代理
- **Session 层**: `src/psi_agent/session/AGENTS.md` — workspace 启动、agent loop、tool 加载调用、schedule 机制、history 持久化
- **Channel 层**: `src/psi_agent/channel/AGENTS.md` — ChannelCore 公共部件、REPL/CLI/Telegram/Feishu 约定
- **Gateway 层**: `src/psi_agent/gateway/AGENTS.md` — 生命周期管理、REST API、Web Console SPA、CI 打包

## 核心通信协议

所有组件通过 **aiohttp** 以 **OpenAI Chat Completions HTTP/SSE** 格式通信。传输支持 Unix socket、TCP、Windows Named Pipe，由地址前缀自动检测（`psi_agent._sockets`）：

- **AI socket**: Session 作为客户端访问，`POST /chat/completions`
- **Channel socket**: Session 作为服务端，`POST /chat/completions`

SSE 流中的特殊字段：
- `delta.content` — AI 最终文本回复
- `delta.reasoning` — 聚合了 AI thinking + tool_call 意图 + tool_call 结果
- `delta.tool_calls` — 部分 tool call 定义（流式累积）

错误响应有两种形式：

1. **非流式（HTTP 层面）**：请求解析失败等，在 `response.prepare()` 之前返回
   ```json
    {"error": {"message": "...", "type": "...", "param": null, "code": 400}}
   ```

2. **流式（SSE 层面）**：已 commit HTTP 200 后发生的错误（上游异常、连接断开等），使用 ChatCompletionChunk 格式
   ```json
   {"id": "error", "choices": [{"index": 0, "delta": {"content": "[Upstream Error]: ..."}, "finish_reason": "error"}]}
   ```
   所有层统一使用 `finish_reason="error"` 标记流式错误，Session 检测到后不写入 conversation history。

> `finish_reason="error"` 是 psi-agent 的扩展，不在 OpenAI 标准枚举内（标准仅 `stop`/`length`/`tool_calls`/`content_filter`/`function_call`）。仅用于内部层间通信，不暴露给外部。

## 日志约定

- 所有模块使用 `from loguru import logger`
- 默认 INFO 级别，`--verbose` 开启 DEBUG
- DEBUG 必须覆盖：每个 SSE chunk、tool 执行、锁获取/释放
- 格式：`时间 | 级别 | 模块:函数:行号 - 消息`
- Channel 客户端使用 `rich.console.Console` 做终端输出，**禁止使用 `print()`**
- **`setup_logging` 一次性生效（刻意设计）**：用全局 `_handler_id` 守卫，首次调用安装 handler，后续调用直接返回旧 handler，**不会**重新应用 `verbose`。因此“谁先调用谁定级别”。在 `psi-agent run`（批量模式）下，`Run.run()` 先于所有子组件调用 `setup_logging(verbose=True)`，故批量模式始终为 DEBUG，各组件配置里的 `verbose` 字段被有意忽略。单独启动某个组件（`psi-agent ai/session/channel ...`）时，则由该组件自己的 `verbose` 决定级别。

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

5. **System prompt 容错**：`system_prompt_builder()` 可能抛异常或返回 None。首次 `run()` 调用时必须 catch 异常，不影响后续对话（此时 history 中没有 system 消息）

6. **Tool 函数必须 awaitable**：`load_tools_from_workspace` 只加载 `async def` 函数。普通函数会被静默跳过

7. **JSON dict/list 必须 guard**：从 `json.loads()` 得到的任意数据访问 `c.get("delta")` 或 `messages[-1]` 前，必须先 `isinstance(c, dict)` / `isinstance(messages, list)` 验证类型。JSON 可以是任意结构，不可信任 key 存在或类型正确。

8. **Default over None**：与其在调用处检查 `if x is None: return`，不如在构造时提供合理默认值（如 `SystemPrompt` 的 default builder 返回 `""`，default checker 返回 `False`）。这样调用处逻辑更简单、更不容易漏判 None。

9. **Hash 的 key 必须和查找时一致**：如果 load 时用 `file_path → hash` 存储，refresh 时就不能用 `tool_name → hash` 查找。key 的语义必须全程一致，否则永远命中不了。

10. **每 chunk 都要有 DEBUG 日志**：无论是 AI 返回的 SSE chunk 还是 Channel 发出的 SSE chunk，每经过协议边界都要记录。这匹配 `ai/server.py` 的 `logger.debug(f"SSE chunk: ...")` 模式。

11. **单个 caller 的 private 方法应内联**：只有一个调用点的私有方法没有存在理由——将其逻辑直接展开到调用处，减少阅读时的跳转。(如 `_build` → inline 到 `ensure`)

12. **模块级函数应尽量放到类上**：如果整个文件的作用就是为一个类服务，工具函数应该作为该类的 `@staticmethod`，而非文件顶级函数。(如 `_extract_async_func` → `SystemPrompt._extract_async_func`)

13. **动态加载 .py 文件用 `compile` + `exec`，禁止 `importlib`**：Python 3.14 的 `importlib.util.exec_module` 生成的 `.pyc` 默认是 timestamp+size 验证（非 hash-based）。热重载场景下源文件修改后 size 常不变，`exec_module` 会复用陈旧 bytecode。正确做法：`source = read_text()` → `compile(source, path, 'exec')` → `exec(compiled, module.__dict__)`。参见 `ToolRegistry._load_from_dir` 和 `SystemPrompt._load_module`。

14. **Startup 失败也需 shield cleanup**：不仅是 shutdown 的 `finally` 需要 `CancelScope(shield=True)` 保护 `runner.cleanup()`，`setup()`/`start()` 失败的 `except` 块同理。参照 `serve_ai` 的模式。

15. **Log 中两处同类操作应格式一致**：如 build prompt 和 rebuild prompt 都应该 log `({len(sp)} chars)`，否则排查时信息不对等。

16. **消费 async generator 必须用 `aclosing()`**：`async for` 在提前退出或被 cancel 时不调用 generator 的 `aclose()`，导致 generator 内 `async with` 持有的资源（aiohttp 连接、文件句柄等）被遗弃给 GC。正确做法：`async with aclosing(gen) as g: async for chunk in g: ...`。对标 `ai/server.py` 的 `finally` + shielded `aclose()` 模式。参见 `agent.py`、`channel_adapter.py`、`schedule_registry.py`。

## 测试约定

- **框架**: `pytest` + `pytest-asyncio`（`asyncio_mode = "auto"`，anyio backend）
- **异步测试**: `@pytest.mark.anyio`
- **测试目录结构**: 镜像 `src/psi_agent/`（如 `ai/server.py` → `tests/psi_agent/ai/test_server.py`）
- **整个 `tests/` 树是 package**: 每层目录都放 `__init__.py`（`tests/__init__.py`、`tests/psi_agent/__init__.py`、`tests/psi_agent/ai/__init__.py`……）。这样 pytest 以**全限定模块名**导入测试，不同目录下允许同名文件并存（如 `ai/test_server.py` 与 `session/test_server.py`）。**漏掉某层 `__init__.py`**会让同名 test 文件在默认 prepend import 模式下被当成顶层同名模块，触发 `import file mismatch` 冲突
- **集成测试**: 放在独立目录 `tests/integration/`（同样含 `__init__.py`）
- **无需 conftest path hack**: `uv sync` 将 psi-agent 安装为 editable package，`import psi_agent` 直接可用
- **Mock AI socket**: `aiohttp.web.Application` + `UnixSite`/`SockSite`（获取随机端口用预绑定 socket）
- **`@pytest.mark.schedule`**：标记需要 >30s 的 schedule 相关测试，`pytest -m "not schedule"` 跳过
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
- **核心代码（`src/` + `tests/`）仅 7 处 ty:ignore**（无法避免）：
  - `tests/integration/conftest.py:112` — pytest async generator fixture 的返回类型局限（`yield` 导致函数被推断为 AsyncGenerator，与标注的 MockAIServer 冲突）
  - `src/psi_agent/gateway/server.py:257` — `anyio.to_thread.run_sync(file_field.file.read)` 返回类型 Any，ty 无法推断
  - `src/psi_agent/gateway/__init__.py:152,167,169`（3 处）— `anyio.to_thread.run_sync(webbrowser.open, ...)` / `anyio.to_thread.run_sync(tray.wait_stop, ...)` / `anyio.to_thread.run_sync(wv.wait_closed, ...)` 同上
  - `src/psi_agent/gateway/_webview.py:40`（1 处）— `events.closing` 无法解析，因 webview 由 `__import__("webview")` 动态导入
  - `src/psi_agent/channel/cli/client.py:16` — `anyio.to_thread.run_sync(sys.stdin.read)` 同上
- **例外**：`examples/` 下的示例 workspace（如 `a-serper-mcp-workspace/tools/_mcp.py`）含若干 `# ty: ignore`（动态 MCP 工具的运行时签名构造），属示例代码，不计入上述核心约定。

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

## 改动后自检清单（Definition of Done）

任何代码改动完成后、提交前，必须逐条核对以下四项：

1. **文档同步**：检查 `AGENTS.md`（含各层 `*/AGENTS.md`）、`README.md` / `README_en.md`、`docs/`、`specs/`、`plans/` 中是否有因本次改动而过时或缺失的内容。凡改了行为 / 协议 / 配置项 / 默认值，就同步对应文档；新增任何刻意为之的「反直觉」行为，必须在 AGENTS.md 留痕，避免后人误当 bug 修掉。

2. **日志粒度对齐**：检查 loguru 日志是否完整——不要漏掉应有的日志（关键分支、IO、错误、生命周期）。新增日志的 level 必须与**周围既有代码**保持一致：每个 SSE chunk / tool 执行 / 锁获取释放走 DEBUG，启动 / 关闭 / 请求完成走 INFO，可恢复异常走 WARNING，不可恢复错误走 ERROR。不要凭空拔高或压低 level。

3. **异常与取消安全**：检查改动点及其邻近代码是否异常安全——被 `cancel` 时会不会出问题？是否存在 cancel 时资源泄露（未关闭的 socket / `AppRunner` / 文件 / 子进程 / 上游 streaming 连接）？清理代码必须放在 `finally`、`except` 或 `async with` 上下文管理器（`__aexit__`）中，跨 `await` 的清理用 `anyio.CancelScope(shield=True)` 保护。注意 `CancelledError` 是 `BaseException`，不在 `Exception` 之下——`except Exception` 不会（也不应）吞掉它；严禁用 `except BaseException` 误吞取消信号。

4. **测试补充**：为新增 / 变更的逻辑补 unit test；涉及跨组件交互（socket、SSE、agent loop、错误传播）的补 integration test。测试目录镜像 `src/psi_agent/`，集成测试放 `tests/integration/`。改完后跑 `uv run pytest` 确认通过。

## 未来扩展方向

- [x] 单进程中运行多个 session 实例（利用 anyio task group）— 通过 Gateway 实现
- [ ] workspace.py 统一 workspace 管理
- [x] 更多 channel 类型 — Gateway REST API + Web Console SPA
- [ ] 更多 AI 后端（Gemini、本地模型等）
- [x] Session history 持久化（已完成）
- [ ] Channel 广播/多客户端队列
