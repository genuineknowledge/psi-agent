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
| 台账文件夹 | `folder_token` 从 `config/todo-sop.yaml` 的 `ledger_schema.folder_token` 读(**唯一来源,不要现场找、不要猜**)。值为空 → 本任务报错停止,明说「folder_token 未配置」,**禁止考古/试错** |
| 写身份 | 全部写操作 `identity="bot"`(tenant token;定时回合无 user_key,不传 user 身份)。授权失败(如 bot 读不了看板)照实报,不换身份硬试 |

## 工具调用预算(40 轮硬上限,必须按此规划)

单轮调用预算:前置 ~6 次(加载技能 1 + 读表 2 + 查假 1 + 读 config 1 + 解析 mentor 名单 1);每组 **3 次**(ensure 1 + cycle_push 1 + 推送 1)。9 组 ≈ 33 次,预算内。**禁止**每组再用「建表 + 逐行写」的老打法(9 组 × 10+ 次 = 撞上限,9/7 已实测)。行内容全部先判好,一次批量写。

## 流程

1. 加载 company-todo-audit 技能,六项口径以该技能「前后对比摘要」节为准。
2. 读 config 拿 `ledger_schema.folder_token`;读看板:当期列 + 上期列、人名列、mentor 列;按 mentor 分组。
3. 每组(每个 mentor),**3 次调用**:
   - **ensure**(1 次):`feishu_mentor_ledger_ensure(mentor_open_id=该 mentor, mentor_name=该 mentor 姓名, folder_token=config 值, identity="bot")` → 拿 app_token。已存在则直接复用。
   - **cycle_push**(1 次):`feishu_mentor_ledger_cycle_push(app_token=..., cycle_date=当期日期, ledger_rows=..., compare_rows=..., mentor_open_id=该 mentor, identity="bot")`,一次完成两张表 + 两批行:
     - `ledger_rows`:组内每人当期三层条目(字段:周期日期/负责人/mentor/层级/父项/标题/截止日期/状态)。层级带编号(大目标N/小目标N/todoN)、父项同步、标题原文、**截止日期有则写**、状态=进行中;**闭环五要素/外部成果/友商对比/打分/评语/任务GUID 一律留空**。层级字段 options 上色(大目标N=color 1 / 小目标N=color 3 / todoN=color 5)沿用建库时的口径,本工具不重复改 options;
     - `compare_rows`:逐人六项(新开/承接/消失/已闭环/回流/请假顺延)+ 结论/待确认。**已闭环当前无台账数据可判 → 填 0 并在待确认注明**;请假顺延按查假结果。
   - **推送**(1 次):`feishu_message_send` 把 base 链接私聊发给该 mentor(每组各收各的)。ensure 返回的 `granted.mentor` 不是 ok 时,推送文案附一句「如打不开链接,请向管理员申请该台账权限」,不重试 grant。
4. 任何一步失败明说,不静默跳过;不推送 todo 卡、不建飞书任务。
