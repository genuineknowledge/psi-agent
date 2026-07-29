# FusionFlow

FusionFlow is the workspace-local G4 parser/compiler, graph compiler, workflow
runner, and authoring Skill. Program-backed Steps use an injected Program runner
contract; the workspace entry point implements it with AnyIO subprocess
execution. Human-backed Steps use a dedicated instruction-preparation Agent,
the existing Haitun `clarify` flow, and a private checkpoint that crosses
conversation turns. The `fusion_flow.execution` compatibility package is
retained for historical parity tests but is not part of the active workspace
path.

Every active G4 run also writes each materialized Artifact to the workflow
bundle's `runs/<run-id>/artifacts/` directory. Text values remain Markdown;
objects, arrays, numbers, booleans, and null are represented by a fenced
`json` block. This user-visible history is separate from private Human resume
state under `.psi/fusion-flow/runs/`.

## Workspace integration

Reusable declarations use the fixed path
`flows/workflows/<slug>/<slug>.workflow`. Saving, listing, and loading are
upper-layer instructions implemented with existing file tools; this feature
does not add a workflow-management operator or manifest protocol.

The frontend reuse command is exactly:

```text
/workflow:<slug>
```

It accepts no suffix or inline parameters. The command maps to the canonical
path. Read the declaration and collect every declared input through normal
conversation before the initial `run_flow` call; never use a call with the
default empty input object as an input probe. Each initial call starts a fresh
run. If it reaches a Human Step, only the returned active request may continue
through `run_flow_resume`. An Agent Step may save a self-contained child
declaration but must not launch another workflow. Its relative
`read`/`write`/`edit` paths resolve against the psi workspace root, not the
launcher process CWD.

The registry includes three compact executable examples:
`single-step`, `sequential`, and `parallel-join`. The larger
`coscientist-ows` bundle demonstrates shared bundle-relative Markdown
instructions, Agent fan-out, multi-output Program Steps, and workspace Skill
and script assets without resource requirements.

## Modules

- `grammar/FusionFlow.g4`: the syntax grammar; ordinary preset/external-operator arity remains checker-owned.
- `test/test_grammar.py`: generated-parser, preset-signature, and authoring-Skill example contract checks.
- `fusion_flow/generated/`: committed ANTLR 4.13.2 Python lexer and parser generated from the grammar.
- `fusion_flow/contracts.py`: diagnostics and parse/check phase results.
- `fusion_flow/core_ir.py`: immutable Workflow Core IR shared by compiler phases.
- `fusion_flow/parser.py`: parser facade and Workflow Core IR output boundary.
- `fusion_flow/checker.py`: static semantics boundary.
- `fusion_flow/compiler.py`: target-neutral Core IR traversal and backend hook boundary.
- `fusion_flow/graph_compiler.py`: concrete `CoreIRCompiler` backend that builds `psi_agent.workflow_graph` models.
- `fusion_flow/workflow_runner.py`: fail-closed compile/plan/execute entry point with Agent, Human, Program, and checkpoint injection boundaries.
- `fusion_flow/artifact_store.py`: atomic, workflow-local Markdown persistence for every materialized G4 Artifact.
- `fusion_flow/job_store.py`: strict v2 JSON state plus non-blocking, OS-released advisory leases and an in-process guard for G4 runs waiting on Human input.
- `fusion_flow/planning.py`: before workflow authoring, checks the syntax mappings declared for each planned step against the syntax names actually available. Each planned step maps to one catalog `Step` identity, which authoring expands into a typed constant and its assertions.
- `fusion_flow/execution/`: inactive Python parity port of legacy `flow.*` primitives; `run_flow` does not import or dispatch through it.
- `test/test_graph_compiler.py`: real Core IR to WorkflowGraph compiler contract checks.
- `test/test_workflow_runner.py`: compile, plan, dependency, resource, and dispatch checks.
- `test/execution/`: parity regression tests for the inactive compatibility package.

The obsolete Node/TypeScript compiler prototype has been removed. The Python
compiler abstraction does not select or implement a concrete output target.
Runtime dependencies, including `antlr4-python3-runtime`, are declared in the
repository root `pyproject.toml` and locked by the root `uv.lock`; this Skill
has no independent npm install or per-Skill package lock.
`graph_compiler.py` is one concrete backend: it imports the generic graph model
from `psi_agent`, while `psi_agent.workflow_graph` does not import this workspace
package.

## Current scope and known gaps

The language contract now covers file-level identity declarations, assertions, `!`/`AND`/`OR` formulas and comparisons, arithmetic, Lists, JSON-style quoted text, and value-producing `if(condition, then, else)` expressions. Workflow blocks contain assertions; a standalone Bool-returning operator call is shorthand for that call asserted equal to `True`. Concepts and operator signatures come from an external catalog, so quoted text is accepted by the surface grammar while typed catalogs decide where text is valid. The 21 preset operators are split into five disjoint owner groups. The canonical dataflow operators `input_workflow(Workflow)`, `output_workflow(Workflow)`, `consumes(Step)`, and `produces(Step)` return ordinary List terms. Their artifact relation is always explicit on the RHS, including singleton forms such as `consumes(step) == [artifact]`; the removed `*_multi` spellings and former two-argument Bool relations have no compatibility aliases. `program_path` and `agent_system_prompt` remain typed executor configuration; a short `step_instruction` may contain quoted text and a longer one may use an explicit `./...` instruction-file reference. `FusionFlow.g4` fixes `if` at three arguments while ordinary preset and externally registered operators keep flexible call arity for checker-owned validation.

For a compact, readable BNF and consistency with KEDispatcher, preset operators remain syntax sugar over the same flexible call rule instead of receiving separate arity-constrained grammar productions. After syntax parsing, the checker/catalog validates their arity and types. Because that information is intentionally not encoded structurally in the BNF, every preset operator in `FusionFlow.g4` documents its parameter types, return type, and explicit arity for human and agent readers; the grammar contract test enforces this documentation invariant.

The generated Python lexer and parser are committed under `fusion_flow/generated/` and wired into the handwritten Python Core IR visitor. Syntax failures return one-based, half-open source spans without partial Core IR. Repeated equivalent constant declarations reuse one identity, conflicting declarations fail, and every named or quoted constant must be declared with at least one concept before use. Numeric and Boolean literals use the KEDispatcher builtin symbols and concepts `ComplexNumber` and `Bool`, while quoted identifiers remain distinct from those literals. Standalone calls require a catalog output concept of `Bool` and become an ordinary `Assertion` against `True`; explicit `== True` remains equivalent. Formula equality becomes an `Assertion`, `!=` intentionally remains `NOT` over an `Assertion`, and ordered comparisons become the corresponding KEDispatcher `comparison_*_op` application asserted equal to `True`. `WorkflowFile` retains global declarations and multiple workflow blocks, while `IfTerm` retains conditional terms without approximation. Shorthand eligibility uses the catalog return concept; operator registration and arity, other catalog type compatibility, workflow legality, and backend support remain static-checker responsibilities.

The Core IR contains catalog-owned `Concept` and `Operator` references, typed constants, recursive compound and conditional terms, ordered list terms, equality assertions, and `NOT`/`AND`/`OR` formulas. `WorkflowFile` stores declarations and ordered workflow blocks; each `Workflow` stores one syntax-level block name with its assertions. The workflow does not redeclare concepts or operators.

`CoreIRCompiler` follows the same template-method design as KEDispatcher's shared Core IR compiler: `compile()` owns traversal, concrete backends override protected node hooks, unsupported nodes fail explicitly, and the compiler does not retain the supplied `WorkflowFile`.

`WorkflowGraphCompiler` uses that traversal directly. It reads the real Core IR,
including `ListTerm.items` returned by the four canonical dataflow operators,
and returns one `WorkflowGraphCompilation` per workflow. Recognized dependency
assertions become graph nodes, edges, or typed policy. In addition to dataflow
and ordinary Step policy, the backend consumes `independent`,
`resource_requirement`, and the explicit control-order relation `depends_on`.
`depends_on` is a runner-registered typed catalog extension over the grammar's
generic operator-call syntax, not a new member of the grammar's 21 canonical
preset operators.
Unknown well-formed assertions remain in `residual_assertions`. A top-level
`selected == if(condition, artifact_a, artifact_b)` lowers to an eager
`SelectNode`; both candidates must be declared Artifacts and both producers run.
Downstream dataflow consumes `[selected]`. Priority selection uses named
intermediate Artifacts; inline or nested `if` terms fail closed.
The graph compiler preserves `program_path`, `agent_system_prompt`, and
`allowed_tool` as residual catalog/dispatcher configuration. The official
workflow runner consumes and validates `program_path`; `agent_system_prompt`
and `allowed_tool` remain unsupported residuals and stop execution. Malformed
supported relations and unsupported recursive terms fail explicitly. An
official execution entry point must reject any final residual rather than skip
or delete it. The graph is serializable, but the compilation is not a
replacement for the original Core IR.

Because `Assertion` is equality, one recognized graph call may appear on either
side. The backend normalizes that call before lowering and explicitly rejects an
equality containing recognized graph calls on both sides.

The package exports `WorkflowGraphCompiler`, `WorkflowGraphCompilation`, and
`WorkflowGraphCompilationError`.

The graph executor supports fixed resource pools supplied as positive
capacities or concrete instance IDs. It validates every requirement before
dispatch, atomically leases all resources needed by one Step, waits when
capacity is temporarily unavailable, and releases leases on success, failure,
timeout, or cancellation. Workflow `max_concurrency` and resource capacity both
apply.

The runner materializes every `./...` Step instruction through one injected
instruction resolver before dispatching any Step, caches shared references, and
passes the resulting text consistently to Agent, Human, and Program executors.
The public workspace adapter accepts UTF-8 Markdown files relative to the
containing `.workflow` file and rejects bundle escapes. If a validated
Agent-only file cannot be read, the adapter delegates its normalized
workspace-relative reference through that Agent's Step prompt; unreadable Human
or Program instructions remain errors. Materialized text is included in the
durable workflow-definition digest used across Human wait/resume turns. Short
inline Instruction text bypasses file resolution.

Each Agent Step receives a `submit_step_result` tool whose schema requires its
exact output Artifact IDs; a valid submission supplies the Step result, and the
ephemeral agent turn closes after the current tool-call batch. Plain text remains
a compatibility path only after a normally completed agent turn: the adapter
accepts one strict JSON object or one standalone, line-delimited `json` fence.
If parsing still fails and the Step has exactly one output, the original response
is bound to that Artifact verbatim and a structured warning is emitted without
logging the response body. Multi-output Steps receive two result-repair turns
first; if both fail, the first invalid response is broadcast verbatim to every
declared output and the same warning is emitted. This is deterministic copying,
not semantic splitting, and is the runtime default rather than an end-user
option. A zero-output Step may submit an exact empty object, but an invalid
response still fails after its repair turns because there is nowhere to bind raw
text. Truncated and tool-round-exhausted turns also fail instead of entering the
raw-text fallback. No fallback publishes only part of the declared result.

A Program executor must have exactly one `program_path(program) == path`
declaration. Absolute and explicit `./...` paths pass through; other path
identities require an injected resolver, and relative resolved paths require an
explicit working directory. The runner supplies only the resolved executable
in `argv`, sends `{"instruction": ..., "inputs": ...}` plus a newline on stdin,
and uses the Step ID as the invocation binding name. The injected Program
runner returns stdout. One produced Artifact keeps it as a scalar string;
multiple produced Artifacts require it to be one strict, finite JSON object
keyed exactly by those Artifact IDs. Non-standard constants such as `NaN` and
`Infinity`, numeric overflow to infinity, and non-finite values at any nested
depth are rejected, as are duplicate object keys.

The generic runner keeps the injected path contract from the original Program
implementation. The public Haitun workspace adapter applies the tighter
security boundary without a shell. On POSIX, it rejects every symbolic-link
component, pins both the working directory and a regular executable inside the
workspace, and uses a trusted isolated Python bootstrap to `fchdir()` and
execute those inherited descriptors. Native executables therefore remain
securely runnable without procfs. A shebang script requires `/proc/self/fd` or
`/dev/fd`, because the kernel must give its interpreter a descriptor-backed
script path; inside such a script, `$0` / `__file__` is that descriptor path
rather than the authored `program_path`. On Windows, the adapter keeps a
non-replaceable executable handle open through process completion after
validating the handle's final path. Native executables start directly; a
workspace `.py` Program starts with the trusted current interpreter in isolated
mode (`sys.executable -I`). Both paths remain shell-free and are covered by
Windows tests.

The adapter creates a separate POSIX process group or Windows Job Object and
performs shielded cleanup of that lifecycle boundary after normal direct-child
exit, failure, timeout, cancellation, or an output-limit violation. It streams
both output pipes with retained-output limits of 4 MiB for stdout and 1 MiB for
stderr. Set `PSI_FUSION_FLOW_PROGRAM_STDOUT_LIMIT_BYTES` or
`PSI_FUSION_FLOW_PROGRAM_STDERR_LIMIT_BYTES` to a positive integer to override
those defaults; crossing either limit terminates the process boundary. There
is no private 300-second Program cap: declared Step and workflow timeouts remain
the only execution deadlines. This lifecycle handling is not a filesystem or
host sandbox: only trusted workspace Programs may run, and a POSIX descendant
that deliberately creates a new session/process group leaves the managed group.

A Human executor keeps instruction preparation and actual user input separate.
The runner gives a contextual preparer the resolved instruction text,
consumed Artifact values, resource lease, and exact output IDs. The public
adapter runs that preparer in its own ephemeral Session with a workspace-bound,
read-only `read` tool, validates its exact
`question/options/recommended/default` JSON, and persists a
`HumanRequestSpec`. It does not build a second approval UI.

The initial `run_flow` call returns a `waiting_for_human` envelope under the
reserved `$fusion_flow/control` key, which cannot collide with a G4 Artifact
ID. The parent Session passes its nested request fields to the existing
`clarify` tool, shows that tool's formatted text verbatim, and ends the turn.
The next user message is JSON-encoded and submitted with the matching `run_id`
and `request_id` to `run_flow_resume`. The generic executor validates and
restores an `ExecutionCheckpoint`, skips completed Steps/selections, and
continues until final outputs or the next Human Step. The request text is never
an Artifact; the submitted choice, free text, or structured value is the Human
Step result.

Every `ExecutionCheckpoint` is bound to its non-empty `workflow_id` and a
SHA-256 `plan_digest` over a canonical serialization of both graph semantics
and explicit plan fibers. Checkpoint values accept only strict, finite JSON
types and compare recursively without Python coercions such as `True == 1`.
Resume also validates known and unique operation IDs, dependency closure, and
the exact materialized-value set. The public workspace resume boundary
separately hashes the current workflow definition, including resolved Markdown
instructions for new bundle-aware runs, and rejects a run when that digest
differs from its persisted definition digest. Legacy source-only state-v2 runs
resume with their original path-identity instruction semantics.

Checkpoint observers publish state before releasing dependent operations.
Human waits release resource leases and Session ownership; workflow and Step
timeouts restart for each resumed execution phase rather than including time
spent waiting for a person. A wait cancels unfinished parallel fibers, so an
uncheckpointed side-effecting Step can run again after resume; workflows should
not place such a Step concurrently with a Human frontier when exactly-once
effects matter.

Persisted Human-run documents use the strict state-v2 schema, including the
workflow/plan-bound checkpoint fields; incompatible versions and unknown or
missing fields fail closed.

Each run resume keeps an advisory lock file handle open for its lease. Lock-file
existence is not ownership: the kernel releases the lock when the holder closes
it or exits, including an abrupt process crash. The `.lockfile` suffix is
separate from the former `.lock` directories, so stale directories from an
earlier runtime cannot block upgraded runs. The job store therefore requires a
filesystem with working local advisory-lock semantics.

A process-local reservation guard complements that advisory lock so two
callers in the same process cannot both acquire a platform lock whose semantics
are process-scoped.

`independent(step)` is a non-binding scheduling hint and never overrides
Artifact or explicit control dependencies. `depends_on(step, predecessor)`
forces the first Step to wait for the second even when no Artifact flows
between them; repeat the relation for multiple predecessors. Declaration order
has no scheduling meaning.

`ForeachEdge`, `max_attempts != 1`, feedback/input-plus-producer graphs, and
circular Artifact or explicit control awaits remain fail-closed execution-plan
boundaries.

This remains a workspace-local package rather than a wheel dependency. The
execution subpackage is a compatibility boundary, not the G4 runtime. Run all
tests from this directory so `fusion_flow` is on the runtime import path:

```powershell
uv run python -m pytest -q
```

Resource pools stay outside `.workflow` source and are supplied by the
embedding tool or application as counts or concrete instance IDs.

Variables, quantifiers, truth formulas, theories, rules, and query/SAT/optimization requests are intentionally absent because the reviewed workflow surface does not use them. Operator execution, concept registries and matching, validation, parsing, backend compilation, and Haitun activation remain separate workstreams.

| Item | Intended contract | Current gap | Required compiler behavior |
| --- | --- | --- | --- |
| `S01` | `input_workflow` and `output_workflow` declare external artifacts. | No gap in the official graph runner. | The generic `WorkflowGraph` executor enforces the exact input/output boundary and adapts Program stdout at the injected dispatcher boundary. |

## Activation boundary

Keep Program execution behind the injected runner boundary: one resolved
executable path in `argv`, step instruction and consumed Artifacts as JSON
stdin, and stdout as the produced value. The workspace implementation uses the
pinned executable boundary and bounded, whole-process-tree AnyIO subprocess
lifecycle described above. Agent Steps continue through the injected contextual
completion boundary. Human Steps continue through contextual
preparation/request callbacks plus the generic checkpoint API; they do not
depend on `fusion_flow.execution`.

`AgentConfig.system_prompt` is the only Python field for an Agent's stable
system prompt. `AgentInvocation.prompt` remains the per-call prompt. The removed
`AgentConfig.system` / `AgentConfig.prompt` constructor spellings are not
compatibility aliases. Because the serialized config key changes to
`system_prompt`, an old cached Agent call may execute again after this migration.

The workspace activation path now points at this directory. `skills/fusion-flow/`
is the source of truth; the former Node/TypeScript Skill and `.flow.ts` runner
are no longer shipped.

`/workflow:<slug>` has explicit priority and resolves to
`flows/workflows/<slug>/<slug>.workflow`. It is an upper-layer command, not a
new operator.

## Regenerating the Python parser

Run ANTLR 4.13.2 from this directory:

```powershell
java -jar antlr-4.13.2-complete.jar -Dlanguage=Python3 -no-listener -Xexact-output-dir -o fusion_flow/generated grammar/FusionFlow.g4
```

Commit only `FusionFlowLexer.py` and `FusionFlowParser.py`; the generated `.interp` and `.tokens` metadata is not needed at runtime. CI pins the tool JAR by SHA-256, regenerates both Python files, and rejects drift. Grammar tests verify the committed runtime file set and importability. Ruff, ty, and Git whitespace exclusions apply only to the generated directory.

## Suggested work split

1. **Core IR contract** is defined in `fusion_flow/core_ir.py`; keep it limited to the reviewed workflow subset.
2. **Language contract** owns `grammar/FusionFlow.g4`; ordinary operator registration, arity, and types stay checker/catalog-owned.
3. **Parser** owns `fusion_flow/generated/` and `fusion_flow/parser.py`: report syntax errors and produce lossless Core IR for later stages.
4. **Static checker** owns the Python checker: validate workflow legality and backend-independent constraints.
5. **Compiler** owns `fusion_flow/compiler.py`: lower checked Workflow Core IR through backend-specific hooks without selecting a target in the shared layer.
6. **Workflow Graph backend** owns `fusion_flow/graph_compiler.py`: compile real Core IR through the shared hooks into the generic `psi_agent.workflow_graph` model while retaining residual assertions.
7. **Planning warnings** owns `fusion_flow/planning.py`: after Haitun lists planned steps and before it authors the DSL, check their declared syntax mappings and warn about missing or unavailable names. Each item is already at `Step` granularity; this phase does not introduce a higher-level requirement model and cannot detect steps that Haitun failed to list.
8. **Haitun integration** keeps the prompt, `run_flow`, and `flow_manage` entry points aligned with the G4 runtime.
9. **Compatibility** preserves the external `flow` Skill identity and natural-language UX while failing closed on legacy `.flow.ts` input.

Dependency order: 1 + 2 -> 3 -> 4 -> 5 -> 6; 2 -> 7; 4 + 5 + 7 -> 8. Workstream 9 runs throughout and gates activation.
