We need to produce a valid FusionFlow G4 workflow that implements the described priority routing scenario. The requirements:

- Input: request (Artifact)
- Triage step produces: urgent_flag, allowed_flag, review_flag, exception_flag (all Artifacts)
- Three handler steps: primary, review, fallback - each consume request and produce their respective result artifacts. They must always run.
- Final step consumes the selected artifact (based on the conditional logic) and produces final_result.

Conditional logic:
- if urgent_flag = true AND allowed_flag = true -> selected = primary_result
- else if review_flag = true OR exception_flag = true -> selected = review_result
- else selected = fallback_result

We need to use `if(...)` expressions for these conditions. The grammar supports chained `if` expressions.

We also need to declare all necessary constants with correct concepts.

Let's design the workflow.

We'll need:
- `request` as Artifact
- `urgent_flag`, `allowed_flag`, `review_flag`, `exception_flag` as Artifact
- `primary_result`, `review_result`, `fallback_result` as Artifact
- `selected_result` as Artifact (intermediate for the selection)
- `final_result` as Artifact

Steps:
- `triage_step`: consumes request, produces the four flags
- `primary_handler_step`: consumes request, produces primary_result
- `review_handler_step`: consumes request, produces review_result
- `fallback_handler_step`: consumes request, produces fallback_result
- `final_step`: consumes selected_result, produces final_result

The conditional selection: We need to model `selected_result == if(condition1, primary_result, if(condition2, review_result, fallback_result))`.

Condition1: `(urgent_flag = True) AND (allowed_flag = True)`
Condition2: `(review_flag = True) OR (exception_flag = True)`

But careful: the grammar says `True` and `False` are boolean literals. But we need to compare an Artifact (flag) to True. The flags are Artifacts, but they hold boolean values. The comparison operator `=` is for numeric equality between terms (including Artifact names). So we can do `urgent_flag = True` - that's a comparison. And we can combine with AND/OR.

We also need to ensure that all handlers always run regardless of the selection. In G4, all steps are data-driven: a step runs if its inputs are available. Since all three handlers consume `request`, and `request` is input, they will all run in parallel before the conditional selection. The selected_result is then used by final_step. So the handlers won't be short-circuited; they all run. That matches the requirement "每次执行都必须实际运行".

Now, check the inlined example for the priority_routing pattern. It shows a similar pattern but with different criteria names. We can adapt.

We also need to declare the boolean literals? In the grammar, `True` is a reserved keyword/boolean literal that can be used in comparisons. So we can write `urgent_flag = True` directly.

We need to declare `True`? No, `True` is a boolean literal token. It's not a constant. So we can use it directly.

Now, let's write the full workflow, including data flow, executor assignment, step configuration. We'll use generic agent names and instructions.

Note: The user said "请补全常量声明、数据流、执行器绑定、StepName 和 Instruction 配置", so we need to provide all that, including the constants for StepName and Instruction.

We'll need to declare:
- `triage_name`, `primary_name`, `review_name`, `fallback_name`, `final_name` as StepName
- `triage_instruction`, `primary_instruction`, `review_instruction`, `fallback_instruction`, `final_instruction` as Instruction
- `triage_agent`, `primary_agent`, `review_agent`, `fallback_agent`, `final_agent` as Agent, Executor

And agent config: we need to define some model/engine/api constants. We can use placeholders: `default_model`, `default_engine`, `default_api`. But the user didn't specify a particular backend. We should declare them as Model, Engine, ApiBase and then use them in agent_config. Similarly for tools? Not strictly required.

Let's produce the output. We'll follow the template from the skill: group assertions by concern order. We'll omit comments like SCENARIO: but we can include a brief scenario comment.

Make sure to use proper syntax: all statements end with semicolon; dataflow operators use Lists; `if` is an expression assigned to a constant via `==`.

One nuance: the `if` expression returns an Artifact. So we do `selected_result == if(condition, primary_result, else_expression);`. That's a value-producing assertion (the outer `==` is the assertion operator, and the right side is an `ifExpression` term). That is valid per grammar: `assertion : term ASSERT_EQ term | operatorCall ;`

Yes.

Now, let's write the code.