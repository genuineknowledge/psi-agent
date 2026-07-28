我们被要求编写一个完整、可编译、有真实可执行语义的FusionFlow G4工作流。需求如下：

- 接收一个 request
- 一个分类 Step 产出 category
- 两个处理 Step 都消费 request，分别产出 primary_result 和 fallback_result
- 最后一个消费 Step 根据 category 是否等于 primary_category，只消费被选择的处理结果并产出 final_result
- 两个处理 Step 必须在每次执行中都实际运行，选择只决定传给最终 Step 的值

需要补全所需的常量声明、数据流、执行器绑定、StepName 和 Instruction 配置。

注意：根据技能文档，必须使用 `if(formula, then_term, else_term)` 来实现选择。且所有Step都会运行，但最终Step的consumes列表中使用if来选择一个artifact。

我们需要声明：
- request: Artifact
- category: Artifact (分类Step产出)
- primary_result, fallback_result: Artifact
- final_result: Artifact
- 可能还需要 primary_category 作为一个常量？但需求说“category 是否等于 primary_category”，primary_category 应该是一个输入参数或常量。我们可以声明一个 named input，比如 primary_category 作为一个Artifact，作为工作流的输入之一。但需求说“接收一份 request”，可能还隐含一个 category 阈值。为了简单，我们可以将 primary_category 声明为一个输入 Artifact。

然后 Step:
- classification_step: 消费 request，产出 category
- primary_handler_step: 消费 request，产出 primary_result
- fallback_handler_step: 消费 request，产出 fallback_result
- final_step: 根据条件消费 primary_result 或 fallback_result，产出 final_result

数据流：
- input_workflow(workflow) == [request, primary_category]  (假设我们也输入primary_category)
- produces(classification_step) == [category];
- produces(primary_handler_step) == [primary_result];
- produces(fallback_handler_step) == [fallback_result];
- consumes(final_step) == [ if( category = primary_category, primary_result, fallback_result ) ];
- produces(final_step) == [final_result];
- output_workflow(workflow) == [final_result];

执行器绑定：每个Step需要一个Executor，我们可定义4个Agent。

还需要 StepName 和 Instruction 常量。

注意：根据技能规则，对非技术用户不显示框架术语，但这里是写代码，必须按规范。

另外，需求说“两个处理 Step 必须在每次执行中都实际运行”，这由数据流保证：它们都消费 request，而分类Step和它们没有依赖关系，所以它们会执行。选择是在final_step的consumes中通过if表达式进行。

需要注意：`if` 是用于 term 选择的，其条件是一个 formula，在 `if` 中我们不能直接使用 `==`，因为 `==` 是顶层断言的操作符。在 formula 中我们使用 `=`（数值相等）进行比较。所以是：`if(category = primary_category, primary_result, fallback_result)`。

另外，`consumes` 返回 List，所以 if 表达式必须是一个 term，而 term 可以是 ifExpression，返回一个 Artifact，所以可放在 List 中`[ if(...) ]`。

我们还需考虑是否要声明 `primary_category` 为常量或工作流输入。由于需求明确说“根据 category 是否等于 primary_category”，所以 primary_category 应该是一个值。可以将它作为工作流输入之一，或者声明为常量。我选择作为工作流输入，这样更通用。

所以 input_workflow 应包括 request 和 primary_category。

完整的流程：

```
-- SCENARIO: 分类路由工作流
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
const handler_a_name: StepName;
const handler_b_name: StepName;
const final_name: StepName;

const classify_instruction: Instruction;
const handler_a_instruction: Instruction;
const handler_b_instruction: Instruction;
const final_instruction: Instruction;

const classify_agent: Agent, Executor;
const handler_a_agent: Agent, Executor;
const handler_b_agent: Agent, Executor;
const final_agent: Agent, Executor;

// 可能还需要 model, engine, api 等常量用于 agent_config
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
  step_executor(primary_handler_step) == handler_a_agent;
  step_executor(fallback_handler_step) == handler_b_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(classify_step) == classify_name;
  step_instruction(classify_step) == classify_instruction;
  step_name(primary_handler_step) == handler_a_name;
  step_instruction(primary_handler_step) == handler_a_instruction;
  step_name(fallback_handler_step) == handler_b_name;
  step_instruction(fallback_handler_step) == handler_b_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- AGENT CONFIGURATION
  agent_config(classify_agent, my_model, my_engine, my_api);
  agent_config(handler_a_agent, my_model, my_engine, my_api);
  agent_config(handler_b_agent, my_model, my_engine, my_api);
  agent_config(final_agent, my_model, my_engine, my_api);
}
```

但需要确保常量声明正确，没有多余的部分。另外，我们还需要为 Step 配置具体的 value，但是在这里我们不需要提供实际的字符串值，只需要声明常量名即可，因为这是一个源文件，具体的指令内容可以在运行时通过外部输入提供（或者用户可以修改）。根据技能，常量声明后需要在其它的地方赋予实际值吗？不，这里只是声明常量名并建立关系，具体的值（比如 step_instruction 的具体字符串）可以在运行时通过输入或环境变量提供。但为了完整性，我们通常还需要为每个 Instruction 赋予一个实际值。但是 Grammar 中，常量只能是 number、relative path id、quoted constant id 或 lowid。Instruction 是概念类型，我们声明的常量可以是 quoted constant id（限定字符串）或 relative path id。但通常 Instruction 的具体内容是需要在运行时提供的。我们可以用 quoted constant id 来表示一个提示文本，例如 `"classify the request"`。但引用中说了：restricted quoted ID，不能包含空格？实际上 grammar 中允许的字符是 `[A-Za-z0-9.!#$%?@_{|}~`]`，没有空格。所以不能直接写自然语言指令。通常的做法是使用相对路径指向一个文件，如 `"./instructions/classify.md"`。或者，我们可以省略指令的具体内容，因为用户说“请补全所需的常量声明、数据流、执行器绑定、StepName 和 Instruction 配置”，可能只需要声明常量名，不要求给出具体内容。所以保持 declare 即可。

但是要注意：在 step_instruction 断言中，右侧必须是一个类型为 Instruction 的常量。我们声明了 `classify_instruction: Instruction`，这只是一个名字，没有具体值。这在语法上是合法的（因为常量不需要赋值），但运行时需要为这些常量提供值。不过，按照 FusionFlow 的语义，这些常量最终需要通过外部输入映射到具体值。由于是代码示例，保持这样即可。

另外，需要确保 step_name 也类似：声明了 classify_name: StepName，但没给出具体值。这可以，但实际运行时可能需要绑定。用户可能期望使用具体字符串。但这里我们可以用 quoted constant id 表示 name，比如 `"classify"`，但这个不能包含空格？name 通常可以没有空格。我们使用 `"classify"`。

但 Instruction 通常需要较长的文本。如果我们想提供具体内容，最好用相对路径文件。但为简化，我们可以在声明时直接赋予一个quoted constant id 作为指令描述，但这个字符串不能有空格，所以可能不实用。我选择保留为纯声明，不赋值。

但是技能中提到：“emmit every explicitly requested relation. Every operand must be a declared grammar term: _ and ... are not wildcards. Declare typed constants for required operands, or omit an optional configuration instead of inserting placeholders.” 所以我们必须为每个断言中涉及的term声明常量。我们的断言中已经使用了所有声明的常量，没有多余。对于 step_name 和 step_instruction，我们声明了常量名，但在断言中直接使用这些常量名。这没问题。

还一个问题：agent_config 需要 Agent, Model, Engine, ApiBase 四个参数。我们声明了 classify_agent 等是 Agent, Executor，但 Model, Engine, ApiBase 也需要声明。我们添加：

```
const my_model: Model;
const my_engine: Engine;
const my_api: ApiBase;
```

在 agent_config 中使用。

另外，allowed_tool 等可选，我们可以省略。

现在写出完整文件。确保注释行符合格式。

最终输出只返回一个标记为 fusionflow 的代码块。

我开始写。