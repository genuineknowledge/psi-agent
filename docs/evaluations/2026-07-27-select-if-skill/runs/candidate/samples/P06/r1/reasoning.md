我们被要求写一个FusionFlow G4工作流：接收 request，一个判定 Step 产出 approved_flag，两条分支（approve_handler 和 reject_handler）都消费 request 并实际运行（即无论flag如何，两个handler总是执行），产出各自的artifact，然后根据 approved_flag 选择产出哪个作为最终输出。要求最终输出就是选中的 artifact 本身，不要额外增加一个只负责转发的最终 Step。

我们需要考虑如何实现“根据 approved_flag 选择输出”。在FusionFlow中，有 `if` 表达式可以做值选择：`selected == if(formula, then_artifact, else_artifact)`。但是注意：工作流输出必须是 `output_workflow(workflow) == [artifact]`，而 `selected` 是一个 Artifact（通过 if 选择）。这样就可以了，不需要再有一个 Step 来转发。

但是注意：`if` 在FusionFlow中是一个值表达式，不是控制流。两个 handler 都会执行（因为他们消费 request 且被产出，而且每个都有 produces 声明）。所以我们只需要一个 `selected_artifact` 通过 `if` 选择，然后 `output_workflow` 输出它。

另外，注意 grammar 中 `ifExpression` 的语法：`IF LPAREN formula COMMA term COMMA term RPAREN`。这里 `formula` 是条件，第二个是 then 分支的 term，第三个是 else 分支的 term。两个都是 term，可以是常量名（即 artifact）。

我们需要一个 `approved_flag` 作为 artifact 吗？还是作为布尔值？根据 grammar，`if` 的条件是 `formula`，它是比较或逻辑表达式。`approved_flag` 应该是某种 Artifact? 但是 `if` 的条件需要比较操作，比如 `approved_flag = True` 之类的。但是 `approved_flag` 是 Artifact 类型，不能直接参与比较。我们需要一个判断 Step 来产出 `approved_flag`，但为了让它能在 if 条件中使用，我们可能需要一个比较：`approved_flag == true` 或者 `approved_flag = something`。但是 `approved_flag` 是 Artifact 类型，它只能被比较是否等于另一个 Artifact？不，比较操作符 `=`, `!=` 等用于 term 的比较，而 term 可以是 constantName（即 artifact）。所以 `approved_flag = some_constant` 是合法的。但 `some_constant` 应该是另一个 Artifact 常量？不过我们可以定义一个常量 `true_value` 作为 Artifact，也可以直接用布尔字面量 `True`？注意：布尔字面量是 `booleanLiteral`，但它在语法中是 atomicTerm，可以是 `True`, `False`。但是 atomicTerm 只能出现在 term 位置，term 可以是 atomicTerm。所以 `approved_flag = True` 在语法上是合法的（`= True` 中的 True 是 booleanLiteral）。但是类型上：approved_flag 是 Artifact，而 True 是 Bool，类型不匹配。在FusionFlow类型系统中，Artifact 和 Bool 是不同的概念，不能直接比较。所以最好让 `approved_flag` 是一个包含布尔值的 Artifact？或者我们定义比较对象为另一个 Artifact 常量。我们可以定义两个常量：`approve_signal` 和 `reject_signal` 作为 Artifact，然后判断 `approved_flag = approve_signal`。但是那样就需要两个额外的常量。或者，可以定义 `approved_flag` 是一个 Artifact，然后让判断 Step 产出 `approved_flag` 为某个特定的字符串常量，然后比较。

更好的做法：让判定 Step 产出 `approved_flag`，它是一个 Artifact，其值可能是 `"approved"` 或 `"rejected"` 这样的字符串。然后我们可以定义常量 `approved_marker = "approved"` 和 `rejected_marker = "rejected"`，但注意这些字符串不能直接作为 Artifact 常量，因为 Artifact 常量名是标识符。我们可以定义两个 Artifact 常量：`approval_marker` 和 `rejection_marker`，然后在 workflow 外部定义它们的值？在 G4 中不能赋值，只能通过数据流。所以我们不能预先定义常量值。更合理的方式是：判定 Step 产出 `approved_flag`，它是一个 Artifact，而条件比较可以直接用 `approved_flag` 与另一个 Artifact 比较，但另一个 Artifact 必须也是由一个 Step 产出的。我们可能不需要比较具体的值，只需要知道 `approved_flag` 是否为 "真"？但 condition 需要是 formula，比较 operator 要求两边都是 term。

其实我们可以利用 `if` 的条件直接使用布尔值，如果 `approved_flag` 是布尔类型？但 Artifact 不是布尔类型。也许我们可以让判断 Step 产出 `approved_flag` 是一个 Artifact，其值就是布尔字面量 True 或 False？但 Artifact 是抽象类型，实际运行时它是一个字符串或者其他格式？不过从 G4 语法层面，我们只能比较 term 之间的相等性。我们可以让判断 Step 产出 `approved_flag`，然后定义两个常量 `true_artifact` 和 `false_artifact`，然后让条件用 `approved_flag = true_artifact`。但是 `true_artifact` 和 `false_artifact` 也是 Artifact，它们的值怎么定义？在 G4 中无法定义常量的值，只能通过数据流。所以我们需要一个 Step 来产出 `true_artifact` 和 `false_artifact`？那太复杂了。

或者，我们可以从输入中获取一个布尔标记？`request` 可能包含一个字段，但输入只有一个 `request: Artifact`。我们无法从 Artifact 内部提取字段。

也许我们应该重新思考设计：要求中“判定 Step 产出 approved_flag”意味着 `approved_flag` 是一个 Artifact，其内容就是某种标识。我们可以直接在条件中使用 `if` 比较 `approved_flag` 与另一个 Artifact，但另一个必须也是由某个 Step 产出的。但我们可以让判定 Step 既产出 `approved_flag` 又产出某种 marker？不行。

实际上，在 G4 中，`if` 条件是公式，比较操作 `term comparisonOp term`。两个 term 都必须是 atomicTerm 或 operatorCall 等。atomicTerm 可以是 constantName。constantName 可以是 LOWID（如 `true_value`）或 NUMBER。所以我们可以定义常量 `true_marker` 和 `false_marker`，但它们的值无法在 G4 中指定，需要运行时注入？输入中？不，`input_workflow` 定义了工作流输入，我们可以把 `true_marker` 作为输入？但题目只要求 `request` 作为输入，不能增加额外输入。

另一种办法：使用 `if` 的条件直接基于 `approved_flag` 的某种属性？没有。

我们是否可以绕过：让判定 Step 不产出 Artifact，而是直接通过控制流选择？但 G4 没有控制流选择，只有值选择。不过我们可以利用 `if` 条件比较两个 Artifact identity，但需要两个具体的 artifact 作为比较基准。

也许我们可以让判定 Step 产出两个不同的 Artifact？比如分别产出 `approved_artifact` 和 `rejected_artifact`？但题目要求判定 Step 只产出 `approved_flag`，而 approve_handler 和 reject_handler 各自产出。要求中说“一个判定 Step 产出 approved_flag；approve_handler 和 reject_handler 都消费 request、每次执行都实际运行，并分别产出 approved_artifact 与 rejected_artifact。” 所以判定 Step 单独产出 `approved_flag`，而两个 handler 各自产出。然后最终根据 `approved_flag` 选择其中一个。

考虑使用 `if` 条件：`approved_flag = some_marker`。但 `some_marker` 需要是一个常量，其值需要在工作流外部定义？在 FusionFlow 中，常量声明只是声明存在一个该类型的实体，并不绑定值。所以比较两个常量的 identity 没有意义，因为它们没有值。运行时比较的是 artifact 的内容？文档中没有明确说 `=` 比较的是什么。但根据常见语义，比较操作符比较的是 term 的 identity（即 artifact 实例的 identity），或者比较它们的值？如果是比较值，那么我们需要在语法上让值编程指定。

我怀疑我们可能需要在 G4 中支持比较 Artifact 的 identity，或者使用运行时注入的布尔值。但既然题目要求编写“完整、可编译并具有真实可执行语义”的工作流，我们必须确保语法正确，语义可执行。我们或许可以利用 `if` 条件中的布尔字面量 `True` 作为条件，但 `approved_flag` 不是布尔类型。

另一种思路：让判定 Step 产出 `approved_flag` 作为一个 Boolean 类型的值？但是 G4 中 `Bool` 类型存在（booleanLiteral），但常量声明可以声明为 `Bool` 吗？语法只支持概念名列表，概念名是 UPID，如 `Bool`。我们可以声明 `const approved_flag: Bool;` 吗？在概念名列表中可以使用 `Bool` 吗？这取决于 catalog 中是否定义了 `Bool` 概念。但通常 FusionFlow 的 catalog 包含 `Artifact, Bool, Integer, List` 等基础类型。我们可以假设 `Bool` 是可用的。如果能声明 `Bool` 类型的常量，那么条件可以直接用 `approved_flag`（因为 `if` 的条件是公式，公式由比较或逻辑组合，但比较操作返回 Bool。而 `approved_flag` 是 Bool 类型，不是 formula，不能直接用。公式必须是比较或逻辑组合。但我们可以写 `approved_flag = True`，左边是 Bool，右边是 Bool 字面量，这是合法的比较。但原子比较 `term comparisonOp term`，两边都是 term，而 `True` 是 booleanLiteral，是 atomicTerm，所以语法通过。类型上，两个 Bool 可以比较，返回 Bool。这样条件就准备好了。

那么我们需要一个 Step 产出 `approved_flag`，其类型是 Bool。在 G4 中，Step 的 `produces` 是输出 Artifact，但 Bool 不是 Artifact，不能直接输出为 Artifact。`produces` 的 RHS 是 list of 常量，这些常量的概念应该是 Artifact。如果 `approved_flag` 是 Bool 类型，它不能出现在 `produces` 的 RHS 中，因为 `produces` 期望 Artifact 列表。所以判定 Step 不能产出 Bool 类型。

看来直接用 Bool 不行。

我们回到前面的想法：让判定 Step 产出 `approved_flag` 作为 Artifact，然后我们还需要一个常量 `approved_true` 作为 Artifact 用于比较。而 `approved_true` 需要被赋值。我们可以通过输入赋值吗？只接受 request，不能增加输入。但是我们可以让判定 Step 同时产出 `approved_flag` 和 `approved_true`？不，产出多个 artifact 是可以的：`produces(judge_step) == [approved_flag, marker];` 然后 `marker` 可以是一个固定的字符串值？但是常量 `marker` 的值不能直接设定，只能通过数据流。我们可以让判定 Step 在其 instruction 中产出两个 artifact：一个 flag，一个 marker（标记为 "approved"）。然后条件判断 `approved_flag = marker` 就能工作了，因为两个都是同一个 Step 产出的，运行时它们的值可以根据 instruction 来设定。但要求是判定 Step 只产出 approved_flag，不能额外产出 marker。

我们也可以让 approve_handler 或 reject_handler 产出 marker？但这样条件需要知道哪个 handler 执行了。事实上两个 handler 都执行，它们各自产出 artifact。我们可以让 approve_handler 同时产出 `approved_marker` 作为附加输出，然后条件比较 approved_flag 与 approved_marker。但那样会使 handler 不纯粹。

另一种方法：不需要比较内容，只需要根据 approved_flag 本身来选择，但 if 条件必须是比较。有没有可能用 `!approved_flag` 作为条件？`!` 是逻辑非，应用于 formula，但 approved_flag 不是 formula。不能。

似乎唯一干净的方式是使用 `approved_flag` 与另一个 Artifact 比较，且另一个 Artifact 必须在工作流内由某个 Step 产出。我们可以让判定 Step 产出两个 Artifact：`approved_flag` 和 `marker`，其中 `marker` 是一个固定值（比如字符串 "approve"），然后条件使用 `approved_flag = marker`。但是要求明说“一个判定 Step 产出 approved_flag”，没有说产出其他。但产出多个 artifact 并不违反“产出 approved_flag”，它只是额外产出。可能可以接受。但最好只产出 approved_flag。

我们再考虑另一个设计：不使用值比较，而直接让 `if` 的条件基于 approved_flag 的 identity（即比较是否等于自身）。例如 `if(approved_flag = approved_flag, then, else)`，这永远是 true，没有用。

我们需要一个条件，比如 `approved_flag` 的某种属性可以区分。但无法表示。

也许题目预期我们使用 `if` 条件直接使用布尔字面量 `True` 或 `False`？即条件固定？但是那就不依赖于 approved_flag 的值了。

或者，我们可以将 `selector` 由另一个 Step 来执行，该 Step 读取 approved_flag 并输出选择后的 artifact。但题目禁止“只负责转发的最终 Step”。但这样 Step 不仅转发，它执行选择逻辑（根据 flag 从两个输入中选一个），所以可能不算“只负责转发”。但是要求说“不要再增加只负责转发的最终 Step”，这意味着我们不能增加一个 Step 专门用来转发。但是如果我们增加一个 Step，它实际执行选择（可能是一个 Agent），这样不是单纯的转发，也许可以？但问题是选择本身是一个布尔判断，用 agent 来做有点杀鸡用牛刀。而且选择逻辑最好用 if 表达式。

综合来看，最自然的方案是使用 `if` 表达式，条件需要是一个 formula。我们可以让 `approved_flag` 是 Artifact，然后定义两个常量 `approve_value` 和 `reject_value`，但它们的值如何指定？我们可以通过 `input_workflow` 传入吗？但是 input 只有一个 request。也许我们可以把这两个常量作为工作流的额外输入？但问题描述只说接收 request。如果我们增加额外输入，可能不符合要求。

考虑到实际可执行语义，在参考示例中，有 `if` 的使用，条件是比较两个 artifact（比如 `(primary_observation = primary_criterion) AND !(block_observation = block_criterion)`）。其中 `primary_criterion` 是工作流的输入 artifact。所以条件可以基于多个输入 artifact 的比较。在我们的场景中，我们可以把判定 Step 视为产生一个 flag，但为了比较，我们还需要一个参考值作为输入。如果只允许 `request` 作为输入，那么我们可以让 `request` 包含一个字段用于比较？但 request 是单一 artifact，不能提取子字段。

或许我们可以用另一种方式：判定 Step 产出两个 output：`approved_flag` 和 `reference_flag`（实际也是同样的 flag），但这样比较没意义。

我想到一个办法：直接使用 `if` 条件而不依赖比较，利用布尔字面量？不行。

再看看语法：`ifExpression` 的条件是 `formula`，而 `formula` 可以是 `comparison` 或 `NOT formula` 或逻辑组合。但不能是 bare `atomicTerm`。所以条件必须是一个比较或逻辑表达式。因此，没有比较的不可能。

所以我们必须有一个比较。我们至少需要两个 term 进行比较。我们可以用 `approved_flag = some_value`，其中 some_value 可以是另一个常量。这个常量可以是工作流的输入之一，或者由另一个 Step 产出。如果我们增加一个输入 `flag_reference`，但不符合要求。如果我们让审批 Step 产出 `approved_flag` 和一个固定的 marker（比如 `approval_marker`），然后比较 `approved_flag = approval_marker`。但审批 Step 产出两个 artifact，可以接受吗？题目只说“产出 approved_flag”，没有说不能产出其他。我认为可以接受，只要它确实产出了 approved_flag，同时也产出另一个用于比较的 marker。但 marker 也可以作为选择 then/else 分支的标识。不过 if 条件使用哪个 marker 来比较呢？我们设定 marker 始终是固定值，比如字符串 "approved"，那么条件就是 `approved_flag = marker`。如果 approved_flag 也是这个字符串，则条件真，选择 `approved_artifact`；否则选择 `rejected_artifact`。这样两个 handler 都执行，都是消费 request，然后选择其中一个输出。

但是，如何确保 `marker` 的值是固定的？在 G4 中，常量的值由产生它的 Step 决定。我们可以让审批 Step 在其 instruction 中确保产出 `marker` 为某个固定值（比如 "approved"）。但问题是，我们无法在 G4 层面强制该值，只能通过 instruction 文本。这是可执行的，因为 instruction 会被传递给 agent 或其他 executor，由它们产生内容。所以合理。

但在语法层面，我们需要声明 `marker` 作为 Artifact，并在 `produces(judge_step) == [approved_flag, marker]`。然后 condition 中使用 marker。但注意：`marker` 是一个 Artifact，且审批 Step 产出它。那么条件写成 `approved_flag = marker` 是否总是 true？因为我们没指定 marker 的内容，它可以和 approved_flag 相同。但是如果我们希望条件区分，我们必须让 marker 的内容与 approved_flag 不同。如果 marker 是固定值 "yes"，而 approved_flag 可能是 "yes" 或 "no"，那么条件为真当且仅当 approved_flag 是 "yes"。这需要审批 Step 的 instruction 体现：产出 approved_flag 为 "yes" 或 "no"，同时 marker 固定为 "yes"。合理。

但是这样我们就需要两个输出：approved_flag 和 marker。题目要求“产出 approved_flag”，并没有禁止额外产出。我认为可以。

另一个思路：让 judge_step 产出 `approved_flag`，而 `approve_handler` 和 `reject_handler` 各自产出 `approved_artifact` 和 `rejected_artifact`，然后我们用一个 `if` 基于 `approved_flag` 与一个外部定义的常量（比如字符串 "true"）比较，但外部常量无法定义。我们可以用 `input_workflow` 传入一个常量作为参考，但要求只接收 request。

最终，我决定使用两个额外常量：`approve_criterion` 和 `reject_criterion`，作为工作流输入。但只接受 request 不符合。那就不增加。

或许可以用 `if` 条件结合逻辑运算符和比较 `approved_flag = approved_flag` 是永远真，但我们也可以取反 `!approved_flag =! approved_flag` 也是永远假。不好。

我注意到在示例中，他们使用 `if((primary_observation = primary_criterion) OR`，其中 primary_criterion 是输入。所以我们确实需要额外的比较对象。如果完全不允许额外输入，我们只能通过内部创建。那就让判断 Step 产出 approved_flag 和一个比较基准（比如 `approval_standard`），然后比较。这样最合理。

所以设计如下：
- 输入 request
- 判断 Step: 消费 request，产出 approved_flag 和 approval_standard（固定的 Artifact，例如内容为 "approved"）
- approve_handler: 消费 request，产出 approved_artifact
- reject_handler: 消费 request，产出 rejected_artifact
- 使用 if: selected == if(approved_flag = approval_standard, approved_artifact, rejected_artifact)
- 输出 selected

但是需要注意：approve_handler 和 reject_handler 总是执行，不管判断结果。这正是要求中的“每次执行都实际运行”。然后 if 选择输出。

这个设计满足要求。

现在编写完整的 G4 source。我们需要声明所有常量：request, approved_flag, approval_standard, approved_artifact, rejected_artifact, selected_artifact，以及 Step: judge_step, approve_handler_step, reject_handler_step，以及所需的其他常量：StepName, Instruction, Agent, Executor 等。

注意 constants 声明语法：`const name: concept1, concept2;` 多个概念用逗号分隔。我们需要为每个常量声明其类型/概念。例如 `const request: Artifact;`。还要声明 Step, StepName, Instruction, Agent, Executor。

根据示例，Step 是一个概念，后面不能再修饰为 Step？在示例中声明 `const security_review: Step;` 然后 `const security_review_name: StepName;` 等等。所以我们需要单独的常量。

此外，agent_config 需要 Agent, Model, Engine, ApiBase。我们定义一个 `agent` 常量，并设定 agent_config。需要声明 model, engine, api_base 等常量。

为了简化，我们可以只定义一个 agent（比如 `agent`），然后用于所有 step executor。或者每个 step 定义不同 agent。但为了简洁，用一个 agent。

还需要 instruction 常量：`const judge_instruction: Instruction;` 等。

注意：如果使用 `if`，我们需要 `selected_artifact` 作为 Artifact。然后 `output_workflow` 使用它。

而且，注意 data flow group 的顺序：首先 input_workflow, 然后 consumes/produces, 然后 foreach, 然后 if selection, 然后 output_workflow。

我们按照示例分组：DATA FLOW, EXECUTOR ASSIGNMENT, STEP CONFIGURATION, WORKFLOW CONFIGURATION, AGENT CONFIGURATION。

WORKFLOW CONFIGURATION 可以设置 max_concurrency 和 workflow_timeout，可选。我们设置 `max_concurrency(workflow) == 3;` 以便并行的 handler 可以同时执行，虽然 judge_step 必须先完成，但 concurrency 只限制同时运行的最大步数。judge_step 独立，handler 可以在 judge 完成后并行。设置大一点允许并行。

STEP CONFIGURATION: 需要 step_name, step_instruction, 可选 step_timeout, max_attempts。我们按需设置。

AGENT CONFIGURATION: 需要 agent_config, allowed_tool 等。我们定义一个 agent，并配置简单的。

现在开始写代码。

注意语法：if expression 是 `selected_artifact == if(formula, approved_artifact, rejected_artifact);` 注意 formula 需要用括号。条件用 `approved_flag = approval_standard` 这里的 `=` 是比较运算符。

确保所有 term 都是已声明的常量名。

最终输出应该是 `[selected_artifact]`，注意 list 字面量。

另外，所有语句以分号结束。

注意注释格式：`--` 开头。

我们按照示例写完整的 workflow，包括所有必需的常量声明。

我会生成一个只包含 fusionflow 代码块的回复。