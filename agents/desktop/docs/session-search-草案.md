# Session 检索设计草案（两 Tool + Skill）

> 状态：**待评审**  
> 原则：**两个薄 tool + 一个 Skill 写配方**；Session ⊥ Gateway；数据以 `histories/*.jsonl` 为主。

---

## 1. 为什么拆两个 Tool

| 方式 | 问题 |
|------|------|
| 单 tool + `mode` 参数 | Agent 要记住 mode/category 组合 |
| **两 tool + Skill** | 每个 tool 参数简单；Skill 教「什么时候用哪个」 |

与现有模式一致：

- `subagent_plan` / `subagent_chat` + `subagent-orchestration/SKILL.md`
- `session_keyword_search` / `session_task_search` + `session-management/SKILL.md`

---

## 2. 两个 Tool（Agent 直接调用）

### 2.1 `session_keyword_search` — 关键词搜索（L1）

**何时用（Skill 会写）：** 用户提到**具体词句**，要在过去对话里找原话。

```python
async def session_keyword_search(
    query: str,
    session_id: str = "",
    workspace: str = "",
    limit: int = 10,
) -> str:
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `query` | ✅ | 搜索词，子串匹配（不区分大小写） |
| `session_id` | | 空 = 跨所有 session；非空 = 只在这一条里搜 |
| `workspace` | | 空 = 当前 workspace |
| `limit` | | 最多返回几个 session 命中，默认 10 |

**返回**（JSON 字符串）：

```json
{
  "ok": true,
  "query": "PR #296",
  "session_id_scope": "",
  "count": 1,
  "hits": [
    {
      "session_id": "77f17a48…",
      "title": "GitHub PR 提交备注",
      "score": 0.08,
      "message_count": 181,
      "snippets": [
        {"role": "user", "text": "…PR #296…"}
      ]
    }
  ]
}
```

0 命中：`ok: true`, `count: 0`, `hits: []`。

---

### 2.2 `session_task_search` — 任务分类搜索（L2）

**何时用（Skill 会写）：** 用户要**某一类会话**，不一定有关键词。

```python
async def session_task_search(
    category: str,
    workspace: str = "",
    limit: int = 10,
    include_gateway: bool = True,
) -> str:
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `category` | ✅ | 见下表 |
| `workspace` | | 空 = 当前 workspace |
| `limit` | | 默认 10 |
| `include_gateway` | | 合并 Gateway title / 在线态 |

**v1 合法 `category`：**

| category | 含义 |
|----------|------|
| `subagent` | 子 agent 会话（`sub-*` 或 background 关联） |
| `github` | 标题/内容像 GitHub 工作 |
| `gateway` | 当前 Gateway 在线的 session |
| `background` | 有 alive 后台进程 |
| `untitled` | 有消息但无标题 |
| `recent` | 7 天内活跃 |
| `all` | 全部（browse） |

未知 category → `ok: false` + 列出合法值。

**返回**：

```json
{
  "ok": true,
  "category": "github",
  "count": 2,
  "hits": [
    {
      "session_id": "77f17a48…",
      "title": "GitHub PR 提交备注",
      "categories": ["github", "gateway"],
      "running": true,
      "message_count": 181,
      "history_mtime": "2026-07-09T…"
    }
  ]
}
```

---

## 3. Skill：`skills/session-management/SKILL.md`

Skill **不写执行逻辑**，只写：

- 什么时候 LOAD
- 用哪个 tool
- 和标准链路（list → search → history → status）

### 3.1 何时 LOAD

- 用户提到**其他会话 / 之前的对话 / 另一个 tab**
- 用户问**有哪些对话 / 后台 agent 怎么样了**
- 委派 subagent 前后要发现或监视

### 3.2 检索：选哪个 Tool

| 用户说法 | 用 |
|----------|-----|
| 「之前有没有提到过 Docker」 | `session_keyword_search(query="Docker")` |
| 「PR 那次说了啥」 | 先 `session_keyword_search(query="PR")` → 再 `sessions_history` |
| 「有哪些 GitHub 相关的 chat」 | `session_task_search(category="github")` |
| 「哪些 subagent 还在跑」 | `session_task_search(category="subagent")` 或 `category=background` |
| 「最近聊过哪些」 | `session_task_search(category="recent")` |

**不要**用 `memory_search` 代替上述（Memory 是提炼事实，不是原始对话）。

### 3.3 标准配方

**A. 跨会话回忆（关键词）**

```
session_keyword_search(query=…)
  → 取 hits[0].session_id
  → sessions_history(session_id=…, limit=30)
  → 用摘要回答用户
```

**B. 按类型列会话**

```
session_task_search(category=…)
  → 给用户简短列表（id + title + running）
  → 若要详情再 sessions_history
```

**C. 发现 + 监视（已有 tool）**

```
sessions_list()                    # 无条件的全景
session_status(session_id=…)       # 单个状态
sessions_history(session_id=…)     # 单个内容
```

**D. 子 agent（引用 subagent-orchestration）**

委派用 `subagent-orchestration`；跟进前可用 `session_task_search(category="subagent")` 找 id。

### 3.4 不能做什么

- 不能替用户点 Gateway UI 切换 tab
- 不能未经确认删除 session
- Gateway 不可达时：keyword/task 仍可用（靠 jsonl）；title 可能为空

---

## 4. 与缺失表 / 现有 Tool 映射

| 缺失表 | 实现 |
|--------|------|
| `session_search` (Hermes P0) | **`session_keyword_search`**（主）+ **`session_task_search`**（扩展） |
| `sessions_list` | ✅ 已有 |
| `sessions_history` | ✅ 已有 |
| `session_status` | ✅ 已有 |

Hermes 的 scroll（单 session 内定位）→ `session_keyword_search(query=…, session_id=…)`。

---

## 5. 实现落点（评审通过后）

| 文件 | 内容 |
|------|------|
| `tools/_session_helpers.py` | `keyword_search()`, `task_search()` |
| `tools/session_keyword_search.py` | 薄封装 |
| `tools/session_task_search.py` | 薄封装 |
| `skills/session-management/SKILL.md` | 上文配方 |
| `systems/prompt_sections.py` | 注册两 tool |
| `systems/prompt_constants.py` | `SESSION_SEARCH_GUIDANCE` 指向 Skill |
| `tests/test_session_search.py` | 两 tool 单测 |

---

## 6. Agent 要填的参数（极简）

| Tool | Agent 通常只填 |
|------|----------------|
| `session_keyword_search` | **`query` 一个词** |
| `session_task_search` | **`category` 一个词** |

其余全有默认值；**选哪个 tool 由 Skill 教**，不是让 Agent 猜 mode。

---

## 7. 开放问题

1. Tool 命名：`session_keyword_search` / `session_task_search` 是否 OK？  
   （或更短：`keyword_search_sessions` — 建议保持 `session_` 前缀成组）

2. Skill 名：`session-management` vs `session-recall`？

3. v1 是否两个 tool 一起上，还是先 keyword 再 task？  
   → 建议 **一起上**（task 规则简单，代码共用 `_session_helpers`）。

---

**评审通过后**：先实现两 tool + Skill，再扩展 smoke 测试。
