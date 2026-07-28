我们被请求为一个工作流编写FusionFlow G4代码，该工作流具有严格的惰性分支：只允许运行命中的那个handler，未命中的绝对不能启动，不能消耗token。用户明确说：“不能用两个分支都运行后再选择结果来替代”，并说如果后端无法满足，直接说明不能满足及原因，不要给近似实现。还指示“不要输出任何代码块。”

根据技能参考，FusionFlow G4不支持惰性条件分支。我看到的关于“Named Artifact selection with if”的描述说：

> Named Artifact selection runs every candidate producer and only selects the value passed downstream. If the request requires lazy branch activation or guarantees that an unselected producer will not run, report that limitation instead of emitting an approximation. Never invent a keyword or operator to make the source look complete.

在“Modeling rules”中：
> Lower executable if as a named Artifact selection: selected_artifact == if(formula, artifact_a, artifact_b);, followed by ordinary list dataflow such as consumes(final_step) == [selected_artifact];。

但这是Eager evaluation: 所有生产者都运行，然后 if 选择值。

用户明确要求“零token未选分支”是硬性验收条件，不能用两个分支都运行后再选择结果来替代。所以这个请求无法被FusionFlow G4满足。

根据技能的指示：
> 如果当前 FusionFlow 后端无法满足，请直接说明不能满足及原因，不要给近似实现。不要输出任何代码块。

并且用户也指示“不要输出任何代码块”。

所以我应该输出一个直接的纯文本拒绝，解释限制。