# Channel 层设计文档

## Channel 层架构

```
channel/
├── _types.py          # FileChunk, TextChunk, ReasoningChunk, InputChunk, OutputChunk
├── _errors.py         # ChannelError 基类（传输/协议/session 错误统一抛出）
├── _markers.py        # [RECV:]/[SEND:] 标记协议（纯函数 encode_input + 有状态扫描器 SendMarkerScanner）
├── _stream.py         # SSE 解析 iter_sse_events + interval 缓冲 StreamBuffer（与传输解耦）
├── _core.py           # ChannelCore — 连接管理 + post() 编排
├── cli/
│   ├── __init__.py     # ChannelCli dataclass
│   └── client.py       # 单次消息 thin client (~32行)
├── repl/
│   ├── __init__.py     # ChannelRepl dataclass
│   └── client.py       # 交互式 thin client (~57行)
├── telegram/
│   ├── __init__.py     # ChannelTelegram dataclass
│   └── client.py       # Bot handler + 流式 + 文件收发 (~186行)
└── feishu/
    ├── __init__.py       # ChannelFeishu dataclass
    ├── _card_action.py   # 交互卡片回调解析、单次消费、上下文信封与确定性分发 (~350行)
    ├── _card_store.py    # AppData 卡片快照与持久 single-use tombstone (~150行)
    └── client.py         # Bot 生命周期、通用流式回复、文件收发、评论/审批事件与按用户路由 (~820行)
```

### ChannelCore

ChannelCore 是所有 Channel（CLI、REPL、Telegram）共享的公共部件：

- async context manager，管理 aiohttp ClientSession
- `post(list[InputChunk]) -> AsyncIterator[OutputChunk]`：InputChunk → 字符串 → POST → SSE → OutputChunk
- 将输入中的 FileChunk 转换为 `[RECV:/path]` 标记（session 端负责读文件）
- 检测输出中的 `[SEND:/path]` 标记并产生 FileChunk
- 将 SSE 的 `delta.reasoning` 流切分为 `ReasoningChunk`（透传可选 `delta.kind`），与 `content`（`TextChunk`）按到达顺序交错产出；同槽不同 `kind` 在 buffer 内视为不同活动类型（不合并）；`[SEND:...]` 仅扫描 content
- SSE 内容在 interval 窗口内缓冲合并为单个 TextChunk（默认 1s，可配置）
- 终端通道（CLI/REPL）设置 interval=0 无需缓冲
- 内部委托：marker 编解码 → `_markers.py`；SSE 解析与 interval 缓冲 → `_stream.py`（均与 HTTP 传输解耦、可独立单测）
- 取消安全：`__aexit__` 关闭 aiohttp `ClientSession` 用 `anyio.CancelScope(shield=True)` 保护（与 AI 层一致），cancel 时不泄露连接
- `post()` 是 async generator（返回 `AsyncGenerator[OutputChunk]`，与 `AiClient.stream` 对齐而非 `AsyncIterator`，使 `aclosing` 可类型检查）；所有 channel 客户端（cli/repl/telegram/feishu）消费时一律用 `async with aclosing(core.post(...))` 包裹（对标 `agent.py`/`channel_adapter.py`/`schedule_registry.py` 的统一约定），确保提前退出 / 被 cancel 时 `post()` 内的 `session.post()` 响应被释放
- `_stream.iter_sse_events` 与 `AiClient` 同款 JSON 守卫与日志级别：坏 JSON、非 list `choices`、非 dict `choice` 跳过并以 **WARNING** 记录（与 `ai_client.py` 一致；`[DONE]` 与 0-choice 心跳属正常流，仍记 DEBUG），缺失或 `null` 的 `delta` 归一为 `{}`，故 `post()` 中 `delta.get(...)` 永不触 None。`iter_sse_events` 返回 `AsyncGenerator` 且在 `post()` 中以 `async with aclosing(...)` 消费——aclosing 约定贯穿 client→`post`→`iter_sse_events` 全链
- **（刻意为之）`_session`/`_endpoint` 不在 dataclass 中声明**：二者在 `__aenter__` 赋值、在 `post()` 中无条件使用；若声明为字段则需 `X | None`，会在 `post()` 引入 Optional narrowing（被迫 assert 或 `# ty: ignore`，违反零抑制）。由 async context manager 保证"先 `__aenter__` 再 `post()`"的时机，故保留为动态属性——勿当 bug "修复"
- **`post_event(envelope)`**：同一 socket 上 ``POST /events``（触发器统一转发）。业务事件定义在 **agent 包** ``channel_events/<channel>/``（``EVENT.yaml`` + ``map.py`` 或 ``produce.py``），由各 Channel 加载后调用本方法；**不**把事件清单写进 ``src/psi_agent/channel``。合成生产者见 ``_synthetic.py``。见 ``docs/superpowers/specs/2026-07-29-channel-events-in-agent-package.md``。

Channel 客户端不再直接处理 HTTP、SSE 解析或错误格式。

## 概述

Channel 层是 psi-agent 的用户界面层，负责连接 Session socket 并通过 SSE 流式显示 AI 回复。

提供四种交互模式：
- **CLI**（单次消息） — 发送一条消息，显示回复，退出
- **REPL**（交互式） — 持续对话
- **Telegram**（bot） — 通过 Telegram Bot 交互，支持文件收发、流式编辑
- **Feishu**（bot） — 通过 Feishu Bot 交互，支持卡片流式渲染、文件收发

## 终端输出约定

- Channel 客户端（repl、cli）是终端 UI 程序，需要格式化输出
- **使用 `rich.console.Console`** 替代 `print()`
- 思考过程（reasoning）：`ChannelCore` 产出 `ReasoningChunk`，CLI/REPL 以 `console.print(..., end="", style="dim")` inline 渲染（Telegram/Feishu 忽略）
- 错误信息：`console.print("[red]Error: ...[/red]")`
- REPL 欢迎页：`console.print(Panel(...))`
- **`Console(highlight=False)`**：禁用自动语法高亮，避免 Rich 误把 AI 回复当代码着色
- **整个仓库不允许 `print()`**——T20 (flake8-print) 规则强制，无 per-file-ignore

## REPL 约定

- 使用 `prompt_toolkit` 的 `PromptSession(multiline=True)`
- `Enter` 换行，`Alt+Enter`（Escape+Enter）发送
- PS1: `> `，PS2: `. `（同宽对齐）
- `Ctrl+D` 退出

## CLI 约定

- 连接 session socket，发送 `--message`，SSE 流式接收后退出
- ``--message -`` 从 stdin 读取消息内容，`run_cli()` 内部通过 `await anyio.to_thread.run_sync(sys.stdin.read, abandon_on_cancel=True)` 异步读入，规避 OS 命令行参数长度限制
- 错误：打印错误信息后 raise（不再 `sys.exit`，以支持非 CLI 上下文）
- 不发送 history，每次只带一条 user message

## Telegram 约定

- 通过 python-telegram-bot 异步 API（initialize/start/start_polling）进行 long polling
- 所有消息类型（`filters.ALL`）包括 slash command 均传递给 agent
- 文本通过 `edit_text` 增量累积实现流式效果，完成后以 Markdown 格式最终渲染
- FileChunk 通过 `reply_photo` / `reply_document` 发送；用户文件下载至 `Downloads/.psi/<date>/`
- 输入文件（photo/document）自动下载并作为 FileChunk 传给 agent
- 支持 SOCKS5 proxy（`--proxy` CLI arg > `PSI_TELEGRAM_PROXY` env）
- 用户白名单：`--allowed-user-ids` 参数或 `None`（不限制）

## Feishu 约定

- 通过 lark-channel-sdk 的 `FeishuChannel.start_background()` 建立 WebSocket 长连接（SDK 推荐的 async 启动：后台拉起、握手就绪即返回；`connect()` 是旧的前台阻塞式），关停用 `stop_background()`
- **触发器事件（agent ``channel_events/feishu``）**：``--agent`` / ``PSI_AGENT`` 指向 agent 包；``start_background()`` 之后 ``register_feishu_agent_events``：（1）``kind=platform_map`` 按 ``platform_event`` 注册 CustomizedEventProcessor，``map.py`` → ``post_event``；（2）``kind=synthetic`` 由统一 runner（``_synthetic.start_synthetic_producers``）在 TaskGroup 里跑各目录 ``produce.py`` 的 ``async produce(ctx)``，``await ctx.emit`` → 同一 ``post_event``。**（刻意为之）** 业务清单只在 agent 包维护（≈ 加 tool）；Feishu 已接线后新增事件**不要**再改 ``src/…/channel``。新业务 ``event`` 默认只动 agent；**新**信封 ``source`` 才外加 Session ``KNOWN_SOURCES``（见 ``session/AGENTS.md`` / developer-guide）。交付准则见 ``docs/superpowers/specs/2026-07-29-channel-events-developer-guide.md``。产品用语：**触发器**（旧称「定事」已弃用）。
- **并发模型（刻意为之）**：lark SDK 在自己的后台线程/event loop 上派发消息回调；`_on_message` 通过 `anyio.from_thread.BlockingPortal.start_task_soon` 把处理协程桥接回主 anyio loop（取代 asyncio `run_coroutine_threadsafe`，遵守「一切异步用 anyio」原则）。`run_feishu` / `run_telegram` 把**启动调用**（telegram: initialize/start/start_polling；feishu: start_background）一并纳入 `try`，`finally` 用 `anyio.CancelScope(shield=True)` 保护——**启动中途失败与正常 cancel 两条路径都会执行关停**，不泄露 bot 连接。**（刻意为之）关停按步骤 best-effort：逐个 `try/except Exception` 吞掉清理异常并 WARNING**——partial-startup 下库会抛 "not running" 之类错误，吞掉以免遮蔽原始异常或中断后续 teardown；`except Exception` 不吞 `CancelledError`，勿把这层 swallow 当 bug "修掉"
- **（刻意为之）`_handle_and_stream` 外层防御 try/except**：它是 `start_task_soon` 投递的任务，内部任何未捕获异常（包括错误通知 `channel.send` 失败）都会逃逸到 portal。外层 `except Exception` 兜底并记录 ERROR，确保单条消息处理崩溃不拖垮整个 bot；不吞 `CancelledError`，勿把这层 try 当 bug "修掉"
- 所有消息（text/post/file/audio）均转化为 InputChunk：文本→TextChunk，文件→下载→FileChunk
- `<audio key="..."/>` inline 标签通过 `message_resource.aget()` API 下载
- 通过 `channel.stream()`  + `stream.append()` 实现卡片流式渲染
- FileChunk 通过 `channel.send()` 发送文件；用户文件下载至 `Downloads/.psi/<date>/`
- 认证：`--app-id` + `--app-secret` CLI args > `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` env
- 用户白名单：`--allowed-user-ids` 参数或 `None`（不限制）
- 处理状态表情（参考 Hermes）：收到白名单消息后立即在该消息上加 `Typing` 表情（`message_reaction.acreate`），回复完成后移除；处理失败则替换为 `CrossMark`。表情操作失败安全，不影响回复
- **群聊 @ 触发（准入策略）**：`require_mention`（默认 True）/ `respond_to_mention_all`（默认 False）经 `run_feishu` 构造 lark SDK 的 `PolicyConfig` 传入 `FeishuChannel(policy=...)`。群聊（chat_type=group/topic）仅在 @机器人时才触发 `on("message")`，未 @ 的走 `on("reject")`；单聊（p2p）默认全响应。**（刻意为之）** 该策略门由 lark SDK 内置，@机器人 判定依赖机器人 `open_id`——`FeishuChannel` 启动时自动拉取；`_ensure_bot_identity` 在 `start_background()` 后兜底重试 `resolve_bot_identity()` 一次，失败仅 WARNING（群 @ 检测不可用但不阻断启动，因单聊仍可用）。`_log_reject` 注册 `on("reject")` 把被拒消息按原因记 DEBUG，便于排查"@ 了不回复"
- **消息元数据注入（`_context_header`）**：发给 agent 的文本最前面注入一段 `<feishu_context>` 块（chat_id / chat_type / message_id / sender_open_id，可选 sender_name / thread_id）。**（刻意为之）只含客观协议事实、绝不含具体 workspace 工具名**——channel 层与 workspace 工具解耦（微内核理念：框架传协议，功能由 workspace 定义）。agent 如何用 chat_id 拉群历史 / 读文档的引导放在 workspace 的 `TOOLS.md`。header 仅在有真实内容（文本/音频/资源）时随内容一并注入；纯元数据（无任何内容）时 `_build_chunks` 丢弃 header 返回空列表，保持"unsupported message type"语义不被元数据破坏
- **交互卡片回调上下文与确定性分发**：Haitun workspace 的 `feishu_message_send_card` 在发送成功后按 `message_id` 把原始卡片、发送方 Session / open_id、接收目标、业务上下文和 `action_id -> handler` 映射写入 AppData 的 v2 snapshot；`_card_action.handle_card_action` 原子领取 snapshot 后，仍按操作者 `open_id` 解析其 agent Session，并把 `<feishu_card_action>` JSON 作为一条结构化 user 消息送入该 Session，在原飞书会话流式回复。卡片回调模块通过参数接收 `resolve_core`、进程内去重函数和 `client._stream_reply`，避免反向导入 `client.py`；`client.py` 只负责注册回调及通用流式/生命周期逻辑。信封固定包含 `schema_version`、`source`、`card`、`business_context`、`dispatch`、`action`；`dispatch` 给出 `action_id` / `handler` / `matched` / `strategy`。**（刻意为之）Channel 只做确定性选择，不直接执行 handler，也不绕过 LLM**：配置了非空 handler 映射时只允许 canonical action ID 精确命中，未知 action 返回 `matched=false, handler=null, strategy=action_handlers`，agent 不得臆造或执行未匹配 handler；只有成功读取且确认未配置映射的 v1/v2 snapshot，才为兼容旧卡片把 `action.value.action`（或 `action_id`）作为 handler，`strategy=action_id`。snapshot 缺失/读取异常时 fail closed，分别使用 `strategy=snapshot_unavailable` / `snapshot_invalid` 且 handler 为空。首个回调把 snapshot 原子改名为持久 `.consumed` tombstone 并最小化其内容；后续进程或重启后的重复回调看到 tombstone 就直接忽略，不再仅依赖进程内 `_SeenEvents`。Channel 的自定义 `appdata` 必须与 Gateway/workspace tool 解析到同一根（推荐统一设置 `PSI_APPDATA`）；不同根会安全地 fail closed，但点击者拿不到发卡业务上下文
- **卡片回调静默成功**：回调 agent 的成功路径应依靠原卡片“已选择”状态完成确认，不生成重复点击说明、处理预告或成功确认；无额外必要信息时输出零 assistant 文本。Feishu 仅在卡片回调流中防御性识别独立的 `NO_REPLY`：它支持任意 SSE 分片，并在 tool result 后重新识别；只有完整候选段 `strip()` 后严格等于 `NO_REPLY` 才吞掉，其他文本（尤其警告、部分失败、权限问题和必要后续步骤）原样流式发送。普通 Feishu 消息不启用该过滤
- **文档评论 @机器人 回复（`respond_to_comments`，默认 True）**：飞书文档评论区 @机器人 会推送 `drive.notice.comment_add_v1` 事件；`run_feishu` 在开关开启时注册 `channel.on("comment", ...)`，回调经 `portal.start_task_soon(_handle_comment, ...)` 调度（与 `_handle_and_stream` 同款异步隔离，异常绝不冒泡）。触发门槛与群聊一致——**仅当评论明确 @了机器人（`CommentEvent.mentioned_bot`）才回复**，其余记 DEBUG 跳过，白名单同样按 `operator.open_id` 生效。流程：`resolve_comment_target`（doc/docx/sheet/file/wiki，不支持则 WARNING 跳过）→ `get_comment_context`（拿 `question` 问题文本 + `quote` 锚定原文）→ 喂 `core.post()` 后 **`_collect_reply` 累积成整段文本**（评论 API 是一次性写入，不支持 IM 卡片式增量流式；`FileChunk` 评论区无处安放，记 DEBUG 忽略）→ **回复前强制 `ctx.is_whole = True` 再 `channel.reply_comment(ctx, text)`**。agent 失败时把错误文本回复到评论。**（刻意为之，数据安全）** SDK `reply_comment` 对 `is_whole=False`（锚定文字的评论）走 `PUT .../replies/:reply_id`——那是**更新覆盖**某条 reply，且 `target_reply_id` 恰是用户 @机器人 的那条 reply，会把用户原话抹掉（数据丢失）；SDK 未提供"在已有评论下无损追加 reply"的接口，故一律强制走 `is_whole=True` 分支（`POST .../comments` 新建整条评论），代价是回复另起一条评论而非挂在原线程下，换零数据丢失——**勿当 bug "修复"回 `reply_comment` 默认路径**。评论 header（`_comment_context_header`）同样只含协议事实（file_token / file_type / comment_id / operator_open_id / quote）、不含工具名。**依赖飞书后台订阅 `drive.notice.comment_add_v1` 事件并开启文档评论权限**，否则收不到事件（代码兜底记日志）
- **审批状态变化主动推送（事件驱动，非轮询）**：员工提交的飞书审批在**状态变化**（通过/驳回/撤回等）时，连接的 app 主动把结果推送给**申请人本人**。分两半实现——workspace 侧 `feishu_approval_subscribe(approval_code)` 工具调 `POST /approval/v4/approvals/:approval_code/subscribe`（tenant token，幂等，每个审批定义订阅一次即可）开订阅；channel 侧负责收事件并推送（事件是经长连接**推**来的，workspace 工具 pull 接不到，故只能在 channel 层收）。**（刻意为之）SDK 无 typed processor**：lark-channel-sdk 1.2.0 未给 `approval_instance` 事件提供归一化 processor，故 `_register_approval_processor` 走 SDK 内部的 `CustomizedEventProcessor` 注入 `dispatcher._processorMap` 的 `p1.approval_instance` / `p2.approval_instance` 两个 key（与 SDK 自身处理 drive 评论同款逃生口）；**必须在 `start_background()` 之后注册**——`start_background` 会重建 dispatcher，提前注册会被覆盖。任何 SDK 内部结构缺失/改名都降级为 WARNING、绝不拖垮启动。**（刻意为之）事件载荷只有 `approval_code`/`instance_code`/`status`，无推送目标**：故 `_handle_approval_event` 先 `_fetch_instance_detail`（`GET /approval/v4/instances/:instance_id`，tenant token）解析出**申请人 open_id** 再 DM 推送；channel 不能 import workspace 工具（微内核解耦），故自行手搭 `BaseRequest`。回调经 `portal.start_task_soon` 桥回主 anyio loop（SDK 回调在后台线程），外层 try/except 兜底异常绝不冒泡。**去重**：飞书事件可能重投，`_SeenEvents`（有界 FIFO，maxlen=512）按 `instance_code+status+operate_time` 去重。白名单按**申请人 open_id** 走 `_allowed`；解析不出申请人则记 DEBUG 跳过。事件 header（`_approval_event_header`）**（刻意为之）只含协议事实**（approval_code / instance_code / status），不含工具名。**依赖飞书后台订阅审批事件并给机器人 `approval:approval` 权限**，否则收不到事件（代码兜底记日志）。取消订阅用 `feishu_approval_unsubscribe(approval_code)`
- **按会话独立渠道（`gateway_url`，默认 None）**：设置后同一飞书机器人对不同飞书**会话**提供**各自独立**的 Session。`run_feishu` 用 `AsyncExitStack` 持有所有 per-chat `ChannelCore` + 一个 REST `aiohttp.ClientSession`；`resolve_core(open_id, *, chat_id="", chat_type="")` 回调**在白名单通过后才解析**（被拦用户不建连接，防非白名单 open_id 刷出大量 `ClientSession`），经 `_GatewayRouteProvider.ensure` → Gateway `POST /feishu/route` 幂等拿回该会话 session 的 `channel_socket`，再经 `_CoreRegistry.get` 缓存复用对应 `ChannelCore`。`_handle_and_stream` / `_handle_comment` / `_handle_approval_event` 的 `core` 参数因此是 `resolve_core` 回调（分别按消息的 `sender_id`+chat 事实 / `operator.open_id` / 申请人 open_id 路由），类型是 `ResolveCore` Protocol 而非 `Callable[...]`——因为回调带 keyword-only 参数，`Callable` 表达不了。**路由/spawn 决策权全在 Gateway 侧的 `FeishuManager`**（含路由键、所挂 AI 与 workspace 子目录），channel **只如实上报 `open_id`/`chat_id`/`chat_type` 三个协议事实**、自己不判断该按哪个键路由，也不 spawn、退出也不删——对比早期把路由塞进 channel 内部直接调 `/sessions` 的做法。**（刻意为之）本地缓存键必须与 Gateway 的路由键同款判定**（`_GatewayRouteProvider._cache_key`：`chat_type` 为 group/topic 且 `chat_id` 非空 → `chat:<chat_id>`，否则 `open_id`）：同一个群里不同人发言必须命中同一条缓存，否则每个发言者各打一次 Gateway、各建一个 `ChannelCore` 连到同一个 socket。这是 channel 侧唯一一处「复制」了 Gateway 的判定逻辑，改动时两处必须同步。评论 / 审批推送 / 卡片回调等无 IM 会话的场景不传 chat 事实，自然落到 `open_id` 分支（`_card_action` 按操作者 `open_id` 解析，群卡片的点击因此仍落到点击者私聊 session——已知留白）。**（刻意为之，取消安全）** `AsyncExitStack` 与 `BlockingPortal` 的进出顺序：portal 后进先出、先于 stack 关闭，保证在飞的 handler 仍能用到活着的 core / http，与旧版「core 在 `stop_background` 之后才关」等价。**并发安全**：`_CoreRegistry` 与 `_GatewayRouteProvider` 均用「快路径 dict 读 + 慢路径 `anyio.Lock` double-checked」，消除同一键并发消息各建一个 core / 各发一次路由的竞态。**降级**：Gateway 不可达或路由失败时 `resolve_core` 回退共享 `session_socket`（用户总能得到回复，只是不隔离），且路由失败**不写缓存**，下条消息重试。`gateway_url=None`（默认）时行为与今天完全一致（全体共用 `session_socket`）。
- **群聊整群共用一个 Session（刻意为之，勿"修"成按发言者拆）**：群消息按 `chat_id` 路由，全群一份上下文/workspace/历史——A 问完 B 追问「那第二点呢」时机器人看得见 A 那轮，这正是群聊该有的连贯性。区分「谁在说话」靠 `_context_header` 每条消息注入的 `sender_open_id`（协议事实，已有机制，无需新增），不靠拆 session。派生规则与隐私级的 session-id 撞名坑见 `gateway/AGENTS.md`「FeishuManager」。**已知留白**：群 workspace 只有一份而 UAT 按发送者 `open_id` 存，「以谁的身份写文档」由 workspace 工具按每条消息的 `sender_open_id` 决定，channel / Gateway 都不做约定。
