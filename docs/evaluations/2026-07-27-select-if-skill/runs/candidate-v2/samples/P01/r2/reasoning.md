我们被要求编写一个完整、可编译并具有真实可执行语义的 FusionFlow G4 工作流。用户描述：工作流接收一份 request；一个分类 Step 产出 category；两个处理 Step 都消费 request，并分别产出 primary_result 和 fallback_result；最后一个消费 Step 根据 category 是否等于 primary_category，只消费被选择的处理结果并产出 final_result。两个处理 Step 必须在每次执行中都实际运行，选择只决定传给最终 Step 的值。

这实际上是一个“命名 Artifact 选择”模式：分类步骤产出 category，两个处理步骤都运行并各自产出结果，然后通过 if 表达式将选中的结果赋值给一个 Artifact，最后最终步骤消费该 Artifact。

需要补全常量声明、数据流、执行器绑定、StepName 和 Instruction 配置。只返回一个标记为 fusionflow 的代码块。

我必须确保所有声明：const 声明（包括 Artifact, Step, StepName, Instruction, Agent, Executor, 以及可能的 Model, Engine, ApiBase, Tool, ReasoningEffort 等）。还需要 workflow 语句。

根据语法，我需要定义常量，然后一个 workflow 块。

用户没有提供具体的 category 值，所以我们可以使用一个常量 primary_category 作为输入，或者我们假设 category 是由分类步骤产出的 Artifact，并且 primary_category 是一个输入常量（可以是工作流输入的一部分，或者是一个固定的常量）。对于“根据 category 是否等于 primary_category”，通常 primary_category 可能是工作流的一个输入参数（外部提供的常量），或者是一个固定值。我假设它是工作流输入之一（一个 Artifact）。

那么输入：request, primary_category （或者我们可以将 primary_category 定义为常量？但用户说要“接收一份 request”，并未提及其他输入。但分类步骤需要知道 how to classify? 可能分类步骤会产生 category，然后与某个标准比较。我们可以让 primary_category 作为工作流输入之一。

更好的方式：primary_category 是一个常量字符串（如 "primary"），我们可以将其定义为 const primary_category_name: Artifact; 并作为工作流输入。或者我们可以在 workflow 内部定义一个常量（const primary_category: Artifact = "primary"? 但语法中 const 后跟常量和概念名列表，没有赋值。所以常量需要在输入中提供。或者我们可以通过指令让分类步骤知道目标类别。

通常，这种模式：工作流接收 request 和 primary_category（或标准）。分类步骤分析 request 并产出 category（一个 Artifact）。然后根据是否匹配 primary_category，选择 primary_result 或 fallback_result。

但是根据语法，if 表达式中比较的是 Artifact 之间的值？不，比较是公式中的 comparison，比较 term。term 可以是 constantName （包括 NUMBER, RELATIVE_PATH_ID, QUOTEDCONSTANTID, LOWID）。所以我们可以将 category 与 primary_category 进行比较。这两个都是 Artifact 类型的常量？但是 Artifact 是概念名，而 constantName 是标识符。在比较中，我们使用 =（等号比较）。所以我们需要声明 category 和 primary_category 作为 Artifact 常量，然后比较它们。

注意：在 if 公式中，比较的是 term，而 term 可以是 constantName。所以 category 和 primary_category 都是声明为 Artifact 的常量，但是比较它们是否相等？语法允许 constantName 出现在比较中，因为 constantName 是 atomicTerm。但是我们需要确保它们在同一类型上可比较。通常，Artifact 是工作流中的数据载体，比较它们可能没有语义？实际上，这个框架允许比较 Artifact 常量（即它们的值）吗？根据 `Named Artifact selection` 示例，它使用 `(review_observation = review_criterion)`，其中 review_observation 和 review_criterion 都是 Artifact。所以是的，可以比较 Artifact 的值。

因此我们需要声明：request, category, primary_category, primary_result, fallback_result, selected_result, final_result 作为 Artifact。

还要声明：classify_step, process_primary_step, process_fallback_step, final_step 作为 Step。

声明 StepName, Instruction, Agent, Executor 等。

按照模板顺序：DATA FLOW, EXECUTOR ASSIGNMENT, STEP CONFIGURATION, WORKFLOW CONFIGURATION, AGENT CONFIGURATION。

由于我们没有实际模型/引擎/API 的细节，但为了可编译，我需要提供具体的常量名。我可以使用通用的 agent_config 和 allowed_tool，但也可以省略 AGENT CONFIGURATION 部分？但是声明了 Agent 和 Executor 但未配置 agent_config 可能是不完整的？不过对于可执行语义，可能需要在某个地方配置 Agent。但用户只要求“补全所需的常量声明、数据流、执行器绑定、StepName 和 Instruction 配置”，所以不需要提供完整的 agent_config？但为了可编译，最好包括一个基本的 agent_config。我可以使用一个通用模型和引擎。注意：在示例中，他们声明了 review_model, review_engine, review_api 等。但为了简化，我们可以省略 agent_config 如果你愿意，但 skill 说“保留右值对于每个非 Bool 操作符”，而 agent_config 返回 Bool，所以可以省略？agent_config 是 Bool 返回，可以不用显式 == True，直接在 workflow 中写为 operatorCall，但 operatorCall 是 assertion 的一种（当 operatorCall 作为 assertion 时，如果返回 Bool，则隐式等于 True）。所以我们可以只写 `agent_config(worker, model, engine, api);` 而不显式断言。但我们需要先声明这些常量吗？是的，它们必须被声明。

我注意到，在一些示例中，agent_config 被直接用作 assertion（如 `agent_config(agent, model, engine, api);`）。在语法中，operatorCall 可以是 assertion 项（因为 assertion 可以只是 operatorCall）。所以这是合法的。

所以我会声明常量：包括 agent, step, artifact, model, engine, api, tool, reasoning_effort 等。

为了保持简洁，我假设使用一个通用的 “claude” 引擎和模型。但常量名必须符合语法：LOWID 或 UPPERID。通常，Agent, Model, Engine, ApiBase, Tool, ReasoningEffort 作为概念名（以大写开头），所以常量应该是 LOWID（小写标识符）或 NUMBER 或 QUOTEDCONSTANTID。在示例中，他们使用 review_model: Model; review_engine: Engine; review_api: ApiBase; high_effort: ReasoningEffort; read_tool: Tool; 这些都是小写标识符。

类似地，我可以定义：

const worker: Agent, Executor;
const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;
const default_tool: Tool;
const standard_effort: ReasoningEffort;

然后 agent_config(worker, default_model, default_engine, default_api); allowed_tool(worker, default_tool); reasoning_effort(worker) == standard_effort;

但为了简单，我可以跳过这些，但为了“可编译并具有真实可执行语义”，最好包括它们。但用户说“补全所需的常量声明、数据流、执行器绑定、StepName 和 Instruction 配置”，没有明确要求 agent_config。不过如果缺少 agent_config，运行时可能无法正确配置 Agent。但我认为可以，只要声明了 Agent, Executor 并绑定到 Step，运行时可能会使用默认配置？但 skill 要求我们声明所有必需的，所以我会包含一个最小配置。

注意：在 skill 的 template 中，他们只显示了 step_executor 等，没有显示 agent_config。但完整示例中有 agent_config。我选择包含它，以确保可编译。

另外，需要定义 step_name 和 step_instruction 的值。step_name 是 StepName 类型的常量，step_instruction 是 Instruction 类型的常量。通常，StepName 可以是一个字符串（QUOTEDCONSTANTID），Instruction 也可以是一个字符串或路径。我可以用一些有意义的字符串，例如 "classify", "process_primary", "process_fallback", "finalize"。

根据语法，QUOTEDCONSTANTID 是 " [A-Za-z0-9.!#$%?@_{|}~`]* "，所以我可以使用 "classify" 作为 StepName。

同样，Instruction 可以是类似的引号字符串，但 skill 说 Instruction 通常是一个路径或自然语言？在 g4 中，Instruction 是一个概念名，没有限制其内容。在示例中，他们使用 review_instruction: Instruction; 并且通过 step_instruction 绑定，但没有给出具体值。实际上，Instruction 的常量可以是 QUOTEDCONSTANTID，但 skill 说“Quoted constants are restricted IDs, not prose.” 但同时也允许说“Instruction path”即 "./..." 路径。为简单，我可以使用路径 "./instruction.txt" 或类似，但这样需要文件存在。或者我可以使用一个合理的 Instruction 字符串，由于没有指定环境，我假设可以使用一个类似 "你是一个分类助手，根据 request 内容输出一个类别标记。" 这样的自然语言字符串？但 skill 禁止将自然语言放在引号常量中，除非是 Instruction 路径。Instruction 是一个概念，其值可以是路径或字符串？语法上允许 QUOTEDCONSTANTID，但 skill 说“Quoted constants are restricted IDs, not prose. Use declared StepName/Instruction identities or a "./..." instruction path; never place natural-language instructions in quotes.” 这意味着我们不应该将自然语言指令直接作为引号字符串常量放在 G4 源码中。相反，我们应使用一个 Instruction 常量，然后可能在运行时由配置提供其内容。但为了“可编译”，我们可以声明一个 Instruction 常量但不对其赋值（？），但常量声明必须有一个名称，没有赋值。所以常量声明必须包含一个名称，但不能提供值。那么 step_instruction(step) == instruction_constant; 这需要 instruction_constant 是一个 Instruction 类型的常量。但它的值是什么？在运行时，可能从外部注入。或者我们可以在 source 中使用 `"./instruction.md"` 路径。这应该符合要求。的确，skill 说：“Quoted constants are restricted IDs or explicit workspace-relative paths, not free-form text.” 所以我们可以使用路径 `"./instruction.md"`。这样更合理。

所以对于 Instructions，我将使用 `"./classify.md"`, `"./process_primary.md"`, `"./process_fallback.md"`, `"./finalize.md"`作为 Instruction 常量。

但是这些文件需要存在，但在运行时会由运行时读取。用户没有要求提供文件，但为了工作流可执行，它们必须在运行时存在。不过我们只被要求编写 G4 源，所以我们可以这样写。

对于 StepName，也是类似，可以使用路径或字符串。但是 StepName 通常只是一个标识符，可以使用引号字符串 "classify" 等。因为 StepName 不是 Instruction，可以使用普通字符串。

为了可编译，我选择使用字符串常量：`"classify"`, `"process_primary"`, `"process_fallback"`, `"finalize"`。

现在，数据流：输入：request, primary_category。分类步骤消费 request，产出 category。两个处理步骤都消费 request，各自产出 primary_result 和 fallback_result。然后 if 表达式根据 category = primary_category 选择 primary_result 或 fallback_result，赋值给 selected_result。最终步骤消费 selected_result，产出 final_result。

输出：final_result。

注意：primary_category 是 Artifact 类型，但也可以是一个常量字符串？但为了比较，它必须是 Artifact 型。所以输入中也要包括它。

但是用户说“收到一份 request”，可能只有一个 request 输入。另外，分类步骤“产出 category”，所以 category 是由分类步骤产生的。而 primary_category 可能是一个已知的常量（例如“primary”），可能不需要是工作流输入，而是一个固定值。但是根据 skill 的 if 模式，比较的是两个 Artifact 的值。所以 primary_category 必须是 Artifact，要么作为输入传入，要么由某个步骤生产。通常，这种场景下 primary_category 是工作流预定义的标准，可以是一个输入。我选择将其作为工作流输入。

但用户没有指定，我可以用一个简单的常量，但语法要求常量必须声明为某种概念名。我可以将 primary_category 声明为 Artifact，并通过输入提供。或者，我可以只用 request 作为唯一输入，而将 primary_category 作为一个固定字符串常量（例如 "primary"），但如何将其作为 Artifact 常量？Artifact 常量不能直接赋值。所以它必须是输入。我来这样做。

声明：

const request: Artifact;
const primary_category: Artifact;
const category: Artifact;
const primary_result: Artifact;
const fallback_result: Artifact;
const selected_result: Artifact;
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

const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;
const default_tool: Tool;
const standard_effort: ReasoningEffort;

workflow conditional_processing {
  -- DATA FLOW
  input_workflow(conditional_processing) == [request, primary_category];
  consumes(classify_step) == [request];
  produces(classify_step) == [category];
  consumes(process_primary_step) == [request];
  produces(process_primary_step) == [primary_result];
  consumes(process_fallback_step) == [request];
  produces(process_fallback_step) == [fallback_result];
  selected_result == if(category = primary_category, primary_result, fallback_result);
  consumes(final_step) == [selected_result];
  produces(final_step) == [final_result];
  output_workflow(conditional_processing) == [final_result];

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

  -- WORKFLOW CONFIGURATION
  max_concurrency(conditional_processing) == 3;
  workflow_timeout(conditional_processing) == 600;

  -- AGENT CONFIGURATION
  agent_config(classify_agent, default_model, default_engine, default_api);
  allowed_tool(classify_agent, default_tool);
  reasoning_effort(classify_agent) == standard_effort;

  agent_config(process_primary_agent, default_model, default_engine, default_api);
  allowed_tool(process_primary_agent, default_tool);
  reasoning_effort(process_primary_agent) == standard_effort;

  agent_config(process_fallback_agent, default_model, default_engine, default_api);
  allowed_tool(process_fallback_agent, default_tool);
  reasoning_effort(process_fallback_agent) == standard_effort;

  agent_config(final_agent, default_model, default_engine, default_api);
  allowed_tool(final_agent, default_tool);
  reasoning_effort(final_agent) == standard_effort;
}

注意：agent_config 返回 Bool，这里我们只写 operatorCall，没有显式 ==。在语法中，assertion 可以只是 operatorCall，但需要确保它在 workflow 块内是有效的。是的。

另外，max_concurrency 和 workflow_timeout 是可选配置，但加上更完整。

现在，我们需要声明常量 classify_name 等，但它们还没有值。在 step_name 断言中，我们需要一个常量标识符。通常，workflow 外部的 const 声明只是声明常量名字和类型，不赋值。然后 step_name 断言将常量绑定在一起。所以这里 classify_name 是 StepName 类型的常量，然后在 step_name(classify_step) == classify_name; 中，将 classify_step 的 name 属性设置为 classify_name 常量。但 classify_name 本身没有值，运行时需要从外部提供值？这可能会引起问题。在典型的使用中，常量可以是一个字符串文字（在 G4 中，constantName 包括 QUOTEDCONSTANTID），所以我们可以直接将字符串作为常量常量。但语法要求 const 声明中 constantName: conceptNameList，然后后面可以通过断言引用。但常量不能同时声明和赋值。所以唯一能提供具体值的方式是使用一个直接包含字符串文字的常量声明？不，const 声明只是声明一个名字，没有值。

我注意到在示例中，他们使用 `const review_instruction: Instruction;` 而没有值。然后断言 `step_instruction(security_review) == security_instruction;` 其中 security_instruction 是 Instruction 类型的常量。但没有为 security_instruction 赋值。这说明这些常量的实际值由运行时解析，可能通过命名约定或外部配置。但在示例中，稍后使用了 `const security_instruction: Instruction;` 并没有给出值。在完整示例的末尾，他们没有设置指令的内容。所以 G4 源不需要提供指令内容，指令内容由运行时引擎在外部提供（例如通过 prompt 文件或环境变量）。所以我们的源也可以这样。

因此，只声明常量名称，不赋值，是可以的。

但是，在可执行时，运行时需要能够解析这些常量的实际值。用户没有提供额外的文件，但我们的任务是编写 G4 源，而不是提供所有支持文件。所以这样是合法的。

现在，关于 selected_result 的 if 表达式：在 DATA FLOW 部分，我直接使用了 `selected_result == if(category = primary_category, primary_result, fallback_result);` 这是允许的，因为 selected_result 是 Artifact 常量，而 if 表达式返回一个 term（应该是 Artifact）。根据语法，ifExpression 是 term 的一种，而 assertion 是 `term ASSERT_EQ term`，所以可以。

但是，这里有一个潜在问题：在语法中，assertion 可以是 `term ASSERT_EQ term`，所以 `selected_result == if(category = primary_category, primary_result, fallback_result);` 是合法的。注意：`=` 是数值相等比较操作符，在 comparison 中使用。所以在 if 条件中使用了 `category = primary_category`，这是 comparison，属于 formula。

现在，关于 executor 绑定：我使用了四个不同的 agent 常量。这很好。

现在，检查是否遗漏了什么：input_workflow 和 output_workflow 需要使用 workflow name。我用了 workflow_name 作为 `conditional_processing`。

现在，确保所有常量都被声明了。我声明了许多常量，包括 model, engine, api, tool, effort。可能还需要声明其他？如 `resource_requirement` 非必需。Agent config 需要四个参数：Agent, Model, Engine, ApiBase。都有了。

现在，attention：step_name(step) == StepName; 但 StepName 是一个概念名，在断言中，我们使用常量名称。所以所有引用的常量必须在 const 声明中。

现在，确保没有语法错误：常量名称都是小写标识符，符合。workflow 名称也是小写标识符。if 表达式在 workflow 块内部，合法。

现在，考虑注释：可以使用 -- 注释。

我将在代码块中编写完整的 G4 源。

由于用户说“只返回一个标记为 fusionflow 的代码块”，所以我只输出一个代码块。

让我写出来。