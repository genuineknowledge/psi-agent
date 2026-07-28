我们被要求编写一个 FusionFlow G4 工作流，具体需求：

1. 接收一个输入 `request`（类型 Artifact）。
2. 有一个判定 Step（记为 `judge_step`）产出 `approved_flag`（类型 Artifact）。
3. `approve_handler` 和 `reject_handler` 都消费 `request`，且每次执行都实际运行（即不能有条件跳过，两个都要跑）。它们分别产出 `approved_artifact` 和 `rejected_artifact`。
4. 最终输出应该就是根据 `approved_flag` 选择出的 Artifact 本身，且不要增加只负责转发的最终 Step。也就是说，我们需要用 `if` 表达式来选择输出 Artifact 之一，然后直接作为 `output_workflow` 的输出。
5. 补全常量声明、数据流、执行器绑定、StepName 和 Instruction 配置。

注意：`approved_flag` 是一个 Artifact，可能包含某种值（比如 "approved"/"rejected" 或者其他）。我们可以让 judge_step 产出 `approved_flag`，然后用 `if` 表达式基于该 flag 选择。但是 `if` 表达式中的条件比较需要 term comparison，我们需要对 `approved_flag` 与某个判据值（比如一个 Artifact 常量，或者某个字面量）进行比较。但是 FusionFlow 的 `comparison` 允许 `atomicTerm`，即常量名（NUMBER, LOWID, QUOTEDCONSTANTID, RELATIVE_PATH_ID）。我们可以定义常量 `approved_criterion` 作为 Artifact 类型，然后在输入中传入，或者直接定义一个字面量常量。但输入只能声明为 `Artifact` 类型，且 `input_workflow` 负责外部输入。我们可以预先设定判据常量，比如 `const approved_value: Artifact;` 作为一个预设值。但是如何定义它的值？在 G4 中常量可以是 NUMBER, LOWID, QUOTEDCONSTANTID, RELATIVE_PATH_ID。最好用一个 LOWID 作为引用，但需要实际值在运行时确定。可能更合理：让 judge_step 产出 `approved_flag`，其内容是一个布尔值或标记；但我们不能直接在 G4 中写字符串比较。或许我们可以用 `approved_flag` 与一个外部传入的 `approval_criterion` Artifact 比较，或者假设 judge_step 产出的 `approved_flag` 本身可以用作布尔值，然后 `if` 表达式条件可以直接用 `approved_flag` 作为公式？但 formula 必须是 comparison，不能是裸 term。所以我们必须有一个比较，比如 `approved_flag = some_criterion`。

考虑到可执行语义，我们可以让 judge_step 产出一个 Artifact，其内容是一个字符串 "true" 或 "false"，或者干脆直接是一个布尔值（但 G4 没有原生布尔 Artifact 类型）。最稳妥：我们定义一个输入 `approval_criterion`，让 judge_step 产出 `approved_flag` 是一个标记，然后比较 `approved_flag = approval_criterion`。但这样就需要两个外部输入。而需求只说了接收 `request`，没有说要额外输入。但需求里提到"接收 request"，但并没有说只能有一个输入。我们可以增加 `approval_criterion` 作为另一个输入，让 judge_step 产出 flag 并与其比较。或者，我们可以让 judge_step 产出 flag，flag 本身是一个字符串 "approved" 或 "rejected"，然后我们在工作流内部定义两个常量字符串 "approved" 和 "rejected" 作为比较目标。但 G4 中常量只能是 NUMBER, LOWID, QUOTEDCONSTANTID, RELATIVE_PATH_ID。QUOTEDCONSTANTID 是带双引号的字符串，所以我们可以定义 `const approved_token: QUOTEDCONSTANTID = "approved";` 和 `const rejected_token: QUOTEDCONSTANTID = "rejected";`。但 `QUOTEDCONSTANTID` 是一个 token 类型，不是字面量字符串；在 G4 中，常量定义 `const approved_token: Artifact;` 然后赋值为一个带引号的字符串？但 `constDecl` 只声明类型，不赋值。实际上在 G4 中，常量是通过 `const identifier: conceptNameList;` 声明的，没有初始值。值由外部输入或运行时绑定。但可以在工作流内使用 `QUOTEDCONSTANTID` 作为常量名？`constantName` 可以匹配 `QUOTEDCONSTANTID`，但 `QUOTEDCONSTANTID` 是带引号的字符串 token，如 `"approved"`；在 G4 中，它可以作为常量名出现在 term 中，表示一个带有该字符串值的常量。但该常量的类型需要在 `constDecl` 中声明。因此我们可以先声明 `const "approved": Artifact;` 但这样不行，因为 `constDecl` 要求 `constantName` 作为标识符，而 `constantName` 可以包含 `QUOTEDCONSTANTID`。所以可以：`const "approved": Artifact;` 然后 `"approved"` 就是一个常量 Artifact？不太对，`"approved"` 是一个字面量，不是标识符。更典型的用法是 `const approved_token: Artifact;` 然后通过某种方式给它赋值，但 G4 没有赋值语句。通常常量是通过输入传入的。所以我们可能需要把判据作为外部输入。

但是需求没有明确禁止额外输入，只是说"接收 request"。我们可以合理增加 `approval_criterion` 作为另一个输入，让 judge_step 产出 flag 并与该 criterion 比较。或者我们可以让 judge_step 产出 `approved_flag` 直接作为一个布尔标记，然后用 `approved_flag = true_literal` 作为比较，其中 `true_literal` 是一个常量 Artifact 代表 true。但怎么定义 true_literal？我们可以通过外部输入传入。

考虑到简化，我们设计工作流接收两个输入：`request` 和 `trigger`（用于比较的判据）。但需求只说 receive request，所以可能只需要一个。不过为了可执行，我们可以把判据内嵌为一个常量字面量，例如使用 QUOTEDCONSTANTID："approved"。但需要声明对应的常量并赋予意义。或许我们可以定义 `const approval_marker: Artifact;` 然后在 workflow 内部使用 `"approved"` 作为 QUOTEDCONSTANTID 直接出现在比较中？G4 语法允许 `constantName` 包括 `QUOTEDCONSTANTID`，所以 `"approved"` 可以作为 term 出现。但它的概念类型呢？`"approved"` 作为一个常量，其类型应该是 Artifact（或者可以在上下文中推断）。在语法中，`if(formula, term, term)` 中的 formula 是 comparison，比较两边都是 term，term 可以是 atomicTerm 即 constantName。所以 `approved_flag = "approved"` 应该是有效的，只要 `"approved"` 被声明为适当的类型？实际上在 constDecl 中，我们可以声明 `const "approved": Artifact;` 吗？尝试：`const "approved": Artifact;` 但语法中 constDecl 是 `CONST constantName COLON conceptNameList`，constantName 可以是 QUOTEDCONSTANTID，所以理论上可以。但这样会定义一个名为 `"approved"` 的常量，类型是 Artifact。然后在比较中可以使用 `"approved"`。但这是否符合语义？在常规编程中，带引号的字符串字面量作为标识符很奇怪，但在 G4 语法中允许。不过在实际运行时，这个常量的值需要由外部提供？由于没有初始值赋值，它可能依赖输入。如果我们不把它放在 input_workflow 中，它可能是未定义的。因此更稳妥：将判据作为输入传入。

综合来看，我打算设计工作流接收两个输入：`request` 和 `approval_criterion`。`judge_step` 消费 request，产出 `approved_flag`。`approve_handler` 消费 request，产出 `approved_artifact`。`reject_handler` 消费 request，产出 `rejected_artifact`。然后使用 if 表达式基于 `approved_flag = approval_criterion` 选择其中一个作为最终输出。

需求明确要求"不要再增加只负责转发的最终 Step"。所以我们不能有一个 Step 来包装输出。因此 `output_workflow` 必须直接是一个 if 表达式？但是 `output_workflow(workflow) == [artifact]` 要求 artifact 是 term，而 term 可以是 ifExpression。所以我们可以写 `output_workflow(workflow_name) == [if(approved_flag = approval_criterion, approved_artifact, rejected_artifact)];`。这是允许的，因为 `term` 可以包含 `ifExpression`。

我们需要确保 `approved_flag`、`approval_criterion`、`approved_artifact`、`rejected_artifact` 都是已声明的 Artifact。

另外，注意 `ifExpression` 需要用括号括起来。

现在我们需要为每个 Step 声明 StepName、Instruction、Executor 等。我们需要定义 `judge_name`, `approve_name`, `reject_name` 等 StepName 常量；Instruction 常量；Executor 常量（Agent 类型）。还需要声明 `judge`, `approve`, `reject` 三个 Step 常量。

按照要求，补全所有声明。

最终工作流结构：

- 输入：[request, approval_criterion]
- judge_step: 消费 request，产出 approved_flag
- approve_handler: 消费 request，产出 approved_artifact
- reject_handler: 消费 request，产出 rejected_artifact
- 输出：[ if(approved_flag = approval_criterion, approved_artifact, rejected_artifact) ]

注意：需求说 approver_handler 和 reject_handler 都消费 request 且每次执行都实际运行。这里没有条件跳过，所以它们都会运行。

我们需要为每个 Step 绑定 StepName 和 Instruction。Instruction 使用 Instruction 类型，我们可以定义 `const judge_instruction: Instruction;` 等，但 Instruction 是一个概念，我们需要在工作流内部通过 step_instruction 赋值。但 step_instruction 需要 Instruction 类型，而 Instruction 是一个概念，在 G4 中概念是预定义的，没有赋值语法。所以我们只能声明 Instruction 类型的常量，并在 step_instruction 中绑定。但 Instruction 的值在哪里？实际运行时可能需要由外部提供？或者我们可以使用 QUOTEDCONSTANTID 作为指令字符串，但指令是 `Instruction` 概念，不是一个普通的字符串。也许我们可以通过 `const judge_instruction: Instruction;` 声明，然后在 step_instruction 中使用它，但其值需要从输入注入？或者可以使用 `.prose` 风格的字符串？但 G4 中没有字符串赋值。实际上在 FusionFlow 中，Instruction 是一个概念，它的值通常通过外部工作流输入提供，类似于 Artifact。因此我们可以把 Instruction 作为输入？但输入已经定义为 Artifact 列表，不能是混合类型。看来对于 Instruction，我们可能不需要显式定义其内容，因为工作流是声明性的，实际指令由执行器（如 Agent）的配置提供。但按语法，`step_instruction(step) == ???` 需要一个 Instruction 类型的 term。我们可以声明 `const judge_instr: Instruction;` 然后 `step_instruction(judge_step) == judge_instr;`。但是 judge_instr 没有值，除非它被作为输入传入？但 `input_workflow` 只能声明 Artifact, List 等。Instruction 不是 Artifact。所以可能 Instruction 是预定义的概念，不需要声明值，执行器会自己知道？但为了语法正确，我们还是需要声明常量。不过在实际 run 中，可能需要通过 name 映射具体指令。但为了满足"完整可编译"，我们按照语法规则声明即可。

类似地，StepName 也是概念，需要声明常量。

Executors 我们需要 Agent 类型，声明 `const judge_agent: Agent, Executor;` 等。

另外，`agent_config` 需要 Model, Engine, ApiBase 等。我们需要声明这些概念常量。可以声明 `const default_model: Model;` 等。然后使用 `agent_config(judge_agent, default_model, default_engine, default_api);`。

还需要注意 `max_attempts`、`step_timeout` 等可选，但最好包含。

现在写代码。

注意：需要在 workflow 块内部的 DATA FLOW 区域使用 `consumes`、`produces`、`input_workflow`、`output_workflow`。在 EXECUTOR ASSIGNMENT 使用 `step_executor`，等等。

为了满足"可编译"，我们不能有语法错误。按照 G4 语法。

让我们写出完整代码。

我选择如下定义：

```fusionflow
-- SCENARIO: 接收请求，根据判定标记选择处理路径，两个处理分支都执行
-- AUTHORED: 2025-04-06 from intent: "判定工作流，两个处理分支均实际运行"

const request: Artifact;
const approval_criterion: Artifact;
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

const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;
const high_effort: ReasoningEffort;

workflow decision_flow {
  -- DATA FLOW
  input_workflow(decision_flow) == [request, approval_criterion];
  consumes(judge_step) == [request];
  produces(judge_step) == [approved_flag];
  consumes(approve_handler) == [request];
  produces(approve_handler) == [approved_artifact];
  consumes(reject_handler) == [request];
  produces(reject_handler) == [rejected_artifact];
  output_workflow(decision_flow) == [if(approved_flag = approval_criterion, approved_artifact, rejected_artifact)];

  -- EXECUTOR ASSIGNMENT
  step_executor(judge_step) == judge_agent;
  step_executor(approve_handler) == approve_agent;
  step_executor(reject_handler) == reject_agent;

  -- STEP CONFIGURATION
  step_name(judge_step) == judge_name;
  step_instruction(judge_step) == judge_instruction;
  step_timeout(judge_step) == 300;
  max_attempts(judge_step) == 2;

  step_name(approve_handler) == approve_name;
  step_instruction(approve_handler) == approve_instruction;
  step_timeout(approve_handler) == 300;
  max_attempts(approve_handler) == 2;

  step_name(reject_handler) == reject_name;
  step_instruction(reject_handler) == reject_instruction;
  step_timeout(reject_handler) == 300;
  max_attempts(reject_handler) == 2;

  -- WORKFLOW CONFIGURATION
  max_concurrency(decision_flow) == 3;
  workflow_timeout(decision_flow) == 900;

  -- AGENT CONFIGURATION
  agent_config(judge_agent, default_model, default_engine, default_api);
  agent_config(approve_agent, default_model, default_engine, default_api);
  agent_config(reject_agent, default_model, default_engine, default_api);

  allowed_tool(judge_agent, read_tool);
  allowed_tool(approve_agent, read_tool);
  allowed_tool(reject_agent, read_tool);
  reasoning_effort(judge_agent) == high_effort;
}
```

注意：我们使用了 `read_tool` 但未声明。需要声明一个 Tool 常量。可以添加 `const read_tool: Tool;` 在顶部。

还需要注意 `agent_config` 需要 ApiBase 类型，我们声明了 `default_api` 但未声明；需要声明 `const default_api: ApiBase;`。

另外，`high_effort` 作为 ReasoningEffort，我们声明了但未使用具体值，可以。

`step_timeout` 需要 Integer 类型，我们给的是数字字面量，OK。

`max_attempts` 也是 Integer。

`max_concurrency` 和 `workflow_timeout` 也是 Integer。

现在检查：`agent_config` 返回 Bool，所以作为 standalone 调用是允许的（等价于 `agent_config(...) == True`）。

`allowed_tool` 也需要 Tool，我们声明了 `read_tool`。

另外，Instruction 常量只是名字，没有具体值，但语法上允许。

但要注意：`step_instruction(judge_step) == judge_instruction;` 中右边的 `judge_instruction` 是一个 Instruction 类型的常量，它没有绑定具体的字符串。在实际运行时，可能需要通过外部配置提供指令。但语法上这是有效的。

我们还需要在 `agent_config` 中使用 `default_model`, `default_engine`, `default_api` 这些常量，它们需要被声明。我们在顶部添加声明。

另外，在 `agent_config` 中，我们还需要确保这些类型正确。

最终代码应该包括所有必需声明。

根据要求，只返回一个 fusionflow 代码块。不要解释。

我准备输出。