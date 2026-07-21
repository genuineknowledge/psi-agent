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
- ~~飞书 Docs comment 事件——仅处理群聊和私聊消息~~ **（已于 2026-07-21 实现，见第 14 节）**

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

---

## 12. 群聊 @ 触发与准入策略

**目标**：飞书机器人在群里被 @ 时才用 Haitun Agent 读取消息并回复；单聊照常回复。

### 12.1 底层机制（`lark_channel` 内置）

- `lark_channel` 的 `PolicyGate` 在消息分发前判定准入，只有通过的消息才触发 `on("message")`（进而调用 Haitun Agent），被拒的走 `on("reject")`。
- 群聊（`chat_type` 为 `group` / `topic`）：`require_mention=True`（默认）时，仅当消息 @机器人（`mentioned_bot`）才通过，否则以 `policy_no_mention` 拒绝。
- 单聊（`p2p`）：默认 `dm_policy="open"`，全部响应，不受 `require_mention` 影响。
- `mentioned_bot` 的判定依赖机器人 `open_id` —— 由 `FeishuChannel` 启动时调 `/bot/v3/info` 自动拉取。

### 12.2 psi-agent 侧配置（`ChannelFeishu` / `run_feishu`）

| 字段 | 默认 | 说明 |
|------|------|------|
| `require_mention` | `True` | 群聊仅在 @机器人时回复；单聊不受影响。设 `False` 则群里每条消息都回复 |
| `respond_to_mention_all` | `False` | 是否把 `@所有人` 视为有效 @（默认否，避免 @all 触发机器人） |

`run_feishu` 据此构造 `lark_channel` 的 `PolicyConfig` 并经 `FeishuChannel(policy=...)` 传入（此前完全未传，只吃库默认值）。

### 12.3 bot 身份兜底与可诊断性（`run_feishu`）

**根因防护**：若启动时 `/bot/v3/info` 拉取失败（网络抖动 / 飞书后台未开启"机器人"能力），`bot_open_id` 为 `None` → 群里每条消息 `mentioned_bot=False` → 全被 `require_mention` 拒掉，表现为"群里 @ 了也不回复"。

- `_ensure_bot_identity(channel)`：`start_background()` 后调用；`channel.bot_identity` 为 `None` 时 `await channel.resolve_bot_identity()` 兜底重试一次。成功记 `INFO`（含 open_id / name）；仍失败记 `WARNING`，明确提示"群聊 @机器人 检测将不可用，请确认飞书后台已开启机器人能力"。自身异常绝不冒泡（不拖垮启动）。
- `_log_reject(event)`：注册为 `channel.on("reject", ...)`，把被策略拒绝的消息按原因（`policy_no_mention` 等）记 `DEBUG`，便于日后"@ 了不回复"排查。失败安全。

### 12.4 文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `psi_agent/channel/feishu/__init__.py` | `ChannelFeishu` 加 `require_mention` / `respond_to_mention_all` 字段并透传 |
| 修改 | `psi_agent/channel/feishu/client.py` | `run_feishu` 构造 `PolicyConfig` + `_ensure_bot_identity` + `_log_reject` |
| 修改 | `tests/psi_agent/channel/feishu/test_feishu.py` | policy 透传 / bot 身份兜底 / reject 回调测试 |
| 修改 | `src/psi_agent/channel/AGENTS.md` | Feishu 约定补「群聊 @ 触发（准入策略）」一条（含刻意为之留痕） |

### 12.5 非目标

- 不暴露更细的 `group_policy` / `dm_policy` / 名单（allowlist / blocklist）等准入维度，仅保留与"@ 触发"直接相关的两个开关。

---

## 13. 群聊上下文与文档读取（消息元数据注入）

**目标**：机器人被 @ 时，能读取群聊上下文（历史消息）以及其中提到的文档 / 文件。

### 13.1 缺口与设计

此前 `_build_chunks` 只把消息正文（`content_text`）发给 agent，agent 不知道自己在哪个群（`chat_id`），即便 workspace 装了 `feishu_message_list` 也无从调用。

`_context_header(ctx)` 在发给 agent 的文本最前面注入一段 `<feishu_context>` 块：

```
<feishu_context>
chat_id: oc_xxx
chat_type: group          # p2p / group / topic
message_id: om_xxx
sender_open_id: ou_xxx
sender_name: 张三          # 可选
thread_id: omt_xxx        # 可选（话题/回复串）
</feishu_context>
```

agent 拿到 `chat_id` 后自行决定是否调 `feishu_message_list(container_id=chat_id)` 拉群历史、对消息中的飞书文档链接调 `feishu_doc_read`、对附件调 `feishu_file_download`。

### 13.2 关键设计约束（刻意为之）

- **channel 与 workspace 工具解耦**：header 只含客观协议事实（chat_id 等），**绝不含具体 workspace 工具名**（遵守微内核理念：框架传协议，功能由 workspace 定义）。"如何用 chat_id 拉上下文"的引导放在 workspace 的 `TOOLS.md`（进入系统提示，agent 可见）。
- **不破坏 unsupported-type 语义**：header 恒会构造，但仅当存在真实内容（文本 / 音频 / 资源）时才随内容注入；纯元数据（无任何内容）时 `_build_chunks` 丢弃 header 返回 `[]`，使调用方仍回"Unsupported message type"。
- **按需读取**：文档 / 附件不预拉，由 agent 判断需要时才读，省 token、避免拉入无关内容（消息自带的附件仍按原有逻辑自动下载为 FileChunk）。

### 13.3 文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `psi_agent/channel/feishu/client.py` | 新增 `_context_header`；`_build_chunks` 注入 header 并保持 unsupported-type 语义 |
| 修改 | `tests/psi_agent/channel/feishu/test_feishu.py` | 元数据头注入 / 群聊 chat_id 携带 / 空消息丢弃 header |
| 修改 | `examples/haitun-workspace/TOOLS.md` | 常驻引导：群聊里如何用 `feishu_message_list` / `feishu_doc_read` / `feishu_file_download` |
| 修改 | `src/psi_agent/channel/AGENTS.md` | Feishu 约定补「消息元数据注入（`_context_header`）」一条（含刻意为之留痕） |

### 13.4 非目标

- channel 层不自动预拉群历史或附件（避免耦合与 token 浪费）；是否拉取由 agent 决策。
- 不解析飞书"分享云文档"卡片为可读 token——最稳的是消息里直接带文档 URL 文本。

---

## 14. 文档评论 @机器人 自动回复（2026-07-21 增强）

**目标**：飞书文档（doc/docx/sheet/file/wiki）评论区 @机器人 时，连接的 app 用 Haitun Agent 的回答回复该评论。此前评论事件被列为非目标（第 10 节），本节实现之。

### 14.1 底层机制（`lark_channel` 内置）

- 评论区 @机器人 会推送 `drive.notice.comment_add_v1` 事件，SDK 归一化为 `CommentEvent`（`file_token` / `file_type` / `comment_id` / `reply_id` / `operator` / `mentioned_bot`），经 `channel.on("comment", ...)` 分发。
- SDK 提供评论读写原语：`resolve_comment_target(file_token, file_type)` → `CommentTarget`（支持 doc/docx/sheet/file，wiki 经节点解析为底层 obj_token）；`get_comment_context(target, comment_id, event_reply_id)` → `CommentContext`（含 `question` 问题文本 + `quote` 锚定原文 + `is_whole` + `target_reply_id`）；`reply_comment(context, content)`（全文评论新建评论，锚定评论对 `target_reply_id` 新增回复）。
- psi-agent 侧此前只订阅 `message` / `reject`，从未订阅 `comment`——这是全部缺口。

### 14.2 psi-agent 侧配置（`ChannelFeishu` / `run_feishu`）

| 字段 | 默认 | 说明 |
|------|------|------|
| `respond_to_comments` | `True` | 文档评论区 @机器人 时回复该评论。设 `False` 则完全不订阅评论事件 |

CLI 经 tyro 自动暴露为 `--respond-to-comments` / `--no-respond-to-comments`。

### 14.3 Handler 逻辑（`_handle_comment`）

注册为 `channel.on("comment", ...)`，经 `portal.start_task_soon(_handle_comment, ...)` 调度（与 `_handle_and_stream` 同款异步隔离，异常绝不冒泡）。流程：

1. **触发门槛**：仅当 `event.mentioned_bot` 为真才回复（与群聊 `require_mention` 语义一致，避免文档里每条评论都触发），否则记 DEBUG 跳过。
2. **白名单**：按 `event.operator.open_id` 走 `_allowed`（与消息白名单同一函数；`open_id` 可能为 `None`，故 `_allowed` 首参放宽为 `str | None`）。
3. `resolve_comment_target` → 目标不支持（`supported=False`）记 WARNING 返回。
4. `get_comment_context`。
5. 组 chunks（`_comment_context_header` + `question`；`question` 为空记 WARNING 仍继续）喂 `core.post()`，**`_collect_reply` 累积成整段文本**——评论 API 是一次性写入，不支持 IM 卡片式增量流式；`FileChunk` 评论区无处安放，记 DEBUG 忽略。
6. **回复前强制 `ctx.is_whole = True`**，再 `channel.reply_comment(ctx, reply_text)`（详见 14.7 数据安全）。agent 调用失败时把错误文本回复到评论；空回复兜底为 `(no response)`。

### 14.4 消息元数据注入（`_comment_context_header`）

与 `_context_header`（第 13 节）同理，在发给 agent 的问题文本最前注入 `<feishu_comment_context>` 块：`file_token` / `file_type` / `comment_id` / `operator_open_id`，可选 `quote`（锚定原文）。**（刻意为之）只含客观协议事实、不含具体 workspace 工具名**——保持 channel 与 workspace 工具解耦；agent 如何用 `file_token` 读文档全文的引导放 workspace 的 `TOOLS.md`。

### 14.5 文件变更

| 操作 | 文件 | 说明 |
|------|------|------|
| 修改 | `psi_agent/channel/feishu/__init__.py` | `ChannelFeishu` 加 `respond_to_comments` 字段并透传 |
| 修改 | `psi_agent/channel/feishu/client.py` | `run_feishu` 注册 `on("comment")` + `_handle_comment` + `_collect_reply` + `_comment_context_header`；`_allowed` 首参放宽为 `str \| None` |
| 修改 | `tests/psi_agent/channel/feishu/test_feishu.py` | 评论 header / @门槛 / 白名单 / 不支持目标 / agent 失败 / 回复异常吞掉 / comment 订阅开关 |
| 修改 | `src/psi_agent/channel/AGENTS.md` | Feishu 约定补「文档评论 @机器人 回复」一条（含刻意为之留痕） |

### 14.6 前置依赖与非目标

- **前置依赖**：飞书开发者后台须订阅 `drive.notice.comment_add_v1` 事件、并给机器人开启文档评论权限，否则收不到事件（代码兜底记日志，不阻断启动）。
- 不做评论的流式增量回复（评论 API 一次性写入，累积成整段）。
- 评论区不回发文件（`FileChunk` 忽略）。
- 不主动读取文档全文——由 agent 拿 `file_token` 自行决策（省 token）。

### 14.7 数据安全：为何一律新建评论（刻意为之）

SDK `reply_comment(context, content)` 按 `context.is_whole` 分两条路：

| `is_whole` | SDK 行为 | 语义 |
|-----------|---------|------|
| `True` | `POST /drive/v1/files/:file_token/comments` | 新建一条整条评论（安全） |
| `False` | `PUT /drive/v1/files/:file_token/comments/:comment_id/replies/:reply_id` | **更新覆盖**某条 reply |

`False` 分支里 `reply_id = context.target_reply_id`，而 `get_comment_context` 在传入 `event_reply_id` 时返回的正是**用户 @机器人 的那条 reply**。飞书官方文档确认该 PUT 是"更新云文档中某条回复的内容"（覆盖，非追加）——若照默认路径,机器人的回答会**抹掉用户 @机器人 的原始评论**（数据丢失）。

SDK 未提供"在已有评论下无损追加一条 reply"的接口（只有 create 整条评论 与 update 覆盖 reply 两个 builder）。故 `_handle_comment` 在调用前**强制 `ctx.is_whole = True`**，锁定安全的 POST-create 路径。代价：机器人的回复另起一条评论，不挂在用户那条评论线程下；换取零数据丢失。这是有意取舍，勿回退。
