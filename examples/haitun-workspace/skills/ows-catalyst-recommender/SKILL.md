---
name: ows-catalyst-recommender
description: Use when the raw overall-water-splitting knowledge base is ready and Stage02 catalyst recommendation artifacts need to be written for single-photocatalyst or Z-scheme downstream handoff.
---

# ows-catalyst-recommender

## Purpose

Run a long-running streaming Stage02 recommendation loop for the overall water
splitting workflow. Each loop iteration recommends either one
single-photocatalyst candidate or one Z-scheme system. The recommendation is
accepted only through the streaming SQLite registry owned by
`coscientist-ows-entry`.

## Quick Workflow

1. Use the Python environment supplied by the host or workspace.
2. Read the OWS entry skill and this skill.
3. Before every recommendation, read the raw knowledge base, laboratory
   limitations, ChemSkills operation library, historical synthesis records, and
   current streaming registry summary.
4. For non-first candidates, read prior Stage05/Stage06/Stage08/Stage09/Stage10
   feedback that exists under the same `output_root`.
5. Apply all hard rejection rules and cumulative synthesis constraints.
6. Write one candidate-specific reasoning file before registration.
7. Write one candidate payload JSON.
8. Call `run_ows_streaming_scheduler.py register-candidate`.
9. If registration returns `duplicate_formula`, recommend a different formula
   and retry. Do not edit the registry directly.
10. After a successful registration, continue the same running recommender loop
    immediately. Do not wait for MatterGen or MatterSim results before the next
    recommendation; use whatever feedback is already available at the moment
    the next recommendation starts.

The Stage02 recommender is a long-running producer. One successful registration
completes one iteration, not the subagent run. Continuous recommendation is
achieved by the same running recommender repeatedly rereading state,
recommending, and registering candidates. The parent/main agent starts a
replacement only if a recommender exits, fails, is interrupted, or is explicitly
stopped.

## Environment

Run helper scripts from the repository root with `python` from the caller's
active environment. The bundle does not assume a virtual-environment path.

## Required Inputs

- Raw knowledge-base JSON:
  `data/knowledge-base/knowledge_base_for_agent.json`.
- Raw laboratory-limitation JSON:
  `data/laboratory-limitations/laboratory_limitations_for_agent.json`.
- ChemSkills operation library: `data/chem-skills`.
- Historical success database: `data/history/`.
- Current streaming registry:
  `<output_root>/00-coscientist-ows-entry/STREAMING_CANDIDATE_REGISTRY.sqlite3`.
- Current recommendation branch: `single-photocatalyst` or `zscheme`.

For later candidates, consult available feedback from the same `output_root`:

- Stage05/Stage06 batch summaries and failure routes.
- Stage08 blocked records and source-liquid inventory.
- Conditional Stage09/Stage10 failure or information-gap feedback files when
  they exist.
- Existing reliable candidates in the streaming registry.

## Optional Web-Aided Knowledge Checking

During recommendation, the recommender reads the configured knowledge base as
the primary source. The agent may, when it considers it necessary, use web
search to spot-check knowledge truthfulness, add background context, and support
divergent hypothesis generation, but this is not mandatory.

Web search must not replace the raw knowledge base, laboratory limitations,
ChemSkills constraints, or available workflow feedback.

## Decision Rules

### Hard Rejection Rules

Reject any single photocatalyst, Z-scheme component, or Z-scheme system when
any of these apply:

- Its composition, required precursor/reagent, atmosphere, equipment
  requirement, or expected synthesis route violates raw laboratory limitations.
- Its preliminary synthesis route requires an operation not expressible using
  `data/chem-skills`.
- Its normalized formula is already present in the streaming registry for the
  same branch.
- Its element composition is exactly the same as any prior reliable Stage05 or
  Stage06 recommendation result.
- For non-first retained routes, its route fails complete cumulative synthesis
  route compatibility.
- For non-first retained routes, the union of existing source liquids and new
  required source liquids would exceed 32 total source liquids.

Candidates rejected before registration should be described in the
candidate-specific reasoning or local notes, not inserted into the registry.

### Cumulative Synthesis Feasibility

Every recommended catalyst should remain compatible with the established
cumulative synthesis route when such a route exists:

1. Match the complete route document, including operation sequence, container,
   atmosphere, furnace program family, cooling setting, ultrasonication setting,
   and source-liquid handling mode.
2. Stay within the 32-source-liquid budget.
3. Prefer candidates that use available empty source-liquid slots when slots
   exist.

For Z-scheme systems, count the union of source liquids required by both HER and
OER components.


## Candidate Payload Contract

### Single-Photocatalyst Payload

Write a JSON object with at least:

- `candidate_id`
- `candidate_name`
- `main_photocatalyst`
- `main_photocatalyst_formula_note`
- `preliminary_synthesis_route`
- `laboratory_feasibility_decision`: `pass`
- `violated_laboratory_limitation_ids`: `none` or comma-separated IDs
- `laboratory_feasibility_reason`
- `difference_from_prior_recommendations`
- `reference_knowledge_ids`
- `supporting_knowledge`

### Z-Scheme Payload

Write a JSON object with at least:

- `zscheme_id`
- `zscheme_name`
- `system_type`
- `her_component_id`
- `oer_component_id`
- `solid_electron_mediator`
- `mechanism_gate_status`: `pass`
- `mechanism_gate_reason`
- `laboratory_feasibility_decision`: `pass`
- `violated_laboratory_limitation_ids`: `none` or comma-separated IDs
- `laboratory_feasibility_reason`
- `difference_from_prior_recommendations`
- `reference_knowledge_ids`
- `supporting_knowledge`
- `components`: an array containing both HER and OER component records.

Each component record must include:

- `candidate_id`
- `candidate_name`
- `parent_zscheme_id`
- `component_role`: `h2_evolving_photocatalyst` or
  `o2_evolving_photocatalyst`
- `main_photocatalyst`
- `main_photocatalyst_formula_note`
- `mechanism_role`
- `preliminary_synthesis_route`
- `laboratory_feasibility_decision`
- `violated_laboratory_limitation_ids`
- `laboratory_feasibility_reason`
- `difference_from_prior_recommendations`
- `reference_knowledge_ids`
- `supporting_knowledge`

Stage02 must not generate, pre-assign, or reserve a `zscheme_pair_id` column.
Stage06 assigns structure-level pair IDs later.

## Reasoning Artifact

Before registration, write one reasoning Markdown file for the candidate. The
file must contain at least 500 Chinese characters of substantive reasoning for
that one recommendation. For a Z-scheme system, the reasoning must cover the HER
component, OER component, and system-level pairing logic.

The reasoning must explain:

- Knowledge-base support and inferred hypotheses.
- Oxidation-state and charge-balance logic.
- Plausible structure family or defect chemistry.
- Band-gap and band-edge implications.
- HER/OER surface redox plausibility.
- Carrier-separation or recombination risks.
- Photocorrosion and aqueous-stability concerns.
- How prior feedback changes the choice.
- How prior reliable compositions and current registry formulas are avoided.
- Why the preliminary synthesis route satisfies laboratory limitations,
  ChemSkills coverage, cumulative route compatibility, and source-liquid budget.

Do not fabricate literature, measurements, phase stability, band positions,
activity, durability, or novelty results that are absent from available inputs.
Mark such points as inferred hypotheses or uncertainties.

## Registration

Use:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py register-candidate \
  --output-root <output_root> \
  --payload-json <candidate_payload.json> \
  --reasoning-file <candidate_reasoning.md> \
  --agent stage02_recommender
```

The scheduler atomically:

1. Opens a SQLite transaction.
2. Checks normalized `branch + formula_key` uniqueness.
3. Writes candidate artifacts under
   `<output_root>/02-ows-catalyst-recommender/streaming/candidates/<candidate_id>/`.
4. Inserts the candidate with status `accepted`.
5. Rebuilds aggregate compatibility CSV views under
   `<output_root>/02-ows-catalyst-recommender/streaming/`.

Do not edit the SQLite database or aggregate CSVs manually.

## Progress Reporting

After each successful registration, the recommender may report progress to the
parent/main agent but should continue the loop unless stopped. Progress reports
should include:

- `agent: stage02_recommender`
- `stage: stage02`
- `candidate_id` or `zscheme_id`
- `recommendation_branch`
- `status`
- `artifact_paths`
- `formula_key`
- registration result, including duplicate diagnostics when registration fails
- concise Chinese summary
