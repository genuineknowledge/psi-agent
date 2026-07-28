# FusionFlow named conditional artifact selection

## Goal

Make the executable G4 shape below first-class:

```fusionflow
selected_result == if(condition, primary_result, fallback_result);
consumes(final_step) == [selected_result];
```

The output is a normal declared `Artifact`. Both candidate-producing Steps remain
eager; selection chooses a value and does not activate or skip branches.

## Graph model

`WorkflowGraph` gains immutable `SelectNode` values. A SelectNode owns one output
Artifact, references two candidate Artifacts, and carries a small serializable
condition tree:

- artifact and literal operands;
- `=`, `<`, `<=`, `>`, and `>=` comparison nodes; surface `!=` lowers to
  `NOT(eq)` rather than adding a separate comparison kind;
- `!`, `AND`, and `OR`.

The selected output counts as producer-backed. It cannot also have a Step
producer or another SelectNode producer. Candidate and condition Artifacts must
be globally available from workflow input, a Step, or an earlier SelectNode.

## Compilation

The graph compiler recognizes only a named Artifact equality whose other side is
an `if` term. Branches must be Artifact constants. Nested `if` branch terms are
not lowered; priority selection is written as multiple named intermediate
Artifact assertions. An `if` embedded directly in `consumes(...)` remains
invalid for the graph target.

## Execution

The plan contains ordinary Step invocations and explicit Select instructions.
Both are operations with deterministic IDs. A Select waits for all condition and
candidate producers, evaluates the condition, copies the chosen candidate value
to its output Artifact, and completes. Every Step is still invoked exactly once.
Cycles across Steps and SelectNodes fail before execution.

## Skill guidance

`SKILL.md` teaches the named intermediate shape, explicit List-valued
`consumes`, eager candidate execution, and multi-stage priority selection via
multiple named Artifacts. It does not imply lazy control flow.

## Out of scope

- branch activation, skipped Steps, regions, joins, or lazy evaluation;
- arbitrary arithmetic/function evaluation inside conditions;
- nested `if` terms as branch values;
- retries, persistence, or checkpoint changes.
