The user wants a complete FusionFlow G4 workflow with the following structure:

1. Input: request
2. A scoring Step that consumes request and produces score
3. An accept handler and a review handler, both consuming request, both must execute on every run
4. A conditional selection: if score >= 80, select accept_result, otherwise select review_result
5. A final Step consumes the selected value and produces final_result

Let me model this carefully:

- `request` is the input Artifact
- `score` is produced by the scoring step
- `score_value` as an Integer or Artifact? The score is an Artifact produced by a Step. The comparison `score >= 80` needs to compare Artifact values... In the grammar, comparisons in formulas use `comparisonOp` which includes `>=`. But the terms in a comparison must be `term comparisonOp term`, and terms include `atomicTerm` which includes `constantName` (which can be a NUMBER). So I can use `score_artifact >= 80` where the numeric literal 80 is the comparison RHS.

Wait, let me re-read the grammar carefully. `comparison` is `term comparisonOp term`. The `term` rule includes `atomicTerm` which includes `constantName`. And `constantName` includes `NUMBER`. So yes, I can write `score_artifact >= 80` as a comparison in a formula.

But wait - `score` would be an Artifact. The comparison in a formula is between terms. An Artifact value... Let me think about what the grammar actually allows.

The grammar says:
- `term` can be `operatorCall`, `listLiteral`, arithmetic, `ifExpression`, or `atomicTerm`
- `atomicTerm` is `constantName | booleanLiteral`
- `constantName` is `NUMBER | RELATIVE_PATH_ID | QUOTEDCONSTANTID | LOWID`

So an artifact name like `score` is a `LOWID`, which is an `identifier`, which through `constantName` is an `atomicTerm`, which is a `term`. And `80` is a `NUMBER`, also a `constantName`, also a `atomicTerm`, also a `term`. So `score >= 80` is a valid comparison.

Now for the conditional selection. The `if(formula, then_term, else_term)` syntax selects a term. In the ASSERT_EQ assertion, the RHS can be a term. So I can write something like:

```fusionflow
consumes(final_step) == [if(score >= 80, accept_result, review_result)];
```

Wait, but `consumes(Step) -> List` returns a List. The RHS of `consumes(step) == [if(formula, term, term)]` - the term inside the list literal... The `ifExpression` is a `term`, and the listLiteral contains `termList?` which is `term (COMMA term)*`. So `[if(formula, term, term)]` is a valid list literal containing one term that is an ifExpression.

Now for the data flow:

- `consumes(scoring_step) == [request]` produces `score`
- `consumes(accept_handler) == [request]` produces `accept_result`
- `consumes(review_handler) == [request]` produces `review_result`
- `consumes(final_step) == [if(score >= 80, accept_result, review_result)]` produces `final_result`

But wait - both accept_handler and review_handler need to execute on every run. With the conditional selection, the final_step only consumes one of them. But the runtime should still execute all Steps that produce artifacts, regardless of whether those artifacts are consumed downstream. The data flow edges define dependencies and execution order. Since accept_handler and review_handler don't depend on anything that depends on the conditional, they will execute when their inputs are ready.

Actually, looking at the skill: "Fan-out: Several Steps each use `consumes(step) == [shared_artifact]`" and "Model sequencing through Artifact edges: a Step that produces an Artifact precedes a Step that consumes it." Both accept_handler and review_handler consume request, so they fan out from request. They both execute.

The conditional if selects which artifact to pass to the final_step. The runtime should handle this - the final_step's dependency is on the output of the if evaluation, which resolves at runtime to one of the two artifacts.

Now for the model. Let me think about the constant types:

- `request: Artifact`
- `score: Artifact`
- `accept_result: Artifact`
- `review_result: Artifact`
- `final_result: Artifact`
- `scoring_step: Step`
- `accept_handler: Step`
- `review_handler: Step`
- `final_step: Step`
- Various name constants like `scoring_name: StepName`
- Various instruction constants like `scoring_instruction: Instruction`
- Executor agents like `scoring_agent: Agent, Executor`, etc.
- `accept_config_agent: Agent, Executor` etc.
- `review_config_agent: Agent, Executor`
- `final_agent: Agent, Executor`

Also need Model, Engine, ApiBase, ReasoningEffort, Tool constants for agent_config.

Let me also include `step_timeout`, `max_attempts`, etc. as optional.

For the numeric comparison in the formula: `score >= 80`. But `score` is an Artifact... In the grammar there's no type constraint on comparison terms - the checker would validate that both are comparable. Let me write it as written.

Actually, let me reconsider. The `score` artifact will contain a numeric value at runtime. The grammar allows the comparison `score >= 80` with the numeric literal 80. The checker should handle type compatibility.

Let me also make sure I include proper agent configuration. I need to declare:
- scoring_agent, accept_agent, review_agent, final_agent as `Agent, Executor`
- review_model: Model
- review_engine: Engine
- review_api: ApiBase
- default_effort: ReasoningEffort (maybe low or none)
- read_tool: Tool (or some tool)

Actually, the user said "real executable semantics" - I should make this complete. Let me write the full workflow.

Let me build this out:

```fusionflow
-- SCENARIO: request scoring with conditional routing
-- AUTHORED: 2025-01-01 from intent: "score, accept, review, conditional routing"

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

const scoring_model: Model;
const engine: Engine;
const api: ApiBase;
const low_effort: ReasoningEffort;
const high_effort: ReasoningEffort;
const read_tool: Tool;

workflow request_scoring {
  -- DATA FLOW
  input_workflow(request_scoring) == [request];
  consumes(scoring_step) == [request];
  produces(scoring_step) == [score];
  consumes(accept_handler) == [request];
  produces(accept_handler) == [accept_result];
  consumes(review_handler) == [request];
  produces(review_handler) == [review_result];
  consumes(final_step) == [if(score >= 80, accept_result, review_result)];
  produces(final_step) == [final_result];
  output_workflow(request_scoring) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(scoring_step) == scoring_agent;
  step_executor(accept_handler) == accept_agent;
  step_executor(review_handler) == review_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(scoring_step) == scoring_name;
  step_instruction(scoring_step) == scoring_instruction;
  step_timeout(scoring_step) == 300;
  max_attempts(scoring_step) == 2;
  step_name(accept_handler) == accept_handler_name;
  step_instruction(accept_handler) == accept_instruction;
  step_timeout(accept_handler) == 300;
  max_attempts(accept_handler) == 2;
  step_name(review_handler) == review_handler_name;
  step_instruction(review_handler) == review_instruction;
  step_timeout(review_handler) == 300;
  max_attempts(review_handler) == 2;
  step_name(final_step) == final_step_name;
  step_instruction(final_step) == final_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(request_scoring) == 3;
  workflow_timeout(request_scoring) == 900;

  -- AGENT CONFIGURATION
  agent_config(scoring_agent, scoring_model, engine, api);
  agent_config(accept_agent, scoring_model, engine, api);
  agent_config(review_agent, scoring_model, engine, api);
  agent_config(final_agent, scoring_model, engine, api);

  allowed_tool(scoring_agent, read_tool);
  allowed_tool(accept_agent, read_tool);
  allowed_tool(review_agent, read_tool);
  allowed_tool(final_agent, read_tool);
  reasoning_effort(scoring_agent) == low_effort;
  reasoning_effort(accept_agent) == high_effort;
  reasoning_effort(review_agent) == high_effort;
  reasoning_effort(final_agent) == low_effort;
}
```

This looks complete. Let me also add `max_output_tokens` or `temperature` if needed... The grammar lists them as optional. I'll keep it clean.

One concern: the `score` artifact is an Artifact. In the formula `score >= 80`, the comparison is between an Artifact and a number. The checker needs to support comparing an Artifact's runtime value to a numeric literal. The grammar allows this syntactically (both are `term`), and the skill explicitly says to use comparisons with numeric literals. This is a valid construction per the grammar.

Let me also consider the `agent_config` - it takes (Agent, Model, Engine, ApiBase). I have `scoring_model` as a Model - should each agent have a different model constant, or share one? For simplicity I used `scoring_model` for all, but that's fine for the data flow.

Actually, let me be more precise about the model. I should use `review_model` as the Model constant name since the skill's in-context example does that. But since there's no separate model per agent needed here, I'll just share one model constant.

Let me finalize. The workflow looks correct per the grammar:

1. All required declarations: ✓
2. Data-flow assertions (input_workflow, consumes, produces, output_workflow): ✓
3. Conditional term selection with `if(formula, then_term, else_term)`: ✓
4. Executor bindings: ✓
5. StepName values: ✓
6. Instruction values: ✓
7. All four handlers execute (scoring, accept, review all consume request): ✓

This should be a valid, complete FusionFlow G4 workflow.