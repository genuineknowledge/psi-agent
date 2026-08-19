# Read completed initial-review decisions

Re-read the talent-pool table after the external Human review and join every decision to the immutable source artifacts.

- Require `talent_pool_manifest.status=complete` and `validated_candidate_assessments.status=complete`.
- Query the configured talent table with pagination and retrieve every exact Feishu `record_id` stored in `talent_pool_manifest.records`. Never join by name.
- Require exactly one live row for every stored `record_id`; missing or duplicate records block the whole batch.
- Compare the complete 12-field AI-owned `row_fingerprint`, including `问题库`, from the manifest with the live row. Human-owned `备注` and `初审状态` are excluded. Any other changed, missing, or malformed visible field blocks the whole batch.
- Accept only `通过` or `不通过` from `初审状态`; `待审批`, missing, or malformed decisions block the whole batch.
- Preserve the complete validated assessment, exact `record_id`, and decision. Do not write, update, or delete any Feishu row.

Return ordinary assistant content as exactly one valid JSON object with `initial_decision_bundle` as its sole top-level key, without Markdown fences or commentary:

```json
{
  "initial_decision_bundle": {
    "schema_version": "3.0",
    "status": "complete|blocked",
    "batch_id": "copy initial_review_batch_id exactly",
    "approved": [{"assessment": {}, "record_id": "rec...", "initial_status": "通过"}],
    "rejected": [{"assessment": {}, "record_id": "rec...", "initial_status": "不通过"}],
    "pending": [],
    "errors": []
  }
}
```
