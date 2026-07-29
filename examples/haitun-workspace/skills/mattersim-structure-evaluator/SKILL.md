---
name: mattersim-structure-evaluator
description: >
  Use when Stage 04 overall water splitting structures are ready for MatterSim
  screening, GPU-bound evaluation commands, and workflow routing review.
---

# mattersim-structure-evaluator

## Overview

Prepare and execute MatterSim screening for one claimed streaming micro-batch.
First-version streaming uses eight sampled top-level candidates per batch. Each
batch is bound to one GPU, and one GPU must run at most one MatterSim process
at a time. Multiple batches may run concurrently on different GPUs.

The helper script still merges all structures in the batch into one combined
input, prepares one MatterSim evaluation command, summarizes combined metrics,
splits detailed outputs back by candidate, and writes Stage05 artifacts under a
batch-specific output directory.

## When To Use

Use this skill after:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py claim-mattersim-batch \
  --output-root <output_root> \
  --gpu-id <single_confirmed_gpu_id>
```

The scheduler emits a batch ID, one GPU ID, a batch sampling plan, an output
directory, and a preparation command.

Do not use this skill when the entry workflow runs with
`execution_scope=stage02_only`.

## Inputs

- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/STRUCTURE_SAMPLING_PLAN.csv`
- optional Z-scheme branch equivalent under
  `<output_root>/05-zscheme-mattersim-structure-evaluator/streaming/batches/<batch_id>/`.
- MatterGen evaluator CLI: defaults to
  `$MATTERGEN_EVALUATE_BIN` or `$MATTERGEN_HOME/.venv/bin/mattergen-evaluate`.
- MatterSim model: defaults to
  `$MATTERSIM_MODEL_PATH` or `$MATTERGEN_HOME/mattersim/mattersim-v1.0.0-5M.pth`.
- reference dataset: defaults to
  `$MATTERGEN_TRI_REFERENCE_PATH` or
  `$MATTERGEN_HOME/data-release/reference_TRI2024correction.gz`.
- one confirmed CUDA GPU ID from the scheduler claim.

## Reference Data Boundary

Do not read `data/reference/` or
`data/reference/mp_required_properties_with_cif.xlsx` in this skill. Stage05
`novelty` is a MatterGen/MatterSim combined-evaluation pass/fail signal, while
`uniqueness` is retained as a diagnostic signal. Stage07 reference novelty must
not change Stage05 routing.

## Execution Environment

- Run this stage's preparation helper from the repository root with `python`
  resolving to the user's prepared project environment. Do not assume a
  machine-specific virtual-environment path.
- Keep the helper entry point workspace-relative:
  `python skills/mattersim-structure-evaluator/scripts/prepare_mattersim_evaluation.py <scheduler-provided arguments>`.
- Generated evaluation commands execute through the repository-local MatterGen
  environment: `mattergen/.venv/bin/mattergen-evaluate`.
- Generated commands must keep
  `OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1` before
  `CUDA_VISIBLE_DEVICES=<single_gpu_id>`.

## Output Artifacts

Single-photocatalyst:

- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/EVALUATION_PLAN.csv`
- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/COMBINED_EVALUATION_PLAN.csv`
- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/STRUCTURE_EVALUATION_SUMMARY.csv`
- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/combined/generated_crystals.extxyz`
- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/combined/detailed_metrics.json`
- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/combined/relaxed_structures.extxyz`
- split candidate outputs under
  `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/evaluations/<candidate_id>/`.

Z-scheme uses the analogous
`05-zscheme-mattersim-structure-evaluator/streaming/batches/<batch_id>/`
directory.

## Workflow

1. Claim one MatterSim batch from the streaming scheduler. The claim records a
   GPU lease.
2. Run the emitted preparation command. It writes `COMBINED_EVALUATION_PLAN.csv`.
3. Execute the single combined command from `COMBINED_EVALUATION_PLAN.csv`.
4. Rerun the same preparation command so the helper splits detailed metrics and
   relaxed structures back to per-candidate outputs and refreshes
   `STRUCTURE_EVALUATION_SUMMARY.csv`.
5. Mark the batch complete:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py complete-mattersim-batch \
  --output-root <output_root> \
  --batch-id <batch_id>
```

6. If MatterSim fails, mark the batch failed with `--status failed
   --failure-reason`, which releases the GPU lease and marks affected
   candidates `eval_failed`.

## Scheduling Rules

- Default batch size is exactly eight sampled top-level candidates.
- One MatterSim batch uses exactly one GPU.
- One GPU runs at most one MatterSim process at a time.
- When multiple complete batches are ready, claim distinct batches for all free
  GPUs before starting lower-priority work.
- Do not rely on one MatterSim command to use multiple GPUs automatically.
- The first version uses only the eight-candidate trigger. A tail of one to
  seven sampled candidates remains queued unless an operator explicitly claims
  a partial batch.

## Closed-Loop Review

After every evaluation pass:

1. Read `STRUCTURE_EVALUATION_SUMMARY.csv`.
2. Treat a structure as a structural-screen survivor only when it is stable and
   novel, and the route is `proceed_to_experimental_validation`.
3. For single-photocatalyst records, a survivor with continuing Stage02
   mechanism support is a reliable recommendation result.
4. If multiple structures with the same single-photocatalyst formula survive
   within a batch, keep only the lowest `energy_above_hull` structure as
   `proceed_to_experimental_validation`.
5. The streaming scheduler records candidates with at least one proceeding row
   as `reliable`; candidates with only failed routes become `evaluated_failed`.
6. Z-scheme records remain component-level at Stage05. Stage06 decides
   whole-system reliability.

## Routing Constants

- `return_to_02_candidate_concretization`
- `return_to_02_mechanism_gate_review`
- `return_to_02_novelty_audit`
- `return_to_04_sampling`
- `proceed_to_experimental_validation`

## Scientific Caution

Describe MatterSim outputs as MLFF-based screening evidence. Any selected
structure still needs stronger validation before scientific claims.

## Opportunistic Knowledge Expansion

While interpreting evaluation results, notice whether the reasoning or result
analysis contains candidate mechanistic knowledge relevant to photocatalytic
overall water splitting. When appropriate, search the formal knowledge base,
the candidate knowledge cache, and external literature to extract candidate
knowledge.

Retained candidate knowledge must be general OWS mechanistic knowledge, link to
source workflow records or artifacts, include real DOI/URL evidence, and state
applicability, uncertainty, and limitations. Reject or defer items without real
literature evidence.

Write full evidence-bearing artifacts under `knowledge-expansion-simple/<run_id>/`
and only the clean deduplicated candidate-knowledge version under
`data/knowledge-base-cache/`. Do not modify
`data/knowledge-base/knowledge_base_for_agent.json` or recommendation workflow
state.

## Return To Parent Agent

Return:

- `agent: structure_runner`
- `stage: stage05`
- `batch_id`
- `candidate_ids`
- `recommendation_branch`
- `status`
- `gpu_id_used`
- `artifact_paths`
- `evaluated_candidate_count`
- `reliable_candidate_count`
- `elapsed_seconds` when known
- `blocker` or failed command summary when incomplete
- concise Chinese summary
