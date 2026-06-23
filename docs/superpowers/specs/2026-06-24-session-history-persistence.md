# Session History 持久化设计规格

**日期**: 2026-06-24
**状态**: 已审批

---

## 1. 概述

Session 当前只在内存中维护 `self.history: list[dict]`，进程退出即丢失。本设计为 SessionAgent 添加可选的 JSONL 文件持久化能力。

**设计原则**：
- 后向兼容——不传 `session_id` 的行为与当前完全一致（UUID 自动生成 + 持久化）
- 可 resume——传入已有的 `session_id` 则加载该文件的历史消息
- 惰性写入——仅在 `finish_reason="stop"` 且内容成功追加后才写盘
- 零依赖——JSONL 格式纯 Python `json` 库可读写

---

## 2. 存储格式

**路径**: `workspace/histories/{session_id}.jsonl`

- 每行一个消息 dict，与 `self.history` 中的消息格式一致
- 文件编码: UTF-8
- 末尾无空行
- 示例（两轮对话）:

```jsonl
{"role": "system", "content": "You are a helpful assistant."}
{"role": "user", "content": "你好"}
{"role": "assistant", "content": "你好！有什么可以帮助你的？"}
{"role": "user", "content": "天气如何？"}
{"role": "assistant", "content": "抱歉，我无法获取实时天气信息。"}
```

---

## 3. Session 参数

`Session` dataclass 新增字段:

```python
session_id: str | None = None
```

- `None`（默认）→ `SessionAgent.create()` 中生成 UUID4 作为 id
- 给定字符串 → 直接用作文件名 `{id}.jsonl`

---

## 4. 加载行为

在 `SessionAgent.create()` 中，通过 `_init_history()` 初始化历史：

1. **创建目录**: 如果 `workspace/histories/` 不存在，`mkdir(parents=True)`
2. **创建 .gitignore**: 在 `histories/` 下写入 `.gitignore`（内容 `*`），仅在目录新创建时写入
3. **确定 session_id**: 如果 `session_id is None`，生成 `uuid.uuid4().hex`
4. **加载历史**: 
   - 文件不存在 → `history = []`，INFO 日志
   - 文件存在 → 逐行 `json.loads(line)`，非法行 `continue` + WARNING 日志，成功行追加到 `history`
5. 将 `history` 和 `history_path` 传递给 `SessionAgent.__init__`
6. INFO 日志: `"History loaded from {path} ({len} messages)"`

---

## 5. 保存行为

在 `SessionAgent.run()` 中，当 `finish_reason="stop"` 且 content 成功追加到 history 后：

1. 如果 `self._history_path` 非空：
   - 打开文件（覆盖写模式）
   - 逐行 `json.dumps(msg, ensure_ascii=False)` + `\n`
   - DEBUG 日志: `"History saved to {path} ({len} messages)"`
2. 如果写盘失败（磁盘满、权限等）→ ERROR 日志 + catch，不影响对话继续

**不保存的场景**:
- `finish_reason="error"` — 错误回复不写入 history，原始 jsonl 不变
- `finish_reason="tool_calls"` — 仅中间状态，等待最终 stop
- 异常/未预期的 finish_reason — history 中可能已有部分 message，但安全起见不写盘

---

## 6. SessionAgent 变更

### `__init__` 新参数

```python
def __init__(
    self,
    *,
    ...
    history: list[dict] | None = None,
    history_path: Path | None = None,
) -> None:
```

- `history` — 从 jsonl 加载的历史消息列表。若为 `None`，初始化为 `[]`
- `history_path` — jsonl 文件的完整路径，从 `create()` 传入；直接构造时默认为 `None`

### `run()` 变更

在 `finish_reason="stop"` 分支，`self.history.append(...)` 之后，调用 `_save_history()`。

### `_save_history(path, history)` (module-level, async)

```python
async def _save_history(path: Path, history: list[dict]) -> None:
    try:
        content = "\n".join(json.dumps(msg, ensure_ascii=False) for msg in history) + "\n"
        await anyio.Path(str(path)).write_text(content)
        logger.debug(f"History saved to {path} ({len(history)} messages)")
    except Exception as e:
        logger.error(f"Failed to save history: {e}")
```

---

## 7. 日志约定

| 场景 | 级别 | 示例消息 |
|------|------|----------|
| 目录创建 | INFO | `"Created histories directory: .../histories/"` |
| .gitignore 创建 | DEBUG | `"Created .gitignore in .../histories/"` |
| 新 session（无历史文件） | INFO | `"Starting new session: {id}"` |
| 历史文件不存在 | INFO | `"No history file found at ..."` |
| 历史加载成功 | INFO | `"History loaded from ... ({n} messages)"` |
| JSONL 解析失败（单行） | WARNING | `"Skipping malformed line {n} in ..."` |
| 历史写入成功 | DEBUG | `"History saved to ... ({n} messages)"` |
| 历史写入失败 | ERROR | `"Failed to save history: {e}"` |

---

## 8. 测试策略

### 单元测试

1. `test_new_session_creates_history` — `session_id=None` → UUID 生成，文件创建
2. `test_resume_session_loads_history` — `session_id="existing"` → 加载已有 jsonl
3. `test_history_saved_after_stop` — `finish_reason="stop"` → jsonl 写入
4. `test_history_not_saved_on_error` — `finish_reason="error"` → 文件不变
5. `test_corrupt_jsonl_line_skipped` — 非法行 → warning + 跳过
6. `test_histories_dir_auto_created` — 目录不存在 → 自动创建
7. `test_gitignore_created` — 首次创建目录时写入 `.gitignore`

### 集成测试

8. `test_full_session_persists_history` — 真实 session → 多轮对话 → 历史文件存在且正确
9. `test_session_resume` — 已有 jsonl → 启动 session → history 包含之前的消息

---

## 9. 版本历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-24 | v0.4.0-draft | Session history JSONL persistence design |
