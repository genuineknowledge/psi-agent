# Channel 层设计文档

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
- 错误：session socket 不存在时打印友好错误，exit code 1
- 不发送 history，每次只带一条 user message

## Model Routing

- Channel 可以按请求内容做模型路由
- 通过 `models` 直接传入多个模型名，顺序约定为从简单/快速到复杂/强力
- 路由器会先调用 `route/chat_with_ustc.py` 里的 USTC OpenAI-compatible API，再用 prompt 结合任务地域、语言、合规和平台背景判断应该选哪个模型
- 路由器 URL 和路由模型写死在 `route/chat_with_ustc.py` 里
- 如果只配置一个模型，则所有请求都使用它
- 如果路由 API 失败或返回无效结果，则回退到 `models` 的第一个模型
- Channel 发送的 `model` 会透传给 Session，再由 AI 层决定最终上游模型
