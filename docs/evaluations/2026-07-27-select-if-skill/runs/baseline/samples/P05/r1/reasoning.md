The user wants a complete FusionFlow G4 workflow with two independent conditional selections. Let me carefully parse the requirements:

1. Input: `request`
2. A routing Step produces `tone_flag` and `risk_flag`
3. Four eager handler Steps all consume `request` on every run:
   - formal handler → `formal_result`
   - casual handler → `casual_result`
   - safe handler → `safe_result`
   - escalated handler → `escalated_result`
4. Independently choose:
   - `formal_result` when `tone_flag` is `formal_tone`, otherwise `casual_result`
   - `escalated_result` when `risk_flag` is `high_risk`, otherwise `safe_result`
5. One final Step consumes both chosen values together and produces `final_result`

The conditional selection uses `if(formula, then_term, else_term)` as documented.

Let me model this:

Artifacts needed:
- `request` (input)
- `tone_flag`, `risk_flag` (produced by routing step)
- `formal_result`, `casual_result`, `safe_result`, `escalated_result` (produced by handlers)
- `formal_tone`, `high_risk` (constants for comparison - these are artifacts with known values)
- `final_result` (output)

Steps:
- `route_step` - produces tone_flag and risk_flag
- `formal_handler`, `casual_handler`, `safe_handler`, `escalated_handler` - all consume request
- `final_step` - consumes the two chosen values

Wait, I need to think about the conditional selection more carefully. The `if` expression selects between terms. The `consumes` operator takes a List literal. So I need to express:

`consumes(final_step) == [if(formula1, formal_result, casual_result), if(formula2, escalated_result, safe_result)]`

For the formulas, I need to compare tone_flag with formal_tone, and risk_flag with high_risk. These are artifact comparisons.

Let me declare the constants:

```fusionflow
const formal_tone: Artifact;
const high_risk: Artifact;
```

And the comparison would be `tone_flag = formal_tone` and `risk_flag = high_risk`.

Now let me write the full workflow:

```fusionflow
-- SCENARIO: Request routing with two independent conditional selections

const request: Artifact;
const tone_flag: Artifact;
const risk_flag: Artifact;
const formal_result: Artifact;
const casual_result: Artifact;
const safe_result: Artifact;
const escalated_result: Artifact;
const final_result: Artifact;
const formal_tone: Artifact;
const high_risk: Artifact;

const route_step: Step;
const formal_handler: Step;
const casual_handler: Step;
const safe_handler: Step;
const escalated_handler: Step;
const final_step: Step;

const route_name: StepName;
const formal_name: StepName;
const casual_name: StepName;
const safe_name: StepName;
const escalated_name: StepName;
const final_name: StepName;

const route_instruction: Instruction;
const formal_instruction: Instruction;
const casual_instruction: Instruction;
const safe_instruction: Instruction;
const escalated_instruction: Instruction;
const final_instruction: Instruction;

const route_agent: Agent, Executor;
const formal_agent: Agent, Executor;
const casual_agent: Agent, Executor;
const safe_agent: Agent, Executor;
const escalated_agent: Agent, Executor;
const final_agent: Agent, Executor;

const model: Model;
const engine: Engine;
const api: ApiBase;

workflow request_routing {
  -- DATA FLOW
  input_workflow(request_routing) == [request];
  consumes(route_step) == [request];
  produces(route_step) == [tone_flag, risk_flag];
  consumes(formal_handler) == [request];
  produces(formal_handler) == [formal_result];
  consumes(casual_handler) == [request];
  produces(casual_handler) == [casual_result];
  consumes(safe_handler) == [request];
  produces(safe_handler) == [safe_result];
  consumes(escalated_handler) == [request];
  produces(escalated_handler) == [escalated_result];
  consumes(final_step) == [
    if(tone_flag = formal_tone, formal_result, casual_result),
    if(risk_flag = high_risk, escalated_result, safe_result)
  ];
  produces(final_step) == [final_result];
  output_workflow(request_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(route_step) == route_agent;
  step_executor(formal_handler) == formal_agent;
  step_executor(casual_handler) == casual_agent;
  step_executor(safe_handler) == safe_agent;
  step_executor(escalated_handler) == escalated_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(route_step) == route_name;
  step_instruction(route_step) == route_instruction;
  step_name(formal_handler) == formal_name;
  step_instruction(formal_handler) == formal_instruction;
  step_name(casual_handler) == casual_name;
  step_instruction(casual_handler) == casual_instruction;
  step_name(safe_handler) == safe_name;
  step_instruction(safe_handler) == safe_instruction;
  step_name(escalated_handler) == escalated_name;
  step_instruction(escalated_handler) == escalated_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(request_routing) == 4;

  -- AGENT CONFIGURATION
  agent_config(route_agent, model, engine, api);
  agent_config(formal_agent, model, engine, api);
  agent_config(casual_agent, model, engine, api);
  agent_config(safe_agent, model, engine, api);
  agent_config(escalated_agent, model, engine, api);
  agent_config(final_agent, model, engine, api);
}
```

Wait, I also need to declare `formal_tone` and `high_risk` as constants that participate as artifacts for comparison. Let me check if they need to be in `input_workflow` or if they're just internal constants.

The grammar says `input_workflow` returns List and declares external inputs. Since `formal_tone` and `high_risk` are used for comparison but are reference values (like enum constants), they should probably be external inputs or just declared constants. In the reference pattern for conditional selection, the criteria artifacts are in `input_workflow`.

Looking at the priority routing example in the skill, `primary_criterion`, `block_criterion`, etc. are all in `input_workflow`. So `formal_tone` and `high_risk` should be inputs too.

But the user said the workflow "receives request" - nothing about also receiving criteria values. Let me re-read...

The user just says "receives request". The `formal_tone` and `high_risk` are comparison targets. I think they should be declared as constants (artifacts) but perhaps not external inputs, or they could be.

Actually, looking at the grammar more carefully, `formal_tone` and `high_risk` are artifacts that are compared with the flags. They need values to compare against. The simplest way is to make them workflow inputs. Let me add them to `input_workflow`.

Actually, thinking about it more - the flags produced by routing are artifacts, and the comparison targets (`formal_tone`, `high_risk`) should also be artifacts with known values. The most natural way in FusionFlow is to have them as inputs.

Let me update the input/output to include them.

Actually wait - the user's requirement is: "A routing Step produces tone_flag and risk_flag." The comparison targets `formal_tone` and `high_risk` are reference values. I'll include them in the input_workflow.

Let me write the final version.