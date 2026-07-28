```fusionflow
-- SCENARIO: Four-level priority routing with eager parallel handlers
-- AUTHORED: 2025-07-18 12:00:00 from intent: "Write a complete, compilable FusionFlow G4 workflow with real executable semantics for four-level priority routing"

const request: Artifact;
const critical_flag: Artifact;
const legal_flag: Artifact;
const review_flag: Artifact;
const critical_result: Artifact;
const legal_result: Artifact;
const review_result: Artifact;
const fallback_result: Artifact;
const chosen_result: Artifact;
const final_result: Artifact;

const triage_step: Step;
const critical_handler: Step;
const legal_handler: Step;
const review_handler: Step;
const fallback_handler: Step;
const final_step: Step;

const triage_name: StepName;
const critical_name: StepName;
const legal_name: StepName;
const review_name: StepName;
const fallback_name: StepName;
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

const provider_model: Model;
const provider_engine: Engine;
const provider_api: ApiBase;
const high_effort: ReasoningEffort;
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
  chosen_result == if(
    critical_flag = True,
    critical_result,
    if(
      legal_flag = True,
      legal_result,
      if(review_flag = True, review_result, fallback_result)
    )
  );
  consumes(final_step) == [chosen_result];
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
  step_timeout(triage_step) == 120;
  max_attempts(triage_step) == 1;

  step_name(critical_handler) == critical_name;
  step_instruction(critical_handler) == critical_instruction;
  step_timeout(critical_handler) == 120;
  max_attempts(critical_handler) == 2;

  step_name(legal_handler) == legal_name;
  step_instruction(legal_handler) == legal_instruction;
  step_timeout(legal_handler) == 120;
  max_attempts(legal_handler) == 2;

  step_name(review_handler) == review_name;
  step_instruction(review_handler) == review_instruction;
  step_timeout(review_handler) == 120;
  max_attempts(review_handler) == 2;

  step_name(fallback_handler) == fallback_name;
  step_instruction(fallback_handler) == fallback_instruction;
  step_timeout(fallback_handler) == 120;
  max_attempts(fallback_handler) == 2;

  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;
  step_timeout(final_step) == 60;
  max_attempts(final_step) == 1;

  -- WORKFLOW CONFIGURATION
  max_concurrency(priority_routing) == 5;
  workflow_timeout(priority_routing) == 300;

  -- AGENT CONFIGURATION
  agent_config(triage_agent, provider_model, provider_engine, provider_api);
  agent_config(critical_agent, provider_model, provider_engine, provider_api);
  agent_config(legal_agent, provider_model, provider_engine, provider_api);
  agent_config(review_agent, provider_model, provider_engine, provider_api);
  agent_config(fallback_agent, provider_model, provider_engine, provider_api);
  agent_config(final_agent, provider_model, provider_engine, provider_api);

  allowed_tool(triage_agent, read_tool);
  allowed_tool(critical_agent, read_tool);
  allowed_tool(legal_agent, read_tool);
  allowed_tool(review_agent, read_tool);
  allowed_tool(fallback_agent, read_tool);
  allowed_tool(final_agent, read_tool);

  reasoning_effort(triage_agent) == high_effort;
  reasoning_effort(critical_agent) == high_effort;
  reasoning_effort(legal_agent) == high_effort;
  reasoning_effort(review_agent) == high_effort;
  reasoning_effort(fallback_agent) == high_effort;
  reasoning_effort(final_agent) == high_effort;
}
```