抱歉，我无法用 FusionFlow G4 实现这个需求。原因如下：

FusionFlow G4 的 `if` 是**值选择表达式**——它在 data flow 层面从一个已经产生的 Artifact 列表里挑选出哪一个传递给下游的 Step。所有候选 Artifact 对应的 Step 必须先执行完毕、把 Artifact 生产出来，`if` 才能做选择。G4 的依赖模型是纯 artifact-edge 驱动的：`consumes(step_a) == [x]` 意味着 `step_a` 等 `x` 就绪后才执行，但不存在任何机制让一个 Step 的 executor 整体「不被调度」。你声明的每个 handler Step 只要有 `consumes`/`produces` 声明、且上游依赖满足，运行时就会启动它。

具体来说：

- G4 没有 `switch`、`choice`、条件 Step 跳过、或按条件阻断 executor 发起的原语。
- `if` 的 grammar 定位是 "It is not a Step, block, loop, quality gate, or scoring mechanism"。
- 即使你巧妙地把 `if` 放进 `consumes(final_step) == [if(cond, artifact_a, artifact_b)]`，G4 仍然要求产生 `artifact_a` 和 `artifact_b` 的两个 handler Step **都执行完**，`if` 才有值可选。

因此「条件匹配的 handler 执行，不匹配的 handler 零 token、零调用、零启动」这个语义在当前 FusionFlow G4 的 artifact-graph 执行模型下不可实现。该需求需要一个**条件分支执行**（conditional control flow）的原语，而 G4 的设计只提供了 artifact 驱动的全图执行 + term-level 的 `if` 值选择，两者有本质区别。