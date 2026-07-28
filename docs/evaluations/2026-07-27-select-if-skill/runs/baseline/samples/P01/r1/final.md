```fusionflow
-- SCENARIO: 分类路由工作流 - 两个处理分支，按分类结果选择最终输出
-- AUTHORED: 2025-01-01 00:00:00 from intent: "按类别决定使用哪个处理结果"

const request: Artifact;
const primary_category: Artifact;
const category: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const final_result: Artifact;

const classify_step: Step;
const primary_handler_step: Step;
const fallback_handler_step: Step;
const final_step: Step;

const classify_name: StepName;
const primary_handler_name: StepName;
const fallback_handler_name: StepName;
const final_name: StepName;

const classify_instruction: Instruction;
const primary_handler_instruction: Instruction;
const fallback_handler_instruction: Instruction;
const final_instruction: Instruction;

const classify_agent: Agent, Executor;
const primary_handler_agent: Agent, Executor;
const fallback_handler_agent: Agent, Executor;
const final_agent: Agent, Executor;

const my_model: Model;
const my_engine: Engine;
const my_api: ApiBase;

workflow conditional_routing {
  -- DATA FLOW
  input_workflow(conditional_routing) == [request, primary_category];
  consumes(classify_step) == [request];
  produces(classify_step) == [category];
  consumes(primary_handler_step) == [request];
  produces(primary_handler_step) == [primary_result];
  consumes(fallback_handler_step) == [request];
  produces(fallback_handler_step) == [fallback_result];
  consumes(final_step) == [
    if(
      category = primary_category,
      primary_result,
      fallback_result
    )
  ];
  produces(final_step) == [final_result];
  output_workflow(conditional_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(classify_step) == classify_agent;
  step_executor(primary_handler_step) == primary_handler_agent;
  step_executor(fallback_handler_step) == fallback_handler_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(classify_step) == classify_name;
  step_instruction(classify_step) == classify_instruction;
  step_name(primary_handler_step) == primary_handler_name;
  step_instruction(primary_handler_step) == primary_handler_instruction;
  step_name(fallback_handler_step) == fallback_handler_name;
  step_instruction(fallback_handler_step) == fallback_handler_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- AGENT CONFIGURATION
  agent_config(classify_agent, my_model, my_engine, my_api);
  agent_config(primary_handler_agent, my_model, my_engine, my_api);
  agent_config(fallback_handler_agent, my_model, my_engine, my_api);
  agent_config(final_agent, my_model, my_engine, my_api);
}
```