# Task

Prepare the existing interview rows for Human final review. Do not create or update any row.

- Require `validated_hiring_conclusions.status=complete`; otherwise perform no Feishu call.
- For every conclusion, use its exact `interview_record_id`. Read the complete configured `interview_table_id` with pagination and require exactly one matching Feishu record id. Never join by name.
- Require the interview row's `姓名` and `目标岗位` to match the preserved assessment and require its current `面试状态=已完成`. The final Human decision will replace that status on this same interview row.
- Use the conclusion's exact `talent_record_id` only to validate the linked row in the configured `talent_pool_table_id`: read the complete table with pagination, require exactly one matching Feishu record id, require matching `姓名` and `匹配岗位`, and require `初审状态=通过`. Never infer this association from a name, role, or visible summary. The talent row is not a destination for the final interview status.
- Include in the manifest a concise Chinese summary of recommendation, requirement evidence, hire reasons, risk closure, remaining unknowns, and confidence. This summary is shown by the Human checkpoint; it is not written into extra Feishu columns.

```json
{
  "final_review_manifest": {
    "schema_version": "3.0",
    "status": "complete|blocked",
    "conclusion_run_id": "...",
    "base_url": "...",
    "table_id": "<interview_table_id>",
    "view_name": "已结束",
    "expected_count": 0,
    "records": [{"interview_record_id": "...", "talent_record_id": "...", "candidate_id": "...", "candidate_name": "...", "interview_revision": "...", "recommendation_summary": "..."}],
    "errors": []
  }
}
```
