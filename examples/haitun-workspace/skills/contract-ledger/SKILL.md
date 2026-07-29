---
name: contract-ledger
description: "Build and maintain the company contract ledger (合同台账) in a Feishu bitable — one row per contract with 编号/名称/对方主体/类型/金额/签署日/生效日/到期日/状态/负责人/原件链接/审批单号. Use when asked to set up a 合同台账, register/log a contract, update a contract's status, find contracts, or produce an expiry (到期) reminder/summary. Resolves table_id with feishu_bitable_list_tables, checks for an existing row (dedup by 合同编号) with feishu_bitable_list_records before feishu_bitable_create_record, keeps 原件链接 pointing at the Feishu doc/drive source, and drives expiry alerts via schedule_manage + feishu_message_send. Grades every write per contract-legal-governance (小事 add/append, 中事 change key fields, 大事 delete). Needs bitable:app scope + the app as a collaborator on the base."
category: productivity
---

# 公司合同台账建立与管理

在飞书多维表格（bitable）里建立并维护**公司合同台账**：一行一份合同，
支持登记新合同、更新状态、检索、按到期日提醒。台账是合同全生命周期的**唯一事实来源**，
也是审查结论与审批单号的落点。
**分级口径以 [`contract-legal-governance`] 为准**——先加载那份总纲，再按本技能执行。

用到的现成工具：
- `feishu_bitable_list_tables(app_token)` — 拿 `table_id`（首次用表必做）
- `feishu_bitable_list_fields` / `feishu_bitable_delete_fields` — 看/删列（清飞书默认占位列）
- `feishu_bitable_list_records(app_token, table_id, filter=...)` — 检索 / 防重查已有行
- `feishu_bitable_create_record(app_token, table_id, fields_json)` — 追加一行合同
- `feishu_bitable_clear_table` — 清默认空行（新表首次）
- `feishu_wiki_get_node` / `feishu_docs_search` / `feishu_doc_read` — 从飞书里定位/读合同原件
- `schedule_manage` + `feishu_message_send` — 到期提醒定时扫描并推送负责人（见 [`schedule-manage`]）

## app_token / table_id 怎么来

台账建在一个飞书多维表格里。`app_token` 是 `feishu.cn/base/<app_token>` URL 里的那段；
若是 wiki 链接（`feishu.cn/wiki/...`），先 `feishu_wiki_get_node` 拿 `obj_token`（obj_type 为 bitable 时即 app_token）。
拿到 `app_token` 后 `feishu_bitable_list_tables` 取 `table_id`。**每次操作前先解析出 table_id，别猜。**

## 台账字段设计（建议列）

首次建表时按下面建列，并用 `feishu_bitable_list_fields` + `feishu_bitable_delete_fields`
删掉飞书默认占位列、`feishu_bitable_clear_table` 清默认空行：

| 列名 | 说明 |
|------|------|
| `合同编号` | 唯一主键，防重靠它（如 HT-2026-001） |
| `合同名称` | |
| `对方主体` | 交易对手方全称 |
| `我方签约主体` | 我方签约的公司实体 |
| `类型` | 采购 / 销售 / 服务 / 租赁 / 劳动 / 保密 / 其他 |
| `金额` | 数值；无金额填 0 并在备注说明 |
| `签署日` / `生效日` / `到期日` | 日期；到期日用于提醒 |
| `状态` | 审查中 / 待签 / 已签 / 履行中 / 已到期 / 已归档 / 已作废 |
| `负责人` | open_id 或姓名，到期提醒推给他 |
| `原件链接` | 合同原件的飞书 doc/drive/wiki 链接，可点回原文 |
| `审批单号` | 关联的用印/合同审批 `instance_code`（来自 [`contract-review-sop`]） |
| `审查结论摘要` | 一句话摘要 + 出处页 slug（详见审查报告） |
| `更新时间` | 每次写入时间 |

> 用户已有台账/自定义列时，以用户的列为准，`feishu_bitable_list_fields` 读回真实列名再写，
> **列名必须和表里一致，不一致会被静默丢值**。

## 登记 / 更新一份合同（流程）

1. **解析原件**：拿到合同链接或搜索词，`feishu_docs_search` / `feishu_wiki_get_node` 定位，
   `feishu_doc_read`（docx/doc/sheet）或 `feishu_file_download` + [`ocr-and-documents`]（PDF/扫描件/WPS 导出件）读正文，
   抽出编号、名称、双方主体、金额、各日期。**抽不到的字段不臆造，留空并问负责人。**
2. **防重**：写前先 `feishu_bitable_list_records(filter=<按合同编号>)` 查是否已有该编号的行。
   - 已存在 → 这是**更新**：关键字段（金额/主体/到期日/状态）改动属**中事**，先出改动说明等确认；
     非关键字段（备注/负责人/原件链接）属小事，直接更新。
   - 不存在 → **新增**：`feishu_bitable_create_record` 追加一行（小事，直接做）。
3. **写行**：`fields_json` 用真实列名映射，`状态` 初始按合同当前阶段填，`原件链接` 填飞书原文链接，
   `更新时间` 填当前时间。
4. **留痕**：追加/更新后一句话说明做了什么、判为哪档（见 governance 留痕规则）。

## 到期提醒

- 按需 `feishu_bitable_list_records(sort='["到期日 ASC"]')` 拉出临近到期的合同，汇总成清单。
- 需要常态化提醒时，用 `schedule_manage` 建定时任务，到点扫描 `到期日` 在阈值内的行，
  `feishu_message_send` 私聊各合同负责人（提醒属**中事：只提醒不改数据**，不自动改状态、不自动续签）。
- 群发提醒按大事，先跟用户确认。

## 边界

- `合同编号` 是唯一主键，写前必查防重，绝不重复建行。
- 关键字段改动是中事、删除记录/清表是大事——按 governance 先确认再动手。
- 抽不到的合同信息不编造，留空并向负责人核实。
- 台账含商业敏感数据：只建在用户指定的 base，提醒只发负责人，不群发未经确认的内容。
