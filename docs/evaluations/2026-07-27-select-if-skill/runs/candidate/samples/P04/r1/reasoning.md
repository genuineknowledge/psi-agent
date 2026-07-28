We need to write a FusionFlow G4 workflow for four-level priority routing. The requirements:

- A triage Step that produces critical_flag, legal_flag, review_flag.
- Four handler Steps: critical_handler, legal_handler, review_handler, fallback_handler. All consume request and produce critical_result, legal_result, review_result, fallback_result.
- All four handlers execute eagerly on every run. So they must all be connected in the data flow and not skipped. In FusionFlow, all Steps whose producers are connected run; we need to ensure each handler has the required inputs and produces outputs.
- Priority selection using if expressions: final priority is critical_result when critical_flag is true, else legal_result when legal_flag is true, else review_result when review_flag is true, else fallback_result.
- A final Step consumes the chosen result and produces final_result.

We need to write the workflow with all declarations, data flow, executor assignments, StepName values, and Instruction values. The priority must be expressed in executable workflow structure using if expressions, not only in Step instructions.

We need to include complete declarations: artifacts, steps, step names, instructions, executors (agent executors), and workflow configuration.

We should follow the patterns from the reference: Named Artifact selection with if, where each candidate result is produced by a Step and if expressions chain to select one.

All four handlers must execute eagerly on every run — this is naturally satisfied because all handlers are connected as producers of their respective results, which are then used in if expressions. But we must ensure that the if expressions reference those results, so they are consumed. That ensures the handlers must run to produce the results.

The final Step consumes the selected result (which is a single artifact selected via if chain) and produces final_result.

Let's design the workflow name: priority_routing.

Declare artifacts:
- request: Artifact (input)
- critical_flag, legal_flag, review_flag: Artifact (outputs of triage)
- critical_result, legal_result, review_result, fallback_result: Artifact (outputs of handlers)
- chosen_result: Artifact (result of if chain)
- final_result: Artifact (output of final step)
- triage_step, critical_handler, legal_handler, review_handler, fallback_handler, final_step: Step
- triage_name, critical_name, legal_name, review_name, fallback_name, final_name: StepName
- triage_instruction, critical_instruction, legal_instruction, review_instruction, fallback_instruction, final_instruction: Instruction
- triage_agent, critical_agent, legal_agent, review_agent, fallback_agent, final_agent: Agent, Executor

Also need model, engine, api, reasoning effort, tool declarations for agent_config.

Data flow:
- input_workflow(priority_routing) == [request];
- consumes(triage_step) == [request];
- produces(triage_step) == [critical_flag, legal_flag, review_flag];
- Each handler consumes request:
  - consumes(critical_handler) == [request];
  - produces(critical_handler) == [critical_result];
  - similarly for legal, review, fallback.
- Then we need if expressions to select chosen_result:
  chosen_result == if(critical_flag = True, critical_result, if(legal_flag = True, legal_result, if(review_flag = True, review_result, fallback_result)));
- consumes(final_step) == [chosen_result];
- produces(final_step) == [final_result];
- output_workflow(priority_routing) == [final_result];

But note: the comparisons use = for equality in formulas. For boolean flag comparison, we should compare to True boolean literal or perhaps compare the flag artifact's value. The flag artifacts are Artifacts; we can compare them to a constant? The grammar says comparison is term comparisonOp term, term includes atomicTerm which can be constantName (including boolean literals). So we can do critical_flag = True. But we need a boolean literal constant. We can declare a constant like TrueBool as a boolean. Actually, we can just use True as a boolean literal in the formula. So it's valid: critical_flag = True.

But careful: The flag artifacts are produced by triage_step as Artifacts. They can be compared in formula. The if expression then selects the appropriate result.

We also need to ensure all handlers run eagerly. Since they all produce results that are referenced in the if chain, the if chain consumes them. However, the if chain only evaluates one branch? But in FusionFlow, if expressions are for value selection, and the evaluation of the if expression itself uses the formula to determine which branch to use. But both branches must be declared Artifacts, and the if expression's result is used downstream. However, the if expression itself does not cause the branches to be executed; the branches are produced by Steps that are already connected. The Steps are already in the graph because we have consumes(handler) and produces(handler) statements. So all handlers will run regardless of the if expression. The if expression just selects which artifact value to pass to final_step. This satisfies "all four handlers must execute eagerly on every run".

We need to include agent_config and allowed_tool for each agent. We can reuse the same model/engine/api for all.

Also include step_name, step_instruction, step_executor for each step.

Include workflow configuration: maybe max_concurrency? Could set to 2 or something to allow parallel execution of triage and handlers? Actually, handlers depend on request, but triage does not depend on handlers. So request is input, both triage and handlers can run in parallel if max_concurrency allows. We could set max_concurrency to 5 to allow all steps to run concurrently. But not required. We'll set max_concurrency(priority_routing) == 5.

Also step_timeout and max_attempts optional. We can set step_timeout for each step.

Let's write the complete workflow file.

We need to include comments for scenario and authored.

We'll follow the structure: data flow, executor assignment, step configuration, workflow configuration, agent configuration.

We need to declare all constants. The grammar says const declarations are at the top level.

List of constants:

Artifacts: request, critical_flag, legal_flag, review_flag, critical_result, legal_result, review_result, fallback_result, chosen_result, final_result.

Steps: triage_step, critical_handler, legal_handler, review_handler, fallback_handler, final_step.

StepNames: triage_name, critical_name, legal_name, review_name, fallback_name, final_name.

Instructions: triage_instruction, critical_instruction, legal_instruction, review_instruction, fallback_instruction, final_instruction.

Agents (Executor): triage_agent, critical_agent, legal_agent, review_agent, fallback_agent, final_agent.

For agent_config, we need to reference Model, Engine, ApiBase concepts. We can declare them as well: provider_model: Model; provider_engine: Engine; provider_api: ApiBase; high_effort: ReasoningEffort; read_tool: Tool.

Then agent_config(triage_agent, provider_model, provider_engine, provider_api); and allowed_tool(triage_agent, read_tool); reasoning_effort(triage_agent) == high_effort; etc.

We can set all agents to use the same model/engine/api.

Let's write the workflow.

One note: The if expression chain: chosen_result == if(critical_flag = True, critical_result, if(legal_flag = True, legal_result, if(review_flag = True, review_result, fallback_result))); This is valid per grammar: nested if expressions.

Now, I'll produce the complete fusionflow code.

Make sure all declarations are properly typed. Use the required concepts: Artifact, Step, StepName, Instruction, Agent, Executor, Model, Engine, ApiBase, ReasoningEffort, Tool.

Include a Bool/boolean constant for True? Actually, in the comparison we can use the boolean literal True directly. That's fine.

Now write.