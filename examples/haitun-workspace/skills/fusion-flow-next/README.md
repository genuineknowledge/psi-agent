# FusionFlow Next

FusionFlow Next is a temporary name for an isolated compiler-architecture package.
This directory intentionally has no `SKILL.md`, so Haitun will not auto-load it.
Existing `fusion-flow` and `.flow.ts` paths remain unchanged.
This PR only establishes compiler architecture contracts.

## Modules

- `grammar/FusionFlow.g4`: syntax only.
- `src/types.ts`: diagnostics, phase results, and an opaque Workflow Core IR boundary.
- `src/parser.ts`: parser facade and opaque Workflow Core IR output boundary.
- `src/checker.ts`: static semantics and exact-lowering gate.
- `src/generator.ts`: checked workflow to deterministic TypeScript boundary.
- `src/planning.ts`: Haitun lists planned functions first and checks their required DSL syntax mappings.

## Current scope and known gaps

The committed grammar is a bootstrap surface: workflow blocks, operator-call assertions using assertion equality (`==`), identifiers, booleans, and ordered lists. It does not yet model declarations, numeric terms or numeric equality (`=`), formulas or rules, or the context-limited `consumes_multi` set from the language review. These belong to the language-contract workstream and must not be approximated by the parser or generator.

`WorkflowCoreIR` is intentionally opaque in this PR. A separate follow-up PR
will define its schema and decide how it aligns with KEDispatcher Core IR,
including workflow and list extensions and source-location metadata. This
package does not define a provisional IR schema in the meantime.

| Item | Intended contract | Current gap | Required compiler behavior |
| --- | --- | --- | --- |
| `S01` | `input_workflow` and `output_workflow` declare external artifacts. | Current `flow.input` requires a default value, while `flow.output` writes a supplied runtime value; the declarations do not contain those values. | The checker reports an error with `designReference: "S01"` and sets `canGenerate` to false until an exact runtime mapping exists. |

## Activation boundary

Do not add a `SKILL.md`, workspace tools, a prompt switch, or runner changes until the parser, checker, and generator have real implementations and runnable checks.

Integrate in this order: generated parser -> real functions and checks -> inactive or opt-in Haitun checker tool -> prompt opt-in -> replace legacy only after migration is complete. Existing `fusion-flow` remains the source of truth until the final migration.

## Suggested work split

1. **Core IR contract** is a separate follow-up PR: replace the opaque `WorkflowCoreIR` marker in `src/types.ts` with the KEDispatcher-aligned contract.
2. **Language contract** owns `grammar/FusionFlow.g4`: define supported syntax, including constructs that cannot yet be lowered exactly to the current runtime.
3. **Parser** owns `generated/` and `src/parser.ts`: generate the parser, report syntax errors, and produce the opaque `WorkflowCoreIR` output expected by later stages. Its concrete schema and construction logic follow the Core IR contract.
4. **Static checker** owns `src/checker.ts`: check workflow legality and reject constructs that cannot be lowered without changing meaning.
5. **TypeScript generator** owns `src/generator.ts`: convert checked Workflow Core IR into deterministic TypeScript and refuse unsupported input.
6. **Planning warnings** owns `src/planning.ts`: check the functions listed by Haitun and warn when a required DSL syntax mapping is missing.
7. **Haitun integration** updates existing prompt and tool entry points only after parsing and checks work: add the syntax-check tool and require planning before workflow generation.
8. **Compatibility and migration** owns runnable checks and the activation gate: keep the existing `fusion-flow`, runner, and `.flow.ts` path unchanged until final migration.

Dependency order: 1 + 2 -> 3 -> 4 -> 5; 2 -> 6; 4 + 5 + 6 -> 7. Workstream 8 runs throughout and gates activation.
