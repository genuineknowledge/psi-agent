# FusionFlow Next

FusionFlow Next is the example-local G4 parser/compiler and authoring Skill.
The declarative pipeline and the TypeScript-compatible Python execution
primitives share this package boundary but remain deliberately separate:
`fusion_flow_next.execution` does not execute G4 Core IR or `WorkflowGraph`.

## Modules

- `grammar/FusionFlow.g4`: the syntax grammar; ordinary preset/external-operator arity remains checker-owned.
- `test/test_grammar.py`: generated-parser and preset-signature contract checks.
- `fusion_flow_next/generated/`: committed ANTLR 4.13.2 Python lexer and parser generated from the grammar.
- `fusion_flow_next/contracts.py`: diagnostics and parse/check phase results.
- `fusion_flow_next/core_ir.py`: immutable Workflow Core IR shared by compiler phases.
- `fusion_flow_next/parser.py`: parser facade and Workflow Core IR output boundary.
- `fusion_flow_next/checker.py`: static semantics boundary.
- `fusion_flow_next/compiler.py`: target-neutral Core IR traversal and backend hook boundary.
- `fusion_flow_next/graph_compiler.py`: concrete `CoreIRCompiler` backend that builds `psi_agent.workflow_graph` models.
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

The language contract now covers file-level identity declarations, assertions, `!`/`AND`/`OR` formulas and comparisons, arithmetic, Lists, and value-producing `if(condition, then, else)` expressions. Workflow blocks contain assertions; a standalone Bool-returning operator call is shorthand for that call asserted equal to `True`. Concepts and operator signatures come from an external catalog. The 23 preset operators are split into four disjoint owner groups, and all four `*_multi` operators return ordinary List terms. `FusionFlow.g4` fixes `if` at three arguments while ordinary preset and externally registered operators keep flexible call arity for checker-owned validation.

For a compact, readable BNF and consistency with KEDispatcher, preset operators remain syntax sugar over the same flexible call rule instead of receiving separate arity-constrained grammar productions. After syntax parsing, the checker/catalog validates their arity and types. Because that information is intentionally not encoded structurally in the BNF, every preset operator in `FusionFlow.g4` documents its parameter types, return type, and explicit arity for human and agent readers; the grammar contract test enforces this documentation invariant.

The generated Python lexer and parser are committed under `fusion_flow_next/generated/` and wired into the handwritten Python Core IR visitor. Syntax failures return one-based, half-open source spans without partial Core IR. Repeated equivalent constant declarations reuse one identity, conflicting declarations fail, and every named or quoted constant must be declared with at least one concept before use. Numeric and Boolean literals use the KEDispatcher builtin symbols and concepts `ComplexNumber` and `Bool`, while quoted identifiers remain distinct from those literals. Standalone calls require a catalog output concept of `Bool` and become an ordinary `Assertion` against `True`; explicit `== True` remains equivalent. Formula equality becomes an `Assertion`, `!=` intentionally remains `NOT` over an `Assertion`, and ordered comparisons become the corresponding KEDispatcher `comparison_*_op` application asserted equal to `True`. `WorkflowFile` retains global declarations and multiple workflow blocks, while `IfTerm` retains conditional terms without approximation. Shorthand eligibility uses the catalog return concept; operator registration and arity, other catalog type compatibility, workflow legality, and backend support remain static-checker responsibilities.

The Core IR contains catalog-owned `Concept` and `Operator` references, typed constants, recursive compound and conditional terms, ordered list terms, equality assertions, and `NOT`/`AND`/`OR` formulas. `WorkflowFile` stores declarations and ordered workflow blocks; each `Workflow` stores one syntax-level block name with its assertions. The workflow does not redeclare concepts or operators.

`CoreIRCompiler` follows the same template-method design as KEDispatcher's shared Core IR compiler: `compile()` owns traversal, concrete backends override protected node hooks, unsupported nodes fail explicitly, and the compiler does not retain the supplied `WorkflowFile`.

`WorkflowGraphCompiler` uses that traversal directly. It reads the real Core IR
types, including `ListTerm.items` for all four `*_multi` operators, and returns
one `WorkflowGraphCompilation` per workflow. Recognized dependency assertions
become graph nodes, edges, or policy; unknown well-formed assertions remain in
`residual_assertions`; malformed recognized relations and unsupported recursive
terms fail explicitly. The graph is serializable, but the compilation is not a
replacement for the original Core IR.

Because `Assertion` is equality, one recognized graph call may appear on either
side. The backend normalizes that call before lowering and explicitly rejects an
equality containing recognized graph calls on both sides.

The package exports `WorkflowGraphCompiler`, `WorkflowGraphCompilation`, and
`WorkflowGraphCompilationError`.

This remains an example-local package rather than a wheel dependency. The
execution subpackage is a compatibility boundary, not the G4 runtime. Run all
tests from this directory so `fusion_flow_next` is on the runtime import path:

```powershell
uv run python -m pytest -q
```

Variables, quantifiers, truth formulas, theories, rules, and query/SAT/optimization requests are intentionally absent because the reviewed workflow surface does not use them. Operator execution, concept registries and matching, validation, parsing, backend compilation, and Haitun activation remain separate workstreams.

| Item | Intended contract | Current gap | Required compiler behavior |
| --- | --- | --- | --- |
| `S01` | `input_workflow` and `output_workflow` declare external artifacts. | The current runtime operations require values that are absent from those declarations. | The graph backend preserves the external-artifact relation, while the original Core IR remains authoritative for values; activation still requires a runtime value contract. |

## Activation boundary

Do not connect `fusion_flow_next.execution` to `SKILL.md`, workspace tools, or
the G4 graph runner merely because it now shares the correct package boundary.
That integration still requires an explicit Core IR / `WorkflowGraph` runtime
contract.

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
