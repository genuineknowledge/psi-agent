```fusionflow
-- SCENARIO: Decision-based routing with always-executed parallel handlers
-- AUTHORED: 2025-03-26 12:00:00 from intent: "工作流接收request；判定Step产出approved_flag；approve_handler和reject_handler都消费request并执行，最终输出根据flag选择artifact"

const request: Artifact;
const approved_flag: Artifact;
const approved_artifact: Artifact;
const rejected_artifact: Artifact;

const judge_step: Step;
const approve_handler_step: Step;
const reject_handler_step: Step;

const judge_step_name: StepName;
const approve_step_name: StepName;
const reject_step_name: StepName;

const judge_instruction: Instruction;
const approve_instruction: Instruction;
const reject_instruction: Instruction;

const judge_agent: Agent, Executor;
const approve_agent: Agent, Executor;
const reject_agent: Agent, Executor;

const common_model: Model;
const common_engine: Engine;
const common_api: ApiBase;

workflow decision_routing {
  -- DATA FLOW
  input_workflow(decision_routing) == [request];
  consumes(judge_step) == [request];
  produces(judge_step) == [approved_flag];
  consumes(approve_handler_step) == [request];
  produces(approve_handler_step) == [approved_artifact];
  consumes(reject_handler_step) == [request];
  produces(reject_handler_step) == [rejected_artifact];
  output_workflow(decision_routing) == [
    if(approved_flag = "approve", approved_artifact, rejected_artifact)
  ];

  -- EXECUTOR ASSIGNMENT
  step_executor(judge_step) == judge_agent;
  step_executor(approve_handler_step) == approve_agent;
  step_executor(reject_handler_step) == reject_agent;

  -- STEP CONFIGURATION
  step_name(judge_step) == judge_step_name;
  step_instruction(judge_step) == judge_instruction;
  step_name(approve_handler_step) == approve_step_name;
  step_instruction(approve_handler_step) == approve_instruction;
  step_name(reject_handler_step) == reject_step_name;
  step_instruction(reject_handler_step) == reject_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(decision_routing) == 2;

  -- AGENT CONFIGURATION
  agent_config(judge_agent, common_model, common_engine, common_api);
  agent_config(approve_agent, common_model, common_engine, common_api);
  agent_config(reject_agent, common_model, common_engine, common_api);
}
```