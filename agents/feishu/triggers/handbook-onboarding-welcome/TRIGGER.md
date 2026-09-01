---
name: handbook-onboarding-welcome
description: 通讯录新建员工时，向新员工私聊发送管理制度确认卡
event: feishu.hr.user_created
source: feishu
filter: {}
visibility: silent
run_once: false
fire: tool
raw_event: contact.user.created_v3
tool: handbook_onboarding_send_welcome
tool_args: {}
---

向 payload.open_id 发送欢迎 + 管理制度链接 + 确认表单卡。
open_id / name 由 Session 注入 event_payload_json，无需写死 tool_args。
