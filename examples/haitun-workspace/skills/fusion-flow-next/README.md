# FusionFlow Next

FusionFlow Next is the example-local G4 parser/compiler and authoring Skill.
The declarative pipeline and the TypeScript-compatible Python execution
primitives share this package boundary but remain deliberately separate:
`fusion_flow_next.execution` does not execute G4 Core IR or `WorkflowGraph`.

## Modules

- `grammar/FusionFlow.g4`: the syntax grammar; ordinary preset/external-operator arity remains checker-owned.
- `test/test_grammar.py`: generated-parser, preset-signature, and authoring-Skill example contract checks.
- `fusion_flow_next/generated/`: committed ANTLR 4.13.2 Python lexer and parser generated from the grammar.
- `fusion_flow_next/contracts.py`: diagnostics and parse/check phase results.
- `fusion_flow_next/core_ir.py`: immutable Workflow Core IR shared by compiler phases.
- `fusion_flow_next/parser.py`: parser facade and Workflow Core IR output boundary.
- `fusion_flow_next/checker.py`: static semantics boundary.
- `fusion_flow_next/compiler.py`: target-neutral Core IR traversal and backend hook boundary.
- `fusion_flow_next/graph_compiler.py`: concrete `CoreIRCompiler` backend that builds `psi_agent.workflow_graph` models.
- `examples/run_workflow.py`: fail-closed compile/plan/execute entry point with legacy and contextual dispatcher contracts.
- `examples/run_deepseek.py`: live completion smoke-test CLI for the bundled tool-free examples.
- `fusion_flow_next/planning.py`: before workflow authoring, checks the syntax mappings declared for each planned step against the syntax names actually available. Each planned step maps to one catalog `Step` identity, which authoring expands into a typed constant and its assertions.
- `fusion_flow_next/execution/`: isolated Python port of the legacy TypeScript `flow.*` runtime, retained for parity and migration work without exposing it as `psi_agent` core API.
- `test/test_graph_compiler.py`: real Core IR to WorkflowGraph compiler contract checks.
- `test/execution/`: runtime, persistence, cancellation, subprocess, and pinned TypeScript differential tests for `fusion_flow_next.execution`.

The obsolete Node/TypeScript compiler prototype has been removed. The Python
compiler abstraction does not select or implement a concrete output target.
`graph_compiler.py` is one concrete backend: it imports the generic graph model
from `psi_agent`, while `psi_agent.workflow_graph` does not import this example
package.

## Current scope and known gaps

The language contract now covers file-level identity declarations, assertions, `!`/`AND`/`OR` formulas and comparisons, arithmetic, Lists, and value-producing `if(condition, then, else)` expressions. Workflow blocks contain assertions; a standalone Bool-returning operator call is shorthand for that call asserted equal to `True`. Concepts and operator signatures come from an external catalog. The 21 preset operators are split into five disjoint owner groups. The canonical dataflow operators `input_workflow(Workflow)`, `output_workflow(Workflow)`, `consumes(Step)`, and `produces(Step)` return ordinary List terms. Their artifact relation is always explicit on the RHS, including singleton forms such as `consumes(step) == [artifact]`; the removed `*_multi` spellings and former two-argument Bool relations have no compatibility aliases. `program_path` and `agent_system_prompt` declare executor catalog identities without embedding commands or prompt bodies in the grammar. `FusionFlow.g4` fixes `if` at three arguments while ordinary preset and externally registered operators keep flexible call arity for checker-owned validation.

For a compact, readable BNF and consistency with KEDispatcher, preset operators remain syntax sugar over the same flexible call rule instead of receiving separate arity-constrained grammar productions. After syntax parsing, the checker/catalog validates their arity and types. Because that information is intentionally not encoded structurally in the BNF, every preset operator in `FusionFlow.g4` documents its parameter types, return type, and explicit arity for human and agent readers; the grammar contract test enforces this documentation invariant.

The generated Python lexer and parser are committed under `fusion_flow_next/generated/` and wired into the handwritten Python Core IR visitor. Syntax failures return one-based, half-open source spans without partial Core IR. Repeated equivalent constant declarations reuse one identity, conflicting declarations fail, and every named or quoted constant must be declared with at least one concept before use. Numeric and Boolean literals use the KEDispatcher builtin symbols and concepts `ComplexNumber` and `Bool`, while quoted identifiers remain distinct from those literals. Standalone calls require a catalog output concept of `Bool` and become an ordinary `Assertion` against `True`; explicit `== True` remains equivalent. Formula equality becomes an `Assertion`, `!=` intentionally remains `NOT` over an `Assertion`, and ordered comparisons become the corresponding KEDispatcher `comparison_*_op` application asserted equal to `True`. `WorkflowFile` retains global declarations and multiple workflow blocks, while `IfTerm` retains conditional terms without approximation. Shorthand eligibility uses the catalog return concept; operator registration and arity, other catalog type compatibility, workflow legality, and backend support remain static-checker responsibilities.

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
`program_path`, `agent_system_prompt`, and `allowed_tool` remain residual for a
future catalog/dispatcher. Malformed supported relations and unsupported
recursive terms fail explicitly. An official execution entry point must reject
any final residual rather than skip or delete it. The graph is serializable,
but the compilation is not a replacement for the original Core IR.

Because `Assertion` is equality, one recognized graph call may appear on either
side. The backend normalizes that call before lowering and explicitly rejects an
equality containing recognized graph calls on both sides.

The package exports `WorkflowGraphCompiler`, `WorkflowGraphCompilation`, and
`WorkflowGraphCompilationError`.

The one-shot executor supports fixed resource pools supplied as positive
capacities or concrete instance IDs. It validates every requirement before
dispatch, atomically leases all resources needed by one Step, waits when
capacity is temporarily unavailable, and releases leases on success, failure,
timeout, or cancellation. Workflow `max_concurrency` and resource capacity both
apply.

`independent(step)` is a non-binding scheduling hint and never overrides
Artifact or explicit control dependencies. `depends_on(step, predecessor)`
forces the first Step to wait for the second even when no Artifact flows
between them; repeat the relation for multiple predecessors. Declaration order
has no scheduling meaning.

`ForeachEdge`, `max_attempts != 1`, feedback/input-plus-producer graphs, and
circular Artifact or explicit control awaits remain fail-closed execution-plan
boundaries.

This remains an example-local package rather than a wheel dependency. The
execution subpackage is a compatibility boundary, not the G4 runtime. Run all
tests from this directory so `fusion_flow_next` is on the runtime import path:

```powershell
uv run python -m pytest -q
```

The bundled `single_step`, `sequential`, and `parallel_join` workflows can be
run with the DeepSeek smoke-test CLI:

```bash
export DEEPSEEK_API_KEY=...
uv run python -m examples.run_deepseek \
  examples/single_step.workflow \
  --inputs-file examples/single_step.inputs.json \
  --strict-executors
```

Resource pools stay outside `.workflow` source. Supply either counts or
concrete instance IDs:

```bash
--resource-capacities '{"gpu_device": 2}'
--resource-capacities '{"gpu_device": ["cuda:0", "cuda:1"]}'
```

The DeepSeek CLI is intentionally a completion-only smoke runner. Resource-aware
workflows must also supply capacities or a configured `ResourceAllocator`.

Variables, quantifiers, truth formulas, theories, rules, and query/SAT/optimization requests are intentionally absent because the reviewed workflow surface does not use them. Operator execution, concept registries and matching, validation, parsing, backend compilation, and Haitun activation remain separate workstreams.

| Item | Intended contract | Current gap | Required compiler behavior |
| --- | --- | --- | --- |
| `S01` | `input_workflow` and `output_workflow` declare external artifacts. | The compatibility-only `fusion_flow_next.execution` operations are not wired to those declarations. | The generic `WorkflowGraph` executor already enforces the exact input boundary; connecting the compatibility execution package still requires a separate runtime value contract. |

## Activation boundary

Do not connect `fusion_flow_next.execution` to `SKILL.md`, workspace tools, or
the G4 graph runner merely because it now shares the correct package boundary.
That integration still requires an explicit Core IR / `WorkflowGraph` runtime
contract.

`AgentConfig.system_prompt` is the only Python field for an Agent's stable
system prompt. `AgentInvocation.prompt` remains the per-call prompt. The removed
`AgentConfig.system` / `AgentConfig.prompt` constructor spellings are not
compatibility aliases. Because the serialized config key changes to
`system_prompt`, an old cached Agent call may execute again after this migration.

Integrate in this order: generated parser -> real functions and checks -> inactive or opt-in Haitun checker tool -> prompt opt-in -> replace legacy only after migration is complete. Existing `fusion-flow` remains the source of truth until the final migration.

## Regenerating the Python parser

Run ANTLR 4.13.2 from this directory:

```powershell
java -jar antlr-4.13.2-complete.jar -Dlanguage=Python3 -no-listener -Xexact-output-dir -o fusion_flow_next/generated grammar/FusionFlow.g4
```

Commit only `FusionFlowLexer.py` and `FusionFlowParser.py`; the generated `.interp` and `.tokens` metadata is not needed at runtime. CI pins the tool JAR by SHA-256, regenerates both Python files, and rejects drift. Grammar tests verify the committed runtime file set and importability. Ruff, ty, and Git whitespace exclusions apply only to the generated directory.

## Suggested work split

1. **Core IR contract** is defined in `fusion_flow_next/core_ir.py`; keep it limited to the reviewed workflow subset.
2. **Language contract** owns `grammar/FusionFlow.g4`; ordinary operator registration, arity, and types stay checker/catalog-owned.
3. **Parser** owns `fusion_flow_next/generated/` and `fusion_flow_next/parser.py`: report syntax errors and produce lossless Core IR for later stages.
4. **Static checker** owns the Python checker: validate workflow legality and backend-independent constraints.
5. **Compiler** owns `fusion_flow_next/compiler.py`: lower checked Workflow Core IR through backend-specific hooks without selecting a target in the shared layer.
6. **Workflow Graph backend** owns `fusion_flow_next/graph_compiler.py`: compile real Core IR through the shared hooks into the generic `psi_agent.workflow_graph` model while retaining residual assertions.
7. **Planning warnings** owns `fusion_flow_next/planning.py`: after Haitun lists planned steps and before it authors the DSL, check their declared syntax mappings and warn about missing or unavailable names. Each item is already at `Step` granularity; this phase does not introduce a higher-level requirement model and cannot detect steps that Haitun failed to list.
8. **Haitun integration** updates existing prompt and tool entry points only after parsing and checks work: add the syntax-check tool and require planning before workflow generation.
9. **Compatibility and migration** owns runnable checks and the activation gate: keep the existing `fusion-flow`, runner, and `.flow.ts` path unchanged until final migration.

Dependency order: 1 + 2 -> 3 -> 4 -> 5 -> 6; 2 -> 7; 4 + 5 + 7 -> 8. Workstream 9 runs throughout and gates activation.
