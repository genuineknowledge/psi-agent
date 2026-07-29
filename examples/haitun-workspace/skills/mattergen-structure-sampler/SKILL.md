---
name: mattergen-structure-sampler
description: >
  Use when Stage 02 overall water splitting candidates are ready for MatterGen
  crystal structure prediction sampling, GPU-bound sampling commands, or
  automatic structure generation.
---

# mattergen-structure-sampler

## Overview

Prepare and execute MatterGen crystal structure prediction for one claimed
streaming candidate at a time. The streaming scheduler emits the candidate,
GPU, candidate CSV, output directory, and preparation command. This skill keeps
the existing MatterGen command format and repository-local MatterGen
environment.

## When To Use

Use this skill after
`run_ows_streaming_scheduler.py claim-sampling --output-root <output_root>
--gpu-id <gpu_id>` returns a claimed candidate and a `prepare_command`.

Do not use this skill when the entry workflow runs with
`execution_scope=stage02_only`.

## Inputs

Streaming single-photocatalyst input:

- `<output_root>/02-ows-catalyst-recommender/streaming/candidates/<candidate_id>/RECOMMENDED_CANDIDATE.csv`

Streaming Z-scheme input:

- `<output_root>/02-ows-catalyst-recommender/streaming/candidates/<zscheme_id>/ZSCHEME_COMPONENT_CANDIDATES.csv`

Other inputs:

- MatterGen generator binary: defaults to
  `$MATTERGEN_GENERATE_BIN` or `$MATTERGEN_HOME/.venv/bin/mattergen-generate`.
- MatterGen CSP checkpoint directory: defaults to
  `$MATTERGEN_MODEL_PATH` or `$MATTERGEN_HOME/checkpoints/crystal_structure_prediction`.
- Confirmed CUDA GPU ID(s) from the streaming scheduler claim.

## Reference Data Boundary

Do not read `data/reference/` or
`data/reference/mp_required_properties_with_cif.xlsx` in this skill. Reference
data is reserved for Stage07 `catalyst-novelty-evaluator` only.

## Execution Environment

- Run this stage's preparation helper from the repository root with `python`
  resolving to the user's prepared project environment. Do not assume a
  machine-specific virtual-environment path.
- Keep the helper entry point workspace-relative:
  `python skills/mattergen-structure-sampler/scripts/prepare_mattergen_sampling.py <scheduler-provided arguments>`.
- Generated sampling commands execute MatterGen through the repository-local
  MatterGen environment: `mattergen/.venv/bin/mattergen-generate`.
- Generated commands must keep
  `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1` before
  `CUDA_VISIBLE_DEVICES=<gpu_id_list>`.

## Output Artifacts

Single-photocatalyst:

- `<output_root>/04-mattergen-structure-sampler/streaming/candidates/<candidate_id>/STRUCTURE_SAMPLING_PLAN.csv`
- `<output_root>/04-mattergen-structure-sampler/streaming/candidates/<candidate_id>/SAMPLING_PARAMETERS.json`
- `<output_root>/04-mattergen-structure-sampler/streaming/candidates/<candidate_id>/SAMPLING_COMMANDS.md`
- `<output_root>/04-mattergen-structure-sampler/streaming/candidates/<candidate_id>/samples/<candidate_id>/generated_crystals.extxyz`

Z-scheme:

- `<output_root>/04-zscheme-mattergen-structure-sampler/streaming/candidates/<zscheme_id>/STRUCTURE_SAMPLING_PLAN.csv`
- component sample directories under
  `<output_root>/04-zscheme-mattergen-structure-sampler/streaming/candidates/<zscheme_id>/samples/<component_candidate_id>/`.

## Workflow

1. Claim one candidate with the streaming scheduler.
2. Run the emitted `prepare_command`.
3. Execute generated MatterGen commands for rows with
   `sampling_status=ready_to_sample`.
4. Verify that every ready row has generated structures, such as
   `generated_crystals.extxyz` or `generated_crystals_cif.zip`.
5. Mark the candidate sampled:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py complete-sampling \
  --output-root <output_root> \
  --candidate-id <candidate_id>
```

6. If sampling fails, mark it failed with `--status failed --failure-reason`.

## Decision Rules

- Sample rows with valid candidate IDs and parseable `main_photocatalyst`
  formulas.
- Keep blocked rows in `STRUCTURE_SAMPLING_PLAN.csv` with explicit
  `blocked_reason` and `recommended_return_step`.
- Preserve Stage02 identifiers and notes without rewriting their meaning.
- Do not expand the Stage02 recommendation set.
- Do not execute sampling commands until the scheduler claim provides a
  confirmed GPU ID.
- The scheduler prevents two workers from claiming the same candidate.
- MatterGen GPU scheduling otherwise follows the existing structure-runner
  strategy.

## Handoff

The streaming scheduler builds MatterSim micro-batches from candidates marked
`sampled`. MatterGen does not call MatterSim directly.

## Return To Parent Agent

Return:

- `agent: structure_runner`
- `stage: stage04`
- `candidate_id`
- `recommendation_branch`
- `status`
- `gpu_ids_used`
- `artifact_paths`
- `ready_sampling_row_count`
- `completed_sampling_row_count`
- `blocked_sampling_row_count`
- `elapsed_seconds` when known
- `blocker` or failed command summary when incomplete
- concise Chinese summary
