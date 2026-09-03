---
name: todo-writing-check
description: 每周一三五 14:50 TODO 填报规范检查。触发时加载 todo-writing-standard 技能,按其规则集段与「检查(定时触发)」节执行;违规项私聊本人只报缺项;报告去向以本任务为准(不出汇总视图)。
cron: "50 14 * * 1,3,5"
visibility: silent
fire: prompt
---

# TODO 填报规范检查(14:50)

## 数据源(定时触发没有对话上下文,参数必须写全)

| 数据源 | 参数 |
|---|---|
| 团队 TODO 看板表 | 链接 https://genuineknowledge.feishu.cn/wiki/H6icwLWn1iwpXAk73QMcA6MgnWc —— /wiki/ 链接先 `feishu_api` GET /open-apis/wiki/v2/spaces/get_node 换 obj_token,再读表;表结构(表头行/人名列/mentor 列/最新日期列)每次现场探,不写死 |
| 请假事实 | `feishu_leave_query`,approval_code=`99EEC396-536A-4C7A-8B2D-412584E35CE3`(只算已通过;审批中/读不出必须单独报告) |

## 判定口径(唯一来源:todo-writing-standard 技能)

1. **先加载技能**:`skill_manage(action="view", skill_name="todo-writing-standard")` 读技能全文。
2. 按技能的**规则集段**逐项判定(按时/按质/按量),检查流程按技能「检查(定时触发)」节执行——判定口径以技能为准,不自行增减规则,不凭本文推断。
3. **mentor check 例外**:看板表表头没有 mentor check 标记列时,该项跳过——不判定、不报告、不提醒 mentor(列补上后按技能正常检查)。
4. **报告去向以本任务为准**:违规项私聊本人只报缺项(不复述规范全文,不提其他人);私聊文案只含缺项本身,不附任何判定过程说明(跳过项/无法判定项一律不写);不出技能的 boss/mentor 汇总视图。

## 红线

- 查假在下结论之前;数不准、日期对不上、表结构没读明白 → 归「待人工确认」,不塞进违规名单凑数。
- 读表/查假/读技能失败要明说查询失败,不得顺势判违规。
- 本任务只管「填没填、写得规不规范」;真实性判定归 16:00 的 todo-check,不越界。
- 私聊发送一律用 `feishu_message_send`(定时触发回合没有会话可回,不调用就是没发)。
