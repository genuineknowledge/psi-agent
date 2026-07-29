---
name: feishu-leave-audit-board
description: "Auto-audit Feishu leave/attendance (假勤) approval applications against tiered conditions, then build a dashboard (看板) and push it to the right people. Use when asked to review/approve leave, 补卡, or attendance approvals and report results. Enumerates applications with feishu_approval_list_instances, reads each with feishu_approval_get, classifies each as 小事/中事/大事 per admin-finance-governance (小事 auto-approve via feishu_approval_decide, 中事 recommend only, 大事 ask the user), logs every action to a bitable, writes a summary doc with feishu_doc_create, and pushes it via feishu_message_send / feishu_topic_start. Needs approval:* scopes and the app authorized on the leave approval definition."
category: productivity
---

# 假勤自动审核 + 看板 + 推送

按条件自动审核假勤审批，把结果汇成一张看板，推送给相关人（HR / 主管 / 申请人）。
**分级口径以 [`admin-finance-governance`] 为准**——先加载那份总纲，再按本技能执行。

用到的现成工具：
- `feishu_approval_list_instances(approval_code, start_time, end_time)` — 列本期假勤实例
- `feishu_approval_get(instance_id)` — 读一条申请（申请人/假别/起止/天数/表单/task_list）
- `feishu_approval_decide(approve, approval_code, instance_code, approver_user_id, task_id, comment)` — 代真实审批人放行/驳回
- `feishu_department_members(...)` / `feishu_chat_find*` — 解析人和群
- `feishu_bitable_*` — 写审核台账（留痕）
- `feishu_doc_create` / `feishu_doc_append_content` — 生成看板文档
- `feishu_message_send` / `feishu_topic_start` — 推送看板

## 每次运行先向用户确认

1. **哪个审批** — 假勤的 `approval_code`（从飞书审批后台，或 `feishu_approval_list_tasks` 取一条样本）。
2. **周期** — start/end（Unix 毫秒串，或默认近 30 天）。
3. **代谁审批** — 小事自动放行时要用的**真实审批人 user_id**（必须是当前任务的审批人本人）。
4. **看板推给谁** — HR/主管的 open_id 或目标群 chat_id。
5. **本次是否覆盖默认阈值** — 不覆盖就用 governance 默认。

缺 1/3 就问，别猜；代人审批的 user_id 拿不到就不能自动放行，降级为"只建议"。

## 分级判定（默认阈值见 governance，可被用户当次规则覆盖）

对每条申请，从表单里取**假别、起止、天数、材料完整性**，判档：
- **小事**：≤1 天且完全合规 → 直接 `feishu_approval_decide(approve=True, ...)` 通过，写台账留痕。
- **中事**：≤3 天 / 中等事假调休 → **只在看板里写"建议通过/驳回 + 理由"**，不调 decide。
- **大事**：>3 天，或病/婚/产/陪产/丧等特殊假，或材料缺失/跨月/疑似冲突 → 汇总后**必问用户**，
  得到明确同意才 decide。

判不准、材料不足 → 往大事走。

## 流程

1. `feishu_approval_list_instances(approval_code, start, end)` → 所有 `instance_code`。
2. 逐条 `feishu_approval_get(code)`：取申请人、假别、起止、天数、状态、`task_list`（拿 `task_id`）。
3. 判档：
   - 小事：`feishu_approval_decide(approve=True, approval_code, instance_code=code, approver_user_id=<真实审批人>, task_id=<该条 task_id>, comment="自动审核-合规-小事")`。
     若返回 `ok=false`，如实记录飞书错误（多为权限/审批人不符），**不要谎报成功**，降级为"建议"。
   - 中事：不动手，记"建议通过/驳回 + 依据"。
   - 大事：不动手，标"待用户确认"，附建议。
4. **留痕台账**（每条都写，小事更要写）：`feishu_bitable_create_record`，列建议：
   `申请人 / 假别 / 起止 / 天数 / 判定档位 / 处置(已通过/已驳回/建议X/待确认) / 依据 / 时间`。
   首次用表先 `feishu_bitable_list_tables` 找 table_id；需要清默认空行/列时用 `clear_table`/`delete_fields`。
5. **看板文档**：`feishu_doc_create` 建"假勤审核看板-<周期>"，`feishu_doc_append_content` 写入：
   - 概览：总条数、已自动通过数、建议数、待确认(大事)数；
   - 按**人 / 假别 / 状态**聚合的小表；
   - 大事清单（逐条列申请人+假别+天数+为什么要人拍板）。
6. **推送**：把看板文档链接 `feishu_message_send` 私聊 HR/主管；或 `feishu_topic_start` 发群并 @相关人
   （@ 用 `feishu_chat_find_member` 拿 open_id）。**群发广播按大事，先跟用户确认**再发。

## 边界

- 绝不编造天数/假别；`feishu_attendance_query` 里 `invalid_user_ids`/`unauthorized_user_ids` 标"无数据"，
  不当作正常出勤。
- 代人审批必须用真实审批人 user_id，动作记在真人名下；海豚从不"以自己名义"批假。
- 本技能只读审批+按分级放行，从不改考勤原始打卡记录。
- 每一次自动放行都必须有台账行 + 一句依据，否则视为未完成（见 governance 留痕规则）。
