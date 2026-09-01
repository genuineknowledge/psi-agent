---
name: feishu-schedule-message
description: "Feishu timed reminders: call schedule_manage (never hand-write TASK.md). Use fire=tool + tool=feishu_message_send + tool_args JSON so Session invokes the send tool at fire time with no LLM. REQUIRED before 提醒我/定时发消息. Real chat_id/open_id from <feishu_context>. Prefer visibility=silent."
category: knowledge-base
---

# 飞书定时消息提醒

## 概念（先分清）

| 名字 | 是什么 | 不是什么 |
|------|--------|----------|
| **`schedule_manage`** | workspace **工具**：create/list/view/patch/delete 定时任务 | 不会往飞书发消息 |
| **`fire`** | 写进 TASK YAML 的**触发模式**字段：`prompt` 或 `tool` | **不是**一个可调用的 tool |
| **`fire=tool`** | 到点由 **Session 框架**直接 `ToolRegistry.get(tool)(**tool_args)` | 不是「调用 tool 的 tool」；LLM 到点根本不参与 |
| **`tool` / `tool_args`** | YAML 里记录「到点调哪个工具、传什么参数」 | 对话这一轮不要自己调 `feishu_message_send` 当提醒 |
| **`feishu_message_send`** | 真正发飞书 IM 的工具 | 由 Session 在**到点**调用，不是设立提醒时调用 |

磁盘上仍有 `schedules/<name>/TASK.md`，但那是 **`schedule_manage` 写出来的**。Agent **禁止**用 `write`/`edit` 手写 TASK.md。

## When to use

- 「到点提醒我…」「每天/每月提醒我…」「到时候给这个群发一条…」

## When not to use

- heartbeat / 静默保活（现成 `schedules/heartbeat`）
- 不需要飞书推送、只要下次对话里看见（那不是本 skill）

## Hard rules

1. 先 `read` 本文件，再调 **`schedule_manage`**（唯一设立入口）。
2. **一次 create 就要完整**：`fire="tool"` + `tool="feishu_message_send"` + `tool_args` JSON（真实 id / 文案）写在**同一调用**里。禁止先 `fire=prompt` / 把调用写进 `content` 再 patch；工具会对 `once_at` 直接拒掉缺 `fire=tool` 的创建。
3. `receive_id` 来自 `<feishu_context>` 的 `chat_id`（或指定 `open_id`）；**禁止** Gateway `session_id`（`feishu-ou_…`）。
4. 单次用 `once_at`（本机墙钟）；周期用 `cron`；二者不要同时传。
5. 飞书提醒建议 `visibility="silent"`。
6. Gateway/Session 进程必须已设 `PSI_FEISHU_APP_ID` / `PSI_FEISHU_APP_SECRET`。
7. 成功后一句话确认即可；**不要**为了「复查 fire 模式」再 delete/recreate（除非用户改时间）。

## Procedure（对话这一轮只调 schedule_manage）

```text
schedule_manage(
  action="create",
  schedule_name="remind-leave-0725",
  once_at="2026-07-25 10:50",
  fire="tool",
  tool="feishu_message_send",
  tool_args='{"receive_id":"oc_真实chat_id","text":"到点啦，该下班了！","receive_id_type":"chat_id"}',
  visibility="silent",
  description="提醒下班"
)
```

周期：把 `once_at` 换成 `cron="0 10 15 * *"`，其余相同。`content` 可选（备注）；**`fire=tool` 时正文不参与执行**。

成功后一句话确认：名称、时刻、发往哪个 id、`fire=tool`。可用 `action=view` 自检 YAML。

## 到点之后（框架，不是 LLM）

Session 读到 `fire: tool` → 直接调用 `feishu_message_send(**tool_args)` → 飞书推送；`run_once` 则删掉该 TASK。

## 取消

- 未触发：`schedule_manage(action="delete", schedule_name="…")`
- `run_once` 已触发：TASK 会被 Session 删掉

## Boundaries

- 禁止手写 / 手改 `schedules/*/TASK.md`（一律 `schedule_manage`）
- 禁止 `fire=prompt` 做飞书 IM 提醒
- 禁止把 `fire` 当成工具名去调
- 禁止占位 `oc_xxx` / 空 `tool_args`
- 禁止 create 同时传 `cron` 与 `once_at`
