# Task

Create or reuse one 15-field initial-review row in the configured `候选人才库` table for every table-writeable assessment, and persist its exact staged resume in the native `简历附件` field before temporary files can be cleaned up.

## Fail-closed guard and SHA binding

- Before any Feishu call, require `validated_candidate_assessments.status=complete`, a non-empty `assessments` list, the validator-generated `assessment_revision`, a non-empty `staged_resume_files` list, non-empty runtime `feishu_config.app_token`, and non-empty runtime `feishu_config.talent_pool_table_id`. `constraint_warnings` do not block a row whose mapped fields remain writeable. Otherwise perform no Feishu read, upload, or write.
- Validate every staged descriptor before making a Feishu call. It must have a 64-character lowercase `sha256`, an allowed `format` (`.pdf`, `.docx`, `.md`, or `.txt`), a neutral `name` equal to `resume<format>`, a non-empty internal `path`, `temporary=true`, and integer `size_bytes` from 1 through 20971520. Never upload `original_name` as the remote filename.
- For each assessment, match `assessment.source.sha256` to `staged_resume_file.sha256`. Require exactly one matching descriptor for every assessment. A zero match, duplicate SHA match, malformed descriptor, or reused staged descriptor blocks the whole batch before any Feishu call.
- SHA-256 is the only attachment join key. Never associate a file by candidate name, local filename, `original_name`, list position, or fuzzy similarity.
- The verified table has exactly these fields in live order: `姓名`, `简历附件`, `评级`, `学历`, `毕业院校/背景`, `总分`, `备注`, `匹配岗位`, `匹配点`, `不匹配点`, `面试建议`, `面试建议理由`, `问题库`, `初审状态`, `简历摘要`. `简历附件` must be native Feishu type 17.
- `评级` must be A-F; `面试建议` must be `建议面试` or `不建议面试`; `初审状态` is `待审批`, `通过`, or `不通过`.
- Use the real view name `候选人看板` in the manifest and Human handoff. Create rows only for `validated_candidate_assessments.assessments`; copy `failed_candidates` to the manifest without inventing rows or attachments for them.

## Exact visible mapping

Build the 12-field AI fingerprint deterministically before deciding whether to upload. A new row contains all 15 visible fields:

- `姓名`: `candidate_name`;
- `评级`: `grade`;
- `学历`: `education`;
- `毕业院校/背景`: `education_background`;
- `简历摘要`: `resume_summary` must be a JSON string array; convert it deterministically to `"\n".join(resume_summary)` before writing the text field, and use an empty string for an empty list;
- `总分`: numeric `total_score`;
- `匹配岗位`: `matched_role_name`;
- `匹配点`: convert the table-writeable `match_points` list to source-ordered lines, exactly `- 要求：…；证据：…` per point; join multiple `resume_evidence` entries with `、` in source order, and use an empty string for an empty point list;
- `不匹配点`: convert the table-writeable `mismatch_points` list to source-ordered lines, exactly `- 风险：…；依据：…` per point; join multiple `resume_evidence` entries with `、` in source order, use an empty string for an empty point list, and preserve cautious evidence-gap wording;
- `面试建议`: exact `interview_recommendation` enum;
- `面试建议理由`: `interview_recommendation_reason` as concise Chinese text;
- `问题库`: render `verification_questions` in exact source order with no evidence metadata, exactly one line per item as `<1-based index>. [<category>] <question>`; for example `1. [真实性核验] 请说明 Python 项目中你个人负责的关键工作。`. Never expose `evidence_anchor`, `purpose`, `positive_signal`, or `risk_signal` in the table;

For a new row only, also set `备注` to an empty string and `初审状态` to `待审批`. The attachment field value must be exactly `[{"file_token": "<returned token>"}]`. Never write a bare token, URL, or local path.

Do not write hashes, IDs, raw JSON, raw resume text, contact information, internal keys, English enum tokens, local paths, temporary URLs, or attachment tokens to any user-visible text field.

## Fingerprint-first attachment idempotency

The canonical fingerprint contains exactly these 12 个 AI 所有字段: `姓名`, `评级`, `学历`, `毕业院校/背景`, `简历摘要`, `总分`, `匹配岗位`, `匹配点`, `不匹配点`, `面试建议`, `面试建议理由`, `问题库`. `备注` and `初审状态` do not enter the fingerprint because Human may change them. `简历附件` does not enter the fingerprint because its `file_token` is not content-stable.

For each assessment, in source order:

1. Query by exact `姓名`, follow pagination, normalize the 12 AI fields to visible scalar text or number values, and compare the complete fingerprint locally. Never use name alone as row identity.
2. More than one complete fingerprint match blocks the whole batch. A same-name row with a different complete fingerprint is a separate assessment revision and must not be overwritten.
3. Exactly one fingerprint match with a non-empty native attachment array containing an object with a non-empty `file_token`: reuse the row. Do not upload or update anything, and do not reset `备注` or `初审状态`.
4. Exactly one fingerprint match with a missing, empty, or malformed attachment: upload the SHA-matched descriptor with `feishu_drive_upload(file_path=<internal path>, parent_node=<feishu_config.app_token>, parent_type="bitable_file", file_name=<neutral staged name>, user_key=<configured user_key>, identity=<configured identity>)`. Require a successful response with one non-empty `file_token`. Then call `feishu_bitable_update_record` for the exact matched `record_id` with only `{"简历附件": [{"file_token": "<returned token>"}]}`. Passing any AI field, `备注`, or `初审状态` in this incremental update is forbidden.
5. Zero fingerprint matches: perform the same upload, then call `feishu_bitable_create_records` once with all 15 fields, including `简历附件` as an array containing the returned token object. Never create first and guess the attachment later.
6. After every create or attachment backfill attempt, query the exact name again, follow pagination, and require exactly one complete fingerprint match with the expected `record_id` when updating and an attachment object containing the exact returned `file_token`. This readback is mandatory even when the create/update response is missing, times out, or reports success. A missing token, different token, duplicate row, ambiguous response, or readback mismatch blocks the whole batch.
7. Resolve the complete fingerprint before uploading. This prevents retries from uploading again when a reusable row already has an attachment. Within one attempt, retain the returned token only long enough to create/update and verify it; never place it in an Artifact or error message.

Any upload, create, update, pagination, or readback failure blocks the batch. Do not report partial success as `complete`. Error text must use a sanitized business reason and must not copy tool responses containing a token, local path, temporary URL, record ID, request ID, or credential.

## Output

Return exactly one JSON object. The manifest proves persistence with a boolean and never contains an attachment token, original upload filename, local path, temporary URL, or upload response:

```json
{
  "talent_pool_manifest": {
    "schema_version": "4.0",
    "status": "complete|blocked",
    "batch_id": "...",
    "base_url": "...",
    "table_id": "exact feishu_config.talent_pool_table_id",
    "view_name": "候选人看板",
    "expected_count": 0,
    "failed_candidates": [],
    "records": [
      {
        "record_id": "...",
        "candidate_id": "...",
        "assessment_revision": "...",
        "row_fingerprint": {
          "姓名": "...",
          "评级": "...",
          "学历": "...",
          "毕业院校/背景": "...",
          "简历摘要": "- ...\n- ...",
          "总分": 0,
          "匹配岗位": "...",
          "匹配点": "...",
          "不匹配点": "...",
          "面试建议": "...",
          "面试建议理由": "...",
          "问题库": "1. [真实性核验] ...\n2. [岗位匹配] ...\n3. [风险澄清] ..."
        },
        "attachment_persisted": true,
        "created": true
      }
    ],
    "errors": []
  }
}
```
