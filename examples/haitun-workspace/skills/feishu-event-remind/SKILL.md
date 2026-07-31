---
name: feishu-event-remind
description: "Feishu event triggers (触发器): call trigger_manage for events already in agent channel_events/feishu. Use fire=tool + feishu_message_send. REQUIRED before 有人进群提醒我 / 人事身份转变提醒. Not for time-based (use feishu-schedule-message)."
category: knowledge-base
---

# 飞书触发器（事件触发）

## 概念（先分清）

| 名字 | 是什么 | 不是什么 |
|------|--------|----------|
| **触发器** | 产品名（对标 `trigger` / `trigger_manage`）；用户也可说「触发事件」 | 勿称「定事」（已弃用）；不是定时任务 |
| **`channel_events/`** | agent 包里 **Channel 事件定义**（生产者）；加事件 ≈ 加 tool | 不是 TRIGGER；不在 Session catalog |
| **`trigger_manage`** | 写 `triggers/*/TRIGGER.md`（挂钩） | 不会自己发飞书；也不 invent 新事件类型 |
| **`event`** | `channel_events` 公布的稳定名（如 `feishu.chat.member_added`） | 不是散文条件 |
| **`raw_event`** | 平台原生类型（回退匹配） | 不是登记新能力 |
| **`fire=tool`** | 命中后 Session 直调工具 | 到事时 LLM 不参与 |

## When to use

- 「有人进群提醒我」「人事身份变了」「新员工手册待确认」「三阶段转正」「报销待审」「To-do 逾期」等 **channel_events 已接通** 的事（真知 SOP 对照见 `channel_events/README.md`）

## When not to use

- 定时（发薪日等）→ `feishu-schedule-message` / `schedule_manage`
- 纯对话入口、口语办业务、现场问答（SOP-1/5/7 主路径）
- **尚未**在 `channel_events/feishu/` 定义的事 → 告诉用户暂不支持（不要 invent）

## 自然语言 → 字段

| 用户说法 | `event` | `raw_event` |
|----------|---------|-------------|
| 有人进群 | `feishu.chat.member_added` | `im.chat.member.user.added_v1` |
| 新员工入职（通讯录新建） | `feishu.hr.user_created` | `contact.user.created_v3` |
| 人事身份字段变了 | `feishu.hr.identity_changed` | `contact.user.updated_v3` |
| 手册待确认 / 已确认 | `haitun.hr.handbook_ack_required` / `handbook_confirmed` | — |
| 试用↔转正等阶段切换 | `haitun.hr.stage_changed` | — |
| 收到新简历 | `haitun.hr.resume_received` | — |
| 报销提交待审 | `haitun.finance.expense_submitted` | — |
| 假勤要审/汇总 | `haitun.finance.attendance_review_needed` | — |
| 财务报告好了 | `haitun.finance.report_ready` | — |
| 任务完成 / 逾期 | `haitun.task.completed` / `overdue` | — |
| 开启代答原则 | `haitun.handoff.activated` | — |
| 需要交接 / 阻塞找人 / 交付物 / 审查 | `handoff.needed` / `blocker.raised` / `deliverable.ready` / `review.requested` | — |

完整表与 SOP 判定见 `channel_events/README.md`。

## Procedure

```text
trigger_manage(
  action="create",
  trigger_name="group-welcome-…",
  event="feishu.chat.member_added",
  raw_event="im.chat.member.user.added_v1",
  filter='{"chat_id":"oc_真实群id"}',
  fire="tool",
  tool="feishu_message_send",
  tool_args='{"receive_id":"oc_…","text":"有新人进群了","receive_id_type":"chat_id"}',
  visibility="silent",
  description="新人进群提醒"
)
```

人事身份转变示例（可按 `changed_fields` / `open_id` 收窄 filter）：

```text
trigger_manage(
  action="create",
  trigger_name="hr-identity-…",
  event="feishu.hr.identity_changed",
  raw_event="contact.user.updated_v3",
  filter='{}',
  fire="tool",
  tool="feishu_message_send",
  tool_args='{"receive_id":"ou_人事负责人","text":"有人身份信息变更","receive_id_type":"open_id"}',
  visibility="silent",
  description="人事身份转变提醒"
)
```

**新员工管理制度确认卡**（动态 `open_id`，不要写死 tool_args）：agent 包已有触发器
`handbook-onboarding-welcome` + 工具 `handbook_onboarding_send_welcome`（Session 注入
`event_payload_json`）。卡片提交走 skill `feishu-handbook-onboarding` →
`handbook_onboarding_process_submit`。联调前改 `config/handbook_onboarding.yaml` 链接，并设
`HAITUN_HANDBOOK_HR_NOTIFY_ID`（或 yaml 里 `hr_notify_id`）。

## Boundaries

- 禁止手写 TRIGGER；禁止为未接通事件 invent 名
- 飞书 IM 提醒必须 `fire=tool` + 真实 receive_id（入职确认卡例外：用 `handbook_onboarding_*`，由事件 payload 解析收件人）
