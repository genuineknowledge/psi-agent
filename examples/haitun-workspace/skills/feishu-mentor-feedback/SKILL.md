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
- `feishu_bitable_list_fields(app_token, table_id)` — get the exact column names before writing
- `feishu_bitable_list_records(app_token, table_id, ...)` — read feedback back
- `feishu_bitable_create_record(app_token, table_id, fields_json)` — record one feedback

## 硬性规矩（务必遵守，否则会静默写错）

1. **一律走 `feishu_bitable_*` 工具，禁止用 `bash`/`powershell` 里的 `urllib`/`requests`
   裸调飞书 REST API 写表。** 裸调绕过了所有列名校验，飞书会把对不上的列静默丢弃
   还返回成功，导致"表里只有姓名列有值，其他列全空"。
2. **写数据前必须先 `feishu_bitable_list_fields` 拿到真实列名**，`fields_json` 的每个 key
   必须与返回的 `field_names` **逐字一致**（含中文、大小写、标点、括号）。
3. **确认写的是哪一张表**：一个 base 里常有多张表，`table_id` 用错就会写到别的表去。
4. 写完检查工具返回：若有 `warning` / `dropped_fields`，说明那几列的值被飞书丢了，
   必须改对列名重写，**不要当成功汇报**。

### ⚠️ 真实翻车案例（引以为戒）

一次填导师反馈表时，用 `bash`+`urllib` 裸调 API，`fields` 的 key 写成了英文
（`Name`/`Mentor`/`SOP_Compliance`/`Score`），而表的真实列名是中文
（`姓名`/`Mentor`/`填写状态`/`SOP合规度`）。飞书对不上的英文列**全部静默丢弃、仍返回
`code:0`**，结果表里前一批记录只有主键姓名落了值，Mentor/填写状态/SOP合规度整列空白，
且脚本打印 `DONE: 22/22` 让人误以为成功。根因就是"裸调 + 列名不匹配 + 飞书静默丢值"。
现在用 `feishu_bitable_create_record` 会先校验列名并拦截，务必用工具、不要裸调。

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

### 飞书默认空行/空列（重要）

飞书新建一张数据表时会**自动带上几个默认空列**（如"文本""单选""附件"占位列）
和**一批默认空行**。这不是工具产生的——`feishu_bitable_create_record` 只追加数据行，
不会碰这些预置内容。若不处理，最终表里会同时出现你的数据 + 一堆飞书预置的空行空列。

所以建表后、写数据前，提醒用户在飞书界面里先清理一次：
- **删空列**：右键预置列的列头 → 删除字段，只保留下表约定的列。
- **删空行**：选中预置空行 → 右键 → 删除行（或先清空默认表再写）。
- 然后**确认剩下的列名与 `fields_json` 的键完全一致**——列名对不上时，飞书不会把值
  填进去（那一列会一直空着），也不报错，容易误以为写成功了。

工具本身不提供删行/删字段能力（YAGNI），清理默认空行空列需在飞书界面手动做一次。

## Recording feedback

When a mentor gives feedback in conversation:

1. Resolve `app_token` (once) and `table_id` (via `feishu_bitable_list_tables`).
2. Get the real column names via `feishu_bitable_list_fields(app_token, table_id)`.
3. Build `fields_json` whose keys exactly match those column names, e.g.:
   ```json
   {"新人":"张三","Mentor":"李四","日期":"2026-07-14","反馈内容":"本周主动承担了两个模块，沟通清晰；下一步多写测试。","评分":4,"标签":"主动性"}
   ```
4. Call `feishu_bitable_create_record(app_token, table_id, fields_json)`.
5. Confirm with the returned `record_id`. If it returns `ok=false`, surface the
   Feishu error (often a permission or column-name mismatch) — don't claim success.
   If it returns a `warning`/`dropped_fields`, those columns were silently dropped —
   fix the column names and rewrite; don't report it as done.

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
