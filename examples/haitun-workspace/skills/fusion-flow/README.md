# FusionFlow

FusionFlow is the workspace-local G4 parser/compiler, graph compiler, workflow
runner, and authoring Skill. Agent-backed Steps reuse
`fusion_flow.execution.run()`, `flow.agent()`, and `flow.session()` through a
workspace `SessionRunner`. Private `RunContext`-free helpers in
`fusion_flow.execution.flow` provide `_retry_operation` and
`_run_parallel_tasks` to both the public Flow API and the G4 interpreter. The
graph interpreter retains graph readiness, Artifact/checkpoint/resume, ordered
foreach results, global admission, resources, timeout, and G4 error policy.
Program-backed Steps use an injected Program runner contract; the workspace
entry point implements it with a specialized Program Agent and structured
AnyIO `compile_program` / `execute_program` tools. The Agent can prepare or
install runtimes, dependencies, compilers, and other toolchain components for
multiple languages, while the host fixes or registers the authoritative launch
and captures its process result. Human-backed Steps use a dedicated
instruction-preparation Agent, the existing Haitun `clarify` flow, and a
private checkpoint that crosses conversation turns.

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
- `fusion_flow/workflow_graph/`: immutable Step-Artifact graph model, validation, and deterministic serialization.
- `fusion_flow/workflow_execution.py`: graph planning and interpretation, Artifact/checkpoint/resume state, global admission, resources, and timeouts; retry and parallel scheduling reuse the Flow helpers.
- `fusion_flow/graph_compiler.py`: concrete `CoreIRCompiler` backend that builds `fusion_flow.workflow_graph` models.
- `fusion_flow/workflow_runner.py`: fail-closed compile/plan/execute entry point with Agent, Human, Program, and checkpoint injection boundaries.
- `fusion_flow/artifact_store.py`: atomic, workflow-local Markdown persistence for every materialized G4 Artifact.
- `fusion_flow/job_store.py`: strict v2 JSON state plus non-blocking, OS-released advisory leases and an in-process guard for G4 runs waiting on Human input.
- `fusion_flow/planning.py`: before workflow authoring, checks the syntax mappings declared for each planned step against the syntax names actually available. Each planned step maps to one catalog `Step` identity, which authoring expands into a typed constant and its assertions.
- `fusion_flow/execution/`: Python `flow.*` runtime, historical compatibility surface, and private `RunContext`-free retry/parallel helpers. Public Flow combinators and the G4 interpreter share them, while the workspace adapter reuses `run`/`agent`/`session` for G4 Agent leaves; immediate control combinators are not G4 graph bytecode.
- `test/test_graph_compiler.py`: real Core IR to WorkflowGraph compiler contract checks.
- `test/test_workflow_runner.py`: compile, plan, dependency, resource, and dispatch checks.
- `test/execution/`: parity and shared Agent-session runtime regression tests.

The obsolete Node/TypeScript compiler prototype has been removed. The Python
compiler abstraction does not select or implement a concrete output target.
Runtime dependencies, including `antlr4-python3-runtime`, are declared in the
repository root `pyproject.toml` and locked by the root `uv.lock`; this Skill
has no independent npm install or per-Skill package lock.
`graph_compiler.py` is one concrete backend. The graph model and plan executor
remain internally decoupled from the parser/compiler/runner even though the
FusionFlow Skill owns all of them.

## Current scope and known gaps

The language contract now covers file-level identity declarations, assertions, `!`/`AND`/`OR` formulas and comparisons, arithmetic, Lists, JSON-style quoted text, and value-producing `if(condition, then, else)` expressions. Workflow blocks contain assertions; a standalone Bool-returning operator call is shorthand for that call asserted equal to `True`. Concepts and operator signatures come from an external catalog, so quoted text is accepted by the surface grammar while typed catalogs decide where text is valid. The 21 preset operators are split into five disjoint owner groups. The canonical dataflow operators `input_workflow(Workflow)`, `output_workflow(Workflow)`, `consumes(Step)`, and `produces(Step)` return ordinary List terms. Their artifact relation is always explicit on the RHS, including singleton forms such as `consumes(step) == [artifact]`; `foreach_item(Step, Artifact)` instead names one List-valued source Artifact and one Step-local item binding. The removed `*_multi` spellings and former two-argument Bool relations have no compatibility aliases. `program_path` and `agent_system_prompt` remain typed executor configuration; a short `step_instruction` may contain quoted text and a longer one may use an explicit `./...` instruction-file reference. `FusionFlow.g4` fixes `if` at three arguments while ordinary preset and externally registered operators keep flexible call arity for checker-owned validation.

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
The graph compiler preserves Program and Agent configuration as residual
catalog-owned configuration. The official workflow runner consumes and
validates `program_path` plus all seven Agent operators: `agent_config`,
`agent_system_prompt`, `allowed_tool`, `max_output_tokens`, `temperature`,
`reasoning_effort`, and `max_turns`. The workspace adapter appends the declared
system prompt to its fixed safety/output protocol, intersects `allowed_tool`
with the safe host registry, and forwards supported generation limits through
the shared session runtime. Because the current AI socket fixes provider
routing, a requested `model`, `engine`, or `api_base` is rejected explicitly
rather than silently ignored. Malformed supported relations, unsupported
recursive terms, and every final residual fail explicitly. The graph is
serializable, but the compilation is not a replacement for the original Core
IR.

Because `Assertion` is equality, one recognized graph call may appear on either
side. The backend normalizes that call before lowering and explicitly rejects an
equality containing recognized graph calls on both sides.

The package exports `WorkflowGraphCompiler`, `WorkflowGraphCompilation`, and
`WorkflowGraphCompilationError`.

The graph executor supports fixed resource pools supplied as positive
capacities or concrete instance IDs. It validates every requirement before
dispatch, atomically leases all resources needed by one Step attempt, waits
when capacity is temporarily unavailable, and releases leases on success,
failure, timeout, or cancellation. Workflow `max_concurrency` and resource
capacity both apply. `max_attempts` retries ordinary Step failures with a new
lease and timeout per attempt; cancellation and workflow control signals are
never retried.

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
explicit working directory. The generic runner supplies the declared path as
logical `argv[0]`, sends `{"instruction": ..., "inputs": ...}` plus a newline on
stdin, carries the materialized instruction, consumed Artifact mapping, exact
output IDs, resource lease, and Step ID in `ProgramInvocation`, and accepts an
injected runner result. The public workspace adapter resolves the working
directory and declared script to regular files inside the workspace. A script
does not need a POSIX executable bit, a shebang, or `chmod`: the Program Agent
may select or install an interpreter, compile source, and execute the resulting
command.

Each Program Step gets a fresh specialized Session. Its preparation tools are
limited to workspace inspection plus `bash`/`powershell`; it cannot launch
another workflow. Fidelity-mode interpreted execution does not accept an
Agent-authored complete argv. The Agent selects one interpreter executable, and
the host constructs exactly
`[interpreter, declared_script, *logical_argv[1:]]`; interpreter flags, inline
code, another script, reordered or omitted logical arguments, and extra
arguments are not accepted. For compiled languages the Agent must call
`compile_program`, which runs the compiler and atomically registers its compiler
argv, the declared source hash, every declared artifact hash, and one exact
launch argv. `execute_program` accepts that compiled launch only while the
source and artifacts still match their registered hashes. These structured
tools capture stdout bytes, stderr bytes, exit status, and launch errors
separately. Shell tools are for environment inspection and installation, not
for authoritative compilation or execution. The Agent ends by calling the
zero-argument `submit_program_result`, which deterministically normalizes the
authoritative captured attempt rather than accepting model-authored Artifact
values.

Program execution defaults to fidelity mode. Before the real Program starts,
the Agent may install a missing runtime, dependency, compiler, or toolchain and
retry an environment or preparation failure. Once the real Program launches,
it is the sole attempt: the Agent must not call `execute_program` again,
regardless of nonzero exit, invalid-input/domain error, or invalid output.
The original captured result or error must remain authoritative. The Agent also
must not patch or replace the declared script, alter declared inputs/stdin, or
reinterpret output. Adaptation is enabled only when the resolved Step
instruction contains this exact standalone line:

```text
Program execution policy: successful completion outranks fidelity.
```

No paraphrase or input/tool/output content enables it. An authorized script or
stdin adaptation must include a concrete `adaptation_reason`, and consumed input
Artifact values remain immutable.

For a successful attempt, zero-output Programs must write no stdout, one output
receives valid UTF-8 stdout verbatim, and multiple outputs require one strict,
finite JSON object keyed by all and only the declared Artifact IDs. Non-standard
constants such as `NaN` and `Infinity`, numeric overflow to infinity, nested
non-finite values, and duplicate object keys are rejected. A launch error,
nonzero exit, invalid UTF-8 stream, or output-contract failure produces the same
error value for every declared output:

```json
{
  "$fusion_flow/program_error": {
    "phase": "<input_format|agent|execution|output_format>",
    "kind": "<stable error kind>",
    "message": "<diagnostic>",
    "attempts": [
      {
        "argv": ["<actual>", "argv"],
        "exit_code": 1,
        "stdout": "partial output\n",
        "stderr": "failure detail\n",
        "stdout_base64": null,
        "stderr_base64": null,
        "error": null
      }
    ]
  }
}
```

A failing zero-output Program raises because no Artifact can carry the error.
Do not treat an error-valued Artifact as a repaired success.

`execute_program` creates a separate POSIX process group or Windows Job Object
and performs shielded cleanup after normal direct-child exit, failure, timeout,
cancellation, or an output-limit violation. It streams both output pipes with
retained-output limits of 4 MiB for stdout and 1 MiB for stderr. Set
`PSI_FUSION_FLOW_PROGRAM_STDOUT_LIMIT_BYTES` or
`PSI_FUSION_FLOW_PROGRAM_STDERR_LIMIT_BYTES` to a positive integer to override
those defaults; crossing either limit terminates the process boundary. There is
no private 300-second Program cap: declared Step and workflow timeouts remain
the only execution deadlines. The Program Agent and its environment-installation
shell access are a trusted-workspace boundary, not a filesystem or host sandbox;
a POSIX descendant that deliberately creates a new session/process group can
leave the managed group.

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
missing fields fail closed. The one compatibility exception is a state-v2
checkpoint written before per-iteration state existed: an omitted
`foreach_iterations` field is read as an empty tuple and is written explicitly
on the next save.

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

`ForeachEdge` dynamically expands Agent- or Program-backed Steps into durable
`step[index]` instances. Iterations are parallel by default and are limited only
by workflow `max_concurrency` and resource capacity. Normal outputs become
source-ordered Lists when every iteration succeeds; an empty source produces
empty Lists. Resources, timeout, retry, and successful checkpoint state belong
to each iteration. Ordinary failures do not cancel siblings: after all ordinary
iterations finish, their errors are raised together rather than materialized as
G4 Artifacts. Successful iteration checkpoints are reused on resume.
Human-backed foreach is rejected before dispatch until Human requests and
responses carry iteration identity.
Non-foreach Program Steps retain their existing `$fusion_flow/program_error`
error-valued Artifact compatibility. In foreach, that reserved Program failure
instead participates in per-item retry and then joins the aggregate exception.
Feedback/input-plus-producer graphs and circular Artifact or explicit control
awaits remain fail-closed execution-plan boundaries.

This remains a workspace-local package rather than a wheel dependency. The
execution subpackage is not the G4 graph interpreter, but it is an intentional
shared runtime boundary for Agent sessions and generic execution kernels. Run
all tests from this directory so `fusion_flow` is on the runtime import path:

```powershell
uv run python -m pytest -q
```

Resource pools stay outside `.workflow` source and are supplied by the
embedding tool or application as counts or concrete instance IDs.

Variables, quantifiers, truth formulas, theories, rules, and query/SAT/optimization requests are intentionally absent because the reviewed workflow surface does not use them. Operator execution, concept registries and matching, validation, parsing, backend compilation, and Haitun activation remain separate workstreams.

| Item | Intended contract | Current gap | Required compiler behavior |
| --- | --- | --- | --- |
| `S01` | `input_workflow` and `output_workflow` declare external artifacts. | No gap in the official graph runner. | The generic `WorkflowGraph` executor enforces the exact input/output boundary and normalizes the injected Program result at the dispatcher boundary. |

## Activation boundary

Keep Program execution behind the injected runner boundary: one declared
script path in logical `argv`, the resolved Step instruction and consumed
Artifacts as JSON stdin, and exact output Artifact IDs in `ProgramInvocation`.
The workspace implementation runs a specialized Program Agent for environment
preparation. In fidelity mode the host fixes interpreted argv from the selected
interpreter, declared script, and logical arguments; compiled launches cross
`compile_program` to bind source/artifact hashes and exact launch argv before
crossing the bounded, whole-process-tree `execute_program` boundary. Within one
Program attempt the real Program may launch only once, and its original result
or error remains authoritative. The script is a workspace-contained regular
file, not a pre-authorized executable, and needs no executable permission.
Agent Steps cross the injected contextual completion boundary through
`flow.agent()` and `flow.session()` inside one
`fusion_flow.execution.run()` per durable G4 run. Human Steps continue through
contextual preparation/request callbacks plus the generic checkpoint API;
their suspend/resume protocol remains graph-owned.

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
6. **Workflow Graph backend** owns `fusion_flow/graph_compiler.py`: compile real Core IR through the shared hooks into the `fusion_flow.workflow_graph` model while retaining residual assertions.
7. **Planning warnings** owns `fusion_flow/planning.py`: after Haitun lists planned steps and before it authors the DSL, check their declared syntax mappings and warn about missing or unavailable names. Each item is already at `Step` granularity; this phase does not introduce a higher-level requirement model and cannot detect steps that Haitun failed to list.
8. **Haitun integration** keeps the prompt, `run_flow`, and `flow_manage` entry points aligned with the G4 runtime.
9. **Compatibility** preserves the external `flow` Skill identity and natural-language UX while failing closed on legacy `.flow.ts` input.

Dependency order: 1 + 2 -> 3 -> 4 -> 5 -> 6; 2 -> 7; 4 + 5 + 7 -> 8. Workstream 9 runs throughout and gates activation.
