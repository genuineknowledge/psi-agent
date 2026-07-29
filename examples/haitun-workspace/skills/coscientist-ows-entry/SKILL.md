---
name: coscientist-ows-entry
description: >
  Use when a user asks for an end-to-end overall water splitting catalyst
  recommendation workflow, candidate prioritization, staged recommendation
  handoff, or closed-loop MatterGen/MatterSim screening.
---

# coscientist-ows-entry

## Overview

Coordinate a streaming workflow for recommending photocatalysts for overall
water splitting. The target reaction produces hydrogen and oxygen from the same
aqueous system under illumination.

The main agent owns orchestration and recovery. Project custom subagents own
stage work. The streaming scheduler owns the SQLite candidate registry, atomic
candidate acceptance, candidate-level queue state, MatterSim GPU leases, and
next-action emission. It does not author recommendations or run GPU commands.

## Repository Assumptions

- Use the repository root as the working context.
- Use `data/knowledge-base/knowledge_base_for_agent.json` as the default
  knowledge base.
- Write all workflow artifacts under `<output_root>/`.
- After `output_root` is specified, limit agent-side file reads to
  `<output_root>/`, `data/`, `.agents/`, `.codex/`, `.venv/`, `mattergen/`,
  repository-root `AGENTS.md`, and repository-root `pyproject.toml`.
- Streaming outputs live under `<output_root>/<stage>/streaming/`.
- Each workflow chooses exactly one `recommendation_branch`:
  `single-photocatalyst` or `zscheme`.
- Run helper Python scripts with the same environment used to run psi-agent;
  do not depend on a machine-specific activation path.
- MatterGen and MatterSim execution use the repository-local MatterGen
  environment through generated commands:
  `mattergen/.venv/bin/mattergen-generate` and
  `mattergen/.venv/bin/mattergen-evaluate`.

## Candidate Knowledge Cache Boundary

Stage agents may opportunistically capture candidate knowledge when their
reasoning, screening, aggregation, novelty comparison, synthesis planning, or
review work reveals generalizable OWS mechanistic knowledge. This is optional
and must not block the main workflow.

Before adding candidate knowledge, check both
`data/knowledge-base/knowledge_base_for_agent.json` and
`data/knowledge-base-cache/` to avoid duplicating formal or already cached
knowledge. Store full evidence traces under `knowledge-expansion-simple/<run_id>/`
and store clean deduplicated candidate knowledge under
`data/knowledge-base-cache/`. Do not modify the formal knowledge base unless a
separate explicit curation step is requested.

## Subagent Architecture

Project custom agents:

- `stage02_context_miner`: optional read-only context miner for Stage02.
- `stage02_recommender`: owns a long-running streaming recommendation loop.
  First version streaming keeps `recommendation_parallelism` recommender
  subagents active when the queue needs candidates; the default is `4`. Each
  loop iteration writes one candidate-specific reasoning file and registers
  exactly one top-level candidate through the streaming scheduler, then
  continues to the next recommendation.
- `structure_runner`: owns either one claimed MatterGen candidate or one
  claimed MatterSim micro-batch.
- `zscheme_evaluator`: owns Stage06 Z-scheme system aggregation for reliable
  Z-scheme component/system records.
- `novelty_evaluator`: owns Stage07 successful-catalyst novelty Excel outputs.
- `synthesis_advisor`: owns Stage08 96-well plate aggregation and synthesis
  route completion for retained reliable catalysts, including source-liquid
  inventory and preparation methods.
- `synthesis_safety_feasibility_judge`: owns Stage09 synthesis-route chemical
  safety and synthesis-feasibility judgment.
- `catalytic_performance_prover`: owns Stage10 external-LLM catalytic
  performance proof.

## Streaming Scheduler CLI

Run from the repository root after activating the project environment.

Initialize a streaming workflow:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py init \
  --output-root <output_root> \
  --knowledge-base-path data/knowledge-base/knowledge_base_for_agent.json \
  --recommendation-branch <single-photocatalyst|zscheme> \
  --recommendation-parallelism 4 \
  --mattersim-batch-size 8 \
  --gpu-id <confirmed_gpu_ids>
```

Inspect and get the next action:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py next-action \
  --output-root <output_root>
```

Register one accepted candidate atomically:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py register-candidate \
  --output-root <output_root> \
  --payload-json <candidate_payload.json> \
  --reasoning-file <candidate_reasoning.md> \
  --agent stage02_recommender
```

Claim one candidate for MatterGen:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py claim-sampling \
  --output-root <output_root> \
  --gpu-id <confirmed_gpu_id>
```

After MatterGen produces structures:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py complete-sampling \
  --output-root <output_root> \
  --candidate-id <candidate_id>
```

Claim one MatterSim batch:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py claim-mattersim-batch \
  --output-root <output_root> \
  --gpu-id <single_confirmed_gpu_id>
```

After MatterSim is evaluated and post-processed:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py complete-mattersim-batch \
  --output-root <output_root> \
  --batch-id <batch_id>
```

The CLI prints JSON. Treat `next_action=run_concurrent_actions` as an
instruction to maintain Stage02 recommendation continuously while independently
draining GPU queues. The returned `concurrent_actions` may include
`maintain_parallel_subagents`, `claim_sampling`, and
`claim_mattersim_batch`.

Treat a successful `register-candidate` response as one completed iteration of
the same running producer, not as subagent completion. Its `agent_next_action`
instructs the running `stage02_recommender` to reread current state and continue
the recommendation loop. The parent/main agent should not start a replacement
unless that recommender exits, fails, or is explicitly stopped.

When the main agent knows how many `stage02_recommender` runs are still active,
pass that count to avoid over-spawning:

```sh
python skills/coscientist-ows-entry/scripts/run_ows_streaming_scheduler.py next-action \
  --output-root <output_root> \
  --active-recommenders <running_stage02_recommender_count>
```

## Inputs And Parameters

- `knowledge_base_path`: default `data/knowledge-base/knowledge_base_for_agent.json`.
- `output_root`: default `ows`.
- `execution_scope`: `full` or `stage02_only`.
- `recommendation_branch`: `single-photocatalyst` or `zscheme`.
- `gpu_id`: confirmed CUDA GPU IDs, comma-separated after normalization.
- `target_recommendation_count`: optional reliable-catalyst stop target.
- `mattergen_batch_size`: optional Stage04 helper override.
- `recommendation_parallelism`: default `4` for the first streaming version.
- `mattersim_batch_size`: default `8`; this is a single trigger, so 1-7 sampled
  candidates remain queued until enough additional samples arrive or an
  operator explicitly claims a partial batch.

## Output Artifacts

Entry artifacts:

- `<output_root>/00-coscientist-ows-entry/PARAMETERS.json`
- `<output_root>/00-coscientist-ows-entry/STREAMING_SCHEDULER_STATE.json`
- `<output_root>/00-coscientist-ows-entry/STREAMING_PIPELINE_STATUS.md`
- `<output_root>/00-coscientist-ows-entry/STREAMING_CANDIDATE_REGISTRY.sqlite3`
- optional `<output_root>/AGENT_RUN_TRANSCRIPT.md`

Stage02 streaming artifacts:

- `<output_root>/02-ows-catalyst-recommender/streaming/candidates/<candidate_id>/CANDIDATE_PAYLOAD.json`
- `<output_root>/02-ows-catalyst-recommender/streaming/candidates/<candidate_id>/REASONING.md`
- `<output_root>/02-ows-catalyst-recommender/streaming/candidates/<candidate_id>/RECOMMENDED_CANDIDATE.csv`
- `<output_root>/02-ows-catalyst-recommender/streaming/candidates/<candidate_id>/ZSCHEME_SYSTEM.csv`
- `<output_root>/02-ows-catalyst-recommender/streaming/candidates/<candidate_id>/ZSCHEME_COMPONENT_CANDIDATES.csv`
- aggregate compatibility views under
  `<output_root>/02-ows-catalyst-recommender/streaming/`:
  `RECOMMENDED_CANDIDATES.csv`, `ZSCHEME_SYSTEMS.csv`, and
  `ZSCHEME_COMPONENT_CANDIDATES.csv`.

Stage04 streaming artifacts:

- `<output_root>/04-mattergen-structure-sampler/streaming/candidates/<candidate_id>/STRUCTURE_SAMPLING_PLAN.csv`
- `<output_root>/04-mattergen-structure-sampler/streaming/candidates/<candidate_id>/SAMPLING_PARAMETERS.json`
- `<output_root>/04-mattergen-structure-sampler/streaming/candidates/<candidate_id>/SAMPLING_COMMANDS.md`
- Z-scheme uses
  `<output_root>/04-zscheme-mattergen-structure-sampler/streaming/candidates/<zscheme_id>/`.

Stage05 streaming artifacts:

- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/STRUCTURE_SAMPLING_PLAN.csv`
- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/EVALUATION_PLAN.csv`
- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/COMBINED_EVALUATION_PLAN.csv`
- `<output_root>/05-mattersim-structure-evaluator/streaming/batches/<batch_id>/STRUCTURE_EVALUATION_SUMMARY.csv`
- Z-scheme uses
  `<output_root>/05-zscheme-mattersim-structure-evaluator/streaming/batches/<batch_id>/`.

## Workflow

1. Initialize with `run_ows_streaming_scheduler.py init`.
2. Run `next-action`.
3. Maintain `recommendation_parallelism` active `stage02_recommender` agents
   when candidates are needed. The default is `4`. They are long-running
   continuous producers and must not wait for MatterGen or MatterSim outcomes
   before recommending again.
4. Each recommender loop iteration writes one candidate reasoning file and one
   candidate payload, then calls `register-candidate`. Before every
   recommendation, it rereads the current registry, historical results, and
   available Stage05/06/08/09/10 feedback under the same `output_root`.
5. If registration returns `duplicate_formula`, the same running recommender
   must recommend a different formula and retry. After a successful
   registration, the same recommender immediately starts its next loop
   iteration unless the parent/user has stopped it.
6. In parallel with recommendation, if the action list includes
   `claim_sampling`, claim one candidate and pass the emitted MatterGen
   preparation command to `structure_runner`.
7. After MatterGen structures exist, call `complete-sampling`.
8. In parallel with recommendation and MatterGen, if the action list includes
   `claim_mattersim_batch`, claim one eight-candidate batch. Prefer filling all
   free GPUs with distinct batches. Each batch binds to exactly one GPU, and
   each GPU runs at most one MatterSim process.
9. Execute the emitted MatterSim preparation command, execute the generated
   combined evaluation command, rerun preparation for post-processing, then
   call `complete-mattersim-batch`.
10. Stage06/07/08/09/10 consume `reliable` candidates from the registry and
    Stage05 batch summaries.

## Parent Event Loop Invariant

The parent/main agent must track active `stage02_recommender` runs separately
from GPU work. On startup, spawn enough recommenders to reach
`recommendation_parallelism`. Each `stage02_recommender` is expected to keep
running and repeatedly call `register-candidate`; a successful registration is
progress from that running agent, not a completion signal. Start a replacement
only when a recommender exits, fails, is interrupted, or the reported active
count otherwise drops below the target. This replacement rule is independent of
whether MatterGen, MatterSim, Stage06, Stage07, Stage08, Stage09, or Stage10
has completed.

The GPU side is a consumer loop. It should claim MatterGen work whenever
accepted candidates and suitable GPU capacity exist, and claim MatterSim work
whenever at least `mattersim_batch_size` sampled candidates exist. GPU work must
not block Stage02 producer replacement.

## Stage02 Rules

Stage02 recommendation content must be written by `stage02_recommender`.

Each `stage02_recommender` recommends exactly one top-level wet-lab screening
unit per loop iteration:

- `single-photocatalyst`: one `candidate_id` with one concrete
  `main_photocatalyst` formula.
- `zscheme`: one `zscheme_id` with covered HER/OER component rows in the
  candidate payload.

Before `register-candidate`, the subagent must write one candidate-specific
reasoning file with at least 500 Chinese characters. Registration is atomic:
the scheduler opens a SQLite transaction, checks `branch + formula_key`, and
inserts the candidate only if no existing candidate has that normalized formula.
If duplicate, the subagent must recommend another catalyst.

## Branch Rules

For single-photocatalyst records, Stage05 rows with
`recommended_return_step=proceed_to_experimental_validation` and continuing
Stage02 mechanism support are reliable recommendation results.

For Z-scheme records, Stage05 remains component-level only. Stage06 decides
whole-system reliability.

`target_recommendation_count` is only a loop-stop setting. It must not be
satisfied with failed, diagnostic, pending, or unevaluated records.

## Reference Data Boundary

`data/reference/` is reserved for Stage07 `catalyst-novelty-evaluator` only.
Stages 00-06 must not read `data/reference/` or
`data/reference/mp_required_properties_with_cif.xlsx`, and must not use
reference novelty to generate, rank, filter, reject, or route candidates in the
same recommendation/evaluation pass.

Stage07 never filters MatterSim-passed recommendations and never changes
Stage05/Stage06 pass/fail decisions.

## Failure Handling

- Missing knowledge base: block and record the missing input.
- Missing confirmed GPU ID before Stage04/Stage05: ask the user and do not
  start GPU work.
- Duplicate formula at registration: reject the write and have the same running
  `stage02_recommender` choose a different candidate and retry.
- MatterGen failure: mark the candidate `sampling_failed`; a recommender may
  use the failure reason for future candidate design.
- MatterSim failure: mark the batch `eval_failed`, release its GPU lease, and
  mark affected candidates `eval_failed`.
- Stale GPU lease: use `release-gpu` only after verifying no process is still
  using that GPU for the recorded batch.

## Required Main-Agent Procedure

1. Read this skill before starting or resuming an OWS workflow.
2. Use the streaming scheduler for `init`, `inspect`, `next-action`,
   `register-candidate`, `claim-sampling`, `complete-sampling`,
   `claim-mattersim-batch`, `complete-mattersim-batch`, and `release-gpu`.
3. Spawn the custom agent named by scheduler `next_action`; instruct it to read
   the relevant downstream skill before acting.
4. Do not mark GPU work complete from a subagent message alone; update the
   registry only after artifacts exist.
5. Keep `<output_root>/` as the only location for intermediate and final
   workflow artifacts.
6. Keep human-readable workflow reports in Chinese. Preserve file names,
   schema keys, CSV headers, paths, material IDs, formulas, and controlled enum
   values exactly.
7. When the user asks to save the conversation, dialog output, run log, or
   transcript, write or update `<output_root>/AGENT_RUN_TRANSCRIPT.md`.

## Subagent Return Contract

Stage02 recommenders are long-running and may report progress after each
successful registration, but they should continue recommending unless stopped.
When a stage subagent returns a final status to the main agent, include these
fields:

- `agent`
- `stage`
- `candidate_id` or `batch_id`
- `recommendation_branch`
- `status`
- `start_time` and `end_time` when known
- `artifact_paths`
- relevant counts such as candidate count, sampled count, evaluated count,
  reliable recommendation count, retained count, blocked count, or source-liquid
  count
- `blocker` or `failure_diagnostics` when not complete
- concise Chinese summary
