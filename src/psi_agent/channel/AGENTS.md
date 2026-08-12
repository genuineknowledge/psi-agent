# Channel 层设计文档

## 请求 trace_id

`ChannelCore.post(..., trace_id=...)` 以 `X-Psi-Trace-Id` 请求头和私有 `routing.trace_id` 同时发送同一 UUID。
缺失时在 Channel 边界创建；Session 响应头和 `router_status.trace_id` 若存在则必须与当前请求一致。
普通 CLI/REPL/Telegram/Feishu 调用无需自行生成，Gateway 可传入由 SPA 创建的值。

## Channel 层架构

```
channel/
├── _types.py          # FileChunk, TextChunk, ReasoningChunk, RouterStatusChunk, InputChunk, OutputChunk
├── _errors.py         # ChannelError 基类（传输/协议/session/附件下载错误统一抛出）
├── _markers.py        # [RECV:] 标记 + encode_input + 有状态扫描器 SendMarkerScanner；[SEND:] 解码重导出自 `psi_agent/_send_markers.py`
├── _stream.py         # SSE 解析 iter_sse_events + interval 缓冲 StreamBuffer（与传输解耦）
├── _core.py           # ChannelCore — 连接管理 + post() 编排
├── _event_defs.py     # 加载 agent 包 channel_events/<channel>/（EVENT.yaml + map.py|produce.py）+ 变更指纹
├── _event_shapes.py   # 事件载荷 → 纯数据（plainify）与形状/字段路径推导；线上路径与自查工具共用
├── _synthetic.py      # kind=synthetic 统一 runner（produce.py 的 SyntheticContext.emit）
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
    ├── _agent_events.py  # channel_events/feishu 接线：注册平台 processor、热重载 watcher、空/异常映射诊断
    ├── _card_action.py   # 交互卡片回调解析、单次/逐行消费、连点合并、上下文信封与确定性分发 (~530行)
    ├── _card_store.py    # AppData 卡片快照、整卡与 per-action tombstone、每卡回写锁 (~340行)
    └── client.py         # Bot 生命周期、通用流式回复、文件收发、评论/审批事件与按用户路由 (~950行)
```

### ChannelCore

ChannelCore 是所有 Channel（CLI、REPL、Telegram）共享的公共部件：

- async context manager，管理 aiohttp ClientSession
- `post(list[InputChunk]) -> AsyncIterator[OutputChunk]`：InputChunk → 字符串 → POST → SSE → OutputChunk
- 将输入中的 FileChunk 转换为 `[RECV:/path]` 标记（session 端负责读文件）
- 检测输出中的 `[SEND:/path]` 标记并产生 FileChunk。解码走 `iter_send_paths()`——它同时承载正则与**空路径过滤**：裸 `[SEND:]` 是模型笔误而非传输请求，放过去会让 `_send_file` 拿空 source path 发起上传。该函数定义在顶层 `psi_agent/_send_markers.py`（本模块重导出）：`session/history_display.py` 的 Gateway 投影复用同一函数，放在本层会让 Session import Channel 的私有模块
- 将 SSE 的 `delta.reasoning` 流切分为 `ReasoningChunk`（透传可选 `delta.kind`），与 `content`（`TextChunk`）按到达顺序交错产出；同槽不同 `kind` 在 buffer 内视为不同活动类型（不合并）；`[SEND:...]` 仅扫描 content
- 用共享 `RouterStatus` schema 校验独立的 `delta.router_status`，立即产出
  `RouterStatusChunk`；状态不进入 interval 缓冲或 `[SEND:]` 扫描，且会先刷新前序文本以保持事件顺序
- SSE 内容在 interval 窗口内缓冲合并为单个 TextChunk（默认 1s，可配置）
- 终端通道（CLI/REPL）设置 interval=0 无需缓冲
- 内部委托：marker 编解码 → `_markers.py`；SSE 解析与 interval 缓冲 → `_stream.py`（均与 HTTP 传输解耦、可独立单测）
- 取消安全：`__aexit__` 关闭 aiohttp `ClientSession` 用 `anyio.CancelScope(shield=True)` 保护（与 AI 层一致），cancel 时不泄露连接
- `post()` 是 async generator（返回 `AsyncGenerator[OutputChunk]`，与 `AiClient.stream` 对齐而非 `AsyncIterator`，使 `aclosing` 可类型检查）；所有 channel 客户端（cli/repl/telegram/feishu）消费时一律用 `async with aclosing(core.post(...))` 包裹（对标 `agent.py`/`channel_adapter.py`/`schedule_registry.py` 的统一约定），确保提前退出 / 被 cancel 时 `post()` 内的 `session.post()` 响应被释放
- `_stream.iter_sse_events` 与 `AiClient` 同款 JSON 守卫与日志级别：坏 JSON、非 list `choices`、非 dict `choice` 跳过并以 **WARNING** 记录（与 `ai_client.py` 一致；`[DONE]` 与 0-choice 心跳属正常流，仍记 DEBUG），缺失或 `null` 的 `delta` 归一为 `{}`，故 `post()` 中 `delta.get(...)` 永不触 None。`iter_sse_events` 返回 `AsyncGenerator` 且在 `post()` 中以 `async with aclosing(...)` 消费——aclosing 约定贯穿 client→`post`→`iter_sse_events` 全链。SSE `data:` 行解析用 `psi_agent.protocol.parse_sse_data()`；`finish_reason` 比较用 `FINISH_REASON_*` 常量
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
- **热重载（`platform_map` 免重启）**：``_watch_channel_events`` 每 5s 比一次 ``channel_events_fingerprint``（``EVENT.yaml``/``map.py``/``produce.py`` 的 size + ``st_mtime_ns`` 取 sha256），变了就重新 ``load_channel_event_defs`` 并补注册新增的 ``platform_event``。**（刻意为之）安装进 dispatcher 的 processor 绝不闭包捕获 ``ChannelEventDef``**，而是每次投递时从 ``_LiveEventDefs`` 现查——``start_background()`` 会重建 ``_processorMap`` 且已存在的 key 会被跳过，一个 ``platform_event`` 只有一次安装机会，捕获了 def 就等于把首次加载的 ``map.py`` 焊死，之后改字段路径永远不生效。故改 ``map.py`` / 新建目录数秒内自动生效（这也是让 agent 能「改完自己验」的前提），但 ``kind: synthetic`` 的 ``produce.py`` **仍需重启 Channel**——运行中的常驻生产者任务不能安全替换。指纹用 ``st_mtime_ns`` 而非内容哈希；``_exec_py_module`` 用 ``compile``+``exec`` 而非 ``importlib``（根 AGENTS.md 坑 13：热重载常见等长改写会命中 ``.pyc`` 缓存）。
- **映射诊断（``_log_empty_mapping``）**：``map_event`` 返回 ``[]`` 时打印它**实际看到的**载荷形状（``describe_shape``）与有值字段路径（``non_null_paths``），因为 Session 侧 ``matched=1 fired=[]`` 无法区分「mapper 丢弃」与「去重跳过」，字段路径写错本来完全静默。**（刻意为之）``EVENT.yaml`` 的 ``filters: true`` 只降日志级别**：声明后空结果记 DEBUG（同样细节）而非 WARNING——``identity_changed`` 订阅 ``contact.user.updated_v3`` 却按设计丢掉头像/手机号变更（占绝大多数投递），逐条 WARNING 属例行噪声，会训练读者忽略这条诊断；只在**大多数投递按设计返回 []** 时才加，仅对畸形载荷返回 ``[]`` 的 mapper 不许声明。
- **并发模型（刻意为之）**：lark SDK 在自己的后台线程/event loop 上派发消息回调；`_on_message` 通过 `anyio.from_thread.BlockingPortal.start_task_soon` 把处理协程桥接回主 anyio loop（取代 asyncio `run_coroutine_threadsafe`，遵守「一切异步用 anyio」原则）。`run_feishu` / `run_telegram` 把**启动调用**（telegram: initialize/start/start_polling；feishu: start_background）一并纳入 `try`，`finally` 用 `anyio.CancelScope(shield=True)` 保护——**启动中途失败与正常 cancel 两条路径都会执行关停**，不泄露 bot 连接。**（刻意为之）关停按步骤 best-effort：逐个 `try/except Exception` 吞掉清理异常并 WARNING**——partial-startup 下库会抛 "not running" 之类错误，吞掉以免遮蔽原始异常或中断后续 teardown；`except Exception` 不吞 `CancelledError`，勿把这层 swallow 当 bug "修掉"
- **（刻意为之）`_handle_and_stream` 外层防御 try/except**：它是 `start_task_soon` 投递的任务，内部任何未捕获异常（包括错误通知 `channel.send` 失败）都会逃逸到 portal。外层 `except Exception` 兜底并记录 ERROR，确保单条消息处理崩溃不拖垮整个 bot；不吞 `CancelledError`，勿把这层 try 当 bug "修掉"
- 所有消息（text/post/file/audio）均转化为 InputChunk：文本→TextChunk，文件→下载→FileChunk
- **批量附件按源消息分组下载（`batched_sources`）**：飞书把「同时发多份文件」实现成**多条消息**，lark SDK 的 `merge_batch` 会把它们合并成一条虚拟消息——`id` 取**最后一条**、`resources` 是**全批拼接**、各原始消息留在 `batched_sources`。而附件下载要求 `message_id` 与 `file_key` **属于同一条原始消息**，因此 `_build_chunks` 必须遍历 `batched_sources`、用每条消息自己的 `message_id` 下载，**不能**读合并后的 `ctx.resources`（它既丢了归属、也会让同一附件被下载多次）。`batched_sources` 是 `Optional` 且**单条消息时根本不设**（`merge_batch` 在 `len(batch) == 1` 时直接返回原消息），故一律 `getattr(...) or [ctx]` 兜底——漏了这层兜底会让单附件这条主路径直接 `AttributeError`。`<audio key="..."/>` 同理逐条扫：原先从**合并后**的 `content_text` 扫 key 却配最后一条的 `message_id`，多条语音会以完全相同的方式失败。喂给 agent 的**文本**仍用合并后的整段（`ctx.content_text`），与逐条下载并不冲突
- **（刻意为之）附件缺失 fail-closed，勿"修"回跳过续跑**：任一附件最终下载失败即抛 `AttachmentDownloadError`（`ChannelError` 子类）中断整批，并把**未接收的文件名**回给用户，**不**把残缺批次交给 agent。因为合并后的文本里带着**全部**文件名，而 FileChunk 只有成功的那几个——模型会照着文本里的文件名**编造出不存在的本地路径**（issue #614 实测：3 份 PDF 只下到 1 份，agent 为另外两份虚构了 `.psi-local/.../resume_files.md` 里的路径）。代价是「3 份坏 1 份」需整批重传，换掉「静默残缺 + 幻觉路径」。这与本文件「文件下载失败→跳过」的旧约定**相反**，是有意改的
- `<audio key="..."/>` inline 标签通过 `message_resource.aget()` API 下载
- 通过 `channel.stream()`  + `stream.append()` 实现卡片流式渲染
- FileChunk 通过 `channel.send()` 发送文件；用户文件下载至 `Downloads/.psi/<date>/`
- 认证：`--app-id` + `--app-secret` CLI args > `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` env
- 用户白名单：`--allowed-user-ids` 参数或 `None`（不限制）
- 处理状态表情（参考 Hermes）：收到白名单消息后立即在该消息上加 `Typing` 表情（`message_reaction.acreate`），回复完成后移除；处理失败则替换为 `CrossMark`。表情操作失败安全，不影响回复
- **群聊 @ 触发（准入策略）**：`require_mention`（默认 True）/ `respond_to_mention_all`（默认 False）经 `run_feishu` 构造 lark SDK 的 `PolicyConfig` 传入 `FeishuChannel(policy=...)`。群聊（chat_type=group/topic）仅在 @机器人时才触发 `on("message")`，未 @ 的走 `on("reject")`；单聊（p2p）默认全响应。**（刻意为之）** 该策略门由 lark SDK 内置，@机器人 判定依赖机器人 `open_id`——`FeishuChannel` 启动时自动拉取；`_ensure_bot_identity` 在 `start_background()` 后兜底重试 `resolve_bot_identity()` 一次，失败仅 WARNING（群 @ 检测不可用但不阻断启动，因单聊仍可用）。`_log_reject` 注册 `on("reject")` 把被拒消息按原因记 DEBUG，便于排查"@ 了不回复"
- **消息元数据注入（`_context_header`）**：发给 agent 的文本最前面注入一段 `<feishu_context>` 块（chat_id / chat_type / message_id / sender_open_id，可选 sender_name / thread_id）。**（刻意为之）只含客观协议事实、绝不含具体 workspace 工具名**——channel 层与 workspace 工具解耦（微内核理念：框架传协议，功能由 workspace 定义）。agent 如何用 chat_id 拉群历史 / 读文档的引导放在 workspace 的 `TOOLS.md`。header 仅在有真实内容（文本/音频/资源）时随内容一并注入；纯元数据（无任何内容）时 `_build_chunks` 丢弃 header 返回空列表，保持"unsupported message type"语义不被元数据破坏
- **交互卡片回调上下文与确定性分发**：Haitun workspace 的 `feishu_message_send_card` 在发送成功后按 `message_id` 把原始卡片、发送方 Session / open_id、接收目标、业务上下文和 `action_id -> handler` 映射写入 AppData 的 v2 snapshot；`_card_action.handle_card_action` 原子领取 snapshot 后，仍按操作者 `open_id` 解析其 agent Session，并把 `<feishu_card_action>` JSON 作为一条结构化 user 消息送入该 Session，在原飞书会话流式回复。卡片回调模块通过参数接收 `resolve_core`、进程内去重函数和 `client._stream_reply`，避免反向导入 `client.py`；`client.py` 只负责注册回调及通用流式/生命周期逻辑。信封固定包含 `schema_version`、`source`、`card`、`business_context`、`dispatch`、`action`；`dispatch` 给出 `action_id` / `handler` / `matched` / `strategy`。**（刻意为之）Channel 只做确定性选择，不直接执行 handler，也不绕过 LLM**：配置了非空 handler 映射时只允许 canonical action ID 精确命中，未知 action 返回 `matched=false, handler=null, strategy=action_handlers`，agent 不得臆造或执行未匹配 handler；只有成功读取且确认未配置映射的 v1/v2 snapshot，才为兼容旧卡片把 `action.value.action`（或 `action_id`）作为 handler，`strategy=action_id`。snapshot 缺失/读取异常时 fail closed，分别使用 `strategy=snapshot_unavailable` / `snapshot_invalid` 且 handler 为空。首个回调把 snapshot 原子改名为持久 `.consumed` tombstone 并最小化其内容；后续进程或重启后的重复回调看到 tombstone 就直接忽略，不再仅依赖进程内 `_SeenEvents`（**`multi_use` 卡例外，见下条**）。消费卡片时旧版 `action`、Card 2.0 `button` 和 `form` 都视为交互元素；Card 2.0 的 callback value 可位于 `button.behaviors`。只替换被点击控件并保留原 header/body：旧版用 `note`，Card 2.0 必须用其支持的 `markdown`，不能因按钮直接放在 `body.elements` 下就清空正文。Channel 的自定义 `appdata` 必须与 Gateway/workspace tool 解析到同一根（推荐统一设置 `PSI_APPDATA`）；不同根会安全地 fail closed，但点击者拿不到发卡业务上下文。**（刻意为之）AppData 根向 Gateway 现问、不靠环境继承**：`run_feishu` 在 `gateway_url` 非空且**没有**显式 `--appdata` 时，经 `GET /defaults` 取回 Gateway 的 `appdata`（该端点本来就返回它，无需新增接口）。因为 Gateway 只把解析后的根导出到**自己**进程的 `PSI_APPDATA`，而 channel 是**兄弟进程**不是子进程、继承不到；启动脚本只给两者之一传 `--appdata` 时，发卡方（Gateway 侧 workspace 工具）写快照的目录与收回调方（channel）读快照的目录就是两个根，每次点击都退到兜底卡并报 `dispatch.matched=false`。显式 `--appdata` 仍然优先，查询失败只记 WARNING 并保持本地解析顺序（显式 → `PSI_APPDATA` → platformdirs），**启动不依赖 Gateway**。channel 启动时无条件 INFO 打印解析出的根（`AppData root: ...`）——缺这行日志正是这个缺陷长期难以发现的直接原因。注意这条只覆盖 `gateway_url` 非空的分支；`psi-agent run` 批量模式下两者同进程，本就共享同一个 `os.environ`，但组件由 `start_soon` 并发启动、Gateway 写 `PSI_APPDATA` 与 channel 读它无先后保证，故显式统一设置 `PSI_APPDATA` 仍是最稳的做法。

- **兜底卡必须跟随原卡 schema（勿"简化"回硬编码 v1）**：读不到快照且 `fetch_message` 也救不回来时，卡片被整张替换为通用「已提交」卡。该兜底体的 schema **必须与被替换的原卡一致**——给 schema 2.0 的原卡发 v1 替换体，飞书返回 `ErrCode: 200830; ErrMsg: schemaV2 card can not change schemaV1`，结果是卡片**完全没变、按钮还能再点**，操作者得不到任何反馈（生产日志里四次有三次是这个错）。`_fallback_card_content` 因此按原卡 `schema` 分别生成 2.0（`body.elements`）或 v1（`config` + `elements`）形态；原卡未知/取不到时留在 v1，因为本仓既有卡片都是 v1。**已知局限**：snapshot 缺失时 `original_card` 只能取自 `fetch_message` 的**渲染态**卡片，其是否保留 `schema` 字段未经实测确认；若不保留，2.0 原卡仍会落到 v1 分支。真正的修复是让快照可用（即上一条的同根问题），本条只是把兜底做到不再必然失败
- **`fetch_message` 兜底对带按钮的卡片结构上不可能成功**：它返回的是**渲染后**的卡片，按钮塌缩成 `{"tag": "button", "text": "...", "type": "primary"}`——`value` 与 `behaviors` 都没了（整卡 `"value"` 出现 0 次）。而识别「点了哪个控件」只能靠这两个字段，故这条兜底路径对按钮卡永远返回 `None`。`_card_has_action_value` 就是用来区分「结构上不可能」与「本次恰好没匹配上」两种情形的，前者的 WARNING 直接点明原因并指向快照与同根检查，而不是打一条泛化的 `failed to preserve`
- **`multi_use` 卡：消费粒度从整卡降到单行（刻意为之，勿"修"回整卡一次性）**：默认卡片是**一张卡只能点一次**的——首个回调替换命中元素、删掉**其余所有**交互元素、把 snapshot 改名成 `.consumed` 墓碑。这对「同意 / 拒绝」正确，但让「一张卡 N 个勾选框」物理上不可能（勾第一条就把其余勾选框全删掉）。`save_card_snapshot(multi_use=True)` 显式 opt-in 后改为：（1）墓碑降为 per-action `{message_id}.{action_slug}.consumed`，用 `touch(mode=0o600, exist_ok=False)` 创建——这是**唯一的并发闸**，不同行各建各自墓碑互不影响，重复点同一行必撞 `FileExistsError` 恰好被拒一次；（2）snapshot **原地保留**并在每次勾选后经 `rewrite_card_snapshot` 回写——**少了这一步第二次勾选会从「还没勾过的原卡」渲染，把第一条的完成状态静默覆盖回未完成**；（3）进程内去重键从 `message_id` 降为 `{message_id}:{action_id}`。**没有规范 action id 的行退回整卡去重**（保守失败，退化成普通单次卡而不是放开重复点）；`_action_slug()` 对不合规 id 走 sha256 截断，避免奇怪 id 拼出非法文件名或撞车，路径穿越防御（`om/../etc` 仍抛 `ValueError`）原样保留。**默认路径逐字节未变**，单次卡走原来 `keep_others=False` 的分支。视觉上已勾选行渲染成 `● ~~文字~~` 且不再是交互元素，未勾选行按钮完整保留；原地刷新不需要新管线——`_card_action` 本来就在每次回调时调 `channel.update_card(message_id, ...)`，`message_id` 不变。**（刻意为之）不用飞书原生 Card 2.0 `checker` 组件**：它看着最像 todo 勾选框，但框架只把 `action` / `button` / `form` 当交互元素，`checker` 不在其中、对消费机制完全不可见，等于绕过防重放。**防重放分两层，各管一段**：per-action 墓碑文件挡「同一行被重复消费」（含飞书 at-least-once 重投与进程重启后重投），作用域**跨进程跨重启**；`card_claim_guard(message_id)` 的每卡一把 `anyio.Lock` 挡「两行同时勾时交错覆盖彼此的完成状态」，作用域**单进程内**。**（刻意为之）只锁 `rewrite_card_snapshot` 是不够的**——读快照 → 改一行 → 写回三步之间都有 `await`，同一进程内两行同时被勾可交错成「A 读 → B 读 → A 写 → B 写」，B 手里是勾选前的快照，per-action 墓碑挡不住它（两行各自成立本来就是对的，冲突在共享的那份快照上），故读必须与写在同一个临界区里。按 `message_id` 分锁而非一把全局锁（不同卡回写互不相干），最后一个等待者负责清 `_REWRITE_LOCKS` / `_REWRITE_WAITERS`，避免锁表按卡数无界增长；诊断计数 `_REJECTED_CLAIMS` 则按插入序限长（`_MAX_TRACKED_REJECTIONS`）淘汰最老的卡，因为它必须跨顺序点击存活才有意义、不能随锁一起清掉。**（刻意为之）不引入 Redis / DB / etcd 做跨进程锁**：一个飞书 app 只能有一条 WS 长连接消费者，这是**飞书平台的限制而非本项目的设计选择**（本机起两个实例会互相抢连接），所以同一张卡的并发勾选必然落在同一个进程里；真出现多进程分别收到同一张卡的回调时锁失效但墓碑仍成立，退化后果是「某一行的完成状态可能被另一进程的回写覆盖」，**不是重复执行动作**。**已知留白**：`channel.update_card` 在临界区**之外**发出，故两行几乎同时勾选时两次 HTTP 更新的到达顺序不保证，卡面可能短暂显示较旧的一版（snapshot 与墓碑仍然正确，下一次勾选或刷新即自愈）
- **连点合并成一个回合（`CardActionBatcher`，`multi_use` 卡专属）**：每次合法勾选都会走 `stream_reply`、即起一个完整 agent 回合，而 `SessionAgent._lock` 每个 session 只有一把锁，于是快点 5 条 = 5 个回合**排队** = 5 条回复；它还自我放大——排队让用户干等，干等就让人继续点。单次卡时代不可能出现（点一下整张卡就结束）。故在途回合期间到达的点击全部并进下一个回合，交给模型的是 `<feishu_card_action_batch count="N">` 包住 N 条 `<feishu_card_action>`；技能文档与工具 docstring 都写明**每条都要逐个处理、但只回一条**——合并省回复，不省动作。**（刻意为之）按 `(message_id, 点击者 open_id)` 分键，不能只按 `message_id`**：群卡里两个人各点各的必须各走自己的 session、各自回复，只按卡分键会吞掉别人的点击。回合抛异常时 `finally` 丢弃残留 pending，不重放给下一个点击者（失败回合不会把这张卡永久锁死）。**单次卡不走这条路径**（`batcher is None or not multi_use` 直接执行），行为零变化
- **卡片回调静默成功**：回调 agent 的成功路径应依靠原卡片“已选择”状态完成确认，不生成重复点击说明、处理预告或成功确认；无额外必要信息时输出零 assistant 文本。Feishu 仅在卡片回调流中防御性识别独立的 `NO_REPLY`：它支持任意 SSE 分片，并在 tool result 后重新识别；只有完整候选段 `strip()` 后严格等于 `NO_REPLY` 才吞掉，其他文本（尤其警告、部分失败、权限问题和必要后续步骤）原样流式发送。普通 Feishu 消息不启用该过滤
- **文档评论 @机器人 回复（`respond_to_comments`，默认 True）**：飞书文档评论区 @机器人 会推送 `drive.notice.comment_add_v1` 事件；`run_feishu` 在开关开启时注册 `channel.on("comment", ...)`，回调经 `portal.start_task_soon(_handle_comment, ...)` 调度（与 `_handle_and_stream` 同款异步隔离，异常绝不冒泡）。触发门槛与群聊一致——**仅当评论明确 @了机器人（`CommentEvent.mentioned_bot`）才回复**，其余记 DEBUG 跳过，白名单同样按 `operator.open_id` 生效。流程：`resolve_comment_target`（doc/docx/sheet/file/wiki，不支持则 WARNING 跳过）→ `get_comment_context`（拿 `question` 问题文本 + `quote` 锚定原文）→ 喂 `core.post()` 后 **`_collect_reply` 累积成整段文本**（评论 API 是一次性写入，不支持 IM 卡片式增量流式；`FileChunk` 评论区无处安放，记 DEBUG 忽略）→ **回复前强制 `ctx.is_whole = True` 再 `channel.reply_comment(ctx, text)`**。agent 失败时把错误文本回复到评论。**（刻意为之，数据安全）** SDK `reply_comment` 对 `is_whole=False`（锚定文字的评论）走 `PUT .../replies/:reply_id`——那是**更新覆盖**某条 reply，且 `target_reply_id` 恰是用户 @机器人 的那条 reply，会把用户原话抹掉（数据丢失）；SDK 未提供"在已有评论下无损追加 reply"的接口，故一律强制走 `is_whole=True` 分支（`POST .../comments` 新建整条评论），代价是回复另起一条评论而非挂在原线程下，换零数据丢失——**勿当 bug "修复"回 `reply_comment` 默认路径**。评论 header（`_comment_context_header`）同样只含协议事实（file_token / file_type / comment_id / operator_open_id / quote）、不含工具名。**依赖飞书后台订阅 `drive.notice.comment_add_v1` 事件并开启文档评论权限**，否则收不到事件（代码兜底记日志）
- **审批状态变化主动推送（事件驱动，非轮询）**：员工提交的飞书审批在**状态变化**（通过/驳回/撤回等）时，连接的 app 主动把结果推送给**申请人本人**。分两半实现——workspace 侧 `feishu_approval_subscribe(approval_code)` 工具调 `POST /approval/v4/approvals/:approval_code/subscribe`（tenant token，幂等，每个审批定义订阅一次即可）开订阅；channel 侧负责收事件并推送（事件是经长连接**推**来的，workspace 工具 pull 接不到，故只能在 channel 层收）。**（刻意为之）SDK 无 typed processor**：lark-channel-sdk 1.2.0 未给 `approval_instance` 事件提供归一化 processor，故 `_register_approval_processor` 走 SDK 内部的 `CustomizedEventProcessor` 注入 `dispatcher._processorMap` 的 `p1.approval_instance` / `p2.approval_instance` 两个 key（与 SDK 自身处理 drive 评论同款逃生口）；**必须在 `start_background()` 之后注册**——`start_background` 会重建 dispatcher，提前注册会被覆盖。任何 SDK 内部结构缺失/改名都降级为 WARNING、绝不拖垮启动。**（刻意为之）事件载荷只有 `approval_code`/`instance_code`/`status`，无推送目标**：故 `_handle_approval_event` 先 `_fetch_instance_detail`（`GET /approval/v4/instances/:instance_id`，tenant token）解析出**申请人 open_id** 再 DM 推送；channel 不能 import workspace 工具（微内核解耦），故自行手搭 `BaseRequest`。回调经 `portal.start_task_soon` 桥回主 anyio loop（SDK 回调在后台线程），外层 try/except 兜底异常绝不冒泡。**去重**：飞书事件可能重投，`_SeenEvents`（有界 FIFO，maxlen=512）按 `instance_code+status+operate_time` 去重。白名单按**申请人 open_id** 走 `_allowed`；解析不出申请人则记 DEBUG 跳过。事件 header（`_approval_event_header`）**（刻意为之）只含协议事实**（approval_code / instance_code / status），不含工具名。**依赖飞书后台订阅审批事件并给机器人 `approval:approval` 权限**，否则收不到事件（代码兜底记日志）。取消订阅用 `feishu_approval_unsubscribe(approval_code)`
- **按会话独立渠道（`gateway_url`，默认 None）**：设置后同一飞书机器人对不同飞书**会话**提供**各自独立**的 Session。`run_feishu` 用 `AsyncExitStack` 持有所有 per-chat `ChannelCore` + 一个 REST `aiohttp.ClientSession`；`resolve_core(open_id, *, chat_id="", chat_type="")` 回调**在白名单通过后才解析**（被拦用户不建连接，防非白名单 open_id 刷出大量 `ClientSession`），经 `_GatewayRouteProvider.ensure` → Gateway `POST /feishu/route` 幂等拿回该会话 session 的 `channel_socket`，再经 `_CoreRegistry.get` 缓存复用对应 `ChannelCore`。`_handle_and_stream` / `_handle_comment` / `_handle_approval_event` 的 `core` 参数因此是 `resolve_core` 回调（分别按消息的 `sender_id`+chat 事实 / `operator.open_id` / 申请人 open_id 路由），类型是 `ResolveCore` Protocol 而非 `Callable[...]`——因为回调带 keyword-only 参数，`Callable` 表达不了。**路由/spawn 决策权全在 Gateway 侧的 `FeishuManager`**（含路由键、所挂 AI 与 workspace 子目录），channel **只如实上报 `open_id`/`chat_id`/`chat_type` 三个协议事实**、自己不判断该按哪个键路由，也不 spawn、退出也不删——对比早期把路由塞进 channel 内部直接调 `/sessions` 的做法。**（刻意为之）本地缓存键必须与 Gateway 的路由键同款判定**（`_GatewayRouteProvider.ensure` 直接调 `psi_agent._feishu_routing.route_key()`：`chat_type` 为 group/topic 且 `chat_id` 非空 → `chat:<chat_id>`，否则 `open_id`）：同一个群里不同人发言必须命中同一条缓存，否则每个发言者各打一次 Gateway、各建一个 `ChannelCore` 连到同一个 socket。此前这里复制了一份 Gateway 的判定逻辑、需要人工同步两侧，现已收敛到 `_feishu_routing`（`is_group_chat()` / `route_key()`），改那一处即两侧同时生效。评论 / 审批推送 / 卡片回调等无 IM 会话的场景不传 chat 事实，自然落到 `open_id` 分支（`_card_action` 按操作者 `open_id` 解析，群卡片的点击因此仍落到点击者私聊 session——已知留白）。**（刻意为之，取消安全）** `AsyncExitStack` 与 `BlockingPortal` 的进出顺序：portal 后进先出、先于 stack 关闭，保证在飞的 handler 仍能用到活着的 core / http，与旧版「core 在 `stop_background` 之后才关」等价。**并发安全**：`_CoreRegistry` 与 `_GatewayRouteProvider` 均用「快路径 dict 读 + 慢路径 `anyio.Lock` double-checked」，消除同一键并发消息各建一个 core / 各发一次路由的竞态。**降级**：Gateway 不可达或路由失败时 `resolve_core` 回退共享 `session_socket`（用户总能得到回复，只是不隔离），且路由失败**不写缓存**，下条消息重试。`gateway_url=None`（默认）时行为与今天完全一致（全体共用 `session_socket`）。
- **群聊整群共用一个 Session（刻意为之，勿"修"成按发言者拆）**：群消息按 `chat_id` 路由，全群一份上下文/workspace/历史——A 问完 B 追问「那第二点呢」时机器人看得见 A 那轮，这正是群聊该有的连贯性。区分「谁在说话」靠 `_context_header` 每条消息注入的 `sender_open_id`（协议事实，已有机制，无需新增），不靠拆 session。派生规则与隐私级的 session-id 撞名坑见 `gateway/AGENTS.md`「FeishuManager」。**已知留白**：群 workspace 只有一份而 UAT 按发送者 `open_id` 存，「以谁的身份写文档」由 workspace 工具按每条消息的 `sender_open_id` 决定，channel / Gateway 都不做约定。
