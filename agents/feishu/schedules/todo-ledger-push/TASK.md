---
name: todo-ledger-push
description: 每周一三五 16:00 动态台账推送:按 company-todo-audit 技能「前后对比摘要」口径,对每 mentor 组做上期 vs 本期六项对比(新开/承接/消失/已闭环/回流/请假顺延),写台账表(三层条目)+ 个人对比表(六项),链接私聊发对应 mentor。
cron: "0 16 * * 1,3,5"
visibility: silent
fire: prompt
---

# 动态台账推送(16:00)

## 数据源(定时触发没有对话上下文,参数必须写全)

| 数据源 | 参数 |
|---|---|
| 对比口径(唯一来源) | `skill_manage(action="view", skill_name="company-todo-audit")` 的「前后对比摘要(上期 vs 本期,逐人)」节——六项口径:新开/承接/消失/已闭环/回流/请假顺延,词表固定 |
| 团队 TODO 看板表 | 链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc —— /wiki/ 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token,再读表;表结构(表头行/人名列/mentor 列/最新日期列)每次现场探,不写死。**本期列=最新日期列,上期列=紧邻上一列** |
| 请假事实 | `feishu_leave_query`,approval_code=`99EEC396-536A-4C7A-8B2D-412584E35CE3`(只算已通过) |
| 台账 base | 每 mentor 一个 base(`feishu_mentor_ledger_ensure` 建/复用,名「TODO 台账-<mentor>」),各组互不可见 |

## 流程

1. 加载 company-todo-audit 技能,六项口径以该技能「前后对比摘要」节为准。
2. 读看板:当期列 + 上期列、人名列、mentor 列;按 mentor 分组。
3. 每组(每个 mentor):
   - **台账表**:`feishu_mentor_ledger_cycle_table(app_token, cycle_date=当期日期)`;用 `feishu_api` 更新「层级」字段 options 上色(大目标N=color 1 / 小目标N=color 3 / todoN=color 5),「父项」同步;把组内每人当期三层条目写成行(负责人/mentor=人员 open_id、层级/父项带编号、标题原文、**截止日期有则写**、状态=进行中;**闭环五要素/外部成果/友商对比/打分/评语/任务GUID 一律留空**)。
   - **个人对比表**:`feishu_todo_compare_table(app_token, cycle_date=当期日期)`;逐人六项(新开/承接/消失/已闭环/回流/请假顺延)写行;**已闭环当前无台账数据可判 → 填 0 并在待确认注明**;请假顺延按查假结果。
   - **推送**:`feishu_message_send` 把 base 链接私聊发给该 mentor(每组各收各的)。
4. 任何一步失败明说,不静默跳过;不推送 todo 卡、不建飞书任务。
