# Task

Append a privacy-conscious audit summary to the configured Feishu document. State the two fact-source boundaries explicitly: 「候选人才库」 is the source of truth for 初审 and resume assessment, while 「面试记录」 is the source of truth for interview evidence and the 最终录用状态. The audit document is not a source of truth.

- Require both `final_decisions.status` and `result_write_receipt.status` to be `complete`.
- Read the document first and use marker `interview-conclusion:<conclusion_run_id>:<validated_hiring_conclusions.conclusion_revision>` for idempotency. Corrected evidence therefore appends a new auditable revision while an exact rerun does not append twice.
- If the marker exists, do not append again.
- Include role/version, counts for `录用`/`不录用`/`待定`, and one concise candidate section containing the Human decision, evidence-backed hire reasons, risk closure, remaining unknowns, and confidence. Carry `interview_record_id` and `talent_record_id` only as internal audit lineage; do not append either technical id to the Human-facing document.
- Never append phone, email, ID, address, protected attributes, or raw resume/interview text.

```json
{
  "report_result": {
    "schema_version": "2.0",
    "status": "complete|blocked",
    "conclusion_run_id": "...",
    "marker": "interview-conclusion:<conclusion_run_id>:<conclusion_revision>",
    "document_id": "...",
    "document_url": "...",
    "appended": true,
    "already_present": false,
    "errors": []
  }
}
```
