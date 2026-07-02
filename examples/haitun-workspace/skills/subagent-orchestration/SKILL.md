---
name: subagent-orchestration
description: Spawn a background child Agent via background_start/stop + subagent_plan/wait/chat. LOAD for isolation or parallelism. NO subagent_run — subagent = new background Agent process recipe.
category: agent
---

# Subagent orchestration

## 定义

**Subagent = 一个新的后台 Agent**（独立 `psi-agent ai` + `psi-agent session`），不是工具名，不是角色扮演。

| 已删除（禁止） | 使用 |
|----------------|------|
| `subagent_run` / `subagent_stop` / `subagent_list` | 本 skill 配方 |
| `bash` + `channel cli` 连子 Agent | **`subagent_chat`** |
| 手写 Named Pipe 路径 | **`subagent_plan`**（Windows 自动用 **TCP**） |

**静默规则（重要）：** Step 1–7 是内部操作。**不要**向用户逐步播报「正在启动 AI」「路径已解析」「让我检查协议」等。只在开始时一句话说明在派子 Agent（可选），**结束时给摘要结果**。调试失败时简短说明 blocker，不要贴长段自言自语。

---

## 何时使用

需要**单独 Session** 做有界任务 → 用本配方。固定多步流水线 → `fusion-flow`。一两步能做完 → 主 Session 直接做。**禁止**起第二个 Gateway。

---

# 配方步骤（入参 / 出参）

## Step 1 — `subagent_plan`

| 入参 | 说明 |
|------|------|
| `session_id` | 空 = 新 `sub-xxxxxxxx`；跟进同一子 Agent 时填原 id |
| `workspace` | 空 = 当前 workspace |

**出参 JSON（关键字段）：**

| 字段 | 用途 |
|------|------|
| `ok` | `true` 才继续 |
| `session_id` | 子会话 id |
| `ai_socket` / `channel_socket` | 后续 wait / chat |
| `ai_process_id` / `session_process_id` | 通常为 `{session_id}-ai` / `-session` |
| `ai_command` / `session_command` | 给 `background_start` 的 `command` |
| `shell` | Windows 为 `powershell` — **必须**传给 `background_start` |
| `repo_root` | `background_start` 的 `cwd` |

`ok: false` → 缺 model / base_url / API key。**优先**从 Gateway 已链接模型继承（`GET /ais/{id}/spawn-config`）；否则需用户环境变量 `FLOW_PSI_MODEL` + `FLOW_PSI_BASE_URL` + `OPENAI_API_KEY`（或启动 Gateway 前设好 `FLOW_PSI_*`）。**不要**假装已派 agent。

---

## Step 2 — `background_list`（可选）

确认 `{session_id}-ai` / `-session` 是否已在跑。都已 `alive` → 跳到 Step 6。

---

## Step 3 — 启动 AI — `background_start`

| 入参 | 值 |
|------|-----|
| `command` | plan 的 `ai_command` |
| `process_id` | plan 的 `ai_process_id` |
| `cwd` | plan 的 `repo_root` |
| `workspace` | plan 的 `workspace` |
| `shell` | plan 的 `shell` |

**出参：** `{ "ok": true, "process_id", "pid", ... }` — 失败则停止，不要继续。

---

## Step 4 — `subagent_wait`

| 入参 | 值 |
|------|-----|
| `socket` | plan 的 `ai_socket` |
| `timeout_seconds` | `30` |

**出参：** `{ "ok": true, "message": "ready" }`

---

## Step 5 — 启动 Session — `background_start`

| 入参 | 值 |
|------|-----|
| `command` | plan 的 `session_command` |
| `process_id` | plan 的 `session_process_id` |
| `cwd` / `workspace` / `shell` | 同 Step 3 |

然后 **`subagent_wait`**，`socket` = plan 的 `channel_socket`。

---

## Step 6 — 发任务 — `subagent_chat`

| 入参 | 值 |
|------|-----|
| `channel_socket` | plan 的 `channel_socket` |
| `message` | 自包含 task 正文（见模板） |
| `timeout_seconds` | `600`（可调） |

**出参：** `{ "ok": true, "text": "<子 Agent 回复正文>" }` — 只含最终文本，**无 reasoning 流**。

同一子 Agent 跟进：保留 `session_id`，**只重复 Step 6**（Step 3–5 跳过）。

### Task 模板

```markdown
## Objective
<一句话完成标准>

## Scope
Workspace: <path>

## Deliverable
<要点 / 表格 / 文件>

## Constraints
勿启动 Gateway 或其它 psi-agent。

## Context
<仅必要路径与片段>
```

---

## Step 7 — 收尾 — `background_stop`

不再复用该子 Agent 时：**先** `session_process_id`，**再** `ai_process_id`。

**出参：** `{ "ok": true, "status": "stopped", ... }`

并行 N 个子 Agent：每个 `session_id` 各做 Step 1–7；合并结果后用 **structured-output-tables**；全部 stop。

---

# 并行示例（P / C 两路 skill）

同一轮内：

1. `subagent_plan` ×2（不同 `session_id`）
2. 两路各 `background_start`（ai）→ `subagent_wait` → `background_start`（session）→ `subagent_wait`
3. 两路各 `subagent_chat`（task 不同）
4. 合并表格给用户
5. 四路 `background_stop`

**不要**边做边向用户念上述内部步骤。

---

# 反模式

| 错误 | 正确 |
|------|------|
| 逐步播报 spawn 调试过程 | 静默执行，只报结果/ blocker |
| Git Bash + `channel cli` + Named Pipe | `subagent_plan` + `subagent_chat` |
| 省略 `shell`（Windows 默认 bash） | `background_start(..., shell=plan.shell)` |
| spawn 后不 stop | Step 7 |

---

# 自检

- [ ] `subagent_plan` → `background_start` ×2 → `subagent_wait` ×2 → `subagent_chat` → 用户摘要
- [ ] 无逐步自言自语
- [ ] `background_stop` 已执行
- [ ] 未起 Gateway
