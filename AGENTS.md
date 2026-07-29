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

**为什么 FusionFlow 的调用序号在命中缓存或成功落盘后才提交？**
调用序号属于可恢复状态。失败的 `session()` / `call()` 不得消耗序号，否则同一调用重试时可能误读下一条历史 binding。显式 `binding_name` 的旧恢复产物也不算本次运行已写入，因此 resume miss 后允许覆盖一次；本次运行再次写同名 binding 才报错。

**为什么 FusionFlow `parallel()` 不照搬 TypeScript 的 grace-period 脱离任务？**
Python 版本坚持 AnyIO 结构化并发：`first` / `any` 取消落后任务后仍等待它们完成清理。这样 `run()` 返回时 binding 与 trace 已封口，不会再被后台任务修改。任务若吞掉取消信号而永久运行，整个并行节点也会继续等待；这是资源与状态一致性的有意取舍。

**为什么 WorkflowGraph 的 `SelectNode` 是 eager 值选择？**
FusionFlow 的命名选择 `selected == if(condition, artifact_a, artifact_b)` 只决定下游读取哪个值。planner 会等待条件和两个候选 Artifact 全部可用，因此两个候选 producer 都会运行。它不是 lazy 分支、Step activation 或控制流 Region；不要据此跳过未选 Step。多级优先级用多个命名 Artifact 串联，禁止把嵌套 `if` 直接塞进 dataflow List。

**为什么 WorkflowGraph 同时有 Artifact 依赖和 `depends_on`？**
`ProducesEdge` / `ConsumesEdge` 表示值的来源与可用性；`StepNode.depends_on`
表示没有值传递时仍必须遵守的显式顺序约束。planner 将两类前驱合并为同一组
`Await`，执行器也会拒绝绕过任一前驱的手写计划。结构模型仍允许保存有环图，
包括显式顺序环；one-shot planner 在执行前统一拒绝 circular await。
`independent` 只是非约束 metadata，不能覆盖数据依赖或显式 `depends_on`。

**WorkflowGraph 的资源调度边界是什么？**
`.workflow` 只通过 `resource_requirement` 声明 Step 所需的资源种类和数量；容量或
具体实例 ID 由 runner 外部配置。执行器在同一个 admission 临界区中同时检查
`max_concurrency` 和全部资源需求，原子取出实例，并在成功、失败、超时或取消后
shielded release。`DispatchContext.resource_lease` 负责把具体实例传给 dispatcher，
但执行器不会自行绑定 GPU、设置环境变量或调用工具。allocator 是进程内对象；
跨进程/跨主机租约和 shared/exclusive 模式不在当前合同内。

**WorkflowGraph checkpoint 与 Human 跨回合等待的边界是什么？**
`ExecutionCheckpoint` 只保存已经物化的 Artifact 值，以及依赖闭包完整的已完成
Step / Select ID，并绑定非空 `workflow_id` 与 graph/plan 结构摘要。checkpoint 值只
接受 finite JSON；恢复时按 JSON 类型和值递归比较，因此 `true` 不等于 `1`。执行器
还会严格校验已完成 ID、输入和值集合，预先发布已完成事件并跳过这些操作；
`checkpoint_observer` 必须成功持久化后才允许依赖者继续。它不是任意缓存命中或
legacy `flow.*` run-directory resume。

Session 在一个请求的完整 agent/tool loop 期间持有 `_lock`，因此 workspace tool
不能在 Human Step 内阻塞等待“下一条用户消息”，否则下一条消息无法进入同一
Session。G4 workspace adapter 必须先保存 checkpoint 和 Human request，返回到父
Session，通过既有 `clarify` 格式化问题并结束当前 turn；下一轮再用匹配的
`run_id` / `request_id` 恢复。人工等待期间不保留资源 lease，workflow/Step timeout
也在每个恢复阶段重新计时。该 adapter 属于 workspace，不把 Human 交互或持久化
塞进 `psi_agent.workflow_execution` 核心。

G4 workspace adapter 还会把每次运行已经物化的输入、中间、选择及最终 Artifact
逐个原子写入 workflow bundle 的 `runs/<run-id>/artifacts/*.md`。字符串保持原
Markdown，其他 finite JSON 值用 fenced `json` 表示。该目录是用户可见的运行历史，
不参与 checkpoint 恢复，也不跟 `.psi/fusion-flow/runs/` 的 Human 私有状态混用；
纯 Agent/Program 运行仍不可 resume，但其中间 Artifact 不能因此丢失。

**FusionFlow 的跨语言兼容边界是什么？**
运行结果与核心行为优先兼容，包括同一运行时内的 binding 恢复、配对的 `node_start` / `node_end` progress 事件、分组 token 汇总、程序快照和 `exec()` 截断标记。真实 TypeScript `inputHash` 与 Python `cache_key` 的输入、provider 身份和长度不同，因此两种运行目录不保证直接互相命中缓存；camelCase metadata 别名只保证字段可读。graph、meta 和 trace 使用各自语言的数据结构：Python 命名 trace 是通用 `ExecutionTrace`，不是 TypeScript 的扁平 provider/model/prompt 对象；只有 `progress.jsonl` 保持共享格式。单节点 trace / progress 写入是 best-effort，最终 graph/meta 才是权威产物，不追随 TypeScript 的诊断写失败即中止。`flow.output()` / `ctx.save()` 写入带 metadata 的单赋值 binding，不额外创建 output graph node。

Python API 保持 snake_case，并由显式 `SessionRunner` 承担 provider 调用；不复制 TypeScript 的 camelCase 配置、环境变量配置、内嵌 provider 选择或 evaluator 的 provider JSON Schema 通道。字符串/编译后的 `RegexRule` 使用原生 Python `re`；需要与 JavaScript `RegExp` 的 ASCII 字符类一致时由调用方显式选择 flags。实际 runner 实现身份不能自动进入 cache key，runner 行为变更时调用方必须同步更新 `AgentConfig.engine`、model 或其他版本字段。自动 GC 通过 `run(keep_count=..., keep_days=...)` 配置，两者同时为 `0` 表示禁用清理。未知 token 保持为 `None`。旧式 `Agent` 的独立诊断 trace 在 resume 时不会覆盖同名旧文件；跳过的文件序号会占入它与 `session()` / `evaluate()` 共享的调用序号，实际序号同时写入 trace metadata，而 `session_calls` 仍只统计本次执行成功或缓存命中的调用。这些都是仓库有意保留的安全适配。

**为什么 `AgentConfig` 的 token / temperature 默认值先保留为 `None`？**
TypeScript 对同一份省略字段的配置按调用位置解析：普通 `session()` 与旧 `Agent` 使用 `8192 / 1`，自定义 evaluator 使用 `256 / 0`。Python 只有把“未填写”保留到实际调用边界，才能区分省略与用户显式传入 `8192 / 1`；交给 `SessionRunner` 前始终会解析成具体数值。

**为什么 G4 使用 `agent_system_prompt`，Python 也只保留 `AgentConfig.system_prompt`？**
这里表示 Agent 长期稳定的系统提示词；Step 当次任务仍由 `step_instruction` 映射到
`AgentInvocation.prompt`。旧 `AgentConfig.system` 和含义重复的
`AgentConfig.prompt` 不保留兼容入口，避免把两层 prompt 再次混淆。配置 payload
字段随之改名，旧运行缓存可能因 hash 变化而重新执行。

**为什么 Agent Step 的单输出与多输出 fallback 不同？**
每个 Agent Step 优先通过按 output Artifact ID 生成精确 schema 的
`submit_step_result` 提交结果，只有正常结束的最终文本才保留为兼容路径，并接受
一个精确 object 或独立行包围的单个 `json` fence。若 Step 恰好声明一个 output，
无法解析为精确 object 的原始回复会直接、完整地绑定到唯一 Artifact，并记录不含
原文正文的结构化 warning。若声明多个 output，则先允许两次结果修复，仍无效时把
第一次无效回复完整广播到每个 output Artifact，并记录同样的 warning。这是确定性
的整段复制而非语义拆分；广播是 runtime 默认策略，当前没有终端用户开关。零输出
仍可提交精确的空 object；无效回复在修复失败后整体报错，因为没有 Artifact 可承载
原文。截断、达到 tool round 上限等非正常结束也会直接失败，不进入原文 fallback。
所有路径都不猜字段、不填默认值，也不发布部分结果。

**为什么 G4 Program Step 改由特化 Agent 执行，而不再要求 `program_path` 可执行？**
`program_path` 现在标识 workspace 内的普通脚本或源码文件，不是 shell command；
它只需解析为 workspace 内的 regular file，无需 shebang、`chmod` 或 POSIX executable
bit。每个 Program Step 建立独立的特化 Session，可用受限的 workspace
inspection 与 `bash` / `powershell` 工具准备或安装多语言 runtime、dependency、
compiler 和 toolchain。fidelity 模式的解释型执行不接受 Agent 自选的完整 argv：
Agent 只选择 interpreter executable，host 固定构造
`[interpreter, declared_script, *logical_argv[1:]]`，不允许插入 flag、别的 script
或额外参数。编译型执行必须先经过结构化 `compile_program`，把 compiler argv、
声明 source hash、产物 hash 与精确 launch argv 绑定；`execute_program` 只启动
该已注册且重新验 hash 的 argv。结构化工具分别捕获 stdout/stderr bytes、exit
code 和 launch error，并保留既有的 AnyIO 进程树清理及输出上限。环境 shell
不能冒充编译注册或最终 Program 执行。

Program Agent 默认是 fidelity mode：真实程序启动前可以修复并重试缺失环境或
toolchain，但不能改脚本、改 consumed input Artifact、改 stdin 或重解释输出。
一旦真实程序成功启动，就禁止第二次执行；无论它随后非零退出、报告
invalid-input/domain error，还是产生不合约输出，都必须保留并提交第一次 attempt
的原始错误。只有解析后的 Step instruction 含精确独立行
`Program execution policy: successful completion outranks fidelity.` 时，
`repair_authorized` 才为真；任何改写脚本或 stdin 的授权适配还必须给出具体
`adaptation_reason`，input Artifact 值始终不可变。`submit_program_result` 不接受
模型自填 Artifact，而是确定性提交该权威结构化 attempt：非零退出、invalid
UTF-8、启动/格式失败等会把含 `phase/kind/message/attempts` 的
`$fusion_flow/program_error` 值复制到每个声明 output；零 output 失败因无 Artifact
承载诊断而直接抛错。

**为什么不能创建 `run_id="last"`？**
`resume_from_run_id="last"` 与 TypeScript 的 `--resume=last` 一样是“选择字典序最新目录”的哨兵。为避免一个真实 run 永远无法按同名恢复，Python 明确保留这个名称并在创建目录前拒绝。

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
    ├── _run.py                 # YAML 配置批量启动（psi-agent run config.yml）
    ├── _logging.py              # loguru 配置，verbose→DEBUG
    ├── ai/
    │   ├── AGENTS.md                # AI 层设计文档
    │   ├── __init__.py               # Ai + serve_ai
    │   └── server.py                 # handler（请求处理）
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
    ├── workflow_execution.py       # WorkflowGraph → plan + async executor + validated checkpoints
    ├── workflow_graph/
    │   ├── __init__.py             # 声明式图模型 API
    │   └── model.py                # Step–Artifact 静态图 + eager SelectNode
    └── gateway/
        ├── AGENTS.md                # Gateway 层设计文档
        ├── __init__.py              # Gateway dataclass + run()
        ├── _manager.py             # 共享类型 + helpers
        ├── _ai_manager.py         # AIManager
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

各层的详细设计文档见：
- **AI 层**: `src/psi_agent/ai/AGENTS.md` — provider 配置、请求透传、错误处理、context compaction 触发
- **Session 层**: `src/psi_agent/session/AGENTS.md` — workspace 启动、agent loop、tool 加载调用、schedule 机制、history 持久化、context compaction
- **Channel 层**: `src/psi_agent/channel/AGENTS.md` — ChannelCore 公共部件、REPL/CLI/Telegram/Feishu 约定
- **Gateway 层**: `src/psi_agent/gateway/AGENTS.md` — 生命周期管理、REST API、Web Console SPA、CI 打包
- **Workflow Graph**: `docs/architecture/workflow/2026-07-23-workflow-graph-design.zh.md` — 允许有环的声明式 Step–Artifact 图及待讨论语义；具体 Core IR 后端位于 `examples/haitun-workspace/skills/fusion-flow/fusion_flow/graph_compiler.py`
- **Workflow Execution**: `docs/architecture/workflow/2026-07-25-workflow-execution-plan-design.zh.md` — one-shot 无环子集的 Fiber/Await/Invoke 计划、全量异步启动、dispatcher 与 validated checkpoint 边界
- **FusionFlow Compatibility Execution**: `examples/haitun-workspace/skills/fusion-flow/fusion_flow/execution/` — 示例 Skill 内隔离保存的旧 TypeScript `flow.*` Python 兼容层；不属于 `psi_agent` wheel，也不是 G4 Core IR / WorkflowGraph runner

## 核心通信协议

所有组件通过 **aiohttp** 以 **OpenAI Chat Completions HTTP/SSE** 格式通信。传输支持 Unix socket（仅 POSIX）、TCP、Windows Named Pipe（仅 Windows），由地址前缀自动检测（`psi_agent._sockets`）；平台与地址不匹配时抛 `ValueError` 快速失败，详见「关键注意事项」第 17 条：

- **AI socket**: Session 作为客户端访问，`POST /chat/completions`
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

17. **Windows batch 参数边界**：`fusion_flow.execution.flow.exec()` 仅在目标显式以 `.cmd`/`.bat` 结尾时使用系统 shell，并对命令与参数整体加引号、延迟还原字面量 `%`；含双引号、`!` 或换行的参数直接拒绝。`!` 在被调 batch 开启 delayed expansion 后会被静默吃掉，默认拒绝比悄悄改参安全。Windows 非 batch 与其他平台始终保持 argv/no-shell 路径。batch 进程使用独立进程组，并尝试用 Job Object、失败时用 `taskkill /T` 清理进程树；Job Object 在进程启动后挂接，存在很小的启动窗口，不能承诺捕获所有后代。

18. **`WorkflowEdge` 是封闭 union**：`WorkflowGraph` 只接受 `ConsumesEdge`、`ProducesEdge`、`ForeachEdge` 的精确类型，不接受子类。子类会破坏 dataclass 基于精确类型的相等性去重，也能覆盖序列化使用的 `kind`。新增边类型时应显式更新 union、校验和序列化。

19. **WorkflowGraph 可保存有环，但 one-shot plan 不执行环**：`workflow_execution.generate_plan()` 把 producer/consumer 数据前驱与 `StepNode.depends_on` 显式顺序前驱合并为 `Await`。它同时启动所有 Fiber；Foreach、retry、input+producer 和 circular await 在计划阶段报错，不能静默忽略或留到运行期死锁。资源需求由执行器的 allocator 在 dispatch 前处理，不再由 planner 拒绝。

20. **Windows 上裸路径地址直接拒绝（刻意为之，勿"修掉"）**：`_sockets.py` 的 `resolve_connector_and_endpoint` / `create_site` 在 `sys.platform == "win32"` 且地址落到 Unix 分支时**主动 `raise ValueError`**。因为 Windows 的 asyncio 没有 `create_unix_connection` / `create_unix_server`，若继续走 `UnixConnector` / `UnixSite`，aiohttp 会在 connect/listen 深处抛一个**不带任何上下文的 `NotImplementedError`**，极难定位（曾导致飞书 channel 每条消息崩、只显示 `generation interrupted`）。真实诱因：`channel feishu --session-socket \\.\pipe\...` 经 POSIX shell 传参时反斜杠被吞成单反斜杠 `\.\pipe\...`，匹配不上命名管道前缀而落到裸路径分支。**这是 fail-fast 前置校验，不是可删的多余检查**——非 Windows（POSIX）行为完全不变，Unix socket 照常工作。Windows/bash 下传管道地址需用四反斜杠 `'\\\\.\\pipe\\...'` 才能让程序收到两根反斜杠开头的 `\\.\pipe\...`。反方向同样门控：非 Windows 上传 `\\.\pipe\name` 也**主动 `raise ValueError`**，因为命名管道要 `ProactorEventLoop`，而 asyncio 在非 win32 平台根本不导出 `ProactorEventLoop`（`asyncio/__init__.py` 只在 `sys.platform == 'win32'` 时 `from .windows_events import *`），aiohttp 那句 `isinstance(loop, asyncio.ProactorEventLoop)` 门控自己会先抛裸 `AttributeError`。两个方向都是 fail-fast 前置校验。

21. **定时任务归 workspace，触发权归 (session × schedule)（刻意为之，勿"修"回每个 Session 都触发、也勿退回单个布尔）**：`schedules/` 从 **workspace** 加载（不是 agent 包）；每个 Session 都读到全部条目，但**是否起 runner 逐条决定**——`ScheduleRegistry(active_names=…, deactive_names=…)`：白名单 `None`/空 → 一条都不触发（所有用户会话的默认），`{"*"}` → 全部，具名集合 → 仅这些；黑名单**优先**做减法。两个名单都要，因为白名单是枚举、覆盖不到启动后新建的 `TASK.md`——「除某几条以外全归我」只能写成 `*` + 黑名单。未激活的条目照旧被加载进 `ScheduleRegistry.schedules` 并计入 `refresh()` 的 added/updated/removed 统计，只是 `_start_runner` no-op（想只看会触发的用 `active_schedules` property）。因为 Gateway 一进程多 Session、飞书按会话各 spawn 一个（私聊按 `open_id` 每人一个、群聊按 `chat_id` 每群一个），若同一条被多个 Session 激活，一条定时提醒会被在线会话数乘一遍；不变式是**一条 schedule 恰好被一个 Session 激活**。粒度是逐条而非整个 Session 一个布尔：布尔只能表达「全触发 / 全不触发」，表达不了「A 条归调度 Session、B 条归某个用户会话」。Gateway 侧 `SchedulerManager.ensure()` 为每个 workspace 维护唯一一个全量激活（`("*",)`）的调度 Session——去重发生在**构造期**，因此没有租约 / 选主 / 接管这类运行时协调。详见 `session/AGENTS.md`「调度归属 workspace，触发权归属 (session × schedule)」与 `gateway/AGENTS.md`「SchedulerManager」。

22. **飞书群聊整群共用一个 Session，且私聊 session_id 里的 `-` 必须转义（两条都刻意为之，勿"修掉"）**：飞书路由键分两支——私聊按发送者 `open_id`（`feishu-<open_id>`，一人一份上下文），**群聊按 `chat_id`**（`feishu-chat-<chat_id>`，**整群共用一份**）。群聊不按发言者拆，因为群里的对话本就是共享的：A 问完 B 追问「那第二点呢」，机器人必须看得见 A 那轮；要区分谁在说话靠 `_context_header` 每条消息注入的 `sender_open_id`（已有机制），不靠拆 session。第二条：`_sanitize_open_id` 的白名单 `[^A-Za-z0-9._-]` **允许** `-` 通过，所以私聊侧派生 session_id / workspace 时必须额外把 `-` 换成 `_`——否则某人 open_id 恰为 `chat-oc_x` 时派生出的 `feishu-chat-oc_x` 与群 `oc_x` 的 session id **逐字节相同**，两个陌生人共享同一份上下文与 workspace，是**隐私事故**而非美观问题。`_session_id` 与 `_workspace_for` 两处必须同步转义，只改一处会「session 分开了、workspace 还是同一个目录」。同理 `chat_id` 为空时**不**按群路由（否则建出 `feishu-chat-` 无主 session），宁可这条消息不隔离。channel 侧 `_GatewayRouteProvider._cache_key` 复制了同款群聊判定（同群不同发言者须命中同一条缓存，否则每人各打一次 Gateway），**两处判定改动时必须同步**。详见 `gateway/AGENTS.md`「FeishuManager」与 `channel/AGENTS.md`「按会话独立渠道」。

23. **`tg.__aexit__(None, None, None)` 不取消子任务——常驻任务会把它挂死**：传三个 `None` 是「正常退出」语义，anyio 于是**等**子任务自己结束。若任务组里有 `start_soon` 起的常驻 server（Gateway 的 AI / Session、channel core），它们永不返回，`__aexit__` 就永久阻塞。在测试里这最阴：`finally: await tg.__aexit__(None, None, None)` 会把测试体内**任何**断言失败从「失败」放大成「挂死」，traceback 都看不到（曾让 `test_manager.py` 在 Windows 上整个文件跑不完，且因 CI 只跑 Linux 而长期隐身）。退组前必须先 `tg.cancel_scope.cancel()`，或显式 `delete()` 掉每个 spawn 出来的实体。参见 `tests/psi_agent/gateway/test_manager.py` 的 `_close()` 与 `test_feishu_manager.py` 的 `_drain()`。

24. **测试断言跨平台路径不能写死后缀**：`_socket_path()` 在 POSIX 上给 `/tmp/.../{id}.sock`、在 Windows 上给 `\\.\pipe\...`（无后缀）。断言 `.endswith(".sock")` 在 `ubuntu-latest` 的 CI 里永远通过，却在每台 Windows 开发机上必然失败——叠加上一条就是挂死。用平台判定函数（`test_manager.py` 的 `_is_socket_path`）。

25. **重定向家目录必须 patch `Path.home()` 本身，不能只 `setenv("HOME")`**：`Path.home()` 在 Windows 上读 `USERPROFILE`、在 POSIX 上才读 `HOME`，所以 `monkeypatch.setenv("HOME", str(tmp_path))` 在 Windows 上**完全不生效**。后果是双重的：断言落点的用例直接失败，而**没有**断言落点的用例会「安静地通过」并往开发者真实目录里写文件（`~/Downloads/.psi/` 曾被测试污染）。CI 三个 job 全是 `ubuntu-latest`，这类差异永远照不出来。正确写法 `monkeypatch.setattr(Path, "home", lambda: tmp_path)`，见 `tests/psi_agent/gateway/test_chat_manager.py` 的 `fake_home` fixture。凡测试碰到会往家目录写盘的代码（目前是 `_chat_manager._downloads_path`），都要先重定向，且**顺手补一条落点断言**——没有断言就等于没有防线。

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
- **嵌套 Python 包**: `fusion_flow` 保持在示例 `fusion-flow` Skill 内，通过 `tool.ty.environment.extra-paths` 纳入全局模块解析，不单独增加打包脚手架
- **ANTLR 生成文件**: `fusion-flow/fusion_flow/generated/` 仅提交 ANTLR 4.13.2 生成的运行时 Python lexer/parser；`.interp`、`.tokens` 和未使用的 visitor 不提交。仅对这个目录关闭 Ruff、ty 和 Git whitespace 检查；手写代码仍保持零抑制。CI 固定 tool JAR 的 SHA-256 并重生成对比，运行时 import 测试负责验证可用性
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
- [x] Context compaction — 超 token 阈值时 AI 层发信号，Session 调用 system.py compact_history 压缩
- [ ] Channel 广播/多客户端队列
