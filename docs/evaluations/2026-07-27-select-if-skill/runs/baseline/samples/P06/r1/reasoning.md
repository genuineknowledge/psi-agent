我们被要求编写一个FusionFlow G4工作流。我们需要仔细阅读需求。

需求：
- 工作流接收 `request`
- 一个判断Step产出 `approved_flag` (应该是Artifact，名字叫approved_flag)
- approve_handler 和 reject_handler 都消费 `request`，每次执行都实际运行，每个都产生自己的artifact：`approved_artifact` 和 `rejected_artifact`
- 工作流最终输出应该是根据 `approved_flag` 选择出的那个Artifact本身（即要么是`approved_artifact`要么是`rejected_artifact`），不需要再增加一个只负责转发的最终Step。
- 选择必须由工作流结构真实执行。
- 补全所需的常量声明、数据流、执行器绑定、StepName和Instruction配置。

我们需要考虑如何在G4中实现“根据approved_flag选择artifact作为最终输出”。在G4中，`output_workflow`接受一个List。我们可以在List中使用`if`表达式来选择Artifact。

核心在于：我们有一个判断Step，产生approved_flag。然后两个处理Step都消费request，分别产生approved_artifact和rejected_artifact。然后`output_workflow`应该是一个List，里面根据approved_flag的值选择哪个artifact输出。

但是，`if(formula, then_term, else_term)`返回一个term。`output_workflow`期望一个List。我们可以把`if`放在列表里：`output_workflow(workflow_name) == [if(condition, approved_artifact, rejected_artifact)];`。这样是合法的，因为列表可以包含一个term，term可以是if表达式。

我们需要考虑如何表示条件：`approved_flag`是一个Artifact，我们如何比较它？可能需要将它和一个常量（比如True布尔值）进行比较？用户描述说approved_flag是Artifact，但具体内容是布尔值？我们可以通过`= True`来比较？但artifact中的内容不是直接在G4中可比较的。G4中的比较是term之间的，比如`approved_flag = true`？但true是布尔字面量。这取决于approved_flag的类型。用户没说具体内容，但我们可以假设approved_flag是一个Artifact，其内容是布尔值，或者我们将其与某个值比较。另一种方式：我们可以设计一个条件如`approved_flag = approved_artifact`？不合理。

更合理的做法：判定Step输出一个布尔值Artifact，然后我们使用`if(approved_flag = True, ...)`。但`True`在G4中是booleanLiteral，是term。`approved_flag`是一个constantName（Artifact），两者可以比较。但需要注意，`approved_flag = True`是一个比较表达式，返回布尔值，用在`if`的formula中。

但是，需求说“approved_flag”是一个Artifact。那么比较的内容是Artifact的值？在G4中，比较是两个term的相等性，如果Artifact的值恰好是布尔字面量，那么它可以与`True`比较。但G4的执行语义中，Artifact的值如何与True比较？实际上，我们需要依赖运行时将Artifact的内容转换为可比较的值。这可能要求approved_flag是一个布尔字面量Artifact。

也许更安全的做法是：判定Step输出一个布尔值Artifact，然后我们使用`if(approved_flag = approved_flag, approved_artifact, rejected_artifact)`？这没有意义。

另一种思路：我们可以使用`if(true, approved_artifact, rejected_artifact)`通过一个条件选择？但这样无法基于flag。

我们需要一个条件表达式，其结果是布尔值，用于选择。既然approved_flag是一个Artifact，我们可以把它与一个常量`"approved"`字符串比较？但所有字面量都是NUMBER或QUOTEDCONSTANTID。我们可以使用一个字符串常量`"approved"`。但approved_flag的内容必须是字符串。我们可以在判定Step的instruction中让它产生"approved"或其它。

我选择：判定Step产出approved_flag，其内容为字符串"approve"或"reject"。然后我们使用比较：`approved_flag = "approve"`。但"approve"是QUOTEDCONSTANTID吗？是的，引号内的字母数字。所以`"approve"`是一个合法的constantName（QUOTEDCONSTANTID）。那么我们可以声明一个常量`const approve_marker: Artifact;`并赋值？不，字面量不能直接赋值给Artifact。我们可以直接在比较中使用字符串字面量。

但`approved_flag`是Artifact，`"approve"`是QUOTEDCONSTANTID，它被解析为constantName。比较`approved_flag = "approve"`是否合法？两个都是term，一个是constantName（Artifact），一个是QUOTEDCONSTANTID（也是constantName），语法上可以。运行时需要理解如何比较Artifact和字符串？可能可以。

我决定采用：判定Step产出approved_flag（内容为"approve"或"reject"字符串），然后`output_workflow`使用`if(approved_flag = "approve", approved_artifact, rejected_artifact)`。

注意：比较运算符是`=`（NUMERIC_EQ），在比较中使用。

现在，设计工作流结构：

- 常量声明：`request`, `approved_flag`, `approved_artifact`, `rejected_artifact`, `final_output`（但最终输出直接用if，不需要final_output Artifact）
- Steps: `judge_step`, `approve_handler_step`, `reject_handler_step`
- 数据流：`input_workflow(workflow) == [request]`
- `consumes(judge_step) == [request]`; `produces(judge_step) == [approved_flag]`
- `consumes(approve_handler_step) == [request]`; `produces(approve_handler_step) == [approved_artifact]`
- `consumes(reject_handler_step) == [request]`; `produces(reject_handler_step) == [rejected_artifact]`
- `output_workflow(workflow) == [if(approved_flag = "approve", approved_artifact, rejected_artifact)]`

注意：`approve_handler`和`reject_handler`都消费`request`，这是两个并行的步骤，它们都实际运行（不被if跳过），以确保不管flag如何，两者都执行。运行时是否根据依赖关系执行？两者都依赖request，所以都会执行。没有条件跳过。输出时根据flag选择。

但我们需要确保approve_handler和reject_handler都有对应的executor、name、instruction等。

还有一点：用户说“不要再增加只负责转发的最终Step”，所以我们不能有额外step。直接用if在output_workflow中完成选择。

现在编写完整代码。

我们还需要提供一些默认的executor（Agent），配置agent_config。可以使用一个agent类型，但需要确保所有步骤都有executor。

我们可以声明三个agent：judge_agent, approve_agent, reject_agent。

还需要声明Model, Engine, ApiBase等concept。但skill中说“包括required declarations and complete Step metadata”，所以我们需要提供agent_config。

我将使用review_model, review_engine, review_api等通用配置。

另外，Instruction: 我们需要为每个step提供step_name和step_instruction。Instruction也是concept（Instruction），需要声明常量。我们声明三个Instruction常量，并在step_instruction中使用它们。

但Instruction的内容是什么？它需要是一个字符串，但G4中Instruction是一个concept，我们需要声明它，然后赋值？实际上，step_instruction(step) == instruction_constant; 而instruction_constant的类型是Instruction。我们需要构造它的值吗？通常Instruction是Opaque？我们可以用QUOTEDCONSTANTID作为它的值？但QUOTEDCONSTANTID被解析为constantName，它本身不是一个Instruction。在G4中，Instruction是一个概念，其值可以是路径或字符串？但是，根据grammar，Instruction是一个概念，但我们没有直接声明值的方式。通常，指令内容由外部提供，或者我们通过Workflow输入传递instruction。但这里用户要求补全Instruction配置，我们需要指定instruction。一种方法是使用相对路径指令文件：`"./instructions/judge.md"`这样的路径，这是一个RELATIVE_PATH_ID。这样可以指向文件内容。同样，对于approve和reject，提供路径。

我将假设这些instruction文件存在（无需实际存在，因为是虚构的工作流）。我们使用类似`"./instructions/judge.md"`。

同样，StepName: 我们声明三个StepName常量：`judge_step_name`, `approve_step_name`, `reject_step_name`。然后将它们赋给step_name。StepName是一个概念，我们如何定义值？可能也是通过QUOTEDCONSTANTID或LOWID？step_name需要一个StepName类型的term。我们可以声明`const judge_step_name: StepName;`，然后在step_name中使用它。但我们需要给它一个值？实际上，在G4中，`step_name(step) == judge_step_name;`只是将step与那个常量绑定，常量的值由外部决定。但我们也可以直接使用字符串字面量？`step_name(step) == "judge";`合法吗？`"judge"`是QUOTEDCONSTANTID，可以作为constantName，但类型是StepName？需要类型匹配。通常，我们声明一个StepName常量并直接使用。为了简单，我声明三个StepName常量，但不需要显式赋值，它们在概念上就是名称。

同样，Instruction：声明常量，但不需要赋值？在G4中，我们需要为instruction提供一个实际内容。我使用路径字符串作为Instruction的值。

另外，我们还需要为agent配置model、engine、api。我们声明通用的`common_model`, `common_engine`, `common_api`。

我们还需要初始化agent_config: `agent_config(judge_agent, common_model, common_engine, common_api);` 等。

允许的工具：不需要。

完整代码：

```fusionflow
-- SCENARIO: Decision-based routing with always-executed parallel handlers
-- AUTHORED: 2025-03-26 12:00:00 from intent: "工作流接收request；判定Step产出approved_flag；approve_handler和reject_handler都消费request并执行，最终输出根据flag选择artifact"

const request: Artifact;
const approved_flag: Artifact;
const approved_artifact: Artifact;
const rejected_artifact: Artifact;

const judge_step: Step;
const approve_handler_step: Step;
const reject_handler_step: Step;

const judge_step_name: StepName;
const approve_step_name: StepName;
const reject_step_name: StepName;

const judge_instruction: Instruction;
const approve_instruction: Instruction;
const reject_instruction: Instruction;

const judge_agent: Agent, Executor;
const approve_agent: Agent, Executor;
const reject_agent: Agent, Executor;

const common_model: Model;
const common_engine: Engine;
const common_api: ApiBase;

workflow decision_routing {
  -- DATA FLOW
  input_workflow(decision_routing) == [request];
  consumes(judge_step) == [request];
  produces(judge_step) == [approved_flag];
  consumes(approve_handler_step) == [request];
  produces(approve_handler_step) == [approved_artifact];
  consumes(reject_handler_step) == [request];
  produces(reject_handler_step) == [rejected_artifact];
  output_workflow(decision_routing) == [
    if(approved_flag = "approve", approved_artifact, rejected_artifact)
  ];

  -- EXECUTOR ASSIGNMENT
  step_executor(judge_step) == judge_agent;
  step_executor(approve_handler_step) == approve_agent;
  step_executor(reject_handler_step) == reject_agent;

  -- STEP CONFIGURATION
  step_name(judge_step) == judge_step_name;
  step_instruction(judge_step) == judge_instruction;
  step_name(approve_handler_step) == approve_step_name;
  step_instruction(approve_handler_step) == approve_instruction;
  step_name(reject_handler_step) == reject_step_name;
  step_instruction(reject_handler_step) == reject_instruction;

  -- WORKFLOW CONFIGURATION (optional, but to allow parallel handlers)
  max_concurrency(decision_routing) == 2;

  -- AGENT CONFIGURATION
  agent_config(judge_agent, common_model, common_engine, common_api);
  agent_config(approve_agent, common_model, common_engine, common_api);
  agent_config(reject_agent, common_model, common_engine, common_api);
}
```

注意：我们使用了`if(approved_flag = "approve", ...)`，其中`"approve"`是QUOTEDCONSTANTID。`=`是比较操作符，合法。

我们还设置了`max_concurrency(decision_routing) == 2;`，确保approve_handler和reject_handler可以并行执行（因为它们都依赖request，不互相依赖）。

我们没有引入额外的最终Step，符合要求。

现在，输出只需要一个标记为fusionflow的代码块。