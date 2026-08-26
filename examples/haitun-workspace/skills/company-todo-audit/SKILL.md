---
name: company-todo-audit
description: "公司 TODO 管理体系·闭环判定与回流 —— 在每次采集前，按闭环五要素判定上一周期每条 todo 是否真正闭环；未闭环项按原截止日算逾期天数，标题加 [逾期 N 天] 前缀回流进本周期派发列表；给 boss 出整体统计。Use when the todo-cycle-audit schedule fires (fire=prompt, cron '30 14 * * 1,3,5', 本周期 15:00 采集前半小时), or when explicitly asked to audit last cycle's closure status by hand. Must run before company-todo-sync in the same cycle — sync 依赖本技能算出的回流项。Companion skills: company-todo-sync (采集与派发), company-todo-review (评价回写)。"
category: productivity
---

# 公司 TODO 管理体系 · 闭环判定与回流

「交付完成」不等于「勾了框」。每次采集前半小时跑一次审计，判上一周期每条 todo 是否走完整圈；
没走完的带着逾期天数回流进本周期，绝不静默消失。

**必须早于 `company-todo-sync` 运行**：回流项要先算出来才能进入本次派发列表。两条 schedule 的
cron 已经错开（本技能 `30 14 * * 1,3,5`，sync `0 15 * * 1,3,5`），正常情况不需要手动排序。

## 用到的工具

- `feishu_bitable_search_records` — 拉上一周期全部 todo 行
- `feishu_api`（GET `/open-apis/task/v2/tasks/:task_guid`）— 查 `assignee_related[].completed_at`
  （见 `feishu-task` 技能表；main 上没有专用的 `feishu_task_get` 工具，任务域已迁移为端点表）
- `wiki_read` — 核对评价是否已回写进对应快照页
- `feishu_bitable_update_records` — 把回流项写进新周期（复制一行、改标题前缀、原截止日保留）
- `feishu_message_send_card` — 给 boss 推整体统计

## 闭环五要素

五项齐备才置为「已闭环」，缺任一项都停在「未闭环」：

| # | 要素 | 从哪验证 |
|---|---|---|
| 1 | 有明确验收人 | 台账 `mentor` 字段非空 |
| 2 | 有自设截止时间且已到期或提前完成 | 台账截止日期 + 实际完成时间 |
| 3 | 负责人已勾选并提交成果物 | `feishu_api` 读任务详情，该负责人在 `assignee_related[]` 里的 `completed_at` 非空 |
| 4 | mentor 已给出评价与打分 | 台账 `mentor打分` 与 `mentor评语` 字段非空 |
| 5 | 评价已回写进本人 wiki 该条 todo 之后 | `wiki_read` 该快照页命中评语文本 |

判定第 3 项时**不要**用 `GET /tasks?type=my_tasks` 列出「机器人自己的任务」——那答的是「发请求身份
自己的任务」，不是某个员工的。要查某人某条任务做完没，直接读该任务详情，看
`assignee_related[]` 里那个人自己的 `completed_at`。

**判定结果写进台账**：五要素不是人工填的文本，而是本技能自动判定的结果——逐条判完后把**满足的
要素**勾进该行台账的「闭环五要素」多选字段（选项名与上表要素名一致），五项勾齐才允许把状态置
「已闭环」；缺任一项状态停在「进行中」/「未闭环逾期」。第 5 项只做**验证**：评价回写 wiki 的
动作由 `company-todo-review` 在 mentor 提交评价时立即执行，本技能用 `wiki_read` 核对快照页该
todo 之后确实有评语文本即可。

## 第 0 步：评价回写扫描（表内直接打分的兜底通道）

mentor 有两条打分通道：评价卡（实时回写 wiki）与**台账表内直接填「mentor打分」「mentor评语」**
（更顺手，但没有事件通知 agent）。本步负责兜底，在五要素判定**之前**执行：

1. 拉上一周期全部行，找出「`mentor打分` 或 `mentor评语` 非空」但「闭环五要素里没有『评价已回写wiki』」
   的行。
2. 对每一行：`wiki_read` 该负责人本周期快照页，在对应 todo 条目之后插入评价（打分 + 评语 + 时间），
   其余内容原样保留，`wiki_write` 整页回写；然后勾上「评价已回写wiki」要素。
3. 扫描后**再**进入五要素判定——表内打分从此和评价卡同权，不存在「表里打了分但 wiki 漏回写」的死角。

## 回流规则

1. 遍历上一周期所有台账里状态非「已闭环」「请假顺延」的行，按原截止日算出逾期天数
   （今天 − 原截止日）。
2. 回流项**进入新周期的派发列表**（供 `company-todo-sync` 本轮一并派发），标题前缀
   `[逾期 N 天]`，原截止日保留在正文里，**不许静默改截止日**。
3. 连续两个周期未闭环的项，在该 mentor 的报表里单独列一块「持续逾期」，并同步给该 mentor 的上级
   （从其 wiki 页「上级：[[...]]」区块解析）。
4. 请假顺延项（`company-todo-sync` 第 2 步标记的）**不计入逾期天数**，但仍然回流——顺延不等于取消。
5. **回流不删除历史**：上一周期的快照页里该条仍然是「未闭环」原样保留，新周期页里是**新的一条**，
   两条通过标题（`[逾期 N 天] <原标题>`）互相对应，不做原地覆盖。

## 给 boss 的整体统计

`feishu_chart` 或直接文本卡列出：本周期大目标/小目标/todo 各多少条、已闭环多少、逾期多少、
请假顺延多少、各 mentor 下属平均分。boss 只读，不写 todo、不被派发——统计卡是私聊推送，不进群。

## 边界

- 五要素任一缺失都不能置「已闭环」，即便负责人已经勾选卡片——「勾了框」只满足第 3 项。
- 逾期天数按**原截止日**算，不是按上次审计日算；不许因为回流就悄悄顺延一个新截止日。
- 判定用的数据只信台账 + 飞书任务详情 + wiki 快照页三处权威源，不接受口头/聊天记录里的「说已经做完了」。
