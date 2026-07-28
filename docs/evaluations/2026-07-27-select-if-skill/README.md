# Select-if skill evaluation protocol

This directory freezes the eight prompts and structural oracle used to compare
three FusionFlow skill snapshots:

- `baseline`: the PR #36 skill before named-selection guidance;
- `candidate`: the first named-selection guidance draft;
- `candidate-v2`: the final guidance in this PR, including executable-backend
  guardrails.

Each snapshot ran all eight cases twice in fresh sessions: 16 samples per
snapshot and 48 generated samples in total.

## Frozen inputs

Before the first sample, record:

- the baseline and candidate Git revisions;
- the SHA-256 of each variant's complete `SKILL.md` snapshot;
- the SHA-256 of `cases.json` and the evaluation harness revision;
- the Python, psi-agent, lockfile, grammar, compiler, and graph-model revisions;
- the model settings from `cases.json` and the wall-clock start time.

Do not edit prompts, generation settings, or extraction between snapshots.
Every snapshot receives byte-identical user prompts. A provider-side model alias
can drift, so timestamps and raw responses are part of the frozen record.

## Run protocol

Use `deepseek-v4-flash` only through the official
`https://api.deepseek.com/v1` endpoint with `temperature=0` and
`max_tokens=32768`. For every `(variant, case, repetition)` sample:

1. Start a new psi-agent process with a new Session and unique session ID.
2. Load only that variant's frozen skill snapshot.
3. Send the case prompt as the only user turn.
4. Archive the exact request, raw SSE stream, final response, reasoning,
   extracted FusionFlow, process logs, exit status, timestamps, session
   workspace, and frozen-input metadata.
5. Run the oracle once against that untouched response.
6. Stop the process.

There are no retries, response repairs, follow-up turns, or selective reruns.
Infrastructure failures remain failed or missing samples. If an experiment must
be repeated, create a new complete run containing every sample for that
snapshot. All 48 recorded requests returned HTTP 200 with no stream or harness
error. The AI helper process return code is `1` because the harness terminates
the long-lived helper after each completed sample; it is not a sample failure.

The API key is supplied only through the process environment. Never print it,
place it in request metadata, include it in command history, or archive it.
The tracked archive layout is:

```text
runs/<snapshot>/
  inputs/{cases.json,FusionFlow.g4,SKILL.md,system_prompt.py,manifest.json}
  samples/<case-id>/r{1,2}/
    {request.json,final.md,reasoning.md,source.ff,metadata.json,result.json}
  summary.json
  raw-samples.zip
```

`raw-samples.zip` preserves the complete original sample directories, including
raw SSE, JSONL histories, logs, and temporary workspaces. Those high-volume
files are not duplicated unpacked. ZIP SHA-256 values and the score table are
recorded in `RESULTS.md`.

## Oracle correction

The first oracle version reused a strict example compiler catalog. It therefore
rejected otherwise legal `Executor` declarations and residual agent
configuration assertions, producing false negatives. A regression test was
written before changing the oracle. The corrected oracle builds its catalog
from grammar-documented signatures, uses the real parser and
`WorkflowGraphCompiler`, inspects graph lowering, and permits unrelated
residual configuration assertions.

No model response was regenerated or edited. Existing baseline and candidate
responses were rescored in place. Their original `summary.initial.json` and
per-sample `result.initial.json` files are retained, while `candidate-v2` used
the corrected oracle from the start. All reported automatic scores use the
corrected oracle.

## Automatic oracle

For positive cases, extract exactly one `fusionflow` fenced block and reject
extra prose or extra fences. Parse and compile that source through the same
FusionFlow graph compiler used by the executable runner; syntax-only parsing is
not a pass. Reject residual assertions. Inspect the compiled graph, not source
spelling, for each case's `oracle` fields:

- count named selector nodes and compute the longest selector-to-selector
  dependency chain;
- inspect condition trees for required logical or ordered comparison operators
  and literal operands;
- confirm every candidate Artifact is produced by its own Step, so candidates
  remain eager;
- confirm final consumers depend on selector outputs, including both independent
  outputs in P05;
- confirm the selector output itself is a workflow output in P06;
- confirm P07 contains no inline `if` under `consumes`.

For N01, do not attempt code extraction. Pass only a direct refusal with no
fenced block that explains that the eager backend cannot guarantee an
unselected handler uses zero tokens and does not substitute an eager
approximation.

Store each predicate and diagnostic separately in `result.json`; a sample passes
only when every predicate for its case passes.

## Blind audit

After automatic scoring, clean subagents independently audited the baseline and
final candidate from frozen prompts plus `final.md`, `source.ff`, and
`metadata.json`; they did not inspect automatic `result.json` files. This was
score-blind but not snapshot-label-blind, because run paths retained their
snapshot names. Auditors checked prompt compliance, executable semantics, and
the current backend boundary. The complete verdict ledger is preserved in
`BLIND_AUDIT.md`; no response was repaired after audit.
