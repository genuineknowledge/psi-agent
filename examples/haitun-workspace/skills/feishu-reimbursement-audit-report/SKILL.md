---
name: feishu-reimbursement-audit-report
description: "Auto-audit Feishu reimbursement (报销) approvals against tiered conditions, download each verified claim's attachments into a per-claim folder, roll everything into a bitable, and produce a financial report + analysis (财务报告/分析单). Use when asked to review/approve reimbursements, archive receipts, or summarize spending. Enumerates claims with feishu_approval_list_instances, reads each with feishu_approval_get, validates + classifies as 小事/中事/大事 per admin-finance-governance (小事 auto-approve via feishu_approval_decide, 中事 recommend, 大事 ask), downloads attachments with feishu_file_download only when checks pass, aggregates via feishu_bitable_*, writes the report with feishu_doc_create, and pushes it with feishu_message_send. Needs approval:* + drive:drive:readonly scopes."
category: productivity
---

# 报销自动审核 + 财务报告/分析 + 附件归档

自动审核报销审批，校验无误的附件下载归档到文件夹，逐单汇总进多维表格，产出财务报告
和分析单并推送给财务/相关人。**分级口径以 [`admin-finance-governance`] 为准**——先加载总纲。
本技能在同分支的 [`feishu-reimbursement-archive`]（下附件+校验机制）之上，加了**分级自动审核**
与**财务报告/分析**。

用到的现成工具：
- `feishu_approval_list_instances` / `feishu_approval_get` — 列举/读取报销单（含 `attachments`）
- `feishu_approval_decide` — 校验全过的小事，代真实审批人放行
- `feishu_file_download(source, save_path, is_url)` — 下载发票/附件
- `feishu_bitable_*` — 逐单汇总台账
- `feishu_doc_create` / `feishu_doc_append_content` — 财务报告 + 分析单
- `feishu_message_send` / `feishu_topic_start` — 推送报告

## 每次运行先向用户确认

1. **哪个审批** — 报销 `approval_code`。
2. **周期** — start/end（Unix 毫秒串，或默认近 30 天）。
3. **归档根目录** — 本地文件夹，在其下按单建子文件夹。
4. **代谁审批** — 小事自动放行用的真实审批人 user_id。
5. **报告推给谁** — 财务/相关人 open_id 或群 chat_id。
6. **是否覆盖默认阈值 / 校验清单** — 不覆盖用 governance 默认报销规则。

## 关键：附件链接约 12 小时失效——边读边下

`feishu_approval_get` 的 `attachments` 是 `{name,type,kind,value}`：
- `kind:"url"` → `feishu_file_download(url, path, is_url=True)`
- `kind:"drive"` → `feishu_file_download(token, path, is_url=False)`

**读一单就立刻下这一单的附件**，同一趟完成。下载报链接过期，就重新 `feishu_approval_get`
拿新链接再下。

## 分级判定 + 校验（默认见 governance）

对每单从 `form` 取**金额、类别、发票号/发票金额、抬头、日期**，先跑校验，再判档：
- 校验项（默认，可被用户当次清单覆盖）：发票金额 == 申请金额；发票/附件齐全；抬头正确；
  在制度类别与本期范围内；单笔未超上限；非重复报销。
- **小事**：金额 ≤ ¥500 且校验**全过** → 附件下载归档后 `feishu_approval_decide(approve=True,...)` 放行，写台账留痕。
- **中事**：金额 ≤ ¥2000 且校验基本过但有需人核对点 → **只出"建议通过/驳回 + 理由"**，不放行。
- **大事**：金额 > ¥2000，或缺票 / 金额不符 / 抬头不对 / 超类别 / 疑似重复 / 跨期 → **必问用户**，附建议。

**校验不通过绝不放行、绝不标"无误"**，哪怕金额很小；判不准往大事走。

## 流程

1. `feishu_approval_list_instances(approval_code, start, end)` → 所有 `instance_code`。
2. 逐单：
   a. `feishu_approval_get(code)` → 申请人、状态、`form`、`attachments`、`task_list`。
   b. 建目录 `<root>/报销-<申请人>-<code>/`，把附件全部下进去（见 12h 说明），
      保留原名，重名加后缀去重。
   c. 从 `form` 抽金额/类别/发票信息/日期/抬头，跑校验清单，记录每项 pass/fail + 原因。
   d. 判档：小事校验全过 → 放行 + 留痕；中事 → 记建议；大事 → 标"待确认" + 建议。
3. **逐单台账**：`feishu_bitable_create_record`，列建议：
   `申请人 / 金额 / 类别 / 发票号 / 校验结果(过/失败项) / 判定档位 / 处置 / 附件数 / 归档路径 / 时间`。
4. **财务报告 + 分析单**：`feishu_doc_create` 建"报销财务报告-<周期>"，`feishu_doc_append_content` 写：
   - 概览：总单数、总金额、已自动通过数/金额、建议数、待确认(大事)数/金额；
   - **分析**：按**类别 / 部门 / 申请人**聚合金额与占比，TOP 支出项，超标项清单，缺票/异常项清单，
     分级分布；
   - 每个金额都显示**算式可核对**（如 "报销额 = Σ各单 = …"）。
5. **推送**：报告文档链接 `feishu_message_send` 私聊财务/相关人。群发广播按大事，先确认再发。

## 边界

- 缺票 / 金额不符 / 抬头不对**绝不标"无误"**，也绝不放行——如实标失败项。
- 财务/个人数据敏感：下载只落用户指定文件夹，报告只发指定接收人。
- 本技能读、校验、归档、按分级放行；代人放行必须用真实审批人 user_id，记在真人名下。
- 每次自动放行都要有台账行 + 一句依据（见 governance 留痕规则）。
