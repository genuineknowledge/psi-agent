# Task

Read exactly one completed interview row, its private sanitized handoff, and its linked visible talent-pool row. This step runs once per `interview_record_id` in foreach.

## Interview row

- Read the complete `interview_table_id` with pagination and require exactly one row whose Feishu `record_id` equals the requested `interview_record_id`. Never join by name.
- Require `面试状态=已完成` and a non-empty `面试纪要`. `补充信息` is optional.
- Require all four scores (`靠谱评分`, `专业能力评分`, `学习行动评分`, `AI Native评分`) to be integers from 1 through 5.
- Compute `面试加权总分 = 靠谱×0.3 + 专业能力×0.3 + 学习行动×0.2 + AI Native×0.2`. The stored value must match the computed value rounded to two decimals.
- Derive the raw grade as S≥4.5, A≥3.5, B≥2.5, otherwise C. If any dimension is ≤2, downgrade one level (S→A, A→B, B→C); if AI Native≤2 and professional≤2, force C. Require `面试定级` to equal the derived final grade.
- Require `聪明人等级` to be T1–T5. Most supported results should be T2–T3; T4–T5 requires unusually strong explicit interview narrative.
- Require non-empty `风险验证结果` and `面试结论`. Read `疑问待验证` as an optional list of remaining questions.

There are intentionally no separate evidence columns. For each of the four scores and the smart-person level, extract one or more concise supporting passages from `面试纪要` or `补充信息`. The passage must faithfully preserve an explicit Human observation, example, result, or explanation; do not invent support from the numeric score. If the narrative cannot support any entered score or level, return `status=blocked`.

## Private handoff and talent join

- Call `read` on `.psi/resume-approval/interview-handoffs/<interview_record_id>.json` and parse the complete JSON object.
- Require handoff schema 2.0, the same interview record id and configured interview table id, a sanitized schema 3.0 `assessed` assessment with a 64-character `assessment_revision`, and matching candidate, complete `matched_role`, assessment revision, and talent record id.
- Treat the handoff's complete `matched_role` (including `hard_requirements`) as the only private role contract. Require its `role_key` and `name` to match `assessment.matched_role_key` and `assessment.matched_role_name`; never infer or reconstruct the role from visible summaries.
- Re-read the complete talent table with pagination and require exactly one row whose Feishu record id equals the handoff's `talent_record_id`.
- Require the interview row's `姓名` and `目标岗位` and the talent row's `姓名` and `匹配岗位` to match the handoff assessment. Require talent `初审状态=通过`.
- Never reconstruct the missing private assessment from visible summaries and never expose the handoff JSON in Feishu.

If any requirement fails, submit a structured `status=blocked` item with safe errors and no invented evidence. Otherwise submit:

```json
{
  "interview_evidence_items": {
    "schema_version": "2.0",
    "status": "complete",
    "interview_record_id": "...",
    "talent_record_id": "...",
    "batch_id": "...",
    "candidate_id": "...",
    "candidate_name": "...",
    "assessment_revision": "64-character SHA-256 revision",
    "matched_role": {},
    "assessment": {},
    "notes": "...",
    "supplement": "...",
    "evidence": "concise combined Human interview evidence summary",
    "interview_scoring": {
      "reliability": {"score": 1, "evidence": ["passage extracted from notes"]},
      "professional": {"score": 1, "evidence": ["passage extracted from notes"]},
      "learning_action": {"score": 1, "evidence": ["passage extracted from notes"]},
      "ai_native": {"score": 1, "evidence": ["passage extracted from notes"]},
      "weighted_total": 1.0,
      "grade": "S|A|B|C",
      "smart_level": "T1|T2|T3|T4|T5",
      "smart_level_evidence": ["passage extracted from notes"],
      "open_questions": []
    },
    "risk_verification": "...",
    "interview_conclusion": "...",
    "errors": []
  }
}
```
