```fusionflow
-- SCENARIO: Score-based routing workflow with eager execution of both branches

const request: Artifact;
const score: Artifact;
const accept_result: Artifact;
const review_result: Artifact;
const selected: Artifact;
const final_result: Artifact;

const scoring_step: Step;
const accept_handler_step: Step;
const review_handler_step: Step;
const final_step: Step;

const "score_request": StepName;
const "accept_handler": StepName;
const "review_handler": StepName;
const "finalize": StepName;

const "./instructions/score_request.md": Instruction;
const "./instructions/accept_handler.md": Instruction;
const "./instructions/review_handler.md": Instruction;
const "./instructions/finalize.md": Instruction;

const scoring_agent: Agent, Executor;
const accept_agent: Agent, Executor;
const review_agent: Agent, Executor;
const final_agent: Agent, Executor;

const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;

workflow score_routing {
  -- DATA FLOW
  input_workflow(score_routing) == [request];
  consumes(scoring_step) == [request];
  produces(scoring_step) == [score];
  consumes(accept_handler_step) == [request];
  produces(accept_handler_step) == [accept_result];
  consumes(review_handler_step) == [request];
  produces(review_handler_step) == [review_result];
  selected == if(score >= 80, accept_result, review_result);
  consumes(final_step) == [selected];
  produces(final_step) == [final_result];
  output_workflow(score_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(scoring_step) == scoring_agent;
  step_executor(accept_handler_step) == accept_agent;
  step_executor(review_handler_step) == review_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(scoring_step) == "score_request";
  step_instruction(scoring_step) == "./instructions/score_request.md";
  step_name(accept_handler_step) == "accept_handler";
  step_instruction(accept_handler_step) == "./instructions/accept_handler.md";
  step_name(review_handler_step) == "review_handler";
  step_instruction(review_handler_step) == "./instructions/review_handler.md";
  step_name(final_step) == "finalize";
  step_instruction(final_step) == "./instructions/finalize.md";

  -- AGENT CONFIGURATION
  agent_config(scoring_agent, default_model, default_engine, default_api);
  agent_config(accept_agent, default_model, default_engine, default_api);
  agent_config(review_agent, default_model, default_engine, default_api);
  agent_config(final_agent, default_model, default_engine, default_api);
}
```