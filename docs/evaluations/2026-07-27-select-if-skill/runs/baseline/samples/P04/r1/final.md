```fusionflow
-- SCENARIO: four-level priority routing with eager handler execution

const request: Artifact;
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
const final_name: StepName;

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

const routing_model: Model;
const routing_engine: Engine;
const routing_api: ApiBase;
const standard_effort: ReasoningEffort;
const read_tool: Tool;

workflow priority_routing {
  -- DATA FLOW
  input_workflow(priority_routing) == [request];
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
      critical_flag = True,
      critical_result,
      if(
        legal_flag = True,
        legal_result,
        if(
          review_flag = True,
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
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(priority_routing) == 4;
  workflow_timeout(priority_routing) == 600;

  -- AGENT CONFIGURATION
  agent_config(triage_agent, routing_model, routing_engine, routing_api);
  agent_config(critical_agent, routing_model, routing_engine, routing_api);
  agent_config(legal_agent, routing_model, routing_engine, routing_api);
  agent_config(review_agent, routing_model, routing_engine, routing_api);
  agent_config(fallback_agent, routing_model, routing_engine, routing_api);
  agent_config(final_agent, routing_model, routing_engine, routing_api);

  allowed_tool(triage_agent, read_tool);
  allowed_tool(critical_agent, read_tool);
  allowed_tool(legal_agent, read_tool);
  allowed_tool(review_agent, read_tool);
  allowed_tool(fallback_agent, read_tool);
  reasoning_effort(triage_agent) == standard_effort;
  reasoning_effort(critical_agent) == standard_effort;
  reasoning_effort(legal_agent) == standard_effort;
  reasoning_effort(review_agent) == standard_effort;
  reasoning_effort(fallback_agent) == standard_effort;
}
```