我们被要求编写一个完整的、可编译且具有真实可执行语义的 FusionFlow G4 工作流。需求是：

- 输入：一个 request 工件（Artifact）。
- 分类 Step：产出 category（也是一个 Artifact）。
- 两个处理 Step：都消费 request，分别产出 primary_result 和 fallback_result。
- 最后一个消费 Step：根据 category 是否等于 primary_category，选择消费 primary_result 或 fallback_result，产出 final_result。
- 两个处理 Step 必须始终运行（即 eager 执行，选择只决定传给最后一步的值）。

我们需要补全所有常量声明、数据流、执行器绑定、StepName 和 Instruction 配置。

注意：我们必须使用 FusionFlow G4 语法，从给定的语法规则和参考示例中提取。我们需要使用 `if` 表达式来做 named artifact selection。

语法中：`selected_artifact == if(condition, artifact_a, artifact_b);` 然后在数据流中使用 `selected_artifact`。

注意：category 是一个 artifact，primary_category 是一个常量（可能是字符串/已声明的标识符？），但我们需要比较 category 和 primary_category。category 是 Artifact，primary_category 应该是一个常量？但语法中 constantName 可以是 LOWID, NUMBER, QUOTEDCONSTANTID, RELATIVE_PATH_ID。所以我们可以声明一个常量 `primary_category` 类型为 `Artifact`？但 `primary_category` 应该是分类的一个可能值。在 `if` 条件中，比较的是两个 term，它们可以是 atomicTerm 即 constantName。所以我们可以将 `primary_category` 声明为一个常量（LOWID），但类型呢？可以是 `Artifact` 或 `CategoryLabel`？但我们只有 Artifact 类型可用。实际上，在 if 条件中，我们可以比较两个 Artifact (category 和 primary_category) 的值，使用 `=` 运算符。所以 primary_category 可以声明为 `Artifact`。

但问题是 `primary_category` 是一个固定的字符串常量，代表“primary”类别。在 FusionFlow 中，常量可以是一个 quoted constant ID 或 lowID。我们可以使用带引号的常量如 `"primary"` 作为 `primary_category` 的值。但是 `primary_category` 本身是一个常量名称，我们需要定义它的类型。按照示例，常量声明形如：`const primary_category: Artifact;` 然后给它赋值？实际上在常量声明中，没有赋值部分；常量只是声明了一个标识符并关联概念。常量值必须在 workflow 内部通过断言来赋值？不，语法中 constDecl 只是标识符加上类型列表，没有值。在 workflow 中，我们可以通过 `primary_category == "primary";` 这样的断言来赋值？但是语法中的断言是 `assertion: term ASSERT_EQ term | operatorCall`。我们可以写 `primary_category == "primary";`，但 `"primary"` 是一个 QUOTEDCONSTANTID，它是一个 term。所以可以。

但更好的做法是：我们将 `primary_category` 声明为 `Artifact`，然后在 workflow 中设置它的值：`primary_category == "primary";`。但 `"primary"` 在语法中是一个 QUOTEDCONSTANTID，但它是一个常量名称（constantName），在 term 中可以是 constantName，而 constantName 包括 NUMBER, RELATIVE_PATH_ID, QUOTEDCONSTANTID, LOWID。所以 `"primary"` 是合法的 term 作为 constantName。所以 OK。

或者，我们可以将 `primary_category` 作为 workflow 的输入？不是必须。但是要求是“一个分类 Step 产出 category”，所以 category 由 Step 产出，primary_category 是一个固定的比较目标，不需要是输入。所以我们可以将其作为常量设置。

但是注意：在 if 条件中，比较的是 `category = primary_category`，而 `category` 是 Artifact term，`primary_category` 是 constantName term。所以我们需要确保 `primary_category` 的值被设置。但如果我们只声明 `const primary_category: Artifact;` 而没有给值，那么它的值未定义。所以我们需要在 workflow 中给它赋值。

另一种方法：直接用 quoted string 字面量比较，如 `category = "primary"`，但 if 条件中要求 term，而 `"primary"` 是 constantName term，所以我们可以写 `category = "primary"` 而不需要声明 primary_category。但为了清晰，我们可以声明一个常量名 primary_category 并赋值为 "primary"。

在参考示例中，`primary_criterion` 被声明为 `Artifact`，并且作为 workflow 输入。但这里 primary_category 不需要是输入，因为它固定。所以我们可以采用类似的方式：声明 `const primary_category: Artifact;`，然后在 workflow 中写 `primary_category == "primary";`。

现在，定义步骤：

- `classify_step`：消费 `request`，产出 `category`。
- `primary_handle_step`：消费 `request`，产出 `primary_result`。
- `fallback_handle_step`：消费 `request`，产出 `fallback_result`。
- `final_step`：根据 `category = primary_category`，消费 `primary_result` 或 `fallback_result`，产出 `final_result`。

我们需要一个 named artifact selection：

`const selected_result: Artifact;`

然后在 workflow 中：
`selected_result == if(category = primary_category, primary_result, fallback_result);`

然后 `consumes(final_step) == [selected_result];`

我们需要确保该 if 是合法的：`if(formula, then_artifact, else_artifact)`，公式是 `category = primary_category`。

现在，我们需要声明所有常量。包括：

- `request`, `category`, `primary_result`, `fallback_result`, `final_result`, `selected_result` 作为 `Artifact`。
- `classify_step`, `primary_handle_step`, `fallback_handle_step`, `final_step` 作为 `Step`。
- `classify_name`, `primary_handle_name`, `fallback_handle_name`, `final_name` 作为 `StepName`。
- `classify_instruction`, `primary_handle_instruction`, `fallback_handle_instruction`, `final_instruction` 作为 `Instruction`。
- `primary_category` 作为 `Artifact`（或者我们可以省略它，直接用 "primary" 字面量，但为了命名清晰，保留）。
- 执行器：我们需要声明一些 Agent/Executor。假设我们使用一些 agent，如 `classifier`, `primary_handler`, `fallback_handler`, `final_consumer` 作为 `Agent, Executor`。

我们还需要配置 agent_config 等？可选。但参考示例中包含了 agent_config、allowed_tool 等。不过问题只要求“补全所需的常量声明、数据流、执行器绑定、StepName 和 Instruction 配置”，所以至少需要这些基本配置。我们可以省略 agent_config 和 allowed_tool，但必须绑定执行器。

按照示例，我们可以定义 `const classifier: Agent, Executor;` 等等。

在 workflow 中，我们需要：
- `step_executor(classify_step) == classifier;` 等。
- `step_name(classify_step) == classify_name;`
- `step_instruction(classify_step) == classify_instruction;`

注意：Instruction 的值可以通过 `classify_instruction == "./instructions/classify.md";` 这样的路径来设置，或者我们可以直接设置一个 quoted string？但是 `step_instruction` 返回的是 Instruction，而 Instruction 是一个概念，我们需要给 StepInstruction 赋值。在语法中，`step_instruction(Step) -> Instruction`，所以我们需要一个常量 `classify_instruction` 类型为 Instruction。然后我们可以用 `classify_instruction == "一些指令";` 来设置其值？但是 QuotedConstantID 可以是 "some text"，它可以是一个 Instruction 的值。所以可以。

但是 `step_instruction` 的文档说：`step_instruction(Step) -> Instruction [arity 1]`，即它是一个函数调用，返回 Instruction。它的断言形式是 `step_instruction(classify_step) == classify_instruction;`。所以 `classify_instruction` 必须是一个 Instruction 类型的常量。然后在 workflow 中我们可能还需要为它赋值？实际上，`step_instruction` 断言将一个 Step 绑定到一个 Instruction 常量上。Instruction 常量本身的值可以通过另一个断言设置吗？在语法中，我们可以写 `classify_instruction == "classify the request and output category";`。这样 `classify_instruction` 的值就被设定为一个字符串。但注意，`"classify the request and output category"` 是一个 QUOTEDCONSTANTID，其中包含空格？语法说 QUOTEDCONSTANTID 的字符集是 `[A-Za-z0-9.!#$%?@_{|}~`]*`，不包括空格。所以不能包含空格。所以 Instruction 的值应该使用路径或受限 ID，不能是带空格的句子。在示例中，instruction 通过 StepInstruction 常量设置，但并没有给 instruction 常量赋值个字符串，而是将其作为常量标识符，然后在运行时由系统解析。在参考示例中，instruction 常量被声明但未赋值，只是用于 `step_instruction` 断言。实际上在示例中，instruction 常量只是标识符，没有给值。这说明 instruction 值是需要由外部配置提供的，或者由用户输入。但在这个代码块中，我们只需要声明，不需要提供具体值。同样，StepName 只需要常量名。

所以我们可以声明 `const classify_name: StepName;` 和 `const classify_instruction: Instruction;`，不做赋值。但这样在语义上是不完整的，不过参考示例也是如此。在完整示例中，它们只声明常量，没有赋值。所以我们可以只声明，不赋值。

但注意，在参考示例中，还有 `const review_model: Model; const review_engine: Engine; const review_api: ApiBase; const high_effort: ReasoningEffort; const read_tool: Tool;` 这些是 agent_config 所需的。但我们可以省略 agent_config，因为我们被要求的是“补全所需的常量声明、数据流、执行器绑定、StepName 和 Instruction 配置”，所以我们可以只声明我们需要的部分。不过为了完整性，如果我们只绑定 executor（Agent, Executor），那么必须声明 Agent 和 Executor 类型的常量。然后使用 `step_executor`。Agent 可能还需要其他配置，但最小可行工作流可以不包含 agent_config 和其他。但注意：在参考示例中，agent_config 是必须的吗？从语义上讲，agent executor 需要一个 model、engine 和 api base 才能运行。但语法上，agent_config 是可选 Bool 断言。我们可以省略它，运行时可能使用默认配置。所以在最简工作流中，我们可以只声明 Agent, Executor 常量，并绑定执行器，而不配置 agent_config。同样，我们可以不声明 model 等。

但问题说“完整、可编译并具有真实可执行语义”，可能我们需要让它能够被运行时解析。但我认为提供最简但语法正确的版本即可。

另外，注意 `primary_category` 的值：我们可以在 workflow 中写 `primary_category == "primary";`。但 `"primary"` 是一个 QUOTEDCONSTANTID，它会被当作一个常量名，而不是字符串值？在语法中，constantName 可以是 QUOTEDCONSTANTID，它在 term 中作为 atomicTerm。所以 `primary_category == "primary";` 是将 primary_category 这个 Artifact 常量设置为另一个名为 "primary" 的常量？但 "primary" 被解析为一个 constantName，即一个标识符，而不是字符串字面量。实际上，QUOTEDCONSTANTID 是带引号的标识符，如 `"some-id"`，它被视为一个符号，类似 atom。所以 `"primary"` 是一个合法的 constantName，但它代表一个常量值。所以 `primary_category == "primary"` 意味着 Artifact 常量 primary_category 被赋值为常量 "primary" 指向的值。这可能是正确的语义。但更常见的做法是将 primary_category 作为 workflow 输入，或者使用一个 String 类型的字面量。由于我们无法在语法中直接嵌入字符串文字，只能使用受限 ID，所以使用 "primary" 是合理的。

或者我们可以将 primary_category 声明为 `const primary_category: Artifact;`，然后在 workflow 中设置 `primary_category == "primary";`。这样“primary”就是一个已声明的常量（不需要另外声明，因为 QUOTEDCONSTANTID 是隐式声明的吗？语法没有要求预先声明常量，所以在 term 中使用 "primary" 是合法的原子项）。

可能更简洁的方式是直接使用 `category = "primary"` 进行比较，而不用声明 primary_category。我们可以在 if 公式中写 `category = "primary"`。这样就不需要额外的常量声明。

根据问题描述：“根据 category 是否等于 primary_category”，所以 primary_category 是一个概念，不是字面量。所以我们可以将它声明为常量。为了清晰，我们声明 `const primary_category: Artifact;`，然后在 workflow 中设置它的值：`primary_category == "primary";`。

现在，编写完整的代码。

我们按照工作流模式：声明常量，然后 workflow 块。

注意：需要包含 group 注释：DATA FLOW, EXECUTOR ASSIGNMENT, STEP CONFIGURATION 等。按照指导，我们可以这样分组。

最终代码。