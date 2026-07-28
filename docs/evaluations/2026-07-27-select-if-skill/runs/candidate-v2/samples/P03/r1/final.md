```fusionflow
-- SCENARIO: score-based routing: accept when score >= 80, else review
-- AUTHORED: from intent: "score a request, then route to accept or review"

const request: Artifact;
const score: Artifact;
const accept_result: Artifact;
const review_result: Artifact;
const selected_result: Artifact;
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

const scorer: Agent, Executor;
const acceptor: Agent, Executor;
const reviewer: Agent, Executor;
const final_consumer: Agent, Executor;

const model: Model;
const engine: Engine;
const api: ApiBase;

workflow request_routing {
  -- DATA FLOW
  input_workflow(request_routing) == [request];

  consumes(scoring_step) == [request];
  produces(scoring_step) == [score];

  consumes(accept_handler) == [request];
  produces(accept_handler) == [accept_result];

  consumes(review_handler) == [request];
  produces(review_handler) == [review_result];

  selected_result == if(score >= 80, accept_result, review_result);

  consumes(final_step) == [selected_result];
  produces(final_step) == [final_result];

  output_workflow(request_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(scoring_step) == scorer;
  step_executor(accept_handler) == acceptor;
  step_executor(review_handler) == reviewer;
  step_executor(final_step) == final_consumer;

  -- STEP CONFIGURATION
  step_name(scoring_step) == scoring_name;
  step_instruction(scoring_step) == scoring_instruction;
  step_timeout(scoring_step) == 300;
  max_attempts(scoring_step) == 2;

  step_name(accept_handler) == accept_name;
  step_instruction(accept_handler) == accept_instruction;
  step_timeout(accept_handler) == 300;
  max_attempts(accept_handler) == 2;

  step_name(review_handler) == review_name;
  step_instruction(review_handler) == review_instruction;
  step_timeout(review_handler) == 300;
  max_attempts(review_handler) == 2;

  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;
  step_timeout(final_step) == 300;
  max_attempts(final_step) == 2;

  -- WORKFLOW CONFIGURATION
  max_concurrency(request_routing) == 3;
  workflow_timeout(request_routing) == 900;

  -- AGENT CONFIGURATION
  agent_config(scorer, model, engine, api);
  agent_config(acceptor, model, engine, api);
  agent_config(reviewer, model, engine, api);
  agent_config(final_consumer, model, engine, api);
}
```