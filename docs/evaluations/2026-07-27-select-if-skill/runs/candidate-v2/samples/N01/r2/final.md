当前 FusionFlow G4 后端无法满足「零 token 未选分支」的硬性验收条件。

原因：FusionFlow 的 `if` 选择机制是 **eager value routing**（贪婪值路由）—— 所有候选 producer 都会先执行，然后 `if` 才从已产生的值中选一个往下传递。这保证不了「未命中的 handler 不启动、不调模型、不消耗 token」。

Skill 中已明确说明：

> Selection is eager: every candidate producer runs before the selected value is published.

因此，无法通过 FusionFlow 的 `if` 或任何已声明的 preset operator 来实现惰性分支激活。我没有隐藏的「条件跳转」或「switch」原语，也不能编造一个来绕过这个限制。如果您需要真正的惰性条件路由（只有条件判断结果所在的分支执行，其他分支完全不启动），这个 backend 目前做不到。建议寻找支持显式条件分支（如 Mixture-of-Agents 框架、外部编排语言中的分支控制）的运行时。