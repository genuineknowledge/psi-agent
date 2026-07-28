The user wants a complete FusionFlow G4 priority routing workflow. Let me analyze the requirements:

1. Input: one `request` artifact
2. Triage Step produces: `urgent_flag`, `allowed_flag`, `review_flag`, `exception_flag` (4 artifacts)
3. Three processing steps (primary, review, fallback) all consume `request` and produce their respective results
4. Final step selects based on conditions using `if` expressions:
   - if urgent_flag=true AND allowed_flag=true → primary_result
   - else if review_flag=true OR exception_flag=true → review_result
   - else → fallback_result
5. Final step consumes the selected result and produces `final_result`

Let me now write this properly. I need to be careful about the conditional term selection pattern from the skill. The canonical shape uses `if(formula, then_term, else_term)` in the `consumes` assertion.

For the formula:
- First condition: `(urgent_flag = True) AND (allowed_flag = True)` → primary_result
- Else: nested if with condition `(review_flag = True) OR (exception_flag = True)` → review_result
- Else: fallback_result

Let me write the full workflow now.

I need to declare all constants:
- Artifacts: request, urgent_flag, allowed_flag, review_flag, exception_flag, primary_result, review_result, fallback_result, final_result
- Steps: triage_step, primary_handler_step, review_handler_step, fallback_handler_step, final_step
- StepNames, Instructions, Executors (agents)
- Model, Engine, ApiBase, ReasoningEffort, Tool for agent config
- Also need True as a boolean literal that can be used in comparisons

Wait, I need to think about how comparisons work. The `comparison` rule is `term comparisonOp term`. So I need to compare `urgent_flag` with `True` (the boolean literal). In the grammar, `booleanLiteral` is a `true | false` token that maps to `True`/`true`/`TRUE`. So I can use `True` as a term.

Let me also think about the constant declarations. I need:
- `const urgent_flag: Artifact;` etc.
- `const True_: BoolValue;` or something to reference True? Actually, the grammar allows `True` as a boolean literal directly in formulas.

Hmm, but in the grammar, `booleanLiteral` is defined as a keyword. So `True` is actually a reserved keyword, not a constant. But in the formula comparison, I can use `urgent_flag = True` because `True` is a `booleanLiteral` which is an `atomicTerm` which is a `term`.

Wait, let me re-read the grammar more carefully:

```
atomicTerm
    : constantName
    | booleanLiteral
    ;
```

So `booleanLiteral` (`True`/`False`) is an `atomicTerm`. And `comparison` is `term comparisonOp term`. So `urgent_flag = True` should parse: `urgent_flag` is a constantName → atomicTerm → term, `True` is booleanLiteral → atomicTerm → term. That should work.

But wait, in the actual formula `if((urgent_flag = True) AND (allowed_flag = True), primary_result, ...)`, the `=` is the numeric equality comparison operator, not the assertion `==`. So this should be fine.

Now, I need to think about the `agent_config` - I need 5 agents. Let me use default names for the model, engine, etc.

Let me also think about the full structure. Following the reference pattern from the skill:

```fusionflow
const request: Artifact;
const urgent_flag: Artifact;
const allowed_flag: Artifact;
const review_flag: Artifact;
const exception_flag: Artifact;
const primary_result: Artifact;
const review_result: Artifact;
const fallback_result: Artifact;
const final_result: Artifact;

const triage_step: Step;
const primary_handler_step: Step;
const review_handler_step: Step;
const fallback_handler_step: Step;
const final_step: Step;

const triage_name: StepName;
const primary_name: StepName;
const review_name: StepName;
const fallback_name: StepName;
const final_name: StepName;

const triage_instruction: Instruction;
const primary_instruction: Instruction;
const review_instruction: Instruction;
const fallback_instruction: Instruction;
const final_instruction: Instruction;

const triage_agent: Agent, Executor;
const primary_agent: Agent, Executor;
const review_agent: Agent, Executor;
const fallback_agent: Agent, Executor;
const final_agent: Agent, Executor;

const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;
const default_effort: ReasoningEffort;
const read_tool: Tool;
```

Now the workflow body:

```fusionflow
workflow priority_routing {
  -- DATA FLOW
  input_workflow(priority_routing) == [request];
  
  consumes(triage_step) == [request];
  produces(triage_step) == [urgent_flag, allowed_flag, review_flag, exception_flag];
  
  consumes(primary_handler_step) == [request];
  produces(primary_handler_step) == [primary_result];
  
  consumes(review_handler_step) == [request];
  produces(review_handler_step) == [review_result];
  
  consumes(fallback_handler_step) == [request];
  produces(fallback_handler_step) == [fallback_result];
  
  consumes(final_step) == [
    if(
      (urgent_flag = True) AND (allowed_flag = True),
      primary_result,
      if(
        (review_flag = True) OR (exception_flag = True),
        review_result,
        fallback_result
      )
    )
  ];
  produces(final_step) == [final_result];
  output_workflow(priority_routing) == [final_result];
  
  -- EXECUTOR ASSIGNMENT
  step_executor(triage_step) == triage_agent;
  step_executor(primary_handler_step) == primary_agent;
  step_executor(review_handler_step) == review_agent;
  step_executor(fallback_handler_step) == fallback_agent;
  step_executor(final_step) == final_agent;
  
  -- STEP CONFIGURATION
  step_name(triage_step) == triage_name;
  step_instruction(triage_step) == triage_instruction;
  step_name(primary_handler_step) == primary_name;
  step_instruction(primary_handler_step) == primary_instruction;
  step_name(review_handler_step) == review_name;
  step_instruction(review_handler_step) == review_instruction;
  step_name(fallback_handler_step) == fallback_name;
  step_instruction(fallback_handler_step) == fallback_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;
  
  -- WORKFLOW CONFIGURATION
  max_concurrency(priority_routing) == 3;
  workflow_timeout(priority_routing) == 900;
  
  -- AGENT CONFIGURATION
  agent_config(triage_agent, default_model, default_engine, default_api);
  agent_config(primary_agent, default_model, default_engine, default_api);
  agent_config(review_agent, default_model, default_engine, default_api);
  agent_config(fallback_agent, default_model, default_engine, default_api);
  agent_config(final_agent, default_model, default_engine, default_api);
  
  allowed_tool(triage_agent, read_tool);
  reasoning_effort(triage_agent) == default_effort;
}
```

This looks good. The user explicitly said "每次执行都必须实际运行" for the three processing steps. The `if` expression only selects which artifact to pass as input to `final_step`, so all three processing steps still run (their `consumes`/`produces` edges are unconditional). Good.

Let me double check the grammar. The `ifExpression` is:
```
IF LPAREN formula COMMA term COMMA term RPAREN
```

So `if(condition, selected_term, else_term)` - the then and else branches are `term`, which can be a `constantName`. In my formula, `primary_result`, `review_result`, `fallback_result` are all constants (Artifacts). That should work.

For the formula inside `if`, I'm using parentheses around sub-formulas: `(urgent_flag = True) AND (allowed_flag = True)`. The grammar allows `LPAREN formula RPAREN` so this should be fine.

The outer parentheses around the entire `if` are needed? Let me check - the `ifExpression` is a `term`, so it can appear inside a `listLiteral` directly: `[if(...)]`. So `consumes(final_step) == [if(...)];` should parse correctly.

Wait, but `ifExpression` is a `term` and `listLiteral` contains `termList` which contains `term`. Yes, this should work.

I think this is complete and valid. Let me also check I have all the right concept names: Artifact, Step, StepName, Instruction, Agent, Executor, Model, Engine, ApiBase, ReasoningEffort, Tool - these should be in the catalog.

The grammar says `conceptName` starts with uppercase: `UPID : [A-Z][A-Za-z0-9_]*;`. So all concept names like `Artifact`, `Step`, `Executor` etc. are valid.

I'm good. Let me write the final answer.