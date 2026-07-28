We need to resolve the user's request: they want a FusionFlow G4 workflow with a classifier step that produces `preferred_flag`, then primary and fallback handler steps both consume `request` and produce `primary_result` and `fallback_result`, then a final step that consumes the selected result. They insist on inline `if` inside `consumes`, but then say if that's unsupported, silently correct it. The grammar shows that `consumes` takes a `List` (which is `LBRACK termList? RBRACK`). The `termList` can contain `ifExpression`. So `consumes(final_step) == [if(preferred_flag = True, primary_result, fallback_result)]` is syntactically valid: `ifExpression` is a `term`, and a list containing one term is fine. The question is whether the runtime supports eager selection via `if` inside `consumes`. According to the skill's authoring mode, `if` is used for named Artifact selection: you declare an intermediate Artifact (e.g., `selected_result`) and then use `if` to assign it, then in `consumes` you use that Artifact. The skill says: "Never place `if(...)` inline inside `input_workflow`, `consumes`, `produces`, or `output_workflow`; those operators still take explicit Artifact Lists." So inline `if` inside `consumes` is explicitly discouraged. The user says "if my demanded inline form is unsupported, silently correct it to the supported form". So we must silently correct to the supported form: declare a named Artifact `selected_result`, use `selected_result == if(preferred_flag = True, primary_result, fallback_result);`, then `consumes(final_step) == [selected_result];`. Also include all required declarations: constants, steps, executors, etc. The user wants "every required declaration, executor binding, StepName value, and Instruction value". We need to provide concrete StepName and Instruction values as constants. The instructio n is typically a string, but the grammar has `QUOTEDCONSTANTID` which is a restricted quoted ID (no whitespace/escapes). For instructions, we might need to provide a path to a file using `RELATIVE_PATH_ID` (e.g., `"./instructions/classify.md"`). But the user didn't specify the actual instructions; we can use placeholder string literals that are valid QUOTEDCONSTANTID (e.g., `"classify"`, `"handlePrimary"`, `"handleFallback"`, `"finalSummary"`). Those are valid restricted IDs. For StepName, we can use `"classifyStep"`, `"primaryHandlerStep"`, etc., also valid. We need to declare all constants as required. Let's write the workflow.

We'll model:

- `request` (Artifact)
- `preferred_flag` (Artifact) — produced by classifier
- `primary_result` (Artifact)
- `fallback_result` (Artifact)
- `selected_result` (Artifact) — intermediate
- `final_result` (Artifact)

Steps: `classifier_step`, `primary_handler_step`, `fallback_handler_step`, `final_step`.

We need to declare Executors (Agent+Executor) for each step. We'll use `classifier_agent`, `primary_agent`, `fallback_agent`, `final_agent`. Also need `Model`, `Engine`, `ApiBase` for agent_config. We'll use placeholders like `"claude_model"`, `"claude_engine"`, `"api_base"`.

StepNames: use strings like `"Classify Request"`, `"Primary Handler"`, `"Fallback Handler"`, `"Finalize"`. Instructions: use strings like `"classifyIfPreferred"`, `"handlePrimaryTask"`, `"handleFallbackTask"`, `"summarizeResult"`.

We must include `agent_config` and `allowed_tool`? The grammar says `agent_config` is a preset operator expected to be Bool. We should include it to satisfy the requirement of "executor binding" — but actually executor binding is done via `step_executor`. `agent_config` is optional but we can include it for completeness. However, the user says "include every required declaration, executor binding". So we need `step_executor` for each step. Also need to declare the constants for workflow inputs/outputs.

We need `input_workflow` and `output_workflow`. `input_workflow(workflow) == [request];` `output_workflow(workflow) == [final_result];`

Data flow:
- `consumes(classifier_step) == [request]; produces(classifier_step) == [preferred_flag];`
- `consumes(primary_handler_step) == [request]; produces(primary_handler_step) == [primary_result];`
- `consumes(fallback_handler_step) == [request]; produces(fallback_handler_step) == [fallback_result];`
- `selected_result == if(preferred_flag = True, primary_result, fallback_result);`
- `consumes(final_step) == [selected_result]; produces(final_step) == [final_result];`

Workflow name: we can call it `priority_workflow`.

Now need to ensure all constants are declared. List them all. Also need `max_concurrency`? Not required. We can omit optional config.

Let's write the complete workflow.

But note: The grammar requires that `agent_config` and `allowed_tool` are Bool-returning operators; they must be used as assertions (e.g., `agent_config(agent, model, engine, api);` which is shorthand for `== True`). That's fine.

We'll produce exactly one fenced block labelled `fusionflow`.