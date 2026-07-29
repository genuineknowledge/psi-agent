---
name: flow
description: Use when authoring, saving, reusing, or running FusionFlow G4 multi-agent workflows, when the user invokes /workflow:<slug>, mentions FusionFlow, or needs coordinated agents, parallel sub-tasks, or a multi-step pipeline. Not for .prose files.
metadata: { "openclaw": { "emoji": "🐾", "homepage": "https://github.com/fuclaw" } }
---

# FusionFlow G4 Skill

This skill authors, saves, reuses, and runs declarative FusionFlow G4 workflows in psi-agent. The workspace tool compiles G4 source into Core IR, lowers it to a `WorkflowGraph`, generates an execution plan, runs Agent-backed Steps in ephemeral Sessions, runs Program-backed Steps through specialized Program Agents with structured process capture, and pauses Human-backed Steps across conversation turns.

> **Workspace boundary.** Store one-off authored G4 files under the workspace-managed `flows/` directory. Reusable declarations have one canonical location: `flows/workflows/<slug>/<slug>.workflow`. The skill ships no runnable example workflows. Every run persists all materialized Artifacts as Markdown under its workflow bundle's `runs/<run-id>/artifacts/` directory. Human Steps additionally persist private checkpoints under the ignored workspace `.psi/fusion-flow/runs/` directory; non-Human runs remain non-resumable.

> **Explicit reuse command.** `/workflow:<slug>` is the one supported shortcut for running a saved workflow. It accepts no suffix or inline parameters. If inputs are needed, collect them through normal conversation. Do not invent `/flow run`, `/flow show`, or `/flow author` commands.

## When to Activate

Activate this skill when the user:

- Invokes `/workflow:<slug>` to reuse a saved workflow.
- Asks to run a FusionFlow G4 workflow they already have ("跑一下这个 / 帮我跑 / 执行"). This skill does **not** ship runnable demo examples; "run" always means a concrete workflow the user has.
- Asks to save, list, load, or reuse a workflow declaration.
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
| `fusion_flow.workflow_runner` | Core IR to graph, plan, and checked dispatch |
| `psi_agent.workflow_execution` | dependencies, concurrency, timeouts, resources, and validated checkpoints |
| `fusion_flow.job_store` | private, versioned Human wait/checkpoint state |
| workspace `run_flow` / `run_flow_resume` tools | file/JSON boundary and ephemeral Session-backed Agent/Human-preparer Steps |
| workspace `clarify` tool | existing user-facing choice or free-text question formatter |

The skill's job is to:

1. Turn the user's intent into valid FusionFlow G4 source, or resolve the concrete G4 workflow they pointed to.
2. Save reusable source at the fixed path with existing file tools when requested.
3. Start it through `run_flow`.
4. If it reaches a Human Step, pass the nested `$fusion_flow/control.request` fields to the existing `clarify` tool, end the turn, and resume from the next user message.
5. Return only the final workflow output Artifact mapping.

## Intent Routing

Natural-language requests and the explicit `/workflow:` reuse command both map to these actions:

| What the user says (examples) | Action |
| --- | --- |
| "我能用这个干嘛 / 你能帮我做什么" | Describe capabilities in plain language (see "Capabilities" at the bottom) + offer to build a flow |
| `/workflow:<slug>` | Map the slug to `flows/workflows/<slug>/<slug>.workflow`, collect its declared inputs, and start one fresh run with `run_flow(flow_path=...)`. |
| "有哪些保存的工作流 / list workflows" | List the fixed `flows/workflows/` directory with existing file tools. |
| "加载 X / 看看保存的 X" | Read the canonical `.workflow` file with existing file tools. |
| "把刚生成的这个保存为 X" | After self-check, save the self-contained bundle at `flows/workflows/<slug>/`: the canonical `.workflow` file plus every referenced instruction Markdown file, preserving relative paths. |
| "跑一下这个 / 帮我跑 X / 执行这个 workflow" | Start the concrete workspace G4 source with `run_flow`; return outputs, or handle its Human request with `clarify`. |
| "接着上次那个跑 / 只重跑改动的部分" | Use `run_flow_resume` only for the active Human request already returned in this conversation. Arbitrary cache/resume is unsupported; otherwise offer a fresh run. |
| "看看结果 / 刚才那个跑完了吗" | Use the result already returned. A Human wait is not completion; wait for the user's answer rather than polling. |
| "环境齐不齐 / 能不能跑 / 帮我检查下" | Confirm that the G4 source parses and that all Steps use supported Agent, Human, or Program executors. |
| **"帮我写个工作流做 X / 帮我编排 / 我想让几个 agent ..."** | **Author a new FusionFlow G4 workflow from natural language. See "Authoring Mode" below.** |
| Anything else workflow-shaped | Interpret intent against this table |

## Running a Workflow

Use the workspace `run_flow` tool for FusionFlow G4 source. It validates the workflow and returns either the final output Artifacts or one persisted Human request under the reserved `$fusion_flow/control` key.

### Saved workflow command

The exact command form is:

```text
/workflow:<slug>
```

For example:

```text
/workflow:daily-brief
```

Treat the slug as a workflow name, never as a path. Do not accept a suffix or
trailing text. Read the canonical declaration and inspect `input_workflow(...)`
before execution. Resolve every declared input from the conversation; if any
value is missing, ask for it in normal conversation and end the turn without
calling `run_flow`. Do not invent command arguments, guess values, or call once
with the default empty input object merely to discover missing inputs.

Map the slug to `flows/workflows/<slug>/<slug>.workflow`. Once all inputs are
available, invoke `run_flow` exactly once with that `flow_path` and the complete
`inputs_json`. Use an empty input object only when the declaration has no inputs.
Every initial invocation is a fresh run; only a returned active Human request
may continue through `run_flow_resume`.

### Fixed-path reuse

- **Save:** use the existing file-writing capability to write the declaration
  at `flows/workflows/<slug>/<slug>.workflow`. If it references companion
  instruction Markdown, copy those files into the same canonical bundle while
  preserving every relative path; never leave a saved declaration pointing
  back to its one-off directory. This is an upper-layer instruction,
  not a new save/list/load operator. A parent Session or Agent Step may save a
  self-contained bundle generated within its assigned hierarchy. Saving never
  executes it.
- **List/read:** use existing directory and file tools.
- **Execute:** only the parent Session invokes `run_flow(flow_path=...)`.

### G4-only boundary

Only author and run FusionFlow G4 source. If the user points to any non-G4 workflow file, do not execute it, treat it as supported, or translate it implicitly. State that this skill accepts G4 source only. If the user explicitly asks to migrate that workflow, enter Authoring Mode and author one new G4 workflow from its intent.

Use a workspace-relative `.workflow` path under `flows/`. Never guess, scan for, or execute a path outside the workspace.

One runnable file must contain exactly one `workflow ... {}` block and use
supported Agent, Human, or Program executors.

Pass named workflow inputs through `inputs_json`. Do not rewrite the G4 source just to inject one run's values.

Pass run-local resource pools through `resource_capacities_json` only when the workflow declares `resource_requirement`.

Call `run_flow` once. If it returns output Artifacts, use them as the result. If it returns a `$fusion_flow/control` object with `status == "waiting_for_human"`, follow the Human protocol below. This reserved key cannot be a G4 Artifact ID, so an ordinary output Artifact named `status` is never control state.

### Human wait and resume

`run_flow_resume` is only for a pending Human request; it is not a general cache or arbitrary-step resume API.

When `run_flow` or `run_flow_resume` returns a sole top-level `$fusion_flow/control` object whose `status == "waiting_for_human"`:

1. Call the existing `clarify` tool with `$fusion_flow/control.request.question`, `.options`, `.recommended`, and `.default`.
2. Show the formatted text verbatim and **END THE TURN**. Do not call another tool and do not treat the question as an output Artifact.
3. On the next user message, map a numbered choice to its option label. If the user selected the generated `Other` line without supplying text, ask for that text first. For an open-ended request with a non-empty `default`, map an affirmative acceptance such as “可以” or “ok” to that exact default. Preserve other free text or structured content.
4. JSON-encode that value and call `run_flow_resume` with the exact `$fusion_flow/control.run_id` and `.request.request_id`.
5. If another Human request is returned, repeat this protocol. Otherwise report the final output Artifact mapping.

Never invent, reuse, or guess a run/request ID. A changed workflow source, stale request, or conflicting duplicate response is a stop-and-report error.

## Agent-, Human-, and Program-backed execution

Before executing a FusionFlow G4 workflow:

1. Ensure every Step executor is declared as exactly one of `Agent`, `Human`, or `Program`. Every Program must declare an explicit workspace-relative `program_path`.
2. Internally estimate cost and latency from the number of Agent and Program Steps. Fold that into one plain-language heads-up line.
3. Say the heads-up line, then run without adding another approval gate unless the user explicitly said "只生成别跑".

### Running is the runtime's job, not yours

Resolve the workspace-relative G4 path, submit it to `run_flow`, and report the returned output mapping. Do not reproduce parsing, dependency scheduling, resource leasing, or Step execution in the parent Session.

Agent-backed Steps must never invoke `run_flow` or start another workflow. A
Step may save a self-contained child declaration to the fixed reusable folder;
the parent Session remains the only launcher. Relative paths passed by a Step
to `read`, `write`, or `edit` resolve against the invoking psi workspace root,
independent of the launcher process working directory.

### Staged execution

Agent/Program-only workflows finish in the initial `run_flow` call. A Human workflow executes to the next Human frontier, persists a checkpoint, releases the current Session turn, and continues only through `run_flow_resume`. Do not call the legacy `.flow.ts` `flow_run(start/status/result)` tool for G4 source, and do not invent polling, PIDs, workers, or a separate approval inbox.

### Checkpoint integrity and resume safety

An `ExecutionCheckpoint` is valid only for its exact non-empty `workflow_id` and `plan_digest`. The digest is SHA-256 over a canonical serialization of the current graph semantics and explicit execution-plan fibers; matching Step and Artifact IDs from another workflow or graph version are not enough. Values must be strict, finite JSON values, and resume compares them recursively with type identity, so JSON `true` never matches JSON `1`. The executor also validates unique known operation IDs, dependency closure, and the exact set of materialized values before it skips any work.

The public `run_flow_resume` boundary additionally validates the current workflow definition against the digest recorded when the run was created; for a new instruction bundle, that definition includes both the `.workflow` source and every referenced Markdown instruction. Legacy source-only state-v2 runs retain their original path-identity semantics when resumed. Each resume is protected by an OS-released advisory file lock plus an in-process reservation guard; a leftover lock file is not ownership, and an abrupt process exit releases the live advisory lease. Do not copy checkpoints between workflows, edit persisted state, or bypass the matching `run_id` / `request_id` protocol.

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

When a call returns output Artifacts, summarize them. The runtime has already
persisted every materialized input, intermediate, selected, and final Artifact
as one Markdown file under the workflow bundle's
`runs/<run-id>/artifacts/` directory. String values are written verbatim;
non-string strict JSON values use a fenced `json` block. These user-visible
files are separate from the private Human checkpoint. When a call returns a
Human request, ask it through `clarify`; the request text is control state, not
an Artifact or completed result.

## File Locations

Paths are relative to the workspace:

| File | Location | Purpose |
| --- | --- | --- |
| `flows/<task-slug>/` | one-off authored FusionFlow G4 source |
| `flows/<task-slug>/instructions/*.md` | optional long-form instructions for that one-off source |
| `flows/workflows/<slug>/<slug>.workflow` | canonical reusable G4 source |
| `flows/workflows/<slug>/instructions/*.md` | optional long-form instructions for that reusable source |
| `<workflow-bundle>/runs/<run-id>/artifacts/*.md` | one Markdown file for every materialized Artifact in one run |
| `.psi/fusion-flow/runs/<run-id>.json` | private resumable state for workflows containing Human Steps |

## Authoring Mode

This is the flagship: turn a natural-language intent into a runnable FusionFlow G4 workflow. The user normally describes what they want in plain words ("帮我写个工作流做 X"). `/workflow:<slug>` reuses an already-saved workflow; it is not an authoring command.

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
2. **Model the workflow** — match the intent to one of the executable reference patterns below. Identify inputs, outputs, Agent-, Human-, or Program-backed Steps, Artifacts, dependencies, concurrency, resources, and timeouts. Let information dependencies determine graph depth: add an intermediate aggregation layer only when downstream work needs a coherent result from a distinct group of upstream Artifacts.
3. **Author one FusionFlow G4 source** — before writing, read `grammar/FusionFlow.g4` completely and treat it as the sole source of truth for FusionFlow syntax and preset operators. Use only declarations, assertions, terms, and operators documented there. Use the workspace-provided target path; never invent a second copy.
4. **Static self-check** — compare the source against `grammar/FusionFlow.g4` and the executable guardrails in this Skill. There is no separate validation tool.
5. **Start it once** — the user asked you to do a task, not to receive an implementation artifact. After the static self-check, say ONE friendly heads-up line ("🚀 方案定了，正在帮你跑，预计几分钟…" — a notice, NOT a question), then call `run_flow` once. A declared Human Step may later ask its own task-specific question through the Human protocol; that is part of execution, not an extra pre-run gate. **Do NOT ask "要不要跑 / 跑不跑" and do NOT wait for `跑`.** The only exception is when the user explicitly says "只生成别跑 / 先给我看看别执行".

Never mention the source file, its path, G4, operator names, static-check stages, or internal runnable artifacts to a non-technical user. From their side you are just doing the task they asked for. If they ask "你在干嘛 / 怎么做的", answer in plain business language ("我让几个分析分头跑、再汇总").

### Talking to the user while you work

Before calling `run_flow`, send one short heads-up such as "🚀 方案定了，正在帮你跑，预计几分钟…". The tools expose no node-level progress, so do not claim that an individual Step or branch has started or completed. When a call returns final outputs, lead with the result; when it returns a Human request, follow the Human protocol. Do not add an approval question between authoring and execution.

### Hard stops in Authoring Mode (real TUI failures, do not repeat)

These are not style preferences. Each one was observed corrupting a real author run. These bans apply **whether you're mid-author or already running**:

1. **Don't fake a result instead of running.** The user wants the real outcome. After the static self-check you call `run_flow` once (see step 5) — you do not stop and hand back a file, and you never substitute a made-up answer for an actual run.
2. **Write OR run any extra workflow source beyond the one `.workflow` file the task needs** — not an offline twin, not a "simpler version", not a "v2", not a "test harness". Companion instruction Markdown belongs to the same bundle and is not another workflow source. An offline twin with baked-in output is a forgery, not a test. If the user later wants one, that is a separate explicit request.
3. **Report numbers you did not get from a real run** — never present mock data, sample data, or figures from an unrelated file as if they are *this* flow's result. The only result you report is what the `run_flow` call actually returned. If it fails, report the failure instead of papering over it with invented numbers.
4. **Write outside the workspace-managed `flows/` location** — do not scan the filesystem for another flow project or create a sibling bundle copy. If the intended path is ambiguous, ask the user instead of guessing.
5. **Start another workflow from inside an Agent Step** — nested `run_flow` calls are forbidden. A Step may save a self-contained child declaration, but only the parent Session may launch it.

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
  step_instruction(security_review) == "Inspect the source for exploitable behavior and unsafe trust boundaries. Return prioritized findings with concrete evidence and remediation.";
  step_timeout(security_review) == 300;
  step_name(performance_review) == performance_review_name;
  step_instruction(performance_review) == "Identify material performance risks in the source. Explain the triggering workload, likely impact, evidence, and practical fixes.";
  step_timeout(performance_review) == 300;
  step_name(readability_review) == readability_review_name;
  step_instruction(readability_review) == "Review maintainability and clarity. Return specific high-impact issues, why they matter, and focused improvements.";
  step_timeout(readability_review) == 300;
  step_name(synthesize_report) == synthesize_report_name;
  step_instruction(synthesize_report) == "Combine the three reviews into one deduplicated report. Preserve evidence, resolve conflicts explicitly, prioritize actions, and separate findings from inference.";

  -- WORKFLOW CONFIGURATION
  max_concurrency(code_review) == 3;
  workflow_timeout(code_review) == 900;

}
```

### G4 source of truth

Before authoring, read `grammar/FusionFlow.g4` completely. It is the sole authority for surface syntax, declarations, assertions, formulas, terms, and preset operator signatures. This skill additionally defines which grammar-valid shapes the executable graph backend accepts.
Runner-specific typed catalog extensions use the grammar's generic operator-call syntax without changing its preset catalog. In particular, `depends_on(Step, Step) -> Bool` is registered by `fusion_flow/workflow_runner.py` and is executable there, but is not one of the grammar's 21 canonical preset operators.

### Executable graph backend guardrails

- Every dataflow operator has one owner and an explicit Artifact List RHS.
- Every executable `if` has the top-level shape `selected_artifact == if(condition, artifact_a, artifact_b);`. Never put `if` inside a dataflow List or another `if`; chain named intermediate Artifacts instead.
- Selection is eager: every candidate producer runs before the selected value is published.
- A Step instruction is either short JSON-style quoted text or a `"./..."` UTF-8 text-file path relative to the `.workflow` file. Use a companion Markdown file when the instruction needs multiple sections.

### Modeling rules

- Group assertions by concern in this exact order: `DATA FLOW`, `EXECUTOR ASSIGNMENT`, `STEP CONFIGURATION`, `SCHEDULING CONFIGURATION`, `WORKFLOW CONFIGURATION`. Omit empty groups.
- In `DATA FLOW`, declare the complete external input List once, then every Step's `consumes`/`produces` edges and named Artifact selections in dependency order, then the complete external output List once.
- Use exactly one symmetric Artifact dataflow contract: `input_workflow(workflow) == [artifact_a, artifact_b];`, `consumes(step) == [artifact_a, artifact_b];`, `produces(step) == [artifact_a, artifact_b];`, and `output_workflow(workflow) == [artifact_a, artifact_b];`. All four operators return `List`; even one Artifact requires an explicit List literal such as `[artifact]`. Never use these calls as standalone assertions, with `== True`, with an Artifact as a second argument, or through alternate multi variants.
- Bool shorthand is only for supported non-dataflow Bool operators such as `independent(step)` and `depends_on(step, predecessor)`. Keep `== False` explicit. Retain the right-hand value for every non-Bool operator.
- When the user supplies a grammar-valid literal as a typed constant name, including a restricted quoted ID or `"./..."` path, preserve that literal and use it directly as the required preset value; do not hide it behind an alias constant and an extra equality.
- Write every `step_instruction` as an executable task specification, not a label. State the objective, how to interpret consumed Artifacts, important constraints or evidence requirements, and the expected result. A name such as `"task_name"` is not an instruction.
- Keep each Step independently understandable and bounded. Let information dependencies determine the hierarchy: synthesize a distinct group of upstream Artifacts before combining it with other groups only when that intermediate result is genuinely consumed downstream. Do not add layers merely because a request is large, and do not collapse separable work into coarse Steps merely to minimize node count.
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
- The current graph runner supports resource scheduling and explicit `depends_on` ordering but still rejects `foreach_item` execution and `max_attempts` values other than `1`.
- Unknown or unsupported assertions remain residual and stop execution. Never delete them, comment them out, or bypass residual validation to make a run start.
- Lower executable `if` as a named Artifact selection: `selected_artifact == if(formula, artifact_a, artifact_b);`, followed by ordinary list dataflow such as `consumes(final_step) == [selected_artifact];`.
- Variables, quantifiers, rules, implications, biconditionals, query/SAT/optimization requests, local concept declarations, local operator declarations, and imperative blocks are outside this language.
- Never emit imports, imperative runtime calls, `run(...)`, or invented `parallel`/`pipeline`/`for` blocks.

#### Executor configuration

Declare every executor as exactly one of `Agent, Executor`, `Human, Executor`, or `Program, Executor`, bind it with `step_executor`, and give each Step a `step_instruction`. `allowed_tool` and `agent_system_prompt` are unsupported residual declarations and must not be emitted.

A Human Step may request an approval, choose among up to four options, or accept open-ended/structured input. Its dedicated preparation Agent receives the resolved instruction text, consumed Artifacts, and output contract, then emits the arguments for the existing `clarify` tool. It never asks the user itself, and its question text never becomes a produced Artifact. The next user response becomes the Human Step result after `run_flow_resume`. Multiple output Artifacts require a JSON object keyed exactly by those Artifact IDs; a zero-output Human Step acts as a pure gate.

Every Program must declare one explicit workspace-relative script or source path:

```text
const worker: Program, Executor;

program_path(worker) == "./bin/worker";
```

The public workspace runner has no catalog path resolver, so do not use a bare Path identity. `program_path` names one workspace-local regular file, not a shell command: do not append arguments, operators, pipes, or environment assignments. It does not need an executable bit, a shebang, or `chmod`. A specialized Program Agent may inspect the workspace, prepare or install the required language runtime, dependencies, compiler, or toolchain, and interpret or compile the declared file. The runtime supplies one newline-terminated JSON object as the authoritative stdin:

```json
{
  "instruction": "<resolved step_instruction text>",
  "inputs": {
    "<consumed Artifact ID>": "<runtime value>"
  }
}
```

Fidelity-mode interpreted execution does not accept an arbitrary argv from the Program Agent. The Agent selects one interpreter executable, and the host constructs exactly `[interpreter, declared_script, *logical_argv[1:]]`; do not add interpreter flags, inline code, another script, extra arguments, or reorder/drop the declared logical arguments. Compiled languages must use structured `compile_program`, which binds the compiler argv, declared source hash, artifact hashes, and one exact launch argv. `execute_program` may launch that compiled argv only after the host revalidates the registered source and artifacts. Preparation shells must not substitute for either structured operation.

The structured tools capture the actual argv, stdout and stderr bytes, exit code, and launch error separately. Once the real Program launches in fidelity mode, it is the sole attempt: never call `execute_program` again, even after a nonzero exit, invalid-input/domain error, or invalid output. Preserve that first result or error and let `submit_program_result` commit it deterministically; the model never authors the Artifact values itself. Before launch, the Program Agent may install missing environment or toolchain components and retry preparation failures, but it must not patch/replace the declared script, alter consumed input Artifact values or stdin, or reinterpret output. Only include the following exact standalone line in the resolved `step_instruction` when the user deliberately authorizes successful completion to outrank fidelity:

```text
Program execution policy: successful completion outranks fidelity.
```

No paraphrase enables adaptation. An authorized script or stdin adaptation must state a concrete `adaptation_reason`, and the consumed input Artifact values remain immutable.

For one produced Artifact, valid UTF-8 stdout is that Artifact's exact string value, including trailing newlines. For multiple produced Artifacts, stdout must be exactly one strict, finite JSON object keyed by all and only those Artifact IDs; `NaN`, `Infinity`, numeric overflow to infinity, nested non-finite values, and duplicate object keys are rejected. A Program that produces no Artifacts must not write stdout. Launch errors, nonzero exits, invalid UTF-8, and output-format errors become the same `{"$fusion_flow/program_error": {...}}` value on every declared output Artifact, preserving captured attempts; a failing zero-output Program raises because it has no Artifact for the diagnostic. Never reinterpret an error-valued Artifact as success.

`execute_program` runs without a shell in a separate POSIX process group or Windows Job Object. Shielded cleanup terminates members of that boundary on failure, declared Step/workflow timeout, cancellation, output overflow, and after a direct child exits with managed descendants still present. There is no internal 300-second Program timeout. Stdout and stderr are streamed with retained-output defaults of 4 MiB and 1 MiB respectively; set `PSI_FUSION_FLOW_PROGRAM_STDOUT_LIMIT_BYTES` or `PSI_FUSION_FLOW_PROGRAM_STDERR_LIMIT_BYTES` to a positive integer to override them. Exceeding either limit terminates the process boundary. Environment preparation can use shell tools, so this remains a trusted-workspace lifecycle boundary rather than a host sandbox; on POSIX, code that deliberately creates a new session/process group leaves the managed group.

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
  step_instruction(triage_step) == "Evaluate incoming_case against each supplied criterion. Produce one observation Artifact per criterion, citing the relevant evidence and marking uncertainty.";
  step_name(primary_handler_step) == primary_handler_name;
  step_instruction(primary_handler_step) == "Produce the primary handling result for incoming_case. Explain the decision, preserve material constraints, and return a result suitable for downstream selection.";
  step_name(review_handler_step) == review_handler_name;
  step_instruction(review_handler_step) == "Produce a reviewed handling result for incoming_case. Identify risks or ambiguities, resolve what the available evidence supports, and state any remaining uncertainty.";
  step_name(fallback_handler_step) == fallback_handler_name;
  step_instruction(fallback_handler_step) == "Produce a safe fallback result for incoming_case when stronger handling criteria are not met. Explain limitations and preserve enough context for finalization.";
  step_name(final_step) == final_name;
  step_instruction(final_step) == "Turn the selected_result into the final response. Preserve its supported conclusions, remove routing metadata, and make unresolved uncertainty explicit.";
}
```

- Build conditions with `=`, `!=`, `<`, `<=`, `>`, or `>=`; reserve `==` for the surrounding assertion.
- Combine comparisons with `!`, `AND`, and `OR`.
- Both branches must be declared Artifacts. The selection result must also be a declared Artifact.
- Every candidate producer runs. Selection is eager value routing, not lazy control flow.
- For more choices, chain named intermediate Artifacts in priority order; do not nest an `if` directly inside another `if`.
- Never place `if(...)` inline inside `input_workflow`, `consumes`, `produces`, or `output_workflow`; those operators still take explicit Artifact Lists.
- Do not replace candidate Artifacts with Boolean Step payloads or invent `switch`, `choice`, or conditional blocks.

Use free-form quoted text only where the typed catalog expects an `Instruction`. Do not encode shell commands, code, large source documents, or secrets as instruction text. Put a long instruction in a companion Markdown file; pass source material through input Artifacts.

### Anti-patterns to refuse

1. **Hand-writing imports or imperative runtime calls.** The authored program is FusionFlow G4 source.
2. **Inventing a keyword or operator.** Flexible call syntax does not make unknown names valid.
3. **Using `==` inside a condition or `=` for a workflow assertion.** These have different grammar roles.
4. **Using a symbolic instruction label as the task.** A Step needs actionable instruction text or a companion instruction file, not only a name such as `"task_label"`.
5. **Treating `max_attempts` as a workflow loop or score gate.** It only sets the attempt limit for one Step.
6. **Expanding a large item list without a cost check.** Every explicit Agent Step may consume a model call; keep the bounded expansion intentional.
7. **Inlining a large source document as an instruction.** Keep the task specification in the instruction and pass source material through an input Artifact.
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
  step_instruction(work_step) == "Complete the requested transformation using input_artifact, follow the user's stated constraints, and return the concrete result as output_artifact.";
}
```

Extend this skeleton only with syntax and preset operators documented in `grammar/FusionFlow.g4`.

### Static self-check

Before the initial `run_flow` call, inspect the source in order:

- every identity is declared with a supported concept;
- assertions use `==`, while formulas use comparison operators;
- each operator uses the documented arity and supported shape;
- each Step has a supported Agent, Human, or Program executor, name, instruction, and explicit data/control dependencies;
- no residual or unsupported operator is emitted.

This is a source review, not a second tool or CLI invocation. Actual parsing and compilation occur inside `run_flow`.

### Running it (automatic, right after the self-check)

1. Call `run_flow(flow_path=..., inputs_json=..., resource_capacities_json=...)` once. Omit resource capacities when the graph declares no resource requirement.
2. If it returns a `$fusion_flow/control` Human-wait envelope, follow the Human wait/resume protocol exactly. Do not present that envelope as the workflow result.
3. When a call returns output Artifacts, summarize them in plain language.
4. On error, report the compiler diagnostic or failed Step without creating a second workflow or bypassing the runner.

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

When the user asks what this skill can do ("你能帮我做什么 / 我能用这个干嘛"), lead with natural-language examples and mention the saved-workflow shortcut:

```
🐾 FusionFlow G4
用自然语言驱动多 Agent 工作流，也可以保存后用固定短指令复用：

  • "帮我写个工作流做 X / 帮我编排 ..."           → 用大白话描述需求，我帮你搭好并运行
  • "把这个保存为 daily-brief"                   → 保存到固定的 workflow 文件夹
  • "/workflow:daily-brief"                       → 按名称加载并全新运行一次
  • "跑一下刚才那个 / 帮我跑这个 workflow"        → 执行 G4 workflow；需要你审批或输入时直接在对话里问
  • "环境齐不齐 / 能不能跑"                        → 检查 G4、Agent/Human/Program executor 和资源声明

不附带现成可运行示例；你想要什么工作流，直接描述，我会写入 workspace 的 flows/ 目录。
```

## Security + Approvals

Agent Steps run through ephemeral psi Sessions with a filtered workspace tool snapshot; nested workflow launchers and `clarify` are unavailable to them. Program Steps run through separate specialized Sessions with workspace-inspection/environment-preparation tools plus structured `compile_program` and `execute_program`; their declared regular script/source file and working directory must resolve inside the workspace, but the script needs no executable permission. Fidelity-mode interpreted argv is host-built, compiled provenance is hash-bound, and a launched Program is never retried. Human instruction preparers receive only a workspace-confined, read-only `read` tool, so a referenced file cannot escape the workspace through `..`, an absolute path, or a symbolic link. Review user-supplied G4 source before execution, but do not add an approval gate unless the workflow itself declares a Human Step. Human interaction reuses the parent Session's existing `clarify` flow and never creates a separate approval UI. Refuse remote URLs: `run_flow` accepts workspace-local `.workflow` files only. Treat the Program Agent's shell-enabled environment preparation as trusted workspace execution, not as a host sandbox.
