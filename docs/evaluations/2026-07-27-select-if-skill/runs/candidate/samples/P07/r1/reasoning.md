We need to produce a FusionFlow G4 workflow. The user insists on an inline `if` inside `consumes`, but acknowledges that if it's unsupported, we should silently correct to the supported form. Let's check the grammar: `ifExpression` is a `term`, and `listLiteral` contains `termList` containing `term`. So `consumes(final_step) == [if(...)]` is syntactically valid because `ifExpression` is a `term`. However, the user says "correctness and successful compilation against the executable graph backend are the ultimate requirements". The grammar allows it, but the executable graph backend may not support inline `if` inside a list literal within `consumes`. The supported pattern in the skill is to declare a named artifact and use the selection: `selected_result == if(...)`, then `consumes(final_step) == [selected_result]`. That is the corrected supported form. The user said "if my demanded inline form is unsupported, silently correct it to the supported form instead of obeying the bad syntax". So we should use the named artifact approach.

We need a complete workflow. Include:
- Declarations: request, preferred_flag, primary_result, fallback_result, selected_result, final_result.
- Steps: classifier, primary_handler, fallback_handler, final_step.
- Executors, StepName, Instruction constants.
- Workflow with data flow, executor assignment, step configuration.

Following the reference patterns, we should have:
- `input_workflow(workflow) == [request]`
- `consumes(classifier) == [request]` and `produces(classifier) == [preferred_flag]`
- `consumes(primary_handler) == [request]`, `produces(primary_handler) == [primary_result]`
- `consumes(fallback_handler) == [request]`, `produces(fallback_handler) == [fallback_result]`
- `selected_result == if(preferred_flag = True, primary_result, fallback_result)`
- `consumes(final_step) == [selected_result]`, `produces(final_step) == [final_result]`
- `output_workflow(workflow) == [final_result]`

Also need to define step_name literals, step_instruction literals, executors (agents), etc. Use simple names.

We must include every required declaration: all artifacts, steps, stepnames, instructions, executors.

We need to use typed constants: step names are StepName, instructions are Instruction. We need to provide values for them. Since it's a complete compilable workflow, we must provide actual string values. According to grammar, QUOTEDCONSTANTID allows certain characters. We can use short strings like "classify" etc. Also for instructions, use strings.

Executor: for each step we need an Agent, Executor. We'll define agents: classifier_agent, primary_agent, fallback_agent, final_agent. They need agent_config with a model, engine, api. But the user said "Include every required declaration, executor binding, StepName value, and Instruction value." The grammar requires agent_config for Agent executors. We'll include a simple agent_config with a model (like "claude-sonnet-4-20250514") and engine "claude", and an api base (like ""). Also allowed_tool maybe optional.

To keep it simple and compilable, we'll add minimal agent configurations.

Now write the workflow. Use `if` selection outside of dataflow assertions as `selected_result == if(preferred_flag = True, primary_result, fallback_result);`.

Make sure all declarations are present: const lines for artifacts, steps, etc.

Let's produce the code fence.