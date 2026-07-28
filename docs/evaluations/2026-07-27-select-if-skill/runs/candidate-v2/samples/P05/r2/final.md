```fusionflow
-- SCENARIO: Two independent conditional selections after routing
-- REQUEST: Routing step produces tone_flag and risk_flag; four handlers run on every request;
--          independently choose formal/casual and escalated/safe; final step consumes both choices.

const request: Artifact;
const tone_flag: Artifact;
const risk_flag: Artifact;
const formal_result: Artifact;
const casual_result: Artifact;
const safe_result: Artifact;
const escalated_result: Artifact;
const selected_tone_result: Artifact;
const selected_risk_result: Artifact;
const final_result: Artifact;

const routing_step: Step;
const formal_handler: Step;
const casual_handler: Step;
const safe_handler: Step;
const escalated_handler: Step;
const final_step: Step;

const router: Agent, Executor;
const formal_agent: Agent, Executor;
const casual_agent: Agent, Executor;
const safe_agent: Agent, Executor;
const escalated_agent: Agent, Executor;
const final_agent: Agent, Executor;

workflow conditional_routing {
  -- DATA FLOW
  input_workflow(conditional_routing) == [request];
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
  selected_tone_result == if(tone_flag = "formal_tone", formal_result, casual_result);
  selected_risk_result == if(risk_flag = "high_risk", escalated_result, safe_result);
  consumes(final_step) == [selected_tone_result, selected_risk_result];
  produces(final_step) == [final_result];
  output_workflow(conditional_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(routing_step) == router;
  step_executor(formal_handler) == formal_agent;
  step_executor(casual_handler) == casual_agent;
  step_executor(safe_handler) == safe_agent;
  step_executor(escalated_handler) == escalated_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(routing_step) == "routing";
  step_instruction(routing_step) == "./instructions/routing.md";
  step_name(formal_handler) == "formal_handler";
  step_instruction(formal_handler) == "./instructions/formal.md";
  step_name(casual_handler) == "casual_handler";
  step_instruction(casual_handler) == "./instructions/casual.md";
  step_name(safe_handler) == "safe_handler";
  step_instruction(safe_handler) == "./instructions/safe.md";
  step_name(escalated_handler) == "escalated_handler";
  step_instruction(escalated_handler) == "./instructions/escalated.md";
  step_name(final_step) == "final_step";
  step_instruction(final_step) == "./instructions/final.md";

  -- WORKFLOW CONFIGURATION
  max_concurrency(conditional_routing) == 5;
  workflow_timeout(conditional_routing) == 600;

  -- AGENT CONFIGURATION
  agent_config(router, "claude-3-5-sonnet", "claude", "https://api.anthropic.com");
  agent_config(formal_agent, "claude-3-5-sonnet", "claude", "https://api.anthropic.com");
  agent_config(casual_agent, "claude-3-5-sonnet", "claude", "https://api.anthropic.com");
  agent_config(safe_agent, "claude-3-5-sonnet", "claude", "https://api.anthropic.com");
  agent_config(escalated_agent, "claude-3-5-sonnet", "claude", "https://api.anthropic.com");
  agent_config(final_agent, "claude-3-5-sonnet", "claude", "https://api.anthropic.com");
}
```