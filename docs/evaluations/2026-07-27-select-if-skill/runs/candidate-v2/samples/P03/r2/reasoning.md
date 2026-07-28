The user wants a complete FusionFlow G4 workflow with specific structure. Let me analyze the requirements:

1. Input: `request` artifact
2. Scoring Step: consumes `request`, produces `score`
3. Accept handler: consumes `request`, produces `accept_result` (must execute every run)
4. Review handler: consumes `request`, produces `review_result` (must execute every run)
5. Selection: select `accept_result` when `score >= 80`, otherwise `review_result`
6. Final Step: consumes the selected value, produces `final_result`

The key requirement is that both accept and review handlers must execute on every run, and the selection is eager (both candidates run before selection). This is the named artifact selection pattern from the skill.

Let me model this:

- `request: Artifact`
- `score: Artifact`
- `accept_result: Artifact`
- `review_result: Artifact`
- `selected: Artifact`
- `final_result: Artifact`

Steps:
- `scoring_step: Step` - consumes `request`, produces `score`
- `accept_handler_step: Step` - consumes `request`, produces `accept_result`
- `review_handler_step: Step` - consumes `request`, produces `review_result`
- `final_step: Step` - consumes `selected`, produces `final_result`

Selection: `selected == if(score >= 80, accept_result, review_result)`

Wait, the condition comparison uses `>=` which maps to `>=` in the grammar. But in the formula, comparisons use the comparison operators. `>=` is `GTE` in the grammar. Let me check - the grammar says:
- comparisonOp: NUMERIC_EQ | NOT_EQUALS | LT | LTE | GT | GTE

So `score >= 80` would be `score >= 80` - wait, the grammar says `LTE : '<=';` and `GTE : '>=';`. So `>=` is valid.

In a formula, we have `comparison: term comparisonOp term`. So `score >= 80` should be valid syntax where `score` is a term (atomicTerm/constantName) and `80` is a constantName (NUMBER). And `80` is indeed a NUMBER token.

Now for the `if` expression: `if(formula, term, term)` - so `if(score >= 80, accept_result, review_result)`.

Then: `selected == if(score >= 80, accept_result, review_result)` is the selection assertion.

Then: `consumes(final_step) == [selected]` and `produces(final_step) == [final_result]`.

Now, I need to include StepName values and Instruction values. Let me declare them:

- `scoring_name: StepName` with value `"score_request"`
- `accept_name: StepName` with value `"accept_handler"`
- `review_name: StepName` with value `"review_handler"`
- `final_name: StepName` with value `"finalize"`

- `scoring_instruction: Instruction` with value `"./instructions/score_request.md"`
- `accept_instruction: Instruction` with value `"./instructions/accept_handler.md"`
- `review_instruction: Instruction` with value `"./instructions/review_handler.md"`
- `final_instruction: Instruction` with value `"./instructions/finalize.md"`

Wait, the skill says "Quoted constants are restricted IDs, not prose. Use declared StepName/Instruction identities or a "./..." instruction path; never place natural-language instructions in quotes." So I should use `"./..."` paths for instruction values.

For StepName values, I can use quoted constants. Let me use simple identifiers.

Actually, looking at the constantName definition:
```
constantName
    : NUMBER
    | RELATIVE_PATH_ID
    | QUOTEDCONSTANTID
    | LOWID
    ;

RELATIVE_PATH_ID : '"./' [A-Za-z0-9._/-]+ '"';
QUOTEDCONSTANTID : '"' [A-Za-z0-9.!#$%?@_{|}~`]* '"';
```

So for StepName, I can use a QUOTEDCONSTANTID like `"score_request"`.
For Instruction, I should use a RELATIVE_PATH_ID like `"./instructions/score_request.md"`.

But wait - the grammar says `step_name(Step) -> StepName` and `step_instruction(Step) -> Instruction`. And `StepName` and `Instruction` are concepts. When I write `step_name(scoring_step) == "score_request"`, the RHS is a constantName token that resolves to a StepName-typed constant.

Let me declare:
```
const "score_request": StepName;
const "./instructions/score_request.md": Instruction;
```

Hmm, but these are constant declarations. The grammar says:
```
constDecl
    : CONST constantName COLON conceptNameList
    ;
constantName
    : NUMBER
    | RELATIVE_PATH_ID
    | QUOTEDCONSTANTID
    | LOWID
    ;
```

So I can have:
```
const "score_request": StepName;
```
This declares a constant with quoted name "score_request" of type StepName.

But wait, how do I reference it? In an assertion like `step_name(scoring_step) == "score_request"`, the RHS `"score_request"` is parsed as a constantName (QUOTEDCONSTANTID), which through the assertion `ASSERT_EQ term` becomes a term. But for the term to be valid, it needs to be a known constant.

Actually, I think the cleaner approach based on the examples in the skill is:

```
const accept_name: StepName;
```
Then somewhere set `step_name(accept_handler_step) == accept_name;`

But the skill says "When the user supplies a grammar-valid literal as a typed constant name, including a restricted quoted ID or "./..." path, preserve that literal and use it directly as the required preset value; do not hide it behind an alias constant and an extra equality."

So I should use:
```
const "score_request": StepName;
```

And then use it directly:
```
step_name(scoring_step) == "score_request";
```

The RHS `"score_request"` is a constantName that was declared as type StepName. This is valid.

For Instructions, similarly:
```
const "./instructions/score_request.md": Instruction;
```
And use:
```
step_instruction(scoring_step) == "./instructions/score_request.md";
```

The RHS `"./instructions/score_request.md"` is a constantName that was declared as type Instruction.

OK, let me now write the full workflow.

I need:
- Agent executors for each step
- Model, Engine, ApiBase for agent_config

Let me declare the agents:
```
const scoring_agent: Agent, Executor;
const accept_agent: Agent, Executor;
const review_agent: Agent, Executor;
const final_agent: Agent, Executor;
```

And the model config:
```
const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;
```

Now the full workflow:

```fusionflow
-- SCENARIO: Score-based routing workflow with eager execution of both branches

const request: Artifact;
const score: Artifact;
const accept_result: Artifact;
const review_result: Artifact;
const selected: Artifact;
const final_result: Artifact;

const scoring_step: Step;
const accept_handler_step: Step;
const review_handler_step: Step;
const final_step: Step;

const "score_request": StepName;
const "accept_handler": StepName;
const "review_handler": StepName;
const "finalize": StepName;

const "./instructions/score_request.md": Instruction;
const "./instructions/accept_handler.md": Instruction;
const "./instructions/review_handler.md": Instruction;
const "./instructions/finalize.md": Instruction;

const scoring_agent: Agent, Executor;
const accept_agent: Agent, Executor;
const review_agent: Agent, Executor;
const final_agent: Agent, Executor;

const default_model: Model;
const default_engine: Engine;
const default_api: ApiBase;

workflow score_routing {
  -- DATA FLOW
  input_workflow(score_routing) == [request];
  consumes(scoring_step) == [request];
  produces(scoring_step) == [score];
  consumes(accept_handler_step) == [request];
  produces(accept_handler_step) == [accept_result];
  consumes(review_handler_step) == [request];
  produces(review_handler_step) == [review_result];
  selected == if(score >= 80, accept_result, review_result);
  consumes(final_step) == [selected];
  produces(final_step) == [final_result];
  output_workflow(score_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(scoring_step) == scoring_agent;
  step_executor(accept_handler_step) == accept_agent;
  step_executor(review_handler_step) == review_agent;
  step_executor(final_step) == final_agent;

  -- STEP CONFIGURATION
  step_name(scoring_step) == "score_request";
  step_instruction(scoring_step) == "./instructions/score_request.md";
  step_name(accept_handler_step) == "accept_handler";
  step_instruction(accept_handler_step) == "./instructions/accept_handler.md";
  step_name(review_handler_step) == "review_handler";
  step_instruction(review_handler_step) == "./instructions/review_handler.md";
  step_name(final_step) == "finalize";
  step_instruction(final_step) == "./instructions/finalize.md";

  -- AGENT CONFIGURATION
  agent_config(scoring_agent, default_model, default_engine, default_api);
  agent_config(accept_agent, default_model, default_engine, default_api);
  agent_config(review_agent, default_model, default_engine, default_api);
  agent_config(final_agent, default_model, default_engine, default_api);
}
```

Wait, I need to check if the selection assertion is correct. The grammar says:

```
ifExpression
    : IF LPAREN formula COMMA term COMMA term RPAREN
    ;
```

And:
```
formula
    : ...
    | comparison
    ;

comparison
    : term comparisonOp term
    ;
```

So `if(score >= 80, accept_result, review_result)` - here `score >= 80` is a comparison, which is a valid formula. Then `accept_result` and `review_result` are terms. This is valid.

And the assertion `selected == if(score >= 80, accept_result, review_result)` uses `ASSERT_EQ` between a term (`selected`) and a term (`if(...)`). The `ifExpression` is a valid term alternative. Good.

Now let me also verify: score and 80 - score is a LOWID constantName, 80 is a NUMBER constantName. Both are valid atomicTerms. The comparison `score >= 80` uses `>=` which should match the `GTE` token. Let me check the grammar tokens:

```
GTE : '>=';
```

But wait - I need to check lexer precedence. If I have `>=`, the lexer might tokenize `>` as GT and then `=` as something else. But ANTLR lexers match the longest possible token first. `>=` is 2 characters, so it should match GTE before matching just `>`. Similarly, `<=` for LTE. So this should work.

Actually, looking more carefully at the grammar tokens: `GT : '>';` and `GTE : '>=';`. Since ANTLR tries longest match first, `>=` will be matched as GTE, not as GT followed by something. Good.

And `80` is a NUMBER (DIGITS). Good.

I think this is complete and correct. Let me also double-check the condition: "score is greater than or equal to the numeric literal 80" - so `score >= 80` is exactly right.

Both handlers "must execute on every run" - and with the eager semantics of named artifact selection, both accept_handler_step and review_handler_step will execute regardless of which result is selected downstream. Good.

Let me make sure I haven't missed anything. The user says "include all required declarations, data-flow assertions, executor bindings, StepName values, and Instruction values". I have all of those.

One thing - should I add `max_concurrency` or other optional config? The user didn't ask for it specifically, but since both accept and review handlers both consume `request` (same input), and scoring also consumes `request`, they could run in parallel. But I don't need to force concurrency - the runtime respects artifact dependency edges.

Let me finalize.