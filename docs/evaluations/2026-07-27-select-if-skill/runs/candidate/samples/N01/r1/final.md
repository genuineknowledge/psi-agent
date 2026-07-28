FusionFlow G4 后端无法满足这个需求。

当前 FusionFlow 的受控流机制只有 **Named Artifact Selection**（即 `if(条件, 结果A, 结果B)`），其语义是**两个分支都先运行，再选择其中一个值传给下游**——所有候选 Step 都会完整执行，不会跳过未选中的分支。这一设计在技能文档和语法规则中均有明确说明：「Every candidate producer runs. Selection is eager value routing, not lazy control flow.」

你要求的「未命中的 handler 绝对不能启动、不能调用模型、不能消耗任何 token」属于**惰性分支激活**（lazy branch activation），确保未选中的 Step 零执行、零开销。FusionFlow 没有提供任何运算符或机制来选择性启动某条分支而跳过其他分支；所有可能被 `if` 选到的 Step 都会在运行时实际执行。

因此，在这个后端的表达能力下，无法提供满足你验收条件的实现。我不会给出任何近似实现（例如用两个分支都跑然后选结果的方式来绕过），因为这直接违背了你明确的「零 token 未选分支」硬性要求。