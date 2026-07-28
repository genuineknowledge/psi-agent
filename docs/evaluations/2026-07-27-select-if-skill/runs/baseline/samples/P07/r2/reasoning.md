The user wants a FusionFlow G4 workflow with specific requirements. Let me parse their request carefully:

1. A classifier Step produces `preferred_flag` (an Artifact)
2. Primary and fallback handler Steps both consume request and produce results
3. Final step consumes the selected result

The user insists on `consumes(final_step) == [if(preferred_flag = True, primary_result, fallback_result)]` but says I should silently correct it to the supported form if it's unsupported.

Let me check the grammar and skill. The grammar supports `ifExpression` which can be used inside a list literal. The `if` syntax is `IF(formula, THEN, ELSE)` where the formula is composed of comparisons with `=`, `!=`, `<`, `<=`, `>`, `>=` combined with `!`, `AND`, `OR`.

The user's proposed form `if(preferred_flag = True, primary_result, fallback_result)` is:
- `preferred_flag = True` - this is a comparison of a term with a boolean literal. In the grammar, `comparison` is `term comparisonOp term`, and `True` is `booleanLiteral` which is an `atomicTerm`. So this is valid.
- `if(formula, term, term)` - matches the grammar's `ifExpression` rule.

But wait - there's a subtlety. In FusionFlow G4, `preferred_flag` is an Artifact. A comparison `preferred_flag = True` compares an Artifact with a boolean. The question is whether this comparison is meaningful/valid. But the user explicitly demands this shape, and the grammar allows it syntactically.

Also, the user says "a classifier Step produces preferred_flag" - so `preferred_flag` is an Artifact. And the `if` expression selects between two Artifacts based on comparing that artifact to `True`.

Let me re-read the condition: the user says `if(preferred_flag = True, primary_result, fallback_result)`. In the grammar, `=` is the `NUMERIC_EQ` token which is used for equality comparison in formulas. So `preferred_flag = True` is a valid comparison expression.

Actually, let me reconsider. The user says "silently correct it to the supported form if it's unsupported." The form `if(preferred_flag = True, primary_result, fallback_result)` is syntactically valid per the grammar. But the question is whether it's semantically correct. 

Looking at the skill's reference pattern for conditional selection:
```fusionflow
consumes(final_step) == [
    if(
      (primary_observation = primary_criterion) AND !(block_observation = block_criterion),
      primary_result,
      if(
        (review_observation = review_criterion) OR (exception_observation = exception_criterion),
        review_result,
        fallback_result
      )
    )
  ];
```

This shows that comparing two Artifacts with `=` is the intended pattern: `artifact_a = artifact_b`. So comparing `preferred_flag` to `True` (a boolean literal) is a comparison between an Artifact and a boolean literal. This should be fine syntactically.

But wait - let me think about whether comparing Artifact to boolean literal makes semantic sense. An Artifact's content could be compared to `True` in the formula. The grammar allows `booleanLiteral` as an `atomicTerm`. So `preferred_flag = True` means comparing the artifact `preferred_flag` to the boolean `True`.

Actually, I think comparing an Artifact to a boolean literal like `True` is fine in the formula syntax. The formula allows `comparison: term comparisonOp term`, and a term can be `atomicTerm` which includes `booleanLiteral`.

OK so the user's inline form is syntactically supported. But is it semantically correct for the backend? The skill says to correct it if it's unsupported by the backend. Let me think...

The canonical conditional selection example in the skill compares two Artifacts. The user's form compares an Artifact to `True`. This might actually work - the checker would validate concept compatibility. `True` is a boolean literal. `preferred_flag` is an Artifact. The comparison `=` checks if they're equal. If the artifact contains the value `true`, this works.

Actually, wait - I need to be more careful. In FusionFlow, `True` could be treated as a boolean value. The grammar says `booleanLiteral: TRUE | FALSE`. So `True` is a valid term. Comparing an Artifact to `True` - the checker would need to validate concept compatibility between `Artifact` and the boolean type. This might or might not pass.

The user says "if my demanded inline form is unsupported, silently correct it to the supported form." Given the canonical example compares two Artifacts, let me think about what the "supported" form would be.

The simplest correction would be to compare `preferred_flag` to another Artifact that represents `true`. But that's artificial.

Actually, let me reconsider. The user wants a classifier Step that produces `preferred_flag`. A classifier produces some kind of flag/tag. Comparing `preferred_flag = True` is reasonable if `preferred_flag` is an Artifact whose content is expected to be a boolean value.

I think the form `if(preferred_flag = True, primary_result, fallback_result)` is actually supported both syntactically and semantically. The grammar allows comparing an atomicTerm (which includes boolean literals) with an Artifact using `=`. And the canonical example shows that comparing Artifacts in `if` expressions is the intended pattern.

So I'll use the user's exact demanded form.

Now let me build the complete workflow:

Concepts needed:
- Artifact: request, preferred_flag, primary_result, fallback_result, final_result
- Step: classifier_step, primary_handler, fallback_handler, final_step
- StepName: need values
- Instruction: need values
- Agent, Executor: need at least one
- Model, Engine, ApiBase, ReasoningEffort, Tool (for agent config)

Let me write a complete workflow following the canonical structure.

```
-- SCENARIO: request classification with primary/fallback routing
-- AUTHORED: from intent: "classifier selects preferred handler path"

const request: Artifact;
const preferred_flag: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const final_result: Artifact;

const classifier_step: Step;
const primary_handler: Step;
const fallback_handler: Step;
const final_step: Step;

const classifier_name: StepName;
const primary_handler_name: StepName;
const fallback_handler_name: StepName;
const final_name: StepName;

const classifier_instruction: Instruction;
const primary_instruction: Instruction;
const fallback_instruction: Instruction;
const final_instruction: Instruction;

const classifier_agent: Agent, Executor;
const primary_agent: Agent, Executor;
const fallback_agent: Agent, Executor;
const final_agent: Agent, Executor;

const default_model: Model;
const claude_engine: Engine;
const api: ApiBase;
const standard_effort: ReasoningEffort;
const read_tool: Tool;

workflow request_routing {
  -- DATA FLOW
  input_workflow(request_routing) == [request];
  consumes(classifier_step) == [request];
  produces(classifier_step) == [preferred_flag];
  consumes(primary_handler) == [request];
  produces(primary_handler) == [primary_result];
  consumes(fallback_handler) == [request];
  produces(fallback_handler) == [fallback_result];
  consumes(final_step) == [if(preferred_flag = True, primary_result, fallback_result)];
  produces(final_step) == [final_result];
  output_workflow(request_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(classifier_step) == classifier_agent;
  step_executor(primary_handler) == primary_agent;
  step_executor(fallback_handler) == fallback_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(classifier_step) == classifier_name;
  step_instruction(classifier_step) == classifier_instruction;
  step_timeout(classifier_step) == 300;
  step_name(primary_handler) == primary_handler_name;
  step_instruction(primary_handler) == primary_instruction;
  step_timeout(primary_handler) == 300;
  step_name(fallback_handler) == fallback_handler_name;
  step_instruction(fallback_handler) == fallback_instruction;
  step_timeout(fallback_handler) == 300;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;
  step_timeout(final_step) == 300;

  -- WORKFLOW CONFIGURATION
  max_concurrency(request_routing) == 3;
  workflow_timeout(request_routing) == 900;

  -- AGENT CONFIGURATION
  agent_config(classifier_agent, default_model, claude_engine, api);
  agent_config(primary_agent, default_model, claude_engine, api);
  agent_config(fallback_agent, default_model, claude_engine, api);
  agent_config(final_agent, default_model, claude_engine, api);
  allowed_tool(classifier_agent, read_tool);
  allowed_tool(primary_agent, read_tool);
  allowed_tool(fallback_agent, read_tool);
  allowed_tool(final_agent, read_tool);
  reasoning_effort(classifier_agent) == standard_effort;
}
```

Wait, I need to double-check: the user says "a classifier Step produces preferred_flag". Is `preferred_flag` the right name? The user uses `preferred_flag` in the `if` expression, so yes.

Also, the user says "primary and fallback handler Steps both consume request and eagerly produce primary_result and fallback_result" - so they both consume `request` (the same input artifact), which is a fan-out pattern. That's correct.

"final_step consumes the selected result and produces final_result" - this is the fan-in.

The `consumes(final_step) == [if(preferred_flag = True, primary_result, fallback_result)]` - this means the final step consumes just one artifact (the selected one), wrapped in a list literal.

Now, the selection expression `if(preferred_flag = True, primary_result, fallback_result)` returns a term (Artifact). This is placed inside `[...]` making it a list with one element. This is valid - `listLiteral` is `LBRACK termList? RBRACK` and `termList` is `term (COMMA term)*`, and `ifExpression` is a `term`.

So the syntax is valid.

Let me also make sure I include all required declarations: StepName values, Instruction values. The user says "Include every required declaration, executor binding, StepName value, and Instruction value."

I need to make sure `step_name` and `step_instruction` are properly bound. Let me check - in FusionFlow, `step_name(step) == stepName` and `step_instruction(step) == instruction`. The `step_name` returns a `StepName` and `step_instruction` returns an `Instruction`. These are preset operators.

But I need to provide actual values for StepName and Instruction. Looking at the grammar and examples, `StepName` and `Instruction` are concepts. StepName values and Instruction values can be named constants. In the canonical example, they're just declared as constants and assigned with `==`. But what are the actual values?

In the canonical example:
```fusionflow
const security_review_name: StepName;
...
step_name(security_review) == security_review_name;
```

The StepName is just a constant of type StepName. The actual string value would need to be provided somehow. Looking at the grammar, `constantName` can be `QUOTEDCONSTANTID` which is a restricted quoted string. But `StepName` and `Instruction` are concepts, not literal types.

Actually, I think in FusionFlow, the constant names ARE the step names - they're identifiers that serve as names. So `security_review_name` is a StepName constant, and assigning it to the step's name via `step_name(security_review) == security_review_name` gives that step that name.

Hmm, but that seems like the StepName value is just a symbolic identifier, not a string. The runtime would use that identifier. Let me look again...

In the canonical example:
```fusionflow
const security_review_name: StepName;
...
step_name(security_review) == security_review_name;
```

Yes, so `security_review_name` is simply declared as a StepName constant, and assigned to the step. The actual display name might come from somewhere else, or the identifier itself might be the name.

For `Instruction`, similarly:
```fusionflow
const security_instruction: Instruction;
...
step_instruction(security_review) == security_instruction;
```

OK, so in the canonical pattern, Instruction is just a declared constant, not a string literal. The actual prompt content would be provided through some other mechanism (the runner/config, not the G4 source).

So my workflow above is fine. Let me also check: do I need `input_workflow` and `output_workflow`? Yes, the canonical example has them.

Let me also double check the concept name casing: `StepName`, `Instruction`, `Agent`, `Executor`, `Model`, `Engine`, `ApiBase`, `ReasoningEffort`, `Tool` - these all start with uppercase as concept names (UPID in grammar).

One thing I want to verify: `reasoning_effort` returns `ReasoningEffort` and `allowed_tool` returns `Bool`. `agent_config` returns `Bool`. So these are standalone calls (shorthand for `== True`).

My workflow looks complete. Let me finalize it.

Actually, one more thought: the user says "compilable" and "successful compilation". The grammar requires `constDecl*` followed by `workflowDecl+`. My file has constDecls then one workflowDecl. That's correct.

The grammar requires semicolons after each `workflowItem`. I have those.

I think this is good. Let me output just the fusionflow code fence.