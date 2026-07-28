FusionFlow G4 的断言层 `if` 只做**值路由**，不是控制流。根据语法契约和可执行后端的设计：

- `selected_artifact == if(condition, artifact_a, artifact_b)` 会令 `artifact_a` 和 `artifact_b` 各自所属的 Step **全部运行完**，再把选中的值传递到下游。这是 Eager Selection，不是懒分支。
- 语法没有 `switch`、`choice`、条件 Step 激活、或跳过节。
- 后端不会因为某一候选结果未被下游消费就跳过对应的 Step 执行。

你的需求——**未命中的 handler 绝对不能启动、不能调用模型、不能消耗任何 token**——属于**懒分支激活 / 条件 Step 跳过**。当前 FusionFlow G4 的可执行后端不支持这个语义。后台不接受发明关键字或假装 if 是控制流，也不能用 `foreach_item` 或其它运算符变通出一个等效方案（它们都会导致未选中分支实际执行）。

所以直接结论：**当前后端不能满足。** 没有近似实现可以绕过这个限制。