# Resume Interview Preparation (Workflow A2)

This workflow owns post-review decision validation, interview-draft generation, controlled Feishu interview-row persistence, and private interview handoffs. Its sole input is the exact `initial_review_handoff` object returned by a completed `resume-approval` run.

## Run sequence

1. Run `resume-approval`. Workflow A creates or reuses talent rows, returns `initial_review_handoff` plus the review request, and ends normally.
2. Complete the Human review externally in the talent table. No workflow remains active and `run_flow_resume` is not used.
3. Start a fresh `resume-interview-preparation` run with the exact `initial_review_handoff` descriptor.
4. A2 rereads every expected talent row by exact Feishu `record_id`, validates the complete 12-field AI-owned fingerprint including `问题库`, and accepts only `通过` or `不通过`.
5. Use A2's `interview_record_ids` to start `interview-conclusion` after interviews are completed.

The file `resume-interview-preparation.inputs.example.json` is shape-only. Its placeholder hash and path cannot be executed; a real descriptor must come from Workflow A.

## Review gate

The immutable input contains pre-review facts only, including each candidate's structured 3–6 item verification question bank. A2 does not trust chat text as a decision. It independently verifies the descriptor hash, local Feishu destination, assessment revisions, exact talent-record coverage, question-bank structure and visible rendering, and live Human decisions before generating any interview draft.

Any expected row that is still `待审批`, missing, duplicated, malformed, or changed in an AI-owned field blocks the whole A2 run before the interview table is touched. Zero approved candidates after a complete review is a valid successful run with empty interview outputs.

## Failure and retry

If decision collection, a draft Agent, or the write stage fails, start a fresh A2 run with the same descriptor. A2 rereads decisions and regenerates drafts; Workflow A is not rerun. The writer queries the exact six-field fingerprint and reuses a unique exact row, so an ambiguous create response is reconciled before another create is allowed.

There is no `run_flow_resume`, automatic checkpoint continuation, or fine-grained retry. Each A2 invocation is independent and consumes the same immutable pre-review source.

## Concurrency and write ownership

One read-only Agent first validates the completed Human decisions. Then one read-only draft Agent handles each approved candidate, with workflow concurrency limited to four. Each draft iteration returns an Artifact only and must copy `建议问题` from the assessment question bank in exact order as `N. [类别] 问题`. After all drafts complete, a deterministic Program rejects any rewritten, dropped, added, or reordered suggestion, attaches authoritative candidate, role, talent-record, and revision identities, and creates one source-ordered write batch. Evidence anchors, purposes, and answer signals remain private and are never rendered into the interview table. A separate Agent performs all Feishu interview-table searches and creates. Parallel draft Agents never write shared files or tables.
