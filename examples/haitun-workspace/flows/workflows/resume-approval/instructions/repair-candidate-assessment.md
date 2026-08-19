# Task

Repair exactly one schema 3.0 candidate assessment from deterministic validation diagnostics. Do not redo unrelated analysis, change batch authorities, or start another workflow.

## Inputs and immutable identity

`assessment_repair_request_round_1` or `assessment_repair_request_round_2` contains:

- `original_assessment`: the complete candidate object to repair;
- `validation_errors`: every currently known repairable error with an exact field path;
- `expected_contract`: the required 3.0 fields, forbidden legacy fields, parsed online scoring rules, deterministic recommendation rules, normalized education rules, active runtime roles, and recommendation enum;
- `immutable_identity`: exact `batch_id`, `candidate_id`, `source_sha256`, both `document_revisions`, and `matched_role_key`.

The supplied `reference_documents`, `role_catalog`, and `batch_id` are fixed authorities. They are provided for verification, not reinterpretation. The repair must preserve:

- the entire `source` object and candidate name; `source` is runner-controlled metadata whose `name` was already normalized before analysis, so copy it exactly and never redact, rename, or reconstruct it during repair;
- `batch_id` and `candidate_id`;
- both document revision hashes;
- `matched_role_key` and therefore the selected catalog role;
- all valid evidence and judgments unrelated to a listed error.

Never select another role during repair. If the selected role's displayed name is wrong, copy the exact `name` for the immutable role key from `expected_contract.active_roles`.

## Repair policy

1. Return one complete assessment object, never a patch or explanation.
2. Fix every listed error and fields directly derived from the correction.
3. Remove every forbidden legacy field and emit exactly the required assessed fields.
4. Use only `expected_contract.scoring_rules` for the total range and grade. Recompute `grade` when `total_score` changes; do not add dimension scores.
5. Normalize `education` and `education_background` exactly as specified by `expected_contract.education_rules`: keep only the allowed highest education level and institution names; every known institution must carry its stage label even when there is only one institution, and majors, cohorts, rankings, awards, experience, and other profile summaries must be removed.
6. Normalize `resume_summary` exactly as specified by `expected_contract.resume_summary_rules`: a JSON array containing 1–5 non-empty strings, every string begins with `- `, and every item is a resume-supported strength. Follow the supplied JSON-array example. Do not return a newline-delimited string, and do not include risks, private data, headings, numbering, or invented claims.
7. `matched_role_name` and every point requirement must exactly match the immutable active role.
8. Keep the structured `{requirement, resume_evidence}` object format and make both `match_points` and `mismatch_points` non-empty. Put affirmative resume-to-role evidence in `match_points`. Put explicit contradiction, evidenced shortfall, or a material role evidence gap/risk in `mismatch_points`; when information is absent, state only that the resume does not show the evidence and Human should verify it, never claim the candidate lacks the ability.
9. Repair verification_questions only from expected_contract.verification_question_rules. Keep 3–6 exact six-field objects in source order; cover 真实性核验 and 岗位匹配, and cover 风险澄清 whenever mismatch_points is non-empty. Copy each evidence_anchor exactly from the allowed role requirement or assessment evidence, keep the question visibly tied to that anchor, and exclude protected attributes and unsupported negative claims. In a question-only repair where every diagnostic is under verification_questions, preserve every other assessment field byte-for-byte.
10. Preserve negative evidence. Do not weaken a result or invent supporting evidence merely to pass validation.
11. Recompute `interview_recommendation` exactly from `expected_contract.recommendation_rules`: only A/B with affirmative evidence for every selected-role hard requirement and no hard-requirement mismatch may be `建议面试`; C/D/E/F and every failed hard-requirement gate must be `不建议面试`. Human may later override a C candidate through `初审状态`, but this repair may not bypass the automatic gate.
   目标工作地点、候选人所在城市、通勤或到岗地点是非常低权重的待确认信息，不得影响推荐结果，也不得成为拒面理由的主因。
12. The recommendation reason must lead with the candidate's 具体岗位证据缺口 or explicit contradiction and explain its role impact; for `建议面试`, it must instead lead with the concrete evidence satisfying every hard requirement. Preserve genuine strengths and explain material items for Human review. 不得把评级或分数本身作为主要理由，也不得只写“C 未达到门槛”“分数不足”或同义套话；grade may appear only as secondary context. If supplied evidence cannot support a required judgment, use the contract's allowed `unknown` text; never fabricate negative or supporting evidence.
13. Do not expose phone, email, ID number, exact address, or other protected personal attributes in candidate-derived analytical fields. The runner-controlled `source` object is already normalized and remains byte-for-byte immutable under rule 1; privacy cleanup must never mutate it during repair.

## Output contract

Do not call `submit_step_result`. Return exactly one valid JSON object in ordinary assistant content. Its sole top-level key must be the exact required output artifact key shown in the runner message, and its value must be the complete repaired candidate assessment. Do not add Markdown, prose, comments, another wrapper, or a JSON-encoded string.
