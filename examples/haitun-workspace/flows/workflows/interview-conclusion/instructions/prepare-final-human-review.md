# Human request preparation

Prepare one concise Chinese final-confirmation request. Include conclusion run ID, candidate count, the Base link to the configured `interview_table_id`, and every manifest record's recommendation summary. Ask the Human to verify the requirement evidence, hire reasons, risk closure, remaining unknowns, and confidence, then set `面试状态` on each exact 「面试记录」 row identified by `interview_record_id` from `已完成` to `录用`, `不录用`, or `待定`, and reply `最终确认` in chat.

Make clear that the 「候选人才库」 row must not be used for the final status, and that `待定` is correct when decisive evidence is missing. If the manifest is blocked, show its errors and do not imply results are final. Return only human-facing text.
