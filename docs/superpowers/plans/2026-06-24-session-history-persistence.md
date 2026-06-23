# Session History 持久化实施计划

> **Goal:** 实现 SessionAgent 历史消息 JSONL 持久化

**Design Spec:** `docs/superpowers/specs/2026-06-24-session-history-persistence.md`

---

### Task 1: 添加 `session_id` 到 Session dataclass

**Files:** `src/psi_agent/session/__init__.py`

- [x] 在 `Session` dataclass 中新增 `session_id: str | None = None` 字段
- [x] 将 `session_id` 传入 `SessionAgent.create()`

---

### Task 2: 实现加载和保存函数

**Files:** `src/psi_agent/session/agent.py`

- [x] 新增 `_init_history(workspace_path, session_id) -> tuple[list[dict], Path]`：
  - UUID 生成、目录创建、.gitignore、`_load_history` 调用
- [x] 新增 `_load_history(history_path: Path) -> list[dict]` 模块级函数
  - 文件不存在 → 返回 `[]`, INFO 日志
  - 文件存在 → 逐行 `json.loads`, 非法行 WARNING + skip
- [x] 新增 `_save_history(history_path: Path, history: list[dict]) -> None` 模块级函数：
  - 覆盖写入 jsonl, DEBUG 日志
  - 异常 catch + ERROR 日志

---

### Task 3: 在 `create()` 中初始化 history

**Files:** `src/psi_agent/session/agent.py`

- [x] `SessionAgent.create()` 调用 `_init_history(workspace_path, session_id)` → 返回 `(history, history_path)`

---

### Task 4: 修改 `__init__` 接收 history

**Files:** `src/psi_agent/session/agent.py`

- [x] 新增参数：
  ```python
  history: list[dict] | None = None,
  history_path: Path | None = None,
  ```
- [x] `self.history = history if history is not None else []`
- [x] `self._history_path = history_path` — 由 `create()` 传入，直接构造时默认为 `None`
- [x] 参数顺序保持一致

---

### Task 5: `run()` 中添加保存

**Files:** `src/psi_agent/session/agent.py`

- [x] 在 `finish_reason="stop"` 分支，`history.append(...)` 后调用 `_save_history()`

---

### Task 6: 测试

**Files:** `tests/psi_agent/session/test_agent.py`（新测试）

- [x] `test_new_session_creates_uuid_and_history_file` — `session_id=None` → UUID 生成，jsonl 存在
- [x] `test_resume_session_loads_history` — 已有 jsonl → history 正确加载
- [x] `test_history_saved_after_stop` — stop 后写盘
- [x] `test_history_not_saved_on_error` — error 后文件不变
- [x] `test_corrupt_jsonl_line_skipped` — 非法行跳过
- [x] `test_histories_dir_and_gitignore_created` — 自动创建目录 + .gitignore

**Integration test:** `tests/integration/test_session_workspace.py` 或新文件

- [x] `test_full_session_persists_history` — full session → 多轮对话 → 验证 jsonl

---

### Task 7: 更新 AGENTS/spec/plan

- [x] `src/psi_agent/session/AGENTS.md` — 新增 History 持久化章节
- [x] `AGENTS.md` 根 — 更新代码结构注释
- [x] `docs/superpowers/specs/...` — 更新 Session dataclass（如需要）
