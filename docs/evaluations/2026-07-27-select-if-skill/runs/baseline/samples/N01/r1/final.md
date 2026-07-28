当前 FusionFlow G4 后端无法满足这个需求，具体原因如下：

你的需求本质是**条件分支执行**（只有命中的 handler Step 才启动，未命中 Step 零启动、零 token）。而 FusionFlow G4 的执行模型是**数据流驱动**——所有被声明的 Step，只要其 `consumes` 的 Artifact 已就绪（即上游依赖已满足），执行引擎即可将其排入调度。工作流中的 `if` 表达式是**值级别（term）的二选一**，不是 Step 级别或控制流级别——它只能在运行时从多个已产生的 Artifact 中选择一个传给下游，但不能阻止未被选中的那个 Step 被调度和执行。

当前所有 19 个预设算子（`input_workflow` / `output_workflow` / `consumes` / `produces` / `foreach_item` / `step_executor` / `step_name` / `step_instruction` / `step_timeout` / `max_attempts` / `max_concurrency` / `workflow_timeout` / `agent_config` / `allowed_tool` / `max_output_tokens` / `temperature` / `reasoning_effort` / `max_turns` / `resource_requirement`）均不提供条件跳过 Step 执行的语义；`if` 是 parser 层面的三目表达式，不是第 20 个预设算子，不具备控制流能力。后端不存在任何语法结构或运行时机制来保证「A 分支执行当且仅当条件为真，B 分支完全不启动」。

如果你需要的是一套能确保「零 token 未选分支」的多 handler 路由，FusionFlow G4 目前在架构层面就不支持——这不是实现细节的缺失，而是执行模型（数据流无条件调度）与你的需求（条件控制流）之间的根本不匹配。