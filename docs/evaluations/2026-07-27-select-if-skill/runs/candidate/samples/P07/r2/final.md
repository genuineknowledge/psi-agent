```fusionflow
-- SCENARIO: Classify request and select primary or fallback handler based on preference flag
-- AUTHORED: 2025-07-16 from intent: "classify, then eager primary/fallback, select result"

const request: Artifact;
const preferred_flag: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

const classifier_step: Step;
const primary_handler_step: Step;
const fallback_handler_step: Step;
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
const default_engine: Engine;
const default_api: ApiBase;
const read_tool: Tool;

workflow priority_workflow {
  -- DATA FLOW
  input_workflow(priority_workflow) == [request];
  consumes(classifier_step) == [request];
  produces(classifier_step) == [preferred_flag];
  consumes(primary_handler_step) == [request];
  produces(primary_handler_step) == [primary_result];
  consumes(fallback_handler_step) == [request];
  produces(fallback_handler_step) == [fallback_result];
  selected_result == if(preferred_flag = True, primary_result, fallback_result);
  consumes(final_step) == [selected_result];
  produces(final_step) == [final_result];
  output_workflow(priority_workflow) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(classifier_step) == classifier_agent;
  step_executor(primary_handler_step) == primary_agent;
  step_executor(fallback_handler_step) == fallback_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(classifier_step) == classifier_name;
  step_instruction(classifier_step) == classifier_instruction;
  step_name(primary_handler_step) == primary_handler_name;
  step_instruction(primary_handler_step) == primary_instruction;
  step_name(fallback_handler_step) == fallback_handler_name;
  step_instruction(fallback_handler_step) == fallback_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- AGENT CONFIGURATION
  agent_config(classifier_agent, default_model, default_engine, default_api);
  agent_config(primary_agent, default_model, default_engine, default_api);
  agent_config(fallback_agent, default_model, default_engine, default_api);
  agent_config(final_agent, default_model, default_engine, default_api);
  allowed_tool(classifier_agent, read_tool);
  allowed_tool(primary_agent, read_tool);
  allowed_tool(fallback_agent, read_tool);
  allowed_tool(final_agent, read_tool);
}
```