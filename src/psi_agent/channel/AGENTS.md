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
└── telegram/
    ├── __init__.py     # ChannelTelegram dataclass
    └── client.py       # Bot handler + 流式 + 文件收发 (~186行)
└── feishu/
    ├── __init__.py     # ChannelFeishu dataclass
    └── client.py       # Bot handler + 卡片流式 + 文件收发 + 处理状态表情 (~219行)
```

### ChannelCore

ChannelCore 是所有 Channel（CLI、REPL、Telegram）共享的公共部件：

- async context manager，管理 aiohttp ClientSession
- `post(list[InputChunk]) -> AsyncIterator[OutputChunk]`：InputChunk → 字符串 → POST → SSE → OutputChunk
- 将输入中的 FileChunk 转换为 `[RECV:/path]` 标记（session 端负责读文件）
- 检测输出中的 `[SEND:/path]` 标记并产生 FileChunk
- 将 SSE 的 `delta.reasoning` 流切分为 `ReasoningChunk`，与 `content`（`TextChunk`）按到达顺序交错产出（类型切换时先 flush 旧类型）；`[SEND:...]` 仅扫描 content
- SSE 内容在 interval 窗口内缓冲合并为单个 TextChunk（默认 1s，可配置）
- 终端通道（CLI/REPL）设置 interval=0 无需缓冲
- 内部委托：marker 编解码 → `_markers.py`；SSE 解析与 interval 缓冲 → `_stream.py`（均与 HTTP 传输解耦、可独立单测）
- 取消安全：`__aexit__` 关闭 aiohttp `ClientSession` 用 `anyio.CancelScope(shield=True)` 保护（与 AI 层一致），cancel 时不泄露连接
- `post()` 是 async generator（返回 `AsyncGenerator[OutputChunk]`，与 `AiClient.stream` 对齐而非 `AsyncIterator`，使 `aclosing` 可类型检查）；所有 channel 客户端（cli/repl/telegram/feishu）消费时一律用 `async with aclosing(core.post(...))` 包裹（对标 `agent.py`/`channel_adapter.py`/`schedule_registry.py` 的统一约定），确保提前退出 / 被 cancel 时 `post()` 内的 `session.post()` 响应被释放
- `_stream.iter_sse_events` 与 `AiClient` 同款 JSON 守卫与日志级别：坏 JSON、非 list `choices`、非 dict `choice` 跳过并以 **WARNING** 记录（与 `ai_client.py` 一致；`[DONE]` 与 0-choice 心跳属正常流，仍记 DEBUG），缺失或 `null` 的 `delta` 归一为 `{}`，故 `post()` 中 `delta.get(...)` 永不触 None。`iter_sse_events` 返回 `AsyncGenerator` 且在 `post()` 中以 `async with aclosing(...)` 消费——aclosing 约定贯穿 client→`post`→`iter_sse_events` 全链
- **（刻意为之）`_session`/`_endpoint` 不在 dataclass 中声明**：二者在 `__aenter__` 赋值、在 `post()` 中无条件使用；若声明为字段则需 `X | None`，会在 `post()` 引入 Optional narrowing（被迫 assert 或 `# ty: ignore`，违反零抑制）。由 async context manager 保证"先 `__aenter__` 再 `post()`"的时机，故保留为动态属性——勿当 bug "修复"

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