我们被要求编写一个完整的、可编译的 FusionFlow G4 工作流，满足以下要求：

1. 输入：request
2. 一个判定 Step 产出 approved_flag
3. approve_handler 和 reject_handler 都消费 request，每次执行都实际运行
4. approve_handler 产出 approved_artifact，reject_handler 产出 rejected_artifact
5. 工作流最终输出应该是根据 approved_flag 选择出的 Artifact 本身，不要增加只负责转发的最终 Step。

要求：选择必须由工作流结构真实执行。也就是通过条件选择（if）来实现根据 approved_flag 选择输出。

注意：output_workflow 是 List 类型的，我们需要输出单一的 Artifact。根据 skill，output_workflow 也必须是 List，即使只有一个元素。所以我们可以让 output_workflow 包含一个 if 表达式来选择 approved_artifact 或 rejected_artifact。

但有一个微妙点：if 表达式返回一个 term，它可以是 Artifact。在 output_workflow 中，List 内容是一个 term。我们可以写 `output_workflow(workflow) == [if(approved_flag = True, approved_artifact, rejected_artifact)];`。但是这里注意：`approved_flag` 本身是什么？题目说 "判定 Step 产出 approved_flag"，所以 approved_flag 应该是一个 Artifact（或者一个布尔值？）。在 FusionFlow 中，Artifact 是通用的数据容器。我们也可以让判定 Step 产出 approved_flag 作为 Artifact，它的值可能是一个布尔文本或某种指示。然后在公式中比较是否等于 True。或者我们也可以把 approved_flag 作为一个布尔常量？但题目说 "产出 approved_flag"，应该是一个 Artifact，其内容代表是否批准。

由于我们使用 `if` 的条件公式，需要使用比较操作符。比较操作符两侧都是 term。term 可以是 constantName（包括布尔字面量）或 operatorCall 等。approved_flag 是一个 Artifact，我们可以比较 `approved_flag == true`？注意语法中的比较使用 `=` 表示相等比较（NUMERIC_EQ），而 `==` 是断言。在 formula 中我们使用 comparison，comparison 使用 `NUMERIC_EQ` 即 `=`。所以我们可以写 `approved_flag = TRUE`。

但要注意：`TRUE` 是一个 booleanLiteral，它可以是 `True`、`true` 或 `TRUE`。这样比较是合法的。但是 Artifact 类型与布尔字面量比较是否合法？根据 grammar，comparison 比较两个 term，类型由 catalog 决定，我们假设 catalog 允许 Artifact 与 Bool 比较。

或者我们可以让 approved_flag 是一个布尔值常量？但题目要求 Step 产出 approved_flag，所以我们得把它定义为 Artifact。

另一种方式：我们可以将 approved_flag 定义为一个 Artifact，然后在 if 条件中使用它。实际运行时，如果 approved_flag 的内容为某个非空或特定值，但我们需要精确的条件。

为了简单，我们假设判定 Step 产出的 approved_flag 内容等于布尔值 true 或 false。我们用 `approved_flag = true` 作为条件。

现在，设计工作流结构：

- 常量声明：
  - request: Artifact
  - approved_flag: Artifact
  - approved_artifact: Artifact
  - rejected_artifact: Artifact
  - final_output: Artifact（实际上我们不需要 final_output 变量，在 output_workflow 中直接使用 if 表达式。但为了清晰，可以声明但不用）
  - 各种 Step、Executor、StepName、Instruction。
  - 需要 agent 等。我们使用默认的 agent？但题目没有指定 executor，我们得声明一些 Executor（比如 judge_agent, approve_agent, reject_agent）。
  - 还要有一个模型等？但 simplest 可以是只声明 Agent, Executor。我们尽量简单。

要求补全所需常量声明、数据流、执行器绑定、StepName 和 Instruction 配置。所以我们需要声明所有必要的常量。

我们按照 standard 模式：

```
const request: Artifact;
const approved_flag: Artifact;
const approved_artifact: Artifact;
const rejected_artifact: Artifact;

const judge_step: Step;
const approve_handler: Step;
const reject_handler: Step;

const judge_name: StepName;
const approve_name: StepName;
const reject_name: StepName;

const judge_instruction: Instruction;
const approve_instruction: Instruction;
const reject_instruction: Instruction;

const judge_agent: Agent, Executor;
const approve_agent: Agent, Executor;
const reject_agent: Agent, Executor;

const some_model: Model;
const some_engine: Engine;
const some_api: ApiBase;

workflow decision_workflow {
  -- DATA FLOW
  input_workflow(decision_workflow) == [request];
  consumes(judge_step) == [request];
  produces(judge_step) == [approved_flag];
  consumes(approve_handler) == [request];
  produces(approve_handler) == [approved_artifact];
  consumes(reject_handler) == [request];
  produces(reject_handler) == [rejected_artifact];
  output_workflow(decision_workflow) == [if(approved_flag = true, approved_artifact, rejected_artifact)];

  -- EXECUTOR ASSIGNMENT
  step_executor(judge_step) == judge_agent;
  step_executor(approve_handler) == approve_agent;
  step_executor(reject_handler) == reject_agent;

  -- STEP CONFIGURATION
  step_name(judge_step) == judge_name;
  step_instruction(judge_step) == judge_instruction;
  step_name(approve_handler) == approve_name;
  step_instruction(approve_handler) == approve_instruction;
  step_name(reject_handler) == reject_name;
  step_instruction(reject_handler) == reject_instruction;

  -- AGENT CONFIGURATION
  agent_config(judge_agent, some_model, some_engine, some_api);
  agent_config(approve_agent, some_model, some_engine, some_api);
  agent_config(reject_agent, some_model, some_engine, some_api);
}
```

注意：output_workflow 直接使用 if 表达式选择 artifact，没有额外的 final step。这正是题目要求的。

还需要注意：approved_flag 被判定 Step 产出，但 approve_handler 和 reject_handler 都消费 request，且每次执行都实际运行。它们是独立于 approved_flag 的，所以即使 approved_flag 为真，reject_handler 也会运行（因为没有依赖让它可以跳过）。题目要求 "每次执行都实际运行"，所以它们应该没有条件跳过，这符合我们的数据流：它们只依赖 request，不依赖 approved_flag。所以它们会被执行。

关于 if 条件：`approved_flag = true`，这里 `true` 是一个 booleanLiteral（TRUE）。我们需要确保 TRUE 被定义为一个 term。在 grammar 中，booleanLiteral 是 atomicTerm 的一种，所以可以作为 term 使用。

验证：output_workflow 中的列表包含一个 if 表达式，if 表达式的 then 和 else 是 approved_artifact 和 rejected_artifact，都是 Artifact，类型匹配 List 元素类型。

还需要注意：在 output_workflow 中，if 表达式本身是一个 term，所以可以放在列表里。

现在，编写完整的代码块。不要任何解释，只返回 fusionflow 代码块。

注意：我们还需要声明 Executor 所需的 Agent、Model、Engine、ApiBase 等常量，并且不能使用占位符 `_`。我们需要提供具体的常量名，但它们可以是任意合法的 lower ID。我们使用一些通用的名称。

另外，我们还需要确保所有使用的常量都已声明。例如，在 agent_config 中我们需要 Model, Engine, ApiBase。我们声明 `some_model: Model;` 等。

最终代码：

```fusionflow
-- SCENARIO: decision workflow with conditional output based on approved_flag
-- AUTHORED: 2025-03-23 12:00:00 from intent: "workflow that chooses output based on flag"

const request: Artifact;
const approved_flag: Artifact;
const approved_artifact: Artifact;
const rejected_artifact: Artifact;

const judge_step: Step;
const approve_handler: Step;
const reject_handler: Step;

const judge_name: StepName;
const approve_name: StepName;
const reject_name: StepName;

const judge_instruction: Instruction;
const approve_instruction: Instruction;
const reject_instruction: Instruction;

const judge_agent: Agent, Executor;
const approve_agent: Agent, Executor;
const reject_agent: Agent, Executor;

const judge_model: Model;
const judge_engine: Engine;
const judge_api: ApiBase;

const approve_model: Model;
const approve_engine: Engine;
const approve_api: ApiBase;

const reject_model: Model;
const reject_engine: Engine;
const reject_api: ApiBase;

workflow decision_workflow {
  -- DATA FLOW
  input_workflow(decision_workflow) == [request];
  consumes(judge_step) == [request];
  produces(judge_step) == [approved_flag];
  consumes(approve_handler) == [request];
  produces(approve_handler) == [approved_artifact];
  consumes(reject_handler) == [request];
  produces(reject_handler) == [rejected_artifact];
  output_workflow(decision_workflow) == [if(approved_flag = true, approved_artifact, rejected_artifact)];

  -- EXECUTOR ASSIGNMENT
  step_executor(judge_step) == judge_agent;
  step_executor(approve_handler) == approve_agent;
  step_executor(reject_handler) == reject_agent;

  -- STEP CONFIGURATION
  step_name(judge_step) == judge_name;
  step_instruction(judge_step) == judge_instruction;
  step_name(approve_handler) == approve_name;
  step_instruction(approve_handler) == approve_instruction;
  step_name(reject_handler) == reject_name;
  step_instruction(reject_handler) == reject_instruction;

  -- AGENT CONFIGURATION
  agent_config(judge_agent, judge_model, judge_engine, judge_api);
  agent_config(approve_agent, approve_model, approve_engine, approve_api);
  agent_config(reject_agent, reject_model, reject_engine, reject_api);
}
```

注意：在条件中，我使用了 `approved_flag = true`。但我需要确认 `true` 是布尔字面量。在 grammar 中，TRUE 可以是 'True' | 'true' | 'TRUE'。这里使用小写 true 应该被接受。但为了清晰，使用小写 true 还是 TRUE？我用 lower case true。

但是 grammar 中 booleanLiteral 的规则是 `TRUE : 'True' | 'true' | 'TRUE';`，所以 `true` 是匹配的。

另外，在 if 表达式中， formula 部分允许 comparison，而 comparison 用 `=` 比较。所以 `approved_flag = true` 是一个 comparison。

现在，输出代码块。