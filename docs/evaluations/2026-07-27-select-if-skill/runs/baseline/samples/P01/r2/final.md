```fusionflow
-- SCENARIO: Classify request, run both handlers, choose result based on category
-- AUTHORED: from intent: "分类 Step 产出 category；两个处理 Step 都消费 request；最终 Step 根据 category 选择结果"

const request: Artifact;
const primary_category: Artifact;
const category: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const final_result: Artifact;

const classify_step: Step;
const process_primary_step: Step;
const process_fallback_step: Step;
const final_step: Step;

const classify_name: StepName;
const process_primary_name: StepName;
const process_fallback_name: StepName;
const final_name: StepName;

const classify_instruction: Instruction;
const process_primary_instruction: Instruction;
const process_fallback_instruction: Instruction;
const final_instruction: Instruction;

const classify_agent: Agent, Executor;
const process_primary_agent: Agent, Executor;
const process_fallback_agent: Agent, Executor;
const final_agent: Agent, Executor;

const common_model: Model;
const common_engine: Engine;
const common_api: ApiBase;
const default_effort: ReasoningEffort;
const read_tool: Tool;

workflow conditional_choice {
  -- DATA FLOW
  input_workflow(conditional_choice) == [request, primary_category];
  consumes(classify_step) == [request];
  produces(classify_step) == [category];
  consumes(process_primary_step) == [request];
  produces(process_primary_step) == [primary_result];
  consumes(process_fallback_step) == [request];
  produces(process_fallback_step) == [fallback_result];
  consumes(final_step) == [if(category = primary_category, primary_result, fallback_result)];
  produces(final_step) == [final_result];
  output_workflow(conditional_choice) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(classify_step) == classify_agent;
  step_executor(process_primary_step) == process_primary_agent;
  step_executor(process_fallback_step) == process_fallback_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(classify_step) == classify_name;
  step_instruction(classify_step) == classify_instruction;
  step_name(process_primary_step) == process_primary_name;
  step_instruction(process_primary_step) == process_primary_instruction;
  step_name(process_fallback_step) == process_fallback_name;
  step_instruction(process_fallback_step) == process_fallback_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- AGENT CONFIGURATION
  agent_config(classify_agent, common_model, common_engine, common_api);
  agent_config(process_primary_agent, common_model, common_engine, common_api);
  agent_config(process_fallback_agent, common_model, common_engine, common_api);
  agent_config(final_agent, common_model, common_engine, common_api);

  allowed_tool(classify_agent, read_tool);
  allowed_tool(process_primary_agent, read_tool);
  allowed_tool(process_fallback_agent, read_tool);
  allowed_tool(final_agent, read_tool);

  reasoning_effort(classify_agent) == default_effort;
  reasoning_effort(process_primary_agent) == default_effort;
  reasoning_effort(process_fallback_agent) == default_effort;
  reasoning_effort(final_agent) == default_effort;
}
```