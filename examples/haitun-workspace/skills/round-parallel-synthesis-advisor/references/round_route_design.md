# Round Parallel Route Design

Use this reference to design one ChemSkills-executable synthesis route for all
retained successful catalysts in a workflow round.

## Read First
Read these sources before writing route content:

- `SYNTHESIS_INPUT_SUMMARY.json`
- `references/source_liquid_preparation_methods.md`
- `data/laboratory-limitations/laboratory_limitations_for_agent.json`
- `data/chem-skills/README.md`
- Required station `SKILL.md` files and parameter templates under
  `data/chem-skills/`

Use Chinese for human-readable route content. Preserve exact file names, schema
keys, ChemSkills station names, operation names, CSV headers, formulae, and
record identifiers.

This read list is a hard gate. Do not choose retained records, block records,
operation names, parameter CSV files, or executable route steps before reading
the authoritative laboratory rule library and the ChemSkills files required by
the route. Record those paths in `ROUND_PARALLEL_SYNTHESIS_INDEX.json`.

While allocating or updating source liquids, maintain
`SOURCE_LIQUID_PREPARATION_METHODS.json` through the Stage08 source-liquid
preparation reference. Add each new source liquid's complete method immediately
when that source liquid is allocated, and write the final total
`SOURCE_LIQUID_BOTTLE_PREPARATION.md` only after the cumulative 96-well route is
complete.

## Retain And Block Records

Apply the current round input JSON, every rule in
`data/laboratory-limitations/laboratory_limitations_for_agent.json`, the
required `data/chem-skills` files, and the Stage08 output contract. Do not
duplicate or paraphrase the rule library here; the repository rule files are the
source of truth.

Mark any record that violates a rule as blocked, include the applicable
`limitation_id` when the violation comes from the laboratory rule library, and
do not write pseudo-executable chemistry for a blocked record.

If not all records can be retained under the current rule library, ChemSkills
definitions, and output contract, retain the largest feasible subset. Use this
deterministic tie-break order:

1. most retained records
2. most distinct formulas or systems
3. highest source-liquid reuse
4. earliest input order

## Route Design
Prefer a conservative aqueous precursor mixing plus static-air muffle-furnace
route when scientifically defensible and when the route can be expressed with
the current ChemSkills definitions. Use only station names, operation names,
operation parameters, and CSV structures confirmed from the `data/chem-skills`
files read for this route.

Do not include any operation that the current laboratory rule library or the
current ChemSkills files place outside the executable synthesis route. Put
non-executable preparation, characterization, or validation notes only in the
non-executable narrative sections required by the output contract.

## CSV Planning
Use the parameter CSV templates from the selected `data/chem-skills` stations.
Choose the template that satisfies the route's source-liquid and operation
needs under the current rule library. Preserve the selected template header,
first column, row order, non-editable fields, and allowed value conventions.
Fill unused editable entries according to the selected template and station
instructions.

Use one physical CSV per file-valued operation parameter. Do not reuse one CSV
file path for multiple operation steps. All physical CSV files must live under:

```text
<output_root>/08-round-parallel-synthesis-advisor/synthesis-routes/
```

Do not create per-round CSV files under
`rounds/<round_id>/synthesis-routes/`. For each completed round, merge the
retained records' parameter values into the total cumulative CSV files for the
single `p1` plate. Preserve existing retained wells and existing source-liquid
column meanings when updating a cumulative CSV.

If a second liquid-addition step is required, write a separate cumulative CSV
file for that step, but the total source-liquid identity budget remains the
same authoritative budget for the cumulative route.
