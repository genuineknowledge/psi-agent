```fusionflow
-- SCENARIO: request routing with two independent conditional value selections
-- AUTHORED: 2025-07-17 14:30:00 from intent: "route a request by tone and risk flags, then merge chosen results"

const request: Artifact;
const formal_tone: Artifact;
const high_risk: Artifact;
const tone_flag: Artifact;
const risk_flag: Artifact;
const formal_result: Artifact;
const casual_result: Artifact;
const safe_result: Artifact;
const escalated_result: Artifact;
const selected_tone: Artifact;
const selected_risk: Artifact;
const final_result: Artifact;

const routing_step: Step;
const formal_handler: Step;
const casual_handler: Step;
const safe_handler: Step;
const escalated_handler: Step;
const final_step: Step;

const routing_name: StepName;
const formal_name: StepName;
const casual_name: StepName;
const safe_name: StepName;
const escalated_name: StepName;
const final_name: StepName;

const routing_instruction: Instruction;
const formal_instruction: Instruction;
const casual_instruction: Instruction;
const safe_instruction: Instruction;
const escalated_instruction: Instruction;
const final_instruction: Instruction;

const routing_agent: Agent, Executor;
const formal_agent: Agent, Executor;
const casual_agent: Agent, Executor;
const safe_agent: Agent, Executor;
const escalated_agent: Agent, Executor;
const final_agent: Agent, Executor;

const review_model: Model;
const review_engine: Engine;
const review_api: ApiBase;

workflow route_and_select {
  -- DATA FLOW
  input_workflow(route_and_select) ==
    [request, formal_tone, high_risk];
  consumes(routing_step) == [request];
  produces(routing_step) == [tone_flag, risk_flag];
  consumes(formal_handler) == [request];
  produces(formal_handler) == [formal_result];
  consumes(casual_handler) == [request];
  produces(casual_handler) == [casual_result];
  consumes(safe_handler) == [request];
  produces(safe_handler) == [safe_result];
  consumes(escalated_handler) == [request];
  produces(escalated_handler) == [escalated_result];

  selected_tone == if(
    tone_flag = formal_tone,
    formal_result,
    casual_result
  );
  selected_risk == if(
    risk_flag = high_risk,
    escalated_result,
    safe_result
  );

  consumes(final_step) == [selected_tone, selected_risk];
  produces(final_step) == [final_result];
  output_workflow(route_and_select) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(routing_step) == routing_agent;
  step_executor(formal_handler) == formal_agent;
  step_executor(casual_handler) == casual_agent;
  step_executor(safe_handler) == safe_agent;
  step_executor(escalated_handler) == escalated_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(routing_step) == routing_name;
  step_instruction(routing_step) == routing_instruction;
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
  step_timeout(routing_step) == 300;
  step_timeout(formal_handler) == 300;
  step_timeout(casual_handler) == 300;
  step_timeout(safe_handler) == 300;
  step_timeout(escalated_handler) == 300;
  step_timeout(final_step) == 300;
  max_attempts(routing_step) == 2;
  max_attempts(formal_handler) == 2;
  max_attempts(casual_handler) == 2;
  max_attempts(safe_handler) == 2;
  max_attempts(escalated_handler) == 2;
  max_attempts(final_step) == 2;

  -- WORKFLOW CONFIGURATION
  max_concurrency(route_and_select) == 4;
  workflow_timeout(route_and_select) == 900;

  -- AGENT CONFIGURATION
  agent_config(routing_agent, review_model, review_engine, review_api);
  agent_config(formal_agent, review_model, review_engine, review_api);
  agent_config(casual_agent, review_model, review_engine, review_api);
  agent_config(safe_agent, review_model, review_engine, review_api);
  agent_config(escalated_agent, review_model, review_engine, review_api);
  agent_config(final_agent, review_model, review_engine, review_api);
}
```