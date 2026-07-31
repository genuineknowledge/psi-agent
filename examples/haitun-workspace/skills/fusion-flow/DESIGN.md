# FusionFlow G4 durable execution design

## Status and scope

This document defines the runtime contract for:

- interpreting the G4 workflow graph while reusing the Python Flow execution
  helpers instead of duplicating retry and parallel scheduling;
- dispatching Agent Steps through the Python `flow.agent()` / `flow.session()` API;
- executing `foreach_item` as durable per-item Step instances;
- applying Step resources, timeouts, retries, output validation, and checkpoints to
  those instances.

The existing G4 `if(condition, true_artifact, false_artifact)` remains an eager
value selector. Lazy conditional subgraphs are deliberately out of scope and are
tracked in [msg-bq/psi-agent#80](https://github.com/msg-bq/psi-agent/issues/80).

## Runtime layers

The G4 runtime has four semantic layers:

1. The parser and compiler lower declarations into an immutable graph and
   catalog-owned executor configuration.
2. The graph interpreter reuses the existing, `RunContext`-free
   `_retry_operation` and `_run_parallel_tasks` helpers from
   `fusion_flow.execution.flow`.
3. The durable graph interpreter owns graph readiness and interpretation,
   Artifact state, checkpoint/resume, global admission, resources, and
   workflow/Step timeout. It wraps the shared helpers with G4 policy and
   persistence instead of implementing a second scheduler.
4. Leaf executor adapters run Agent, Program, or Human work. The Agent adapter
   uses `flow.agent()` and `flow.session()` rather than implementing another
   session protocol.

Public `flow.parallel()`, `flow.if_()`, `flow.for_each()`, and `flow.retry()` are
immediate Python callback combinators, not graph bytecode. The G4 interpreter
does not mechanically lower graph instructions into those public calls.
Where their semantics overlap, the public Flow API and the G4 interpreter call
the same private retry/parallel helpers and add their own policy and state.

## Shared execution kernel contract

The shared helpers must be callable without an active `execution.run()` and
must not call `current_run_context()` or know about Flow bindings/traces,
WorkflowGraph types, Artifacts, checkpoints, resources, or executor kinds.

The kernel provides mechanisms rather than one hard-coded policy:

- retry sequencing reports a one-based attempt and accepts explicit retry
  classification, so cancellation, Human suspension, and execution invariants
  can escape without being retried;
- `_run_parallel_tasks` provides structured parallel execution, join, and an
  optional bounded startup window; it propagates self-cancellation and drains
  the active window so cleanup failures are not lost, while callers own their
  error and persistence policy.

The public Flow API adds its active-run requirement, traces and existing
TypeScript-compatible fail-fast/retry-backoff behavior around the shared retry
and parallel helpers. The G4 interpreter adds per-attempt admission, resources,
timeout and output validation, plus per-iteration checkpoints and ordered
Artifact publication.

Sharing the mechanism must not collapse the contracts. Public Flow remains
fail-fast. G4 foreach lets ordinary iterations finish, then raises their errors
together; cancellation, control signals and invariants still escape promptly.

## Agent Step contract

The compiler consumes every Agent-owned G4 declaration and produces one immutable
runtime configuration per Agent identity:

- `agent_config(Agent, Model, Engine, ApiBase)`;
- `agent_system_prompt(Agent)`;
- `allowed_tool(Agent, Tool)`;
- `max_output_tokens(Agent)`;
- `temperature(Agent)`;
- `reasoning_effort(Agent)`;
- `max_turns(Agent)`.

Unknown, duplicate, type-invalid, or unsupported routing declarations fail
closed. A declaration must never parse successfully and then be silently
ignored.

The workspace adapter wraps one complete G4 workflow execution in one shared
Python `execution.run()` runtime and injects a production `SessionRunner`. Each Agent executor
has one immutable `AgentHandle`; every logical Step instance calls:

```python
await flow.session(
    handle,
    prompt,
    context=serialized_invocation_context,
    binding_name=stable_invocation_id,
)
```

The injected runner retains the current workspace `SessionAgent` tool loop,
non-recursion denylist, resource context, and exact `submit_step_result` schema.
It must validate and normalize all declared Artifact outputs before returning a
successful `SessionResult`. Therefore `flow.session()` can never commit an
invalid structured result as a successful binding.

The fixed Step safety/output protocol and the declared Agent system prompt are
separate layers. User configuration may augment the fixed protocol but may not
remove the non-recursion rule, workspace boundary, or exact-output contract.
`allowed_tool` is intersected with the host-safe workspace registry; it cannot
re-enable a denied workflow launcher.

The Flow binding is a validated invocation-result journal. The G4 Artifact set
and checkpoint remain the authoritative workflow state. The durable commit
order is:

1. execute the Agent and tools;
2. validate the exact output object;
3. commit the Flow binding;
4. commit G4 Artifacts;
5. persist the G4 checkpoint.

If a process stops after step 3, resume may reuse the validated binding and
complete steps 4-5 without rerunning tools.

## Program and Human adapter boundary

Program and Human remain leaf adapters because their semantics are not
equivalent to existing public Flow primitives.

A Program Step is not a direct `flow.exec()` call. Its adapter retains the
specialized Program Agent, compile/repair authorization, source and compiled
artifact provenance, workspace/argv/stdin restrictions, strict JSON Artifact
contract, separate stdout/stderr limits, and process-tree cleanup. Replacing
that adapter with `flow.exec()` would lose observable behavior.

A Human Step is not a `flow.call()` service. Its workspace adapter prepares the
question, raises a workflow control signal, commits the G4 checkpoint and
waiting request, returns control to the parent Session, and later resumes by
`run_id` / `request_id`. There is no equivalent Human/checkpoint primitive in
the public Flow API.

`workflow_execution` therefore sees Program and Human only through the generic
dispatcher boundary. Neither their leaf implementation nor their workspace
persistence belongs in the shared execution kernel.

## Stable Step-instance identity

A logical invocation identity includes:

```text
plan digest / Step ID / expansion path
```

For a non-foreach Step the expansion path is empty. For foreach it contains the
zero-based item index; future nested expansion appends another segment. The
retry attempt is recorded separately and does not change the logical result
binding.

The cache fingerprint includes the Agent configuration, normalized prompt and
inputs, output schema, allowed-tool set, and runner/adapter protocol version.
Changing any of those values invalidates an old result.

## Foreach language contract

`foreach_item(step, source) == item_binding` declares that `source` is an
Artifact whose runtime value must be a List, and that `step` is a template
expanded once for each member of that List. Iterations run in parallel by
default. Only workflow `max_concurrency` and available resource instances limit
how many are active; foreach has no separate scheduling declaration.

## Foreach execution and results

The source value must be a finite JSON List. Expansion creates logical
`step[index]` instances. Every instance receives the normal consumed global
Artifacts plus the local item binding. It uses the same executor dispatcher as
a non-foreach Step.

Each declared normal output Artifact becomes a List ordered by source index,
not completion order; an empty source produces empty output Lists. Outputs are
published only if every iteration succeeds. Ordinary exhausted failures do not
cancel siblings: all ordinary iterations reach a terminal result, then the
interpreter raises their errors together. Those errors are exceptions, not G4
Artifacts, so downstream graph operations do not run after a failed foreach.

## Per-iteration Step policy

Foreach itself does not own a second resource, timeout, or retry policy. Each
item is a normal Step instance and inherits:

- `resource_requirement`: acquired and released per attempt;
- `step_timeout`: applied per attempt;
- `max_attempts`: applied independently per item.

Resources are released before another attempt. If a caller configures retry
backoff, it must happen outside the lease. Cancellation is never retried. The
workflow timeout remains an outer bound over all iterations.
The shared retry and parallel helpers drive these mechanisms; the interpreter
wraps each attempt with admission, resources, timeout and validation.

After an item exhausts its attempts, ordinary executor/validation/timeout
failures join the final aggregate exception. Workflow control and invariant
failures still abort or suspend immediately:

- cancellation or workflow timeout;
- Human-input suspension;
- malformed graph, plan, checkpoint, or source type;
- checkpoint persistence failure;
- allocator invariant failure.

Outside foreach, a workspace Program keeps the existing
`$fusion_flow/program_error` error-valued Artifact contract.
Inside foreach, that same reserved Program result is an iteration failure: it
participates in the Step's `max_attempts`, then joins the aggregate exception.

Agent- and Program-backed Steps are supported as foreach templates. A
Human-backed foreach fails during runner preflight in this version. The
existing Human resume document identifies only the base Step, not one expanded
iteration, and parallel Human iterations could otherwise race to publish
multiple active requests. Supporting it requires an iteration-aware request
queue and response checkpoint contract; it must not be approximated by
waiting for every Human suspension as an ordinary item failure.

## Partial checkpoint and resume

The checkpoint stores the exact source Artifact and each terminal iteration.
Successful records contain validated outputs; failed records contain durable
diagnostic summaries while the live aggregate retains the original exception
objects. Resume validates the source plus plan-bound Step/index identities, so
a second source digest is unnecessary.

On resume:

- succeeded items are not invoked again;
- failed and pending items run;
- an item that was running during interruption returns to pending unless its
  validated Flow binding can be reused.

The Step is marked complete only after every iteration succeeds and ordered
outputs are published. A successful iteration checkpoint prevents that work
from being repeated after a sibling failure or interruption.

## Existing eager selection

The current `SelectNode` waits for its condition operands and both candidate
Artifacts, then copies one already-materialized value. Both candidate producers
remain executable. It is not lowered through `flow.if_()` and this change does
not alter its behavior.

## Compatibility and validation gates

The implementation is complete only when tests prove:

- where semantics overlap, public Flow and G4 paths exercise the same
  `RunContext`-free `_retry_operation` and `_run_parallel_tasks` helpers without
  changing their distinct policies;
- the shared helpers work without an active `execution.run()` context;
- every Agent-owned grammar operator is consumed or rejected explicitly;
- the production `SessionRunner` honors the normalized Agent configuration;
- invalid Agent output is never committed to a Flow binding;
- foreach preserves input order under out-of-order completion;
- empty foreach produces empty output Lists;
- workflow concurrency and resource limits compose;
- resource leases, timeout, and retry are per item;
- ordinary item failures do not cancel siblings and are raised together after
  all ordinary iterations finish;
- successful item checkpoints resume without rerunning those items;
- iteration failures are not G4 Artifacts;
- cancellation and Human suspension are not aggregated as ordinary errors;
- Human-backed foreach is rejected before any item is dispatched;
- checkpoint resume invokes only unfinished items;
- existing eager select behavior remains unchanged.
