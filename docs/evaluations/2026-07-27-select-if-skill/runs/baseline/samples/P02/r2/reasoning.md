我们需要编写一个完整的FusionFlow G4优先级路由工作流。用户给出了明确的输入、步骤、条件逻辑。我需要按照语法规则编写。

关键要求:
- 输入: request
- triage Step: 产出 urgent_flag, allowed_flag, review_flag, exception_flag (四个Artifact)
- primary, review, fallback Step: 都消费 request, 分别产出 primary_result, review_result, fallback_result (每次都必须实际运行 - 意味着它们不能因为条件选择而不运行? 但条件选择是在最终Step的consumes中决定哪个被消费, 而三个Step各自独立产生Artifact, 依赖关系上它们都只依赖request, 所以即使最终只选一个, 三个Step都会执行, 因为它们各自产生了独立的Artifact. 这个在数据流图中可以实现: primary_handler_step consumes [request], produces [primary_result]; 同样 review consumes [request], produces [review_result]; fallback consumes [request], produces [fallback_result]. 最终Step的consumes用if选择其中一个. 这样三个步骤都会执行, 满足"每次执行都必须实际运行".)
- 最终Step: 通过条件从三个result中选一个消费, 然后产出final_result.

我需要声明常量:
- Artifacts: request, urgent_flag, allowed_flag, review_flag, exception_flag, primary_result, review_result, fallback_result, final_result
- Steps: triage_step, primary_handler_step, review_handler_step, fallback_handler_step, final_step
- 还需要StepName, Instruction, Executor (Agent)等

数据流:
- input_workflow: [request]
- triage_step consumes [request], produces [urgent_flag, allowed_flag, review_flag, exception_flag]
- primary_handler_step consumes [request], produces [primary_result]
- review_handler_step consumes [request], produces [review_result]
- fallback_handler_step consumes [request], produces [fallback_result]
- final_step consumes [ if( (urgent_flag = True) AND (allowed_flag = True), primary_result, if( (review_flag = True) OR (exception_flag = True), review_result, fallback_result ) ) ]
- final_step produces [final_result]
- output_workflow: [final_result]

需要声明所有常量。时间和配置方面给出合理的值。

注意: urgent_flag, allowed_flag 等虽然命名为flag, 但在G4中它们是Artifact类型。条件比较中使用 `= True` 比较的是这些Artifact的内容。但G4语法中比较只能比较constantName (即工作流中声明的常量), 不能写True作为右值? 实际上 formula 中的 comparison 是 term comparisonOp term. term可以是constantName或booleanLiteral. 所以可以写 (urgent_flag = True). 这些True是booleanLiteral. 注意: 布尔字面量是True/False. 条件中使用时, 假设urgent_flag的输出内容可以是布尔值. 这是合法的.

还需要声明常量: primary_name, review_name, fallback_name, triage_name, final_name (StepName); 相应Instruction常量; Agent/Executor常量.

指令(Instruction)需要用常量表示. 但Instruction概念需要是已知类型. 按语法const instruction_name: Instruction; 然后step_instruction(step) == instruction_name. Instruction的内容在实际运行时应该是配置在agent_config或者外部. 这里只能声明常量名, 具体prompt在实际运行前由外部提供或通过workflow输入? 不过按skill描述, Instruction是concept, 可以在workflow中声明为一个常量, 然后通过某种方式注入. 我们安全起见就声明常量.

同样需要声明Agent, Executor, Model, Engine, ApiBase等.

根据完整例子模式, 还需要声明review_model, review_engine, review_api, high_effort, read_tool等.

我尽量写完整但不用过于复杂. 使用合理的名字.

需要设定workflow_name, 比如 priority_routing.

注意: 在triage_step中, produces四个artifact. 语法上可以: produces(triage_step) == [urgent_flag, allowed_flag, review_flag, exception_flag]; 这是允许的.

条件表达式的嵌套需要正确括号. 我构建:

if(
  (urgent_flag = True) AND (allowed_flag = True),
  primary_result,
  if(
    (review_flag = True) OR (exception_flag = True),
    review_result,
    fallback_result
  )
)

注意在G4中, AND/OR可以用 & 或 and 等. 为了清晰用 AND 和 OR.

最终step consumes 这个if表达式.

现在编写完整文件.

考虑需要一个triager agent, primary/review/fallback agents, final agent. 每个step绑定一个executor (agent). 配置agent_config, allowed_tool (可选), reasoning_effort (可选).

为了演示, 我们给所有agent配置同一个model, engine, apiBase. 但需要声明常量.

完整常量列表:

const request: Artifact;
const urgent_flag: Artifact;
const allowed_flag: Artifact;
const review_flag: Artifact;
const exception_flag: Artifact;
const primary_result: Artifact;
const review_result: Artifact;
const fallback_result: Artifact;
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

const model: Model;
const engine: Engine;
const api: ApiBase;
const effort: ReasoningEffort;

-- 如果需要tool, 声明read_tool: Tool; 但可以省略allowed_tool.

现在编写workflow块.

数据流按上面.

还需要max_concurrency, workflow_timeout, step_timeout, max_attempts等配置. 可选但建议.

还要注意step_configuration: step_name, step_instruction.

最后output.

注意: 必须保证三个处理步骤都运行, 所以不能把它们放在if里, 它们必须声明为独立step, 有自己独立的produces, 并且被final_step的consumes引用if表达式. 这三个step的consumes都是[request], 所以它们相互独立, 可以并行.

另外, triage_step produce四个flag, 然后三个handler step只依赖request, 不依赖flag. 所以它们会在triage之前或同时? 按数据流, step之间的依赖由Artifact决定. 三个handler step只consumes [request], 并不consumes triage_step的产出. 所以它们可以和triage_step并行 (因为都依赖request). 但逻辑上应该先triage再决定? 但这里要求三个handler都必须实际运行, 所以可以并行. 最终step依赖if表达式中的flag, 所以最终step会在triage_step和至少一个handler step完成后执行. 依赖关系由运行时自动解决: 最终step的consumes中使用了urgent_flag等, 所以它依赖triage_step; 同时使用了primary_result/review_result/fallback_result, 所以它也依赖那些handler steps. 在if表达式中所有引用的artifact都被认为是依赖. 所以最终step会等待triage和所有三个handler完成. 完美.

现在编写.

另外, instruction内容不能是字符串, 而是常量名. 实际指令内容应在运行时通过命名常量传入. 在source中只需要声明名称.

代码块用 ```fusionflow 包裹.

确保语法: 使用==断言, 不用=在断言里. 条件表达式里用=.

注意: 按照grammar, 条件表达式是 formula -> comparison: term comparisonOp term. comparisonOp 包括 = (NUMERIC_EQ). 所以 (urgent_flag = True) 是valid.

现在写出完整代码.

注意: 注释以 -- 开头.

写完后检查一遍.

现在输出.