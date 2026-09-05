---
name: todo-remind
description: 每周一三五 14:30 TODO 填报提醒——读看板表当期列,对未填写(且无已通过请假)的成员私聊提醒「今天还没填」+ 规范要点;只提醒未写的人,不做任何规范检查,不发报告。
cron: "30 14 * * 1,3,5"
visibility: silent
fire: prompt
---

# TODO 填报提醒(14:30)

## 数据源(定时触发没有对话上下文,参数必须写全)

| 数据源 | 参数 |
|---|---|
| 团队 TODO 看板表 | 链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc —— /wiki/ 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token,再读表;表结构(表头行/人名列/最新日期列)每次现场探,不写死 |
| 请假事实 | `feishu_leave_query`,approval_code=`99EEC396-536A-4C7A-8B2D-412584E35CE3`(只算已通过;审批中/读不出必须单独报告) |

## 流程(只管"写没写",不管"写得规不规范")

1. 换 token 后读表:认表头,定位**最新日期列**(当期列),读人名列。
2. 逐人看当期列:非空 → 跳过,不打扰。
3. 空白 → 先 `feishu_leave_query` 查该人该日是否落在**已通过**请假区间:
   - 请假免填 → 跳过,不提醒;
   - 审批中(skipped_not_approved)/日期读不出(needs_fix)→ 不提醒,但记录;
   - 无请假 → **未写**。
4. 对未写的人 `feishu_message_send` 私聊提醒:文案「<姓名>,今天(<日期>)的 TODO 还没填,记得去 TODO LIST 表填一下」+ 规范要点(三层结构:大目标/小目标/TODO;每条 TODO 要有 deadline;TODO 不超过 5 条;不用过去式)。
5. 消息只发给本人,不提其他人;不发群、不发 boss/mentor 报告。

## 硬顺序与红线

- **查假在下结论之前**:先拿到 空白(人×日期)清单,查完请假,才可以说「未写」。顺序颠倒 = 把休假的人当没写催。
- 表头日期与请假日期比对前先归一成 ISO(表头 `9.2` 这类无年份写法按当年解释;跨年不确定的记「待人工确认」,不提醒)。
- 读表/查假失败要明说查询失败,不得顺势当作未写提醒。
- 本任务**不做结构/规范检查**(那是 15:00 的 todo-writing-check);已填但写得不规范的人,本任务不打扰。
