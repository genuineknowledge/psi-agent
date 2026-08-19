# Prepare one interview draft

You receive exactly one `interview_task` that has already passed the immutable handoff checks, plus local `feishu_config`. Work only on this candidate. The assessment and `matched_role` are authoritative; do not change the candidate, role, decision, or evidence.

You may search the interview table only for optional same-role completed interview context. Restrict comparison to records whose target role exactly equals `matched_role.name` and whose interview status is completed. If the query fails, is unavailable, or returns no useful rows, continue without cohort comparison. Historical context must never override the supplied assessment.

Return ordinary assistant content as exactly one valid JSON object with `interview_drafts` as its sole top-level key. Do not use Markdown fences or commentary:

```json
{
  "interview_drafts": {
    "schema_version": "1.0",
    "candidate_id": "copy interview_task.candidate_id exactly",
    "面试前摘要": "基于该候选人已验证简历证据的简洁摘要",
    "面试重点": "1. ...\n2. ...",
    "风险提示": "- ...",
    "建议问题": "1. [真实性核验] ...\n2. [岗位匹配] ...\n3. [风险澄清] ..."
  }
}
```

Rules:

- Return exactly the six keys shown inside `interview_drafts`: the schema, candidate id, and four visible fields. Do not add metadata or references.
- Each visible field may be a non-empty string or JSON array of non-empty strings.
- `面试前摘要` summarizes verified experience relevant to the exact role.
- `面试重点` lists decision-oriented areas that distinguish genuine ability from résumé wording.
- `风险提示` lists evidence gaps, mismatch points, and matters that require confirmation. Unknown evidence is not a negative fact.
- `建议问题` is not newly generated. Copy every item from `interview_task.assessment.verification_questions` in exact source order and render only `<1-based index>. [<category>] <question>`, one line per item. Do not rewrite, add, drop, reorder, summarize, or expose `evidence_anchor`, `purpose`, `positive_signal`, or `risk_signal`. The write-batch Program rejects any mismatch.
- Do not echo candidate name, role name, talent record id, table id, batch id, hashes, revisions, contact information, raw résumé text, protected attributes, or hidden JSON into generated prose.
- Do not return authority fields outside the shown contract. Do not write, update, or delete any Feishu row.
