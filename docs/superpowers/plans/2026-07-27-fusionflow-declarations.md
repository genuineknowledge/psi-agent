# FusionFlow Declarations Implementation Plan

1. Add failing grammar/parser tests for the two declarations, then update the
   19-operator grammar to 21 operators and regenerate ANTLR 4.13.2 sources.
2. Add graph-compiler coverage proving both executor declarations remain
   residual for a future catalog/dispatcher.
3. Add failing execution-model tests for the sole `system_prompt` field and
   config payload; rename runtime callers while preserving per-call `prompt`.
4. Update the FusionFlow README, authoring skill, architecture document, and
   migration note for the two executor declarations.
5. Run focused tests, format/lint/type checks, the nested FusionFlow suite, and
   the repository test suite; review the final diff before publishing.
