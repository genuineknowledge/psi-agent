# FusionFlow Declarations Implementation Plan

1. Add failing grammar/parser tests for the three declarations, then update the
   19-operator grammar to 22 operators and regenerate ANTLR 4.13.2 sources.
2. Add failing graph-compiler tests for `ValueSource`, implicit input semantics,
   deterministic ordering, conflicts, unused values, and residual executor
   declarations; implement the minimum lowering and export.
3. Add failing execution-model tests for the sole `system_prompt` field and
   config payload; rename runtime callers while preserving per-call `prompt`.
4. Update the FusionFlow README, authoring skill, architecture document, and
   migration note without claiming runtime delivery support.
5. Run focused tests, format/lint/type checks, the nested FusionFlow suite, and
   the repository test suite; review the final diff before publishing.
