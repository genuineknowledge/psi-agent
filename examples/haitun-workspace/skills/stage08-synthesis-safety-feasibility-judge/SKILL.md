---
name: stage08-synthesis-safety-feasibility-judge
description: >
  Use after Stage08 round-parallel synthesis has produced a 96-well route, or
  when given a Stage08 96-well plate synthesis route, and Codex must judge each
  catalyst independently for chemical safety and synthesis feasibility by
  checking the expected intermediate product after each experimental step.
  In the OWS workflow this is the Stage09 synthesis-route safety and
  feasibility review. The output must give separate Chinese reasoning of at
  least 500 Chinese characters per catalyst for chemical safety and at least
  500 Chinese characters per catalyst for synthesis feasibility.
---
# Stage08 Synthesis Safety Feasibility Judge

## Objective

Assess the supplied Stage08 96-well plate synthesis route for chemical safety
and catalyst synthesis feasibility, one catalyst at a time. In the OWS workflow,
run this as Stage09 after Stage08 synthesis routing is complete.

## Input

Use only the supplied route text or the local file path explicitly supplied by
the user. The route may be a Stage08 round-level route, ChemSkills input route,
or equivalent 96-well plate synthesis document.

Evaluate every catalyst that the route attempts to synthesize. Ignore empty
wells and do not add catalysts that are not present in the route.

## Assessment Rules

Use the route content and general chemistry knowledge. Do not call ChemSkills,
do not use external LLM scripts, and do not modify Stage08 route files.

Process each catalyst independently. Use shared plate-level operations only as
they apply to the current catalyst. Do not let another catalyst's conclusion,
reasoning, success, failure, similarity, or position affect the current
catalyst.

If the route omits information needed for a judgment, state the missing
information in that catalyst's reasoning and mark the affected conclusion as
`信息不足，无法充分判断`.

Use the `ows630-road.md` style as the preferred standard: route facts first,
controlled conclusions, explicit high-concern elements and waste/ventilation
conditions for safety, and step-by-step intermediate-state feasibility rather
than free-form risk inflation. Treat micro-scale nitrate, ammonium, organic
ligand, and high-temperature risks as chemical safety controls; mark
`有化学安全问题` only when the current catalyst's reagents or route facts create
an additional material-specific or route-specific safety issue that remains
significant under normal ventilation, high-temperature handling, labeling, and
waste-segregation controls.

## Synthesis Feasibility Method

For each catalyst, follow the experimental steps in the route in order. After
each step, state the expected result as a specific intermediate product or
physical precursor state for that catalyst, then judge whether that expected
result can actually be reached under the stated reagents, amounts, vessel,
mixing, heating, atmosphere, time, and workup conditions.

Give an explainable reason for every step-level judgment. Then summarize the
reachable and unreachable expected intermediates and give the final synthesis
feasibility conclusion for that catalyst.

## Output

Write in Chinese unless the user requests another language. Write the complete
output to a Markdown document unless the user explicitly requests inline output.
If the user does not supply an output path, write the document in the current
working directory using a descriptive filename.

For OWS workflow Stage09, write:

- `<output_root>/09-synthesis-safety-feasibility-judge/rounds/<round_id>/SYNTHESIS_SAFETY_FEASIBILITY_JUDGMENT.md`
- `<output_root>/09-synthesis-safety-feasibility-judge/rounds/<round_id>/SYNTHESIS_SAFETY_FEASIBILITY_JUDGMENT.md.audit.json`

Each catalyst section in the Markdown document must start with a level-3
heading so the scheduler can count processed catalysts:

```markdown
### <index>. <catalyst name or formula>
```

The audit JSON must use these scheduler-checked keys:

- `total`: number of retained catalysts assessed
- `min_safety_reason_chinese_chars`: minimum Chinese-character count among all
  chemical-safety reasoning fields
- `min_feasibility_reason_chinese_chars`: minimum Chinese-character count among
  all synthesis-feasibility reasoning fields
- `missing_safety_conclusion`: list of catalyst indices or names missing a
  controlled chemical-safety conclusion
- `missing_feasibility_conclusion`: list of catalyst indices or names missing a
  controlled synthesis-feasibility conclusion
- `missing_step_table`: list of catalyst indices or names missing the step-level
  synthesis assessment
- `records`: per-catalyst conclusion records, including catalyst identity,
  well position when available, safety conclusion, feasibility conclusion, and
  Chinese-character counts

For each catalyst, output:

- Catalyst name or formula
- Well position when available
- Chemical safety conclusion: `有化学安全问题`, `无明显化学安全问题`, or `信息不足，无法充分判断`
- Chemical safety reasoning: at least 500 Chinese characters for this catalyst
- Synthesis step assessment: for each route step, list the step, expected
  intermediate product or precursor state, reachability judgment, and reason
- Synthesis feasibility conclusion: `预计能成功合成催化剂`, `预计不能成功合成催化剂`, or `信息不足，无法充分判断`（Successful synthesis is defined as obtaining samples matching the target chemical formula.）
- Synthesis feasibility reasoning: summarize the step-level expected
  intermediates and final judgment in at least 500 Chinese characters for this
  catalyst

The 500-character requirement applies separately to each catalyst's chemical
safety reasoning and each catalyst's synthesis feasibility reasoning. It is not
a whole-plate total.

Make every catalyst section self-contained by repeating the catalyst name or
formula and the relevant route facts. Do not replace per-catalyst reasoning with
a table-only output or a plate-level summary.
