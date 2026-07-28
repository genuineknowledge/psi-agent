The user wants a FusionFlow G4 workflow for four-level priority routing. Let me carefully analyze the requirements:

1. Receives a `request` artifact as input
2. Triage Step produces `critical_flag`, `legal_flag`, `review_flag` (three artifacts)
3. Four handler Steps: each consumes `request` and produces their respective result
4. ALL four handlers must execute eagerly on every run (they can't be skipped)
5. Final priority selection using `if` expressions:
   - `critical_flag` is true → use `critical_result`
   - else `legal_flag` is true → use `legal_result`
   - else `review_flag` is true → use `review_result`
   - else → `fallback_result`
6. A final Step consumes the chosen result and produces `final_result`
7. All required declarations, data flow, executor assignments, StepName values, Instruction values

Key challenge: the `if` expression selects among artifacts, but we need to express comparisons. The grammar supports `=` for equality comparisons in formulas. But here "critical_flag is true" means we need to check if an artifact equals a boolean value. However, the grammar has `booleanLiteral` as `True`/`False`/`true`/`false`. And comparisons use `comparisonOp` which includes `NUMERIC_EQ` (`=`).

Wait, let me re-read. The formulas at the bottom use `comparison` which is `term comparisonOp term`. So I can compare an artifact to `True` using `=`.

The structure:

```fusionflow
consumes(final_step) == [
  if(
    critical_flag = True,
    critical_result,
    if(
      legal_flag = True,
      legal_result,
      if(
        review_flag = True,
        review_result,
        fallback_result
      )
    )
  )
];
```

But wait - the handlers must execute eagerly on every run. The `if` expression selects among them at the consumption point, but all four handlers produce their results. The key is that the handlers are separate Steps that produce their artifacts independently. The `if` only selects which artifact to consume as input to the final step.

Since all four handler Steps produce their artifacts (they have `produces` declarations), and they're all consumed somewhere (if not directly by the final step, then... hmm, the `if` expression itself references the artifacts, which means they need to be available. The runtime should recognize that all four artifacts need to be produced because they're referenced in the graph.

Actually, looking at the canonical example more carefully - the `if` expression selects a term. The terms are artifact references. The runtime needs to resolve all referenced artifacts, which means all handler steps must execute. The `if` just selects which one is passed as input to the consuming step.

So the structure is:
- `triage_step` produces `[critical_flag, legal_flag, review_flag]`
- Each handler step consumes `[request]` and produces its result
- `final_step` consumes `[if(...)]` - the if selects among the four handler results
- `final_step` produces `[final_result]`

For the handler steps to be eager, they just need to be declared as Steps that produce artifacts. Since their results are referenced in the `if` expression that's part of the `consumes` assertion for the final step, the runtime should understand that all those artifacts need to be available.

Let me write the complete workflow. I need:
- Declarations: artifacts, steps, StepNames, Instructions, Executors, etc.
- Workflow block with all assertions

Let me think about what concepts I need:
- Artifact: request, critical_flag, legal_flag, review_flag, critical_result, legal_result, review_result, fallback_result, final_result
- Step: triage_step, critical_handler, legal_handler, review_handler, fallback_handler, final_step
- StepName: for each step
- Instruction: for each step
- Executor (Agent): for each step
- Model, Engine, ApiBase, ReasoningEffort, Tool for agent config

Let me craft this carefully.

For the triage step producing three artifacts, that's fine with `produces(triage_step) == [critical_flag, legal_flag, review_flag]`.

Now for the handlers - all four must be Steps that execute eagerly. They all consume `request` and produce their result. The `if` in `consumes(final_step)` references all four handler results, which means all four must be available.

Let me write it:

```fusionflow
-- SCENARIO: four-level priority routing

const request: Artifact;
const critical_flag: Artifact;
const legal_flag: Artifact;
const review_flag: Artifact;
const critical_result: Artifact;
const legal_result: Artifact;
const review_result: Artifact;
const fallback_result: Artifact;
const final_result: Artifact;

const triage_step: Step;
const critical_handler: Step;
const legal_handler: Step;
const review_handler: Step;
const fallback_handler: Step;
const final_step: Step;

const triage_name: StepName;
const critical_handler_name: StepName;
const legal_handler_name: StepName;
const review_handler_name: StepName;
const fallback_handler_name: StepName;
const final_name: StepName;

const triage_instruction: Instruction;
const critical_instruction: Instruction;
const legal_instruction: Instruction;
const review_instruction: Instruction;
const fallback_instruction: Instruction;
const final_instruction: Instruction;

const triage_agent: Agent, Executor;
const critical_agent: Agent, Executor;
const legal_agent: Agent, Executor;
const review_agent: Agent, Executor;
const fallback_agent: Agent, Executor;
const final_agent: Agent, Executor;

const routing_model: Model;
const routing_engine: Engine;
const routing_api: ApiBase;
const standard_effort: ReasoningEffort;
const read_tool: Tool;

workflow priority_routing {
  -- DATA FLOW
  input_workflow(priority_routing) == [request];
  consumes(triage_step) == [request];
  produces(triage_step) == [critical_flag, legal_flag, review_flag];
  consumes(critical_handler) == [request];
  produces(critical_handler) == [critical_result];
  consumes(legal_handler) == [request];
  produces(legal_handler) == [legal_result];
  consumes(review_handler) == [request];
  produces(review_handler) == [review_result];
  consumes(fallback_handler) == [request];
  produces(fallback_handler) == [fallback_result];
  consumes(final_step) == [
    if(
      critical_flag = True,
      critical_result,
      if(
        legal_flag = True,
        legal_result,
        if(
          review_flag = True,
          review_result,
          fallback_result
        )
      )
    )
  ];
  produces(final_step) == [final_result];
  output_workflow(priority_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(triage_step) == triage_agent;
  step_executor(critical_handler) == critical_agent;
  step_executor(legal_handler) == legal_agent;
  step_executor(review_handler) == review_agent;
  step_executor(fallback_handler) == fallback_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(triage_step) == triage_name;
  step_instruction(triage_step) == triage_instruction;
  step_name(critical_handler) == critical_handler_name;
  step_instruction(critical_handler) == critical_instruction;
  step_name(legal_handler) == legal_handler_name;
  step_instruction(legal_handler) == legal_instruction;
  step_name(review_handler) == review_handler_name;
  step_instruction(review_handler) == review_instruction;
  step_name(fallback_handler) == fallback_handler_name;
  step_instruction(fallback_handler) == fallback_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(priority_routing) == 4;
  workflow_timeout(priority_routing) == 600;

  -- AGENT CONFIGURATION
  agent_config(triage_agent, routing_model, routing_engine, routing_api);
  agent_config(critical_agent, routing_model, routing_engine, routing_api);
  agent_config(legal_agent, routing_model, routing_engine, routing_api);
  agent_config(review_agent, routing_model, routing_engine, routing_api);
  agent_config(fallback_agent, routing_model, routing_engine, routing_api);
  agent_config(final_agent, routing_model, routing_engine, routing_api);

  allowed_tool(triage_agent, read_tool);
  allowed_tool(critical_agent, read_tool);
  allowed_tool(legal_agent, read_tool);
  allowed_tool(review_agent, read_tool);
  allowed_tool(fallback_agent, read_tool);
  reasoning_effort(triage_agent) == standard_effort;
  reasoning_effort(critical_agent) == standard_effort;
  reasoning_effort(legal_agent) == standard_effort;
  reasoning_effort(review_agent) == standard_effort;
  reasoning_effort(fallback_agent) == standard_effort;
}
```

Wait, I need to reconsider. The handlers execute eagerly because their artifacts are referenced in the `if` expression inside `consumes(final_step)`. The runtime must evaluate all terms referenced, which means all handlers must produce their artifacts. This is the correct approach.

Also, I should double-check: the triage step produces three artifacts. That's fine syntactically - `produces(triage_step) == [critical_flag, legal_flag, review_flag]` is a valid List literal.

The `if` expression is inside a List literal `[if(...)]` because `consumes` returns a List. That's correct per the grammar.

Let me also verify: the comparison is `critical_flag = True` using `NUMERIC_EQ` (`=`), which is correct for formulas.

This looks complete and correct. Let me output only the fusionflow code fence.