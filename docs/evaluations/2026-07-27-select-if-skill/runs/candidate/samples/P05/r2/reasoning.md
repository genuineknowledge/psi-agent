We need to write a FusionFlow G4 workflow as specified. The user wants:

- input: request (Artifact)
- routing Step produces tone_flag and risk_flag
- four eager handler Steps: formal, casual, safe, escalated each consume request, produce respective results
- two conditional selections using if:
  1. selected_tone = if(tone_flag = formal_tone, formal_result, casual_result)
  2. selected_risk = if(risk_flag = high_risk, escalated_result, safe_result)
- final Step consumes both selected_tone and selected_risk, produces final_result
- Include all declarations, data flow, executor bindings, StepName values, Instruction values.

We must follow the G4 grammar: use `==` for assertions, `=` for comparisons in formulas. Use proper concept names (UpperCamelCase for concepts, lower_snake_case for constants/identifiers).

Declare needed constants: All artifacts, steps, step names, instructions, executors, etc. The workflow needs typed constants for the flags and criteria as well? The conditions compare tone_flag (an Artifact) to formal_tone (another Artifact/constant). Similarly risk_flag to high_risk. Those comparison target constants should be Artifact type? In the if example, they used `review_observation = review_criterion`, and those are both Artifact. So we need to declare `formal_tone` and `high_risk` as Artifact constants? But they are not produced by any step; they are just values used for comparison. In the example, they had `primary_criterion`, `review_criterion`, etc. as Artifact inputs. So we should declare them as input artifacts as well? The spec says "receives request" but also need to define those constants. We can make them inputs, or define them as constants but not produced. In FusionFlow, constants must be declared but they don't need to be produced by a step if they are external inputs. So I'll declare them as input artifacts.

Let me plan:

Inputs: request (Artifact), formal_tone (Artifact), high_risk (Artifact). However, "formal_tone" and "high_risk" seem like flag values, not artifacts. But for comparison syntax they must be Artifact type. I'll treat them as input Artifacts.

Alternate: we can declare them as constants directly with a value? The language doesn't have literal strings except quoted constants. Quoted constants are for IDs, not strings. But we can have them as Artifact inputs.

Let's follow the pattern from the priority_routing example: `primary_criterion`, `block_criterion`, `review_criterion`, `exception_criterion` are all input Artifacts. So we'll have `formal_tone`, `high_risk` as input artifacts.

Thus input_workflow = [request, formal_tone, high_risk]

Steps:
- routing_step: consumes request, produces [tone_flag, risk_flag]
- formal_handler: consumes [request], produces [formal_result]
- casual_handler: consumes [request], produces [casual_result]
- safe_handler: consumes [request], produces [safe_result]
- escalated_handler: consumes [request], produces [escalated_result]
- final_step: consumes [selected_tone, selected_risk], produces [final_result]

Need intermediate artifacts: selected_tone, selected_risk (Artifact).

Declare all.

Now executor bindings: we need Agent executors. Use names like routing_agent, formal_agent, etc. Declare Agent, Executor concepts.

Step names: routing_name, formal_name, casual_name, safe_name, escalated_name, final_name.

Instructions: routing_instruction, formal_instruction, etc.

All must be declared and used.

Now the conditional selections:

selected_tone == if(tone_flag = formal_tone, formal_result, casual_result);
selected_risk == if(risk_flag = high_risk, escalated_result, safe_result);

Then consumes(final_step) == [selected_tone, selected_risk];

Let's write the full workflow file.

We need to follow the exact order: DATA FLOW, EXECUTOR ASSIGNMENT, STEP CONFIGURATION, WORKFLOW CONFIGURATION, AGENT CONFIGURATION. In this case, no workflow config (concurrency/timeout) required, but we can set max_concurrency if needed. The spec doesn't mention concurrency; we can omit or set to something. We'll include workflow config minimally.

Also need agent_config for each agent. Use the pattern: agent_config(agent, model, engine, api_base). We need to declare model, engine, api_base typed constants.

Also allowed_tool? Not required. reasoning_effort? Not required.

Let's write it all.

Make sure to include the scenario comment header.

Now produce the output in a fenced block labeled fusionflow.