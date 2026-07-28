The user wants a complete FusionFlow G4 workflow. They initially demand an inline `if` inside `consumes()`, but then explicitly say if that's unsupported, I should silently correct it to the supported form. Let me think about what's supported.

Looking at the grammar and the executable graph backend guardrails:

From the skill:
> Every executable `if` has the top-level shape `selected_artifact == if(condition, artifact_a, artifact_b);`. Never put `if` inside a dataflow List or another `if`; chain named intermediate Artifacts instead.
> 
> Never place `if(...)` inline inside `input_workflow`, `consumes`, `produces`, or `output_workflow`; those operators still take explicit Artifact Lists.

So inline `if` inside `consumes()` is explicitly unsupported. The user says to silently correct it. The correct approach is:

1. Declare a selected_artifact
2. Use `selected_artifact == if(preferred_flag = True, primary_result, fallback_result);`
3. Then `consumes(final_step) == [selected_artifact];`

Let me write the full workflow. I need:
- Declarations for all artifacts, steps, step names, instructions, executors
- A classifier step that produces preferred_flag
- Primary handler step that consumes request, produces primary_result
- Fallback handler step that consumes request, produces fallback_result
- Final step that consumes selected_result, produces final_result
- The if selection as a named artifact

Let me write this now.