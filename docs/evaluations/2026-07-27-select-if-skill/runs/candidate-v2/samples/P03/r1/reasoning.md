We need to author a FusionFlow G4 workflow based on the request. Let me follow the grammar and rules.

The user wants:

- input: request (Artifact)
- scoring Step: consumes request, produces score (Artifact)
- accept_handler Step: consumes request, produces accept_result (Artifact). Must execute on every run.
- review_handler Step: consumes request, produces review_result (Artifact). Must execute on every run.
- selection: if score >= 80, select accept_result, else select review_result
- final Step: consumes the selected result, produces final_result

Key requirements:
- Both accept_handler and review_handler must execute on every run. That means they cannot be conditionally skipped. They consume request and must run.
- The selection is eager: every candidate producer runs before selection.
- Use named Artifact selection with `if`: selected_artifact == if(condition, artifact_a, artifact_b);
- Both branches must be declared Artifacts. The selection result must also be a declared Artifact.
- Build conditions with numeric comparison operators. For numeric comparison, the comparison is `score = 80` or `score >= 80`. In the grammar, comparison operators are `NUMERIC_EQ` (`=`), `NOT_EQUALS` (`!=`), `LT` (`<`), `LTE` (`<=`), `GT` (`>`), `GTE` (`>=`). So we can use `score >= 80`. But note: `score` is an Artifact, and 80 is a numeric literal constantName. We can compare Artifact with number using the comparison operators. The formula is `score >= 80` where `score` is a constantName (Artifact) and `80` is also a constantName (NUMBER). That's valid.

- Must include all required declarations: Artifacts, Steps, StepName, Instruction, Executors (Agent, Executor). Use the canonical example shape.

- step_name and step_instruction must be set. Use declared StepName and Instruction constants. For Instruction, we need to provide a string. According to grammar, Instruction is a concept; its value can be a QUOTEDCONSTANTID or a RELATIVE_PATH_ID. But the skill says "Quoted constants are restricted IDs, not prose. Use declared StepName/Instruction identities or a `"./..."` instruction path; never place natural-language instructions in quotes." So for instruction, we should use a declared Instruction constant and then bind it. However, we need to provide an Instruction value. The grammar says constantName can be QUOTEDCONSTANTID. But the skill says not to put prose in quotes. However, the instruction for an agent step is basically the prompt. The skill says: "Use declared `StepName`/`Instruction` identities or a `"./..."` instruction path; never place natural-language instructions in quotes." Hmm, that seems to say we should use a path or an identity. But what identity? In the canonical example, they declare `const security_instruction: Instruction;` and then `step_instruction(security_review) == security_instruction;` — they don't set a value for `security_instruction`. That's odd. Actually, in the canonical example, they declare the Instruction constant but never assign a value. That implies the value would be provided at runtime via workflow inputs? But that's not shown. Alternatively, maybe the Instruction constant is just a named reference and the runtime resolves it from some external source? But the grammar doesn't have a way to assign a literal string to an Instruction constant directly. Looking at the canonical example more carefully: they declare `const security_instruction: Instruction;` but never set it to a value. That seems incomplete. However, the skill's code template shows `const work_instruction: Instruction;` and then `step_instruction(work_step) == work_instruction;` — again no value. So perhaps the Instruction is expected to be provided as a workflow input or configured externally. But the user asks for a complete, compilable workflow with "real executable semantics". We need to provide something that works.

Looking at the grammar: constantName can be a QUOTEDCONSTANTID, which is a restricted ID in quotes: `"` [A-Za-z0-9.!#$%?@_{|}~`]* `"`. So technically we could do `const scoring_instruction: Instruction = "score this request";`? No, the grammar doesn't have assignment syntax for constDecl; constDecl is just `CONST constantName COLON conceptNameList;` — no value assignment. So the value is not set in the source; it's set elsewhere (runtime config, inputs).

Given the user says "real executable semantics", I think we need to provide the instructions as actual values. But the grammar doesn't allow that. Perhaps the Instruction can be given as a workflow input? But the user wants everything in the source.

Wait, re-reading the skill: "Use declared StepName/Instruction identities or a `"./..."` instruction path". So for instruction, we could use a RELATIVE_PATH_ID like `"./instructions/score.txt"`. That would point to a file. But the user might not have that file. However, for the purpose of this exercise, we can provide a path and note that the instruction file must exist.

Alternatively, maybe we can set the Instruction to a quoted constant that serves as an identity, and the runtime resolves it from some registry. But that's speculative.

Given the constraints, I'll use the canonical pattern: declare Instruction constants and set step_instruction to those constants. I'll also set step_name to declared StepName constants. Since no assignment syntax exists in the grammar, the Instruction and StepName values are considered to be provided externally. But the user wants "real executable semantics". I think the expected answer uses the pattern from the skill.

Let me just follow the canonical example closely, adapting to this scenario. Use the same pattern with declared Instruction constants and step_instruction assertions.

Also, the condition: `if(score >= 80, accept_result, review_result)`. Need to ensure both accept_handler and review_handler run. Since they both consume request, and selection is eager, they will run. Good.

Data flow:
- input_workflow(workflow) == [request];
- consumes(scoring_step) == [request];
- produces(scoring_step) == [score];
- consumes(accept_handler) == [request];
- produces(accept_handler) == [accept_result];
- consumes(review_handler) == [request];
- produces(review_handler) == [review_result];
- selected_result == if(score >= 80, accept_result, review_result);
- consumes(final_step) == [selected_result];
- produces(final_step) == [final_result];
- output_workflow(workflow) == [final_result];

Declarations needed:
Artifacts: request, score, accept_result, review_result, selected_result, final_result
Steps: scoring_step, accept_handler, review_handler, final_step
StepNames: scoring_name, accept_name, review_name, final_name
Instructions: scoring_instruction, accept_instruction, review_instruction, final_instruction
Executors: each step needs an Agent, Executor (or could use different ones). In canonical example they use same model/engine for all. I'll declare four agents: scorer, acceptor, reviewer, editor (all Agent, Executor).

Also need agent_config for each agent. Use same model, engine, api.

Might also include max_concurrency? Not necessary but can be 2 or 3 since accept and review can run in parallel with scoring? Actually scoring must finish before selection, but accept and review can run in parallel with scoring since they don't depend on score. So concurrency could be 3. But not required.

Let me write the workflow. I'll keep it clean.

For the workflow name, I'll use `request_routing`.

Now, about the condition syntax: `if(score >= 80, accept_result, review_result)`. In the grammar, ifExpression is `IF LPAREN formula COMMA term COMMA term RPAREN`. formula can be comparison. comparison is `term comparisonOp term`. So `score >= 80` is valid: `score` is a term (constantName), `>=` is GTE operator, `80` is a term (NUMBER constantName). Good.

Also need to ensure we use `==` for assertions, `=` for comparison.

I'll include `max_concurrency(workflow) == 3` to allow parallel execution.

I'll also set `max_attempts` on some steps for robustness.

Let me craft the full source.

Note: I need to include `consumes` and `produces` for each step. The accept and review steps both consume request, so `consumes(accept_handler) == [request];` and `consumes(review_handler) == [request];`.

But careful: in the grammar, `consumes(Step) -> List`, and listLiteral is `LBRACK termList? RBRACK`. So `[request]` is a list with one term.

Also, `produces(scoring_step) == [score];` etc.

For `foreach_item` we don't need it.

Now, let's produce the code.