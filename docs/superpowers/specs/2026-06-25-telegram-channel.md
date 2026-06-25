# Telegram Channel 设计规格

**日期**: 2026-06-25
**状态**: 待审批

---

## 1. 概述

Channel 层新增 Telegram 通道，将 Telegram Bot 接入 psi-agent Session，通过 ChannelCore 流式收发消息。Bot 收到用户消息后调用 `core.post()`，使用 Telegram message edit 功能实现流式输出效果。

---

## 2. Chunk 分发协议（Telegram 视角）

Telegram 通道对所有 Chunk 类型的处理：

| Chunk 类型 | Telegram 操作 |
|-----------|--------------|
| `TextChunk(text)` | 累积到 `accumulated` 字符串，`edit_text(accumulated)` 更新已发送消息 |
| `FileChunk(path)` | 先尝试 `send_photo`，失败则 `send_document` |

---

## 3. 消息流

```
+----------+     +-------------+     +---------+     +-----------+
| Telegram | --> | ChannelCore | --> | Session | --> | AI Server |
|    Bot   |     |  .post()    |     |  socket |     |  socket   |
+----------+     +-------------+     +---------+     +-----------+
     |                |                    |
     v                v                    v
  handler()      Chunk queue           SSE stream
     |                |
     v                v
 send_message    AsyncIterator[Chunk]
 (placeholder)         |
     |                v
     v          noop/0choice → skip
  edit_text     FileChunk  → send_document / send_photo
     ^          TextChunk  → accumulated += text → edit_text
     |                         |
     |                  [DONE] → done
     |
     +--- next user message ---+
```

---

## 4. ChannelTelegram

文件 `psi_agent/channel/telegram/__init__.py`：

```python
@dataclass
class ChannelTelegram:
    session_socket: str
    bot_token: str = ""
    interval: float = 1.0
    allowed_user_ids: list[int] | None = None
    proxy: str = ""
    verbose: bool = False

    async def run(self) -> None: ...
```

### 4.1 参数解析

- `bot_token`: CLI 参数优先，空字符串时从 `PSI_TELEGRAM_BOT_TOKEN` 环境变量读取，仍为空则报错退出
- `allowed_user_ids`: `None` 不限制，`[]` 拒绝所有（无意义但兼容），有值则仅匹配列表中的 user_id
- `interval`: 透传给 ChannelCore，控制 SSE 缓冲窗口

### 4.2 启动流程

```
1. setup_logging(verbose)
2. 解析 bot_token（CLI > env，报错退出）
3. 创建 ptb Application.builder().token(...).build()
4. 注册 handler：filters.ALL 匹配所有消息（文本、图片、文件，含 slash command）
5. Application.initialize() + start() + updater.start_polling() + anyio.Event().wait()
```

---

## 5. 消息 Handler 逻辑

文件 `psi_agent/channel/telegram/client.py`：

```python
async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # 白名单检查
    if not _allowed(update.effective_user.id, ...):
        return

    # 构建 Chunk 列表：text/caption → TextChunk，photo/document → download → FileChunk
    chunks = await _build_chunks(update)

    sent = await update.message.reply_text("...")

    # 流式累积
    accumulated = ""
    async for chunk in core.post(chunks):
        if isinstance(chunk, TextChunk):
            accumulated += chunk.text
            if accumulated.strip():
                await sent.edit_text(accumulated)
        elif isinstance(chunk, FileChunk):
            await _send_file(update.message, chunk.path)
```

### 5.1 文件发送

```python
async def _send_file(message: Message, path: str) -> None:
    try:
        await message.reply_photo(path)
    except Exception:
        await message.reply_document(path)
```

---

## 6. CLI 结构

```python
# cli.py
ChannelTelegram 加入 ChannelGroup union:

ChannelGroup = Annotated[
    ChannelRepl | ChannelCli | ChannelTelegram,
    conf.subcommand(name="channel", ...),
]
```

运行：
```bash
psi-agent channel telegram \
    --session-socket ./channel.sock \
    --bot-token 123:abc
```

---

## 7. 错误处理

| 场景 | 处理 |
|------|------|
| `core.post()` 异常 | `edit_text` 错误消息，log exception |
| Telegram 网络错误 | ptb 框架内置自动重连 |
| FileChunk path 不存在 | 跳过，`logger.warning` |
| edit_text 消息已被删除 | catch `BadRequest`，静默跳过 |
| 无 `bot_token` | raise `ValueError("Missing bot token...")` — 启动级错误 |

---

## 8. 依赖

`pyproject.toml` 新增 optional dependency：

```toml
[project.optional-dependencies]
telegram = ["python-telegram-bot[socks]>=22.0"]
```

---

## 9. 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `psi_agent/channel/telegram/__init__.py` | `ChannelTelegram` dataclass + `run()` |
| 新建 | `psi_agent/channel/telegram/client.py` | handler、文件发送、主循环 |
| 修改 | `psi_agent/cli.py` | 加入 `ChannelTelegram` 到 `ChannelGroup` |
| 修改 | `pyproject.toml` | 新增 `telegram` optional dependency |

---

## 10. 非目标/范围外

- 多用户并发消息队列：依赖 Session 端 `anyio.Lock` FIFO 排队
- Bot 命令（/start、/help、/stop 等）——未来扩展（当前所有消息包括命令均传给 agent）
- Inline keyboard、callback query 等高级交互——未来扩展
- Webhook 模式——当前仅 long polling
- 用户 session 隔离——全局单 Session
