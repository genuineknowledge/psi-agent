我们需要根据用户请求编写一个完整的FusionFlow G4优先级路由工作流。用户已经给出了具体需求：输入是request，一个triage Step产出四个flag（urgent_flag, allowed_flag, review_flag, exception_flag），三个处理Step（primary, review, fallback）都消费request且必须实际运行，然后最终Step根据条件选择结果。

注意事项：
- 条件选择必须由工作流后端真实执行，即使用声明式 `if` 表达式进行artifact selection，而不是写在自然语言指令里。
- 所有Step都要配置executor、step_name、step_instruction。
- 数据流要完整：input_workflow, consumes, produces, output_workflow。
- 最终Step的优先级逻辑：urgent_flag为true且allowed_flag为true => primary_result；否则review_flag为true或exception_flag为true => review_result；否则fallback_result。
- 因为所有三个处理Step都消费request，并且必须实际运行（不能有条件跳过），所以我们需要三路并行都跑，然后用`if`选择最终使用的result。
- 注意：`if`只能用在top-level artifact selection，不能嵌套，所以我们需要链式中间artifact。

需要声明常量：Artifact, Step, StepName, Instruction, Agent, Executor等。还需要考虑类型声明如Model, Engine, ApiBase, ReasoningEffort, Tool等用于agent_config。

用户要求"补全常量声明、数据流、执行器绑定、StepName 和 Instruction 配置"，所以我们需要提供完整的可编译源代码。

参考frozen-skill中的模式，特别是`priority_routing`示例。那个示例已经展示了一个类似的三层if链。我们可以借鉴，但要调整以满足此处的具体条件。

用户的条件：
- `selected_result == if( (urgent_flag = True) AND (allowed_flag = True) , primary_result, review_or_fallback)`
- `review_or_fallback == if( (review_flag = True) OR (exception_flag = True) , review_result, fallback_result)`

注意：比较符是 `=`（数值相等），布尔值字面量是 `True`（根据语法，可以是True/true/TRUE）。我们使用`True`。

另外注意：triage Step产生四个flag（artifact），需要声明对应的Artifact类型。三个处理Step每个产生一个result。

最终Step消费`selected_result`并产出`final_result`。

我们需要配置agent：至少triage_agent, primary_handler, review_handler, fallback_handler, final_consumer。每个都需要agent_config。

我们还需要声明Model, Engine, ApiBase, ReasoningEffort, Tool等常量。但为了简化，我们可以声明一些虚构但语法正确的名称。不过要符合语法：概念名是大写开头（UPID），操作符名是小写（LOWID）。常量名可以是数字、路径或小写ID。

对于agent_config，需要四个参数：Agent, Model, Engine, ApiBase。我们可以定义如`default_model: Model; default_engine: Engine; default_api: ApiBase;` 然后在agent_config中使用它们。

还需要allowed_tool？可以加一个read_tool。reasoning_effort可以设置高。

重点：确保每个Step都绑定executor，有step_name和step_instruction。

外部队列：workflow名称可以叫`priority_routing`或`request_router`。

还要注意输入：workflow输入`[request]`以及四个flag吗？注意：按设计，triage Step产出四个flag，所以输入只有request。四个flag是内部产生的，不是外部输入。

输出：final_result。

根据示例，我们需要在workflow内声明所有常量和数据流。

我们还需要注意`step_timeout`和`max_attempts`是可选的，但可以加上以展示完整性。

引用frozen-skill：group assertions by concern in exact order: DATA FLOW, EXECUTOR ASSIGNMENT, STEP CONFIGURATION, WORKFLOW CONFIGURATION, AGENT CONFIGURATION.

开始写：

```fusionflow
-- SCENARIO: Priority routing workflow: triage flags, run all handlers, select result based on flags.
-- AUTHORED: 2025-01-01 from intent: "priority routing with triage and three handlers"

const request: Artifact;
const urgent_flag: Artifact;
const allowed_flag: Artifact;
const review_flag: Artifact;
const exception_flag: Artifact;
const primary_result: Artifact;
const review_result: Artifact;
const fallback_result: Artifact;
const review_or_fallback: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

const triage_step: Step;
const primary_step: Step;
const review_step: Step;
const fallback_step: Step;
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
const high_effort: ReasoningEffort;
const read_tool: Tool;

workflow priority_routing {
  -- DATA FLOW
  input_workflow(priority_routing) == [request];
  consumes(triage_step) == [request];
  produces(triage_step) == [urgent_flag, allowed_flag, review_flag, exception_flag];
  consumes(primary_step) == [request];
  produces(primary_step) == [primary_result];
  consumes(review_step) == [request];
  produces(review_step) == [review_result];
  consumes(fallback_step) == [request];
  produces(fallback_step) == [fallback_result];
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
  step_executor(primary_step) == primary_agent;
  step_executor(review_step) == review_agent;
  step_executor(fallback_step) == fallback_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(triage_step) == triage_name;
  step_instruction(triage_step) == triage_instruction;
  step_timeout(triage_step) == 300;
  max_attempts(triage_step) == 2;
  step_name(primary_step) == primary_name;
  step_instruction(primary_step) == primary_instruction;
  step_timeout(primary_step) == 300;
  max_attempts(primary_step) == 2;
  step_name(review_step) == review_name;
  step_instruction(review_step) == review_instruction;
  step_timeout(review_step) == 300;
  max_attempts(review_step) == 2;
  step_name(fallback_step) == fallback_name;
  step_instruction(fallback_step) == fallback_instruction;
  step_timeout(fallback_step) == 300;
  max_attempts(fallback_step) == 2;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;
  step_timeout(final_step) == 300;
  max_attempts(final_step) == 2;

  -- WORKFLOW CONFIGURATION
  max_concurrency(priority_routing) == 3;
  workflow_timeout(priority_routing) == 900;

  -- AGENT CONFIGURATION
  agent_config(triage_agent, default_model, default_engine, default_api);
  agent_config(primary_agent, default_model, default_engine, default_api);
  agent_config(review_agent, default_model, default_engine, default_api);
  agent_config(fallback_agent, default_model, default_engine, default_api);
  agent_config(final_agent, default_model, default_engine, default_api);
  allowed_tool(triage_agent, read_tool);
  allowed_tool(primary_agent, read_tool);
  allowed_tool(review_agent, read_tool);
  allowed_tool(fallback_agent, read_tool);
  allowed_tool(final_agent, read_tool);
  reasoning_effort(triage_agent) == high_effort;
  reasoning_effort(primary_agent) == high_effort;
  reasoning_effort(review_agent) == high_effort;
  reasoning_effort(fallback_agent) == high_effort;
  reasoning_effort(final_agent) == high_effort;
}
```

注意：`allowed_flag` 是变量名，不是关键字。没问题。

检查：
- 使用了`True`布尔字面量，语法允许。
- `if`表达式在顶层使用，符合后端要求。
- 所有常量都已声明。
- 数据流正确：triage产出四个flag，三个handler消费request，产生各自的result；用两个if链式选择；最后final step消费selected_result。
- 所有step都有executor、name、instruction。
- 添加了timeout和max_attempts作为可选配置，以显示完整性。
- workflow的max_concurrency=3，因为triage、primary、review、fallback可以并行吗？triage必须先完成才能知道flag，但后面的handler并行时triage已经完成了，所以整体max_concurrency可以设大一些。三个handler可以并行，triage单独，final在最后。设max_concurrency=3或4都可以。这里设为3，确保并行度。
- agent_config使用默认模型等。

注意：我们没有为`step_instruction`提供具体字符串值，而是使用`Instruction`类型常量。在实际部署中，这些Instruction值需要在实例化时提供。这样写是符合语法的。

另外注意：`consumes(triage_step) == [request]` 返回List，正确。`produces(triage_step) == [urgent_flag, allowed_flag, review_flag, exception_flag]` 产生多个artifact，没问题。

还要注意检查符号：比较运算符是`=`（数值相等），我们在if条件中使用`(urgent_flag = True)`，其中True是布尔字面量。语法中`comparisonOp`包括`NUMERIC_EQ` (`=`)，所以这是合法的。

现在，输出应该是一个fenced block labelled `fusionflow`，只返回这个代码块，没有额外文字。

注意：用户要求"只返回一个标记为 fusionflow 的代码块"。所以直接输出即可。