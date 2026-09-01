---
name: taskflow
description: "Manage tasks, projects, and workflows as a durable, cross-session board under <workspace>/taskflow/. A three-layer model: workflows define the allowed status flow, projects group tasks, and tasks are the atomic work items that move through statuses. Each item is Markdown with YAML frontmatter (title/slug/status/priority/assignee/timestamps) and links others with [[slug]]. LOAD when the user wants to track work that outlives one chat — create/advance/query tasks, plan a project, define a workflow, or render a board. Distinct from task-planning (the in-session todo list), plan (a one-shot plan file), and clarify (asking the user). Uses only the existing read/write/edit/find_files/list_dir/search_content/bash tools — no dedicated tool, no extra deps."
category: productivity
---

# Taskflow（任务 / 项目 / 工作流管理）

维护一个**跨会话持久**的任务看板：**工作流(workflow)** 定义状态怎么流转，**项目(project)**
把任务归组，**任务(task)** 是走在某工作流上的最小工作项。所有内容是带 YAML frontmatter 的
Markdown，存在 `<workspace>/taskflow/` 下，会话结束也不丢，随时间累积成团队/个人的长期看板。

本 skill 是**纯约定 + 纪律**——只用 workspace 已有的文件工具（`read`、`write`、`edit`、
`find_files`、`list_dir`、`search_content`、`bash`）。**没有专用 tool，零额外依赖。**

除非用户明显用其它语言，一律用中文回复。

---

## 与相邻 skill 的边界

| skill | 是什么 | 生命周期 | 何时用 |
|-------|--------|----------|--------|
| **taskflow（本 skill）** | 磁盘上的**持久**任务/项目/工作流看板 | **跨会话**，长期累积 | 跟踪能跨越多次对话的工作：项目、待办、状态流转 |
| **task-planning** | 用 `todo` tool 维护的**会话内**执行清单 | 单个 session | 已经在执行、想追踪本轮进度 |
| **plan** | 动手前写的**一次性** Markdown 计划文件 | 一次性 | 复杂/陌生任务落子前的前置规划 |
| **clarify** | 缺信息/要选方案时**问用户** | 一次性 | 需求不清、要用户拍板 |

一句话：**taskflow 管「长期要做哪些事、到哪一步了」并落盘；task-planning 追「本轮做到哪」在会话；
plan 写「这个任务怎么做」到文件；clarify 问「到底要什么」给用户。**

判断：如果这项工作**下次对话还得接着跟**（项目、需求池、bug 列表、个人待办），用 taskflow；
如果只是**本轮执行的临时步骤**，用 `todo`（task-planning）。

---

## 存储布局（`<workspace>/taskflow/`）

首次写入时创建目录。

```
taskflow/
  workflows/<workflow-slug>.md   # 可选：自定义状态机；不建则用默认工作流
  projects/<project-slug>.md     # 项目：目标、状态、关联 task
  tasks/<task-slug>.md           # 任务：单一工作项，属于某 project，走某 workflow 的 status
  board.md                       # 可选看板：按 status 汇总所有 task
```

**slug 规则**（对齐 llm-wiki/ontology，便于 `[[slug]]` 互链）：标题小写、非字母数字折叠成单个短横线。
例："登录页重构" 可用 `login-page-refactor`，"Fix CI flakiness" → `fix-ci-flakiness`。
**slug 在同类型内唯一**（同一个 task slug 不重复）。

**默认工作流**（不定义 workflow 文件时）：

```
todo → doing → done
          ↘ blocked ↗        （blocked 可回到 doing）
  任意状态 → cancelled        （废弃）
```

时间戳一律 ISO-8601 UTC，用 `bash: date -u +%Y-%m-%dT%H:%M:%SZ` 取。

---

## 文件格式

### task（`tasks/<slug>.md`）

```markdown
---
title: 修复登录页在移动端错位
slug: fix-login-mobile-layout
project: login-revamp
workflow: default
status: doing
priority: P1
assignee: alice
tags: [frontend, bug]
created: 2026-07-15T08:00:00Z
updated: 2026-07-15T09:30:00Z
due: 2026-07-20
---

## 描述
移动端 <768px 时登录框溢出容器。

## 验收标准
- [ ] iPhone SE / Pixel 视口下不横向滚动
- [ ] 表单在 320px 宽仍可用

## Log
- 2026-07-15T08:00:00Z 建单，todo
- 2026-07-15T09:30:00Z todo → doing（alice 接手）

## Links
属于 [[login-revamp]]；阻塞于 [[design-tokens-migration]]。
```

字段：`title`（人读标题）、`slug`（文件名主干）、`project`（所属 project slug，无则 `none`）、
`workflow`（用哪个工作流，默认 `default`）、`status`（当前状态，必须是该 workflow 的合法状态）、
`priority`（`P0`–`P3`）、`assignee`（负责人，可空）、`tags`（kebab-case 列表）、
`created`/`updated`（ISO-8601 UTC）、`due`（可选，`YYYY-MM-DD`）。

### project（`projects/<slug>.md`）

```markdown
---
title: 登录体验重构
slug: login-revamp
status: active
owner: alice
created: 2026-07-15T08:00:00Z
updated: 2026-07-15T08:00:00Z
---

## 目标
把登录/注册流程重构为响应式，移动端可用。

## 范围
仅前端；不含后端鉴权改造。

## Tasks
- [[fix-login-mobile-layout]]
- [[add-oauth-buttons]]

## Milestones
- 2026-07-20 移动端可用
```

字段：`title`、`slug`、`status`（`active` / `paused` / `done` / `archived`）、`owner`、
`created`/`updated`。body 用 `## Tasks` 列 `[[task-slug]]`，`## Milestones` 记里程碑。

### workflow（`workflows/<slug>.md`，可选）

```markdown
---
title: 默认工作流
slug: default
states: [todo, doing, blocked, done, cancelled]
transitions:
  todo: [doing, cancelled]
  doing: [blocked, done, cancelled]
  blocked: [doing, cancelled]
  done: []
  cancelled: []
---

## 状态含义
- **todo** 待办，未开始
- **doing** 进行中
- **blocked** 受阻，等外部条件
- **done** 完成
- **cancelled** 废弃

## 说明
task frontmatter 里 `workflow: default` 即引用本文件。自定义流程时新建一个 workflow 文件，
task 引用其 slug。
```

字段：`title`、`slug`、`states`（合法状态列表）、`transitions`（每个状态 → 允许流转到的状态列表）。

---

## 配方

### A. 建 project

```
1. 先去重：search_content query="<关键词>" path="taskflow/projects"
   （或 bash: grep -ril "<term>" taskflow/projects/）
   命中同主题 → 更新已有 project，不新建。
2. bash: date -u +%Y-%m-%dT%H:%M:%SZ  取时间
3. write taskflow/projects/<slug>.md，frontmatter 齐全（created == updated == now）。
```

### B. 建 task

```
1. 去重：search_content query="<关键词>" path="taskflow/tasks"
2. 定 project（无归属填 none）、workflow（默认 default）、初始 status（默认该 workflow 首状态）、
   priority、assignee。
3. 取时间 → write taskflow/tasks/<slug>.md。
4. 若属于某 project：read 该 project，在其 ## Tasks 里 edit 追加 [[task-slug]]，bump updated。
```

### C. 推进 task 状态（核心）

```
1. read taskflow/tasks/<slug>.md，拿当前 status 与 workflow。
2. read 对应 workflow（default 用内置默认表）→ 查 transitions[当前状态] 是否含目标状态。
   - 非法流转 → 停下，告诉用户「当前 X 只能到 [...]」，不硬改。
3. 合法 → edit frontmatter：status 改为新值、updated 改为 now。
4. 在 ## Log 追一行：「<now> 旧状态 → 新状态（原因/操作人）」。
5. 若变 done/cancelled 且属于某 project：可选去 project 的 ## Tasks 勾掉复选框。
```

### D. 查询 / 过滤

```
按 status：   grep -rl "^status: doing"     taskflow/tasks/
按 priority： grep -rl "^priority: P0"       taskflow/tasks/
按 assignee： grep -rl "^assignee: alice"    taskflow/tasks/
按 project：  grep -rl "^project: login-revamp" taskflow/tasks/
组合过滤先 grep 出候选再 read 逐个确认。search_content 做全文/模糊匹配。
汇报时给「标题 · status · priority · assignee」的紧凑列表，不要贴整文件。
```

### E. 看板视图（`board.md`）

```
1. list_dir taskflow/tasks/ → 逐个 read 取 frontmatter。
2. 按 status 分组、组内按 priority(P0→P3) 排序。
3. write/覆盖 taskflow/board.md：每个 status 一个 ## 段，列
   - [[task-slug]] — 标题 · Pn · @assignee
4. board.md 是**派生视图**：任务状态变了就重新生成，不要手改它当真相源。
```

### F. 自定义 workflow

```
1. write taskflow/workflows/<slug>.md，定义 states + transitions（见格式）。
2. 让相关 task 的 frontmatter workflow 字段指向该 slug。
3. 推进状态时按该 workflow 的 transitions 校验。
```

### G. Housekeeping（改名 / 删除，照 llm-wiki 保持链接一致）

```
改名 task/project：
  1. 先找反向链接：bash: grep -rl "\[\[<old-slug>\]\]" taskflow/
  2. 重命名文件 + 改其 slug/title
  3. 修正所有入链 [[old-slug]] → [[new-slug]]，并更新 task 的 project 字段
删除：
  1. grep 找出所有指向它的 [[...]] 入链，告知用户这些会变悬空
  2. delete 文件；按用户意愿清理或保留对端链接
```

---

## 原则

- **跨会话持久**：taskflow 存磁盘、下次对话还在——这是它区别于 `todo` 的根本。别拿它当会话内清单。
- **状态流转要合法**：改 status 前先查 workflow 的 transitions；非法流转要拒绝并说明，不硬改。
- **每次变更留痕**：状态变化写进 task 的 `## Log`，并 bump `updated`（保留原 `created`）。
- **slug 唯一可互链**：同类型内 slug 不重复；project/task 用 `[[slug]]` 双向引用。
- **board 是派生视图**：真相源是各 task 文件，`board.md` 随时可重建，不手改。
- **零依赖**：只用现有文件工具读写 Markdown，永远不要为此加数据库/任务库依赖。
- **静默维护**：建/改多文件时别逐步播报；开头一句、结尾给结构化摘要。
- **不存密钥**：taskflow 是用户可读可编辑的纯 Markdown。

---

## 相关

- **会话内进度清单**：`skills/task-planning/SKILL.md`（`todo` tool）
- **动手前一次性规划**：`skills/plan/SKILL.md`
- **问用户澄清/选型**：`skills/clarify/SKILL.md`
- **文本知识库（可交叉链接）**：`skills/llm-wiki/SKILL.md`、`skills/ontology/SKILL.md`
