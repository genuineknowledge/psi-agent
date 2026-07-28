We need to write a complete FusionFlow G4 workflow with two independent conditional selections (using `if`), as per the frozen grammar. We must follow the exact grammar rules and modeling patterns from the skill. The user explicitly says "Write a complete, compilable FusionFlow G4 workflow with real executable semantics and two independent conditional selections." They want:

- input: request
- routing Step produces tone_flag and risk_flag
- four eager handlers (formal, casual, safe, escalated) all consume request on every run (so each handler produces its respective result)
- then two independent if selections:
  - selected_tone = if(tone_flag = formal_tone, formal_result, casual_result)
  - selected_risk = if(risk_flag = high_risk, escalated_result, safe_result)
- one final Step consumes both selected values and produces final_result

We need to follow the "Named Artifact selection with if" pattern and the review example patterns. Also, we must include all declarations, data flow, executor bindings, StepName values, and Instruction values.

The skill says: "Do not hide either choice in prose." And "Return exactly one fusionflow code fence and nothing else."

We need to declare constants: request, tone_flag, risk_flag, formal_result, casual_result, safe_result, escalated_result, selected_tone, selected_risk, final_result as Artifact. Declare Steps: routing_step, formal_handler, casual_handler, safe_handler, escalated_handler, final_summary. StepNames, Instructions, Executors (Agent types). Also need constants for formal_tone, high_risk? They are comparison values. The grammar allows constantName which includes LOWID, NUMBER, etc. For formal_tone and high_risk, they need to be declared. But they are values to compare against, not Artifacts. They can be constants of some type? In the grammar, comparison uses term comparisonOp term, and term can be atomicTerm which is constantName or booleanLiteral. So we can declare constants like const formal_tone: Tone; but Tone is not a predefined concept. Alternatively, we can use quoted constants or just use LOWID as constants. The grammar doesn't enforce type at this level; the checker will handle. To keep it simple, we can declare them as plain constants: const formal_tone: Tone; const high_risk: RiskLevel; But we need concept names that exist. We can just use LOWID for the constants and refer to them in comparisons. The skill examples use things like `primary_criterion` as Artifact, but for values we can have Artifact constants? Actually the pattern shows comparison against criteria Artifacts. In the example, `primary_criterion` is an Artifact that is input from outside, and `primary_observation = primary_criterion`. Similarly we can have `formal_tone` and `high_risk` as input Artifacts. That makes sense: the workflow receives these as inputs. But the user says "The workflow receives request. A routing Step produces tone_flag and risk_flag." So request is input, tone_flag and risk_flag are produced by routing Step. For comparisons, we need reference values. We can declare them as workflow inputs: formal_tone, high_risk as Artifacts. That matches the pattern.

Thus input_workflow should include request, formal_tone, high_risk. Or we can treat formal_tone and high_risk as constants defined internally? The grammar doesn't have literal constants except numbers/booleans. To compare tone_flag = formal_tone, both must be terms. We could compare tone_flag with something like a string constant using a quoted constant? But the grammar says QUOTEDCONSTANTID only allows certain chars. That could work: `"formal"` as a quoted constant. But then the comparison would be `tone_flag = "formal"`. Is that valid? The grammar: term comparisonOp term, term can be atomicTerm which is constantName, constantName includes QUOTEDCONSTANTID. So yes, we can use `"formal"` as a literal value. However, the skill says "Treating quoted constants as prompt strings" is an anti-pattern but that's for instructions, not for comparison values. Also the grammar says "Restricted ID, not a general string: no whitespace or escape sequences." So `"formal"` is allowed. But maybe safer to use Artifacts as input.

Let's follow the priority_routing example: they have input Artifacts for criteria. We'll make formal_tone and high_risk input Artifacts.

Also need to include step_name and step_instruction for each step, with StepName and Instruction typed constants (LOWID names). We'll make up descriptive names and instructions.

For executor bindings, we need Agents. We'll declare agents for each step: routing_agent, formal_agent, casual_agent, safe_agent, escalated_agent, final_agent. We'll use agent_config with model, engine, apiBase. Need to declare those too. Also allowed_tool perhaps optional.

We need to produce final_result as output.

Now produce the code fence with the workflow. Ensure all declarations are present. Use proper syntax.

I'll write the workflow following the "Named Artifact selection with if" pattern and the review example style.