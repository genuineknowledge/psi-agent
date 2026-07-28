We need to produce a FusionFlow G4 workflow. Let's parse the requirements:

- Input: request (Artifact)
- Scoring Step: consumes request, produces score (Artifact)
- Accept handler: consumes request, produces accept_result (Artifact)
- Review handler: consumes request, produces review_result (Artifact)
- Both accept and review handlers must execute on every run (so they are not conditionally skipped; they always run)
- Select accept_result when score >= 80, otherwise select review_result
- Final Step: consumes the selected value, produces final_result
- Output: final_result

This is the Named Artifact selection pattern: eager evaluation of both branches, then if condition selects which value passes downstream.

We need to model:
- `score` as an Artifact produced by scoring step
- `accept_result` and `review_result` produced by respective steps
- `selected_result` via if condition using `score >= 80`
- For score to be compared, we need a numeric literal. The condition: `score >= 80` where 80 is a numeric literal. Note: `score` is an Artifact. In the grammar, `if` formula uses comparisons on terms. But Artifact is a concept; we need a numeric constant to compare. Probably we declare a constant for the threshold.

Let's design:

Declarations:
- `request: Artifact;`
- `score: Artifact;`
- `accept_result: Artifact;`
- `review_result: Artifact;`
- `selected_result: Artifact;`
- `final_result: Artifact;`
- `scoring: Step;`
- `accept_handler: Step;`
- `review_handler: Step;`
- `final_step: Step;`
- `scoring_name: StepName;`
- `accept_name: StepName;`
- `review_name: StepName;`
- `final_name: StepName;`
- `scoring_instruction: Instruction;`
- `accept_instruction: Instruction;`
- `review_instruction: Instruction;`
- `final_instruction: Instruction;`
- `scoring_executor: Agent, Executor;` or just Executor? The requirement says executor bindings. Need to declare agents.
- We need a threshold constant: `threshold: Integer;` (the grammar supports NUMBER constant). Actually the grammar's constantName includes NUMBER. So we can declare `const threshold: Integer;` but Integer is a concept? The catalogue would have Integer. We can use numeric literal directly in formula. The condition: `score >= threshold` where threshold is a constant. But we need a number. We can use bare number: `score >= 80`. But formula uses term comparisons, term can be atomicTerm which includes constantName, and constantName includes NUMBER. But `score` is an Artifact, not number. Hmm, in the condition `score >= 80`, `score` is a constantName that is an Artifact. The comparison is between terms. The semantics is: the Artifact `score` contains a value, and we compare it with the literal 80. The runtime would resolve the comparison. The grammar permits term comparisonOp term. So `score >= 80` is syntactically valid as a comparison. We just need to ensure `score` is a declared Artifact constant and `80` is a NUMBER constant. So we can write:

`selected_result == if(score >= 80, accept_result, review_result);`

But `>=` is `GTE`. So fine.

Now we need `score >= 80` where `80` is a NUMBER literal. But in the grammar, NUMBER is a token, but in constantName rule, NUMBER is one alternative. So we can use `80` directly.

Declare all needed constants.

We need executor bindings: step_executor for each step. We need to declare Agent, Executor constants. Also need step_name and step_instruction for each step.

The full-featured example shows how to declare agents and executor. We'll follow that pattern.

We also need to handle the case where both accept and review handlers must execute on every run. In the dataflow, both consume request, and both produce accept_result and review_result. No condition on them. So they always run.

The selection is done via `if` in the dataflow: `selected_result == if(score >= 80, accept_result, review_result);`

Then final step consumes `[selected_result]`.

We must include all required declarations: StepName values and Instruction values. Since we don't have specific values, we use declared constants and assign them via step_name and step_instruction assertions. Same for agents and executor assignments.

Let's write the workflow. We'll use one workflow named `request_handler`.

We need to declare agents: we can declare one for scoring, one for accept, one for review, one for final. Or we can reuse? Better to declare separate for clarity.

Now write the fusionflow code. Ensure everything is syntactically correct per grammar.

Also include `--` comments as allowed.

Produce exactly one fenced block labelled fusionflow.

Let's proceed.