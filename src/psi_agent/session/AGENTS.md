# Session 层设计文档

## 概述

Session 层是 psi-agent 的核心——负责 workspace 解析、agent loop、tool 执行、schedule 调度以及面向 Channel 的 HTTP/SSE 服务。

## Workspace / Agent 路径

| 字段 | CLI | 用途 |
|------|-----|------|
| `Session.workspace` | `--workspace` | 用户打开目录（相对文件 IO）；空 → `Path.cwd()` |
| `Session.agent` | `--agent` | Agent 包目录（tools / schedules / `systems/`）；**空 → 与 workspace 相同**（兼容旧单根） |
| `Session.appdata` | `--appdata` / `PSI_APPDATA` | 记忆区根；history 写 `{appdata}/histories/`（第 4C）；空 → resolve |

`SessionAgent.create(workspace_path=…, agent_path=…)`：省略 `agent_path` 时回落到 `workspace_path`。每回合经 ``runtime_scope`` 绑定 `get_session_id()` / `get_workspace()` / `get_agent()`（见下节适用范围）。

### `runtime_context` 适用范围（刻意限制）

ContextVar 是**隐式环境态**，比进程全局好（多 Session 不互踩），但仍是隐藏依赖——应尽量窄用，能传参就传参。

| | 约定 |
|--|------|
| **唯一写入方** | 仅 `SessionAgent.run` 经 `runtime_scope`（整轮含 tool 执行）。禁止 Gateway / Channel / AI / 测试外业务代码自行 `set_*` |
| **`get_session_id()`** | 仅 **workspace 工具**需要「当前会话 id」时（如 `todo`、fusion memory）。框架内部用 `Conversation.session_id` / 显式参数 |
| **`get_workspace()` / `get_agent()`** | 仅 **workspace 工具**在解析相对路径、找 agent 包根时（`write`/`bash`/`read` 等）。**框架核心**（`SessionAgent` / registries / Gateway / Channel）一律用构造时的 `workspace_path` / `agent_path` 或 REST 入参，**禁止**回读 ContextVar |
| **Tool AI socket bridge** | `current_tool_ai_socket()` 仅在 `SessionAgent` 实际 await workspace tool 的区间返回当前 AI socket，并用 token 复位；它供 `run_flow` 创建受限的临时 Step Session，不进入 tool schema，也不能传播 API key/provider 配置。 |
| **禁止扩进 ContextVar 的** | AppData / 记忆区根、API key、provider、Gateway listen、任意「方便全局拿一下」的配置——这些走显式字段 / DI / CLI |
| **本步消费现状** | ✅ haitun 工具经 ``tools/_runtime_paths.py`` 读 ``get_workspace()`` / ``get_agent()``。todos / history / Gateway ``state/`` 已迁 AppData（legacy 双读） |

## Workspace 启动流程

`Session.run()` 的启动顺序（由 `SessionAgent.create()` 完成加载）：

```
1. setup_logging(verbose)
2. 解析 workspace（空 → cwd）；解析 agent（空 → 同 workspace）
3. SessionAgent.create(workspace_path=…, agent_path=…, appdata_root=…) → session_id、AiClient、从 agent 加载 tools/schedules/system；history 在 AppData（legacy 双读）
4. 启动 anyio.task_group：
   ├── serve_session(agent=agent)  ← 从 agent 读取 channel_socket + handle_request
   └── 每个 schedule 一个 run_one_schedule(schedule, agent) task

**关键点**：
- `SessionAgent` 自包含：持有 `_ai_client`、`_channel_adapter`、`_lock`、`_workspace_path`、`_agent_path`
- `_session_id` 从 `_history_path.stem` 派生，同时用于 sys.modules 隔离（tools/system 的 module name）
- `channel_socket` 由 `Session.run()` 直接传给 `serve_session()`，不进入 agent 内部
- **工具可见的 session id / 路径**：见上方「`runtime_context` 适用范围」。``todo`` 等经 ``get_session_id()`` 读取，勿回落到 ``default``
- 所有手动模块加载使用 `原名_session_id_文件hash` 作为 module name（tool 和 system prompt 均用 `compile` + `exec` 避免 importlib bytecode 缓存），确保同进程多 session 隔离
- `SessionAgent.create()` 完成所有初始化——`__init__.py` 只做入口编排
- Tool / schedule / system 从 **agent_path** 加载；history 写 **AppData** ``histories/``（第 4C；legacy ``{workspace}/histories/`` 双读）
- AppData 路径助手在 ``psi_agent._appdata``（与 Gateway 共享；**禁止**经 ContextVar 传递 AppData 根）
- System prompt 在首次 `run()` 调用时惰性构建（通过 `system_prompt_builder`）
- `system_prompt_builder` 和 `system_prompt_rebuild_checker` 兼容旧的零参形式；如定义了位置参数，Session 会传入当前原始 `user_message`。这一显式参数只用于本轮动态 prompt，不会改变写入 history 的 `kind` 标记副本
- 后续请求可调用 `system_prompt_rebuild_checker()`（如果定义），返回 True 则用同一条当前 `user_message` 重建 system prompt
- 可选 `system_after_turn(user_message, assistant_message)` 在 `finish_reason="stop"` 的最终 assistant 消息已 commit 后执行。它是可恢复的 workspace hook：普通异常记 WARNING，不回滚已成功交付的回合；取消信号仍向外传播。未定义时使用 no-op 默认值
- 未整段重建时，提示词一字不改；若 agent 包定义了 `turn_context_builder()`，则每回合把易变块挂到**本回合 user 消息**上（见下方「每回合易变上下文」）
- 可选生命周期顺序为 `system_before_turn` → `system_prompt_builder` → AI/tool loop → `system_after_turn`。`system_before_turn` 接收当前回合的临时 hook context（含额外请求参数副本）；普通异常、非 dict 返回值和超时降级为 `{}`，但绝不吞掉取消。其结果仅用于本轮 prompt 构建且不写入 history；除 OpenAI 保留字段外，原始请求参数仍透传给 AI。`schedule.*` 回合跳过该 hook。

## Agent Loop 逻辑

1. 收到 channel 请求 → `ChannelAdapter.handle()` 解析请求，提取 user_message + extra_params
2. `SessionAgent.run()` 入口：
   - add() / replace_system() 在首次变更时自动建立快照（implicit snapshot）
   - 惰性构建或重建 system prompt（首次 run 或 rebuild checker 返回 True 时）；随后把本回合的易变块挂到 user 消息上（见「每回合易变上下文」）
   - 检查暂存的 schedule 响应 → peek + yield → yield 全部成功后 `clear_pending()`
   - User message 追加到 history 后立即 ``commit()`` 落盘
3. 获取 `anyio.Lock`（忙则 FIFO 排队等待）—— `handle_request()` 在调用 `run()` 前持有
4. 通过 `AiClient.stream()` 发送 `history + tools + extra_params` 到 AI backend（streaming）
5. 消费 `AiDelta` 流（AiClient 已做好 SSE 解析、错误检测）：
   - content → `yield AgentChunk(content=...)` 给 ChannelAdapter
   - reasoning（模型 thinking）→ `yield AgentChunk(reasoning=..., kind="thinking")`（上游 `delta.kind` 优先）
   - tool 执行起止 → 仍写入 **同一** `reasoning` 槽（刻意压缩，便于 Session↔AI OpenAI 形同构），`kind="tool_call"|"tool_result"`；正文可继续带 `[Tool Call:]`/`[Tool Result:]` 过渡标记
   - tool_calls → 累积（按 index 拼接 partial JSON）
    - `finish_reason="tool_calls"` → 执行 tool → 结果追加到 history → 回到步骤 4
    - finish_reason="stop" → 最终 content 追加到 history + `commit()` + 刷新 schedule registry + 若收到 compaction 信号则调用 `_maybe_compact()` → 释放锁
   - finish_reason="error" → 回滚到快照 → `raise AgentError(message)`
   - 任何未捕获异常 → 回滚到快照 → 向上传播
6. 最多 `max_tool_rounds` 轮 tool call，达到上限时追加关闭 assistant 消息 + commit
7. **Turn 级别原子性**：``run()`` 所有正常出口调用 ``commit()``（save + clear snapshot）；异常时 ``async with`` 上下文管理器自动 ``rollback()``。内存和磁盘仅在同一检查点同步更新。

**注意**：
- Channel 不发送 history。每次请求只带最新一条 user message，Session 自己维护完整 history。
- `response.prepare()` 在 lock 内执行——客户端在 lock 释放前不会看到 HTTP 200。
- `SessionAgent.handle_request()` 编排完整请求生命周期：parse → lock+prepare → run → write。
- `ChannelAdapter` 是纯无状态工具——不持有 agent/lock 引用。
- Channel 请求中除 `messages` 外的不认识参数全部透传到 AI 层（`extra_params`）。
- AI 返回多 choice 时报错（`finish_reason="error"`），0 choice 作为心跳跳过。
- AI 返回非 200 或 `finish_reason="error"` 时，错误信息不写入 conversation history，且通过 turn 快照回滚机制保证本轮用户消息也不落盘。

## System prompt 生命周期

`SystemPrompt.ensure()` 在**每个回合入口**调用，按优先级走两条路径中的一条：

| 触发条件 | 行为 | 日志 |
|---|---|---|
| history 为空（首个回合） | 调 `system_prompt_builder()` 整段构建 | `System prompt loaded (N chars)` |
| `system_prompt_rebuild_checker()` 返回 True | 整段重建 | `System prompt rebuilt (N chars)` |

两条都不触发时，提示词**一字不改**沿用。所以**易变内容一律不放提示词里**——放进去就会冻结在首次构建那一刻，改由每回合的 turn context 承载（下一节）。

## 每回合易变上下文（turn context）

`SystemPrompt.turn_context()` 在 `ensure()` 之后调用，渲染「本回合的现在」——时钟、可能随重新挂载而变的 runtime 行。产物**不进 system prompt**，而是挂在本回合 user 消息的 `turn_context` 键上（`history_display.TURN_CONTEXT_KEY`），只在 `messages_for_ai()` 投影时折进 `content`。

### 为什么必须挂在尾部而不是提示词尾部

没有这套机制，提示词里**所有描述「现在」的内容都被冻结**：

- 7月24日建的会话连着几天都告诉用户今天是 7月24日；
- 构建那一刻若时区标签算错了（容器 `TZ` 尚未生效 → `_build_datetime_section` 落到 `astimezone()` 回退分支，`Asia/Shanghai` 被记成 `UTC`），这个错标签会活到会话结束；
- agent 照读这行陈旧时间作答，被追问矛盾时还会临时编一套时区换算来解释对不上的地方（真实事故）。

但**修法不能是每回合重渲染提示词**，哪怕只重渲染它的尾部：

| | |
|--|--|
| **代价一：重扫 workspace** | 整段构建要重扫 skills / tools / bootstrap 文件。实测 haitun 约 **110ms、150KB** 提示词，放进每个回合是净损失 |
| **代价二：永久堵死提示缓存** | 上游按**前缀**缓存，而 system prompt 是**整个请求的最前面**（`any_llm` 的 Anthropic 转换器把所有 `role=system` 抽成顶层 `system` 参数，排在 `messages` 之前）。每回合改它——哪怕只改尾部——就意味着无论怎么配缓存都不可能命中 |
| **⚠️ 时态：本仓当前并未开启缓存** | Anthropic 的 prompt caching 是 **opt-in** 的：文档里那个叫 "automatic caching" 的选项指的是断点自动前移，**仍然要在请求顶层放一个 `cache_control`**；`src/` 里没有任何 `cache_control`/`ephemeral`（可 grep 复核），`ai/server.py` 也只读 `prompt_tokens`/`completion_tokens`/`total_tokens`。所以**代价二是未来式**，当下真正在付的只有代价一 |
| **所以** | 易变块彻底移出提示词，挂到**请求尾部**（本回合 user 消息）。这不是「保住了缓存」，而是**让前缀真正稳定下来、把开启缓存变成一个可行选项**；开启本身是独立的事（动计费行为，且要先确认提示词过得了 512/1024 token 门槛、会话节奏跟得上 5 分钟 TTL）|

### 契约与容错（刻意为之）

| | 约定 |
|--|------|
| **workspace 侧签名** | `async def turn_context_builder() -> str`——不收参数（它不改写任何已有文本，只生产本回合的块），返回要挂上去的内容。**未定义即没有这个块**，老 workspace 行为不变 |
| **折进位置** | 折在消息正文**之后**。放前面会移动这一回合的每个 byte，正好抵掉「存在带外键里」想省的东西 |
| **不写回 history 行** | `turn_context` 是非上线键，与 `kind` / `chat_type` 同属 `_DISPLAY_ONLY_KEYS`：投影给 AI 时才折进 `content`，落盘行与 SPA 展示都看不到它。这样**之前每个回合投影出来都逐字节相同**，前缀才真的可复用 |
| **多模态 content** | `content` 不是 `str`（block 列表）时原样返回、丢掉这个块——没有唯一的可追加位置，丢一行时钟远好过把 block 结构写坏 |
| **构建失败** | `except Exception` 记 ERROR 后返回 `""`，不中断回合。**丢一行时钟远好过丢掉整个回合** |
| **返回值不可用** | 非 `str` / 空串 / 纯空白一律当「没有这个块」 |
| **为什么不给 `turn_context_fn` 设默认函数**（不同于 `builder` / `checker` 的「Default over None」，见根 AGENTS.md 坑 8） | 默认函数只能返回空串，那与 `None` 语义重合、却多一次无谓的 `await`；且 `None` 在这里承载**可观测的语义**——「这个 workspace 没有易变块」。`compaction_fn` 同样保持 `None` |
| **为什么不单独发一条尾部消息** | 不是因为发不出去——Anthropic 会把连续的同角色轮次**合并成一条**（"Consecutive `user` or `assistant` turns in your request will be combined into a single turn"），不报错。真正的理由是那条消息**必须落进 history 才能发出去**（`messages_for_ai()` 只投影已有行，凭空插一条就要在投影期造行），于是每回合都往历史里多塞一条一次性的时钟消息：历史被噪音撑大、压缩时还得判断哪些该丢。挂在本回合 user 消息上则一行不多、且天然随该回合一起过期 |
| **`USER.md` / `HEARTBEAT.md` 归谁** | 留在提示词里——它们是「当作长期上下文读」的散文，不是本回合的新闻。文档承诺的「re-read every turn」由 `system_prompt_rebuild_checker()` 按**内容哈希**兑现：字节真变了才整段重建，改一次付一次，而不是每回合付一次 |

## 其他约定

- AI 连接超时：`ClientTimeout(total=None)` — 语义：不超时，与 channel 一致（由 `AiClient.stream()` 管理）
- 流式 `delta` 字段可能为 `null`（非缺失 key），`AiClient` 用 `isinstance(delta_data, dict)` 校验后产出 `AiDelta`
- Tool 模块在 `sys.modules` 中以 `psi_tool_{name}_{session_id}_{file_hash}` 注册（完整 64 位 SHA-256 hash，不截断），同进程多 session 互不冲突
- Schedule 加载时捕获各种 per-task 错误（IO、YAML 解析、cron 验证），单个 schedule 失败不影响整体加载

## 协议适配层

Session 层使用两个对称的协议适配器，将 `SessionAgent.run()` 包裹为纯业务逻辑：

### AiClient（`ai_client.py`）
- 封装 HTTP/SSE 连接管理与原始解析
- `stream(request_body) → AsyncIterator[AiDelta]`
- 处理：非 200、多 choice 错误检测、心跳跳过、`[DONE]` 终止

### ChannelAdapter（`channel_adapter.py`）
- 纯无状态编解码——`parse_request()` 和 `write()` 两个入口
- `parse_request(request) → (user_message, extra_params)` — HTTP JSON 解析
- `write(response, chunks)` — 消费 `AgentChunk` 迭代器，写入 SSE 到 response
- 不持有 agent / lock 引用，不调用 `agent.run()`

### 核心类型
| 类型 | 方向 | 职责 |
|------|------|------|
| `AiDelta` | AI→SessionAgent | SSE 解析后的内部流元素 |
| `AgentChunk` | SessionAgent→Channel | 纯语义输出（`content` / `reasoning` + 可选 `kind` provenance） |
| `AgentRunOutcome` | SessionAgent→调用方 | 可选的单次调用结束原因，不进入 SSE |
| `AgentError` | SessionAgent→Channel | 不可恢复错误信号 |

## SessionAgent 支持多种传输

所有组件通过前缀自动检测传输协议（实现位于 `psi_agent._sockets`）：

`AiClient` 端（`resolve_connector_and_endpoint`）：
- `http(s)://host:port` → `TCPConnector`
- `\\\\.\\pipe\\name` → `NamedPipeConnector`（Windows only）
- 裸文件系统路径 → `UnixConnector`

服务器端（`create_site`）：
- `http(s)://host:port` → `TCPSite`
- `\\\\.\\pipe\\name` → `NamedPipeSite`（Windows only）
- 裸文件系统路径 → `UnixSite`

两端都会做平台门控，避免深处抛出无上下文的异常：
- Windows 上传裸路径（含被误引成单反斜杠的 `\.\pipe\...`）→ 抛 `ValueError`，提示改用命名管道地址；否则 asyncio 无 `create_unix_connection`，aiohttp 会抛裸 `NotImplementedError`。
- 非 Windows 上传 `\\\\.\\pipe\\name` → 抛 `ValueError`，提示改用 Unix socket 或 TCP 地址；否则 aiohttp 的 `isinstance(..., asyncio.ProactorEventLoop)` 门控本身会因该属性在非 Windows 不存在而抛 `AttributeError`。
- bash 里传管道地址要用四反斜杠 `'\\\\.\\pipe\\...'`，保证程序收到两根反斜杠开头。

## Tool 加载约定

- `workspace/tools/*.py` 中的每个 `.py` 文件（不含 `_` 开头）
- 文件中所有非 `_` 开头的 `async def` 函数都会被加载为 tool
- 内部以 per-file 结构存储（`FileEntry` dataclass），包含 `file_hash`、`tools`（ToolFunction dict）、`funcs`（callable dict）、`fresh`（是否本次导入）
- `ToolRegistry.tools` 为 `@property`，展平所有 `FileEntry` 为 `dict[str, ToolFunction]`
- 参数类型必须为 `str`、`int`、`float`、`bool`、`list[X]` 或 `X | None`（`Optional[X]`）
- `*args`、`**kwargs` 和多类型 Union（`int | str`）不支持，抛 `TypeError`
- `from_callable()` 的各种异常（类型校验、annotation 解析等）均被捕获，只跳过该 tool 不中断整体加载
- 只支持 Google-style docstring（`Args:` 段落，`Returns:` 和 `Yields:` 作为描述结束标记）
- 用 `inspect.signature()` 提取参数（类型注解 → JSON Schema 类型）
- 用 `inspect.getdoc()` 提取描述（支持 Google-style 的 `Args:` 格式）
- 跨文件同名 tool 以后加载者覆盖（`tools` property 展平时 `dict.update` 自然行为）

## 动态重载

`ToolRegistry.refresh(session_id)` 在每次 agent turn 前自动调用，检测文件变更并增量更新：

```python
# refresh() → dict[str, str]  {'echo': 'added', 'bash': 'skipped'}
```

- 扫描 `workspace/tools/`，按 `FileEntry.file_hash` 检测变更：
  - hash 不变 → 复制旧 FileEntry（`fresh=False`），tool 标记为 `skipped`
  - hash 变化 → 重新 `compile` + `exec`（`fresh=True`）
  - 新文件 → 导入并标记 `added`
  - 文件删除 → 其所有 tool 标记 `removed`
  - 文件内 tool 增删 → 分别标记 `added` / `removed`
- `fresh` 标志保证 skipped 文件不被误删
- `ScheduleRegistry` 以 per-file `ScheduleEntry` 存储（含 hash），`refresh()` 支持 add/update/remove/skip。每个 schedule 有独立 `CancelScope`，update/remove 时取消旧 runner 并启动新 runner。`refresh()` 内部已 try/except，失败时 log warning 返回 `{}`，不修改内部状态，调用方可直接 await 无需自行容错
- Schedule 刷新的两个时机：
  1. 每次 `run()` 入口（turn 开始），与 tool 一并刷新
  2. `finish_reason="stop"` 后（turn 结束），仅刷新 schedule——因本轮 tool 可能修改了 workspace schedules 下的文件，需立即生效，不等下次 turn

## Tool 调用细节

**参数类型解析**：
由于项目全量使用 `from __future__ import annotations`，函数注解以字符串形式存储。因此 `ToolFunction.from_callable()` 必须用 `typing.get_type_hints()` 解析，**不能**直接读 `param.annotation`。

**流式 Tool Call 累积**：
AI 的 tool_calls 通过 SSE 流式传输——多个 chunk 中的 `delta.tool_calls` 逐步补充同一 index 的参数。Agent 用 `accumulated_tool_calls: dict[int, dict]` 按 index 累积：
- `id`：取第一次非空值
- `function.name`：取第一次非空值
- `function.arguments`：**拼接**所有 partial JSON 片段

同时累积 `reasoning`（AI 的思考过程）——DeepSeek V4 等 reasoning model 要求 tool call 轮次中 `reasoning` 必须完整回传到 API。

收到 `finish_reason="tool_calls"` 后，按 index 排序生成完整 tool_calls 列表，逐一执行。

**Tool 执行容错**：
- `arguments` 不是合法 JSON，或解析结果不是 JSON object → 返回明确的错误 tool result，且不调用 Tool；合法的 `{}` 仍可用于零参数 Tool
- Tool 函数可能抛异常 → 以错误文本作为 tool result 返回，不中断 agent loop
- Tool 返回非字符串（int, None） → 通过 `str()` 强转

## Schedule 机制完整流程

```
每个 schedule 一个 run_one_schedule() coroutine：
  while True:
    _seconds_until_next(cron)   ← 本地墙钟下次触发（勿用 time.time() 作 croniter base；TZ 设了则按该时区）
    await anyio.sleep(wait)     ← 睡到触发
    async with agent._lock:       ← 等当前请求完成
      if fire == tool:
        ToolRegistry.get(tool)(**tool_args)  ← 直调，不跑 LLM（飞书提醒等）
      else:  # fire == prompt（缺省）
        user = {role:user, content:TASK.md, kind:schedule.silent}  ← user 始终 silent
        agent.run(user, response_kind=display|silent)              ← 由 TASK.md visibility 决定
      ← 整轮写入 JSONL（带 kind）；display 才 stash pending；silent 不注入下一轮 SSE
      ← run_once 成功后删 TASK.md 并结束 runner
```

关键点：
- Schedule 配置：`name, cron, task_content, visibility`（`display`/`silent`，缺省 `display`），以及 **`run_once`**（缺省 `false`），以及 **`fire`**（`prompt` 缺省 / `tool`）
- **`fire: tool`（刻意为之）**：到点 Session **直接** `ToolRegistry.get(tool)(**tool_args)`，**不跑 LLM**。用于飞书提醒等必须可靠推送的场景；YAML 含 `tool` + `tool_args`。`fire: prompt` 仍把 TASK 正文当 user message 交给 agent（heartbeat / 日报等）。workspace `schedule_manage` 对飞书提醒应写 `fire=tool`
- **`run_once: true`（刻意为之）**：成功跑完一轮后删除对应 `TASK.md`（及空目录）并结束该 runner，避免「单次提醒」因 5 段 cron 无年份而次年再触发。workspace 工具 `schedule_manage` 的 `once_at` 会写入此字段
- **cron 按本地时间解释（刻意为之，勿改回 UTC）**：`_seconds_until_next` 用 `datetime.now()` + `croniter`，**禁止**把 Unix timestamp 交给 `croniter` 当 base——后者会把 5 段字段当 UTC，导致 `once_at` 写的本地时刻在非 UTC 机器上晚数小时才触发。workspace `schedule_manage` 的 `once_at`/`cron` 语义都是本机墙钟。此外若设了标准 `TZ` 环境变量，`ScheduleRegistry._schedule_tz()` 解析成 `ZoneInfo` 并以 `datetime.now(tz)` 作 base，让 cron 字段按该时区解释（如 UTC 容器设 `TZ=Asia/Shanghai` 则 `0 9 * * *` 按北京 9 点触发）；`TZ` 未设 / 非法时退回 naive `datetime.now()`，行为与默认一致，不额外依赖 `tzdata`
- **消息 ``kind``（JSONL provenance，敲定协议）**：OpenAI ``role`` 不变；用正交字段区分对话来源（``chat`` / ``schedule.display`` / ``schedule.silent`` / …）。Gateway ``/history`` 只返回 ``is_displayable_chat_message``。AI 请求经 ``messages_for_ai`` 剥掉消息 ``kind``/遗留 ``chat_type``。**≠** SSE / ``AgentChunk.kind``（``thinking`` / ``tool_call`` / ``tool_result``）——后者只标过程流 provenance，不进 history 白名单语义
- ``visibility: silent`` 的 schedule（heartbeat）结果永不 pending、永不展示
- ``visibility: display`` 的 schedule 结果可进 history，并通过 pending 随下次 ``POST /chat`` 带回（``/events/schedule`` 推送通道仍待定）
- `fire: prompt` 触发只是 Session 内再跑一轮 agent（TASK 正文当 user message）——**不会**自动往飞书推 IM；`fire: tool` 才按 YAML 直调工具（如 `feishu_message_send`）
- Schedule 响应的 content 和 reasoning 各自存在于各自的消息周期，不会交错
- 多个 schedule 可以并发 sleep，但通过 lock 串行触发
- 每个 schedule 在加载时独立处理——IO 错误、YAML 解析问题、cron 验证失败都只跳过该 schedule

### History 展示白名单（``history_display.py``）

| kind | 展示 |
|------|------|
| `chat` | user/assistant 非空 content |
| `schedule.display` | 仅 assistant |
| `schedule.silent` / `compacted` | 否 |
| 遗留 `chat_type=schedule` / `*_schedule` role | 视为 silent |

Gateway ``HistoryManager`` 同时投影剥掉 ``[SEND:]``/``[RECV:]`` 标记。

## History 持久化

Session 支持将对话历史持久化到 AppData `histories/{session_id}.jsonl`（第 4C）：

- **写**：始终 `{appdata}/histories/{session_id}.jsonl`（`appdata` = `Session.appdata` / `PSI_APPDATA` / platformdirs）
- **读**：优先 AppData 文件；缺则双读 legacy `{workspace}/histories/{session_id}.jsonl`
- `Session.session_id: str | None = None` — None 时自动生成 UUID，给定字符串时可 resume
- 加载：`SessionAgent.create()` → `Conversation.from_workspace(..., appdata_root=…)` 双读
- **Turn 级别原子性**：`SessionAgent.run()` 每次调用通过 ``async with self._conversation`` 进入上下文管理器，首次 `add()` / `replace_system()` 自动建立快照。user message 追加后立即 `commit()`（早期落盘，崩溃恢复基线），后续仅在对 AI 响应成功的检查点再次 `commit()` 更新；任何异常（AI error、连接断开、cancellation）都会通过 ``__aexit__`` 自动触发 `Conversation.rollback()` 恢复到快照，保证内存和磁盘始终同步于最近一个成功阶段。
- 保存时机（一致性检查点）：
  - `finish_reason="stop"` — assistant 响应追加后立即 `commit()`，随后刷新 schedule registry（完整回合）；若收到 compaction 信号则 `_maybe_compact()` 插入 `compacted` 消息并 `commit()`
  - `finish_reason="tool_calls"` — 所有 tool 结果追加后立即 `commit()`（子回合）
  - unexpected `finish_reason` — 累积 content 追加后 `commit()`
  - 达到 `max_tool_rounds` — 追加 `[Max tool rounds reached]` assistant 消息后 `commit()`
- `Conversation.save()` 使用 tempfile + `os.replace()` 实现原子写入；`commit()` 封装 save + 清除快照
- **部分保存**的场景：`finish_reason="error"`、AI 连接断开、channel 断开、schedule runner 异常——user message 已通过早期 `commit()` 落盘，AI 响应部分通过 `rollback()` 回滚，不写入磁盘
- 首次使用时自动创建 AppData `histories/` 目录 + `.gitignore`（忽略全部文件）

## Context Compaction

当 AI 层返回 `psi_compaction` 信号时，Session 触发上下文压缩。流程：

1. `AiClient.stream()` 解析 `psi_compaction` → `AiDelta.compaction_needed=True`，并把
   `prompt_tokens` / `threshold` 一并透出（经 `AiClient._as_int`，缺失或非法为 0）
2. Agent loop 在 `finish_reason="stop"` 后调用 `_maybe_compact(prompt_tokens, threshold)`
3. 从 `{agent}/systems/system.py` 提取 `compact_history()` 函数
4. **冷却门槛**：`_compaction_cooldown_elapsed()` 不过则直接返回（见下）
5. 构造 `complete_fn`（使用现有 `AiClient` 做流式调用并收集全部 content 的闭包）
6. `summary = await compact_history(conversation.messages, complete_fn)`
7. 插入独立的 `compacted` 消息（`role="compacted"`, `kind="compacted"`）到 conversation
8. `commit()` 落盘——历史消息**保留**，不删除；随后记录水位线
   `_tokens_at_last_compaction`（**仅成功时**记，失败没缩小任何东西，下次信号仍应放行）
9. 下次发送 AI 请求时，`messages_for_ai()` 负责：找到 system prompt 和最后一个 compacted，删除中间消息，将 compacted 内容合并到 system prompt

JSONL 留存：``system, u1, a1, u2, a2, compacted(summary), u3, a3, ...``
发给 AI：``[system+summary, u3, a3, ...]``

`compact_history` 约定签名：

```python
async def compact_history(
    history: list[dict[str, Any]],
    complete_fn: Callable[[list[dict[str, Any]]], Awaitable[str]],
) -> str:
```

未定义时 → 记录 warning，跳过压缩，history 持续增长。
多次 compaction → 每次插入独立的 `compacted` 消息；`messages_for_ai()` 仅取最后一条合并到 system prompt。
这一步安全的前提是默认实现**链式累积**（新摘要在上一份之上更新，故包含而非丢弃更早
上下文）；若自定义的 `compact_history` 忽略传入的 `compacted` 行，则每压一次就少一层
历史——这正是「时不时压缩就忘记前面对话」的成因。

### 压缩冷却（`COMPACTION_COOLDOWN_FRACTION = 0.1`，刻意为之，勿"修掉"）

信号只表示 `prompt_tokens` 超了阈值，而压缩**改不了 system prompt 的体积**。当提示词
本身占阈值很大比例时（实测 `haitun-workspace` 提示词约 45.4K token = 100K 默认阈值的
45%），信号会每回合复发；没有门槛的话 Session 就会连续重压，每次白付一次 LLM 调用还
削掉一层更早的上下文。故要求自上次**成功**压缩起 `prompt_tokens` 增长达
`threshold * COMPACTION_COOLDOWN_FRACTION` 才允许再压。

- **按 token 而非消息条数计量**：单条 tool 结果可达数万 token，而两条聊天消息只有几百，
  条数门槛对重工具场景毫无意义
- 信号缺数字时 **fail open**，保持改动前行为
- 水位线存 `SessionAgent` 实例属性——该对象每 session 进程建一次，跨回合有效；进程重启
  归零（可接受：重启后最多多压一次）

### peek_pending / clear_pending 安全机制

`Conversation.peek_pending()` 返回 pending chunks 的副本但**不清空** buffer——调用方在 yield 全部成功后显式调用 `clear_pending()`。这保证 channel 断开时 pending schedule chunks 不会永久丢失，下次请求会重新 push。
