```fusionflow
-- SCENARIO: Score-based routing: accept when score >= 80, otherwise route to review
-- AUTHORED: from intent: "score-routed workflow with conditional term selection"

const request: Artifact;
const score: Artifact;
const accept_result: Artifact;
const review_result: Artifact;
const final_result: Artifact;

const scoring_step: Step;
const accept_handler: Step;
const review_handler: Step;
const final_step: Step;

const scoring_name: StepName;
const accept_name: StepName;
const review_name: StepName;
const final_name: StepName;

const scoring_instruction: Instruction;
const accept_instruction: Instruction;
const review_instruction: Instruction;
const final_instruction: Instruction;

const scoring_agent: Agent, Executor;
const accept_agent: Agent, Executor;
const review_agent: Agent, Executor;
const final_agent: Agent, Executor;

const shared_model: Model;
const shared_engine: Engine;
const shared_api: ApiBase;

workflow score_routing {
  -- DATA FLOW
  input_workflow(score_routing) == [request];
  consumes(scoring_step) == [request];
  produces(scoring_step) == [score];
  consumes(accept_handler) == [request];
  produces(accept_handler) == [accept_result];
  consumes(review_handler) == [request];
  produces(review_handler) == [review_result];
  consumes(final_step) == [if(score >= 80, accept_result, review_result)];
  produces(final_step) == [final_result];
  output_workflow(score_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(scoring_step) == scoring_agent;
  step_executor(accept_handler) == accept_agent;
  step_executor(review_handler) == review_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(scoring_step) == scoring_name;
  step_instruction(scoring_step) == scoring_instruction;
  step_name(accept_handler) == accept_name;
  step_instruction(accept_handler) == accept_instruction;
  step_name(review_handler) == review_name;
  step_instruction(review_handler) == review_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- AGENT CONFIGURATION
  agent_config(scoring_agent, shared_model, shared_engine, shared_api);
  agent_config(accept_agent, shared_model, shared_engine, shared_api);
  agent_config(review_agent, shared_model, shared_engine, shared_api);
  agent_config(final_agent, shared_model, shared_engine, shared_api);

  -- WORKFLOW CONFIGURATION
  max_concurrency(score_routing) == 3;
  workflow_timeout(score_routing) == 600;
}
```