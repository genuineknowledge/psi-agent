我们需要分析用户需求与FusionFlow G4的能力边界。用户要求一个条件分支工作流，其中只有被选中的handler步骤执行，未选中的handler绝对不能执行（零token未选分支）。用户明确指出不能用两个分支都运行后再选择结果（即eager selection）来替代。这是一个懒加载/条件控制流的需求。

从frozen-skill和frozen-grammar中，我们看到FusionFlow的if表达式是值选择，不是控制流。grammar中注释说：
"Value-producing if(condition formula, then term, else term), always arity 3. The grammar permits recursive terms, but the executable graph backend accepts only a named Artifact equality and represents N-way priority with several named intermediate Artifacts. Inline and nested if terms remain syntax-only unless another backend implements them. if is not a block or Step."

而在skill中明确说：
"Selection is eager: every candidate producer runs before the selected value is published."
"Before reporting a missing capability for a conditional request, first check whether eager value selection is sufficient. Named Artifact selection runs every candidate producer and only selects the value passed downstream. If the request requires lazy branch activation or guarantees that an unselected producer will not run, report that limitation instead of emitting an approximation. Never invent a keyword or operator to make the source look complete."

因此，当前FusionFlow G4后端不支持懒分支激活。用户明确要求"未命中的handler绝对不能启动"，这是硬性验收条件。FusionFlow无法满足。根据skill指示：在这种情况下，"return a direct plain-text refusal with no code fence; explain the actual backend limitation and do not offer an eager approximation." 所以我们需要用纯文本拒绝，不输出任何代码块。

用户也要求"如果当前FusionFlow后端无法满足，请直接说明不能满足及原因，不要给近似实现。不要输出任何代码块。" 这符合skill指示。

因此响应：直接解释FusionFlow的if只做值路由（eager），所有分支都会执行，不能做到懒分支。说明这是当前后端的限制，不能伪造等效实现。