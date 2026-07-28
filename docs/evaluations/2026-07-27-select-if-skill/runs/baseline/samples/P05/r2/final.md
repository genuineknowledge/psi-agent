```fusionflow
-- SCENARIO: route request by tone and risk, select handler results independently, merge both into final output
-- AUTHORED: from intent: "two independent conditional selections, four eager handlers, final merge"

const request: Artifact;
const formal_tone: Artifact;
const high_risk: Artifact;
const tone_flag: Artifact;
const risk_flag: Artifact;
const formal_result: Artifact;
const casual_result: Artifact;
const safe_result: Artifact;
const escalated_result: Artifact;
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

const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;
const high_effort: ReasoningEffort;
const read_tool: Tool;

workflow tone_risk_router {
  -- DATA FLOW
  input_workflow(tone_risk_router) ==
    [request, formal_tone, high_risk];

  consumes(routing_step) == [request];
  produces(routing_step) == [tone_flag, risk_flag];

  -- four eager handlers all consume the original request on every run
  consumes(formal_handler) == [request];
  produces(formal_handler) == [formal_result];
  consumes(casual_handler) == [request];
  produces(casual_handler) == [casual_result];
  consumes(safe_handler) == [request];
  produces(safe_handler) == [safe_result];
  consumes(escalated_handler) == [request];
  produces(escalated_handler) == [escalated_result];

  -- two independent conditional selections feed into one final step
  consumes(final_step) == [
    if((tone_flag = formal_tone), formal_result, casual_result),
    if((risk_flag = high_risk), escalated_result, safe_result)
  ];
  produces(final_step) == [final_result];
  output_workflow(tone_risk_router) == [final_result];

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

  -- WORKFLOW CONFIGURATION
  max_concurrency(tone_risk_router) == 5;
  workflow_timeout(tone_risk_router) == 600;

  -- AGENT CONFIGURATION
  agent_config(routing_agent, default_model, default_engine, default_api);
  agent_config(formal_agent, default_model, default_engine, default_api);
  agent_config(casual_agent, default_model, default_engine, default_api);
  agent_config(safe_agent, default_model, default_engine, default_api);
  agent_config(escalated_agent, default_model, default_engine, default_api);
  agent_config(final_agent, default_model, default_engine, default_api);

  allowed_tool(routing_agent, read_tool);
  allowed_tool(formal_agent, read_tool);
  allowed_tool(casual_agent, read_tool);
  allowed_tool(safe_agent, read_tool);
  allowed_tool(escalated_agent, read_tool);
  allowed_tool(final_agent, read_tool);
  reasoning_effort(routing_agent) == high_effort;
}
```