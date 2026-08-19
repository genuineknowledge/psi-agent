# Task

Create one evidence-based hiring recommendation from one `validated_interview_item`. Do not call Feishu.

Combine the stored role requirement, sanitized resume assessment, initial-review approval, and completed interview evidence. Distinguish resume facts, Human interview notes, model inference, and unresolved unknowns.

Use `hire`, `no_hire`, or `hold`. `hire` requires every string from `matched_role.hard_requirements` to appear exactly once in `requirement_evidence_matrix`, all requirements to pass with non-empty evidence, at least one evidence-backed hire reason, and every listed risk to be resolved. Missing decisive evidence must produce `hold`. The complete `matched_role` comes from the private A2 handoff and is authoritative; never reconstruct it from the assessment, visible rows, or candidate name.

Preserve the validated candidate identity, interview Feishu `record_id`, talent `record_id`, `interview_revision`, complete `matched_role`, assessment, and four-dimension interview scoring exactly. Apply the formal resume/interview decision matrix. Map S/A/B/C to `远超预期/符合预期/低于预期/明显不足` for the matrix only. The matrix is a baseline, not permission to bypass failed hard requirements or unresolved decisive risks.

```json
{
  "hiring_conclusions": {
    "schema_version": "2.0",
    "status": "concluded",
    "candidate_id": "...",
    "candidate_name": "...",
    "interview_record_id": "...",
    "interview_revision": "...",
    "talent_record_id": "...",
    "matched_role": {},
    "assessment": {},
    "interview_scoring": {},
    "decision_matrix": {
      "resume_grade": "A|B|C|D|E|F",
      "interview_grade": "S|A|B|C",
      "performance_band": "远超预期|符合预期|低于预期|明显不足",
      "matrix_result": "直接录用|强烈推荐|推荐录用|可录用|待定|重新评估|不推荐|淘汰"
    },
    "recommendation": "hire|no_hire|hold",
    "requirement_evidence_matrix": [{"requirement": "...", "result": "pass|fail|unknown", "evidence": [], "source": "resume|interview|both"}],
    "hire_reasons": [{"reason": "...", "evidence": [], "source": "resume|interview|both"}],
    "risk_closure": [{"risk": "...", "status": "resolved|unresolved|confirmed", "evidence": []}],
    "remaining_unknowns": [],
    "interview_summary": "...",
    "confidence": 0.0
  }
}
```

Submit complete structured evidence only. Never fill a Human final decision.
