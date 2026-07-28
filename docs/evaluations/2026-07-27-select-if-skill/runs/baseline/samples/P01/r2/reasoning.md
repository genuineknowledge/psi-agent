我们被要求编写一个完整的、可编译的、具有真实可执行语义的FusionFlow G4工作流。需求描述：

- 接收一份 request。
- 一个分类 Step 产出 category。
- 两个处理 Step 都消费 request，并分别产出 primary_result 和 fallback_result。
- 最后一个消费 Step 根据 category 是否等于 primary_category，只消费被选择的处理结果并产出 final_result。
- 两个处理 Step 必须在每次执行中都实际运行，选择只决定传给最终 Step 的值。

我们需要用 FusionFlow G4 语法表达这一点。关键是 final_step 的 consumes 需要使用 if 表达式来根据 category 选择 primary_result 或 fallback_result。注意 category 和 primary_category 的比较是公式的一部分。

在语法中，consumes 返回一个 List，所以 if 表达式需要作为一个 List 内的元素，即 `consumes(final_step) == [if(formula, primary_result, fallback_result)];`。

formula 是一个比较：`category = primary_category`。注意 `=` 用于比较，`==` 用于顶层的 equality。category 和 primary_category 都是 Artifact 或其他概念？根据描述，category 是由分类 Step 产出的 Artifact。primary_category 是一个常量（可能是预定义的 Artifact，或者是输入？）但从需求看，它可能是一个输入值或者常量。我们可以将其声明为输入 Artifact，或者直接作为常量（Artifact 类型的常量）。通常我们把它作为输入，以便外部提供。

另外，request 是输入 Artifact。

所有 Step 都需要执行器、StepName、Instruction 等。我们需要声明常量。

注意：语法要求 workfile 包含全局 identity declarations (constDecl) 然后 workflow。每个 workflow 内部是 assertion。

我们需要创建一个完整的 workflow。

参考之前的例子，我们需要声明：

- request: Artifact
- category: Artifact
- primary_result: Artifact
- fallback_result: Artifact
- final_result: Artifact
- primary_category: Artifact（输入）
- classify_step: Step
- process_primary_step: Step
- process_fallback_step: Step
- final_step: Step
- 对应的 StepName, Instruction, Executor（Agent）

我们还需要声明 Executor 等。可以使用预设的 Agent 概念。

按照规范，我们需要按顺序分组：DATA FLOW, EXECUTOR ASSIGNMENT, STEP CONFIGURATION, WORKFLOW CONFIGURATION, AGENT CONFIGURATION。

数据流：
- input_workflow(workflow) == [request, primary_category];
- consumes(classify_step) == [request];
- produces(classify_step) == [category];
- consumes(process_primary_step) == [request];
- produces(process_primary_step) == [primary_result];
- consumes(process_fallback_step) == [request];
- produces(process_fallback_step) == [fallback_result];
- consumes(final_step) == [if(category = primary_category, primary_result, fallback_result)];
- produces(final_step) == [final_result];
- output_workflow(workflow) == [final_result];

注意：if 表达式中的 formula 是 `category = primary_category`。

另外，两个处理 Step 都必须实际运行，选择只决定传给最终 Step 的值——我们的模型自然保证它们都会运行，因为它们都断言了 produces，并且 not 条件式不会阻止它们运行。

其他配置：需要给每个 step 分配 executor、name、instruction。可以假设使用相同的 Agent 或不同的 Agent。我们至少声明一个 Agent 用于每个 step，也可以复用同一个。

还需要声明 Agent 的 agent_config 等，按照模板。

考虑使用类似之前的 fan-out/fan-in 模型。

完整代码块以 fusionflow 标记。

注意语法：`step_timeout` 和 `max_attempts` 可选，可以不配置，但为了完整性可以配置或省略。为了避免复杂，只配置必要的。

我们可以在 step configuration 中设置必要的 name 和 instruction，executor 绑定。

instruction 需要是 Instruction 类型的常量，可以用 QUOTEDCONSTANTID 来写具体指令？但最好是用常量名，然后在外部提供。这里我们可以声明为 Instruction 常量并赋予一个名字，但实际字符串值不在源码中提供（可以通过外部注入）。我们只需声明即可。

我们使用默认的 executor，比如都用一个 agent "worker" 但不是同一个？可以每个 step 用不同的 agent，也可以相同。我们分别声明 classify_agent, process_primary_agent, process_fallback_agent, final_agent。也可以复用，但为了清晰，分开。

然后 agent_config 需要提供 Model, Engine, ApiBase。我们可以声明一些简单的模型常量（如 review_model）。为了最小化，我们可以参照模板声明 review_model, review_engine, review_api, high_effort 等，但这里的需求没有指定，我们可以使用通用名称。不过为了编译通过，需要这些概念存在于 catalog 中。我们的源代码需要引用它们，但 catalog 是在运行时提供的。我们按照 skill 中的例子来：声明 `review_model: Model;` 等，然后 agent_config 使用它们。

在示例中，有 `agent_config(security_agent, review_model, review_engine, review_api);`。所以我们也要类似。

由于我们只有 4 个 step，可以声明一组公共的 model/engine/api，并且每个 agent 可以使用相同的配置。

在 workflow 配置方面，可以设置 max_concurrency 等，但可选。为了简化，我们只设置必要的数据流。

另外，注意语法：workflow time 等可选。

现在编写完整的源码。确保：

- 按语法要求，全局声明常量，然后一个 workflow 定义。
- 所有引用的 name 都已声明。
- 使用正确的 == 和 = 。
- 注释用 --。

确认常量名称大小写：conceptName 是 UPID (大写开头)，所以 Artifact, Step, StepName, Instruction, Agent, Executor, Model, Engine, ApiBase, ReasoningEffort, Tool 等是 Concept。LOWID 用于常量名和 operatorName。

所有常量名用小写字母开头，符合规则。

编写代码：

```
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

注意：我们添加了 ReasoningEffort 和 Tool 的声明，但也可以省略。为了符合典型模式，我们保留。

检查是否有遗漏：必须确保每个 operator 都使用正确的 arity。agent_config 是 arity 4，我们用了。allowed_tool arity 2，reasoning_effort arity 1。都是正确的。

注意：max_concurrency 可选，如果设置可以防止并行，但这里似乎不需要限制。我们保持默认。

另外，我们需要确保所有常量都是 lowercase 开头：classify_name, process_primary_name, process_fallback_name, final_name 等。

现在确认语法：if 表达式在 consumes 内部，需要是 `[if(formula, primary_result, fallback_result)]`。注意 formula 用 `category = primary_category`（使用 =）。正确。

整个 workflow 文件应该是一个单一文件。

由于要求只返回一个标记为 fusionflow 的代码块，不要解释，所以直接输出代码块。