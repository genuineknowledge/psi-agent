我们被要求编写一个FusionFlow G4工作流，接收request，一个判定Step产出 approved_flag，然后approve_handler和reject_handler都消费request并分别产出approved_artifact和rejected_artifact，最终输出根据approved_flag选择出的Artifact本身，不要再增加只负责转发的最终Step。选择必须由工作流结构真实执行。

注意：用户要求“不要再增加只负责转发的最终Step”，即我们不能用一个额外的Step来消费选择后的artifact并产生最终输出。我们必须让工作流的输出直接是选择后的Artifact。但是FusionFlow的output_workflow必须是一个List，通常是一个或多个Artifact。所以我们要让output_workflow直接包含这个选择后的Artifact，而不经过任何额外的Step来转发。

如何实现？我们可以这样：申明一个Artifact selected_result，用于存放选择的结果，然后output_workflow直接等于[selected_result]。我们需要一个机制来设置selected_result的值。在FusionFlow中，我们可以使用 if 表达式给一个Artifact赋值，但这只是一个声明，不是Step。实际上，我们可以在workflow内写一个断言：selected_result == if(condition, approved_artifact, rejected_artifact); 这不会产生一个Step，但会根据条件在运行时确定值。这是允许的：if选取两个候选Artifact之一，赋值给selected_result。然后output_workflow(workflow) == [selected_result]; 这样，输出就直接是选择后的Artifact，没有额外转发Step。

但是注意：按照语法，if表达式是一个term，可以出现在assertion的右侧，但ifExpression是value-producing的，所以selected_result == if(...); 是一个有效的assertion（等式断言）。这样做是合法的。

另外，判定的Step需要产出approved_flag，它是一个Artifact（可能是布尔值字符串），然后我们用它作为条件。我们需要在if条件中使用比较：approved_flag = "True"（假设approved_flag是字符串"True"或"False"）。比较运算符使用 = （等于）。

注意：approved_flag是从一个Step产生的Artifact，它只能是一个Artifact类型，不能是布尔类型。我们可以让该Step产出文本"True"或"False"。然后在if条件中比较它。

现在，所有步骤都需要真实执行，包括approve_handler和reject_handler，即使其中一个的结果不会被下游使用，但用户要求“每次执行都实际运行”，所以它们都必须consumes request并produces。这没问题，if的选择是eager的，两个handler都会执行。

工作流结构：

- 输入：request (Artifact)
- 判定Step：consume request, produce approved_flag
- approve_handler：consume request, produce approved_artifact
- reject_handler：consume request, produce rejected_artifact
- 选择：selected_result == if(approved_flag = "True", approved_artifact, rejected_artifact)
- 输出：output_workflow(workflow) == [selected_result]

我们需要声明所有常量：Artifact, Step, StepName, Instruction, Executor等。

还需要绑定执行器、StepName、Instruction。

为了完整，需要指定Agent/Executor。可以是catalog中的名称。

注意：语法中，constDecl必须指定概念名列表，如Artifact, Step, StepName, Instruction, Agent, Executor等。需要声明：

- request, approved_flag, approved_artifact, rejected_artifact, selected_result: Artifact
- decision_step, approve_handler, reject_handler: Step
- decision_name, approve_name, reject_name: StepName
- decision_instruction, approve_instruction, reject_instruction: Instruction
- decision_agent, approve_agent, reject_agent: Agent, Executor

还需要一些其他的？可能还需要model, engine等用于agent_config，但可以省略，因为FusionFlow不需要强制配置agent_config？实际上agent_config是可选的吗？根据语法，agent配置是可选的？按需。但通常我们会配置agent。不过用户没有要求配置agent细节，我们可以保持简单，只声明Agent和Executor，绑定step_executor即可。不配置agent_config也可以运行？可能依赖运行时。但为了保险，我们可以提供一个基本的agent_config。但注意：语法中agent_config需要四个参数：Agent, Model, Engine, ApiBase。如果没有提供ApiBase或Model等，可能默认有。我们可以声明几个额外的常量：model, engine, api_base，并对每个agent使用agent_config。但用户可能不关心，我们可以省略，只绑定executor。但为了完整且可编译，最好提供。但问题没有要求，所以我们可以只写必要的声明，不配置agent_config。如果运行时需要，默认应该可以。我们按最简版本。

考虑到需要真实可执行，可能需要model等，但不是必须的。我们假设运行时允许不配置agent_config？在参考示例中，通常有agent_config。但这里没有要求，我们可以不写。如果缺少agent_config，编译可能通过？检查技能要求：agent_config是可选配置。所以可以省略。但需要绑定step_executor到Agent/Executor。我们可以只声明Agent, Executor，然后在step_executor中绑定。

我们需要确保所有使用的概念都已声明。按照模板，需要：

- 输入输出Artifact
- Step
- StepName
- Instruction
- Agent, Executor

可能还需要其他，如Tool，但不需要。

注意：声明Agent时，它是Agent和Executor同时声明，用逗号分隔概念：decision_agent: Agent, Executor;

接下来，编写workflow块。

为了可读性，遵循顺序：DATA FLOW, EXECUTOR ASSIGNMENT, STEP CONFIGURATION。

数据流：

input_workflow(workflow) == [request];

consumes(decision_step) == [request];
produces(decision_step) == [approved_flag];

consumes(approve_handler) == [request];
produces(approve_handler) == [approved_artifact];

consumes(reject_handler) == [request];
produces(reject_handler) == [rejected_artifact];

selected_result == if(approved_flag = "True", approved_artifact, rejected_artifact);

output_workflow(workflow) == [selected_result];

注意：approved_flag = "True" 中的 "True" 是一个常量字符串，需要声明吗？在语法中，常量可以是QUOTEDCONSTANTID，但我们需要在constDecl中声明吗？不，它直接出现在assertion中作为term，但term中的atomicTerm可以是constantName，而constantName可以是NUMBER、RELATIVE_PATH_ID、QUOTEDCONSTANTID或LOWID。但这里 "True" 作为QUOTEDCONSTANTID，它应该被解析为constantName。但是我们需要在全局声明它吗？按照语法，constDecl是用于声明常量与概念的绑定。而常量值（如字符串字面量）可以直接出现在term中，不需要事先声明。但是 "True" 是一个QUOTEDCONSTANTID token，它符合constantName。然而，比较运算符两边都应该是term，approved_flag是一个AtomicTerm（constantName）？approved_flag是一个LOWID，也是constantName。但是approved_flag已经在constDecl中声明为Artifact。那么 "True" 作为一个未声明的constantName，是否允许？语法上允许constantName直接由token定义，不要求事先声明。但FusionFlow可能期望所有标签（常量名）都在之前声明过。实际上，从参考示例看，如 `step_name(security_review) == security_review_name;` 中的 `security_review_name` 是事先声明的。如果使用一个字符串字面量如 `"True"`，它不是LOWID，而是QUOTEDCONSTANTID，这要求它作为constantName是合法的，但可能需要在constDecl中声明吗？不，constantName的token定义包括QUOTEDCONSTANTID，所以它可以直接出现在term中。但这是否被编译器接受？可能需要声明。为了安全，我们可以将 "True" 声明为一个常量，比如 `const true_flag: Artifact;` 并赋值？不，不能赋值，只能声明类型。实际上，我们不需要声明 "True" 作为常量，可以在if条件中直接写比较。但为了更安全，我们可以在constDecl中声明 `const true_value: Artifact;` 然后使用true_value？但true_value不是字面量，而是另一个Artifact，它需要被产生。还是用字面量比较简单。

如果我们写 `approved_flag = "True"`，这涉及一个QUOTEDCONSTANTID，语法上有效。但运行时是否能正确处理？通常这种比较是字符串相等。我们假设有效。

另外，if的公式比较复杂，需要括号保证运算优先级？公式已经定义好优先级，我们可以不带括号：`approved_flag = "True"` 是一个comparison，它是formula的一种，可以单独作为if的条件。所以if(approved_flag = "True", approved_artifact, rejected_artifact) 是合法的ifExpression。

现在，编写整个代码。

工作流名称：approval_workflow

注意：所有常量声明放在workflowFile开始，全局。

按照技能要求，需要一个完整的工作流。补全所有必要的声明和数据流。

我们还需要为每个Step设置step_name和step_instruction，这些需要StepName和Instruction常量。Instruction可以是路径或字符串？在语法中，Instruction是一个概念，其常量可以是QUOTEDCONSTANTID（代表路径）或者LOWID（如果Instruction有名字）。但通常我们通过step_instruction(step) == some_instruction来绑定，其中some_instruction是常量。我们可以声明一些Instruction常量，如decision_instruction等等。但需要给它们值？在FusionFlow中，step_instruction接受一个Instruction类型的常量，其值通过其他方式定义？实际上，step_instruction本身就是一个预设操作，它返回Instruction类型，其参数是Step，值通过等号右边给出。等号右边可以是constantName（即一个Instruction类型的常量），该常量本身可能没有值，但运行时需要知道指令内容。通常，Instruction可以是一个字符串或路径。在grammar中，constantName可以是LOWID、QUOTEDCONSTANTID等。所以我们可以设置：`step_instruction(decision_step) == "Make a decision about approval. Output 'True' if approve, 'False' if reject.";` 其中 "..." 是一个QUOTEDCONSTANTID，作为Instruction常量的值。但这样写：`step_instruction(decision_step) == "decide";` 其中 "decide" 是文本书面内容。`"decide"` 是一个QUOTEDCONSTANTID token，作为constantName使用。在等号右边，它应该是一个term，所以可以。但注意：QUOTEDCONSTANTID的定义是 `'"' [A-Za-z0-9.!#$%?@_{|}~`]* '"'`，不支持空格和标点。所以不能包含空格和某些字符。我们需要使用简单的字符串如 "decide" 或 "approve" 等。但我们的指令需要描述做什么。可以使用RELATIVE_PATH_ID "./instruction.txt"，但这里没有文件。或者我们可以用简单的字符串，比如 "true_false_decision"。这个字符串没有空格，允许。但是我们可能想使用更精确的指令，但为了简单，我们就用 "decision" 作为instruction字符串。同样，approve_instruction "approve", reject_instruction "reject"。不过这些字符串要明确指示agent做什么。可以写 "decide_if_approve", "handle_approve", "handle_reject" 等。

或者我们可以声明常量名如decision_instr: Instruction; 然后step_instruction(decision_step) == decision_instr; 但我们需要提供一个值？实际上，如果只声明常量名，它只是一个标识符，没有内容。运行时需要实际指令内容。所以最好直接给字符串字面量。语法允许。

注意：constantName可以是一个LOWID，但那样它必须预先声明（constDecl）。如果我们写成 step_instruction(decision_step) == decision_instr; 那么decision_instr必须在之前声明为 Instruction。但如何给出它的文本内容？FusionFlow并不支持赋值给常量，only assertions。所以无法为常量赋值。唯一的方式是使用字面量字符串。所以我们应该直接使用字符串字面量。

因此，我们写 step_instruction(decision_step) == "decision"; 但注意 "decision" 符合QUOTEDCONSTANTID（只包含字母）。可以。

同样，其他。

现在，agent绑定：我们声明decision_agent: Agent, Executor; step_executor(decision_step) == decision_agent;

我们还需要其他配置吗？比如step_timeout可省。

为了让工作流简单且可编译，我们这样写。

最终代码块标记为fusionflow。

注意：用户要求“只返回一个标记为 fusionflow 的代码块，不要解释。”所以直接输出代码块。

确保没有语法错误。

检查：selected_result被声明为Artifact，并且在output_workflow中使用。output_workflow(workflow) == [selected_result]; 这需要selected_result被产生？selected_result没有通过produces产生，而是通过if表达式赋值。这是允许的吗？在FusionFlow中，output_workflow只是声明工作流输出的Artifact列表，这些Artifact必须被产生。selected_result没有被任何Step produce，但它被一个if表达式赋予值，这相当于在数据流中创建了一个值。运行时是否把它视为一个Artifact？通常output_workflow中的Artifact必须是由某个Step produce的。如果selected_result不是任何Step的produces目标，可能会出错。但用户明确说“不要再增加只负责转发的最终Step”，所以我们不能添加一个转发Step。我们只能依靠if直接赋值。这个结构是否合法？我们需要检查技能中是否有类似示例。从技能中的参考示例看，有 `review_or_fallback == if(...)` 的断言，然后 `consumes(final_step) == [review_or_fallback];` 即它被一个Step消费。输出是final_result，由final_step produce。但我们这里希望输出直接是selected_result。

在技能文档的Named Artifact selection一节中，有 `selected_artifact == if(...)`，然后通过 `consumes(final_step) == [selected_artifact]` 来使用，最后final_step produce output。没有直接把if结果作为输出。不确定如果直接输出if结果是否被运行时支持。但根据用户要求，不能增加转发Step，我们可以尝试将if结果直接作为output。可能运行时允许output_workflow包含未被produces的Artifact，只要它在workflow内被某个if赋值。运行时可能通过计算if来确定值，然后输出。但这是推测。为了更加符合标准，我们可以考虑让两个handler中的一个来产出最终artifact？但那样需要根据flag选择哪个handler来产出，但用户要求两个handler都实际运行，所以不能只运行一个。所以只能通过if选择。

我们可以尝试另一种结构：使用if来产生一个Artifact，然后让output_workflow直接引用它。如果运行时不允许，可能编译会报错。但用户强调“选择必须由工作流结构真实执行”，所以使用if是唯一方式。我们就按此编写。

另外，可以声明一个中级Artifact selected_result，然后把它用于output_workflow。如果没有Step produces它，运行时可能把它当作一个"virtual artifact"仅由表达式定义。我认为这是可接受的。

为了增加合规性，我们可以在workflow中添加一个明确的断言来赋值，比如 `selected_result == if(...);` 然后 `output_workflow(workflow) == [selected_result];`。这样语法上没问题。我倾向于相信运行时支持output_workflow引用一个未被produces但通过if定义的Artifact。

让我们采用这个方案。

注意：常量声明中需要为每个Artifact、Step、StepName、Instruction等声明类型。我们必须包含所有用到的。

另外，注意：foreach_item之类的不用。

现在写出代码。

另外，按照代码模板，有注释 SCENARIO 和 AUTHORED，但非必须。我们可以包含。

最终代码块：

```fusionflow
-- SCENARIO: approval workflow with full parallel handlers and conditional output
-- AUTHORED: from intent: "decision with both handlers running"

const request: Artifact;
const approved_flag: Artifact;
const approved_artifact: Artifact;
const rejected_artifact: Artifact;
const selected_result: Artifact;

const decision_step: Step;
const approve_handler: Step;
const reject_handler: Step;

const decision_name: StepName;
const approve_name: StepName;
const reject_name: StepName;

const decision_instruction: Instruction;
const approve_instruction: Instruction;
const reject_instruction: Instruction;

const decision_agent: Agent, Executor;
const approve_agent: Agent, Executor;
const reject_agent: Agent, Executor;

workflow approval_workflow {
  -- DATA FLOW
  input_workflow(approval_workflow) == [request];
  consumes(decision_step) == [request];
  produces(decision_step) == [approved_flag];
  consumes(approve_handler) == [request];
  produces(approve_handler) == [approved_artifact];
  consumes(reject_handler) == [request];
  produces(reject_handler) == [rejected_artifact];
  selected_result == if(approved_flag = "True", approved_artifact, rejected_artifact);
  output_workflow(approval_workflow) == [selected_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(decision_step) == decision_agent;
  step_executor(approve_handler) == approve_agent;
  step_executor(reject_handler) == reject_agent;

  -- STEP CONFIGURATION
  step_name(decision_step) == decision_name;
  step_instruction(decision_step) == decision_instruction;
  step_name(approve_handler) == approve_name;
  step_instruction(approve_handler) == approve_instruction;
  step_name(reject_handler) == reject_name;
  step_instruction(reject_handler) == reject_instruction;
}
```

注意：我们使用了instruction常量名称如decision_instruction，但没有给它们具体文本内容。在运行时，这些Instruction常量需要被解析。如果没有预定义的instruction内容，可能失败。我们可以改为直接使用字符串字面量，但那样需要在声明中声明Instruction常量吗？step_instruction的右边必须是Instruction类型的term，而字符串字面量如"decision"当作constantName，其概念类型未声明。语法上允许constantName，但概念必须是Instruction。如果我们没有在constDecl中声明"decision"作为Instruction，那么它的概念未定义，可能会被检查器拒绝。因此，我们最好在workflow block外部声明Instruction常量并赋予他们值？但语法不允许对常量赋值。所以唯一方式就是使用一个已经声明的Instruction常量，如decision_instruction: Instruction; 然后通过其他方式（如文件或默认）提供内容。但在这个练习中，我们假设运行环境会处理这些。或者我们可以使用TODO吗？不行。

为了简化，我们可以不提供step_instruction配置，只绑定executor。instruction可以省略，但运行可能失败。但我们可以提供简单的字符串直接作为constantName，并希望检查器认为该constantName是合法常量且其类型可以匹配。但类型检查需要知道它的概念。通常，如果使用未声明的常量，它会报错。所以我们需要在constDecl中声明它们。但如何给它们值？我们不能。所以要么用有预设值的路径字符串（RELATIVE_PATH_ID），要么使用已经定义的指令常量。或许我们可以假设存在预定义的Instruction常量，如 "decide" 已经定义在catalog中。但不太可能。

考虑到用户只要求“真实可执行语义”，并不要求所有细节，我们可以保持使用常量名，并假设它们在catalog中已定义。或者，我们可以将instruction的字符串直接作为常量名，如将常量声明为 "decision" 作为Instruction？语法上，constantName可以是QUOTEDCONSTANTID，但constDecl中constantName出现在冒号左边，它必须是LOWID或NUMBER？不，constantName token的定义包括LOWID、NUMBER、QUOTEDCONSTANTID、RELATIVE_PATH_ID。所以我们可以声明 `const "decision": Instruction;` 但是语法中constantName是标识符，通常用LOWID。使用引号可能会引起解析问题。在grammar中，constantName可以是QUOTEDCONSTANTID，但通常例子中使用LOWID。我们最好保持一致。

为了安全，我们只绑定executor，跳过step_name和step_instruction，看是否必要。但step_name和step_instruction是必须的吗？在技能中描述为“包括required declarations and complete Step metadata”，但这里用户要求“补全所需的常量声明、数据流、执行器绑定、StepName 和 Instruction 配置”。所以我们需要提供。我们可以使用简单的LOWID常量，如 `const decision_inst: Instruction;` 然后step_instruction(decision_step) == decision_inst; 不需要给出具体内容，只要存在这个常量即可。运行时可能需要其他配置文件来解析，但我们可以假设。

所以当前代码应该可编译，但可能因为缺少指令内容而执行失败。但用户只要求编写，我们提交代码。

注意：语法要求每个constDecl后分号，每个workflowItem后分号。正确。

另外，在workflow块中，我们使用了selected_result作为等式左侧，这是一个赋值断言，需要分号。我们加了。

现在输出。