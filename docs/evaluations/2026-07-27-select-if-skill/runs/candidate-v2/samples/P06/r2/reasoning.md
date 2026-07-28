我们被要求编写一个 FusionFlow G4 工作流，其语义：

- 接收一个 request (Artifact)。
- 有一个判定 Step 产出 approved_flag。
  - approved_flag 是一个 Artifact。
- approve_handler 和 reject_handler 都消费 request，并且每次执行都实际运行（即不能有条件跳过，必须两种handler都执行）。
  - approve_handler 产出 approved_artifact。
  - reject_handler 产出 rejected_artifact。
- 最终输出应当根据 approved_flag 选择出的 Artifact 本身（approved_artifact 或 rejected_artifact），不能再增加一个只负责转发的最终 Step。

注意：不能增加最终 Step，意味着最终输出必须直接是选择结果。在 G4 中，output_workflow 必须是一个 Artifact 列表。我们可以使用 if 表达式结合 Artifact 选择来实现：声明一个 selected_result Artifact，然后使用 if 条件选择。由于 if 是值选择，不引入新的 Step。所以最后 output_workflow 输出 selected_result 即可。

另外，要求 approve_handler 和 reject_handler 都实际运行，不能因为 if 选择而跳过其中一个 producer。我们的 if 选择是 eager 的：它运行所有候选 producer，然后选择值。所以两种 handler 都会运行，满足要求。

需要定义：
- 输入: request
- Step: judge_step (产出 approved_flag)
- Step: approve_handler (消费 request, 产出 approved_artifact)
- Step: reject_handler (消费 request, 产出 rejected_artifact)
- Artifact: selected_result = if((approved_flag = "approved"), approved_artifact, rejected_artifact) 或其他条件方式。但我们假设 approved_flag 是一个 Artifact，内容可能是布尔值或字符串。在公式中，我们可以比较 approved_flag 与一个常量（如 "approved"）或者直接比较布尔值。但注意公式需要是 comparison，所以我们可以声明一个常量 approved_value: Artifact? 实际上，我们可以用比较 approved_flag = approved_value，但需要声明 approved_value 作为常量。或者我们可以直接使用 True/False，但 approved_flag 是 Artifact，比较需要 term comparison，所以可以用 approved_flag = true_literal? 但 TRUE 是 boolean 常量，但 approved_flag 是 Artifact，类型不同？实际上在 G4 中 Artifact 可以比较吗？在示例中有比较 observation = criterion，所以我们可以假设 Artifact 可以比较。我们可以声明一个常量 approved_constant: Artifact，值为 "yes" 或 "approved"，然后在公式中比较 approved_flag = approved_constant。

另一种方法：将 approved_flag 设为 Bool? 但声明 const approved_flag: Bool, Artifact; 但概念 Bool 在示例中未出现，可能不在 catalog 中。最好沿用 Artifact 比较方式。

具体实现：
```fusionflow
-- SCENARIO: request approval with eager handler execution
const request: Artifact;
const approved_flag: Artifact;
const approved_artifact: Artifact;
const rejected_artifact: Artifact;
const selected_result: Artifact;

const judge: Step;
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

const default_engine: Engine;
const default_model: Model;
const default_api: ApiBase;
const default_tool: Tool;

const approved_constant: Artifact;

workflow request_workflow {
  -- DATA FLOW
  input_workflow(request_workflow) == [request];
  consumes(judge) == [request];
  produces(judge) == [approved_flag];
  consumes(approve_handler) == [request];
  produces(approve_handler) == [approved_artifact];
  consumes(reject_handler) == [request];
  produces(reject_handler) == [rejected_artifact];
  selected_result == if(approved_flag = approved_constant, approved_artifact, rejected_artifact);
  output_workflow(request_workflow) == [selected_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(judge) == judge_agent;
  step_executor(approve_handler) == approve_agent;
  step_executor(reject_handler) == reject_agent;

  -- STEP CONFIGURATION
  step_name(judge) == judge_name;
  step_instruction(judge) == judge_instruction;
  step_name(approve_handler) == approve_name;
  step_instruction(approve_handler) == approve_instruction;
  step_name(reject_handler) == reject_name;
  step_instruction(reject_handler) == reject_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(request_workflow) == 3; -- all three can run in parallel

  -- AGENT CONFIGURATION
  agent_config(judge_agent, default_model, default_engine, default_api);
  agent_config(approve_agent, default_model, default_engine, default_api);
  agent_config(reject_agent, default_model, default_engine, default_api);
  allowed_tool(judge_agent, default_tool);
  allowed_tool(approve_agent, default_tool);
  allowed_tool(reject_agent, default_tool);
}
```

注意：我们需要定义 approved_constant 作为 Artifact，它的值应当通过外部输入（或常量）确定。但用户没有指定 approved_flag 的具体值，所以我们可以将其设为工作流输入的一部分？实际上，judge step 应该产生一个标记，然后条件比较。我们可以让 approved_constant 作为工作流输入传入（例如通过 input_workflow）。但题目说“接受 request”，只提到这一个输入。可能 approved_constant 可以是内部常量（比如字符串 "approved"）。但 grammar 中常量声明只能是 NUMBER、RELATIVE_PATH_ID、QUOTEDCONSTANTID 或 LOWID。如果我们声明一个 Artifact 的常量，它的值如何设定？在 G4 中，常量只是一个名字，具体值在运行时由外部提供？或者我们可以将 approved_constant 作为工作流输入的一部分，让用户提供。

但是题目说“工作流接收 request”，没有说还要接收 approved_constant。所以最好让 judge step 输出一个可以用布尔比较的值。比较时，我们可以直接比较 approved_flag = True? 但 True 是 booleanLiteral，可 not 是 Artifact 类型。或者我们可以将 approved_flag 声明为 Artifact, Bool（多概念），但 Bool 是否在 catalog 中存在？skill 中提到 Bool 用于 operator return type，但没有提到可以直接声明为 Bool 的常量。示例中使用了 observation = criterion 的比较，其中两者都是 Artifact。所以我认为最好让 approved_flag 和 approved_constant 都是 Artifact，approved_constant 的值通过一个额外的输入提供。但题目没有给出额外输入，所以我们可以将 approved_constant 定义为不带 input 的内部常量？但 G4 中常量声明只给名字和概念，不给值。值是由运行时提供的（比如通过输入或环境变量）。所以我们需要在 input_workflow 中增加 approved_constant 作为输入，否则它没有值。这样用户需要提供两个输入：request 和 approved_constant。

但题目描述 “工作流接收 request” 似乎暗示只有一个输入。但为了可执行，我们可以让 judge step 产出 approved_flag 为一个 Artifact，其值为布尔或字符串，然后在 if 表达式中比较 approved_flag = 'true' 之类的，但 'true' 是 QUOTEDCONSTANTID，可以声明为 Artifact 常量。不过我们不知道 judge 产出的内容具体是什么。我们可以假设 judge 产出 "approved" 或 "rejected"，所以我们可以声明一个常量 true_flag: Artifact 并通过 input 传入，或者直接在 if 中使用字面量。在 if 表达式中，我们可以使用 quoted constant 吗？if 的条件是一个 formula，其中 comparison 是 term comparisonOp term，term 可以是 atomicTerm，atomicTerm 包括 constantName，constantName 可以是 QUOTEDCONSTANTID。所以我们可以直接写 approved_flag = "approved" 吗？"approved" 是 QUOTEDCONSTANTID，g4 识别。所以我们可以避免额外输入常量，直接使用字面常量。

但注意：approved_flag 是 Artifact，而 "approved" 是 QUOTEDCONSTANTID（也是常数名）。这应该可以比较，因为都是 term。所以可以直接写 approved_flag = "approved"。

这样就不需要 approved_constant 常量和额外输入了。

修改后：

```fusionflow
selected_result == if(approved_flag = "approved", approved_artifact, rejected_artifact);
```

但 "approved" 是 QuotedConstantId，它被 grammar 识别为 constantName。所以是合法的。

还需要定义一些必要的常量，如 agent 配置中用到的 model、engine、api base、tool。但是这些是外部 catalog 定义的，我们只需要声明常量。workspace 环境下这些常量应该已经存在。按照 skill 中的示例，我们声明 default_engine...。注意命名是否需要和示例一样？示例用了 review_model, review_engine, review_api, read_tool。我们可以通用化。

另外，注意没有定义 approved_flag 为 Bool 或其他，只是 Artifact。所以没问题。

我们需要一个完整的文件，包括所有声明和 workflow 块。注意我们必须包含所有必需的关系：input_workflow, output_workflow, consumes/produces for all steps, step_executor, step_name, step_instruction。

还可能需要 max_concurrency 以允许 judge、approve_handler、reject_handler 并行运行（judge 和两个 handler 没有依赖关系，可以并行；但是条件选择取决于 judge，但 eager 执行仍会执行所有三步）。我们设置 max_concurrency=3 合适。

另外，agree 与 reject 需要独立的 Name 和 Instruction 常量。我们可以用 judge_name 等。

还要注意，output_workflow 输出 selected_result，这是一个 Artifact，符合要求：最终输出就是根据 approved_flag 选择出的 Artifact 本身，没有额外转发 Step。

确保没有 final Step 来转发。这里 output_workflow 直接输出 selected_result，selected_result 则由 if 公式定义，if 公式本身不产生 Step，只是一个值选择。

现在写出最终答案。