# FusionFlow Next

FusionFlow Next is a temporary name for an isolated compiler-architecture package.
This directory intentionally has no `SKILL.md`, so Haitun will not auto-load it.
Existing `fusion-flow` and `.flow.ts` paths remain unchanged.
This package establishes isolated compiler architecture and Core IR contracts.

## Modules

- `grammar/FusionFlow.g4`: syntax only.
- `src/core-ir.ts`: the concrete Workflow Core IR classes shared by parser, checker, and generator.
- `src/types.ts`: source locations, diagnostics, and parse/check/generate phase results.
- `src/parser.ts`: parser facade and Workflow Core IR output boundary.
- `src/checker.ts`: static semantics and exact-lowering gate.
- `src/generator.ts`: checked workflow to deterministic TypeScript boundary.
- `src/planning.ts`: Haitun lists planned functions first and checks their required DSL syntax mappings.

## Current scope and known gaps

The committed grammar is a bootstrap surface: workflow blocks, operator-call assertions using assertion equality (`==`), identifiers, booleans, and ordered lists. It does not yet model declarations, numeric terms or numeric equality (`=`), formulas or rules, or the workflow operator catalog. These belong to the language-contract workstream and must not be approximated by the parser or generator.

The Core IR contains catalog-owned `Concept` and `Operator` references, typed constants, recursive compound terms, ordered list terms, assertions, and `NOT`/`AND`/`OR` formulas. `Workflow` is the only workflow-level class and stores one syntax-level block name with its assertions. Constants are carried by the terms that use them rather than duplicated in a document-level collection. The workflow does not redeclare concepts or operators.

Variables, quantifiers, truth formulas, theories, rules, and query/SAT/optimization requests are intentionally absent because the reviewed workflow surface does not use them. Operator execution, concept registries and matching, validation, parsing, generation, and Haitun activation remain separate workstreams.

| Item | Intended contract | Current gap | Required compiler behavior |
| --- | --- | --- | --- |
| `S01` | `input_workflow` and `output_workflow` declare external artifacts. | Current `flow.input` requires a default value, while `flow.output` writes a supplied runtime value; the declarations do not contain those values. | The checker reports an error with `designReference: "S01"` and sets `canGenerate` to false until an exact runtime mapping exists. |

## Activation boundary

Do not add a `SKILL.md`, workspace tools, a prompt switch, or runner changes until the parser, checker, and generator have real implementations and runnable checks.

Integrate in this order: generated parser -> real functions and checks -> inactive or opt-in Haitun checker tool -> prompt opt-in -> replace legacy only after migration is complete. Existing `fusion-flow` remains the source of truth until the final migration.

## Suggested work split

1. **Core IR contract** is defined in `src/core-ir.ts`; keep it limited to the reviewed workflow subset.
2. **Language contract** owns `grammar/FusionFlow.g4`: define supported syntax, including constructs that cannot yet be lowered exactly to the current runtime.
3. **Parser** owns `generated/` and `src/parser.ts`: generate the parser, report syntax errors, and produce the concrete `Workflow` output expected by later stages.
4. **Static checker** owns `src/checker.ts`: check workflow legality and reject constructs that cannot be lowered without changing meaning.
5. **TypeScript generator** owns `src/generator.ts`: convert checked Workflow Core IR into deterministic TypeScript and refuse unsupported input.
6. **Planning warnings** owns `src/planning.ts`: check the functions listed by Haitun and warn when a required DSL syntax mapping is missing.
7. **Haitun integration** updates existing prompt and tool entry points only after parsing and checks work: add the syntax-check tool and require planning before workflow generation.
8. **Compatibility and migration** owns runnable checks and the activation gate: keep the existing `fusion-flow`, runner, and `.flow.ts` path unchanged until final migration.

Dependency order: 1 + 2 -> 3 -> 4 -> 5; 2 -> 6; 4 + 5 + 6 -> 7. Workstream 8 runs throughout and gates activation.
