The user wants a complete FusionFlow G4 workflow that:

1. Receives a `request` artifact
2. A scoring Step consumes `request` and produces `score`
3. Both an accept handler and a review handler consume `request` and produce `accept_result` and `review_result` respectively
4. Select `accept_result` when `score >= 80`, otherwise `review_result`
5. A final Step consumes the selected value and produces `final_result`

Let me model this carefully:

- `request` is input
- `score` is produced by a scoring step
- `accept_result` and `review_result` are produced by two parallel steps that both consume `request`
- The selection uses `if(score >= 80, accept_result, review_result)` as the value consumed by the final step
- Final step produces `final_result`

Let me write the complete workflow:

```fusionflow
-- SCENARIO: Score-based routing: accept when score >= 80, otherwise review
-- AUTHORED: from intent: "score-based routing workflow with if condition"

const request: Artifact;
const score: Artifact;
const accept_result: Artifact;
const review_result: Artifact;
const final_result: Artifact;

const scoring_step: Step;
const accept_handler: Step;
const review_handler: Step;
const final_step: Step;

const scoring_name: StepName;
const accept_name: StepName;
const review_name: StepName;
const final_name: StepName;

const scoring_instruction: Instruction;
const accept_instruction: Instruction;
const review_instruction: Instruction;
const final_instruction: Instruction;

const scoring_agent: Agent, Executor;
const accept_agent: Agent, Executor;
const review_agent: Agent, Executor;
const final_agent: Agent, Executor;

workflow score_routing {
  -- DATA FLOW
  input_workflow(score_routing) == [request];
  consumes(scoring_step) == [request];
  produces(scoring_step) == [score];
  consumes(accept_handler) == [request];
  produces(accept_handler) == [accept_result];
  consumes(review_handler) == [request];
  produces(review_handler) == [review_result];
  consumes(final_step) == [if(score >= 80, accept_result, review_result)];
  produces(final_step) == [final_result];
  output_workflow(score_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(scoring_step) == scoring_agent;
  step_executor(accept_handler) == accept_agent;
  step_executor(review_handler) == review_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(scoring_step) == scoring_name;
  step_instruction(scoring_step) == scoring_instruction;
  step_name(accept_handler) == accept_name;
  step_instruction(accept_handler) == accept_instruction;
  step_name(review_handler) == review_name;
  step_instruction(review_handler) == review_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- AGENT CONFIGURATION (required for Agent-backed steps)
  agent_config(scoring_agent, scoring_agent, scoring_engine, scoring_api) == True;
  agent_config(accept_agent, accept_agent, accept_engine, accept_api) == True;
  agent_config(review_agent, review_agent, review_engine, review_api) == True;
  agent_config(final_agent, final_agent, final_engine, final_api) == True;

  -- WORKFLOW CONFIGURATION
  max_concurrency(score_routing) == 3;
  workflow_timeout(score_routing) == 600;
}
```

Wait, I need to also declare the Model, Engine, ApiBase constants for agent_config. Let me also think about whether the condition is right. The grammar says `comparison` uses `NUMERIC_EQ` which is `=`, not `==`. So `score >= 80` is correct syntactically - `>=` is the GTE operator.

Actually wait, looking at the grammar more carefully in the comparison expression: `comparisonOp` includes `GTE : '>=';`. So `score >= 80` is valid as a formula within an `if` expression.

But I need to make sure `score` is a declared Artifact and `80` is a number literal (NUMBER token from the grammar). The grammar says `constantName` can be `NUMBER`. So `80` is a valid term in a comparison.

Let me also check: in the `if` expression, the formula is `score >= 80` - this is a `comparison` rule: `term comparisonOp term`. `score` is a LOWID constantName, `>=` is GTE, `80` is a NUMBER constantName. Valid.

Now let me also think about the agent_config - I need to declare the Model, Engine, ApiBase constants. Let me do it cleanly.

Actually, looking at the full-featured example in the skill, it declares:
```
const review_model: Model;
const review_engine: Engine;
const review_api: ApiBase;
const high_effort: ReasoningEffort;
const read_tool: Tool;
```

And then uses `agent_config(security_agent, review_model, review_engine, review_api);` as a standalone Bool-returning call (shorthand for `== True`).

Let me structure it more carefully. I'll use one shared Model, Engine, ApiBase for all agents since the example does that.

Let me rewrite more cleanly:

```fusionflow
-- SCENARIO: Score-based routing: accept when score >= 80, otherwise route to review
-- AUTHORED: from intent: "score-based routing workflow using if selection"

const request: Artifact;
const score: Artifact;
const accept_result: Artifact;
const review_result: Artifact;
const final_result: Artifact;

const scoring_step: Step;
const accept_handler: Step;
const review_handler: Step;
const final_step: Step;

const scoring_name: StepName;
const accept_name: StepName;
const review_name: StepName;
const final_name: StepName;

const scoring_instruction: Instruction;
const accept_instruction: Instruction;
const review_instruction: Instruction;
const final_instruction: Instruction;

const scoring_agent: Agent, Executor;
const accept_agent: Agent, Executor;
const review_agent: Agent, Executor;
const final_agent: Agent, Executor;

const shared_model: Model;
const shared_engine: Engine;
const shared_api: ApiBase;

workflow score_routing {
  -- DATA FLOW
  input_workflow(score_routing) == [request];
  consumes(scoring_step) == [request];
  produces(scoring_step) == [score];
  consumes(accept_handler) == [request];
  produces(accept_handler) == [accept_result];
  consumes(review_handler) == [request];
  produces(review_handler) == [review_result];
  consumes(final_step) == [if(score >= 80, accept_result, review_result)];
  produces(final_step) == [final_result];
  output_workflow(score_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(scoring_step) == scoring_agent;
  step_executor(accept_handler) == accept_agent;
  step_executor(review_handler) == review_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(scoring_step) == scoring_name;
  step_instruction(scoring_step) == scoring_instruction;
  step_name(accept_handler) == accept_name;
  step_instruction(accept_handler) == accept_instruction;
  step_name(review_handler) == review_name;
  step_instruction(review_handler) == review_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- AGENT CONFIGURATION
  agent_config(scoring_agent, shared_model, shared_engine, shared_api);
  agent_config(accept_agent, shared_model, shared_engine, shared_api);
  agent_config(review_agent, shared_model, shared_engine, shared_api);
  agent_config(final_agent, shared_model, shared_engine, shared_api);

  -- WORKFLOW CONFIGURATION
  max_concurrency(score_routing) == 3;
  workflow_timeout(score_routing) == 600;
}
```

This looks complete and correct. Let me verify each requirement:

1. ✅ Receives `request` - `input_workflow(score_routing) == [request];`
2. ✅ Scoring Step consumes `request` and produces `score` - `consumes(scoring_step) == [request]; produces(scoring_step) == [score];`
3. ✅ Both accept handler and review handler consume `request` - `consumes(accept_handler) == [request]; consumes(review_handler) == [request];`
4. ✅ They produce `accept_result` and `review_result` - `produces(accept_handler) == [accept_result]; produces(review_handler) == [review_result];`
5. ✅ Select `accept_result` when `score >= 80`, otherwise `review_result` - `consumes(final_step) == [if(score >= 80, accept_result, review_result)];`
6. ✅ Final Step consumes selected and produces `final_result` - `produces(final_step) == [final_result]; output_workflow(score_routing) == [final_result];`
7. ✅ All required declarations present
8. ✅ Data-flow assertions present
9. ✅ Executor bindings present
10. ✅ StepName values present
11. ✅ Instruction values present
12. ✅ Condition is executable workflow structure (using `if` expression with comparison `score >= 80`), not prose in instructions

The condition is proper G4 syntax - it's an `if` expression with a `comparison` formula `score >= 80`, and the terms `accept_result` and `review_result` are declared artifacts. This is exactly how the grammar and reference patterns specify term selection.

One thing - the user says "StepName values" and "Instruction values" - I have declared the typed constants and bound them with `==`. Good.

I think this is correct. Let me output only the fusionflow code fence.