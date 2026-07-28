The user wants a complete FusionFlow G4 workflow for four-level priority routing. Let me carefully model this:

1. Input: `request` artifact
2. Triage Step: produces `critical_flag`, `legal_flag`, `review_flag`
3. Four handler Steps: `critical_handler`, `legal_handler`, `review_handler`, `fallback_handler` - all consume `request` and produce their respective results. ALL FOUR must execute eagerly.
4. Priority selection via `if` expressions:
   - `critical_flag` true → `critical_result`
   - else `legal_flag` true → `legal_result`
   - else `review_flag` true → `review_result`
   - else `fallback_result`
5. Final Step consumes chosen result → `final_result`

I need to model the flags as `Artifact` type (they are the values produced by triage step and compared in formulas). The `if` chaining needs to be done with intermediate named Artifacts as the grammar requires.

Let me think about the flag comparison. The triage step produces critical_flag, legal_flag, review_flag. The condition checks if `critical_flag = True` or similar. But wait - the flags are Artifacts and the comparison is with boolean literals. Let me use `critical_flag = True` (using `=` which is NUMERIC_EQ for comparison).

Actually, looking at the grammar more carefully:
- `comparisonOp` includes `NUMERIC_EQ : '=';` for equality comparison in formulas
- `booleanLiteral : TRUE | FALSE;` is a valid term

So `critical_flag = True` should work as a comparison in a formula.

For the chained if: `if(critical_flag = True, critical_result, ...)` then nested.

Let me structure it:

1. `selected_result == if(critical_flag = True, critical_result, legal_or_fallback);`
2. `legal_or_fallback == if(legal_flag = True, legal_result, review_or_fallback);`
3. `review_or_fallback == if(review_flag = True, review_result, fallback_result);`

So I need intermediate artifacts: `legal_or_fallback` and `review_or_fallback`.

Let me write the complete workflow now.

The user wants "real executable semantics" - I need to include all declarations, data flow, executor assignments, StepName values, and Instruction values.

Let me declare all constants needed:

Artifacts:
- request
- critical_flag, legal_flag, review_flag
- critical_result, legal_result, review_result, fallback_result
- legal_or_fallback, review_or_fallback, selected_result
- final_result

Steps:
- triage_step
- critical_handler_step, legal_handler_step, review_handler_step, fallback_handler_step
- final_step

StepNames:
- triage_name, critical_handler_name, legal_handler_name, review_handler_name, fallback_handler_name, final_name

Instructions:
- triage_instruction, critical_instruction, legal_instruction, review_instruction, fallback_instruction, final_instruction

Agents (Executors):
- triage_agent, critical_handler, legal_handler, review_handler, fallback_handler, final_agent

Models, engines, etc. for agent_config.

Let me also add named Artifact selection for the flags - actually the triage Step produces them as Artifacts directly.

Let me write this out fully.