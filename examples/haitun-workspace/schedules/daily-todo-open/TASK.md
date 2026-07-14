---
name: daily-todo-open
cron: "0 0 * * *"
---
# 每日 08:00（北京时间）在主群发起当日 Todo List 话题

> cron 说明：调度引擎按 **UTC** 计时。`0 0 * * *` = UTC 00:00 = 北京时间 08:00。
> 若部署机时区不同，请改这里的 cron。

现在执行以下步骤，全部用工具完成，不要只给计划：

1. **找到主群**：调用 `feishu_chat_find`，群名用 **「在此填写主群名称」**（用户会告知实际群名，请替换这段占位文字）。
   - 若返回多个候选，选名字完全匹配的那个的 `chat_id`；实在无法判断就停下并说明。
   - 若返回 0 个，说明机器人不在该群或群名不对，停下并报告，不要瞎发。

2. **发布话题根消息**：用 `feishu_message_send(receive_id=<上一步的 chat_id>, text=<下面的正文>)`。
   正文包含：今天的日期、这是「当日 Todo List」、**请大家在本条消息下回复各自今天的 todo**、**截止时间为今天中午 12:00**。语气按你的人设（简洁、清楚）。

3. **记录话题信息供 12:00 复核任务使用**：把返回的 `message_id`、`thread_id`、`chat_id` 连同今天日期，用 `write` 工具写入
   `state/daily-todo/<YYYY-MM-DD>.json`（JSON，字段：`date`、`chat_id`、`root_message_id`、`thread_id`）。
   - 目录不存在就先建。这一步很关键：12:00 的复核任务靠它定位今天的话题。

4. 确认根消息已发出（`feishu_message_send` 返回 `ok=true` 且有 `message_id`）后，一句话回报结果即可。
