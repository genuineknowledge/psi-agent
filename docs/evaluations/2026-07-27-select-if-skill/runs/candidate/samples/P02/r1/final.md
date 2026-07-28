```fusionflow
-- SCENARIO: Priority routing: triage request → run three handlers in parallel → select final result based on flag conditions
-- AUTHORED: 2025-04-02 10:00:00 from intent: "优先级路由工作流，输入 request，triage 产出四个布尔 flag，三个处理步骤都运行，根据 flag 选择结果"

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
const primary_handler_step: Step;
const review_handler_step: Step;
const fallback_handler_step: Step;
const final_step: Step;

const triage_agent: Agent, Executor;
const primary_handler: Agent, Executor;
const review_handler: Agent, Executor;
const fallback_handler: Agent, Executor;
const final_consumer: Agent, Executor;

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
  consumes(primary_handler_step) == [request];
  produces(primary_handler_step) == [primary_result];
  consumes(review_handler_step) == [request];
  produces(review_handler_step) == [review_result];
  consumes(fallback_handler_step) == [request];
  produces(fallback_handler_step) == [fallback_result];
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
  step_executor(primary_handler_step) == primary_handler;
  step_executor(review_handler_step) == review_handler;
  step_executor(fallback_handler_step) == fallback_handler;
  step_executor(final_step) == final_consumer;

  -- STEP CONFIGURATION
  step_name(triage_step) == "Triage Request";
  step_instruction(triage_step) == "Analyze the request and set four boolean flags: urgent_flag (is this urgent?), allowed_flag (is this request allowed by policy?), review_flag (does it require human review?), and exception_flag (is it an exception case?). Output these flags as boolean values.";
  step_name(primary_handler_step) == "Primary Handler";
  step_instruction(primary_handler_step) == "Process the request as a primary case and produce the primary result.";
  step_name(review_handler_step) == "Review Handler";
  step_instruction(review_handler_step) == "Process the request with additional human-like review and produce the review result.";
  step_name(fallback_handler_step) == "Fallback Handler";
  step_instruction(fallback_handler_step) == "Process the request using standard fallback logic and produce the fallback result.";
  step_name(final_step) == "Final Step";
  step_instruction(final_step) == "Consume the selected result and produce the final output.";
  step_timeout(triage_step) == 300;
  step_timeout(primary_handler_step) == 300;
  step_timeout(review_handler_step) == 300;
  step_timeout(fallback_handler_step) == 300;
  step_timeout(final_step) == 120;
  max_attempts(triage_step) == 2;
  max_attempts(primary_handler_step) == 2;
  max_attempts(review_handler_step) == 2;
  max_attempts(fallback_handler_step) == 2;

  -- WORKFLOW CONFIGURATION
  max_concurrency(priority_routing) == 3;
  workflow_timeout(priority_routing) == 900;

  -- AGENT CONFIGURATION
  agent_config(triage_agent, default_model, default_engine, default_api);
  agent_config(primary_handler, default_model, default_engine, default_api);
  agent_config(review_handler, default_model, default_engine, default_api);
  agent_config(fallback_handler, default_model, default_engine, default_api);
  agent_config(final_consumer, default_model, default_engine, default_api);

  allowed_tool(triage_agent, read_tool);
  allowed_tool(primary_handler, read_tool);
  allowed_tool(review_handler, read_tool);
  allowed_tool(fallback_handler, read_tool);
  reasoning_effort(triage_agent) == high_effort;
  reasoning_effort(primary_handler) == high_effort;
  reasoning_effort(review_handler) == high_effort;
  reasoning_effort(fallback_handler) == high_effort;
}
```