---
name: fusion-flow
description: Author or run Fusion Flow Next, a declarative workflow system defined by the formal FusionFlow G4 language and lowered into checked Step–Artifact plans. Use for coordinated agents, parallel sub-tasks, and multi-step pipelines; use the legacy `flow` skill under `skills/fusion-flow-legacy/` only for explicit `.flow.ts` or Fuclaw compatibility requests.
metadata: { "openclaw": { "emoji": "🐾", "homepage": "https://github.com/fuclaw" } }
---

# Fusion Flow Next Skill

Fusion Flow Next is a formal-language workflow system defined by `grammar/FusionFlow.g4`. This skill authors its declarative programs in psi-agent; the workspace tool compiles them into Core IR, lowers them to a `WorkflowGraph`, generates a checked execution plan, and synchronously executes Agent-backed Steps. Bounded, Agent-only, and one-shot describe the current executor, not the language itself.

> **Workspace boundary.** Store authored G4 files under the workspace-managed `flows/` directory. The skill ships no runnable example workflows and creates no background run directory.

> **No slash commands.** This skill is triggered by **natural-language intent**, never by a `/flow xxx` command. The user just talks: "帮我写个并行调研的工作流" / "跑一下刚生成的那个" / "刚才那个跑完了吗". Do NOT teach, suggest, or expect any `/flow run` / `/flow show` / `/flow author` syntax — those slash commands do not exist and printing them to the user is a bug (a user in an environment without this skill installed will see "命令没找到"). Map what the user *means* to the actions below.

## When to Activate

Activate this skill when the user:

- Asks to run a FusionFlow G4 workflow they already have ("跑一下这个 / 帮我跑 / 执行"). This skill does **not** ship runnable demo examples; "run" always means a concrete workflow the user has.
- Mentions FusionFlow or agent-flow
- **Describes any task that needs a multi-agent workflow or agent collaboration**, even without saying "flow" — e.g. "让几个 agent 分别审一遍再汇总", "并行跑 N 个子任务再合并", "一步接一步处理(先 A 再 B 再 C)", "多角度评审后汇总", "把这件事拆成多个 agent 协作". If the task clearly benefits from orchestrating more than one agent / parallel branches / a multi-step pipeline, enter **Authoring Mode** (below) and offer to build a flow.

When in doubt about whether a task is "workflow-shaped": if it would take **two or more coordinated LLM steps** (fan-out/fan-in, an artifact pipeline, or per-item work), it qualifies — activate and propose a flow. A single one-shot question does not.

### HARD RULE: when you recognize a multi-agent task, your job is to BUILD A FLOW — not to do it yourself

Once a task is workflow-shaped (multiple agents / parallel branches / multi-step pipeline / per-item work), your **one default action** is to enter Authoring Mode and build a FusionFlow G4 workflow. That is the entire point of this skill — the flow runtime spawns and coordinates the sub-agents; **you do not play those sub-agents yourself**.

Do **NOT** offer "我直接帮你做这一次" as an option, and especially do **NOT** make it the default. Building the flow IS how you help: doing it by hand throws away explicit dependencies, graph concurrency, named Artifacts, and the reusable G4 source.

❌ **Real failure to never repeat** (observed in testing): user said "让几个 AI 从安全/性能/可读性分别审一段代码再汇总". The agent replied with "方式 A：我直接帮你审这次代码 / 方式 B：给你做成可复用工作流" — offering to personally act as the three reviewers, with the manual path listed first as the default. **Wrong.** The correct response is to go straight into Authoring Mode and build the review flow: three reviewer Steps consume the same input, then one final Step consumes all three review artifacts. No A/B menu, no "I'll just review it myself".

✅ **Correct shape**: "🐾 这是个多 agent 协作任务，我来帮你搭一个工作流：3 个审查 agent（安全/性能/可读性）并行审 → 一个汇总 agent 合并成带严重等级的报告。" Then run the author loop (understand → model → author → static self-check → one heads-up line → **run it once**).

The only time you don't build a flow is when the user **explicitly** says they just want a one-off answer and not a tool ("别给我搭工具，就这一次，你直接说结论"). Even then, confirm — don't assume.

Do **not** activate this skill for `.prose` files — those belong to OpenProse.

## Architecture

| Layer | Responsibility |
| --- | --- |
| `grammar/FusionFlow.g4` + parser | G4 source to Core IR |
| `fusion_flow.workflow_graph` | immutable Step–Artifact graph model |
| `fusion_flow.workflow_execution` | plan generation, dependencies, concurrency, timeouts, and run-local resources |
| `fusion_flow.workflow_runner` | Core IR compilation and checked dispatch |
| workspace `run_flow` tool | file/JSON boundary and ephemeral Session-backed Agent Steps |

The skill's job is to:

1. Turn the user's intent into valid FusionFlow G4 source, or resolve the concrete G4 workflow they pointed to.
2. Submit it once through the synchronous `run_flow` tool.
3. Return the workflow's output Artifact mapping.

## Intent Routing

The user talks in natural language. Map what they **mean** to one of these actions. There is **no slash-command syntax** — never echo a `/flow xxx` form back at them.

| What the user says (examples) | Action |
| --- | --- |
| "我能用这个干嘛 / 你能帮我做什么" | Describe capabilities in plain language (see "Capabilities" at the bottom) + offer to build a flow |
| "跑一下这个 / 帮我跑 X / 执行这个 workflow" | Run the concrete workspace G4 source once with `run_flow` and return its output Artifact mapping. |
| "接着上次那个跑 / 只重跑改动的部分" | Explain that resume/cache is not part of this runner; run the workflow again only if the user wants a fresh execution. |
| "看看结果 / 刚才那个跑完了吗" | A `run_flow` call returns only after completion; use the result already returned by that call. |
| "环境齐不齐 / 能不能跑 / 帮我检查下" | Confirm that the G4 source parses and that all Steps use supported Agent executors. |
| **"帮我写个工作流做 X / 帮我编排 / 我想让几个 agent ..."** | **Author a new FusionFlow G4 workflow from natural language. See "Authoring Mode" below.** |
| Anything else workflow-shaped | Interpret intent against this table |

## Running a Workflow

Use the workspace `run_flow` tool for FusionFlow G4 source. It validates and executes the workflow as one synchronous operation and returns the final output Artifacts as a JSON object.

### G4-only boundary

Only author and run FusionFlow G4 source. If the user points to any non-G4 workflow file, do not execute it, treat it as supported, or translate it implicitly. State that this skill accepts G4 source only. If the user explicitly asks to migrate that workflow, enter Authoring Mode and author one new G4 workflow from its intent.

Use a workspace-relative `.workflow` path under `flows/`. Never guess, scan for, or execute a path outside the workspace.

Before execution, gather any required file, web, or workspace context in the parent Session.
Inner Agent Steps have no tools. Pass the gathered, bounded content through named workflow
inputs in `inputs_json`; do not pass unresolved file paths and expect a Step to read them.

Build `instructions_json` as an exact mapping from every declared `step_instruction` ID to
the full instruction body. Each body must be real non-empty text, not the ID repeated back
or a path reference. The runtime rejects missing and extra IDs before creating any Step Session.

Pass run-local resource pools through `resource_capacities_json` only when the workflow declares `resource_requirement`.

Call `run_flow` once and use its returned JSON object as the result.

### Resume

This runner has no run token, persisted run state, resume, or cache protocol. A second call is a fresh execution.

## Agent-backed execution

Before executing a FusionFlow G4 workflow with Agent-backed Steps:

1. Ensure every Step executor is declared as `Agent`; this runner rejects Human and Program executors before dispatch.
2. Keep the graph within 32 Steps, concurrency 8, and a 15-minute total timeout. Each Step gets one model round.
3. Resolve every instruction body and gather all tool-derived inputs in the parent Session.
4. Internally estimate cost and latency from the number of Agent Steps. Fold that into one plain-language heads-up line.
5. Say the heads-up line, then run without adding another approval gate unless the user explicitly said "只生成别跑".

### Running is the runtime's job, not yours

Resolve the workspace-relative G4 path, submit it to `run_flow`, and report the returned output mapping. Do not reproduce parsing, dependency scheduling, resource leasing, or Step execution in the parent Session.

### One synchronous call

`run_flow` waits for the graph to finish and returns the final JSON result. Do not call the legacy `.flow.ts` `flow_run(start/status/result)` tool for G4 source, and do not invent polling, PID, token, worker, or background-state handling.

### When a run fails

A compilation or Step exception is a **STOP-and-report point**. Report the failing Step or diagnostic exposed by `run_flow`, state one best hypothesis, and hand back to the user.

These actions are forbidden when a run fails:

- editing the workflow or creating a modified copy to work around the failure;
- bypassing `run_flow` and manually executing individual Steps;
- silently retrying or approximating an unsupported operator or executor.

Do not create a mock or offline twin with baked-in output.

### Don't fake or guess progress

The tool does not expose intermediate progress. Do not invent node status while the call is in flight.

## Reading a Run

When the call completes, summarize the returned output Artifact mapping. There is no persisted run to inspect later.

## File Locations

Paths are relative to the workspace:

| File | Location | Purpose |
| --- | --- | --- |
| `flows/<task-slug>/` | authored FusionFlow G4 source |

## Authoring Mode

This is the flagship: turn a natural-language intent into a runnable FusionFlow G4 workflow. The user just describes what they want in plain words ("帮我写个工作流做 X") — there is no command to invoke and no implementation format to explain.

> **NO-MOCK RULE (global, applies to all of Authoring Mode).** When you build a flow for the user, author **exactly one** real FusionFlow G4 workflow and NEVER fabricate a mock/offline/simplified twin to "test" or "demonstrate" it. A twin with hardcoded sample output, fake numbers, or a fake executor standing in for the real work is a **forgery** — it always "passes" regardless of what the real flow does, so it proves nothing and misleads the user. Validate the one real workflow, then actually run it. If the user *explicitly* later asks for an offline twin, that's a separate request you confirm first — never self-initiate one.
>
> Inlined source snippets in this Skill are authoring guidance, not runnable bundled workflows. The ban is on fabricating a second version of the user's flow.

### When to enter Authoring Mode

- User describes a workflow they want built: "帮我写个工作流 ..." / "make a flow that ..." / "帮我编排 ..." / similar.
- User asks "帮我写一个 flow ..." / "make a flow that ..." / similar in any LLM client.
- User edits existing FusionFlow G4 source and asks you to "rewrite" or "扩展".
- **User describes a workflow-shaped task without naming "flow"** — anything needing two or more coordinated agents / parallel branches / a multi-step pipeline / per-item work (see "When to Activate"). In that case, don't wait for the word "flow": offer to build one, then run the author loop below.

### The 5-step author loop

1. **Understand intent** — restate the user's goal in 1 sentence. If genuinely ambiguous, ask **one** clarifying question (don't grill them). Note whether the user looks like a *developer* (asked to edit FusionFlow G4 source or mentioned operators) — that's the only case where you show technical detail later. Everyone else gets the minimal plain-language summary.
2. **Model the workflow** — match the intent to one of the executable reference patterns below. Identify inputs, outputs, Agent-backed Steps, Artifacts, dependencies, concurrency, resources, and timeouts.
3. **Author one FusionFlow G4 source** — before writing, read `grammar/FusionFlow.g4` completely and treat it as the sole source of truth for FusionFlow syntax and preset operators. Use only declarations, assertions, terms, and operators documented there. Use the workspace-provided target path; never invent a second copy.
4. **Static self-check** — compare the source against `grammar/FusionFlow.g4` and the executable guardrails in this Skill. There is no separate validation tool.
5. **Run it once** — the user asked you to do a task, not to receive an implementation artifact. After the static self-check, say ONE friendly heads-up line ("🚀 方案定了，正在帮你跑，预计几分钟…" — a notice, NOT a question), then call `run_flow` once. **Do NOT ask "要不要跑 / 跑不跑" and do NOT wait for `跑`.** The only exception is when the user explicitly says "只生成别跑 / 先给我看看别执行".

Never mention the source file, its path, G4, operator names, static-check stages, or internal runnable artifacts to a non-technical user. From their side you are just doing the task they asked for. If they ask "你在干嘛 / 怎么做的", answer in plain business language ("我让几个分析分头跑、再汇总").

### Talking to the user while you work

Before calling `run_flow`, send one short heads-up such as "🚀 方案定了，正在帮你跑，预计几分钟…". The synchronous tool exposes no node-level progress, so do not claim that an individual Step or branch has started or completed. When the call returns, lead with the result. Do not add an approval question between authoring and execution.

### Hard stops in Authoring Mode (real TUI failures, do not repeat)

These are not style preferences. Each one was observed corrupting a real author run. These bans apply **whether you're mid-author or already running**:

1. **Don't fake a result instead of running.** The user wants the real outcome. After the static self-check you call `run_flow` once (see step 5) — you do not stop and hand back a file, and you never substitute a made-up answer for an actual run.
2. **Write OR run any extra workflow source beyond the one file the task needs** — not an offline twin, not a "simpler version", not a "v2", not a "test harness". One intent = one file. An offline twin with baked-in output is a forgery, not a test. If the user later wants one, that is a separate explicit request.
3. **Report numbers you did not get from a real run** — never present mock data, sample data, or figures from an unrelated file as if they are *this* flow's result. The only result you report is what the `run_flow` call actually returned. If it fails, report the failure instead of papering over it with invented numbers.
4. **Write outside the workspace-managed `flows/` location** — do not scan the filesystem for another flow project or create a sibling bundle copy. If the intended path is ambiguous, ask the user instead of guessing.

The real run is how you deliver — there is no "spend-free preview" step to offer the user. Perform the static self-check, then call `run_flow` once.

### Heads-up line (说一句就开跑，不是 gate)

Keep this **minimal**. A real investor ("悠悠") and an internal teammate ("张浩") both bailed on the authoring flow because the summary was wall-to-wall framework jargon (`parallel / pmap / reduce / evaluate / choice / 原语 / 异构复合工作流`). Their words: "这么专业应该不是给我这种用户用的吧" / "这表述太专业太多专有名词了，我都不知道咋聊了". This line is a **告知**, not a go/no-go gate — you say it and then immediately run. It exists so the user isn't surprised by a few minutes' wait / the cost, NOT to ask permission.

**Default heads-up (use this for everyone unless they're clearly a developer):** one plain-language sentence on what they'll get + a rough time estimate. No primitive names, no API names, no pattern names, no file path, no per-step breakdown, no token math, no "要不要跑".

```
🚀 我来帮你做：<一句话讲清楚要产出什么，e.g. "并行调研 5 个 AI 方向，汇总打分后给你一份带『重点关注 / 投资机会』的总报告">，预计几分钟，这就开始。
```

That's it — one line, then you run. Do **not** add `做什么 / 要多久 / 你会拿到` as separate fields, do not list steps, do not show 🔧/🎯/📝 lines, do not show the file path, do not ask for approval. If the user is clearly a **developer** (asked to edit FusionFlow G4 source, mentioned operators, or explicitly asks "用了哪些语法 / 给我看结构 / 文件在哪"), you may then show technical detail **on demand**:

```
🔧 3 个审查 Step 共用输入，1 个汇总 Step 消费三个结果 ｜ `max_concurrency = 3`
```

Only show that line when a developer explicitly asks for it. Never push it at a business user, and never volunteer the file path unprompted.

**Jargon → plain-language map (so the default sentence stays clean).** Never say the left; say the right:

| 框架黑话（别说） | 业务语言（要这么说） |
| --- | --- |
| G4 / operator | 工作流结构 |
| Step | 一次处理 |
| Artifact | 中间结果 |
| `max_concurrency` | 同时处理 |
| `consumes(step) == [result_a, result_b]` | 汇总多个结果 |
| 异构复合工作流 | 多方向 + 分层汇总 |
| token / LLM 调用 | （折成）几分钟 / 花多少钱 |


Token estimate rule of thumb: each ordinary LLM work step ≈ 1500 input + 800 output tokens; each structured judgement step ≈ 2000 input + 50 output. Sum, then convert to RMB at the provider's listed rate (火山 ARK Agent Plan 包月里这是 0 元，flag it as "≈ 0 (Agent Plan)").

### Reference patterns

Read `grammar/FusionFlow.g4` completely before using these patterns. The grammar is authoritative; these patterns illustrate artifact dependencies and do not add syntax or operators.

| Pattern | FusionFlow shape | When to use |
| --- | --- | --- |
| **Fan-out + fan-in** | Several Steps each use `consumes(step) == [shared_artifact]`; one final Step uses `consumes(final_step) == [result_a, result_b]`. Set `max_concurrency` on the workflow when needed. | PR review, multi-perspective audit, content moderation. |
| **Artifact pipeline** | Each Step produces the Artifact consumed by the next Step. Keep `max_attempts` omitted or equal to `1`. | Writing, ETL, and refine-and-check work. |
| **Named Artifact selection** | Keep every candidate result explicit, then bind `selected_artifact == if(formula, artifact_a, artifact_b)` and use `selected_artifact` in ordinary dataflow. For priority selection, chain named intermediate Artifacts. | Eagerly run all candidate producers, then choose one value for downstream Steps. |
| **Composite workflow** | Combine artifact chains, fan-out/fan-in, explicit bounded Agent Steps, and named Artifact selections. | When one simple pattern does not cover the task. |

Before reporting a missing capability for a conditional request, first check whether eager value selection is sufficient. Named Artifact selection runs every candidate producer and only selects the value passed downstream. If the request requires lazy branch activation or guarantees that an unselected producer will not run, report that limitation instead of emitting an approximation. Never invent a keyword or operator to make the source look complete.

#### Full-featured in-context example

This is the canonical review shape from the activation example: three independent review Steps consume the same source, then one final Step consumes their outputs.

```fusionflow
-- SCENARIO: security, performance, and readability review followed by one report

const source_code: Artifact;
const security_findings: Artifact;
const performance_findings: Artifact;
const readability_findings: Artifact;
const final_report: Artifact;

const security_review: Step;
const performance_review: Step;
const readability_review: Step;
const synthesize_report: Step;

const security_review_name: StepName;
const performance_review_name: StepName;
const readability_review_name: StepName;
const synthesize_report_name: StepName;

const security_instruction: Instruction;
const performance_instruction: Instruction;
const readability_instruction: Instruction;
const synthesis_instruction: Instruction;

const security_agent: Agent, Executor;
const performance_agent: Agent, Executor;
const readability_agent: Agent, Executor;
const editor_agent: Agent, Executor;


workflow code_review {
  -- DATA FLOW
  input_workflow(code_review) == [source_code];
  consumes(security_review) == [source_code];
  produces(security_review) == [security_findings];
  consumes(performance_review) == [source_code];
  produces(performance_review) == [performance_findings];
  consumes(readability_review) == [source_code];
  produces(readability_review) == [readability_findings];
  consumes(synthesize_report) ==
    [security_findings, performance_findings, readability_findings];
  produces(synthesize_report) == [final_report];
  output_workflow(code_review) == [final_report];

  -- EXECUTOR ASSIGNMENT
  step_executor(security_review) == security_agent;
  step_executor(performance_review) == performance_agent;
  step_executor(readability_review) == readability_agent;
  step_executor(synthesize_report) == editor_agent;

  -- STEP CONFIGURATION
  step_name(security_review) == security_review_name;
  step_instruction(security_review) == security_instruction;
  step_timeout(security_review) == 300;
  step_name(performance_review) == performance_review_name;
  step_instruction(performance_review) == performance_instruction;
  step_timeout(performance_review) == 300;
  step_name(readability_review) == readability_review_name;
  step_instruction(readability_review) == readability_instruction;
  step_timeout(readability_review) == 300;
  step_name(synthesize_report) == synthesize_report_name;
  step_instruction(synthesize_report) == synthesis_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(code_review) == 3;
  workflow_timeout(code_review) == 900;

}
```

### G4 source of truth

Before authoring, read `grammar/FusionFlow.g4` completely. It is the sole authority for surface syntax, declarations, assertions, formulas, terms, and preset operator signatures. This skill additionally defines which grammar-valid shapes the executable graph backend accepts.
Runner-specific typed catalog extensions use the grammar's generic operator-call syntax without changing its preset catalog. In particular, `depends_on(Step, Step) -> Bool` is registered by `fusion_flow/workflow_runner.py`.

### Executable graph backend guardrails

- Every dataflow operator has one owner and an explicit Artifact List RHS.
- Every executable `if` has the top-level shape `selected_artifact == if(condition, artifact_a, artifact_b);`. Never put `if` inside a dataflow List or another `if`; chain named intermediate Artifacts instead.
- Selection is eager: every candidate producer runs before the selected value is published.
- Quoted constants are restricted IDs, not prose. Use declared `StepName`/`Instruction` identities; supply natural-language bodies separately through `instructions_json`.

### Modeling rules

- Group assertions by concern in this exact order: `DATA FLOW`, `EXECUTOR ASSIGNMENT`, `STEP CONFIGURATION`, `SCHEDULING CONFIGURATION`, `WORKFLOW CONFIGURATION`. Omit empty groups.
- In `DATA FLOW`, declare the complete external input List once, then every Step's `consumes`/`produces` edges and named Artifact selections in dependency order, then the complete external output List once.
- Use exactly one symmetric Artifact dataflow contract: `input_workflow(workflow) == [artifact_a, artifact_b];`, `consumes(step) == [artifact_a, artifact_b];`, `produces(step) == [artifact_a, artifact_b];`, and `output_workflow(workflow) == [artifact_a, artifact_b];`. All four operators return `List`; even one Artifact requires an explicit List literal such as `[artifact]`. Never use these calls as standalone assertions, with `== True`, with an Artifact as a second argument, or through alternate multi variants.
- Bool shorthand is only for supported non-dataflow Bool operators such as `independent(step)` and `depends_on(step, predecessor)`. Keep `== False` explicit. Retain the right-hand value for every non-Bool operator.
- When the user supplies a grammar-valid literal as a typed constant name, including a restricted quoted ID or `"./..."` path, preserve that literal and use it directly as the required preset value; do not hide it behind an alias constant and an extra equality.
- Model data sequencing through Artifact edges: a Step that produces an Artifact precedes a Step that consumes it. When ordering is required without passing data, use `depends_on(step, predecessor) == True`; repeat it for multiple predecessors. Declaration order never defines execution order.
- Preserve the external data boundary from the user's intent. Fan-out Steps that analyze the same subject reuse one shared input Artifact; do not split it into synthetic per-branch workflow inputs.
- Emit every explicitly requested relation. Every operand must be a declared grammar term: `_` and `...` are not wildcards. Declare typed constants for required operands, or omit an optional configuration instead of inserting placeholders.
- Model fan-out by making several steps consume the same artifact.
- Model fan-in with `consumes(step) == [artifact_a, artifact_b];`.
- Expand a known, bounded item set into explicit Agent Steps. The current runner does not execute `foreach_item`.
- Bind each step to its executor with `step_executor`.
- Configure concurrency, timeouts, and resources with the corresponding supported operators; keep `max_attempts` omitted or set to `1`.
- Treat `independent(step)` only as a hint. Artifact dependencies and `depends_on` still decide when the Step is ready.
- Declare resource demand with `resource_requirement(step, resource)`. Resource capacities or concrete IDs come from runner configuration, never from `.workflow` source.
- The current one-shot runner supports resource scheduling and explicit `depends_on` ordering but still rejects `foreach_item` execution and `max_attempts` values other than `1`.
- Unknown or unsupported assertions remain residual and stop execution. Never delete them, comment them out, or bypass residual validation to make a run start.
- Lower executable `if` as a named Artifact selection: `selected_artifact == if(formula, artifact_a, artifact_b);`, followed by ordinary list dataflow such as `consumes(final_step) == [selected_artifact];`.
- Variables, quantifiers, rules, implications, biconditionals, query/SAT/optimization requests, local concept declarations, local operator declarations, and imperative blocks are outside this language.
- Never emit imports, imperative runtime calls, `run(...)`, or invented `parallel`/`pipeline`/`for` blocks.

#### Executor configuration

Declare every executor as `Agent, Executor`, bind it with `step_executor`, and give each Step a `step_instruction`. The current runner rejects Human and Program executors before dispatch. `allowed_tool`, `program_path`, and `agent_system_prompt` are not part of this runner's supported catalog; do not emit them.

#### Named Artifact selection with `if`

Keep every candidate result explicit and produced by a Step. Bind each `if` result to a declared Artifact before downstream dataflow:

```fusionflow
const incoming_case: Artifact;
const primary_criterion: Artifact;
const block_criterion: Artifact;
const review_criterion: Artifact;
const exception_criterion: Artifact;
const primary_observation: Artifact;
const block_observation: Artifact;
const review_observation: Artifact;
const exception_observation: Artifact;
const primary_result: Artifact;
const review_result: Artifact;
const fallback_result: Artifact;
const review_or_fallback: Artifact;
const selected_result: Artifact;
const final_result: Artifact;

const triage_step: Step;
const primary_handler_step: Step;
const review_handler_step: Step;
const fallback_handler_step: Step;
const final_step: Step;

const triage_name: StepName;
const primary_handler_name: StepName;
const review_handler_name: StepName;
const fallback_handler_name: StepName;
const final_name: StepName;
const triage_instruction: Instruction;
const primary_handler_instruction: Instruction;
const review_handler_instruction: Instruction;
const fallback_handler_instruction: Instruction;
const final_instruction: Instruction;

const triage_agent: Agent, Executor;
const primary_handler: Agent, Executor;
const review_handler: Agent, Executor;
const fallback_handler: Agent, Executor;
const final_consumer: Agent, Executor;

workflow priority_routing {
  -- DATA FLOW
  input_workflow(priority_routing) ==
    [incoming_case, primary_criterion, block_criterion, review_criterion, exception_criterion];
  consumes(triage_step) == [incoming_case];
  produces(triage_step) ==
    [primary_observation, block_observation, review_observation, exception_observation];
  consumes(primary_handler_step) == [incoming_case];
  produces(primary_handler_step) == [primary_result];
  consumes(review_handler_step) == [incoming_case];
  produces(review_handler_step) == [review_result];
  consumes(fallback_handler_step) == [incoming_case];
  produces(fallback_handler_step) == [fallback_result];
  review_or_fallback == if(
    (review_observation = review_criterion) OR (exception_observation = exception_criterion),
    review_result,
    fallback_result
  );
  selected_result == if(
    (primary_observation = primary_criterion) AND !(block_observation = block_criterion),
    primary_result,
    review_or_fallback
  );
  consumes(final_step) == [selected_result];
  produces(final_step) == [final_result];
  output_workflow(priority_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(triage_step) == triage_agent;
  step_executor(primary_handler_step) == primary_handler;
  step_executor(review_handler_step) == review_handler;
  step_executor(fallback_handler_step) == fallback_handler;
  step_executor(final_step) == final_consumer;

  -- STEP CONFIGURATION
  step_name(triage_step) == triage_name;
  step_instruction(triage_step) == triage_instruction;
  step_name(primary_handler_step) == primary_handler_name;
  step_instruction(primary_handler_step) == primary_handler_instruction;
  step_name(review_handler_step) == review_handler_name;
  step_instruction(review_handler_step) == review_handler_instruction;
  step_name(fallback_handler_step) == fallback_handler_name;
  step_instruction(fallback_handler_step) == fallback_handler_instruction;
  step_name(final_step) == final_name;
  step_instruction(final_step) == final_instruction;
}
```

- Build conditions with `=`, `!=`, `<`, `<=`, `>`, or `>=`; reserve `==` for the surrounding assertion.
- Combine comparisons with `!`, `AND`, and `OR`.
- Both branches must be declared Artifacts. The selection result must also be a declared Artifact.
- Every candidate producer runs. Selection is eager value routing, not lazy control flow.
- For more choices, chain named intermediate Artifacts in priority order; do not nest an `if` directly inside another `if`.
- Never place `if(...)` inline inside `input_workflow`, `consumes`, `produces`, or `output_workflow`; those operators still take explicit Artifact Lists.
- Do not replace candidate Artifacts with Boolean Step payloads or invent `switch`, `choice`, or conditional blocks.

Do not encode free-form command strings, code, prompts, or secrets as quoted constants. `grammar/FusionFlow.g4` permits restricted quoted IDs and workspace-relative `"./..."` paths; neither is a general string.

### Anti-patterns to refuse

1. **Hand-writing imports or imperative runtime calls.** The authored program is FusionFlow G4 source.
2. **Inventing a keyword or operator.** Flexible call syntax does not make unknown names valid.
3. **Using `==` inside a condition or `=` for a workflow assertion.** These have different grammar roles.
4. **Treating quoted constants as prompt strings.** They are restricted IDs or explicit workspace-relative paths, not free-form text.
5. **Treating `max_attempts` as a workflow loop or score gate.** It only sets the attempt limit for one Step.
6. **Expanding a large item list without a cost check.** Every explicit Agent Step may consume a model call; keep the bounded expansion intentional.
7. **Passing a document path to a tool-less Step.** Read the document in the parent Session and pass only the bounded content the Step needs as an input Artifact.
8. **Relaying an external tool's secret through workflow source.** Let the tool read its own configuration; never encode credentials in constants.
9. **Sharing mutable state between parallel branches.** Use artifacts and explicit producer/consumer relations.

### Code template

Every authored workflow follows this shape:

```fusionflow
-- SCENARIO: <one-line user-facing description>
-- AUTHORED: <YYYY-MM-DD HH:mm:ss> from intent: "<original user intent>"

const input_artifact: Artifact;
const output_artifact: Artifact;
const work_step: Step;
const work_name: StepName;
const work_instruction: Instruction;
const worker: Agent, Executor;

workflow workflow_name {
  -- DATA FLOW
  input_workflow(workflow_name) == [input_artifact];
  consumes(work_step) == [input_artifact];
  produces(work_step) == [output_artifact];
  output_workflow(workflow_name) == [output_artifact];

  -- EXECUTOR ASSIGNMENT
  step_executor(work_step) == worker;

  -- STEP CONFIGURATION
  step_name(work_step) == work_name;
  step_instruction(work_step) == work_instruction;
}
```

Extend this skeleton only with syntax and preset operators documented in `grammar/FusionFlow.g4`.

### Static self-check

Before the single `run_flow` call, inspect the source in order:

- every identity is declared with a supported concept;
- assertions use `==`, while formulas use comparison operators;
- each operator uses the documented arity and supported shape;
- each Step has an Agent executor, name, instruction, and explicit data/control dependencies;
- no residual or unsupported operator is emitted.

This is a source review, not a second tool or CLI invocation. Actual parsing and compilation occur inside `run_flow`.

### Running it (automatic, right after the self-check)

1. Call `run_flow(flow_path=..., instructions_json=..., inputs_json=..., resource_capacities_json=...)` once. Omit resource capacities when the graph declares no resource requirement.
2. The call returns only after completion. Summarize the returned output Artifact mapping in plain language.
3. On error, report the compiler diagnostic or failed Step without creating a second workflow or bypassing the runner.

### What Authoring Mode is NOT

- It is **not** a guarantee the workflow gets good *content*. We control structure and execution; the task instructions still depend on the user's domain.
- It is **not** auto-iterating on content. The user reads the result and asks for changes, but there is no "要不要跑" gate before the first run.
- It is **not** a reason to show implementation details to a business user. Technical users can ask for the FusionFlow G4 source and structure on demand.

## Doctor Checks

When the user asks whether a workflow can run:

1. Confirm the source is a readable workspace-relative `.workflow` file.
2. Perform the static self-check above without invoking a separate validator.
3. Confirm that required resource capacities can be supplied.

If the static check finds an issue, report:

```
✗ FusionFlow source is not ready to run
  Reason: <first source-contract issue>
```

Otherwise:

```
✓ FusionFlow source is ready for run_flow
```

## Capabilities

When the user asks what this skill can do ("你能帮我做什么 / 我能用这个干嘛"), describe these in plain language — never as slash commands. The user just talks naturally and you map intent (see "Intent Routing"):

```
🐾 FusionFlow G4
用自然语言驱动多 Agent 工作流。直接跟我说就行，不用记任何命令：

  • "帮我写个工作流做 X / 帮我编排 ..."           → 用大白话描述需求，我帮你搭好并运行
  • "跑一下刚才那个 / 帮我跑这个 workflow"        → 同步执行 G4 workflow 并返回输出
  • "环境齐不齐 / 能不能跑"                        → 检查 G4、Agent executor 和资源声明

不附带现成可运行示例；你想要什么工作流，直接描述，我会写入 workspace 的 flows/ 目录。
```

## Security + Approvals

Agent Steps run through ephemeral, tool-less psi Sessions. The parent Session must gather approved external context and pass bounded values in `inputs_json`. Review user-supplied G4 source before execution, but do not add an approval gate to a workflow authored for the user's current request. Refuse remote URLs: `run_flow` accepts workspace-local `.workflow` or `.g4` files only.
