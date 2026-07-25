# FusionFlow Reference Alignment

## Goal

Make PR #15 match the observable FusionFlow behavior in `fusion-flow-src.zip`
where that behavior is part of the runtime or artifact contract, while keeping
the repository's Python microkernel and AnyIO boundaries.

## Behaviors to align

1. `session()` and `call()` reserve a call ordinal provisionally. A resume hit
   or a successfully persisted result commits it; failures release it so retry
   probes the same historical binding.
2. A resume miss for an explicit binding may replace the old resume artifact.
   A second write to that explicit name in the current run still fails.
3. `exec()` starts timeout and stdout/stderr consumption before sending stdin.
4. A finite stdout limit terminates the process as soon as the limit is crossed,
   returns the retained prefix as a truncated success, and persists an explicit
   truncation marker. `0` and positive infinity disable the limit.
5. `progress.jsonl` appends paired `node_start` and `node_end` records with an
   `event` field for every traced node.
6. Token aggregation exposes separate `user` and `internal` buckets as well as
   their combined totals.
7. `RunContext.flow` exposes the same package-level `flow` object.
8. When `program_path` is omitted, `run()` best-effort snapshots the Python
   entry script from `sys.argv[0]`.
9. `Agent()` accepts an explicit `SessionRunner` for calls outside `run()`.

## Intentional Python differences

1. Every `parallel()` mode keeps AnyIO structured concurrency. On failure or
   after selecting `first`/`any`, siblings are cancelled and awaited. This
   prevents detached callbacks from writing bindings after the selected result
   or after the run is sealed. Callbacks are therefore required to cooperate
   with cancellation.
2. `Agent()` does not select or embed a provider. Provider ownership remains in
   the injected `SessionRunner`, preserving the framework's microkernel
   boundary.
3. Public Python configuration remains idiomatic `snake_case`:
   `thinking_budget_tokens`, `minimum`, and `maximum`. No duplicate camelCase
   constructor surface is added.

These differences are retained for state integrity and component ownership,
not as migration shortcuts.

## Verification

- Add regression tests that fail against the current PR implementation for
  every aligned behavior.
- Keep explicit tests for the three intentional Python differences.
- Run FusionFlow tests, Ruff formatting/lint, `ty check`, and the full test
  suite.
- Have a fresh review agent compare the final implementation and tests against
  both this specification and the extracted TypeScript reference.
