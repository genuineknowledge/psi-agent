---
name: todo-ledger-push
description: 每周一三五 16:00 动态台账推送(合并原 15:10 mentor 提醒):对每 mentor 组生成上期 vs 本期前后对比文本表格(新开/承接/搞定/进行中/请假顺延),附对齐存疑清单与看板表链接,一条消息私聊发对应 mentor。不建多维表格、不写 base。
cron: "0 16 * * 1,3,5"
visibility: silent
fire: prompt
---

# 动态台账推送(16:00,已合并 15:10 mentor 提醒)

## 数据源(定时触发没有对话上下文,参数必须写全)

| 数据源 | 参数 |
|---|---|
| 对比口径(唯一来源) | `skill_manage(action="view", skill_name="company-todo-audit")` 的「前后对比摘要(上期 vs 本期,逐人)」节——新开/承接/消失/已闭环/回流/请假顺延口径以技能为准,不自行增减 |
| 团队 TODO 看板表 | 链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc —— /wiki/ 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token,再读表;表结构(表头行/人名列/mentor 列/最新日期列)每次现场探,不写死。**本期列=最新日期列,上期列=紧邻上一列** |
| 请假事实 | `feishu_leave_query`,approval_code=`99EEC396-536A-4C7A-8B2D-412584E35CE3`(只算已通过) |
| 对齐存疑清单 | workspace 根目录文件 `align-pending.txt`(15:00 检测落盘,每行:姓名|期次|缺什么依据)。**存在则附进对应组的消息;不存在则跳过,不报错** |

## 硬约束(已定口径)

- **不建多维表格、不写 base、不调用 feishu_mentor_ledger_ensure / feishu_mentor_ledger_cycle_table / feishu_todo_compare_table / feishu_mentor_ledger_cycle_push 任何台账工具**——对比结果以**消息内的文本表格**呈现,人在消息里直接看;
- 一条消息发对应 mentor:原 15:10 的检查提醒已并入本任务,不再单独提醒;
- 字段不照搬台账 schema:文本表格**不用 mentor 字段**(组名在消息标题体现),只保留人 + 前后两日条目 + 搞定情况。

## 流程

1. 加载 company-todo-audit 技能,前后对比口径以该技能「前后对比摘要」节为准。
2. 读看板:当期列 + 上期列、人名列、mentor 列;按 mentor 分组。
3. 每组(每个 mentor)逐人对比,判每条 TODO 的**连续性**:
   - **搞定**:上期有、本期消失 → 推断已完成(拿不准写「待确认」);
   - **承接**:两期都在(标题延续)→ 进行中;
   - **新开**:本期新增;
   - **请假顺延**:按查假结果;
   - **回流**:按技能口径。
4. 生成**文本表格**(markdown,直接在消息里),按人逐行:

   ```markdown
   | 成员 | 上期(前一日) | 本期(今日) | 搞定情况 |
   |---|---|---|---|
   | 张三 | 1. xxx(9.4) 2. yyy | 1. xxx 2. zzz | yyy → 已搞定;zzz 新开;xxx 进行中 |
   ```

   条目写标题简写(太长截断),搞定情况写清「搞定/进行中/新开/消失待确认/请假顺延」;同日多期时按紧邻两期对比(前后两天)。
5. 组装消息,**一条发对应 mentor**:
   - 开头:检查提醒(「请检查你手下成员的当期填报是否合理」,一句,不复述规范全文);
   - 正文:文本表格 + 该组对齐存疑清单(align-pending.txt 里该组人员的行,原样附上;无则跳过);
   - 结尾:看板表链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc(方便 mentor 直接检查)。
6. `feishu_message_send` 私聊发送(定时触发回合没有会话可回,不调用就是没发);任何一步失败明说,不静默跳过。
