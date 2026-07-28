```fusionflow
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
const default_effort: ReasoningEffort;
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
  reasoning_effort(classifier_agent) == default_effort;
}
```