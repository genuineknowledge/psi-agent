# Channel 层设计文档

## Channel 层架构

```
channel/
├── _types.py          # FileChunk, TextChunk, Chunk
├── _core.py           # ChannelCore — 连接管理 + SSE 管道
├── cli/
│   ├── __init__.py     # ChannelCli dataclass
│   └── client.py       # 单次消息 thin client (~18行)
└── repl/
    ├── __init__.py     # ChannelRepl dataclass
    └── client.py       # 交互式 thin client (~41行)
```

### ChannelCore

`ChannelCore` 是 CLI 和 REPL 共享的公共部件：

- async context manager，管理 aiohttp ClientSession
- `post(list[Chunk]) -> AsyncIterator[Chunk]`：Chunk → 字符串 → POST → SSE → Chunk
- 将输入中的 `FileChunk` 转换为 `[RECV:/path]` 标记（session 端负责读文件）
- 检测输出中的 `[SEND:/path]` 标记并产生 `FileChunk`
- SSE 内容在 interval 窗口内缓冲合并为单个 `TextChunk`（默认 1s，可配置）

Channel 客户端（CLI/REPL）不再直接处理 HTTP、SSE 解析或错误格式。

## 概述

Channel 层是 psi-agent 的用户界面层，负责连接 Session socket 并通过 SSE 流式显示 AI 回复。

提供两种交互模式：
- **CLI**（单次消息） — 发送一条消息，显示回复，退出
- **REPL**（交互式） — 持续对话

## 终端输出约定

- Channel 客户端（repl、cli）是终端 UI 程序，需要格式化输出
- **使用 `rich.console.Console`** 替代 `print()`
- 思考过程（reasoning_content）：`console.print(..., style="dim")`
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
