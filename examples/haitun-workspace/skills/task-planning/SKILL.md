---
name: task-planning
description: Decide when multi-step work MUST use the session todo tool, decompose steps, and track progress. LOAD when starting or continuing complex work — not merely when the user says "make a todo list".
category: agent
---

# Task planning（session `todo`）

本 skill 是 **何时建 todo / 如何维护** 的权威约定。Gateway / spa-v2 只**消费**已写入的清单（`N/M`、当前项）；**不**替你决定要不要拆步。先判该不该建表，再动手。

## 一句话

- **用户说目标；Agent 决定要不要拆。** 不必等「帮我列个 todo」。
- **`todo` = 本 Session 的可核对执行清单**（可持久、可被 UI 读），不是对用户的问卷。
- **有分拆价值才建表；没有就直接做** — 禁止为「显得专业」凑清单。

---

## 判定门（先过这扇门）

问自己三句（任一为「是」→ **必须** `todo`；全「否」→ **禁止** 建表）：

1. **多步可核对？** 完成过程需要 ≥3 个可独立勾掉的步骤，或用户一次塞了多项子目标？
2. **多产物 / 多文件 / 长工具链？** 要写文档、改多文件、调研+成稿+交付，或预计 ≥3 轮 tool？
3. **中途容易丢线？** 不做清单就容易漏步、重复做、或做完无法自检「还剩什么」？

| 门控结果 | 行为 |
|----------|------|
| **必须建表** | 开工前（或发现变复杂的当轮）`todo(..., merge=false)` 写出计划，立刻推进第 1 项 |
| **禁止建表** | 直接执行；不要空喊「我先列个计划」却不写 `todo`，也不要写只有 1 条的装饰性清单 |
| **中途升级** | 开头以为一步能完、做到一半变大 → **当轮**改判为必须，用 `merge=false` 重开一版或 `merge=true` 补步骤 |

### 必须建表（示例）

| 信号 | 示例 |
|------|------|
| ≥3 个依赖步骤 | 读材料 → 提纲 → 成稿 → 校验 → `[SEND:]` |
| 一次多项 | 「修 CI、改 README、再开 PR」 |
| 长链路 | 多轮 bash / 多文件 edit / 浏览器调研 + 写报告 |
| 多交付物 | 剧本杀全文 + 角色卡 + Word 打包 |
| 用户强信号 | 「分步做」「别漏」「先计划再执行」（加强信号，不是唯一条件） |

### 禁止建表（示例）

| 情况 | 示例 |
|------|------|
| 一步能完 | 读一个文件、答一个概念、跑一条命令、改一个typo |
| 纯对话 | 翻译、总结、闲聊、澄清概念 |
| 短 skill ≤2 步 | 固定 one-shot 配方足够 |
| 用户只要结果 | 「别啰嗦，直接给答案 / 直接改」 |
| 单次 clarify | 缺信息先问清楚 — 用 `clarify`，不要用 todo 代替提问 |

### 灰色（自行判断，默认偏「不做表」）

- 名义 2 步但每步很重（例如「写完整 Word 剧本」）→ 可拆 **2～4 条**有意义的阶段，不要拆成「写第 1 章 / 第 2 章…」刷条数。
- 已有清晰口头计划且本轮内肯定做完 → 口头执行即可，**可不**落 `todo`。

---

## 与 UI / 进度条的关系（刻意）

- **有 `todo` 文件** → 前端可显示 `N/M` 与当前项；这是清单的副作用，不是建表目的。
- **无 `todo`** → 任务仍可有侧栏/状态；**不要**为了喂进度条而建空表或假步骤。
- Agent **不**负责百分比圆环；只负责清单真伪与 status 及时更新。

---

## 配方

### A. 开始（必须建表时）

```
判定门 → 必须：
  todo(todos='[
    {"id":"1","content":"…","status":"in_progress"},
    {"id":"2","content":"…","status":"pending"},
    …
  ]', merge=false)
  → 立刻做第 1 项（禁止只建表不动手）
```

**拆法：** 通常 **3–7 条**，按依赖排序；每条是用户能听懂的阶段，不是微操作。
**`content` 必须是字符串**（不要传 list/对象）。
**不要**默认加「询问用户是否满意」；**不要**默认加「测试/验证」除非用户要求或项目惯例必须。

### B. 推进

```
完成一步 → merge=true：旧项 completed，下一项 in_progress
发现新步骤 → merge=true：追加 pending
某步失败 → 旧项 cancelled + 追加修订项（merge=true）
```

**同时只有 1 个 `in_progress`。** 完成即标 `completed`，不要攒到最后一次性勾完。

### C. 收尾

```
相关项全部 completed（废弃项 cancelled）
→ 给用户结果摘要（做了什么、如何验证）
→ 不要复读整张表，除非用户要看
```

### D. 只读

```
todo()   # 无参 → 完整列表 + summary
```

---

## 静默与播报

- 建表 / 改 status：**不要**逐步播报「我更新了 todo」。
- 开始：一句目标或直接开干；结束：结果摘要。
- 长任务可在**大阶段之间**给一行进度（刚完成什么、下一步什么）——不替代 Execution Bias。

---

## 不要

- 不要把礼貌收尾、clarify 选项写进 todo
- 不要用 todo 代替 `clarify` / 自然语言提问
- 不要为侧栏进度、为「看起来在推进」而建 1 条装饰清单
- 不要与 `goal`（跨会话长期目标）或飞书个人 ToDo 看板 skill 混淆：本 tool 只服务**当前 Session 执行步骤**

---

## 相关

- Tool：`tools/todo.py`（读空参；写 JSON 数组；`merge`）
- 落盘：AppData `todos/{session_id}.json`（legacy `{workspace}/.psi/todos/` 双读）
- 收尾自检：`skills/task-self-check/SKILL.md`
- 隔离子任务：用 subagent，不是 todo — `skills/subagent-orchestration/SKILL.md`
