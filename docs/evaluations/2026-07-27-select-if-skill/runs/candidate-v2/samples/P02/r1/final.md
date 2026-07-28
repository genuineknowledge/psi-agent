```fusionflow
-- SCENARIO: Priority routing workflow: triage flags, run all handlers, select result based on flags.
-- AUTHORED: 2025-01-01 from intent: "priority routing with triage and three handlers"

const request: Artifact;
const urgent_flag: Artifact;
const allowed_flag: Artifact;
const review_flag: Artifact;
const exception_flag: Artifact;
const primary_result: Artifact;
const review_result: Artifact;
const fallback_result: Artifact;
const review_or_fallback: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

const triage_step: Step;
const primary_step: Step;
const review_step: Step;
const fallback_step: Step;
const final_step: Step;

const triage_name: StepName;
const primary_name: StepName;
const review_name: StepName;
const fallback_name: StepName;
const final_name: StepName;

const triage_instruction: Instruction;
const primary_instruction: Instruction;
const review_instruction: Instruction;
const fallback_instruction: Instruction;
const final_instruction: Instruction;

const triage_agent: Agent, Executor;
const primary_agent: Agent, Executor;
const review_agent: Agent, Executor;
const fallback_agent: Agent, Executor;
const final_agent: Agent, Executor;

const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;
const high_effort: ReasoningEffort;
const read_tool: Tool;

workflow priority_routing {
  -- DATA FLOW
  input_workflow(priority_routing) == [request];
  consumes(triage_step) == [request];
  produces(triage_step) == [urgent_flag, allowed_flag, review_flag, exception_flag];
  consumes(primary_step) == [request];
  produces(primary_step) == [primary_result];
  consumes(review_step) == [request];
  produces(review_step) == [review_result];
  consumes(fallback_step) == [request];
  produces(fallback_step) == [fallback_result];
  review_or_fallback == if(
    (review_flag = True) OR (exception_flag = True),
    review_result,
    fallback_result
  );
  selected_result == if(
    (urgent_flag = True) AND (allowed_flag = True),
    primary_result,
    review_or_fallback
  );
  consumes(final_step) == [selected_result];
  produces(final_step) == [final_result];
  output_workflow(priority_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(triage_step) == triage_agent;
  step_executor(primary_step) == primary_agent;
  step_executor(review_step) == review_agent;
  step_executor(fallback_step) == fallback_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(triage_step) == triage_name;
  step_instruction(triage_step) == triage_instruction;
  step_timeout(triage_step) == 300;
  max_attempts(triage_step) == 2;
  step_name(primary_step) == primary_name;
  step_instruction(primary_step) == primary_instruction;
  step_timeout(primary_step) == 300;
  max_attempts(primary_step) == 2;
  step_name(review_step) == review_name;
  step_instruction(review_step) == review_instruction;
  step_timeout(review_step) == 300;
  max_attempts(review_step) == 2;
  step_name(fallback_step) == fallback_name;
  step_instruction(fallback_step) == fallback_instruction;
  step_timeout(fallback_step) == 300;
  max_attempts(fallback_step) == 2;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;
  step_timeout(final_step) == 300;
  max_attempts(final_step) == 2;

  -- WORKFLOW CONFIGURATION
  max_concurrency(priority_routing) == 3;
  workflow_timeout(priority_routing) == 900;

  -- AGENT CONFIGURATION
  agent_config(triage_agent, default_model, default_engine, default_api);
  agent_config(primary_agent, default_model, default_engine, default_api);
  agent_config(review_agent, default_model, default_engine, default_api);
  agent_config(fallback_agent, default_model, default_engine, default_api);
  agent_config(final_agent, default_model, default_engine, default_api);
  allowed_tool(triage_agent, read_tool);
  allowed_tool(primary_agent, read_tool);
  allowed_tool(review_agent, read_tool);
  allowed_tool(fallback_agent, read_tool);
  allowed_tool(final_agent, read_tool);
  reasoning_effort(triage_agent) == high_effort;
  reasoning_effort(primary_agent) == high_effort;
  reasoning_effort(review_agent) == high_effort;
  reasoning_effort(fallback_agent) == high_effort;
  reasoning_effort(final_agent) == high_effort;
}
```