---
name: feishu-mentor-feedback
description: "Collect and summarize mentor feedback on new hires, stored in a Feishu bitable (多维表格). Use when a mentor gives feedback about a mentee and you should record it, or when asked to summarize/review a person's feedback history. Records go into a Feishu base via the feishu_bitable_* tools (create rows, read rows, list tables); resolve the base app_token from a feishu.cn/base/... link (or a wiki link via feishu_wiki_get_node). Needs the app to have bitable:app scope and be a collaborator on the feedback base."
category: knowledge-base
---

# Feishu Mentor Feedback

Collect mentor feedback on new hires into a **Feishu bitable (多维表格)** and
summarize it on demand. Feedback is durable, structured, and visible to the team
in Feishu — not lost in chat history.

Uses the generic `feishu_bitable_*` tools:
- `feishu_bitable_list_tables(app_token)` — find the `table_id`
- `feishu_bitable_list_records(app_token, table_id, ...)` — read feedback back
- `feishu_bitable_create_record(app_token, table_id, fields_json)` — record one feedback

## Prerequisites

- The user gives a **feedback base** link. Get its `app_token`:
  - `feishu.cn/base/<app_token>` → the `<app_token>` segment is it.
  - `feishu.cn/wiki/<token>` → call `feishu_wiki_get_node(token)`; when `obj_type`
    is `bitable`, its `obj_token` is the `app_token`.
- The app must have the `bitable:app` scope and be added as a **collaborator
  (editor)** on that base, or reads/writes return 403 (error 1254302).

## Suggested table structure

If the user hasn't made a table yet, suggest these columns (they can adapt):

| Column | Type | Notes |
|---|---|---|
| 新人 | 文本 / 人员 | Who the feedback is about |
| Mentor | 文本 | Who gave the feedback |
| 日期 | 日期 / 文本 | When |
| 反馈内容 | 文本 | The feedback itself |
| 评分 | 数字 | Optional 1–5 |
| 标签 | 文本 | Optional theme (沟通/技术/主动性…) |

### 清理飞书新建表的默认空行/空列

飞书新建一张数据表时会自带**默认占位列**（如"文本""单选""附件"）和**若干空行**。
这些不是写记录产生的，若不清理会和你的数据并存。用专门的工具自动清掉，不必手动进飞书界面：

- **删空行**：`feishu_bitable_clear_table(app_token, table_id)` 清掉表里所有行（写数据前先清一次），
  或 `feishu_bitable_delete_records(app_token, table_id, record_ids)` 按 id 删指定行。
- **删空列**：先 `feishu_bitable_list_fields(app_token, table_id)` 拿到各列的 `field_id`（含
  `is_primary` 标记），再 `feishu_bitable_delete_fields(app_token, table_id, field_ids)` 删掉不需要的
  占位列。**主键列（is_primary=true）删不掉**（飞书报 1254046），保留它即可。

建议流程：建表 → `clear_table` 清默认空行 → `list_fields` 看列 → `delete_fields` 删多余占位列
→ 确认剩下列名与 `fields_json` 的键一致 → 再写数据。

## Recording feedback

When a mentor gives feedback in conversation:

1. Resolve `app_token` (once) and `table_id` (via `feishu_bitable_list_tables`).
2. Build `fields_json` matching the table's real column names, e.g.:
   ```json
   {"新人":"张三","Mentor":"李四","日期":"2026-07-14","反馈内容":"本周主动承担了两个模块，沟通清晰；下一步多写测试。","评分":4,"标签":"主动性"}
   ```
3. Call `feishu_bitable_create_record(app_token, table_id, fields_json)`.
4. Confirm with the returned `record_id`. If it returns `ok=false`, surface the
   Feishu error (often a permission or column-name mismatch) — don't claim success.

Notes: column names in `fields_json` must exactly match the table's fields; date
columns often accept a string like `"2026-07-14"` but some bases want a timestamp
— if a write is rejected on the date field, try the other form or drop it.

## Summarizing feedback

When asked to review someone's feedback:

1. `feishu_bitable_list_records(app_token, table_id, page_size=500)`; page through
   `has_more`/`page_token` until you have them all.
2. Filter/group by the 新人 column (either post-filter in your reasoning, or pass a
   `filter` expression / `field_names`).
3. Produce a concise summary: recent feedback, recurring themes, progress, and
   concrete next steps. Keep it specific, not generic praise.
4. Optionally deliver it: `feishu_message_send` (DM the mentee/manager) or
   `feishu_topic_start` (post in a group, @-mention the people).

## Boundaries

- Only record feedback the mentor actually gave — don't invent scores or content.
- Feedback about people is sensitive; deliver summaries only to the intended
  recipients, and confirm before broadcasting to a group.
