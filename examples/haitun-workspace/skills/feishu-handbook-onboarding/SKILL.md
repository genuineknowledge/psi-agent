---
name: feishu-handbook-onboarding
description: "Use when a new hire joins (feishu.hr.user_created), when sending or re-sending the management-handbook confirm card, or when handling a <feishu_card_action> with handler handbook_onboarding_process_submit / action handbook_submit. Covers welcome + doc links + form validation + notify both sides / resend card."
category: productivity
agent_editable: true
---

# 入职管理制度确认（卡片闭环）

新员工入职后：发欢迎 + 管理制度链接 + **确认表单卡** → 对方提交 → **校验** → 通过则通知本人与 HR；失败则说明原因并 **再发一张新卡**（旧卡点击后已失效，不能改原卡重填）。

## When to use

- 通讯录新建员工（`feishu.hr.user_created`）或用户要求「给某人发入职手册确认卡」。
- 收到 `<feishu_card_action>`，且 `dispatch.handler` 为 `handbook_onboarding_process_submit`（或 `action.value.action` = `handbook_submit`）。

## When not to use

- 普通群提醒、与手册确认无关的审批卡。
- 试图轮询文档勾选框代替确认卡（不要做）。

## Instructions

### 发卡（欢迎 / 失败重发）

1. 优先调用 `handbook_onboarding_send_welcome`。
2. 触发器场景：参数可留空，靠 Session 注入的 `event_payload_json`（含 `open_id` / `name`）。
3. 手工联调：传入 `open_id`（必填）与可选 `name`。
4. 链接与校验规则在 agent 包 `config/handbook_onboarding.yaml`；HR 通知目标可用环境变量 `HAITUN_HANDBOOK_HR_NOTIFY_ID`（及可选 `HAITUN_HANDBOOK_HR_NOTIFY_ID_TYPE`）覆盖。
5. 工具成功后卡片已对用户可见：本轮 **零 assistant 文本**（不要说「卡片已发送」）。

### 处理确认提交

1. 解析 `<feishu_card_action>` 整段 JSON。
2. 立即调用 `handbook_onboarding_process_submit(card_action_json=<整段 JSON 字符串>)`，不要先复述「你点击了…」。
3. 工具内已完成校验与通知 / 重发卡：
   - `passed=true` → 零文本结束（通知已由工具发出）。
   - `passed=false` 且 `resent_card=true` → 零文本结束（失败原因已在新卡上）。
   - `ok=false` 且带 `error` → 仅回复必要错误，勿谎称成功。
4. **不要**手写第二张卡或改用散装 `feishu_message_send_card`，除非本工具明确失败且用户要求兜底。

### 配置提示（联调）

- 把 `handbook_links` 换成真实管理制度 URL。
- 设置 `hr_notify_id` 或环境变量，否则通过后只通知新员工、不通知 HR。
- 飞书后台需订阅 `contact.user.created_v3`；Channel/Gateway 进程带同一组 `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`。

## 相关事件

| 信号 | 用途 |
|------|------|
| `feishu.hr.user_created` | 入职入口 → 触发器调 `handbook_onboarding_send_welcome` |
| `haitun.hr.handbook_confirmed` | 合成接口预留；本 MVP 以双侧消息通知为准，不依赖该事件才闭环 |
