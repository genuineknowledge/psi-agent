# Task

Re-read the exact existing interview rows after the Human checkpoint. The chat reply is only a trigger.

- Require one non-empty `interview_record_id` and one linked `talent_record_id` for each validated conclusion; reject duplicate interview ids. A `talent_record_id` may repeat only when distinct requested interviews are explicitly linked to that same approved candidate.
- Read the complete configured `interview_table_id` with pagination and require exactly one record for each exact `interview_record_id`. Never join by name.
- Require the row's `姓名` and `目标岗位` to match the validated conclusion. Preserve `talent_record_id` only as the already-validated initial-review lineage; do not query it for the final status.
- Accept only `录用`, `不录用`, or `待定` from `面试状态`; `已完成`, missing, or malformed rows block the complete conclusion run.
- Preserve the generated conclusion, Human decision, `interview_record_id`, and `talent_record_id`.

```json
{
  "final_decisions": {
    "schema_version": "3.0",
    "status": "complete|blocked",
    "conclusion_run_id": "...",
    "confirmed": [{"candidate_id": "...", "candidate_name": "...", "decision": "录用|不录用|待定", "interview_record_id": "...", "talent_record_id": "...", "conclusion": {}}],
    "pending": [],
    "errors": []
  }
}
```
