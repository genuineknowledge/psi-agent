# FusionFlow Next

FusionFlow Next is a temporary name for an inactive compiler-architecture
package. The existing `SKILL.md` still targets the Node/TypeScript `.flow.ts`
runtime. The Python compiler modules are not connected to that skill, runtime,
or workspace tools, so existing `.flow.ts` behavior is unchanged.

## Modules

- `grammar/FusionFlow.g4`: the syntax grammar; ordinary preset/external-operator arity remains checker-owned.
- `test/grammar-contract.mjs`: preset-operator signature-comment contract check for the grammar.
- `fusion_flow_next/generated/`: committed ANTLR 4.13.2 Python lexer and parser generated from the grammar.
- `fusion_flow_next/contracts.py`: diagnostics and parse/check phase results.
- `fusion_flow_next/core_ir.py`: immutable Workflow Core IR shared by compiler phases.
- `fusion_flow_next/parser.py`: parser facade and Workflow Core IR output boundary.
- `fusion_flow_next/checker.py`: static semantics boundary.
- `fusion_flow_next/planning.py`: before workflow authoring, checks the syntax mappings declared for each planned step against the syntax names actually available. Each planned step maps to one catalog `Step` identity, which authoring expands into a typed constant and its assertions.
- `src/core-ir.ts`: the existing Workflow Core IR classes shared by the Node/TypeScript compiler prototype.
- `src/types.ts`: the existing source locations, diagnostics, and phase results.
- `src/parser.ts`, `src/checker.ts`, `src/generator.ts`, and `src/planning.ts`: the existing Node/TypeScript compiler boundaries.

The Node/TypeScript compiler prototype remains during the stacked Python
replacement and is removed only after its Python replacements are present.

## Current scope and known gaps

The language contract now covers file-level identity declarations, assertions, `!`/`AND`/`OR` formulas and comparisons, arithmetic, Lists, and value-producing `if(condition, then, else)` expressions. Workflow blocks contain assertions only; concepts and operator signatures come from an external catalog. The 23 preset operators are split into four disjoint owner groups, and all four `*_multi` operators return ordinary List terms. `FusionFlow.g4` fixes `if` at three arguments while ordinary preset and externally registered operators keep flexible call arity for checker-owned validation.

For a compact, readable BNF and consistency with KEDispatcher, preset operators remain syntax sugar over the same flexible call rule instead of receiving separate arity-constrained grammar productions. After syntax parsing, the checker/catalog validates their arity and types. Because that information is intentionally not encoded structurally in the BNF, every preset operator in `FusionFlow.g4` documents its parameter types, return type, and explicit arity for human and agent readers; the grammar contract test enforces this documentation invariant.

The generated Python lexer and parser are committed under `fusion_flow_next/generated/` and wired into the handwritten Python Core IR visitor. Syntax failures return one-based, half-open source spans without partial Core IR. Repeated equivalent constant declarations reuse one identity, conflicting declarations fail, and every named or quoted constant must be declared with at least one concept before use. Numeric and Boolean literals use the KEDispatcher builtin symbols and concepts `ComplexNumber` and `Bool`, while quoted identifiers remain distinct from those literals. Formula equality becomes an `Assertion`, `!=` intentionally remains `NOT` over an `Assertion`, and ordered comparisons become the corresponding KEDispatcher `comparison_*_op` application asserted equal to `True`. `WorkflowFile` retains global declarations and multiple workflow blocks, while `IfTerm` retains conditional terms without approximation. Operator registration and arity, catalog type compatibility, workflow legality, and backend support remain static-checker responsibilities.

The Core IR contains catalog-owned `Concept` and `Operator` references, typed constants, recursive compound and conditional terms, ordered list terms, equality assertions, and `NOT`/`AND`/`OR` formulas. `WorkflowFile` stores declarations and ordered workflow blocks; each `Workflow` stores one syntax-level block name with its assertions. The workflow does not redeclare concepts or operators.

Variables, quantifiers, truth formulas, theories, rules, and query/SAT/optimization requests are intentionally absent because the reviewed workflow surface does not use them. Operator execution, concept registries and matching, validation, parsing, backend compilation, and Haitun activation remain separate workstreams.

| Item | Intended contract | Current gap | Required compiler behavior |
| --- | --- | --- | --- |
| `S01` | `input_workflow` and `output_workflow` declare external artifacts. | The current runtime operations require values that are absent from those declarations. | A backend compiler must provide a lossless mapping or reject the workflow with `design_reference="S01"`; the Python path remains inactive until a backend implements that mapping. |

## Activation boundary

Do not connect the Python modules to the existing `SKILL.md`, workspace tools,
a prompt switch, or runner changes until the parser, checker, and compiler have
real implementations and runnable checks.

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
5. **Compiler** will own `fusion_flow_next/compiler.py`: lower checked Workflow Core IR through backend-specific hooks without selecting a target in the shared layer.
6. **Planning warnings** owns `fusion_flow_next/planning.py`: after Haitun lists planned steps and before it authors the DSL, check their declared syntax mappings and warn about missing or unavailable names. Each item is already at `Step` granularity; this phase does not introduce a higher-level requirement model and cannot detect steps that Haitun failed to list.
7. **Haitun integration** updates existing prompt and tool entry points only after parsing and checks work: add the syntax-check tool and require planning before workflow generation.
8. **Compatibility and migration** owns runnable checks and the activation gate: keep the existing `fusion-flow`, runner, and `.flow.ts` path unchanged until final migration.

Dependency order: 1 + 2 -> 3 -> 4 -> 5; 2 -> 6; 4 + 5 + 6 -> 7. Workstream 8 runs throughout and gates activation.
