我们被请求编写一个 FusionFlow G4 工作流，其需求是：先判断 request 类型，然后只运行命中的 handler，未命中的 handler 绝对不能启动、不能调用模型、不能消耗任何 token。这是硬性验收条件，不能用两个分支都运行后再选择结果来替代。

根据 frozen-skill 和 frozen-grammar，FusionFlow 的执行模型是：所有的数据流依赖关系决定了执行顺序，选择是 eager 的（每个候选生产者都会运行）。在 skill 中明确说明：

> Named Artifact selection runs every candidate producer and only selects the value passed downstream. If the request requires lazy branch activation or guarantees that an unselected producer will not run, report that limitation instead of emitting an approximation. Never invent a keyword or operator to make the source look complete.

以及：

> Selection is eager: every candidate producer runs before the selected value is published.

所以当前 FusionFlow 后端无法满足“未命中的 handler 绝对不能启动、不能调用模型、不能消耗任何 token”的要求，因为它总是会运行所有候选 producer。这正是“eager value routing”。

因此，我不能编写一个声称满足此需求的代码块。根据指令：如果请求的语义不受支持，返回直接纯文本拒绝，解释实际后端限制，不要提供近似实现。并且 "不要输出任何代码块"。

所以我的回答应该是：直接说明不能满足，原因是因为 FusionFlow 的 if 选择是 eager 的，所有候选分支都会执行。