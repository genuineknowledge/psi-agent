我们被要求编写一个 FusionFlow G4 工作流，满足需求：

- 接收一份 request
- 一个分类 Step 产出 category
- 两个处理 Step 都消费 request，分别产出 primary_result 和 fallback_result
- 最后一个消费 Step 根据 category 是否等于 primary_category，只消费被选择的处理结果并产出 final_result
- 两个处理 Step 必须在每次执行中都实际运行（即不能有短路，只是值选择）
- 补全所需的常量声明、数据流、执行器绑定、StepName 和 Instruction 配置

注意要在单个 fusionflow 代码块中，不要解释。

从 grammar 和 skill 中，我们可以使用 Named Artifact selection 模式：用 if 表达式将选中的结果赋给一个 artifact，然后传给 final Step。

我们需要声明：
- request: Artifact
- category: Artifact (由分类step产出)
- primary_result, fallback_result: Artifact
- selected_result: Artifact (if 表达式结果)
- final_result: Artifact
- primary_category: (常量？应该是一个 Artifact 常量，但实际是参考值，通常是一个字符串常量？但是在 G4 中我们可以定义一个常量名来持有值，但常量只能是 Number, RELATIVE_PATH_ID, QUOTEDCONSTANTID, LOWID。我们可以声明一个常量 primary_category 作为 Artifact 常量（但类型不匹配？实际上 primary_category 应该是一个常量值，用于比较。但是 G4 的 artifact 是 data，但我们可以把它作为一个独立常量，不是 Artifact，而是一个 constantName。但是 if 条件中的比较是 term comparison，我们可以用 constantName 来比较。但通常我们需要一个输入常量或者一个预设值。我想我们可以定义 primary_category 作为一个常量，类型是？在概念系统中需要定义。但 skill 中允许常量声明 const primary_category: SomeConcept; 但我们要使用它作为比较值。为了方便，我们可以声明一个 const primary_category: Artifact; 但实际上它不是一个 step 产出的 artifact，而是一个固定值。那就需要定义为一个 Artifact 并且通过输入传入？或者我们可以在 workflow 中将 primary_category 作为一个输入 Artifact。这样更符合 dataflow：输入 request, primary_category。这样 if 条件里可以比较 category 和 primary_category。是的，这样更干净。因为两个处理 Step 都消费 request，而 final Step 根据 category 决定消费哪个结果。在 Named Artifact selection 中，every candidate producer runs. selection 是 eager value routing。所以我们需要一个比较来选值。

所以输入：input_workflow 包含 request 和 primary_category。让分类 Step 消费 request 产出 category；然后两个处理 Step 消费 request 产出 primary_result 和 fallback_result；然后定义 selected_result == if( category = primary_category, primary_result, fallback_result ); 最后 final Step 消费 selected_result 产出 final_result。注意 category = primary_category 是 NUMERIC_EQ 即 '='。

另外，注意要满足 "两个处理 Step 必须在每次执行中都实际运行" —— 它们都会运行，因为如果没有依赖关系，它们都会执行（引擎根据依赖图执行，它们都依赖于 request，而 request 是输入，所以都会执行）。if 表达式只是选择哪个结果传给 final Step，不会阻止处理 Step 运行。

现在按照 skill 中的模板和 Full-featured 示例来写。

需要声明所有的 artifacts, steps, 常量 (StepName, Instruction, Agent, Executor, 等)。确保声明完整，包括 step_instruction 等。

我们还需要定义一些概念如 StepName, Instruction, Agent, Executor 等。需要声明 const primary_category_constant: ??? 实际上 primary_category 是一个输入 Artifact，所以我们声明 const primary_category: Artifact; 并且 input_workflow 包含它。

分类 Step 需要 instruction，描述它根据 request 判断类别。类似地，两个处理 Step 也需要 instruction 描述处理。final Step instruction 描述根据 category 进行选择并输出。

我们还需要 agent/executor 绑定。我们可以使用一个 agent: Agent, Executor;

但为了清晰，可以定义多个 agent 或者共享一个？都可以。为了简单，共享同一个 agent 但不同 instruction。

按照 skill 中 full example，我们需要声明 step_name, step_instruction, step_executor 等。

还要注意 workflow 名称，假设为 categorization_router。

完整代码：

(在 fusionflow 代码块中)