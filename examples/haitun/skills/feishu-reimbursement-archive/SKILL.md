---
name: feishu-reimbursement-archive
description: "Pull Feishu reimbursement (报销) approval instances, download each one's attachments (receipts/invoices) into a per-claim folder, and validate each claim against the checklist the user provides, producing a summary table. Use when asked to archive/organize reimbursement documents or audit reimbursements. Enumerates instances with feishu_approval_list_instances, reads each with feishu_approval_get, downloads attachments with feishu_file_download, and applies the USER-supplied validation rules each run — hard-codes no audit rules. Needs approval:approval:readonly + drive:drive:readonly scopes."
category: productivity
---

# Feishu Reimbursement (报销) Archive & Audit

Enumerate reimbursement approvals, download each claim's receipts into its own
folder, and check each against a validation checklist. This skill is a **generic
recipe**: it gathers and organizes, but the **validation conditions are supplied
by the user each run** — it hard-codes no audit rules.

Uses existing tools:
- `feishu_approval_list_instances(approval_code, start_time, end_time)` — list claims
- `feishu_approval_get(instance_id)` — read one claim's form + `attachments`
- `feishu_file_download(source, save_path, is_url)` — download receipts
- an output tool of the user's choice for the summary table

## Every run: get inputs from the user first

1. **Which approval** — the reimbursement `approval_code` (from the Feishu approval
   admin, or `feishu_approval_list_tasks` for a sample claim).
2. **Period** — start/end as Unix millisecond strings (or let it default to 30 days).
3. **The archive root** — a local folder to build per-claim subfolders under.
4. **The validation checklist** — e.g. "发票金额 == 申请金额", "必须有发票附件",
   "抬头是公司名", "日期在本期范围内", "单笔 ≤ 上限". Get it explicitly.

If the checklist is missing, ask — do not invent audit conditions.

## Critical: attachment links expire in ~12 hours

Approval-form attachments (发票图片/PDF) are **direct URLs valid only ~12 hours**,
not permanent drive tokens. `feishu_approval_get` returns them under `attachments`
as `{name, type, kind, value}`:
- `kind: "url"` → download with `feishu_file_download(url, path, is_url=True)`.
- `kind: "drive"` → download with `feishu_file_download(token, path, is_url=False)`.

**Read the instance detail and download its files right away, in the same pass.**
If a download returns an expired-link error, re-call `feishu_approval_get` for a
fresh URL and retry.

## Flow

1. `feishu_approval_list_instances(approval_code, start_time, end_time)` → all
   `instance_codes`.
2. For each code:
   a. `feishu_approval_get(code)` → applicant, status, `form`, `attachments`.
   b. Create a folder `<root>/报销-<applicant>-<code>/`.
   c. Download every attachment into that folder (see the 12h note above); keep
      the original file names, de-dupe collisions with a suffix.
   d. Extract the claim's key fields from `form` (amount, category, invoice info,
      date) for validation.
3. **Validate** each claim against the user's checklist. Record pass/fail per
   condition, with the reason.
4. **Summary table** — one row per claim: applicant, amount, status, attachment
   count, validation result (and which checks failed). Deliver where the user
   asked (save a file / write a bitable / message).

## Boundaries

- Report validation results honestly — flag failures and missing attachments;
  never mark a claim "无误" if a required receipt is absent or a check failed.
- Reimbursement data is financial/personal — keep downloads in the user's chosen
  folder and don't broadcast details beyond the intended recipient.
- This skill reads and archives; it does not approve/reject. To act on a claim,
  the user drives `feishu_approval_decide` separately (which records under a real
  approver).
