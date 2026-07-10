---
name: session-management
description: Discover, inspect, search, export, create, and hand off workspace sessions. LOAD when the user references other chats, exports a transcript, transfers work to another session, or asks about session list/status.
category: agent
---

# Session management

## 原则

- **Session ≠ Gateway**：真相源是 `histories/{session_id}.jsonl` + background registry；Gateway 仅可选增强（标题、在线态、创建 runtime）。
- **当前对话**靠 Session 内置 history，无需 search tool。
- **不要用 `memory_search` 代替 session 检索** — Memory 是提炼事实，不是原始对话。
- **静默执行**：内部 list / search / export / handoff 步骤不要逐步播报；结束时给摘要。

---

## 何时 LOAD

| 场景 | 走哪条配方 |
|------|------------|
| 其他会话 / 之前的 tab / 另一个 chat | A / B |
| 「有哪些对话」「后台 agent 怎么样了」 | D / C |
| 「我们之前有没有提到过 X」 | A |
| **导出**对话 / 保存 md / 备份 transcript | E |
| **工作交接** — 交给另一个 session 继续 | F / G |
| **开新 tab / 新建 session** 并交接 | G |
| 子 agent 跟进前定位 `sub-*` id | B / C |

委派 spawn 仍用 **`skills/subagent-orchestration/SKILL.md`**；本 skill 管**发现、监视、检索、导出、创建、交接**。

---

## Tool 选型

| 用户意图 | Tool | 典型参数 |
|----------|------|----------|
| 列清单（无条件） | `sessions_list` | （空）或 `running_only=true` |
| **关键词** — 哪次提到过 X | `session_keyword_search` | `query="X"` |
| **单 session 内**找 X | `session_keyword_search` | `query="X"`, `session_id=…` |
| **任务类型** — GitHub / subagent / 最近 | `session_task_search` | `category="github"` 等 |
| 读某个 session 内容 | `sessions_history` | `session_id=…`, `limit=30` |
| 看某个 session 状态 | `session_status` | `session_id=…` |
| **导出** transcript 到文件 | `sessions_export` | `output_path=…`, 可选 `session_id` / `export_format` |
| **新建 session**（Gateway） | `sessions_create` | `workspace`, 可选 `ai_id` / `session_id` |
| **工作交接** | `sessions_handoff` | `target_session_id`, `task`, 见下文 |

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

### `sessions_export` 格式

| format | 用途 |
|--------|------|
| `markdown`（默认） | 仅 user/assistant 对话，`### User` / `### Assistant` |
| `json` / `jsonl` / `text` | 结构化或纯文本；可选 `include_tool_messages=true` |

`output_path` 必填（路径+文件名）；相对路径落在 workspace 下。`session_id` 空 = 当前 session。

---

## Handoff 交接逻辑（核心）

`sessions_handoff` 把 **来源 session 的上下文 + 明确任务** 发到 **目标 session 的 channel**。Agent 按下面决策树执行，**不要**手工整理 md 再粘贴。

### 1. 解析来源 session（`source_session_id`）

按优先级，**命中即停**：

1. **`source_session_id` 非空** → 直接用
2. **`query` 非空** → `session_keyword_search(query=…, limit=1)` 取 top hit 的 `session_id`
3. **`category` 非空** → `session_task_search(category=…, limit=1)` 取 top hit
4. **以上皆空** → 当前 session（进程内 `session_id`）

失败时：向用户说明「找不到来源」或「需要显式 source_session_id」，**不要**空 handoff。

### 2. 解析目标 session（`target_session_id`）

- **必填**。目标 session **必须在跑**（Gateway tab 或 background runtime，channel socket 可连）。
- 用户没指定目标、但要「开新 tab 继续」→ 先走 **配方 G**（`sessions_create` → 用返回的 `session_id` 作 target）。
- handoff 前可 `session_status(session_id=target)` 确认 `running`；未跑则提示用户打开 tab 或 `sessions_create`。

### 3. 构建上下文（`context` / `query` / `history_limit`）

| 条件 | 行为 |
|------|------|
| `context` 非空 | **手动覆盖**，不读 history |
| `query` 非空 | 读来源 history（最多 `history_limit` 条，默认 20）；附加 **Matching excerpts**；对话段只保留含关键词的 user/assistant 轮 |
| 皆空 | 读来源最近 user/assistant 对话（不含 tool/system） |

上下文上限约 12k 字符；超出截断。最终包进固定模板：

```markdown
## Session handoff
**From session:** `{source}` (可选 title)
**Task:** {task}
### Context
…
---
Continue this work in the current session. Do not ask the user to re-paste this handoff.
```

### 4. 投递与 `wait`

| `wait` | 行为 |
|--------|------|
| `false`（默认） | 发到 target channel 即可；不等回复 |
| `true` | 等 target 首段回复（最长 `timeout_seconds`，默认 600s） |

### 5. Handoff 成功后 **当前 session 的行为**

- **停止**在来源 session 里继续执行已交接的任务
- 一句话告诉用户：任务已交给 `{target_session_id}`，请在 Gateway **打开对应 tab**
- 若 `wait=true` 且收到 `reply_text`，可摘要 target 的确认回复
- **不要**来源与 target 相同（tool 会拒绝）

### 6. 与 subagent 的边界

| | handoff | subagent |
|---|---------|----------|
| 目标 | 已有/新建 **Gateway session** 续作 | 新建 `sub-xxx` **后台隔离**任务 |
| 配方 | 本 skill F / G | `subagent-orchestration` |
| 典型话术 | 「交给另一个 tab 继续」 | 「后台跑一个子任务」 |

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

### E. 导出 transcript

用户要保存、备份、或「导出成 md」：

```
# 当前 session，默认 markdown 纯对话
sessions_export(output_path="exports/chat-20260710.md")

# 指定 session + 格式
session_keyword_search(query="PR #305")   # 可选：先定位 id
  → sessions_export(
       session_id=…,
       output_path="exports/pr305.jsonl",
       export_format="jsonl",
     )
```

导出成功后告知 **绝对/相对路径** 与 `message_count`；markdown 不含 metadata / tool / system。

### F. 工作交接 → 已有 target

用户指定 **B 继续 A 的工作**（B 已在跑）：

```
# 1. 确认 target 在线
session_status(session_id="B")
  → 若未 running：请用户打开 tab，或改走配方 G

# 2. 交接（来源 = 当前 session）
sessions_handoff(
  target_session_id="B",
  task="继续 PR #305 的 CI 修复",
  query="PR #305",              # 可选：收窄 context
)

# 来源 = 别的 session（显式 id）
sessions_handoff(
  target_session_id="B",
  source_session_id="A",
  task="…",
)

# 来源靠搜索定位（source 留空）
session_keyword_search(query="PR #305")
  → sessions_handoff(target_session_id="B", task="…", query="PR #305")

session_task_search(category="github")
  → sessions_handoff(target_session_id="B", task="…", category="github")

# 用户已写好摘要，跳过 history 抽取
sessions_handoff(
  target_session_id="B",
  task="…",
  context="（用户或你整理的要点）",
)
```

**Handoff 成功后**：当前 session **停止**继续该任务；提示用户去 Gateway 打开 **B**。

### G. 新建 session + 交接（Gateway）

**不要**做叠加 tool；分两步编排：

```
# 1. 创建（需 Gateway 在线）
sessions_create(workspace="…")          # workspace 空 = 当前
  → ok 且 session_id、channel_socket 就绪

# 2. 交接
sessions_handoff(
  target_session_id=<新建 session_id>,
  task="…",
  query="…",                            # 或 source_session_id="A"
)

# 可选：等 target 确认
sessions_handoff(..., wait=true)
```

告诉用户去 Gateway 侧栏打开新 tab（若 UI 未自动切换）。创建失败（Gateway 不可达 / 无 ai_id）时 **不要**假装已交接。

---

## 不要

- 不要替用户切换 Gateway UI tab
- 不要未经确认删除 session
- 不要逐步播报内部 tool 调用
- handoff 后不要在**来源 session**继续执行已移交任务
- 不要用 `sessions_export` + 手工粘贴代替 `sessions_handoff`（除非用户明确要求只要文件、不要自动投递）

---

## 相关 skill

- **Subagent 委派**：`skills/subagent-orchestration/SKILL.md`
- **Fusion Memory（跨会话事实，非 transcript）**：`skills/fusion-memory-setup/SKILL.md`
