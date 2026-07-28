# Select-if skill evaluation results

## Scores

| Snapshot | Automatic oracle | Clean-subagent audit |
| --- | ---: | ---: |
| `baseline` | 2/16 (12.5%) | 2/16 (12.5%) |
| `candidate` | 12/16 (75.0%) | not audited |
| `candidate-v2` | 13/16 (81.25%) | 12/16 (75.0%) |

The final skill improves the conservative audited score from 2/16 to 12/16.
Automatic and manual results are reported separately; the higher automatic
score is not substituted for the manual result.

## Final-candidate failures

- `P01/r1`: `primary_category` was neither a workflow input nor produced by a
  Step, so the selector condition referenced an unbound Artifact.
- `P04/r2`: the named selector chain ended at `first_priority`, but the final
  Step consumed an unproduced residual alias named `selected_result`.
- `P05/r2`: `agent_config` used quoted model and URL values outside the
  grammar's restricted quoted-constant forms, so parsing failed.
- The manual audit additionally failed `P05/r1`: it promoted comparison
  criteria `formal_tone` and `high_risk` to workflow inputs even though the
  prompt specified only `request` as the external input boundary.

All other final-candidate samples passed the clean-subagent audit, including the
negative lazy-branch case: both repetitions refused to promise that unselected
handlers consume zero tokens.

## Raw archive integrity

| Snapshot | ZIP entries | Uncompressed bytes | ZIP bytes | SHA-256 |
| --- | ---: | ---: | ---: | --- |
| `baseline` | 232 | 9,407,162 | 1,119,257 | `103EDB0036DDF50AF00608B8CBCFA6BD24281FA2D993C1FA4E0486C8F552CDAA` |
| `candidate` | 232 | 7,838,545 | 1,084,562 | `897D2381D02904D08438E1127A0F78EFCC4D4F81402751AEB3663CF9F71` |
| `candidate-v2` | 216 | 10,306,072 | 1,172,893 | `32837F62809852FAB8EFC456F9D8626D884CCEC05992A4047270A4EF545FC842` |

Every ZIP entry was opened and read to EOF before unpacked duplicate SSE,
JSONL, and temporary-workspace files were removed. `candidate-v2` has fewer
entries because it used the corrected oracle from the start and therefore has
no `result.initial.json` copies.

## Reproducibility notes

- Model: `deepseek-v4-flash`
- Endpoint: official `https://api.deepseek.com/v1`
- Temperature: `0`
- Maximum output tokens: `32768`
- Repetitions: `2`
- Fresh process, Session, and session ID per sample
- One user turn per sample
- Retries, repairs, follow-up prompts, and selective reruns: disabled
- HTTP/stream/harness failures: `0/48`

See each run's `inputs/manifest.json`, `summary.json`, and sample metadata for
timestamps and frozen hashes.
