---
name: feishu-event-remind
description: "Feishu event reminders (定事): call trigger_manage for events already in agent channel_events/feishu. Use fire=tool + feishu_message_send. REQUIRED before 有人进群提醒我. Not for time-based (use feishu-schedule-message)."
category: knowledge-base
---

# 飞书事件提醒（定事）

## 概念（先分清）

| 名字 | 是什么 | 不是什么 |
|------|--------|----------|
| **`channel_events/`** | agent 包里 **Channel 事件定义**（生产者）；加事件 ≈ 加 tool | 不是 TRIGGER；不在 Session catalog |
| **`trigger_manage`** | 写 `triggers/*/TRIGGER.md`（挂钩） | 不会自己发飞书；也不 invent 新事件类型 |
| **`event`** | `channel_events` 公布的稳定名（如 `feishu.chat.member_added`） | 不是散文条件 |
| **`raw_event`** | 平台原生类型（回退匹配） | 不是登记新能力 |
| **`fire=tool`** | 命中后 Session 直调工具 | 到事时 LLM 不参与 |

## When to use

- 「有人进群提醒我」等 **channel_events 已接通** 的事

## When not to use

- 定时（`feishu-schedule-message`）
- **尚未**在 `channel_events/feishu/` 定义的事 → 告诉用户暂不支持（不要 invent）

## 自然语言 → 字段

| 用户说法 | `event` | `raw_event`（可省略自动补） |
|----------|---------|---------------------------|
| 有人进群 | `feishu.chat.member_added` | `im.chat.member.user.added_v1` |

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

## Boundaries

- 禁止手写 TRIGGER；禁止为未接通事件 invent 名
- 飞书 IM 提醒必须 `fire=tool` + 真实 receive_id
