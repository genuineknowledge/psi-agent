We need to produce a FusionFlow G4 workflow. The user wants a specific inline `if` inside `consumes`, but acknowledges that if that is unsupported, we should silently correct to the supported form. According to the skill and grammar, the executable graph backend requires `if` to be a top-level named artifact selection, not placed inline inside `consumes`. The user says "silently correct it". So we must write the workflow in the correct, supported shape: declare a named artifact `selected_result` and assign it via `selected_result == if(preferred_flag = True, primary_result, fallback_result);`, then use `consumes(final_step) == [selected_result];`.

The workflow must include: request artifact, classifier step produces preferred_flag, primary and fallback handler steps both consume request and produce primary_result and fallback_result, final step consumes the selected result and produces final_result. Must include all required declarations: artifacts, steps, step names, instructions, executors, agent configs, etc. Also need executor bindings, step names, instructions (presumably using declared StepName and Instruction constants). The user says "include every required declaration, executor binding, StepName value, and Instruction value." So we need to declare constants for step names and instructions, and assign them.

Also need input_workflow and output_workflow assertions.

We should follow the full-featured example pattern: declare artifacts, steps, step names, instructions, executors (agents), workflow block with DATA FLOW, EXECUTOR ASSIGNMENT, STEP CONFIGURATION, possibly AGENT CONFIGURATION and WORKFLOW CONFIGURATION. The user didn't specify timeouts or agent config details, but we can include minimal config. We need to declare an agent executor for each step. Use `agent_config` with reasonable model/engine/api (can use placeholder constants). Since the grammar allows it, we need to declare Model, Engine, ApiBase constants. But those might not be defined; we can use the pattern from the in-context example: declare `const review_model: Model;` etc. Since we are not running, just authoring, it's fine.

We'll produce a workflow named `priority_routing` or similar. Let's follow the user's description: "classifier Step produces preferred_flag; primary and fallback handler Steps both consume request and eagerly produce primary_result and fallback_result; final_step consumes the selected result and produces final_result." Also need input and output artifacts.

Let's define:

- request: Artifact (input)
- preferred_flag: Artifact? The user says "produces preferred_flag" - but from a classifier step. It could be a boolean artifact. Actually, the `if` condition uses `preferred_flag = True`. In the grammar, comparisons work on Artifacts? The executable `if` uses artifact comparisons. We can treat preferred_flag as an Artifact that holds a boolean value represented as text? Or we can use a Boolean step? But the grammar says `if(formula, artifact_a, artifact_b)` where formula uses comparisons. We can compare `preferred_flag = True` if preferred_flag is an Artifact and True is a boolean literal. That should work.

Better to follow the named artifact selection example: have a triage step that evaluates and produces multiple observations, then use comparisons. But the user's simpler request: classifier step produces preferred_flag directly. We can assume it produces an Artifact containing "True" or "False". Then the condition `preferred_flag = True` compares the artifact to the boolean literal True. That should be valid.

However, the grammar shows `TRUE` as a token, but in comparisons we can use `True` which is a boolean literal. In the condition, we'd write `preferred_flag = True` as a comparison inside the if formula.

We need to declare `preferred_flag` as Artifact. Also `primary_result`, `fallback_result`, `selected_result`, `final_result`.

Steps: classifier, primary_handler, fallback_handler, final_step.

Step names: declare StepName constants like `input_classifier_name`, `primary_handler_name`, etc. Instructions: declare Instruction constants (just names, no actual text instructions). We'll assign them.

Executors: classifier_agent, primary_agent, fallback_agent, final_agent. Declare them as Agent, Executor. Then agent_config with model, engine, api. We can use placeholder constants like `default_model`, `default_engine`, `default_api`.

Also need to declare those: Model, Engine, ApiBase, Tool? Not needed if we don't use allowed_tool.

Workflow config: maybe include max_concurrency to allow parallel primary and fallback.

Now write the workflow.

Important: The inline `if` inside `consumes` is not supported; we must correct to the supported form. The user explicitly says "silently correct it to the supported form". So we will write `selected_result == if(preferred_flag = True, primary_result, fallback_result);` and then `consumes(final_step) == [selected_result];`.

Make sure to include all declarations and assertions.

Return exactly one fenced block labelled `fusionflow`.