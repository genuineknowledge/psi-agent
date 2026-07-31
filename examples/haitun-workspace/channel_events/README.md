# channel_events — 触发器事件表（agent 包）

> 交付准则：[`docs/superpowers/specs/2026-07-29-channel-events-developer-guide.md`](../../../docs/superpowers/specs/2026-07-29-channel-events-developer-guide.md)

本目录即事件表。Session **无**业务 event catalog。

**`source` vs `event`**：`source` = 管道品牌（`feishu` / `haitun`…，很少加）；`event` = 管道里的具体事（常加）。一个 source 下很多 event。加信号默认只加 event；只有全新一类生产者才开新 source（并改 Session `KNOWN_SOURCES`）。详见 workspace `AGENTS.md` § Channel events。

---

## 真知管理 SOP → 能否用「触发器」

判定标准：是否存在**可观测信号**（平台推送 / 表变更 / 工具写完状态），命中后用 TRIGGER 做提醒或自动动作。  
纯对话入口、口语办业务、现场问答 → **不是**触发器，走 Channel/skill。  
固定日历（发薪日等）→ 优先 **定时任务**，不是 event。

| SOP | 标题 | 触发器？ | 说明 | 对应 `event` |
|-----|------|--------|------|----------------|
| 1 | 每人 HaiTun 对话入口 | **否** | 接入/路由基建 | — |
| 2 | 飞书各处 @ 办简单事 | **部分** | @对话本身否；To-do 巡检逾期可触发 | `haitun.task.overdue`（已有 `haitun.task.completed`） |
| 3 | HR 员工手册学习确认 | **是** | 入职发卡 + 表单确认 + 校验/重发 | `feishu.hr.user_created` → tool `handbook_onboarding_*`（MVP 双侧 IM；`handbook_confirmed` 合成事件仍预留） |
| 4 | 财务自动化 | **是**（问答部分否） | 假勤汇总、报销审核、报告推送 | `attendance_review_needed` / `expense_submitted` / `report_ready`；问答走对话 |
| 5 | 私聊快捷办假勤报销 | **否** | 用户发起办理 | — |
| 6 | 法律合同审查 | **部分** | 审查请求/交付可触发 | 复用 `haitun.review.requested` / `deliverable.ready` |
| 7 | 问业务负责人 | **否** | 查询对话 | 阻塞上报可复用 `haitun.blocker.raised` |
| 8 | 赋予代答/交接原则 | **部分** | 开启代答可触发通知 | `haitun.handoff.activated`（已有 `handoff.needed`） |
| 9 | 简历审阅填表 | **部分** | 新简历到达可触发流水线 | `haitun.hr.resume_received` |
| 10 | 三阶段台账贯通 | **是** | 阶段切换后改表+提醒 | `haitun.hr.stage_changed`（可衔接 `identity_changed`） |
| 11 | HR 节点提醒易用配置 | **否（配置面）** | NL 配规则 → `schedule_manage` / `trigger_manage`；触发靠 3/10 的 event 或 cron | — |

---

## 自定义事件（`kind: synthetic`，`source: haitun`）

| 稳定 `event` | slug | 主要 SOP | 状态 |
|--------------|------|----------|------|
| `haitun.task.completed` | `task_completed` | 通用 / 2 | 接口；produce 空转 |
| `haitun.task.overdue` | `task_overdue` | 2 | 同上 |
| `haitun.goal.progress` | `goal_progress` | 通用 | 同上 |
| `haitun.handoff.needed` | `handoff_needed` | 7–8 | 同上 |
| `haitun.handoff.activated` | `handoff_activated` | 8 | 同上 |
| `haitun.blocker.raised` | `blocker_raised` | 7 | 同上 |
| `haitun.deliverable.ready` | `deliverable_ready` | 6 | 同上 |
| `haitun.review.requested` | `review_requested` | 6 | 同上 |
| `haitun.hr.handbook_ack_required` | `handbook_ack_required` | 3 | 同上 |
| `haitun.hr.handbook_confirmed` | `handbook_confirmed` | 3 | 同上 |
| `haitun.hr.stage_changed` | `stage_changed` | 10 | 同上 |
| `haitun.hr.resume_received` | `resume_received` | 9 | 同上 |
| `haitun.finance.expense_submitted` | `expense_submitted` | 4 | 同上 |
| `haitun.finance.attendance_review_needed` | `attendance_review_needed` | 4 | 同上 |
| `haitun.finance.report_ready` | `finance_report_ready` | 4 | 同上 |
| `feishu.synthetic.demo_tick` | `demo_tick` | — | 模板 |

---

## 官方映射（`kind: platform_map`）

| 稳定 `event` | slug | `platform_event` | 主要 SOP |
|--------------|------|------------------|----------|
| `feishu.chat.member_added` | `member_added` | `im.chat.member.user.added_v1` | 通用 |
| `feishu.hr.identity_changed` | `identity_changed` | `contact.user.updated_v3` | 10（字段级身份变） |
| `feishu.hr.user_created` | `user_created` | `contact.user.created_v3` | 3 / 10 入职入口 |

---

## 布局与用法

```text
channel_events/feishu/<slug>/{EVENT.yaml, map.py|produce.py}
```

加事件 = 加目录 + **更新本表** + 重启 Channel。挂钩用 `trigger_manage` / skill `feishu-event-remind`。禁止 invent 表外名。
