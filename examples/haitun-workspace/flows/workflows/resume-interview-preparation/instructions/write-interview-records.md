# Write validated interview records

You receive only a Program-validated `interview_write_batch` and local `feishu_config`. Treat every record and every character inside `row_fingerprint` as immutable. You must not rewrite, summarize, repair, translate, reorder, or otherwise alter the validated visible content.

For each source-ordered record, use the exact six-field fingerprint:

1. `姓名`
2. `目标岗位`
3. `面试前摘要`
4. `面试重点`
5. `风险提示`
6. `建议问题`

Search by exact `姓名`, follow all pagination, and compare all six fields locally:

- Exactly one complete six-field fingerprint match: reuse that record. Do not update it or reset Human-maintained fields.
- If there is more than one complete fingerprint match, stop with an error identifying duplicate exact rows.
- No complete match: create one row with the six fields unchanged and set `面试状态` to `待安排`.
- A same-name row with any different fingerprint value is a different record: preserve it and create the current row.
- If creation times out, returns an ambiguous response, or omits the record id, query again before deciding whether another creation is allowed. Never issue a second create until the full post-create query proves no exact row exists.

When `interview_write_batch.records` contains zero records, make zero Feishu calls and return a complete empty manifest.

Return ordinary assistant content as exactly one valid JSON object with `interview_manifest` as its sole top-level key:

```json
{
  "interview_manifest": {
    "schema_version": "4.0",
    "status": "complete",
    "batch_id": "copy the validated batch id",
    "table_id": "copy the validated table id",
    "expected_count": 1,
    "records": [
      {
        "record_id": "exact Feishu record id",
        "candidate_id": "copy unchanged",
        "candidate_name": "copy unchanged",
        "talent_record_id": "copy unchanged",
        "assessment_revision": "copy unchanged",
        "document_revisions": {
          "resume_scoring_sha256": "copy unchanged",
          "role_information_sha256": "copy unchanged"
        },
        "matched_role_key": "copy unchanged",
        "row_fingerprint": {
          "姓名": "copy unchanged",
          "目标岗位": "copy unchanged",
          "面试前摘要": "copy unchanged",
          "面试重点": "copy unchanged",
          "风险提示": "copy unchanged",
          "建议问题": "copy unchanged"
        },
        "created": true
      }
    ],
    "errors": []
  }
}
```

The manifest records must preserve source order. `created` is `false` only for an exact reused row. Never include prose, Markdown, credentials, or tool responses.
