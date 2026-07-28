The user is asking for a FusionFlow G4 workflow that has conditional branching where only the selected handler runs, and the unselected handler must NOT run at all (zero tokens consumed on the unselected branch). This is a hard requirement - they want true conditional execution, not a fan-out + fan-in pattern where both branches run and then a result is selected.

Let me think about whether FusionFlow G4 can support this.

Looking at the grammar and skill documentation:

1. FusionFlow G4 has `if(formula, then_term, else_term)` for term selection in data flow assertions like `consumes(final_step) == [if(formula, artifact_a, artifact_b)]`.
2. The grammar explicitly states: "`if` is surface syntax, not one of the 19 preset operators and not a block or Step."
3. The skill says: "Let the checker validate branch concept compatibility. Let lowering/runtime decide dependencies and eager/lazy branch evaluation."

The key question is: does the FusionFlow backend support lazy/eager branch evaluation where only the selected Artifact's producing Step runs?

Looking at the conditional term selection example more carefully:

```fusionflow
consumes(final_step) == [
    if(
      (primary_observation = primary_criterion) AND !(block_observation = block_criterion),
      primary_result,
      if(...)
    )
  ];
```

In this example, the `if` is selecting which Artifact the final_step consumes. But critically, ALL four candidate Artifacts (primary_result, review_result, fallback_result) are produced by separate Steps that are declared with their own `consumes`/`produces` edges. The artifact dependencies mean ALL those branches must run before the final step can execute, because the runtime needs to know the actual values of the observations to evaluate the condition.

Wait - but the user's requirement is different. They want only ONE handler to run based on a classification done earlier. Let me re-examine.

In the conditional term selection example, the triage step produces observations, and then the `if` formula uses those observations to select which result to pass to the final step. But ALL handlers (primary, review, fallback) still run because they're declared as independent steps with their own artifact production edges.

For the user's requirement of "only the matched handler runs, unmatched handlers consume zero tokens", FusionFlow G4 has a fundamental limitation:

1. The `if` in FusionFlow is term selection - it selects which value (Artifact) passes to a downstream consumer.
2. The Artifact dependency model means that for a Step to produce an Artifact, the Step must execute.
3. There is no lazy or conditional Step execution in the grammar or documented semantics. Steps are executed when their consumed artifacts are available.
4. There is no `switch`, `choice`, `conditional step`, or any control-flow branching mechanism that would skip Step execution.

Looking at the conditional term selection model more carefully: in the `priority_routing` example, the triage step produces observations, and then primary_handler_step, review_handler_step, and fallback_handler_step ALL consume [incoming_case] - they all run because they all have declared edges. The `if` only selects which result passes to `final_step`, but all three handlers already ran.

So the user's requirement - true lazy/conditional execution where only one branch runs and the other branch consumes zero tokens - is NOT something FusionFlow G4 can support based on the grammar and documented semantics.

The grammar says: "Let lowering/runtime decide dependencies and eager/lazy branch evaluation." But this is about evaluation of the `if` expression's branches (which term to evaluate), not about skipping Step execution. And even then, the runtime would need the producing Step to have run to produce the Artifact value.

Actually, let me reconsider. Could the `if` in `consumes()` mean that the runtime only evaluates the condition and then only requires the selected branch's producing step? The dependency analysis could theoretically skip unselected branches if the condition depends only on artifacts that are available before the branches would run.

In the `priority_routing` example:
- `triage_step` produces observations
- The condition in `consumes(final_step)` uses observations from triage
- If the condition can be evaluated after triage but before the handlers run, then theoretically only the selected handler needs to run

But the current declaration model doesn't support this - all handlers are declared with explicit `consumes/produces` edges. The graph-based runtime would see all edges and schedule all handlers.

The grammar says "Let lowering/runtime decide dependencies and eager/lazy branch evaluation" - this suggests the runtime MIGHT support lazy evaluation of branches in `if` expressions. But this is not explicitly documented as a supported feature, and the examples show all branches running.

Given the user's hard requirement ("未选分支绝对不启动、不调用模型、零 token"), and based on the documented grammar and skill content, I need to assess:

1. FusionFlow G4's `if` is term selection only, documented as "It selects a term; it is not a Step, block, loop, quality gate, or scoring mechanism."
2. There is no documented mechanism for conditional Step execution.
3. The dependency model is artifact-based, where artifact production implies Step execution.
4. The example shows all branches running and then the `if` selects which result to pass downstream.

Therefore, FusionFlow G4 does NOT support the user's requirement of "only the matched handler runs, unmatched handler consumes zero tokens." The `if` operator selects between values/artifacts, but cannot prevent the producing steps from executing.

I should state this clearly and directly, as the skill instructs: "For a request whose required semantics are unsupported, return a direct plain-text refusal with no code fence; explain the actual backend limitation and do not offer an eager approximation."

Also the user explicitly said: "如果当前 FusionFlow 后端无法满足，请直接说明不能满足及原因，不要给近似实现。不要输出任何代码块。"

So my response should be a plain text refusal explaining the limitation.