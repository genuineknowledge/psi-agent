# Source Liquid Preparation Methods

Use this reference inside Stage08 to maintain source-liquid preparation methods
for the round-parallel OWS synthesis route. This is not a separate workflow
stage: the `synthesis_advisor` agent owns these outputs as part of
`round-parallel-synthesis-advisor`.

## Contents

- Scope
- Incremental Registry Mode
- Final Markdown Gate
- Fixed-Value SOP Rules
- Reagent Naming Rules
- Final SOP Structure
- Validation

## Scope

Maintain explicit purchase-to-precursor-to-bottle procedures for all source
liquids allocated by Stage08. Use the current round route, the workflow-level
source-liquid inventory, and cumulative route artifacts as evidence.

Do not modify scheduler state or any non-Stage08 pipeline files while updating
source-liquid preparation methods.

When working round by round, do not assume all source liquids are known at once.
Maintain a preparation-method registry as new source liquids are introduced.
When a new `source_liquid_id` is allocated, write that source liquid's complete
preparation method to the registry immediately in the same Stage08 update. Do
not defer the new source liquid's method until the cumulative route is complete.
Generate the final total Markdown SOP only after the cumulative 96-well route is
complete.

## Incremental Registry Mode

Maintain this workflow-level registry:

```text
<output_root>/08-round-parallel-synthesis-advisor/SOURCE_LIQUID_PREPARATION_METHODS.json
```

Use JSON as the authoritative registry because it can be merged by
`source_liquid_id`. A CSV view may be emitted only when explicitly useful; it is
not required by the Stage08 contract.

On each round:

1. Load the existing registry if it exists; otherwise create it.
2. Read the workflow-level `SOURCE_LIQUID_INVENTORY.json` and the current
   round's `ROUND_PARALLEL_SYNTHESIS_INDEX.json`.
3. Add method entries only for new `source_liquid_id` values, or update entries
   whose precursor identity, target concentration, bottle volume, or procurement
   constraints changed.
4. Complete every new method entry before finishing the current Stage08 update;
   the registry must not lag behind newly allocated source liquids.
5. Preserve existing entries that are not touched by the current round.
6. Record the source round and evidence paths in every updated entry.
7. Do not generate or overwrite the final total Markdown SOP unless the
   96-well cumulative route is complete.

Use this JSON shape:

```json
{
  "updated_at": "ISO-8601 timestamp",
  "status": "incremental",
  "final_markdown_written": false,
  "final_markdown_path": null,
  "route_completion_gate": {
    "required_well_count": 96,
    "observed_well_count": 0,
    "is_complete": false,
    "evidence_file": null
  },
  "methods": [
    {
      "source_liquid_id": "src_11",
      "bottle": "11号原液瓶",
      "element": "Sn",
      "target_concentration": "0.050 M Sn element equivalent",
      "final_bottle_volume": "20.00 mL",
      "method_kind": "working_precursor_dilution",
      "procurement": [
        "tin(IV) chloride pentahydrate, SnCl4·5H2O, reagent grade or higher",
        "citric acid monohydrate, C6H8O7·H2O, reagent grade or higher",
        "ammonium hydroxide, 25-28 wt% NH3(aq)"
      ],
      "working_precursor": {
        "name": "0.1000 M citrate-stabilized Sn(IV), Sn equivalent",
        "preparation": [
          "Dissolve 2.1014 g citric acid monohydrate in about 25.0 mL deionized water.",
          "Add 1.7530 g SnCl4·5H2O in portions.",
          "Add 0.80 mL 25-28 wt% NH3(aq).",
          "Dilute to 50.00 mL after a clear complex solution is obtained."
        ]
      },
      "final_bottle_steps": [
        "Label a 20 mL volumetric flask.",
        "Add about 8.0 mL deionized water.",
        "Pipette 10.00 mL of the 0.1000 M Sn working precursor.",
        "Dilute to 20.00 mL and mix.",
        "Check clarity and transfer to the source bottle."
      ],
      "qc_safety": "Sn(IV) hydrolyzes readily; do not use turbid or precipitated solution.",
      "first_seen_round_id": "rXX",
      "last_updated_round_id": "rXX",
      "evidence_files": []
    }
  ]
}
```

Keep every registry entry self-contained enough to generate the final SOP
without re-deriving chemistry from earlier round files.

## Final Markdown Gate

Write the total source-liquid bottle preparation SOP only when reliable evidence
shows that the cumulative 96-well route is complete:

```text
<output_root>/08-round-parallel-synthesis-advisor/SOURCE_LIQUID_BOTTLE_PREPARATION.md
```

Acceptable completion evidence includes a final cumulative synthesis route,
final source-liquid inventory, or run status file showing 96 retained
wells/candidates and no pending source-liquid additions.

If completion evidence is missing or `observed_well_count` is below 96:

1. Update only `SOURCE_LIQUID_PREPARATION_METHODS.json`.
2. Set `route_completion_gate.is_complete` to `false`.
3. Keep `final_markdown_written` as `false`.
4. Do not write the final total Markdown SOP.

If completion evidence is present:

1. Set `route_completion_gate.is_complete` to `true`.
2. Ensure every active source liquid in `SOURCE_LIQUID_INVENTORY.json` has one
   matching registry method.
3. Generate `SOURCE_LIQUID_BOTTLE_PREPARATION.md` from the final inventory plus
   registry entries.
4. Set `final_markdown_written` to `true` and record the repository-relative
   Markdown path in `final_markdown_path`.

## Fixed-Value SOP Rules

Use fixed values in the registry and final SOP. Do not write variable or
placeholder instructions such as:

- "If the mother solution concentration is C, pipette 1.000/C mL"
- "Calculate by the calibrated concentration"
- "Use a known-concentration mother liquor"
- "Add a few drops" when a defined volume is possible

It is acceptable to use approximate pre-added water volumes such as
"about 8.0 mL" when the exact analytical operation is the later fixed aliquot
plus final volumetric dilution.

For 20.00 mL bottles at 0.050 M element equivalent, the bottle contains
1.000 mmol element. A common explicit pattern is:

1. Prepare a 0.1000 M element-equivalent working precursor.
2. Add about 8.0 mL deionized water to a 20 mL volumetric flask.
3. Pipette 10.00 mL of the 0.1000 M working precursor.
4. Dilute to 20.00 mL and mix.

## Reagent Naming Rules

Preserve hydration states when they determine mass.

For directly weighed solids, specify the purchasable reagent name, formula,
hydration state, grade or purity, and exact mass for the fixed final volume.

For commercial liquids, specify the purchasable product concentration or wt%.
If the product concentration is not fixed, state that a fixed-volume SOP cannot
be written until the purchased product is selected.

For hydrolysis-prone or complexed sources, define a fixed working precursor
solution, then use a fixed aliquot of that working precursor in the final
bottle.

If a nitrate hydrate has uncertain hydration, do not invent a fixed mass from
it. Prefer a fixed commercial solution or a route from a stable oxide/acid pair.

Use "reagent grade or higher" to mean laboratory reagent grade, analytical
reagent grade, AR, ACS reagent, ACS grade, trace-metals basis, or an equivalent
higher-purity grade. Avoid technical or industrial grade unless the user
explicitly accepts it.

## Final SOP Structure

For a final full SOP, include:

1. Scope and fixed bottle scheme.
2. Normalized reagent names and naming notes.
3. Bottle summary table.
4. Fixed procurement specifications and working precursor recipes.
5. Uniform pre-preparation steps.
6. Per-bottle preparation steps.
7. Execution checklist.

For per-bottle steps that use a working precursor, use the concise pattern:

1. Label the volumetric flask.
2. Add about 8.0 mL deionized water.
3. Pipette 10.00 mL of the 0.1000 M working precursor.
4. Dilute to 20.00 mL and mix.
5. Check clarity, then transfer to the source bottle.

Do not duplicate the working-precursor recipe inside the per-bottle steps.

## Validation

Before finishing an incremental update:

1. Confirm `source_liquid_id` values are unique.
2. Confirm every allocated source liquid has exactly one method entry.
3. Confirm new or changed source liquids from the current round have entries.
4. Confirm unchanged entries were preserved.
5. Confirm `final_markdown_written` remains `false` unless the 96-well gate is
   complete.

Before finishing a final Markdown SOP:

1. Search for forbidden variable forms: `C_`, `1.000/C`, `标定浓度`, `母液`,
   `按 .*换算`, and similar placeholders.
2. Search for outdated ammonia concentration if the SOP should use
   25-28 wt% NH3(aq): `28-30`.
3. Confirm every active `src_XX` bottle appears in both the summary table and
   the per-bottle section.
4. Confirm unused bottles remain explicitly unused.
5. Confirm every final active source liquid has a corresponding registry entry.
