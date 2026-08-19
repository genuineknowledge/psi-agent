# Task

Verify that every final decision exists on the exact Human-edited interview row. Do not create or overwrite records.

- Require `final_decisions.status=complete`; otherwise return blocked without Feishu calls.
- Query the complete configured `interview_table_id` with pagination and require exactly one record for every collected `interview_record_id` whose `面试状态` equals the collected decision.
- Require the row's identity to match the preserved conclusion and carry the associated `talent_record_id` into the receipt only for audit lineage. Never read or write final status on the talent row.
- Build `interview_table_url` from the configured `base_url` and `interview_table_id`; it must open the interview table, not the talent table.
- Reuse the configured report document. Never claim persistence when a row is missing or inconsistent.

```json
{
  "result_write_receipt": {
    "schema_version": "3.0",
    "status": "complete|blocked",
    "conclusion_run_id": "...",
    "storage_model": "single-visible-interview-row",
    "records": [{"interview_record_id": "...", "talent_record_id": "...", "decision": "..."}],
    "interview_table_url": "...",
    "report_document_id": "...",
    "report_document_url": "...",
    "errors": []
  }
}
```
