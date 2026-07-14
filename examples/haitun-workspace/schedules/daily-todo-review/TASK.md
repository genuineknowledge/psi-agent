---
name: daily-todo-review
cron: "0 4 * * *"
---
# 每日 12:00（北京时间）复核 Todo 话题并逐条评价

> cron 说明：调度引擎按 **UTC** 计时。`0 4 * * *` = UTC 04:00 = 北京时间 12:00。
> 必须晚于 `daily-todo-open`（08:00）。若部署机时区不同，请改这里的 cron。

现在执行以下步骤，全部用工具完成：

1. **定位今天的话题**：用 `read` 读取 `state/daily-todo/<今天 YYYY-MM-DD>.json`，取出 `thread_id` 和 `chat_id`。
   - 读不到（文件不存在）说明今天没开话题，停下并报告，不要凭空造。

2. **读取话题下的全部回复**：调用 `feishu_message_list(container_id=<thread_id>, container_id_type="thread", sort_type="ByCreateTimeAsc")`。
   - 若 `has_more=true`，带 `page_token` 继续翻页，直到取全。
   - 每条回复里识别发送者与其 todo 文本（跳过机器人自己发的根消息）。

3. **逐条评价**：对每个成员的 todo，生成一条简洁、具体、可执行的评价（按你的人设，不空话套话）。

4. **把评价发送出去**：
   - 默认在同一话题里回复：`feishu_message_reply(message_id=<该成员那条回复的 message_id>, text=<评价，可用 <at user_id="ou_xxx"></at> @ 到本人>, reply_in_thread=true)`。
   - **指定人员**：若用户指定了要额外汇总发送给某人（open_id 见 `TOOLS.md` 或用户指令），把整体评价汇总后用 `feishu_message_send(receive_id=<open_id>, receive_id_type="open_id", text=<汇总>)` 私信发送。

5. 全部发送成功后，一句话回报「今天复核了 N 条 todo」。
