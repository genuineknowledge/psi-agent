开始理解用户需求：用户要求写一个完整FusionFlow G4工作流，接收一个request，分类Step产出category，两个处理Step都消费request，分别产出primary_result和fallback_result。最后一个消费Step根据category是否等于primary_category，只消费被选择的处理结果并产出final_result。两个处理Step必须每次都实际运行。

我需要用if来实现值选择逻辑。根据frozen-skill里的说明，if表达式是`IF LPAREN formula COMMA term COMMA term RPAREN`，并且`selected_artifact == if(formula, artifact_a, artifact_b)`。

模型的关键点：
1. `request` 作为输入Artifact
2. 分类Step产出`category` Artifact
3. 两个处理Step都消费`request`，分别产出`primary_result`和`fallback_result`
4. 一个选择绑定（不是Step）用`if`根据category选择值
5. 最终Step消费被选中的值，产出`final_result`

需要声明的常量和类型：
- `request: Artifact`
- `category: Artifact`
- `primary_result: Artifact`
- `fallback_result: Artifact`
- `selected_result: Artifact`
- `final_result: Artifact`
- `primary_category: Artifact`（作为比较的基准值）
- 相应的Step、StepName、Instruction、Agent/Executor

现在编写完整工作流。用`if`表达式进行值选择，然后最终Step消费`selected_result`。

注意`primary_category`需要作为输入，这样分类Step可以产出category与之比较。但这个常量其实应该是一个引用值，不一定是输入——根据语法比较的是`Artifact`和`Artifact`，所以需要一个输入或者一个常量来表达这个基准值。最好把它作为工作流输入。

但用户说接收一份request，没提到primary_category输入。我可以把primary_category作为工作流的一个输入常量（输入Artifact），这样分类逻辑可以比较。

让我重新考虑：category是一个Artifact，primary_category也是一个Artifact（输入），分类Step产出category，然后if比较`category = primary_category`。

先列出所有需要声明的内容并构建完整的工作流。

注意：根据grammar，`if`是在term中使用的，不是独立断言。`selected_result == if(formula, primary_result, fallback_result)`是合法的。

另外，`consumes(final_step) == [selected_result]`是合法的dataflow关系。

注意数据流顺序：consumes和produces应该按依赖顺序排列。

现在开始写完整代码。我需要包含：
- 全局const声明
- workflow声明
- DATA FLOW部分
- EXECUTOR ASSIGNMENT部分
- STEP CONFIGURATION部分

对于WORKFLOW CONFIGURATION和AGENT CONFIGURATION，虽然没有要求，但为了完整性可以包含，或者根据用户需求。用户说"补全所需的常量声明、数据流、执行器绑定、StepName 和 Instruction 配置"，所以就这些部分。

数据流部分：
- input_workflow：request, primary_category
- 分类Step：consumes(request), produces(category)
- 处理Step1：consumes(request), produces(primary_result)
- 处理Step2：consumes(request), produces(fallback_result)
- if选择：selected_result == if(category = primary_category, primary_result, fallback_result)
- 最终Step：consumes(selected_result), produces(final_result)
- output_workflow：final_result

注意primary_category也是输入，否则无法比较。

考虑用户说的"只消费被选择的处理结果"，所以最终Step只消费selected_result，没问题。

现在编写完整代码。