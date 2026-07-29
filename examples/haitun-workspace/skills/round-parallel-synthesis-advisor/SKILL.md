---
name: round-parallel-synthesis-advisor
description: >
  Use after Stage05, Stage06, or Stage07 has produced reliable overall-water-splitting
  catalyst recommendations and the workflow needs one round-level ChemSkills
  synthesis route for all retained catalysts on a single 96-well quartz plate.
  Use for Stage08 round-parallel synthesis handoff, one-plate source-liquid
  budgeting, source-liquid preparation methods, catalyst retention/blocking
  under the authoritative source-liquid limit, and model-written ChemSkills
  route content.
---

# round-parallel-synthesis-advisor

## Overview
Generate a round-level Stage08 synthesis route companion for reliable
photocatalysts or Z-scheme systems. This skill replaces per-catalyst route
generation in the closed-loop workflow with one single-plate parallel synthesis
route for the current round, while keeping ChemSkills parameter files in the
workflow-level cumulative route.

The helper script creates only empty route artifacts. The agent must write all
chemistry, feasibility decisions, retained/blocked catalyst decisions, operation
inputs, ChemSkills execution-spec content, and cumulative CSV parameter
contents.

When the project `synthesis_advisor` subagent owns this stage, it may run the
empty-shell helper and complete the model-written Stage08 route artifacts. It
must return retained/blocked counts, source-liquid count, violated limitation
IDs, and artifact paths to the parent agent.

## Required References
Before writing the route content, read:

```text
references/round_route_design.md
references/round_output_contract.md
references/source_liquid_preparation_methods.md
```

Also read the current round's `SYNTHESIS_INPUT_SUMMARY.json`,
`data/laboratory-limitations/laboratory_limitations_for_agent.json`,
`data/chem-skills/README.md`, and only the ChemSkills station files/templates
required by the final route. This is a hard gate: do not retain/block records,
choose operations, write route prose, or write CSV parameters until these
authoritative files have been read. Record the paths in
`ROUND_PARALLEL_SYNTHESIS_INDEX.json`.

## Inputs
The workflow input is:

```text
<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/SYNTHESIS_INPUT_SUMMARY.json
```

`records` may contain `system: single` and/or `system: zscheme` records prepared
by Stage05 and Stage06. Treat these records as one round-level candidate pool,
not as independent route files.

## Outputs
Write exactly one round route markdown:

```text
<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/ROUND_PARALLEL_SYNTHESIS_ROUTE.md
```

Write one index:

```text
<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/ROUND_PARALLEL_SYNTHESIS_INDEX.json
```

Do not write per-round CSV parameter files. Every physical ChemSkills parameter
CSV must be written only to the workflow-level cumulative parameter directory:

```text
<output_root>/08-round-parallel-synthesis-advisor/synthesis-routes/
```

The current round's parameter values must be merged into the cumulative
parameter CSV files for the total synthesis route. Do not create
`rounds/<round_id>/synthesis-routes/`. Do not write per-catalyst route markdown
files.

### Workflow-Level Cumulative Outputs

During each Stage08 update and at round completion, maintain these
workflow-level files at:

```text
<output_root>/08-round-parallel-synthesis-advisor/SOURCE_LIQUID_INVENTORY.json
<output_root>/08-round-parallel-synthesis-advisor/SOURCE_LIQUID_PREPARATION_METHODS.json
<output_root>/08-round-parallel-synthesis-advisor/CUMULATIVE_SYNTHESIS_ROUTE.md
<output_root>/08-round-parallel-synthesis-advisor/CHEMSKILLS_EXECUTION_SPEC.md
```

**SOURCE_LIQUID_INVENTORY.json** must contain:
- `source_liquids`: array of all source liquids allocated across completed
  rounds and the current Stage08 update
- `source_liquid_limit`: source-liquid capacity derived from the authoritative
  laboratory/ChemSkills files used for this route
- `allocated_count`: total number of allocated source liquids
- `available_slots`: number of empty source-liquid slots remaining
  (`source_liquid_limit - allocated_count`)
- `last_updated_round`: the most recent round_id that updated this inventory

**SOURCE_LIQUID_PREPARATION_METHODS.json** must be maintained directly as part
of Stage08 using
`references/source_liquid_preparation_methods.md`:
- whenever Stage08 allocates a new `source_liquid_id`, write that source
  liquid's complete preparation method in this JSON immediately in the same
  Stage08 update; do not wait for the cumulative 96-well route to be complete
- keep one self-contained preparation method per allocated `source_liquid_id`
- preserve prior methods when later rounds add new source liquids
- keep `route_completion_gate.required_well_count` as `96`
- keep `final_markdown_written=false` until the cumulative 96-well route is complete
- when the cumulative route reaches 96 wells, write
  `<output_root>/08-round-parallel-synthesis-advisor/SOURCE_LIQUID_BOTTLE_PREPARATION.md`
  and set `final_markdown_written=true`

**CUMULATIVE_SYNTHESIS_ROUTE.md** must document:
- The common operation pattern that all synthesis routes must follow
- The set of ChemSkills operations used across all completed rounds
- Operation sequence constraints that future rounds must maintain
- Any operation-level design decisions that establish the cumulative route pattern

Model the cumulative route layout from the output contract and current
workflow-level artifacts, not as a simple concatenation of per-round route
files. In addition to the operation-pattern sections, include these cumulative
summary sections:
- `## 保留结果与累计单孔板布局`: list retained records across completed rounds
  with cumulative `p1` well numbers, original round ids, original round wells,
  record ids, catalyst/system components, source-liquid ids, and total volume
  where available.
- `## 总原液预算与 source-liquid 清单`: summarize total source-liquid usage
  against the authoritative source-liquid limit, remaining slots, and the
  complete source-liquid inventory with bottle columns, elements, reagent
  names, concentrations, first-used rounds, and roles where available.

**CHEMSKILLS_EXECUTION_SPEC.md** must contain exactly these three sections:
- `## 总原液预算与 source-liquid 清单`
- `## ChemSkills 可执行总路线`
- `## ChemSkills JSON 输入格式`

These cumulative files enable Stage02 to enforce synthesis route compatibility
and source-liquid budget constraints in future rounds.

## Script
Run the helper only to create empty shell artifacts:

```sh
python skills/round-parallel-synthesis-advisor/scripts/generate_round_synthesis_shell.py \
  --input-json <path-to-SYNTHESIS_INPUT_SUMMARY.json>
```

The script must not:

- choose synthetic methods
- choose precursors
- retain or block catalysts
- count source liquids beyond schema defaults
- assign wells
- generate CSV parameter contents
- write route prose

After running the script, the agent must complete the markdown, workflow-level
cumulative CSV files, ChemSkills execution spec, and index according to the
references.

## Z-scheme System Synthesis

When `SYNTHESIS_INPUT_SUMMARY.json` contains records with `system: zscheme`, each Z-scheme system must be synthesized as follows:

- **One well per Z-scheme system**: Each Z-scheme record occupies exactly **one well** on plate `p1`, just like a single-photocatalyst record.
- **HER + OER precursors co-deposited**: The well receives the union of precursor source liquids for both the HER component and the OER component. For example, if HER requires metal salts A, B, C and OER requires metal salts D, E, F, deposit all six into the same well.
- **Co-calcination**: The mixed precursors undergo the same heating program together, forming either a composite/mixed-phase material or an in-situ Z-scheme heterostructure, depending on the specific chemistry.
- **Same operation sequence**: Z-scheme wells follow the same ChemSkills operation sequence selected for the retained single-plate route. Only operation parameters may differ where the authoritative ChemSkills files allow per-well liquid-addition differences.
- **Source-liquid accounting**: Count the union of HER and OER precursor source liquids according to the workflow output contract and the laboratory/ChemSkills rules. If HER and OER share a common loaded source liquid, count that loaded liquid only once.
- **Well accounting**: Each Z-scheme system consumes one well in the retained route, symmetrically with single-photocatalyst records.

**Chemical consideration**: Co-depositing HER and OER precursors in one well may form a composite or mixed phase rather than two distinct photocatalyst particles with a controlled interface. This is an acceptable trade-off for the current Stage08 one-plate constraint. Future external post-processing (mechanical mixing, surface modification, mediator addition) can be applied outside the Stage08 ChemSkills scope if needed.

## Authority Gates
- Treat `data/laboratory-limitations/laboratory_limitations_for_agent.json` as
  the authoritative laboratory rule library. Do not use a copied rule summary
  from this skill as the basis for retain/block decisions.
- Treat `data/chem-skills/README.md`, the selected station `SKILL.md` files,
  and the selected parameter templates under `data/chem-skills/` as the
  authoritative ChemSkills operation and parameter sources.
- If the skill text, route references, station docs, and CSV templates conflict
  on operation names, CSV headers, editable columns, wells, or parameter
  limits, follow the authoritative `data/chem-skills` file that defines the
  executable artifact, with CSV templates taking precedence for CSV shape.
- If not all records can be retained under the current rule library,
  ChemSkills definitions, and output contract, retain the maximum feasible
  subset using this deterministic tie-break order: most retained records, most
  distinct formulas or systems, highest source-liquid reuse, then earliest
  input order.
- Stage08 route design must not use MatterSim energies, atomic coordinates,
  Stage07 reference-comparison labels, or failure diagnostics as synthesis
  inputs. Use only the synthesis input summary, laboratory rule library,
  ChemSkills definitions, and Stage08 output contract.

## Completion
Stage08 is complete only when:

- `SYNTHESIS_INPUT_SUMMARY.json` exists
- `ROUND_PARALLEL_SYNTHESIS_INDEX.json` exists and is valid
- `ROUND_PARALLEL_SYNTHESIS_ROUTE.md` exists and is non-empty
- every path in `parameter_csv_files` exists and points to the workflow-level
  `08-round-parallel-synthesis-advisor/synthesis-routes/` directory
- `plate_count` is `1`
- `plate_id` is `p1`
- `source_liquid_limit` is recorded and matches the authoritative
  laboratory/ChemSkills files used for this route
- `source_liquid_count` is an integer no greater than `source_liquid_limit`
- `retained_records` plus `blocked_records` covers every input record exactly once
- `SOURCE_LIQUID_INVENTORY.json` exists at workflow level and is updated for this round
- `SOURCE_LIQUID_PREPARATION_METHODS.json` exists at workflow level, covers every
  allocated source liquid, and is updated for this round
- `CUMULATIVE_SYNTHESIS_ROUTE.md` exists at workflow level and is updated for this round
- `CHEMSKILLS_EXECUTION_SPEC.md` exists at workflow level and contains only the
  three required sections
- if `SOURCE_LIQUID_PREPARATION_METHODS.json` reports the 96-well route as
  complete, `SOURCE_LIQUID_BOTTLE_PREPARATION.md` exists and is non-empty
- `rounds/<round_id>/synthesis-routes/` does not exist

## Opportunistic Knowledge Expansion

While writing the round-level synthesis route or analyzing blocked/retained
records, notice whether the work contains candidate mechanistic knowledge
relevant to photocatalytic overall water splitting or synthesis-feasibility
patterns. When appropriate, search the formal knowledge base, the candidate
knowledge cache, and external literature to extract candidate knowledge.

Retained candidate knowledge must link source workflow artifacts, include real
DOI/URL evidence, and state applicability, uncertainty, and limitations. Reject
or defer items without real literature evidence.

Write full evidence-bearing artifacts under `knowledge-expansion-simple/<run_id>/`
and clean deduplicated candidate knowledge under `data/knowledge-base-cache/`.
Knowledge expansion is optional and must not change retained/blocked route
decisions, source-liquid budgeting, ChemSkills constraints, completion
validation, or the formal knowledge base.

## Return to Parent Agent

When `synthesis_advisor` owns this stage, return:

- `agent: synthesis_advisor`
- `stage: stage08`
- `round_id`
- `recommendation_branch`
- `status`
- `artifact_paths` for `SYNTHESIS_INPUT_SUMMARY.json`,
  `ROUND_PARALLEL_SYNTHESIS_INDEX.json`,
  `ROUND_PARALLEL_SYNTHESIS_ROUTE.md`, workflow-level cumulative files, and
  generated cumulative parameter CSV files, including
  `SOURCE_LIQUID_PREPARATION_METHODS.json` and the conditional final
  `SOURCE_LIQUID_BOTTLE_PREPARATION.md` when written
- `input_record_count`
- `retained_record_count`
- `blocked_record_count`
- `source_liquid_count`
- `violated_laboratory_limitation_ids`
- `elapsed_seconds` when known
- `blocker` when the route cannot satisfy the completion contract
- concise Chinese summary

The parent agent must record the run with the entry scheduler and validate
Stage08. Stage08 should run in parallel with Stage07 when both are needed.
