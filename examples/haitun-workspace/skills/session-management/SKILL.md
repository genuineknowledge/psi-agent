---
name: session-management
description: Discover, inspect, and search workspace sessions (list, status, history, keyword/task search). LOAD when the user references other chats, past conversations, or subagent sessions.
category: agent
---

# Session management

## 原则

- **Session ≠ Gateway**：真相源是 `histories/{session_id}.jsonl` + background registry；Gateway 仅可选增强（标题、在线态）。
- **当前对话**靠 Session 内置 history，无需 search tool。
- **不要用 `memory_search` 代替 session 检索** — Memory 是提炼事实，不是原始对话。

---

## 何时 LOAD

- 用户提到**其他会话 / 之前的 tab / 另一个 chat**
- 用户问「有哪些对话」「后台 agent 怎么样了」
- 子 agent 跟进前需要定位 `sub-*` session id
- 用户问「我们之前有没有提到过 X」

委派 spawn 仍用 **`skills/subagent-orchestration/SKILL.md`**；本 skill 管**发现、监视、检索**。

---

## Tool 选型

| 用户意图 | Tool | 典型参数 |
|----------|------|----------|
| 列清单（无条件） | `sessions_list` | （空）或 `running_only=true` |
| **关键词** — 哪次提到过 X | `session_keyword_search` | `query="X"` |
| **单 session 内**找 X | `session_keyword_search` | `query="X"`, `session_id=…` |
| **任务类型** — GitHub / subagent / 最近 | `session_task_search` | `category="github"` 等 |
| 读某个 session 内容 | `sessions_history` | `session_id=…` |
| 看某个 session 状态 | `session_status` | `session_id=…` |

### `session_task_search` 的 category

| category | 含义 |
|----------|------|
| `subagent` | `sub-*` 或 background 关联 |
| `github` | 标题/近期 user 消息像 GitHub 工作 |
| `gateway` | Gateway 在线 session |
| `background` | 有 alive 后台进程 |
| `untitled` | 有消息但无标题 |
| `recent` | 7 天内活跃 |
| `all` | 全部 browse |

---

## 配方

### A. 跨会话回忆（关键词）

```
session_keyword_search(query=…)
  → 取 hits[0].session_id
  → sessions_history(session_id=…, limit=30)
  → 摘要回答（不要贴全文除非用户要）
```

### B. 按类型列会话

```
session_task_search(category=…)
  → 给用户 id + title + running 的短列表
  → 需要细节再 sessions_history
```

### C. 监视在跑的 subagent

```
session_task_search(category="subagent")   # 或 category="background"
  → session_status(session_id=…)
  → sessions_history(session_id=…, limit=20)
```

### D. 发现全景

```
sessions_list()
  → 需要时再 status / history / search
```

---

## 不要

- 不要替用户切换 Gateway UI tab
- 不要未经确认删除 session
- 不要逐步播报内部 tool 调用（静默执行，最后给摘要）

---

## 相关 skill

- **Subagent 委派**：`skills/subagent-orchestration/SKILL.md`
- **Fusion Memory（跨会话事实，非 transcript）**：`skills/fusion-memory-setup/SKILL.md`
