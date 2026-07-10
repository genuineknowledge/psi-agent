---
name: task-planning
description: Decide when multi-step work needs a session todo list, decompose tasks, and track progress with the todo tool. LOAD when starting or continuing complex work — not when the user explicitly asks to "make a todo list".
category: agent
---

# Task planning (todo)

## 原则

- **用户说目标，Agent 决定是否拆** — 不必等「帮我拆任务」「列个 todo」。
- **`todo` tool = 记事本**；拆什么、几条、顺序 — **你的判断**。
- **静默维护**：建表、改 status 不要逐步播报；开始一句、结束给结果摘要。
- 与 **`clarify`** 不同：todo 是 **Agent 自己的执行清单**，不是问用户「还需要什么」。

---

## 何时 LOAD / 何时调用 `todo`

### 应该建表或更新（有分拆价值）

| 信号 | 示例 |
|------|------|
| **3+ 依赖步骤** | 先读代码 → 改 3 个文件 → 跑测试 → 更新文档 |
| **用户一次给多项** | 「修 CI、顺便更新 README、再开个 PR」 |
| **长链路 tool 调用** | 多轮 bash / 多文件 edit / 调研 + 实现 |
| **执行中发现新步骤** | `merge=true` 追加或改 status |
| **强信号** | 「分步做」「别漏」「先计划」—— 但不是必要条件 |

### 不要调用 `todo`（直接做）

| 情况 | 示例 |
|------|------|
| **一步完成** | 读一个文件、答一个概念、跑一条命令 |
| **纯对话** | 翻译、总结、闲聊 |
| **已有短 skill 且 ≤2 步** | 固定 one-shot 配方够用时 |
| **用户要直接结果** | 「别啰嗦，直接给答案」 |

### 灰色地带（自行判断）

- 名义 2 步但每步很重 → 可拆 **2 条**
- 开头以为简单，中途变大 → 做到一半 **`merge=false` 新开一版计划** 也可

---

## 配方

### A. 开始复杂任务

```
判断：3+ 步 / 多子任务 / 长链路？
  → 否：直接执行，不调 todo
  → 是：
      todo(todos='[
        {"id":"1","content":"…","status":"in_progress"},
        {"id":"2","content":"…","status":"pending"}
      ]', merge=false)
      → 立刻做第 1 项（不要只建表不动手）
```

**拆法：** 一般 **3–7 条**，按依赖排序；不要碎成 15 条 micro-task。  
**不要**默认加「测试/验证」条，除非用户要求或项目惯例必须。

### B. 推进中更新

```
完成一步 → todo(todos='[{"id":"1","status":"completed"},{"id":"2","status":"in_progress"}]', merge=true)
发现新步骤 → todo(todos='[{"id":"4","content":"…","status":"pending"}]', merge=true)
某步失败 → cancel 旧项 + 加修订项 merge=true
```

**同时只有 1 个 `in_progress`**（store 会纠正多个 in_progress，但仍应主动保持）。

### C. 收尾

```
全部 relevant 项 → completed（或 cancelled 废弃项）
→ 给用户 **结果摘要**（完成了什么、如何验证）
→ 不要复读整张 todo 表，除非用户要看
```

### D. 只读当前计划

```
todo()   # 无参，返回完整列表 + summary
```

---

## 与 system prompt 的关系

`Planning & Progress` 段要求多步工作先 brief plan — **本 skill 规定用 `todo` tool 承载该 plan**，使进度可追踪、可持久（`workspace/.psi/todos/{session_id}.json`）。

---

## 不要

- 不要把礼貌性收尾写进 todo（「询问用户是否满意」不是任务项）
- 不要每改一次 todo 就向用户汇报
- 不要用 todo 代替 `clarify`（缺信息、要选方案 → 用 clarify 或自然语言问用户）
- 简单任务不要为了「显得专业」强行建表

---

## 相关

- **Task self-check**：收尾前静默自检 — `skills/task-self-check/SKILL.md`
- **Subagent 委派**：隔离子任务用 subagent，不是 todo — `skills/subagent-orchestration/SKILL.md`
