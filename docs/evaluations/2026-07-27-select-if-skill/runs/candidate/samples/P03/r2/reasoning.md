The user wants me to author a FusionFlow G4 workflow that:

1. Receives `request` as input
2. A scoring Step consumes `request` and produces `score`
3. Both an accept handler and a review handler consume `request` and produce `accept_result` and `review_result` respectively
4. Select between them based on `score >= 80` - accept when score >= 80, otherwise review
5. A final step consumes the selected value and produces `final_result`
6. The condition must be executable workflow structure (not hidden in prose)

This requires the `if(...)` expression for named artifact selection. Let me model this carefully.

The scoring step produces score. Then we need the comparison: `score >= 80`? But `score` is an Artifact, and `80` is a numeric literal. In the grammar, `if(...)` takes a formula which contains comparisons between terms. Terms can be constant names. So I need to declare `80` as a constant somehow - but in the grammar, constants are `constantName` which can be `NUMBER`. So I can use `80` directly in the comparison? Let me think...

In the grammar, `comparison: term comparisonOp term`, and `term` includes `atomicTerm : constantName | booleanLiteral`, and `constantName : NUMBER | ...`. So `80` is a valid term. And `score` is an Artifact (constantName with type Artifact). So `if(score >= 80, accept_result, review_result)` should work syntactically.

But wait - `score` is of concept `Artifact` and `80` is a `NUMBER` constant. The comparison operator `>=` compares terms, and terms include atomicTerms which include constantNames. So syntactically it's fine. The checker would need to verify that comparing an Artifact with a number is valid, but the grammar allows it. Since we're told to write a "compilable" workflow with "real executable semantics", I'll trust the syntax.

Now let me design the full workflow:

Declarations needed:
- `request: Artifact;`
- `score: Artifact;`
- `accept_result: Artifact;`
- `review_result: Artifact;`
- `selected_result: Artifact;`
- `final_result: Artifact;`
- Steps: `scoring_step`, `accept_handler`, `review_handler`, `final_step`
- StepName constants
- Instruction constants
- Executor/Agent constants
- Agent config details

Let me also declare the numeric literal 80 as a constant (could be inline in the if expression since NUMBER is a constantName).

Actually, let me look at the grammar more carefully. `comparison: term comparisonOp term`. `term` includes `atomicTerm : constantName`. `constantName : NUMBER | RELATIVE_PATH_ID | QUOTEDCONSTANTID | LOWID`. So `80` is a NUMBER, which is a valid constantName/atomicTerm. So `score >= 80` is syntactically valid.

Now, in the if expression: `if(formula, term, term)`. The formula would be `score >= 80`. And the then/else terms would be `accept_result` and `review_result` (both Artifact constant names).

Then `selected_result` is bound to the if result, and `consumes(final_step) == [selected_result]`.

Let me write this out fully:

```fusionflow
-- SCENARIO: Score-based routing workflow: accept when score >= 80, otherwise review
-- AUTHORED: 2025-07-17 10:00:00 from intent: "Score-based routing"

const request: Artifact;
const score: Artifact;
const accept_result: Artifact;
const review_result: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

const scoring_step: Step;
const accept_handler: Step;
const review_handler: Step;
const final_step: Step;

const scoring_step_name: StepName;
const accept_handler_name: StepName;
const review_handler_name: StepName;
const final_step_name: StepName;

const scoring_instruction: Instruction;
const accept_instruction: Instruction;
const review_instruction: Instruction;
const final_instruction: Instruction;

const scoring_agent: Agent, Executor;
const accept_agent: Agent, Executor;
const review_agent: Agent, Executor;
const final_agent: Agent, Executor;

const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;

workflow score_routing {
  -- DATA FLOW
  input_workflow(score_routing) == [request];
  consumes(scoring_step) == [request];
  produces(scoring_step) == [score];
  consumes(accept_handler) == [request];
  produces(accept_handler) == [accept_result];
  consumes(review_handler) == [request];
  produces(review_handler) == [review_result];
  selected_result == if(score >= 80, accept_result, review_result);
  consumes(final_step) == [selected_result];
  produces(final_step) == [final_result];
  output_workflow(score_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(scoring_step) == scoring_agent;
  step_executor(accept_handler) == accept_agent;
  step_executor(review_handler) == review_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(scoring_step) == scoring_step_name;
  step_instruction(scoring_step) == scoring_instruction;
  step_name(accept_handler) == accept_handler_name;
  step_instruction(accept_handler) == accept_instruction;
  step_name(review_handler) == review_handler_name;
  step_instruction(review_handler) == review_instruction;
  step_name(final_step) == final_step_name;
  step_instruction(final_step) == final_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(score_routing) == 3;
  workflow_timeout(score_routing) == 600;

  -- AGENT CONFIGURATION
  agent_config(scoring_agent, default_model, default_engine, default_api);
  agent_config(accept_agent, default_model, default_engine, default_api);
  agent_config(review_agent, default_model, default_engine, default_api);
  agent_config(final_agent, default_model, default_engine, default_api);
}
```

Wait, I need to double check - the user says "real executable semantics" and the score comparison must be in the workflow structure. The `if(...)` in FusionFlow is an expression that selects between two artifacts based on a formula. This is exactly the "named artifact selection" pattern from the skill.

The accept handler and review handler must both execute on every run - check. They both consume `request` independently; the `if()` selector then picks which result flows to the final step. This matches the requirement.

Let me make sure all declarations are complete. I need StepName values - I declared them as constants. They need to be typed. Same for Instruction. The grammar allows `const <name>: <concept>`.

I think this is complete and correct. Let me format it exactly as a single fusionflow code block.