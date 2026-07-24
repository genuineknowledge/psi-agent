---
name: feishu-self-service-agent
description: "员工自助办事 — 员工在飞书私聊 HaiTun 说出业务需求（请假/报销/补卡/加班/用章等）并提供信息，HaiTun 按公司 SOP 标准格式代员工把审批单提交上去。Use when someone DMs the bot asking to file/apply for something (帮我请假/报销/提交申请). Loads admin-finance-governance for tiering, finds the right approval_code and reads its form template with feishu_approval_get_definition, maps the employee's words onto the real form fields (asks for anything missing, never invents), then per governance shows the filled standard form for the applicant to confirm before feishu_approval_create submits it under the applicant's own open_id (sender_open_id). Complements the audit-side skills (feishu-leave-audit-board / feishu-reimbursement-audit-report). Needs approval:* scopes + the app authorized on the approval definition."
category: productivity
---

# 员工自助办事（代员工按 SOP 提交审批）

员工私聊 HaiTun 说清要办什么（请假、报销、补卡、加班、用章等）并给出信息，
HaiTun 查该业务的 SOP 与标准表单，把员工的口语补齐成合规表单，**代员工本人**提交审批。
这是审批的**发起端**，与审核端技能（[`feishu-leave-audit-board`] / [`feishu-reimbursement-audit-report`]）互补。
**分级口径以 [`admin-finance-governance`] 为准**——先加载那份总纲，再按本技能执行。

用到的现成工具：
- `feishu_approval_get_definition(approval_code)` — 读该审批的表单模板（要填哪些字段、类型、是否必填）
- `feishu_approval_create(approval_code, form_json, applicant_open_id, title, ...)` — 代员工提交申请
- `feishu_approval_list_tasks(user_id)` / `feishu_approval_get(instance_id)` — 取样本 / 查提交后状态
- 制度依据用 [`feishu-admin-finance-assistant`]（真知小助手）查 SOP 标准，**带出处**
- 需要时 `feishu_message_send` 把回执发给员工；`feishu_bitable_*` 写自助办事台账留痕

## 申请人身份：以员工本人提交

提交的单子**记在员工本人名下**：把私聊消息 `<feishu_context>` 里的 `sender_open_id`
作为 `applicant_open_id` 传给 `feishu_approval_create` 即可。用机器人的 tenant token 提交，
**不需要员工单独授权**（区别于文档搜索的 UAT 授权码流，也区别于审核端 `decide` 要真实审批人 user_id）。
绝不以海豚自己名义发起员工的申请。

## 每次办事的流程

1. **识别意图**：从私聊里判断员工要办什么业务（哪种审批）。拿不准就问清是哪类事项。
2. **定位审批 + 读模板**：确定该业务的 `approval_code`（从飞书审批后台，或 `feishu_approval_list_tasks`
   取一条同类样本），`feishu_approval_get_definition(approval_code)` 读回表单模板：
   逐个字段的 `id` / `name` / `type` / `required`。**填单只能用模板里真实存在的字段 id，绝不编造字段。**
3. **查 SOP 标准**：涉及标准/上限/材料要求（假别规则、报销类别与上限、抬头、单据要求等），
   用真知小助手查制度**带出处**核对；SOP 说不清的地方往上一档、问员工或问 HR，不臆造条款。
4. **把口语补齐成合规表单**：把员工给的信息映射到各字段。**缺必填字段或信息不足就逐项问员工**
   （如假别、起止时间、天数、金额、事由、发票/附件），问齐再往下，**绝不替员工编内容**。
5. **分级 + 提交前确认**：提交审批是**对外/涉人**动作，按 governance 保底规则**最低从中事起**：
   - 把拼好的**标准表单逐字段回显给员工**（approval_name + 每个字段填了什么），
     明确说"确认无误我就提交"，**等员工明确点头**。
   - 大事（如长假、大额报销、特殊假别、材料存疑）：确认时把风险点一并说清，仍需员工明确同意。
   - 员工要改就改完再回显，直到确认。
6. **提交**：`feishu_approval_create(approval_code, form_json=<拼好的[{id,type,value}]数组>,
   applicant_open_id=<sender_open_id>, title=<可选>)`。
   若审批流需发起人指定审批人，用 `node_approver_open_id_list_json` 传 `[{"key":node_id,"value":[open_id]}]`。
7. **回执 + 留痕**：拿到 `instance_code` 后告诉员工"已提交，单号 xxx，去飞书审批里可查/撤回"。
   若返回 `ok=false`，**如实**把飞书错误告诉员工（多为权限/审批未授权/字段不符），**不谎报成功**。
   在自助办事台账写一行（申请人 / 事项 / 关键数值 / 判定档位 / 单号 / 时间）留痕。

## 边界

- 绝不编造字段、假别、金额、事由或制度条款；信息不足就问，判不准就往大事走、交人拍板。
- 未经员工确认不提交；提交失败如实回报，绝不谎报成功。
- 单子必须记在员工本人名下（`applicant_open_id`=本人 open_id），海豚从不以自己名义替人发起。
- 财务/个人信息敏感：回执与台账只发给本人及应知悉者，不群发。
- 本技能只负责**发起提交**；单子的审核/放行由审核端技能与真实审批人处理。
