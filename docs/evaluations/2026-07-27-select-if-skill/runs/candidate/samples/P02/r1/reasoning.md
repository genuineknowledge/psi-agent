我们被要求编写一个完整的 FusionFlow G4 工作流，具有可执行语义。需求：优先级路由工作流。输入：request。triage Step 产出四个 flag：urgent_flag, allowed_flag, review_flag, exception_flag。然后三个处理步骤（primary, review, fallback）都消费 request，每次都必须实际运行，分别产出 primary_result, review_result, fallback_result。最终 Step 根据条件选择结果：urgent_flag = true AND allowed_flag = true → primary_result；否则如果 review_flag = true OR exception_flag = true → review_result；否则 → fallback_result。最终 Step 消费选择的结果，产出 final_result。

注意：要求条件选择必须能由工作流后端真实执行，不能只写在自然语言指令里。这意味着我们要使用 FusionFlow G4 语法中的 `if` 表达式来实现条件选择，这符合语法中"Value-producing if(condition formula, then term, else term)"。另外，三个处理步骤必须实际运行（即不跳过）。所以我们需要使用一个 if 表达式链来产生 selected_result，然后最终 Step 消费 selected_result。

语法上，if 表达式可以用在普通赋值中，例如 `selected_result == if(..., ..., ...)`。但注意：if 表达式是一个 term，可以出现在 assertion 的右侧？在 assertion 规则中：`assertion: term ASSERT_EQ term | operatorCall;`。所以 `if(...)` 是一个 term（通过 `ifExpression`），所以我们可以写 `selected_result == if(...)`。但 `if(...)` 本身是一个 value-producing 的表达式，可以嵌套。

我们需要声明所有必要的 constant（Artifact, Step, Executor 等）。按照惯例，我们先声明全局 const，然后在 workflow block 内使用断言。

参考 frozen skill 中的例子：`review_or_fallback == if(...)` 这种形式。所以我们可以写多个命名 if 赋值。

具体来说：

1. triage 步骤消费 request，产出 four flags。
2. primary, review, fallback 步骤都消费 request，分别产出 primary_result, review_result, fallback_result。
3. 然后定义条件选择：可以先定义一个中间选择（review_or_fallback），然后最终选择（selected_result）。
   - `review_or_fallback == if((review_flag = True) OR (exception_flag = True), review_result, fallback_result)`
   - `selected_result == if((urgent_flag = True) AND (allowed_flag = True), primary_result, review_or_fallback)`
4. 然后最终步骤消费 selected_result，产出 final_result。
5. 输出 final_result。

注意：要求 `urgent_flag` 为 true 且 `allowed_flag` 为 true 时选 primary_result；否则 `review_flag` 为 true 或 `exception_flag` 为 true 时选 review_result；否则选 fallback_result。上面的嵌套吻合。

我们需要声明常量：包括 Artifact 类型（request, urgent_flag, allowed_flag, review_flag, exception_flag, primary_result, review_result, fallback_result, selected_result, final_result），Step 类型（triage_step, primary_handler_step, review_handler_step, fallback_handler_step, final_step），StepName 和 Instruction 类型，Agent/Executor 类型。还需要声明一些配置参数：比如 model, engine, api base, reasoning effort, tool 等。

按照模板，我们可以复制命名风格。注意输入输出声明：input_workflow(workflow) == [request] ; output_workflow(workflow) == [final_result]。

还需注意：`request` 是一个 Artifact。四个 flag 也是 Artifact。但 flag 是 bool 值？在语法中，Artifact 可以表示任意值，比较时使用 `=`, `!=` 等比较运算符。所以 `(urgent_flag = True)` 是合法的，其中 `True` 是 boolean 字面量，`urgent_flag` 是 Artifact（其值在运行时应该是一个布尔值）。这符合条件选择的可执行语义。

但是注意：在 if 表达式中，条件公式包括比较，但比较的双方必须是 term。`True` 是 booleanLiteral，是 atomicTerm。所以没问题。

我们需要确保每个步骤都有 step_executor 绑定。按照惯例，使用 agent 类型作为 executor。我们还需要 agent_config 设置。可以类似全特性例子。

注意：`step_timeout` 和 `max_attempts` 可选，但可以加上一些容错。`max_concurrency` 可以设置，因为三个处理步骤可以并行（他们之间没有数据依赖）。所以可以设置 `max_concurrency == 3`。（如果设成更大的值，但只有三个步骤）。另外，triage 步骤必须在三个处理步骤之前完成，因为它的输出产出了 flag，而 flag 在 if 表达式中被用于选择，但三个处理步骤的执行不依赖 flag（它们只消费 request，不依赖 flag）。但注意，if 表达式的条件只用于选择被 final step 消费的 artifact，不影响三个处理步骤的执行。根据要求，三个处理步骤每次都必须实际运行，所以即使选择 primary，review 和 fallback 仍然执行。这符合我们的结构：所有 consumer 步骤都运行（因为它们都是步骤，没有条件跳过）。

数据流：
- input_workflow -> [request]
- consumes(triage_step) == [request]; produces(triage_step) == [urgent_flag, allowed_flag, review_flag, exception_flag]
- consumes(primary_handler_step) == [request]; produces(primary_handler_step) == [primary_result]
- consumes(review_handler_step) == [request]; produces(review_handler_step) == [review_result]
- consumes(fallback_handler_step) == [request]; produces(fallback_handler_step) == [fallback_result]
- 然后两个 if 赋值（它们不是步骤，是数据流中的条件选择，语法上使用 ARTIFACT == if(...)）
- consumes(final_step) == [selected_result]; produces(final_step) == [final_result]
- output_workflow == [final_result]

注意：if 赋值需要放在 workflow block 内。语法支持直接写赋值断言。

需要声明所有常量，包括 StepName 和 Instruction 常量。我们可以使用 QUOTEDCONSTANTID 作为 name 和 instruction 的值？在示例中，`const security_review_name: StepName;` 然后通过 `step_name(security_review) == security_review_name;` 绑定，但实际执行时需要具体的名称字符串。类似地，`step_instruction` 也需要具体的指令。但是 grammar 中 constantName 可以是 QUOTEDCONSTANTID。所以我们可以直接使用带引号的值作为常量名？例如 `const triage_name: StepName;` 然后 `step_name(triage_step) == triage_name;` 但 triage_name 的值没有定义。更好的做法是直接使用字符串字面量作为常量值。但 grammar 中常量声明是 `const constantName COLON conceptNameList`，constantName 不能是字符串字面量（constantName 只能是 NUMBER, RELATIVE_PATH_ID, QUOTEDCONSTANTID, LOWID）。QUOTEDCONSTANTID 本身就是一个带引号的字符串，可以作为一个常量名。例如 `const "Triage Request": StepName;` 这是合法的吗？在 constDecl 中，constantName 包括 QUOTEDCONSTANTID，所以可以。但通常我们使用 LOWID 如 `triage_name`。但为了给 name 和 instruction 赋值，我们需要用常量与 step_name 绑定。然后 step_name 的值应该是一个 StepName 常量。但 StepName 实际上是一个概念，其值可以是字符串。在语法中，我们通过 `step_name(step) == stepped_name_literal` 来设置名称，其中 stepped_name_literal 可以是一个 QUOTEDCONSTANTID？在 assertion 中，右边是 term，term 可以是 atomicTerm，atomicTerm 是 constantName（包括 QUOTEDCONSTANTID）。所以可以直接写 `step_name(triage_step) == "Triage Request";` 吗？语法上允许，因为 `"Triage Request"` 是 QUOTEDCONSTANTID，是 constantName，属于 atomicTerm。但是注意 constantName 是 token，在 parser 中它可以是带引号的字符串。但通常我们声明一个 const 常量，然后引用它。但为了简洁，可以直接将字符串字面量作为值用在 assertion 的右边。我们检查语法：assertion: term ASSERT_EQ term | operatorCall。term 可以是一个 atomicTerm，而 atomicTerm 可以是 constantName。constantName 可以是 QUOTEDCONSTANTID。所以直接使用 `step_name(triage_step) == "Triage Request";` 合法。类似地，instruction 也可以直接使用字符串。

但是为了符合典型示例，我们声明常量然后赋值。不过直接写字符串更简洁，且符合语法。不过我们需要确保这些常量概念（StepName, Instruction）被声明了，但我们可以直接使用字符串字面量，不需要声明常量？因为常量声明是针对 Artifact, Step, Agent 等，StepName 和 Instruction 也是概念，但它们可以有值。如果我们不声明 const，直接使用字符串字面量作为值，是否需要在 workflow block 内声明它们？语法上，constDecl 是全局的，但我们可以不声明 StepName 类型的常量，只要在其使用处（如 step_name 的右边）提供一个 term。term 可以是字符串字面量（QUOTEDCONSTANTID），而 QUOTEDCONSTANTID 本身就是 constantName 的一种，所以即使没有全局 const 声明，它也是一个合法的 term。所以直接写字符串可以。但是为了清晰，我们可以声明一些常量。

考虑到用户期望一个完整的工作流，我将像示例那样声明 const，然后使用常量名。但常量值是什么？我们可以声明 `const triage_name: StepName;` 然后后面通过 `step_name(triage_step) == triage_name;` 绑定，但 triage_name 的具体值未定义。在实际运行中，运行时需要知道 name 的具体字符串。FusionFlow 可能期望在 attribute 中提供具体值？但语法中没有直接赋值给常量的方法，除了通过 operator 调用。例如 `step_name(triage_step) == "Triage Request";` 就可以直接设置名称。所以我倾向于直接使用字符串字面量。

类似地，instruction 也可以直接使用字符串字面量。注意，instruction 可能很长，但语法允许 QUOTEDCONSTANTID 包含一些字符（不包括空白和转义字符）。对于较长的指令，可能不适合放在一行。但为了可读性，我们可以写一个简短的说明。实际使用时，可以通过外部配置提供。但要求中要求补全 StepName 和 Instruction 配置，所以我们应该提供一些合理的字符串。

此外，agent_config 需要 Model, Engine, ApiBase 等参数。需要声明这些常量。我们可以参考示例：`const review_model: Model; const review_engine: Engine; const review_api: ApiBase;` 然后 `agent_config(agent, review_model, review_engine, review_api);`。这些常量值不需要具体指定，因为 agent_config 是一个 Bool 返回的 operator，它主要用来绑定配置，但实际值可能来自运行时环境？在语法中，agent_config 是预设 operator，它接受四个参数，返回 Bool。但为了可执行，可能需要具体的模型/引擎/API 基础 URL。但示例中只是声明了常量，没有赋值。我们也可以直接使用常量名，但需要声明它们。

我认为这是可以接受的：声明常量，然后在 agent_config 中引用它们。运行时引擎会查找这些常量的实际配置（可能通过环境变量或默认值）。我们按照示例去做。

我们还需要 allowed_tool 设置，可以添加一个 tool 常量。

另外，注意：步骤 step_instruction 需要 Instruction 类型的值。同样，我们可以使用字符串字面量，或者声明一个常量。最好直接使用字符串。

考虑到可读性和完整性，我将采用类似示例的结构，但 for 循环我们不需要。我们还需要设置 workflow_timeout 和 max_concurrency 等。

执行器绑定：我们需要为每个步骤声明一个 Agent/Executor 常量。比如 triage_agent, primary_handler 等。并且 agent_config 绑定它们。

现在，编写完整代码。注意要符合语法，所有断言末尾有分号，注释以 -- 开头。

我们将生成一个 fusionflow 代码块。