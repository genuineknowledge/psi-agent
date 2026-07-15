---
name: plan
description: Plan mode — decompose a complex task into small, ordered, executable steps and write them to a Markdown plan file. Do NOT implement. LOAD before starting a large or unfamiliar task, or when the user says "先做个计划" / "规划一下" / "别直接写". Distinct from task-planning (the in-session todo list) and clarify (asking the user).
category: agent
---

# Plan mode（前置规划）

**目的**：复杂任务动手前，先把它拆成小步、写成一份**可执行的 Markdown 计划文件**。
本 skill **只规划、不实现** —— 写完计划就停，把控制权交回用户审阅。

## 与相邻 skill 的边界

| skill | 是什么 | 何时用 |
|-------|--------|--------|
| **plan（本 skill）** | 把任务拆成小步，落成一份**磁盘上的 Markdown 计划文件**，不动代码 | 复杂/陌生任务**动手前**的前置规划 |
| **task-planning** | 用 `todo` tool 维护**会话内**执行清单，边做边勾 | 已经在执行、需要追踪进度时 |
| **clarify** | 缺信息/要选方案时**问用户** | 需求不清、要用户拍板时 |

一句话：**plan 写「怎么做」到文件，task-planning 追「做到哪」在会话，clarify 问「到底要什么」给用户。**

---

## 何时 LOAD

### 应该先规划

| 信号 | 示例 |
|------|------|
| **多文件 / 多模块改动** | 加一个跨 session/gateway/channel 的特性 |
| **陌生代码区** | 第一次碰某个子系统，需先摸清再落子 |
| **有多种可行架构** | 存储选 JSONL vs SQLite、同步 vs 异步 |
| **用户明确要计划** | 「先做个计划」「规划一下别直接写」「分步来」 |
| **风险/不可逆** | 迁移、重构、删数据 —— 先写清步骤与验证 |

### 不要规划（直接做 / 交给别的 skill）

| 情况 | 应该 |
|------|------|
| **一两步就能完成** | 直接做，最多用 `todo`（task-planning） |
| **需求本身不清** | 先 `clarify` 问用户，别对着模糊需求瞎拆 |
| **纯对话 / 只读查询** | 直接答 |
| **已经在执行中** | 用 `todo` 追踪，不要中途再开正式计划文件 |

---

## 计划文件

### 位置与命名

写到 `docs/superpowers/plans/`（若不存在则在 workspace 内建同名目录），命名：

```
docs/superpowers/plans/<YYYY-MM-DD>-<kebab-slug>.md
```

例：`docs/superpowers/plans/2026-07-13-add-plan-skill.md`。日期用**今天的真实日期**，slug 用简短英文短横线。

### 结构（照抄这个骨架）

```markdown
# <任务标题>

> **Goal:** <一句话说清要达成什么>

**Design Spec:** <若有对应 docs/superpowers/specs/….md 则链接，否则删掉此行>

---

### Task 1: <小步标题>

**Files:** `<会新建/修改的文件路径>`

- [ ] <具体、可验证的动作>
- [ ] <具体、可验证的动作>

---

### Task 2: <小步标题>

**Files:** `<路径>`

- [ ] <动作>
```

### 拆分原则

- **3–7 个 Task**，按**依赖顺序**排；每个 Task 是一个能独立完成、能验证的小步。
- 每步写清 **改哪些文件** + **具体动作**（用 `- [ ]` 复选框，便于执行时逐条勾）。
- **不要碎成 15 条 micro-task**，也不要一个 Task 塞下整个特性。
- **验证/测试**：只有用户要求或项目惯例必须时才单列测试 Task。
- 依赖有第三方库时**在计划里点名**（版本、为什么选它），把「需要新增依赖」显式写成一步，供审阅。

---

## 配方

### A. 生成计划

```
1. 读相关代码/spec，摸清现状（可用 read / find_files / 委派 subagent 调研）
2. 需求不清 → 先 clarify，不要对着模糊需求硬拆
3. 按上面骨架写文件 → write(path="docs/superpowers/plans/<date>-<slug>.md", …)
4. 停下。给用户一句话摘要 + 计划文件路径，交回控制权。
   —— 不要顺手开始实现。
```

### B. 修订计划

```
用户反馈 → edit 计划文件对应 Task（改动作、加/删 Task、调顺序）
→ 再次交回用户审阅
```

### C. 从计划到执行（本 skill 之外）

```
用户批准 → 切到执行：用 task-planning 的 `todo` 承载进度，
逐个 Task 做、做完把计划文件里的 [ ] 勾成 [x]。
```

---

## 不要

- **不要边写计划边改生产代码** —— plan mode 的核心就是「不实现」。
- 不要把礼貌性收尾（「询问用户是否满意」）写成 Task。
- 不要在需求不清时硬拆 —— 那是 `clarify` 的活。
- 不要为「显得专业」给简单任务强开计划文件。
- 写完计划不要自作主张接着实现；等用户批准。

---

## 相关

- **执行期进度追踪**：`skills/task-planning/SKILL.md`（`todo` tool）
- **问用户澄清/选型**：`skills/clarify/SKILL.md`
- **收尾自检**：`skills/task-self-check/SKILL.md`
- **历史计划范例**：`docs/superpowers/plans/*.md`
