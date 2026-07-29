---
name: stage08-catalytic-performance-prover
description: >
  Use when Stage08-passed catalyst names/formulas, especially a 96-catalyst
  plate list, need catalytic-performance proof by external LLM calls. This
  skill must run the bundled LLM_proof/run_llm_proof.py script instead of
  writing proof paragraphs directly. The script calls one external model request
  per catalyst, runs requests concurrently, and writes a Markdown document plus
  an audit JSON.
---
# Stage08 Catalytic Performance Prover

## Required Action

Run `LLM_proof/run_llm_proof.py`. Do not manually write catalyst proof
paragraphs in the main agent response.

## Inputs

Use only the local JSON source needed to obtain catalyst names and formulas.
In the OWS workflow, pass the current round Stage08 index explicitly:

`<output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/ROUND_PARALLEL_SYNTHESIS_INDEX.json`

The input JSON must contain `retained_records` with catalyst names and formulas.
Pass the cumulative synthesis index explicitly with `--input-json`; do not rely
on a script default.

## Command

Use the repository's active Python environment (or another environment with the
project dependencies installed), provide the API key only through the current
shell environment or `--api-key`, and run:

```bash
export LLM_PROOF_API_KEY="..."
python skills/stage08-catalytic-performance-prover/LLM_proof/run_llm_proof.py \
  --input-json <output_root>/08-round-parallel-synthesis-advisor/rounds/<round_id>/ROUND_PARALLEL_SYNTHESIS_INDEX.json \
  --output <output_root>/10-catalytic-performance-prover/rounds/<round_id>/CATALYTIC_PERFORMANCE_PROOF.md \
  --concurrency 20
```

If the user provides an output path, pass it with `--output`.

## Script Behavior

The script uses `gpt-5.5` by default and the fixed default base URL
`https://api.chatanywhere.tech/v1`.

The script sends one API request per catalyst. Each request contains only the
single catalyst name/formula inserted into the script's prompt template.

The script writes:

- the Markdown proof document specified by `--output`;
- an audit JSON next to the Markdown output unless `--audit-json` is supplied,
  including model-judged count, indices, names, and formulas only for
  `无催化性能` conclusions;
- an optional JSONL checkpoint when `--checkpoint` is supplied.

## Completion Check

After the script finishes, verify that the Markdown document exists and that
the number of `### ` headings matches the number of input catalysts. Report the
output path and the audit JSON path to the user.
