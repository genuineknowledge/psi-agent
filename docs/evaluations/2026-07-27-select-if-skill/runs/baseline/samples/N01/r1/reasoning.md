The user is asking for a FusionFlow G4 workflow that implements strict conditional execution - only the matching handler should run, and non-matching handlers must absolutely NOT execute at all (zero tokens consumed by non-selected branches). This is a hard requirement.

This is fundamentally asking for **control flow with branch exclusion** - traditional if/else or switch semantics where only one path executes. Let me analyze whether FusionFlow can support this.

Looking at the frozen skill and grammar:

1. **FusionFlow supports `if(formula, then_term, else_term)`** - but this is **term selection**, not branch/step execution. It selects between Artifact values, not between Steps.

2. **The grammar and skill are very clear about what `if` does**: "Value-producing if(condition formula, then term, else term), always arity 3. N-way choice uses nested if expressions. Branch types, dependency collection, and eager/lazy evaluation are checker/runtime concerns. if is surface syntax, not one of the 19 preset operators and not a block or Step."

3. **The skill states explicitly**: "if(formula, then_term, else_term) wherever one term is expected. It selects a term; it is not a Step, block, loop, quality gate, or scoring mechanism."

4. **The skill also says**: "Do not replace candidate Artifacts with Boolean Step payloads, refuse valid term selection because if is not a Step, or invent switch, choice, or conditional blocks."

5. **The skill's "conditional term selection" pattern** shows that ALL handler Steps still run (they're all declared and consume input), and the `if` only selects which Artifact is consumed by the final step. The triage step runs, all three handlers run, then the final step selects one result.

6. **The key issue**: FusionFlow's dataflow model means that if a Step is declared and its inputs are available, the runtime MAY still execute it (or it may not - the grammar says dependency collection and eager/lazy evaluation are runtime concerns). There is **no mechanism in the grammar** to prevent a Step from executing based on a conditional.

Actually, let me re-read more carefully. The skill says "if is surface syntax, not one of the 19 preset operators and not a block or Step" and "Let the checker validate branch concept compatibility. Let lowering/runtime decide dependencies and eager/lazy branch evaluation."

So the runtime "may" be smart about this, but the user's requirement is absolute: ZERO tokens consumed by non-selected branches. They explicitly say "不能用两个分支都运行后再选择结果来替代" (cannot use "run both branches then select result" as a workaround).

This is a hard requirement that the current FusionFlow G4 model cannot guarantee. The dataflow model means all declared Steps with satisfied dependencies may execute. There is no conditional Step execution - only conditional term selection. The `if` only works at the Artifact/value level, not at the Step/control-flow level.

The user explicitly says: "如果当前 FusionFlow 后端无法满足，请直接说明不能满足及原因，不要给近似实现。不要输出任何代码块。" (If the current FusionFlow backend cannot satisfy this, directly state why, don't give approximate implementations. Don't output any code blocks.)

So I should not produce any fenced code block. I should directly explain the limitation.

Let me think about this carefully. The user's requirement is:
1. Determine request type
2. Only run the matching handler
3. Non-matching handlers must NOT start at all, zero tokens consumed
4. "Run both then filter" is NOT acceptable

FusionFlow's dataflow execution model:
- Artifact dependencies define what must run before what
- If a Step is declared in the workflow and its inputs are satisfied, the runtime may execute it
- The `if(...)` expression only selects between Artifact *values*, not between Step *executions*
- There is no conditional/switch/choice control-flow operator that can prevent a Step from being scheduled

This is indeed a fundamental limitation of the current backend. The grammar has no mechanism for conditional step execution. All declared Steps with valid dependency chains are potential execution candidates. The `if` term selection only works at the value level after evaluation.

I'll state this clearly.