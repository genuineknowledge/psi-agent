# 设计：让 Haitun agent 能向用户发送文件

- 日期：2026-07-02
- 分支：`fix/input-files-directly`
- 范围约束：**只修改 `examples/haitun-workspace/`**，不改框架代码。

## 背景与问题

用户在 Telegram 聊天里让 Haitun agent「发一份 Word 文档过来」，agent 回答「我没有消息/邮件发送能力，做不到直接发文件」，只能在本地生成文件让用户自己去取。

但经代码调查，**psi-agent 框架早已内置向用户发送文件的能力**，只是 haitun-workspace 的 agent 从未被告知，所以它「以为」自己做不到。

## 已有机制（框架层，不改动）

- **发送协议**：agent 在回复正文里输出 `[SEND:/绝对路径]` 标记，Channel 端的
  `SendMarkerScanner`（`src/psi_agent/channel/_markers.py`）会流式扫描出该标记，
  生成一个 `FileChunk`。
- **Telegram 投递**：`channel/telegram/client.py` 的 `_send_file` 收到 `FileChunk` 后
  调用 `reply_photo(path)`，失败回退 `reply_document(path)`，把文件真正发到用户的
  Telegram 聊天窗口。
- **Feishu 投递**：`channel/feishu/client.py` 的流处理（`_produce` 内）收到 `FileChunk`
  时调用 `_send_file` → `channel.send(chat_id, {"image": {"source": path}})`，失败回退
  `{"file": {"source": path}}`，把文件真正发到用户的飞书会话。与 Telegram 完全对等。
- **REPL**：`channel/repl/client.py` 忽略 `FileChunk`，所以本地终端看不到发送效果
  （本设计不针对 REPL，用户主渠道是 Telegram 与飞书）。
- **已知副作用**：core 只探测不剥离标记，`[SEND:...]` 文本会原样残留在发给用户的
  文字消息里（见 `tests/psi_agent/channel/test__core.py:181`）。这是框架行为，本次不改。

## 目标

只在 workspace 指令里「教会」agent 使用这个已有能力，使它在被要求发文件时：

1. 知道自己**可以**把文件发到用户聊天窗口（Telegram 与飞书均支持）；
2. 先用已有工具（`write` / `bash` / `powershell`）在本地生成文件；
3. 用绝对路径输出 `[SEND:/abs/path]` 完成发送；
4. 把标记放在消息末尾单独一行，减少残留标记对正文的干扰。

**不做**（YAGNI）：不加 `send_file.py` 工具、不加 Word 生成工具、不改任何框架文件。
邮箱不做——它需要额外凭证与框架级 channel 集成，超出「只改 workspace」的约束；用户的
两个主渠道 Telegram 与飞书都已被本方案覆盖。

## 方案

在 `examples/haitun-workspace/systems/prompt_sections.py` 新增一个 section 常量
`SEND_FILES_SECTION`，并在 `systems/system.py` 的 `build_system_prompt()` 稳定前缀
部分注入它（紧跟 `TOOL_CALL_STYLE_SECTION` 之后，属于「怎么与用户交互」这一类）。

### `SEND_FILES_SECTION` 内容要点（Markdown 段落，风格对齐现有 section）

```
## Sending Files to the User
You can deliver files straight to the user's chat window. To send a file, put a
marker on its own line: [SEND:<absolute-path>]. The channel detects it and
uploads the file to the user (images show inline; other types arrive as a
document).

- Generate or locate the file first (write / bash / powershell), then reference
  it by ABSOLUTE path.
- One marker per file; put each on its own line at the end of your reply.
- Only send files that exist and that the user asked for or would expect.
- The marker text itself may remain visible in the chat — keep your prose above
  it self-contained; don't rely on the marker reading like a sentence.
- If the user asks for a document (e.g. a Word .docx), create it locally with
  your tools, then send it with the marker. If a needed library is missing,
  install it or fall back to a format you can produce.
```

（英文与其它 section 一致；agent 会按 USER.md 的语言偏好用中文与用户交流。）

### 注入点（`system.py`，`build_system_prompt`）

现有稳定前缀顺序：
```
identity
[help]
build_tooling_section(tools)
TOOL_CALL_STYLE_SECTION
EXECUTION_BIAS_SECTION
SAFETY_SECTION
FUSION_MEMORY_SECTION
...
```
改为在 `TOOL_CALL_STYLE_SECTION` 之后插入 `SEND_FILES_SECTION`：
```
TOOL_CALL_STYLE_SECTION
SEND_FILES_SECTION      ← 新增
EXECUTION_BIAS_SECTION
```
`import` 列表同步加入 `SEND_FILES_SECTION`。

## 组件边界

- `prompt_sections.py`：纯常量，无逻辑。新增一个字符串常量，可被 smoke test 直接读到。
- `system.py`：仅在 import 与 `stable_parts` 列表里各加一处引用。改动面极小、可逆。

## 错误处理

无运行时逻辑改动，因此无新增异常路径。风险点仅在于：
- 注入位置错误 → 由 smoke test（`python systems/system.py` 打印完整 prompt）验证 section
  出现在稳定前缀里、位置正确。

## 测试

1. **Smoke test**：运行 `uv run python examples/haitun-workspace/systems/system.py`，
   断言输出中包含 `## Sending Files to the User` 且位于 cache boundary 之前。
2. **可选人工验证**（依赖真实 Telegram bot，非自动化）：在 Telegram 里让 agent 发一份
   文件，确认聊天窗口收到附件。此步不纳入自动化验证。

## 交付

- 修改：`examples/haitun-workspace/systems/prompt_sections.py`（+1 常量）
- 修改：`examples/haitun-workspace/systems/system.py`（+1 import，+1 注入）
- 不新增文件，不改框架。
