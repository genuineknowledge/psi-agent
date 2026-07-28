# Clean-subagent audit ledger

Auditors received frozen case prompts and each assigned sample's `final.md`,
`source.ff`, and `metadata.json`. They did not inspect `result.json`. Verdicts
below were locked before comparison with automatic scores.

## Baseline

| Sample | Verdict | Reason |
| --- | --- | --- |
| P01/r1 | FAIL | Inline `if(...)` in final `consumes`; no named selector Artifact. |
| P01/r2 | FAIL | Inline `if(...)` in final `consumes`; no named selector Artifact. |
| P02/r1 | FAIL | Inline nested priority chain instead of two named selector Artifacts. |
| P02/r2 | FAIL | Inline nested priority chain instead of two named selector Artifacts. |
| P03/r1 | FAIL | Ordered `if(...)` is inline in final `consumes`. |
| P03/r2 | FAIL | Ordered `if(...)` is inline in final `consumes`. |
| P04/r1 | FAIL | Three inline nested selections instead of a three-Artifact named chain. |
| P04/r2 | FAIL | Three inline nested selections instead of a three-Artifact named chain. |
| P05/r1 | FAIL | Two inline selections in final `consumes`. |
| P05/r2 | FAIL | Two inline selections in final `consumes`. |
| P06/r1 | FAIL | Inline selection in `output_workflow`; no named output selector. |
| P06/r2 | FAIL | Inline selection in `output_workflow`; no named output selector. |
| P07/r1 | FAIL | Preserved the explicitly unsupported inline form from the adversarial prompt. |
| P07/r2 | FAIL | Preserved the explicitly unsupported inline form from the adversarial prompt. |
| N01/r1 | PASS | Direct refusal explains eager value selection and gives no approximation. |
| N01/r2 | PASS | Direct refusal explains eager value selection and gives no approximation. |

Baseline total: **2/16**.

## Final candidate (`candidate-v2`)

| Sample | Verdict | Reason |
| --- | --- | --- |
| P01/r1 | FAIL | Selector condition references unbound `primary_category`. |
| P01/r2 | PASS | One named selector, eager candidates, and final `[selected]` consumption. |
| P02/r1 | PASS | Two-level named priority chain with eager candidates. |
| P02/r2 | PASS | Two-level named priority chain with eager candidates. |
| P03/r1 | PASS | Named ordered selector using the numeric literal `80`. |
| P03/r2 | PASS | Named ordered selector; quoted IDs and relative paths are grammar-valid. |
| P04/r1 | PASS | Three-level named priority chain with four eager handlers. |
| P04/r2 | FAIL | Final Step consumes unproduced alias `selected_result`. |
| P05/r1 | FAIL | Adds `formal_tone` and `high_risk` as external workflow inputs. |
| P05/r2 | FAIL | Invalid quoted model/API values make the workflow unparsable. |
| P06/r1 | PASS | Named selector Artifact is itself the workflow output. |
| P06/r2 | PASS | Named selector Artifact is itself the workflow output. |
| P07/r1 | PASS | Correctly replaces adversarial inline syntax with a named selector. |
| P07/r2 | PASS | Correctly replaces adversarial inline syntax with a named selector. |
| N01/r1 | PASS | Refuses unsupported lazy execution and emits no workflow. |
| N01/r2 | PASS | Refuses unsupported lazy execution and emits no workflow. |

Final-candidate total: **12/16**.

This audit is score-blind but not fully snapshot-label-blind: auditors could see
the snapshot name in the filesystem path. Automatic and audit verdicts remain
separate in `RESULTS.md`.
