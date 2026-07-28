The user wants a complete FusionFlow G4 workflow for four-level priority routing. Let me carefully design this based on the frozen grammar and skill instructions.

Key requirements:
1. 4 handler Steps execute eagerly (all run)
2. Priority chain: critical_flag → legal_flag → review_flag → fallback
3. Named Artifact selection with `if`
4. Final Step consumes the chosen result

Let me model this:

Inputs: request, critical_flag, legal_flag, review_flag (four named Artifacts for the triage outputs)

Actually, looking more carefully, the triage step produces all four flags, and the workflow also receives the request as input.

Let me think about the data flow:

Input: `request` (the request artifact)

Triage Step:
- consumes: `request`
- produces: `critical_flag`, `legal_flag`, `review_flag` (the triage produces these)

Actually wait, the user says "A triage Step produces critical_flag, legal_flag, review_flag" - so the triage produces three flags. And there are four handler Steps that consume `request` and produce respective results.

So the handler Steps:
- critical_handler: consumes `request`, produces `critical_result`
- legal_handler: consumes `request`, produces `legal_result`
- review_handler: consumes `request`, produces `review_result`
- fallback_handler: consumes `request`, produces `fallback_result`

All four handlers execute eagerly (they consume the shared input `request`).

Priority selection (chained `if`):
1. If critical_flag is true: critical_result
2. Else if legal_flag is true: legal_result  
3. Else if review_flag is true: review_result
4. Else: fallback_result

In FusionFlow G4, I need to use named Artifact selection with chained `if` statements:

```
first_priority == if(critical_flag=True, critical_result, legal_or_review)
```

Wait, but the comparison uses `=` (NUMERIC_EQ not ASSERT_EQ). Let me re-read the grammar.

The formula for comparison is: `comparison : term comparisonOp term` where `comparisonOp` includes `NUMERIC_EQ : '='`.

So `critical_flag = True` is a valid comparison.

But wait - the flags are Artifacts. Can I compare an Artifact to `True`? Let me think about this. The grammar allows it syntactically. Semantically, the triage Step produces Artifacts that contain a boolean value. The `if` condition checks if the content equals `True`.

Let me write the chained selection:

```
critical_or_rest == if(critical_flag = True, critical_result, legal_or_rest);
legal_or_rest == if(legal_flag = True, legal_result, review_or_fallback);
review_or_fallback == if(review_flag = True, review_result, fallback_result);
selected_result == critical_or_rest;
```

Wait, actually the chaining can be simpler. Let me think again.

The requirement is:
- final = critical_result if critical_flag is true
- final = legal_result if legal_flag is true (and critical_flag is not true)
- final = review_result if review_flag is true (and neither critical_flag nor legal_flag is true)
- final = fallback_result otherwise

Using chained named Artifact selection:

```
first_priority == if(critical_flag = True, critical_result, second_priority);
second_priority == if(legal_flag = True, legal_result, third_priority);
third_priority == if(review_flag = True, review_result, fallback_result);
selected_result == first_priority;
```

Then `consumes(final_step) == [selected_result]`.

Now let me write the full workflow with all declarations.

I need to declare:
- All Artifacts
- All Steps (triage, 4 handlers, final)
- StepName constants
- Instruction constants (or paths)
- Agent/Executor constants
- Model, Engine, ApiBase, etc.

Let me use the full-featured example pattern from the skill.

For Instructions, the grammar allows `"./"` paths or quoted constant IDs. I'll use descriptive quoted constant IDs for step names and instructions.

Let me construct this carefully.

```fusionflow
-- SCENARIO: Four-level priority routing with eager handlers and chained if selection

const request: Artifact;
const critical_flag: Artifact;
const legal_flag: Artifact;
const review_flag: Artifact;
const critical_result: Artifact;
const legal_result: Artifact;
const review_result: Artifact;
const fallback_result: Artifact;
const first_priority: Artifact;
const second_priority: Artifact;
const third_priority: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

const triage_step: Step;
const critical_handler_step: Step;
const legal_handler_step: Step;
const review_handler_step: Step;
const fallback_handler_step: Step;
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
const critical_handler: Agent, Executor;
const legal_handler: Agent, Executor;
const review_handler: Agent, Executor;
const fallback_handler: Agent, Executor;
const final_consumer: Agent, Executor;

const standard_model: Model;
const standard_engine: Engine;
const standard_api: ApiBase;

workflow priority_routing {
  -- DATA FLOW
  input_workflow(priority_routing) == [request];
  consumes(triage_step) == [request];
  produces(triage_step) == [critical_flag, legal_flag, review_flag];
  consumes(critical_handler_step) == [request];
  produces(critical_handler_step) == [critical_result];
  consumes(legal_handler_step) == [request];
  produces(legal_handler_step) == [legal_result];
  consumes(review_handler_step) == [request];
  produces(review_handler_step) == [review_result];
  consumes(fallback_handler_step) == [request];
  produces(fallback_handler_step) == [fallback_result];
  first_priority == if(critical_flag = True, critical_result, second_priority);
  second_priority == if(legal_flag = True, legal_result, third_priority);
  third_priority == if(review_flag = True, review_result, fallback_result);
  selected_result == first_priority;
  consumes(final_step) == [selected_result];
  produces(final_step) == [final_result];
  output_workflow(priority_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(triage_step) == triage_agent;
  step_executor(critical_handler_step) == critical_handler;
  step_executor(legal_handler_step) == legal_handler;
  step_executor(review_handler_step) == review_handler;
  step_executor(fallback_handler_step) == fallback_handler;
  step_executor(final_step) == final_consumer;

  -- STEP CONFIGURATION
  step_name(triage_step) == triage_name;
  step_instruction(triage_step) == triage_instruction;
  step_name(critical_handler_step) == critical_handler_name;
  step_instruction(critical_handler_step) == critical_instruction;
  step_name(legal_handler_step) == legal_handler_name;
  step_instruction(legal_handler_step) == legal_instruction;
  step_name(review_handler_step) == review_handler_name;
  step_instruction(review_handler_step) == review_instruction;
  step_name(fallback_handler_step) == fallback_handler_name;
  step_instruction(fallback_handler_step) == fallback_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- AGENT CONFIGURATION
  agent_config(triage_agent, standard_model, standard_engine, standard_api);
  agent_config(critical_handler, standard_model, standard_engine, standard_api);
  agent_config(legal_handler, standard_model, standard_engine, standard_api);
  agent_config(review_handler, standard_model, standard_engine, standard_api);
  agent_config(fallback_handler, standard_model, standard_engine, standard_api);
  agent_config(final_consumer, standard_model, standard_engine, standard_api);
}
```

Wait, I need to double-check some things:

1. The user said "four handler Steps consume request" - yes, all four consume the same `request` artifact. That makes them eager (all run) since they all depend on the shared input.

2. For the `if` expressions - the grammar says ifExpression is `IF LPAREN formula COMMA term COMMA term RPAREN`. So `if(critical_flag = True, critical_result, second_priority)` is valid where `critical_flag = True` is a comparison, `critical_result` is a term, `second_priority` is a term. The formula rule allows comparison inside formula. So this should be valid.

3. Actually wait - I need to check if `True` is a valid atomicTerm. Looking at the grammar: `atomicTerm : constantName | booleanLiteral ;` and `booleanLiteral : TRUE | FALSE ;` and `TRUE : 'True' | 'true' | 'TRUE';`. Yes, `True` is valid.

4. The `selected_result == first_priority;` - this is an assertion with `ASSERT_EQ`. `term ASSERT_EQ term` where both are constantName references. That's valid.

5. The data flow section uses `first_priority == if(...)`, which is a named Artifact selection as documented - that's allowed.

6. I have all required declarations: all Artifacts, Steps, StepNames, Instructions, Agents/Executors, Model/Engine/ApiBase.

7. The executor assignments use `step_executor(step) == executor` - correct.

8. Step config uses `step_name`, `step_instruction` - correct.

9. Agent config uses `agent_config(agent, model, engine, api)` - correct.

10. Data flow uses `input_workflow`, `consumes`, `produces`, `output_workflow` - all correct.

11. No `foreach_item`, no `resource_requirement`, no `step_timeout`, no `max_attempts` - that's fine, they're optional.

12. No `max_concurrency` or `workflow_timeout` - optional.

This looks complete and correct. Let me make sure the code fence is labeled `fusionflow`.