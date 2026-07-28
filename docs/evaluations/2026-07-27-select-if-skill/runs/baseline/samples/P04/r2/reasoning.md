The user wants me to write a complete FusionFlow G4 workflow for four-level priority routing. Let me carefully analyze the requirements:

1. Receives a request artifact
2. A triage Step produces: critical_flag, legal_flag, review_flag
3. Four handler Steps consume the request and produce: critical_result, legal_result, review_result, fallback_result
4. All four handlers must execute eagerly on every run (so they all produce their results regardless of which one is eventually selected)
5. Priority: critical_flag true → critical_result, else legal_flag true → legal_result, else review_flag true → review_result, else fallback_result
6. A final Step consumes the chosen result and produces final_result
7. Include declarations, data flow, executor assignments, StepName values, and Instruction values
8. Express priority in executable workflow structure using if term selection

Let me model this:

- input: request (Artifact)
- triage step produces: critical_flag, legal_flag, review_flag (all Artifacts)
- critical_handler produces: critical_result (Artifact)
- legal_handler produces: legal_result (Artifact)
- review_handler produces: review_result (Artifact)
- fallback_handler produces: fallback_result (Artifact)
- final step consumes the chosen result via if expression

For the eager execution of all four handlers - since each handler consumes `request` and produces a different artifact, and the final step only consumes the chosen one via `if`, that's fine. The handlers will all execute because they're all declared with `consumes(handler) == [request]` and their outputs are declared. The runtime will execute all of them in parallel since there's no dependency between them.

The final step's `consumes` would be:

```
consumes(final_step) == [
  if(
    critical_flag = critical_flag_value,  -- need to think about what to compare
    critical_result,
    if(
      legal_flag = legal_flag_value,
      legal_result,
      if(
        review_flag = review_flag_value,
        review_result,
        fallback_result
      )
    )
  )
]
```

Wait, but what are the flags compared against? The user says "critical_flag is true" etc. But in G4, flags are Artifacts. I need some criterion artifacts to compare against. Let me add truth markers or boolean comparison artifacts.

Looking at the conditional term selection pattern in the skill, it uses criterion artifacts: `primary_criterion`, `block_criterion`, etc., and compares against them. Let me follow that pattern.

The user says the triage Step produces `critical_flag, legal_flag, review_flag`. I'll also have input criteria: `true_marker` to compare against.

Actually, re-reading the user's request more carefully: "A triage Step produces critical_flag, legal_flag, and review_flag" - these are Artifacts produced by the triage step. Then we need to check if they are "true". In G4, we can compare them against a truth marker.

Let me add input criteria artifacts: `critical_criterion`, `legal_criterion`, `review_criterion` - these define what "true" means (the values to match against). Or simpler: just have a `true_value` artifact that the triage outputs are compared against.

Actually, looking at the skill's conditional term selection example more carefully:

```fusionflow
consumes(final_step) == [
    if(
      (primary_observation = primary_criterion) AND !(block_observation = block_criterion),
      primary_result,
      ...
    )
  ];
```

So the pattern is: observation (produced by triage) = criterion (input artifact). Let me adapt:

- Input: `request`, `true_marker`
- Triage produces: `critical_flag`, `legal_flag`, `review_flag`
- Critical handler produces: `critical_result`
- Legal handler produces: `legal_result`
- Review handler produces: `review_result`
- Fallback handler produces: `fallback_result`
- The final step's consumes uses if expressions comparing each flag to true_marker

The priority expression:
```
if(
  critical_flag = true_marker,
  critical_result,
  if(
    legal_flag = true_marker,
    legal_result,
    if(
      review_flag = true_marker,
      review_result,
      fallback_result
    )
  )
)
```

This is clean and follows the pattern.

Now for declarations - I need all the constants:

```fusionflow
const request: Artifact;
const true_marker: Artifact;
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
const final_step_name: StepName;

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

const review_model: Model;
const review_engine: Engine;
const review_api: ApiBase;
```

Now the workflow body:

```fusionflow
workflow priority_routing {
  -- DATA FLOW
  input_workflow(priority_routing) == [request, true_marker];
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
      critical_flag = true_marker,
      critical_result,
      if(
        legal_flag = true_marker,
        legal_result,
        if(
          review_flag = true_marker,
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
  step_name(final_step) == final_step_name;
  step_instruction(final_step) == final_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(priority_routing) == 4; -- triage + all 4 handlers can run

  -- AGENT CONFIGURATION
  agent_config(triage_agent, review_model, review_engine, review_api);
  agent_config(critical_agent, review_model, review_engine, review_api);
  agent_config(legal_agent, review_model, review_engine, review_api);
  agent_config(review_agent, review_model, review_engine, review_api);
  agent_config(fallback_agent, review_model, review_engine, review_api);
  agent_config(final_agent, review_model, review_engine, review_api);
}
```

Wait - the user says "All four handlers must execute eagerly on every run." I have a concern: the `if` expression in the final step's `consumes` selects only one artifact. But does this mean the runtime would skip the other handlers? Let me re-read the skill.

From the skill's "Conditional term selection" section:
> "Let the checker validate branch concept compatibility. Let lowering/runtime decide dependencies and eager/lazy branch evaluation."

And from the "Conditional term selection" modeling rules:
> "Do not replace candidate Artifacts with Boolean Step payloads, refuse valid term selection because if is not a Step, or invent switch, choice, or conditional blocks."

So the runtime owns dependency and branch evaluation. The user explicitly states that all four handlers must execute eagerly on every run. In my model, all four handlers consume `request` and produce their own result artifacts. The only place where `if` appears is in the final step's `consumes`. The critical question is whether the runtime will skip a handler whose result isn't selected.

Actually, the handlers produce artifacts that are consumed by the final step's `if` expression. The `if` selects one artifact from the candidates. But each handler is declared to produce its artifact, and the dependency edge is through `consumes(final_step) == [if(...)]`.

Looking at the skill more carefully, in the "Conditional term selection" section:

> "Keep every candidate result explicit and produced by a Step."
> "conserves(final_step) == [if(formula, artifact_a, artifact_b)];"

And the full example shows all four handler steps being declared with their `produces` and `consumes` relations, and all produce their results. The `if` only selects which one is consumed by the final step.

But will the runtime execute all four handlers? Each handler consumes `request` and produces a result. The final step consumes the `if(...)` expression. Since each handler is a separate step with its own `consumes`/`produces`, and all consume the same input, they should all execute. The `if` only selects which artifact is passed to the final step.

Actually, I think there might be a subtlety. The `if` expression is inside `consumes(final_step)`. The question is whether the runtime lazily only executes handlers whose results are selected. But in the grammar/skill, the `if` selects among term values (Artifacts), and the Steps that produce those Artifacts have their own independent `consumes`/`produces` edges.

Let me look at the skill's priority_routing example again. It has:

```fusionflow
consumes(final_step) == [
    if(
      (primary_observation = primary_criterion) AND !(block_observation = block_criterion),
      primary_result,
      ...
    )
  ];
```

All four handlers (primary_handler_step, review_handler_step, fallback_handler_step) have their own `consumes` and `produces` edges. All four handlers produce their results. The `if` only selects which one the final step consumes.

So the pattern is correct - all four handlers will execute because they have their own artifact production edges. The `if` only selects which artifact the final step receives.

But wait - will the runtime skip a handler whose result is not selected by the `if`? Looking at the example more carefully, each handler has its own `produces(handler) == [result]` and the final step's `consumes` uses `if` to select. Each handler is a separate Step with its own consumption of `incoming_case`. Since they're all declared, they all have their dependencies and should all execute.

I think this works. The user explicitly wants all four to execute eagerly, and the G4 model supports this because each handler is its own Step with its own data edge.

Now let me also think about the concurrency. The user says "all four handlers must execute eagerly." I set `max_concurrency(priority_routing) == 4` to allow triage + all four handlers to potentially run in parallel (though triage depends on request, and handlers also depend on request, so they can all start once request is available).

Actually, the triage step produces the flags. The handlers only consume request (not the flags). So the handlers don't depend on triage at all. They can all run in parallel with triage. Good - that satisfies "eagerly on every run."

Let me finalize the workflow. I need to include StepName values and Instruction values. The user says "Include all declarations, data flow, executor assignments, StepName values, and Instruction values." So I need to declare StepName constants and Instruction constants, which I already have as `*_name` and `*_instruction` constants.

Wait, but StepName and Instruction are concepts. The constants need to be declared with those concept types. Let me check my declarations - I have:
```
const triage_name: StepName;
```
That's correct. And:
```
const triage_instruction: Instruction;
```
Also correct.

The values are set through `step_name(triage_step) == triage_name;` and `step_instruction(triage_step) == triage_instruction;`. These map StepName and Instruction identities. In G4, the actual string values would be resolved by the catalog - the constants are typed identities that hold the instruction text.

From the skill: "Do not encode free-form command strings, code, prompts, or secrets as quoted constants." And "Treating quoted constants as prompt strings" is listed as an anti-pattern. But also "Restricted quoted ID or explicit workspace-relative path" is allowed.

Hmm, but the user says "Include ... Instruction values." In G4, Instruction is a concept from the catalog. The `step_instruction` operator maps a Step to an Instruction identity. The Instruction identity would be defined elsewhere (in the catalog) or could be a restricted quoted ID? 

Let me re-read: "When the user supplies a grammar-valid literal as a typed constant name, including a restricted quoted ID or './...' path, preserve that literal and use it directly as the required preset value."

So I could use QUOTEDCONSTANTID for instruction values. But the grammar says QUOTEDCONSTANTID is `'"' [A-Za-z0-9.!#$%?@_{|}~`]* '"'` - no whitespace. That's very restrictive - it can only be alphanumeric and a few special chars, no spaces. That can't hold an instruction string with spaces.

Actually, looking at the grammar again: `'"' [A-Za-z0-9.!#$%?@_{|}~`]* '"'` - this allows letters, digits, and special chars but NO whitespace. So "review_the_request" would be valid but "review the request" would not.

Hmm, but Instruction values are specific to the user's task. Let me just use descriptive names for the constants and declare them. The actual instruction text would be in the catalog.

Actually, I think the proper approach is: the Instruction is a concept from the catalog, and we reference it by its identity (constant name). The constant itself IS the instruction value identity. The user asked to "include Instruction values" which I interpret as declaring the instruction constant identities and assigning them via `step_instruction`.

Let me just declare them with meaningful LOWID names:
```
const triage_instruction: Instruction;
const critical_instruction: Instruction;
const legal_instruction: Instruction;
const review_instruction: Instruction;
const fallback_instruction: Instruction;
const final_instruction: Instruction;
```

And assign them via `step_instruction(triage_step) == triage_instruction;`.

This should be sufficient. The actual instruction text is a catalog concern.

OK, let me write the complete workflow now. I'll make the flag comparison work with `true_marker` as an input artifact.

One more thing: the user says "express the priority in executable workflow structure rather than only describing it inside a Step instruction." My use of `if` nested in the `consumes` of the final step does exactly this - it's structural, not inside an instruction string.

Let me write the complete answer.