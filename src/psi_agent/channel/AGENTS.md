# Channel 层设计文档

## Channel 层架构

```
channel/
├── _types.py          # FileChunk, TextChunk, ReasoningChunk, InputChunk, OutputChunk
├── _errors.py         # ChannelError 基类（传输/协议/session 错误统一抛出）
├── _markers.py        # [RECV:]/[SEND:] 标记协议（encode_input + SendMarkerScanner，纯函数）
├── _stream.py         # SSE 解析 iter_sse_events + interval 缓冲 StreamBuffer（与传输解耦）
├── _core.py           # ChannelCore — 连接管理 + post() 编排
├── cli/
│   ├── __init__.py     # ChannelCli dataclass
│   └── client.py       # 单次消息 thin client (~18行)
├── repl/
│   ├── __init__.py     # ChannelRepl dataclass
│   └── client.py       # 交互式 thin client (~41行)
└── telegram/
    ├── __init__.py     # ChannelTelegram dataclass
    └── client.py       # Bot handler + 流式 + 文件收发 (~117行)
└── feishu/
    ├── __init__.py     # ChannelFeishu dataclass
    └── client.py       # Bot handler + 卡片流式 + 文件收发 (~100行)
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

- 通过 lark-channel-sdk 的 `FeishuChannel.connect()` 进行 WebSocket 长连接
- 所有消息（text/post/file/audio）均转化为 InputChunk：文本→TextChunk，文件→下载→FileChunk
- `<audio key="..."/>` inline 标签通过 `message_resource.aget()` API 下载
- 通过 `channel.stream()`  + `stream.append()` 实现卡片流式渲染
- FileChunk 通过 `channel.send()` 发送文件；用户文件下载至 `Downloads/.psi/<date>/`
- 认证：`--app-id` + `--app-secret` CLI args > `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET` env
- 用户白名单：`--allowed-user-ids` 参数或 `None`（不限制）
- 处理状态表情（参考 Hermes）：收到白名单消息后立即在该消息上加 `Typing` 表情（`message_reaction.acreate`），回复完成后移除；处理失败则替换为 `CrossMark`。表情操作失败安全，不影响回复