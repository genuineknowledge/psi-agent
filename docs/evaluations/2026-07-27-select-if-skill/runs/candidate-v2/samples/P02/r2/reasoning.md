开始理解用户的请求。用户希望编写一个完整、可编译、具有真实可执行语义的FusionFlow G4优先级路由工作流。

首先，我需要解析用户的具体需求：

**输入：** 一个 request (Artifact)

**步骤：**
1. triage Step → 产出 urgent_flag, allowed_flag, review_flag, exception_flag
2. primary_handler_step → 消费 request，产出 primary_result
3. review_handler_step → 消费 request，产出 review_result
4. fallback_handler_step → 消费 request，产出 fallback_result
5. final_step → 消费选择结果，产出 final_result

**选择逻辑（通过 `if` 条件，必须有真实语义）：**
- 如果 urgent_flag = true 且 allowed_flag = true → 选 primary_result
- 否则如果 review_flag = true 或 exception_flag = true → 选 review_result
- 否则 → 选 fallback_result

**要求：**
- 补全常量声明、数据流、执行器绑定、StepName 和 Instruction 配置
- 条件选择必须能由工作流后端真实执行（即使用 `if` 表达式和声明式 Artifact 选择）
- 只返回一个 fusionflow 代码块

现在，我需要根据 frozen-skill 中的指导来构建这个工作流：

1. **语法合规性：** 使用 grammar 中的合法语法（`if` 表达式、`==` 断言等）
2. **命名 Artifact 选择模式：** 使用多个 `if` 表达式形成条件链，不能嵌套 if
3. **所有候选 producer 都会运行**（这是 eager selection），符合用户要求的"每次执行都必须实际运行"
4. **可执行语义：** 条件必须通过声明式 `if` 表达式表达，而非自然语言

让我回顾一下语法规则：

- `if(condition, then, else)` - 必须是三个参数
- 条件使用 comparison operators: `=`, `!=`, `<`, `<=`, `>`, `>=`
- 条件组合使用 `!`, `AND`, `OR`
- 不能嵌套 if，需使用链式中间 Artifact

根据用户的需求，我理解条件链应该是：

第一层选择（检查 urgent 和 allowed）：
```
urgent_flag = True 且 allowed_flag = True → primary_result
否则 → review_or_fallback
```

第二层选择（review_or_fallback）：
```
review_flag = True 或 exception_flag = True → review_result
否则 → fallback_result
```

等等，用户的 flag 是 Artifact 而不是布尔字面量。所以 flag 应该是 `True` / `False` 这样的布尔值，但它们是 Artifact 类型。在比较中，我需要比较 flag 和布尔字面量。

但是 grammar 中，条件是比较两个 term，term 可以是 atomicTerm（constantName 或 booleanLiteral）。所以 `urgent_flag = True` 是合法比较。

不过，urgent_flag 作为 Artifact，在运行时值是"True"或"False"字符串。条件比较比较的是 Artifact 的值与布尔字面量 True。

等等，让我看看 grammar 定义：
- `booleanLiteral : TRUE | FALSE ;`
- `TRUE : 'True' | 'true' | 'TRUE' ;`
- `FALSE : 'False' | 'false' | 'FALSE' ;`

所以 `urgent_flag = True` 是合法语法。

但是，urgent_flag 是 Artifact，它的值会是步骤产生的文本内容。在运行时，如果 triage step 产出内容为 "True"，那么与字面量 `True` 比较是否会匹配？这取决于运行时的语义——比较的是字符串值。

根据 frozen-skill 中的 Named Artifact selection with if 示例：
```
review_or_fallback == if(
    (review_observation = review_criterion) OR (exception_observation = exception_criterion),
    review_result,
    fallback_result
);
```

这里比较的是两个 Artifact（review_observation = review_criterion）。所以 Artifact 比较的是 Artifact。但我的场景需要比较 Artifact 与布尔字面量。

在 priority_routing 示例中，比较的都是 Artifact 对 Artifact。但我这里需要比较 flag 值与布尔字面量。

在语法上，`term comparisonOp term` 允许 atomicTerm（constantName 或 booleanLiteral），所以 flag 与布尔字面量比较是合法的语法。

但为了更稳健，我也可以让 triage step 产出专门的 flag 值，然后与预期的值比较。不过我觉得直接与 True/False 比较在语义上更清晰，且语法允许。

实际上，再看用户的描述，flag 是 "triage Step 产出 urgent_flag、allowed_flag、review_flag 和 exception_flag"。它们是 Artifact，产出内容应该是布尔字符串。

在 runtime 语义中，比较的是 Artifact 的字符串值。所以 `urgent_flag = True` 就是比较 urgant_flag 产出的字符串是否等于 "True"。

好的，就这样设计。

现在来构建工作流：

**Artifact 声明：**
- request (输入)
- urgent_flag, allowed_flag, review_flag, exception_flag (triage 产出)
- primary_result, review_result, fallback_result (各自产出)
- selected (第一层 if 结果)
- review_or_fallback (第二层 if 结果)
- final_result (最终产出)

等等，看一下结构——urgent_flag 等是 triage 产出的。需要明确每个 Artifact 的类型。

在 priority_routing 示例中：
```
consumes(triage_step) == [incoming_case];
produces(triage_step) == [primary_observation, block_observation, review_observation, exception_observation];
```

所以 triage 产出是一个列表中的多个 Artifact。

在我的场景中：
```
produces(triage_step) == [urgent_flag, allowed_flag, review_flag, exception_flag];
```

每个处理 step 消费 request 并产出各自的结果：
```
consumes(primary_handler_step) == [request];
produces(primary_handler_step) == [primary_result];
...
```

final step 消费 selected result 产出 final_result：
```
consumes(final_step) == [selected_result];
produces(final_step) == [final_result];
```

条件选择：
```
review_or_fallback == if(
    (review_flag = True) OR (exception_flag = True),
    review_result,
    fallback_result
);

selected_result == if(
    (urgent_flag = True) AND (allowed_flag = True),
    primary_result,
    review_or_fallback
);
```

这样就用两个链式 if 表达了三层优先级。

现在看一下 executor 分配。我需要声明 agent：
- triage_agent, primary_handler, review_handler, fallback_handler, final_consumer

StepName 和 Instruction：
- 各 step 的 name 和 instruction

需要声明：
```
const triage_name: StepName;
const primary_handler_name: StepName;
const review_handler_name: StepName;
const fallback_handler_name: StepName;
const final_name: StepName;

const triage_instruction: Instruction;
const primary_instruction: Instruction;
const review_instruction: Instruction;
const fallback_instruction: Instruction;
const final_instruction: Instruction;
```

关于 model/engine/api：
看着 frozen-skill 中的代码，可以省略 agent_config 或其他 agent 配置，或者用基本的配置。

让我使用简化配置，只声明 agent 和 executor，加上 agent_config。

还需要注意语法中的细节：StepName 和 Instruction 是类型，需要和常量关联。

还有 `step_executor(step) == agent` 的绑定。

现在让我整理完整的代码。

关于 agent_config:
- 需要 Model, Engine, ApiBase
- 在示例中使用了 `review_model`, `review_engine`, `review_api`
- 我可以声明一个简单的 agent 配置

由于用户没有指定具体模型，我可以使用通用的 agent 配置。

现在编写完整的 workflow：

```fusionflow
-- SCENARIO: priority routing workflow with triage and three handler branches
-- AUTHORED: from intent: "优先级路由工作流：triage → 条件选择 primary/review/fallback → final"

const request: Artifact;
const urgent_flag: Artifact;
const allowed_flag: Artifact;
const review_flag: Artifact;
const exception_flag: Artifact;
const primary_result: Artifact;
const review_result: Artifact;
const fallback_result: Artifact;
const selected_result: Artifact;
const review_or_fallback: Artifact;
const final_result: Artifact;

const triage_step: Step;
const primary_handler_step: Step;
const review_handler_step: Step;
const fallback_handler_step: Step;
const final_step: Step;

const triage_name: StepName;
const primary_name: StepName;
const review_name: StepName;
const fallback_name: StepName;
const final_name: StepName;

const triage_instruction: Instruction;
const primary_instruction: Instruction;
const review_instruction: Instruction;
const fallback_instruction: Instruction;
const final_instruction: Instruction;

const triage_agent: Agent, Executor;
const primary_agent: Agent, Executor;
const review_agent: Agent, Executor;
const fallback_agent: Agent, Executor;
const final_agent: Agent, Executor;

const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;

workflow priority_routing {
  -- DATA FLOW
  input_workflow(priority_routing) == [request];
  consumes(triage_step) == [request];
  produces(triage_step) == [urgent_flag, allowed_flag, review_flag, exception_flag];
  consumes(primary_handler_step) == [request];
  produces(primary_handler_step) == [primary_result];
  consumes(review_handler_step) == [request];
  produces(review_handler_step) == [review_result];
  consumes(fallback_handler_step) == [request];
  produces(fallback_handler_step) == [fallback_result];
  review_or_fallback == if(
    (review_flag = True) OR (exception_flag = True),
    review_result,
    fallback_result
  );
  selected_result == if(
    (urgent_flag = True) AND (allowed_flag = True),
    primary_result,
    review_or_fallback
  );
  consumes(final_step) == [selected_result];
  produces(final_step) == [final_result];
  output_workflow(priority_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(triage_step) == triage_agent;
  step_executor(primary_handler_step) == primary_agent;
  step_executor(review_handler_step) == review_agent;
  step_executor(fallback_handler_step) == fallback_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(triage_step) == triage_name;
  step_instruction(triage_step) == triage_instruction;
  step_name(primary_handler_step) == primary_name;
  step_instruction(primary_handler_step) == primary_instruction;
  step_name(review_handler_step) == review_name;
  step_instruction(review_handler_step) == review_instruction;
  step_name(fallback_handler_step) == fallback_name;
  step_instruction(fallback_handler_step) == fallback_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;

  -- AGENT CONFIGURATION
  agent_config(triage_agent, default_model, default_engine, default_api);
  agent_config(primary_agent, default_model, default_engine, default_api);
  agent_config(review_agent, default_model, default_engine, default_api);
  agent_config(fallback_agent, default_model, default_engine, default_api);
  agent_config(final_agent, default_model, default_engine, default_api);
}
```

嗯，这里有一个问题。在语法中，`True`/`False` 是 booleanLiteral 关键字。按照 grammar：
```
comparisonOp : NUMERIC_EQ | NOT_EQUALS | LT | LTE | GT | GTE ;
NUMERIC_EQ : '=' ;
NOT_EQUALS : '!=' ;
```

所以比较运算符包括 `=` (数值/相等比较) 和 `!=` 等。

在示例中：
```
(review_observation = review_criterion)
```
用的是 `=`（NUMERIC_EQ）比较两个 Artifact。

所以我应该用 `=` 来比较 Artifact 值：
```
(review_flag = True)
```

这里 `True` 是 booleanLiteral，在语法中属于 atomicTerm 范畴。这是合法的。

再看 `# Q` 的缩进——用户要求"只返回一个标记为 fusionflow 的代码块"，所以需要加上标记。

现在检查所有需求：

1. ✅ 输入是 request（通过 input_workflow）
2. ✅ triage step 产出四个 flag
3. ✅ primary/review/fallback handler 都消费 request，每次执行都必须实际运行（because the workflow backend runs all candidate producers regardless of selection — they share the same `consumes` edge and their `produces` edges are present in the dataflow, so backend will schedule them all before the if selection）
4. ✅ 条件选择：通过两个 if 表达式声明链式选择，可在后端真实执行
5. ✅ 最终 step 消费 selected_result 产出 final_result

让我用更准确的判断——用户的 "每次执行都必须实际运行" 指的是每个 handler step 都必须被实际执行，即使它的结果可能不被最终选中。在 eager selection 语义中，所有候选 producer 都会运行（因为 if 表达式是在运行后选择值），所以这自动满足。

现在让我再检查语法，特别是关于空的 group 是否被允许。

语法中 workflowItem 可以是 assertion 或 operatorCall。注释 group（如 `-- DATA FLOW`）是 LINE_COMMENT，会被跳过。

好的，一切看起来正确。

关于 agent_config 的参数：
```
agent_config(Agent, Model, Engine, ApiBase) -> Bool
```

我声明了 default_model, default_engine, default_api，并在每个 agent 上配置同样的。

这个工作流没有使用 tool（可选的），所以不需要 allowed_tool。这是可接受的。

现在我还需要确认一下常量类型声明是否需要更多的 Artifact。在示例 priority_routing 中，每个 flag/observation 被声明为 Artifact，condition 中的 comparison 操作数的类型匹配检查由 checker/catalog 负责，不在这里操心。

好的，让我最终输出代码。

哦等一下，我注意到用户说"请补全常量声明、数据流、执行器绑定、StepName 和 Instruction 配置"，这意味着用户期望一个完整的、可编译的工作流，而不是需要用户补充的骨架。

我现在已经补全了，所以直接输出完整的代码块。

另外，关于 `if` 表达式的比较，frozen-skill 的 Named Artifact selection 部分说：
```
Build conditions with =, !=, <, <=, >, or >=; reserve == for the surrounding assertion.
Combine comparisons with !, AND, and OR.
Both branches must be declared Artifacts.
```

所以我的比较是正确的：在 if 表达式中使用 `=` 和 `!=`，在工作流主体中使用 `==`。

最终代码看起来没问题。让我再检查一遍用户强调的条件链：
- 第一条件：urgent_flag = True AND allowed_flag = True → primary_result ✅
- 第二条件：review_flag = True OR exception_flag = True → review_result ✅
- 否则 → fallback_result ✅

对应我的代码：
```
review_or_fallback == if(
    (review_flag = True) OR (exception_flag = True),
    review_result,
    fallback_result
);
selected_result == if(
    (urgent_flag = True) AND (allowed_flag = True),
    primary_result,
    review_or_fallback
);
```

这个链对应了优先级：
1. primary（当 urgent 且 allowed）
2. review（当 review_flag 或 exception_flag）
3. fallback（其他情况）

完全符合用户需求。

现在输出。