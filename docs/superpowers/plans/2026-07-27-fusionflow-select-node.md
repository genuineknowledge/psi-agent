# FusionFlow SelectNode implementation plan

1. Add failing graph-model tests for SelectNode ownership, dependencies,
   serialization, and duplicate producers.
2. Add failing planner/executor tests proving eager candidate execution, named
   selected values, chained selects, and cycle rejection.
3. Add failing compiler and runner tests for
   `artifact == if(formula, artifact, artifact)` and for rejecting inline or
   nested selection.
4. Implement the smallest graph condition types, SelectNode validation,
   compilation lowering, plan instruction, and evaluator needed by those tests.
5. Capture clean-model conditional-authoring baselines, update only the
   `SKILL.md` conditional guidance, and rerun the same cases.
6. Run focused tests, full lint/type checks, relevant full suites, then request
   independent review before committing and opening a draft stacked PR.
