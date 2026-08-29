# AGENTS.md

本文档面向后续开发者（人或 AI Agent），说明 psi-agent 的设计思路、代码结构、开发约定以及我们在开发过程中沉淀的最佳实践。

## 设计理念

psi-agent 是一个**微内核**式的 agent 框架。核心理念是：

1. **最小化核心**: 框架本身只提供通信协议、组件组合和 tool/Schedule 加载机制
2. **功能由 agent 包定义**: tools、system prompt 从 **agent** 目录加载（``Session.agent``；空则与 workspace 同根兼容）。用户 **workspace** 是打开目录（相对文件 IO），**定时任务 `schedules/` 例外地从 workspace 加载**（见坑 17）；**AppData** 是进程记忆区（todos / history / Gateway state）
3. **组件无状态**: AI 后端不保存任何状态；Session 只维护一个内存中的 history（落盘在 AppData）；Channel 不管理历史
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
JSONL 格式零依赖，逐行追加读写简单。现路径为 AppData ``{appdata}/histories/{session_id}.jsonl``（legacy ``{workspace}/histories/`` 双读），`session_id` 可由 CLI 传入以 resume。`SessionAgent.run()` 每次调用通过 ``async with self._conversation`` 进入上下文管理器——``Conversation`` 的 ``add / commit / rollback`` 实现回合级原子性。仅在回合成功完成（stop / tool_calls 全部执行 / unexpected finish / max rounds）时落盘；异常时 ``__aexit__`` 自动 ``rollback()`` 恢复内存到快照，磁盘不落地任何新消息。细节见 `session/AGENTS.md` / `gateway/AGENTS.md`。

**为什么拆 agent / workspace / AppData 三区？**
能力包（tools / system）与用户打开目录、进程记忆区解耦：同一 agent 可挂多个 workspace；定时任务 `schedules/` 跟着 **workspace** 走（同一 agent 挂不同 workspace 应有各自的提醒，见坑 17）；todos / history / Gateway `state/` 进 AppData（`platformdirs` / `--appdata` / `PSI_APPDATA`），避免写进用户项目树。路径助手在 ``psi_agent._appdata``（跨 Session/Gateway，避免循环导入）；**禁止**把 AppData 根塞进 Session ContextVar。分层细节见各层 `AGENTS.md`。

**为什么 socket 文件不自动 unlink？**
支持热换 Server。每个 `session.post()` 新建 TCP/Unix 连接，由 `UnixConnector` 按路径重新 connect。只要新的服务进程绑定到同一 socket 路径，客户端无需重启即可继续通信。auto-unlink 会破坏这个能力——socket 文件需要保留，由新进程手动接管。

**Workflow 的形式语言与执行边界是什么？**
Workflow 是由 `FusionFlow.g4` 定义的形式语言工作流系统。Haitun workspace
的 `workflow` Skill 负责其声明式源码；parser/compiler 将源码编译为
`fusion_flow.workflow_graph` 的 Step–Artifact 图，`fusion_flow.workflow_execution`
生成并执行可检查的计划。workspace runner 在计划之上分派 Agent 和 Program，并用
checkpoint + `run_flow_resume` 处理 Human 的跨回合等待。不含 Human Step 的工作流在
首次 `run_flow` 调用内完成；Human 工作流只通过保存的请求继续。各类 Step 只能使用
runner 注入的受限能力，外层 Session 仍须先收集完整的输入 Artifact。旧 Node/Fuclaw
runtime 位于 `fusion-flow-legacy`，只处理显式 `.flow.ts` 兼容请求。

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
    ├── _appdata.py             # AppData 路径助手（todos/history/state；Session↔Gateway 共享）
    ├── protocol.py             # 跨组件 SSE 协议归属（线格式类型 + finish_reason 常量 + 辅助帧/终止帧规则）
    ├── _feishu_routing.py      # 飞书群聊/私聊判定与路由键（Gateway↔Channel 共享）
    ├── _send_markers.py        # [SEND:] 解码：正则 + 空路径过滤（Channel↔Session 共享）
    ├── _run.py                 # YAML 配置批量启动（psi-agent run config.yml）
    ├── _logging.py              # loguru 配置，verbose→DEBUG
    ├── _tls.py                  # 出站 HTTPS 的 TLS 上下文（AuthManager↔AI 层共享；绕开 PQ ClientHello 被丢）
    ├── ai/
    │   ├── AGENTS.md                # AI 层设计文档
    │   ├── __init__.py               # Ai + serve_ai
    │   └── server.py                 # handler（请求处理）
    ├── session/
    │   ├── AGENTS.md                # Session 层设计文档
    │   ├── __init__.py             # Session dataclass + run()，入口编排
    │   ├── server.py               # serve_session — aiohttp HTTP/SSE scaffold
    │   ├── channel_adapter.py       # ChannelAdapter — 纯无状态编解码（parse_request + write）
    │   ├── agent.py                # SessionAgent — agent loop + 编排（委托给 4 个组件）；AgentRun = chunk 流 + 终态
    │   ├── tool_registry.py        # ToolRegistry — 工具集（加载/重载/查询）
    │   ├── conversation.py         # Conversation — 对话历史 + 持久化
    │   ├── system_prompt.py        # SystemPrompt — 系统 prompt 生命周期
    │   ├── schedule_registry.py    # ScheduleRegistry — 定时任务集
    │   ├── ai_client.py            # AiClient — AI 侧协议适配（HTTP/SSE → AiDelta）
    │   ├── trigger_registry.py     # TriggerRegistry — 事件触发器集（与 schedule 平行）
    │   ├── live_agent.py           # 按 session id 登记在服务中的 SessionAgent；脱离轮次的活据此续跑一个回合
    │   ├── runtime_context.py      # 本轮 session id / workspace / agent 路径（ContextVar；工具侧只读）
    │   ├── history_display.py      # 消息 `kind` provenance + `/history` 展示白名单 + AI 请求投影
    │   ├── event_protocol.py       # 事件薄信封校验（`source`/`event`/`payload`；无业务 catalog）
    │   ├── file_serving.py         # 会话文件下载的路径归约（限定在根内）
    │   ├── protocol.py             # Session 专属类型（含 `AgentRunResult`）+ 重导出 `psi_agent.protocol` 共享定义
    ├── router/
    │   ├── AGENTS.md               # Router 层设计与不变量
    │   ├── entry.py                # Router 统一入口（routing / aggregation / fallback）
    │   ├── client.py               # Socket-aware Chat Completions/SSE 客户端
    │   ├── server.py               # 共享 HTTP/SSE 服务边界
    │   ├── routing/                # Selector 单目标分流 + 工具链 sticky
    │   ├── aggregation/            # 全候选并发广播 + 专用 Aggregator 汇总
    │   └── fallback/               # 全候选按序尝试 + 首个完整成功响应重放
    ├── channel/
    │   ├── AGENTS.md                # Channel 层设计文档
    │   ├── __init__.py              # package marker
    │   ├── _types.py               # FileChunk, TextChunk, ReasoningChunk, InputChunk, OutputChunk
    │   ├── _errors.py              # ChannelError 异常基类
    │   ├── _markers.py             # [RECV:] 标记 + encode_input + 有状态扫描器 SendMarkerScanner（[SEND:] 解码重导出自 `_send_markers`）
    │   ├── _stream.py              # SSE 解析 iter_sse_events（含 IDLE 静默上报）+ interval 缓冲 StreamBuffer（与传输解耦）
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
        ├── _free_model.py          # 免费模型哨兵 key → 登录 token（同源校验）
        ├── _session_manager.py    # SessionManager
        ├── _scheduler_manager.py  # SchedulerManager — 每 workspace 一个全量激活的调度 Session（触发其 schedules/）
        ├── _router_manager.py      # RouterManager — 内部语义路由服务注册表
        ├── _feishu_manager.py      # FeishuManager — 飞书 open_id → Session 路由
        ├── _oauth_manager.py       # OAuthRelay — OAuth 回调中继（免手抄授权码）
        ├── _title_manager.py       # 会话标题 CRUD + AI 生成
        ├── _state.py               # GatewayState — 状态持久化 (state/latest.json)
        ├── server.py               # aiohttp REST handlers
        ├── _chat_manager.py        # SSE 流式对话管理
        ├── _history_manager.py     # JSONL 历史读取
        ├── _workspace_manager.py   # 目录浏览
        ├── _openapi.py             # OpenAPI schema 生成
        ├── _attention.py           # AttentionHub — tray/webview 注意力提示
        ├── _tray.py                # 系统托盘图标 (pystray)
        ├── _webview.py            # 原生 webview 窗口 (pywebview)
        ├── spa/                    # Vue 3 SPA v1（Vite + SFC）
        └── spa-v2/                 # React SPA v2（任务工作台；默认 GET /）
```

项目使用 **src-layout**（`src/psi_agent/`），由 `uv sync` 安装为 editable package。

`scripts/` 放不属于包的仓库工具。目前一个：`gen_legal_html.py` 把 `spa-v2/legal/` 下两份协议 md 生成为 `spa-v2/public/{terms,privacy}.html`，**安装器协议页与产品内协议链接共用这一份产物**（安装器以 `dontcopy` 引同一路径）。它是生成物、入库、CI 用 `--check` 守同步；设计见 `docs/superpowers/specs/2026-08-15-installer-tos-consent-design.md`。

各层的详细设计文档见：
- **AI 层**: `src/psi_agent/ai/AGENTS.md` — provider 配置、请求透传、错误处理、context compaction 触发
- **Session 层**: `src/psi_agent/session/AGENTS.md` — workspace 启动、agent loop、tool 加载调用、schedule 机制、history 持久化、context compaction
- **Router 层**: `src/psi_agent/router/AGENTS.md` — 单目标分流、广播聚合、Fallback、组合与 SSE/隐私/取消不变量
- **Channel 层**: `src/psi_agent/channel/AGENTS.md` — ChannelCore 公共部件、REPL/CLI/Telegram/Feishu 约定
- **Gateway 层**: `src/psi_agent/gateway/AGENTS.md` — 生命周期管理、REST API、Web Console SPA、CI 打包

## 核心通信协议

所有组件通过 **aiohttp** 以 **OpenAI Chat Completions HTTP/SSE** 格式通信。传输支持 Unix socket（仅 POSIX）、TCP、Windows Named Pipe（仅 Windows），由地址前缀自动检测（`psi_agent._sockets`）；平台与地址不匹配时抛 `ValueError` 快速失败，详见「关键注意事项」第 17 条：

- **AI socket**: Session 作为客户端访问，`POST /chat/completions`；可直连 AI，也可指向 Router 的 `session_socket`
- **Router upstream socket**: Router 作为客户端访问 Selector/Aggregator、候选 AI 或显式嵌套 Router 的 `POST /chat/completions`
- **Channel socket**: Session 作为服务端，`POST /chat/completions`

SSE 流中的特殊字段：
- `delta.reasoning` — 过程流（刻意压缩）：AI thinking + tool 进度仍走同一槽，便于 Session 出口与 AI 层 OpenAI 形协议同构复用；用正交字段 ``delta.kind``（`thinking` / `tool_call` / `tool_result`）供 UI 白名单渲染（Cursor 风进程行只订 tool_*，默认不晒 thinking）
- `delta.content` — AI 最终文本回复
- `delta.tool_calls` — 部分 tool call 定义（流式累积；Agent 侧协议，与 UI 的 tool 进度 `kind` 不同）
- `delta.kind` — 仅当本帧带 `reasoning` 时有效的 provenance（见上）

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

3. **Compaction 信号（SSE 层面）**：Token 用量超过 `max_context_tokens` 阈值时，AI 层在上游 stream 结束后发送额外 SSE 事件，通知 Session 触发 context compaction：
   ```json
   {"choices": [{"delta": {}, "finish_reason": "compaction_needed"}],
    "psi_compaction": {"needed": true, "prompt_tokens": N, "threshold": M}}
   ```
   `psi_compaction` 和 `finish_reason="compaction_needed"` 均为 psi-agent 内部扩展。
   其中 `prompt_tokens` / `threshold` **不是纯日志字段**：Session 用它们做压缩冷却判断
   （`session/AGENTS.md`「压缩冷却」），省略会让冷却退化成 fail-open、退回连续重压。

4. **Usage 信号（SSE 层面）**：provider 的流式 token 用量通常位于 `choices=[]` 的尾帧，
   而内部协议把 0 choice 当心跳跳过。AI 层因此把最后一份有效 usage 规范化为单 choice 辅助帧：
   ```json
   {"choices": [{"delta": {}, "finish_reason": "usage"}],
    "psi_usage": {"prompt_tokens": N, "completion_tokens": M, "total_tokens": T}}
   ```
   `usage` 属于 `AUXILIARY_FINISH_REASONS`，不得覆盖真实终止原因。Session 在一次 Agent run
   内累加每轮模型请求；任一轮没有有效 usage 时，聚合 token 值保持未知，不能把已知部分当精确总量。

### 协议归属

上述格式的**唯一定义处**是 `psi_agent/protocol.py`（与五个组件平级，因为它描述的是层与层之间的约定）：

| 层次 | 内容 |
|------|------|
| 格式层 | `DeltaMessage` / `StreamChoice` / `ChatCompletionChunk`；`make_error_chunk()` / `make_compaction_signal()` / `parse_sse_data()` |
| 常量层 | `FINISH_REASON_STOP` / `_TOOL_CALLS` / `_ERROR` / `_COMPACTION_NEEDED`、`REASONING_KIND_*`、`SSE_DONE` |
| 语义层 | `AUXILIARY_FINISH_REASONS` frozenset、`is_terminal_finish()` / `is_auxiliary_finish()` |

三条规则：

1. **新增或改动 `finish_reason` 值，只改这一个文件。** 辅助帧（不终止流、不得覆盖终止帧）加进 `AUXILIARY_FINISH_REASONS` 即全局生效——此前这条规则在 Router 里被独立实现了 5 次，每次都是人读文档后手写的 `if`。
2. **未知 `finish_reason` 视为终止**。`is_terminal_finish()` 只把辅助帧集合排除在外；`None` 既不终止也不辅助（流尚未报告结束）。
3. **解析 `data:` 行一律用 `parse_sse_data()`**。SSE 规范中 `data:` 后的空格是**可选的**，不要写 `line[6:]` 或 `startswith("data: ")`——曾有四处这么写，无空格的帧会被整帧静默丢弃。该函数只做切片：空载荷（`data:` 心跳帧）返回 `""`，绝大多数调用方应先 `if not data_str: continue` 静默跳过，别让它走到 `json.loads` 去每拍记一条 warning；例外是 `router/client.py`——它把多行 `data:` 累积后 `"\n"` join，空载荷合法地贡献一个 `""`，故那里必须判 `is not None`。`[DONE]` 也原样返回，**语义由调用方定**——`session/ai_client.py` 是 `continue`、`channel/_stream.py` 是 `return`、`gateway` 的标题/摘要两处是 `break`，三者不同，所以这个函数刻意不接管终止动作。

`session/protocol.py` 重导出这些共享定义（保持既有 import 路径有效），并额外持有 Session 专属类型（`AgentError` / `AgentRunStatus` / `AgentStopCause` / `AgentRunResult` / `AgentChunk` / `AiDelta`）。新代码优先从 `psi_agent.protocol` 导入。

`any_llm.api.ChatCompletionChunk`（`ai/server.py`）与本仓的同名 dataclass **不同源**：前者是接收上游 provider 响应的 Pydantic 模型，后者用于构造下游 SSE。刻意不统一，靠模块路径区分。

## 日志约定

- 所有模块使用 `from loguru import logger`
- 默认 INFO 级别，`--verbose` 开启 DEBUG
- DEBUG 必须覆盖：每个 SSE chunk、tool 执行、锁获取/释放
- 格式：`时间 | 级别 | 模块:函数:行号 - 消息`
- Channel 客户端使用 `rich.console.Console` 做终端输出，**禁止使用 `print()`**
- **`setup_logging` 一次性生效（刻意设计）**：用全局 `_handler_id` 守卫，首次调用安装 handler，后续调用直接返回旧 handler，**不会**重新应用 `verbose`。因此“谁先调用谁定级别”。在 `psi-agent run`（批量模式）下，`Run.run()` 先于所有子组件调用 `setup_logging(verbose=False)`（`_run.py`），故**批量模式把级别钉在 INFO**，各组件配置里的 `verbose` 字段一律被忽略——生产走的正是批量模式，所以生产**没有任何路径能开出全局 DEBUG**，要定向观测请用下面的 `PSI_DEBUG_MODULES`。单独启动某个组件（`psi-agent ai/session/channel ...`）时，则由该组件自己的 `verbose` 决定级别。（历史：这里曾是 `verbose=True`，PR #625 改成 `False`，而本文档与 `_logging.py` docstring 都直到 2026-08-25 才跟上——期间一直错写“批量模式始终为 DEBUG”。）

### 定向 DEBUG（按模块调级，不全局开）

`stderr` 之外还有第二个 sink：一个**自带轮转的文件**，只收指定模块的 DEBUG。用于观测上游模型原始 SSE 之类的场景，不必把整个进程调成 DEBUG。设计与背景见 `docs/superpowers/specs/2026-08-25-targeted-debug-logging-design.md`。

| 环境变量 | 作用 |
|---|---|
| `PSI_DEBUG_MODULES` | 模块名白名单，逗号或分号分隔。**留空即不安装文件 sink**，行为与没有此功能时逐字节一致 |
| `PSI_DEBUG_LOG_PATH` | 显式落盘文件路径（可选），优先于下面的推导。可含 `{pid}` 占位符，由本项目替换 |

落盘路径优先级：`PSI_DEBUG_LOG_PATH` → `PSI_APPDATA/logs/psi-debug-<pid>.log` → `platformdirs` 用户数据目录。轮转参数写死在 `_logging.py`：**每份 20 MB、保留 10 份、gz 压缩**，即单进程磁盘上限约 200 MB。

排查 thinking 泄漏时的典型配置：

```
PSI_DEBUG_MODULES=psi_agent.ai.server,psi_agent.channel._core
```

五条约束，改动前请先读：

1. **stderr 级别绝不受这个变量影响。** 部署环境的 docker log driver 是 `json-file` 且 **opts 为空——即 `docker logs` 那份没有任何轮转**。所以定向 DEBUG 只进文件 sink，让 `docker logs` 的量保持不变。别把定向 DEBUG 接回 stderr。
2. **`setup_logging` 里 `logger.remove()` 必须先于文件 sink 安装。** 裸 `remove()` 会清掉**所有** handler；顺序颠倒会在装完文件 sink 后立刻把它删掉，而守卫 `_file_handler_id` 已置位，于是整个进程再也装不上——且不报错。已有回归测试钉住。
3. **两个 sink 各用独立守卫**（`_handler_id` / `_file_handler_id`）。它们的输入不同：stderr 看调用方的 `verbose`，文件看进程环境。共用守卫会让“谁先调用”意外决定文件 sink 装不装。
4. **`_logging.py` 刻意不 import `_appdata.py`**：后者是 async 模块，而 `_logging` 处在依赖图最底层且零项目内依赖。代价是 appname 字面量 `"Haitun"` 在两处重复，靠交叉注释锁住。另外 `setup_logging` 是同步的、且在 `resolve_appdata_root()` **之前**执行（见 `gateway/__init__.py`），所以它**看不到 `--appdata` 命令行参数**，只认环境变量。
5. **一个进程一个文件，文件名带 PID。** 一个容器里常有多个 psi-agent 进程：生产的 `launch-gateway.sh` 是 `psi-agent gateway` 与 `psi-agent channel feishu` 并排跑，而要观测的两个模块恰好分居其中。共用一个路径会**丢行**——`enqueue=True` 只在单进程内串行化，轮转后落败的一方还会继续往被改名的 inode 里写。实测两进程写 600 行、轮转都没触发，磁盘上只剩 586 行。PID 由本项目自己拼进文件名：loguru 的 file sink **只替换 `{time}`**（见 `loguru._file_sink.FileSink._create_path`），路径里留个 `{process}` 会在首次写入时 `KeyError`。

**隐私风险（开启前必读）**：`psi-debug-<pid>.log` 里会有**真实对话内容与用户 open_id**，且刻意**不做脱敏**——打码与“看模型原始输出”直接矛盾，自我对话本身就是要看的东西。纪律：默认关闭；**查完即关**；文件不得复制出生产机、不得贴入工单或聊天；只在需要的那一个容器开。磁盘上限按**进程**算，不是按容器：gateway 容器有两个进程，开一个容器就是约 400 MB；生产一机 7 容器全开会到 2.8 G 量级。靠 `retention=10` 自动删除旧文件兜底。

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

5. **System prompt 容错**：`system_prompt_builder()` 可能抛异常或返回 None。首次 `run()` 调用时必须 catch 异常，不影响后续对话（此时 history 中没有 system 消息）。同理 `turn_context_builder()`（每回合的易变块）失败、返回非 `str` 或空串时一律**当作没有这个块**——丢一行时钟远好过丢掉整个回合（见坑 19）

6. **Tool 函数必须 awaitable**：`load_tools_from_workspace` 只加载 `async def` 函数。普通函数会被静默跳过

7. **JSON dict/list 必须 guard**：从 `json.loads()` 得到的任意数据访问 `c.get("delta")` 或 `messages[-1]` 前，必须先 `isinstance(c, dict)` / `isinstance(messages, list)` 验证类型。JSON 可以是任意结构，不可信任 key 存在或类型正确。

8. **Default over None**：与其在调用处检查 `if x is None: return`，不如在构造时提供合理默认值（如 `SystemPrompt` 的 default builder 返回 `""`，default checker 返回 `False`）。这样调用处逻辑更简单、更不容易漏判 None。

9. **Hash 的 key 必须和查找时一致**：如果 load 时用 `file_path → hash` 存储，refresh 时就不能用 `tool_name → hash` 查找。key 的语义必须全程一致，否则永远命中不了。

10. **每 chunk 都要有 DEBUG 日志**：无论是 AI 返回的 SSE chunk 还是 Channel 发出的 SSE chunk，每经过协议边界都要记录。这匹配 `ai/server.py` 的 `logger.debug(f"SSE chunk: ...")` 模式。**原文行会截断**（`_CHUNK_LOG_LIMIT`），所以 `ai/server.py` 另有一条 `delta keys: ...` 字段清单行（`_describe_delta`）：它只记字段名、长度与存在性，长度由**字段数**而非内容大小决定，故永不截断。这条线是为了让「某个 key 从未出现」与「某个 key 被截断挤掉」可分辨——两者在截断后的原文里长得一模一样。判断字段归属请用这条，别拿正则去捞原文。

    **请求侧同理**：`message census: ...`（`_describe_messages`）按 message 逐条报 role、`reasoning_content`/`reasoning`/`thinking` 的长度、`tool_calls` 数与 `content` 长度，行首给出 `n=<条数> reasoning_carriers=<带 reasoning 字段的条数>`。长度由**message 条数**而非历史大小决定。存在的理由与响应侧一致：`Request body` 那行会截断（实测生产 5 条请求全被砍在整 1000 字符、system prompt 刚开头），于是「`reasoning_content` 没发出去」与「发了但被截断挤掉」无法分辨。查 thinking 泄漏时，两条清单行要**对着看**：请求侧 `reasoning_carriers` 与响应侧 `reasoning=ABSENT` 同时为零，才说明这个通道两个方向都没开。

11. **单个 caller 的 private 方法应内联**：只有一个调用点的私有方法没有存在理由——将其逻辑直接展开到调用处，减少阅读时的跳转。(如 `_build` → inline 到 `ensure`)

12. **模块级函数应尽量放到类上**：如果整个文件的作用就是为一个类服务，工具函数应该作为该类的 `@staticmethod`，而非文件顶级函数。(如 `_extract_async_func` → `SystemPrompt._extract_async_func`)

13. **动态加载 .py 文件用 `compile` + `exec`，禁止 `importlib`**：Python 3.14 的 `importlib.util.exec_module` 生成的 `.pyc` 默认是 timestamp+size 验证（非 hash-based）。热重载场景下源文件修改后 size 常不变，`exec_module` 会复用陈旧 bytecode。正确做法：`source = read_text()` → `compile(source, path, 'exec')` → `exec(compiled, module.__dict__)`。参见 `ToolRegistry._load_from_dir` 和 `SystemPrompt._load_module`。

14. **Startup 失败也需 shield cleanup**：不仅是 shutdown 的 `finally` 需要 `CancelScope(shield=True)` 保护 `runner.cleanup()`，`setup()`/`start()` 失败的 `except` 块同理。参照 `serve_ai` 的模式。

15. **Log 中两处同类操作应格式一致**：如 build prompt 和 rebuild prompt 都应该 log `({len(sp)} chars)`，否则排查时信息不对等。

16. **消费 async generator 必须用 `aclosing()`**：`async for` 在提前退出或被 cancel 时不调用 generator 的 `aclose()`，导致 generator 内 `async with` 持有的资源（aiohttp 连接、文件句柄等）被遗弃给 GC。正确做法：`async with aclosing(gen) as g: async for chunk in g: ...`。对标 `ai/server.py` 的 `finally` + shielded `aclose()` 模式。参见 `agent.py`、`channel_adapter.py`、`schedule_registry.py`。**推论：任何包装 generator 的自定义 async iterable 必须自己转发 `aclose()`**——否则包装一层就把这条约定连同上游连接一起漏掉（`AgentRun` 因此显式实现 `aclose()`，见 `session/AGENTS.md`「运行终态」）。

17. **Windows 上裸路径地址直接拒绝（刻意为之，勿"修掉"）**：`_sockets.py` 的 `resolve_connector_and_endpoint` / `create_site` 在 `sys.platform == "win32"` 且地址落到 Unix 分支时**主动 `raise ValueError`**。因为 Windows 的 asyncio 没有 `create_unix_connection` / `create_unix_server`，若继续走 `UnixConnector` / `UnixSite`，aiohttp 会在 connect/listen 深处抛一个**不带任何上下文的 `NotImplementedError`**，极难定位（曾导致飞书 channel 每条消息崩、只显示 `generation interrupted`）。真实诱因：`channel feishu --session-socket \\.\pipe\...` 经 POSIX shell 传参时反斜杠被吞成单反斜杠 `\.\pipe\...`，匹配不上命名管道前缀而落到裸路径分支。**这是 fail-fast 前置校验，不是可删的多余检查**——非 Windows（POSIX）行为完全不变，Unix socket 照常工作。Windows/bash 下传管道地址需用四反斜杠 `'\\\\.\\pipe\\...'` 才能让程序收到两根反斜杠开头的 `\\.\pipe\...`。反方向同样门控：非 Windows 上传 `\\.\pipe\name` 也**主动 `raise ValueError`**，因为命名管道要 `ProactorEventLoop`，而 asyncio 在非 win32 平台根本不导出 `ProactorEventLoop`（`asyncio/__init__.py` 只在 `sys.platform == 'win32'` 时 `from .windows_events import *`），aiohttp 那句 `isinstance(loop, asyncio.ProactorEventLoop)` 门控自己会先抛裸 `AttributeError`。两个方向都是 fail-fast 前置校验。

18. **定时任务归 workspace，触发权归 (session × schedule)（刻意为之，勿"修"回每个 Session 都触发、也勿退回单个布尔）**：`schedules/` 从 **workspace** 加载（不是 agent 包）；每个 Session 都读到全部条目，但**是否起 runner 逐条决定**——`ScheduleRegistry(active_names=…, deactive_names=…)`：白名单 `None`/空 → 一条都不触发（所有用户会话的默认），`{"*"}` → 全部，具名集合 → 仅这些；黑名单**优先**做减法。两个名单都要，因为白名单是枚举、覆盖不到启动后新建的 `TASK.md`——「除某几条以外全归我」只能写成 `*` + 黑名单。未激活的条目照旧被加载进 `ScheduleRegistry.schedules` 并计入 `refresh()` 的 added/updated/removed 统计，只是 `_start_runner` no-op（想只看会触发的用 `active_schedules` property）。因为 Gateway 一进程多 Session、飞书按会话各 spawn 一个（私聊按 `open_id` 每人一个、群聊按 `chat_id` 每群一个），若同一条被多个 Session 激活，一条定时提醒会被在线会话数乘一遍；不变式是**一条 schedule 恰好被一个 Session 激活**。粒度是逐条而非整个 Session 一个布尔：布尔只能表达「全触发 / 全不触发」，表达不了「A 条归调度 Session、B 条归某个用户会话」。Gateway 侧 `SchedulerManager.ensure()` 为每个 workspace 维护唯一一个全量激活（`("*",)`）的调度 Session——去重发生在**构造期**，因此没有租约 / 选主 / 接管这类运行时协调。详见 `session/AGENTS.md`「调度归属 workspace，触发权归属 (session × schedule)」与 `gateway/AGENTS.md`「SchedulerManager」。

19. **飞书群聊整群共用一个 Session，且私聊 session_id 里的 `-` 必须转义（两条都刻意为之，勿"修掉"）**：飞书路由键分两支——私聊按发送者 `open_id`（`feishu-<open_id>`，一人一份上下文），**群聊按 `chat_id`**（`feishu-chat-<chat_id>`，**整群共用一份**）。群聊不按发言者拆，因为群里的对话本就是共享的：A 问完 B 追问「那第二点呢」，机器人必须看得见 A 那轮；要区分谁在说话靠 `_context_header` 每条消息注入的 `sender_open_id`（已有机制），不靠拆 session。第二条：`_sanitize_open_id` 的白名单 `[^A-Za-z0-9._-]` **允许** `-` 通过，所以私聊侧派生 session_id / workspace 时必须额外把 `-` 换成 `_`——否则某人 open_id 恰为 `chat-oc_x` 时派生出的 `feishu-chat-oc_x` 与群 `oc_x` 的 session id **逐字节相同**，两个陌生人共享同一份上下文与 workspace，是**隐私事故**而非美观问题。`_session_id` 与 `_workspace_for` 两处必须同步转义，只改一处会「session 分开了、workspace 还是同一个目录」。同理 `chat_id` 为空时**不**按群路由（否则建出 `feishu-chat-` 无主 session），宁可这条消息不隔离。channel 侧 socket 缓存需要同款判定（同群不同发言者须命中同一条缓存，否则每人各打一次 Gateway），故群聊判定与路由键已收敛到 `psi_agent/_feishu_routing.py`（`is_group_chat()` / `route_key()`）——改那一处即全局生效，不再需要人工同步两侧。详见 `gateway/AGENTS.md`「FeishuManager」与 `channel/AGENTS.md`「按会话独立渠道」。

19. **`tg.__aexit__(None, None, None)` 不取消子任务——常驻任务会把它挂死**：传三个 `None` 是「正常退出」语义，anyio 于是**等**子任务自己结束。若任务组里有 `start_soon` 起的常驻 server（Gateway 的 AI / Session、channel core），它们永不返回，`__aexit__` 就永久阻塞。在测试里这最阴：`finally: await tg.__aexit__(None, None, None)` 会把测试体内**任何**断言失败从「失败」放大成「挂死」，traceback 都看不到（曾让 `test_manager.py` 在 Windows 上整个文件跑不完，且因 CI 只跑 Linux 而长期隐身）。退组前必须先 `tg.cancel_scope.cancel()`，或显式 `delete()` 掉每个 spawn 出来的实体。参见 `tests/psi_agent/gateway/test_manager.py` 的 `_close()` 与 `test_feishu_manager.py` 的 `_drain()`。

20. **测试断言跨平台路径不能写死后缀**：`_socket_path()` 在 POSIX 上给 `/tmp/.../{id}.sock`、在 Windows 上给 `\\.\pipe\...`（无后缀）。断言 `.endswith(".sock")` 在 `ubuntu-latest` 的 CI 里永远通过，却在每台 Windows 开发机上必然失败——叠加上一条就是挂死。用平台判定函数（`test_manager.py` 的 `_is_socket_path`）。

21. **重定向家目录必须 patch `Path.home()` 本身，不能只 `setenv("HOME")`**：`Path.home()` 在 Windows 上读 `USERPROFILE`、在 POSIX 上才读 `HOME`，所以 `monkeypatch.setenv("HOME", str(tmp_path))` 在 Windows 上**完全不生效**。后果是双重的：断言落点的用例直接失败，而**没有**断言落点的用例会「安静地通过」并往开发者真实目录里写文件（`~/Downloads/.psi/` 曾被测试污染）。CI 三个 job 全是 `ubuntu-latest`，这类差异永远照不出来。正确写法 `monkeypatch.setattr(Path, "home", lambda: tmp_path)`，见 `tests/psi_agent/gateway/test_chat_manager.py` 的 `fake_home` fixture。凡测试碰到会往家目录写盘的代码（目前是 `_chat_manager._downloads_path`），都要先重定向，且**顺手补一条落点断言**——没有断言就等于没有防线。

19. **易变内容挂在请求尾部、不进 system prompt（刻意为之，勿"优化"成写进提示词、也勿删掉那几个 no-op 分支）**：`SystemPrompt.ensure()` 每回合都跑，但只有两条路径——history 空则整段构建；`system_prompt_rebuild_checker()` 返回 True 则整段重建；否则提示词**一字不改**。所有描述「现在」的内容改由 `SystemPrompt.turn_context()` 每回合渲染，挂到**本回合 user 消息**的 `turn_context` 键上（`history_display.TURN_CONTEXT_KEY`），只在 `messages_for_ai()` 投影时折进 `content`。缺了这套机制，提示词就是「首个回合建一次、整段沿用到会话结束」，里面**所有描述「现在」的内容全部冻结**：7月24日建的会话连着几天说今天是 7月24日；构建那刻算错的时区标签（容器 `TZ` 未生效 → `Asia/Shanghai` 记成 `UTC`）活到会话结束；agent 照读陈旧时间作答、被追问时还会编一套时区换算圆场（真实事故）。**为什么不重渲染提示词（哪怕只渲染它的尾部）**：一是整段构建要重扫 skills/tools/bootstrap，实测 haitun 约 110ms、150KB 提示词，这笔是**当下就在付**的；二是它会永久堵死提示缓存这条路——上游按**前缀**缓存，而 system prompt 是**整个请求的最前面**（`any_llm` 的 Anthropic 转换器把所有 `role=system` 抽成顶层 `system` 参数、排在 `messages` 之前），每回合改它就意味着无论怎么配缓存都不可能命中。**注意时态：本仓当前并未开启缓存**——Anthropic 的 prompt caching 是 opt-in 的，文档里那个叫 "automatic caching" 的选项指的是断点自动前移、**仍然要在请求顶层放一个 `cache_control`**，而 `src/` 里没有任何 `cache_control`/`ephemeral`（可 grep 复核）。所以现在不存在「击穿」，改动的收益是**让前缀真正稳定下来、把开启缓存变成一个可行选项**；开启本身是独立的事（会动计费行为，还要先确认提示词长度过得了 512/1024 token 门槛、会话节奏跟得上 5 分钟 TTL）。切 `stable_prefix + 边界 + dynamic_suffix` 并不解决这件事：省下的只是重扫开销，前缀照样每回合变，所以那套边界常量已随此设计一并删除。挂到请求尾部则变动只落在这一个回合。**折在正文之后而非之前**：前置会移动这一回合的每个 byte，正好抵掉带外存储想省的东西。**几个 no-op 分支都是刻意的容错，别当冗余删掉**——未定义 `turn_context_builder`（老 workspace 行为不变）、builder 抛异常、返回非 `str`/空串/纯空白（一律当没有这个块）、`content` 是多模态 block 列表（原样返回并丢块，没有唯一可追加位置，丢一行时钟远好过写坏 block 结构）。**不单独发一条尾部消息**：不是发不出去（Anthropic 明确会把连续同角色轮次合并成一条，不报错），而是那条消息得先落进 history 才能发出去，于是每回合往历史里多塞一条一次性的时钟消息——历史被噪音撑大、压缩时还要判断哪些该丢；挂在本回合 user 消息上则一行不多，且随该回合一起过期。`turn_context` 属于非上线键（与 `kind`/`chat_type` 同在 `_DISPLAY_ONLY_KEYS`），**不写回 history 行**，这样之前每个回合投影出来逐字节相同、前缀才真能复用（实测两回合请求只在末尾分叉，第一回合那行至今仍带着它当时的时钟）。`turn_context_fn` 保持 `None` 默认而不套用坑 8 的「Default over None」：默认函数只能返回空串，与 `None` 语义重合却多一次无谓 await，且 `None` 本身承载「这个 workspace 没有易变块」的语义。**`USER.md`/`HEARTBEAT.md` 留在提示词里**（它们是当作长期上下文读的散文，不是本回合的新闻），文档承诺的「re-read every turn」由 rebuild checker 按**内容哈希**兑现——字节真变了才重建，改一次付一次。详见 `session/AGENTS.md`「每回合易变上下文」。

22. **cancel scope 不能跨 `yield`；给流加超时只能包裸读，不能包 generator 的 `__anext__`**：两条一体，都在给「上游静默时把缓冲尾巴发出去」加超时时踩过（`channel/_stream.py` 的 `idle_timeout`）。**其一**，`with anyio.move_on_after(...)`/`fail_after(...)` 里出现 `yield`，正常路径全绿，但消费者**提前 break** 时 `aclose()` 会在**另一个任务**里退出那个 scope，anyio 直接 `RuntimeError: Attempted to exit cancel scope in a different task than it was entered in`，且上游 generator 不被终结（资源照漏）。所以 scope 必须在 `yield` 之前完全关闭——先在 scope 内读、出了 scope 再判 `scope.cancelled_caught` 并 `yield`。这条也否掉了「起 pump 任务 + memory stream + 超时等队列」那种写法：它把 `yield` 留在 task group 内部，同样只在提前 break 时炸。**其二**，超时**包在 async generator 的 `__anext__` 上会静默截断流**——取消会终结 generator，后续数据全丢（与 `gateway.server._write_chat_sse_with_keepalive` 里那条「keepalive 不得 `fail_after` 包 `agen.__anext__()`」是同一个坑的两种表现）。而 aiohttp 的 `StreamReader` 是**类实现**的异步迭代器，取消其 `__anext__` 后 reader 完好、下次读接着走（已用真停顿服务器实测）。**推论**：给流式读加超时，要么作用在类实现的 reader 上，要么把超时下推到最底层的裸读；凡对「可能是 generator」的源加超时，必须留有 opt-out（本仓即 `idle_timeout=0` 默认）。测试要专门覆盖**提前 break** 这条路径，正常消费路径照不出这两个缺陷。

23. **`os.environ` 传不到兄弟进程——需要共享的路径必须现问，不能靠继承（真实事故）**：Gateway 启动会把解析后的 AppData 根写进自己的 ``os.environ["PSI_APPDATA"]``，这对**同进程**工具与之后 fork 的子进程有效，但飞书 channel 通常是**兄弟进程**（各自 `psi-agent gateway` / `psi-agent channel feishu`），**继承不到**。启动脚本只给两者之一传 `--appdata` 时，两个进程各自按「显式 → `PSI_APPDATA` → platformdirs」解析，落到**不同根**：Gateway 侧 workspace 工具把卡片快照写进 A（46 个 `.json`），channel 侧回调去 B 里找（0 个 `.json`），于是每次点击都读不到快照。**一个缺陷会表现成两个症状**，别当两个 bug 分头修：没有快照既无法原地改卡（退到通用兜底卡），又让注入 agent 的上下文变成 `dispatch.matched=false` / `handler=null`——而提示词在 `matched=false` 时明确要求「不得声称成功、只回操作者需要的信息」，于是多出来的那条叙述性回复**正是 agent 照规则执行的结果**，不是模型跑偏，改提示词是错的方向。修法是让路径有**单一权威**：channel 在没有显式 `--appdata` 时经 ``GET /defaults`` 向 Gateway 现问（该端点本来就返回 `appdata`，无需新增接口），显式传参仍优先、查询失败只 WARNING 并保持本地解析顺序，**启动不依赖 Gateway**。**推论**：任何「两个进程必须看到同一个路径」的需求都不能靠 env 继承表达，要么每个进程各自显式传参，要么向权威方现问。**另一半教训是可观测性**：channel 此前根本不打印自己解析出的 AppData 根，这是该缺陷长期难以发现的直接原因——凡「两处必须一致」的路径，各方都应在启动时无条件 INFO 打印自己的解析结果。详见 `channel/AGENTS.md`「AppData 根向 Gateway 现问」与 `gateway/AGENTS.md` 三区路径小节。

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

## 注释约定

- **语种与风格跟随所在文件**，不跟随个人习惯：改一个文件前先看它现有的注释/docstring 是英文还是中文，然后与之保持一致。**单个 `.py` 文件内必须统一**
- 仓库整体是混合的（`src/` 与 `tests/` 均约 1:6 中英），但这不是「随便写」的许可——它是逐文件收敛的结果。典型：`gateway/_feishu_manager.py`、`gateway/_scheduler_manager.py` 与其对应测试通篇中文；`session/schedule_registry.py`、`session/agent.py`、`gateway/server.py`、`gateway/_session_manager.py` 通篇英文
- **`刻意为之:` 是例外**，可嵌在英文注释里作反直觉行为的标记词（如 `# prompt = LLM turn on task_content; tool = direct ToolRegistry call (刻意为之).`）。它是全仓统一的检索词，配合「改动后自检清单」第 1 条使用，不算破坏语种一致性
- 新建文件按**同层同类邻居**定语种（如 `gateway/_scheduler_manager.py` 对标 `gateway/_feishu_manager.py`），别按仓库全局比例猜
- 中文注释里避免全角 `，`、`（`、`）`、`：` 与 `×`——ruff 的 RUF001/002/003 报 ambiguous unicode，一律改半角 `,` `(` `)` `:` 和 `x`；`。`、`——`、`「」`、`→` 不在规则里，可用（本条以 `ruff check --isolated --select RUF001,RUF002,RUF003` 实测为准）

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

## 多树协作与分支同步

本仓常被同时 checkout 成多棵工作树并行施工（前端树 / workspace 树 / 参谋树）。约定如下：

- **一棵树只改一个区**：前端树只碰 `src/psi_agent/gateway/spa-v2/`（及必要的 Gateway 壳 / spa v1）；workspace 树主要碰 `examples/haitun-workspace/`，以及必要的 Session / Gateway 服务端。越区改动优先换树，而不是在本树顺手改
- **同 remote ≠ 同磁盘**：别人把分支合进 `main`，不会自动出现在你的工作树里；要用 `git fetch` 后显式合并
- **接 `main` 时停在自己的 `feat/…` 上**：`git fetch origin` → 先 commit 或 stash 保护 WIP → `git merge origin/main`。冲突以各层 `AGENTS.md` 为准（保留三区 / AppData / ContextVar 约定后再叠自己的功能）
- **禁止**擅自 `git reset --hard origin/main`——它会丢掉本树的本地提交，除非用户明确要求
- **阅读顺序**：根 `AGENTS.md` → `session/AGENTS.md` → `gateway/AGENTS.md` → `examples/haitun-workspace/AGENTS.md` 或 `spa-v2/AGENTS.md`
- **各区验收命令看本层文档**：Python 侧见上面「开发命令」；前端侧见 `spa-v2/AGENTS.md`「本地开发」（`npm run build` 后经 Gateway 硬刷验收，该目录没有 `npm test`）

## 改动后自检清单（Definition of Done）

任何代码改动完成后、提交前，必须逐条核对以下四项：

1. **文档同步**：检查 `AGENTS.md`（含各层 `*/AGENTS.md`）、`README.md` / `README_en.md`、`docs/`、`specs/`、`plans/` 中是否有因本次改动而过时或缺失的内容。凡改了行为 / 协议 / 配置项 / 默认值，就同步对应文档；新增任何刻意为之的「反直觉」行为，必须在 AGENTS.md 留痕，避免后人误当 bug 修掉。凡改协议格式 / `finish_reason` 常量 / 辅助帧规则，必须同步 `psi_agent/protocol.py` 的 docstring 与本文件「核心通信协议 → 协议归属」；子层 `AGENTS.md` 只引用函数名，不重复写格式定义。

2. **日志粒度对齐**：检查 loguru 日志是否完整——不要漏掉应有的日志（关键分支、IO、错误、生命周期）。新增日志的 level 必须与**周围既有代码**保持一致：每个 SSE chunk / tool 执行 / 锁获取释放走 DEBUG，启动 / 关闭 / 请求完成走 INFO，可恢复异常走 WARNING，不可恢复错误走 ERROR。不要凭空拔高或压低 level。

3. **异常与取消安全**：检查改动点及其邻近代码是否异常安全——被 `cancel` 时会不会出问题？是否存在 cancel 时资源泄露（未关闭的 socket / `AppRunner` / 文件 / 子进程 / 上游 streaming 连接）？清理代码必须放在 `finally`、`except` 或 `async with` 上下文管理器（`__aexit__`）中，跨 `await` 的清理用 `anyio.CancelScope(shield=True)` 保护。注意 `CancelledError` 是 `BaseException`，不在 `Exception` 之下——`except Exception` 不会（也不应）吞掉它；严禁用 `except BaseException` 误吞取消信号。

4. **测试补充**：为新增 / 变更的逻辑补 unit test；涉及跨组件交互（socket、SSE、agent loop、错误传播）的补 integration test。测试目录镜像 `src/psi_agent/`，集成测试放 `tests/integration/`。改完后跑 `uv run pytest` 确认通过。

## 云端服务边界（psi-cloud）

C 端注册登录的云端服务**不在本仓库**，在服务器 `/srv/psi-cloud`（独立 git 仓库）。psi-agent 是可安装的客户端包，塞进服务端目录会让打包与依赖边界变浑。

- 两侧只通过 HTTP 契约耦合。契约的权威定义是云端的 `/openapi.json`（自动生成，不会与实现脱同步）。
- 云端的目录结构、模块契约与硬规则记在 `/srv/psi-cloud/AGENTS.md`，**本文不重复**。一句话概括：`core/` 是框架且不认识任何业务，`modules/` 下每个目录一块业务自报清单，认证是 `modules/auth`。
- 本机侧只有 `gateway/_auth_manager.py` 与 `_auth_store.py` 两个文件与它对接，**不持任何供应商密钥**。改动云端接口要同步上面那份设计文档。

## 未来扩展方向

- [x] 单进程中运行多个 session 实例（利用 anyio task group）— 通过 Gateway 实现
- [ ] workspace.py 统一 workspace 管理
- [x] 更多 channel 类型 — Gateway REST API + Web Console SPA
- [ ] 更多 AI 后端（Gemini、本地模型等）
- [x] Session history 持久化（已完成）
- [x] Context compaction — 超 token 阈值时 AI 层发信号，Session 调用 system.py compact_history 压缩
- [ ] Channel 广播/多客户端队列
