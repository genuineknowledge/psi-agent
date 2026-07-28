```fusionflow
-- SCENARIO: Classifier selects primary or fallback handler based on preferred_flag
-- AUTHORED: 2025-01-01 12:00:00 from intent: "classify request, eagerly produce two results, select one"

const request: Artifact;
const preferred_flag: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const final_result: Artifact;
const true_bool: Artifact;

const classifier_step: Step;
const primary_handler_step: Step;
const fallback_handler_step: Step;
const final_step: Step;

const classifier_name: StepName;
const primary_handler_name: StepName;
const fallback_handler_name: StepName;
const final_name: StepName;

const classifier_instruction: Instruction;
const primary_handler_instruction: Instruction;
const fallback_handler_instruction: Instruction;
const final_instruction: Instruction;

const classifier_agent: Agent, Executor;
const primary_agent: Agent, Executor;
const fallback_agent: Agent, Executor;
const final_agent: Agent, Executor;

const review_model: Model;
const review_engine: Engine;
const review_api: ApiBase;
const high_effort: ReasoningEffort;
const read_tool: Tool;

workflow select_handler {
  -- DATA FLOW
  input_workflow(select_handler) == [request];
  true_bool == True;
  consumes(classifier_step) == [request];
  produces(classifier_step) == [preferred_flag];
  consumes(primary_handler_step) == [request];
  produces(primary_handler_step) == [primary_result];
  consumes(fallback_handler_step) == [request];
  produces(fallback_handler_step) == [fallback_result];
  consumes(final_step) == [if(preferred_flag = true_bool, primary_result, fallback_result)];
  produces(final_step) == [final_result];
  output_workflow(select_handler) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(classifier_step) == classifier_agent;
  step_executor(primary_handler_step) == primary_agent;
  step_executor(fallback_handler_step) == fallback_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(classifier_step) == classifier_name;
  step_instruction(classifier_step) == classifier_instruction;
  step_name(primary_handler_step) == primary_handler_name;
  step_instruction(primary_handler_step) == primary_handler_instruction;
  step_name(fallback_handler_step) == fallback_handler_name;
  step_instruction(fallback_handler_step) == fallback_handler_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(select_handler) == 3;
  workflow_timeout(select_handler) == 900;

  -- AGENT CONFIGURATION
  agent_config(classifier_agent, review_model, review_engine, review_api);
  agent_config(primary_agent, review_model, review_engine, review_api);
  agent_config(fallback_agent, review_model, review_engine, review_api);
  agent_config(final_agent, review_model, review_engine, review_api);
  allowed_tool(classifier_agent, read_tool);
  allowed_tool(primary_agent, read_tool);
  allowed_tool(fallback_agent, read_tool);
  reasoning_effort(classifier_agent) == high_effort;
  reasoning_effort(primary_agent) == high_effort;
  reasoning_effort(fallback_agent) == high_effort;
}
```