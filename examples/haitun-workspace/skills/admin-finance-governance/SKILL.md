---
name: admin-finance-governance
description: "The tiered autonomy rulebook for all 行政/财务 (admin & finance) work — 小事不问 / 中事少问 / 大事必问. LOAD this first whenever you audit leave/attendance (假勤), audit reimbursements (报销), or answer/act on admin-finance matters, so every decision uses one consistent 小事(small)/中事(medium)/大事(big) classification, action boundary, and audit-trail rule. Defines default thresholds (editable via skill_manage) for leave-days and reimbursement-amount, what the bot may do at each tier, and how every auto-approval must be logged. The leave/reimbursement/assistant skills all reference this file."
category: knowledge-base
---

# 行政财务分级授权总纲（小事不问 / 中事少问 / 大事必问）

这是海豚处理一切**行政/财务**事务的最高口径。假勤审核、报销审核、真知小助手
问答与执行，都先按本表把事情分到 **小事 / 中事 / 大事** 三档，再决定动手还是问人。

三个下游技能引用本文件：
- [`feishu-leave-audit-board`] — 假勤自动审核 + 看板 + 推送
- [`feishu-reimbursement-audit-report`] — 报销自动审核 + 财务报告 + 附件归档
- [`feishu-admin-finance-assistant`] — 真知小助手（制度问答）

## 三档的核心定义

| 档 | 海豚该做什么 |
|----|-------------|
| **小事** | **直接做**，事后一句话留痕。内部/可逆动作（读、汇总、归档、组织）一律照做；用户已授权的低风险外部动作（本纲判为小事的假勤/报销放行）也直接执行。 |
| **中事** | **只出结论 + 理由，不动手**。给出"建议通过/驳回 + 依据"，等人确认或在飞书里点。 |
| **大事** | **先问用户**。汇总清楚、给出建议，但放行/驳回/对外广播前必须拿到用户明确同意。 |

**保底规则（任何情况下从严）**：涉及「金额放行、对外广播/群发、删除文件、改考勤或审批记录、
触碰他人隐私数据」的动作，**最低从中事起**，小事阈值不适用。

## 默认阈值（可用 `skill_manage` 改本文件调整）

### 假勤（请假/补卡/加班等）
- **小事**：单次 ≤ 1 天，且完全合规（有明确假别、起止清晰、在制度允许范围、无冲突）。
- **中事**：单次 ≤ 3 天，或事假/调休类中等时长。
- **大事**：> 3 天；或病假 / 婚假 / 产假 / 陪产假 / 丧假等**特殊假别**；或材料缺失、
  跨月、疑似冲突、涉及薪资扣减的情况。

### 报销
- **小事**：金额 ≤ ¥500，**且**发票金额 == 申请金额，**且**发票/附件齐全、抬头正确、
  在制度类别与本期范围内。
- **中事**：金额 ≤ ¥2000，且校验基本通过但有需人核对的点。
- **大事**：金额 > ¥2000；或缺票、发票金额与申请金额不符、抬头不对、超类别上限、
  疑似重复报销、跨期。

> 阈值是默认值，不是铁律。用户当次给了不同规则，以用户当次为准；材料不足以判定时，**向上一档**。

## 每次自动放行必须留痕（小事也要）

海豚每做一个"小事直接放行"的动作，必须：
1. 在对应的飞书多维表格台账里写一行（申请人 / 事项 / 关键数值 / 判定档位 / 处置 / 依据 / 时间）。
2. 用一句话说明为什么判为小事、依据哪条阈值。
3. 放行动作若是审批，必须以**真实审批人的 user_id** 通过 `feishu_approval_decide` 执行——
   海豚是"代该审批人操作"，记录落在真人名下（详见下游技能）。

留痕不可省略：没有台账记录的自动放行视为未完成。

## 边界

- 绝不编造数字、假别、金额或制度条款。判不准就往大事走、问用户。
- 财务与个人数据敏感：下载/汇总只落在用户指定位置，结论只发给指定接收人，
  群发广播前按大事确认。
- 本纲只定"该不该做、谁来拍板"；具体怎么拉数据、怎么下附件、怎么出报告，见下游技能。
