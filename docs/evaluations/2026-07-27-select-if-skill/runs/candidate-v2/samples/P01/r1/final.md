```fusionflow
-- SCENARIO: classify a request, run two handlers eagerly, then select one result for final output
-- AUTHORED: from intent: "classify request, always run two handlers, select final based on category"

const request: Artifact;
const category: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

const primary_category: Artifact;

const classify_step: Step;
const primary_handle_step: Step;
const fallback_handle_step: Step;
const final_step: Step;

const classify_name: StepName;
const primary_handle_name: StepName;
const fallback_handle_name: StepName;
const final_name: StepName;

const classify_instruction: Instruction;
const primary_handle_instruction: Instruction;
const fallback_handle_instruction: Instruction;
const final_instruction: Instruction;

const classifier: Agent, Executor;
const primary_handler: Agent, Executor;
const fallback_handler: Agent, Executor;
const final_consumer: Agent, Executor;

workflow classification {
  -- DATA FLOW
  input_workflow(classification) == [request];
  consumes(classify_step) == [request];
  produces(classify_step) == [category];
  consumes(primary_handle_step) == [request];
  produces(primary_handle_step) == [primary_result];
  consumes(fallback_handle_step) == [request];
  produces(fallback_handle_step) == [fallback_result];
  primary_category == "primary";
  selected_result == if(category = primary_category, primary_result, fallback_result);
  consumes(final_step) == [selected_result];
  produces(final_step) == [final_result];
  output_workflow(classification) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(classify_step) == classifier;
  step_executor(primary_handle_step) == primary_handler;
  step_executor(fallback_handle_step) == fallback_handler;
  step_executor(final_step) == final_consumer;

  -- STEP CONFIGURATION
  step_name(classify_step) == classify_name;
  step_instruction(classify_step) == classify_instruction;
  step_name(primary_handle_step) == primary_handle_name;
  step_instruction(primary_handle_step) == primary_handle_instruction;
  step_name(fallback_handle_step) == fallback_handle_name;
  step_instruction(fallback_handle_step) == fallback_handle_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;
}
```