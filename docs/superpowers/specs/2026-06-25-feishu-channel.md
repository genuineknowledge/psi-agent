# Feishu Channel 设计规格

**日期**: 2026-06-25
**状态**: 已实现

---

## 1. 概述

Channel 层新增 Feishu（飞书）通道，通过 `lark-channel-sdk` 将飞书机器人接入 psi-agent Session，使用 ChannelCore + SDK 内置卡片流式渲染实现实时交互。

---

## 2. Chunk 分发协议（Feishu 视角）

| Chunk 类型 | Feishu 操作 |
|-----------|------------|
| `TextChunk(text)` | `channel.stream()` — SDK 内置卡片流式渲染，每个 delta 实时推送 |
| `FileChunk(path)` | `channel.send({file: {source: {path}}})` 发送，失败 fallback `{image: {source: {path}}}` |

---

## 3. 消息输入

| 飞书消息类型 | Chunk 映射 |
|-------------|-----------|
| `ctx.content_text` 中的 `<audio key="..."/>` inline 标签 | parse key → `channel.client.im.v1.message_resource.aget()` → `FileChunk(path)` |
| `ctx.resources` 中每个 resource（image、file、video、sticker） | `channel.download_resource_to_file()` → `FileChunk(path)` |

---

## 4. ChannelFeishu

文件 `psi_agent/channel/feishu/__init__.py`：

```python
@dataclass
class ChannelFeishu:
    session_socket: str
    app_id: str = ""
    app_secret: str = ""
    interval: float = 1.0
    allowed_user_ids: list[str] | None = None
    verbose: bool = False

    async def run(self) -> None: ...
```

### 4.1 参数解析

- `app_id`: CLI 参数优先，空字符串时从 `PSI_FEISHU_APP_ID` 环境变量读取
- `app_secret`: CLI 参数优先，空字符串时从 `PSI_FEISHU_APP_SECRET` 环境变量读取
- 任一为空则 `raise ValueError`
- `allowed_user_ids`: `None` 不限制；有值则仅匹配列表中的 `open_id` 或 `user_id`
- `interval`: 透传给 ChannelCore

### 4.2 启动流程

```
1. setup_logging(verbose)
2. 解析 app_id / app_secret（CLI > env，缺失则 raise ValueError）
3. 创建 FeishuChannel(app_id, app_secret)
4. channel.on("message", handler)
5. channel.start_background()（后台线程运行 WebSocket） + anyio.Event().wait()（阻塞，接受外部 cancel）
```

---

## 5. 消息 Handler 逻辑

文件 `psi_agent/channel/feishu/client.py`：

```python
async def _handle_message(ctx: MessageContext) -> None:
    # 白名单检查
    if not _allowed(ctx.sender_id, allowed_ids):
        return

    # 构建 Chunk 列表
    chunks: list[Chunk] = []
    if ctx.content_text:
        chunks.append(TextChunk(ctx.content_text))
    for r in ctx.resources:
        saved = await channel.download_resource_to_file(
            r.file_key, resource_type=r.resource_type, dest_dir=downloads
        )
        chunks.append(FileChunk(str(saved)))

    if not chunks:
        return
    # channel.stream() 内置卡片流式渲染
    async def _produce(stream):
        async for chunk in core.post(chunks):
            if isinstance(chunk, TextChunk):
                await stream.append(chunk.text)

    await channel.stream(ctx.chat_id, {"markdown": _produce}, {"reply_to": ctx.message_id})
```

### 5.1 文件下载

`InboundMessage.resources` 包含 `ResourceDescriptor` 列表（`type`、`file_key`、`file_name`）。调用 `channel.download_resource_to_file(file_key, resource_type=r.type, dest_dir=...)` 下载。

---

## 6. CLI 结构

```python
# cli.py
ChannelGroup = Annotated[
    ChannelRepl | ChannelCli | ChannelTelegram | ChannelFeishu,
    conf.subcommand(name="channel", ...),
]
```

运行：
```bash
psi-agent channel feishu \
    --session-socket ./channel.sock \
    --app-id cli_xxx \
    --app-secret ****
```

---

## 7. 错误处理

| 场景 | 处理 |
|------|------|
| `core.post()` 异常 | `channel.send(chat_id, {"text": f"Error: {e}"})`，log exception |
| 文件下载失败 | 跳过，`logger.error` |
| SDK 连接断开 | `FeishuChannel` 内置自动重连 |
| 无 `app_id` 或 `app_secret` | `raise ValueError` |

---

## 8. 依赖

`pyproject.toml`：

```toml
dependencies = [
    ...
    "lark-channel-sdk>=1.0",
]
```

---

## 9. 文件变更清单

| 操作 | 文件 | 说明 |
|------|------|------|
| 新建 | `psi_agent/channel/feishu/__init__.py` | `ChannelFeishu` dataclass + `run()` |
| 新建 | `psi_agent/channel/feishu/client.py` | handler、文件下载、主循环 |
| 修改 | `psi_agent/cli.py` | 加入 `ChannelFeishu` 到 `ChannelGroup` |
| 修改 | `psi_agent/_run.py` | 加入 feishu 到 YAML 分发 |
| 修改 | `pyproject.toml` | 新增 `lark-channel-sdk` runtime dependency |

---

## 10. 非目标/范围外

- 卡片回调（button click、form submit）——未来扩展
- 多租户 Session 隔离——全局单 Session
- Webhook 模式——当前仅 WebSocket 长连接
- 飞书 Docs comment 事件——仅处理群聊和私聊消息

---

## 11. 处理状态表情回复（2026-06-28 增强，参考 Hermes）

收到**通过白名单**的消息后，立即在该消息上添加 `Typing` 表情作为"处理中"反馈；回复流结束后：**成功**移除 `Typing`，**失败**则替换为 `CrossMark`（红 ❌）。`Typing` / `CrossMark` 为 Hermes 实际使用、且飞书官方确认有效的 `emoji_type`。

飞书 reaction API 仅有 create / delete / list，**无原子"替换"**，故"替换为 `CrossMark`" = 先 `delete` `Typing` 再 `create` `CrossMark`。

### 11.1 常量与辅助函数（`feishu/client.py`）

- 常量：`_EMOJI_PROCESSING = "Typing"`、`_EMOJI_FAILED = "CrossMark"`
- `_add_reaction(channel, message_id, emoji_type) -> str | None`：调用 `channel.client.im.v1.message_reaction.acreate`，成功返回 `resp.data.reaction_id`；`data` 为空或异常时返回 `None`（失败安全）
- `_remove_reaction(channel, message_id, reaction_id) -> None`：调用 `message_reaction.adelete`；异常仅 `logger.error`，不抛

### 11.2 生命周期（`_handle_and_stream`）

```
白名单通过 → reaction_id = _add_reaction(Typing)   # 立即
failed = False
try:
    build chunks（异常 → send error, failed=True, return）
    if not chunks: return                          # 非失败，仅移除 Typing
    channel.stream(...)（异常 → send error, failed=True）
finally:
    if reaction_id: _remove_reaction(Typing)       # 移除 Typing
    if failed:      _add_reaction(CrossMark)        # 失败标记 CrossMark
```

- `finally` 保证每条退出路径（无内容 / build 失败 / stream 失败 / 成功）都先移除 `Typing`。
- `if not chunks`（unsupported / 无内容）**不算失败**，不加 `CrossMark`。
- 失败时"先移除 Typing 再加 CrossMark"实现 Hermes 的"替换"视觉。
- 表情操作全部**失败安全**，绝不影响主回复流程。

### 11.3 文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `psi_agent/channel/feishu/client.py` | 常量 + `_add_reaction`/`_remove_reaction` + `_handle_and_stream` 生命周期 |
| 修改 | `tests/psi_agent/channel/feishu/test_feishu.py` | reaction 辅助函数 + 成功/失败生命周期测试 |
| 修改 | `src/psi_agent/channel/AGENTS.md` | 处理状态表情说明 |

### 11.4 非目标

- 表情不做配置化（明确硬编码 `Typing` / `CrossMark`）。
- 不实现"内容感知"的多态表情（Hermes issue #14667 方向），仅处理中 / 失败两态。
