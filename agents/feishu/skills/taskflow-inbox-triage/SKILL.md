---
name: taskflow-inbox-triage
description: "Turn a pile of incoming items (emails, IM messages, issues, notifications, pasted text) into a triaged, prioritized, actionable task list. LOAD when the user says something like 收件箱/inbox 太乱、帮我理一下未读、这堆邮件/消息该先处理哪个、把这些转成待办/任务、清一清 backlog. Source-agnostic: it classifies, prioritizes, and converts — it does not fetch. Pull the raw items with whatever source skill/tool fits (himalaya for email, feishu/discord/x_search for IM, github-issues for issue backlog, fetch/read for pasted or linked text), then this skill decides what each item is, how urgent it is, and what task it becomes. Default sink is the session `todo` list; optional sinks are GitHub issues and scheduled reminders."
category: agent
---

# Taskflow Inbox Triage —— 收件箱 → 分类 → 排序 → 可执行任务

把一堆**进来的东西**（未读邮件、飞书/Discord 消息、GitHub issue backlog、@提及、
粘贴的文本）理成一张**分好类、排好序、能直接开工**的任务清单。

**这个 skill 只管三件事:分类(classify) → 排序(prioritize) → 转任务(convert)。**
它**不负责去抓数据** —— 抓取交给对应的来源 skill/工具（见下「取件」）。抓回来的原始
条目丢进这个流程,它决定「每条是什么、多急、变成哪个任务」,默认写进当前会话的 `todo`。

Reply in Chinese unless the user clearly uses another language.

## 什么时候 LOAD

- 「收件箱/inbox 太乱,帮我理一下」「这堆未读先看哪个」
- 「把这些邮件/消息/issue 转成待办」「清一清 backlog」
- 用户一次性甩来**一批**待处理条目(不管来自哪),要你排优先级 + 落成任务

**不该 LOAD:** 只有一条明确任务(直接做或直接建一条 todo)、纯问答/翻译/闲聊、
用户只是要「读某封信」(那是 [himalaya](../himalaya/SKILL.md) 的活,不必 triage)。

## 与相邻 skill 的分工

| 场景 | 用谁 |
|------|------|
| 一堆杂乱条目要**分类+排序+转任务** | **本 skill** |
| 已经有明确的多步任务,只要拆解+追踪进度 | [task-planning](../task-planning/SKILL.md)(`todo` 工具) |
| 缺信息、要用户在几个方案里选 | 用自然语言直接问用户(别自己猜) |
| 某个 triage 出来的任务很重、要隔离执行 | [subagent-orchestration](../subagent-orchestration/SKILL.md) |

本 skill 的产出(排好序的任务)**直接喂给 `todo`**,之后的执行/追踪归 task-planning。
两者接力:triage 决定「做什么、什么顺序」,task-planning 承载「做到哪一步」。

## 流程(四步)

### 1. 取件(Collect)—— 不是本 skill 的核心,但得先有料

先把原始条目拿到手。**来源无关**,挑合适的工具/skill:

| 来源 | 怎么取 |
|------|--------|
| 邮件(IMAP) | [himalaya](../himalaya/SKILL.md):`himalaya envelope list -f INBOX -o json` |
| 飞书 | `feishu_doc` / `feishu_drive` 工具 |
| Discord | `discord` 工具的 `fetch_messages` / `search_members` |
| X/Twitter | `x_search` 工具 |
| GitHub issue backlog | [github-issues](../github-issues/SKILL.md):`gh issue list` |
| 链接/网页 | `fetch` 工具 |
| 用户直接粘贴 / 本地文件 | 直接用文本;文件用 `read` |

拿到后统一成一张「条目表」:每条至少有 **id / 来源 / 摘要**,尽量带 **发件人 / 时间 / 链接**。
条目多(>~15 条)时,取件+初筛可以丢给 subagent 并行,主线程只收结果。

### 2. 分类(Classify)

给每条打一个**类型**标签。默认用下面这套,可按用户领域调整:

| 类型 | 含义 | 典型信号 |
|------|------|----------|
| `action` | 需要我做事 | 「请…」「能不能…」「deadline」「待办」明确请求 |
| `reply` | 只需回复/答复,无别的动作 | 提问、确认、征求意见 |
| `waiting` | 在等别人,我这边挂起 | 「等你回复后…」对方球未落 |
| `fyi` | 知会/参考,不用动 | 通知、抄送、周报、订阅 |
| `spam` | 噪音/无关 | 营销、自动化、误发 |

分类只做**判断**,不臆造事实。拿不准类型的条目标 `unsure`,别硬塞。

### 3. 排序(Prioritize)

对 `action` / `reply` / `waiting` 排优先级。用**紧急度 × 重要度**两维,落成 P0–P3:

| 优先级 | 判据 | 例 |
|--------|------|-----|
| **P0** | 紧急且重要:有硬截止/线上故障/阻塞他人 | 「今天下班前要」「生产挂了」 |
| **P1** | 重要不紧急:该做,近几天内 | 需要推进的正事,无当天死线 |
| **P2** | 紧急不重要:能快速清掉 | 一句话能回的确认 |
| **P3** | 都不高:有空再说 | fyi 里偶尔需轻动作的 |

排序信号:**显式截止日期 > 发件人重要度 > 是否阻塞他人 > 时效性**。
截止日期尽量解析成绝对日期(相对「明天/下周」按当前日期换算),别留模糊词。
`fyi`/`spam` 不进优先级队列。

### 4. 转任务(Convert)—— 默认落到 `todo`

把 `action` / `reply`(以及需要跟进的 `waiting`)转成**可执行任务项**,写进当前会话
的 `todo` 列表。任务描述要**动词开头、具体、可判定完成**,别照抄原文标题。

```
# 排好序后一次性建表(P0 在前 → in_progress,其余 pending)
todo(todos='[
  {"id":"1","content":"回复张三关于 API 截止日期(今天 18:00 前确认能否交付)","status":"in_progress"},
  {"id":"2","content":"给运维提工单:登录页 500(阻塞用户,附错误链接)","status":"pending"},
  {"id":"3","content":"回复季度周报,确认收到即可","status":"pending"}
]', merge=false)
```

- 一批 triage 出来 **3–7 条**主任务为宜;碎条(一句话能清的)合并或直接顺手做掉。
- 每条尽量把**来源链接/发件人/截止**塞进 content 或结尾括注,执行时不用回头翻。
- `waiting` 项如果需要我方跟进,转成「(日期)追问 X 是否回复」;纯等待不用建 todo。
- 具体的 `todo` 用法(merge、status 流转、单一 in_progress)见
  [task-planning](../task-planning/SKILL.md)。

#### 可选去向(用户明确要时才用)

- **GitHub issue**:团队 backlog 里的 `action` 想落成 issue → [github-issues](../github-issues/SKILL.md)
  的 `gh issue create`(带 label/assignee)。
- **定时提醒**:有硬截止、要到点提醒 → `schedule_manage` 工具挂 cron。
- **沉淀记录**:需要长期留存的清单 → `memory_add` 或 `write` 成 workspace 内 Markdown。

## 收尾:给用户一张 triage 小结

建完 todo 后,给用户一段**紧凑**的分类小结,让他一眼看清"我理成了什么":

```
共 12 条:P0 ×1、P1 ×2、reply ×3、fyi ×5、spam ×1(已忽略)
→ 已建 3 条 todo,P0「回复张三 API 截止」进行中
需要你定:第 4 条"预算审批"发件人不确定,要不要处理?
```

- 别复读整张 todo 表;别把每条 fyi/spam 都列出来。
- 有 `unsure` / 拿不准该不该做的条目,收尾时点名,直接问用户拍板。

## 安全与边界

- **triage 是判断,不是代替用户做决定的授权。** 分类/排序是建议;**回邮件、提 issue、
  发消息这类外发动作,发出前必须按对应 skill 的规则复述确认**(如 himalaya 发信前确认
  收件人/主题/正文)。本 skill 只把它们**排进 todo**,不擅自触发外发。
- **不臆造条目内容。** 摘要基于原文,读到什么写什么;拿不准标 `unsure`,别脑补。
- **`spam`/`fyi` 是"先不处理",不是"删除"。** 除非用户明确要求,绝不代删邮件/消息/issue。
- **收件箱内容含隐私**(发件人、正文、内部信息):只按当前 triage 请求用,不外传、
  不回显敏感值,引用按「发件人/主题」而非贴全文。
- **优先级是相对判断,会错。** P0/P1 拿不准时如实标注理由,让用户可推翻。

## 常见坑

| 症状 | 处理 |
|------|------|
| 条目太多(几十上百) | 先按来源/时间粗筛,`fyi`/`spam` 批量归类;`action` 才逐条排序;可用 subagent 并行初筛 |
| 全标成 P0 | 优先级会失效 —— 强制拉开档次,同一批 P0 尽量 ≤2~3 条 |
| 任务描述照抄原邮件标题 | 改写成动词开头、可判定完成的动作(「回复 X 确认 Y」而非「Re: 关于 Y」) |
| 相对截止日期(明天/下周) | 按当前日期换算成绝对日期再写进任务 |
| 把 triage 当成执行 | 本 skill 只到"建好排序的 todo";实际执行/追踪交给 task-planning + 各来源 skill |
| 擅自回信/提 issue | 外发动作先确认,本 skill 默认只落 todo,不自动外发 |
