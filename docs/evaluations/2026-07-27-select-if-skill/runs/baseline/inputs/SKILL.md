---
name: flow
description: Use when authoring or running FusionFlow G4 multi-agent workflows, when the user mentions FusionFlow, agent-flow, Fuclaw, or @agent-flow/core, or when a task needs coordinated agents, parallel sub-tasks, a multi-step pipeline, or inspection or resumption of a prior workflow run. Not for .prose files. Activated by task intent, not by slash commands.
metadata: { "openclaw": { "emoji": "🐾", "homepage": "https://github.com/fuclaw" } }
---

# FusionFlow G4 Skill (Fuclaw)

This skill is the author + run protocol for **FusionFlow G4 workflows** on **`@agent-flow/core`** (alias: Fuclaw). The LLM authors declarative, G4-conformant FusionFlow source; the configured runner executes it with a full **execution graph** for replay. Unlike OpenProse, where the LLM *is* the VM, here the VM is a Node.js process; the LLM orchestrates the run and reads its artifacts.

> **What you're working in.** The normal delivery is a **self-contained bundle**: the user copied the `fusion-flow/` folder somewhere, ran `npm install` once, and works inside it. Call that directory `<workDir>`. Everything below — G4 workflow source, the `.env`, and `runs/` artifacts — lives **relative to `<workDir>`**, NOT inside an unrelated checkout. This skill runs in any long-context LLM client (Claude Code / Cursor / Cherry Studio / Claude.ai); it does not depend on OpenClaw or any plugin install.

> **No slash commands.** This skill is triggered by **natural-language intent**, never by a `/flow xxx` command. The user just talks: "帮我写个并行调研的工作流" / "跑一下刚生成的那个" / "刚才那个跑完了吗". Do NOT teach, suggest, or expect any `/flow run` / `/flow show` / `/flow author` syntax — those slash commands do not exist and printing them to the user is a bug (a user in an environment without this skill installed will see "命令没找到"). Map what the user *means* to the actions below.

## When to Activate

Activate this skill when the user:

- Asks to run a FusionFlow G4 workflow they already have ("跑一下这个 / 帮我跑 / 执行"). This skill does **not** ship runnable demo examples; "run" always means a concrete workflow the user has.
- Asks to see the result of a previous run ("跑完了吗 / 看看结果 / 上次那个怎么样了")
- Mentions "agent-flow", "Fuclaw", or "@agent-flow/core"
- **Describes any task that needs a multi-agent workflow or agent collaboration**, even without saying "flow" — e.g. "让几个 agent 分别审一遍再汇总", "并行跑 N 个子任务再合并", "一步接一步处理(先 A 再 B 再 C)", "多角度评审后汇总", "把这件事拆成多个 agent 协作". If the task clearly benefits from orchestrating more than one agent / parallel branches / a multi-step pipeline, enter **Authoring Mode** (below) and offer to build a flow.

When in doubt about whether a task is "workflow-shaped": if it would take **two or more coordinated LLM steps** (fan-out/fan-in, an artifact pipeline, or per-item work), it qualifies — activate and propose a flow. A single one-shot question does not.

### HARD RULE: when you recognize a multi-agent task, your job is to BUILD A FLOW — not to do it yourself

Once a task is workflow-shaped (multiple agents / parallel branches / multi-step pipeline / per-item work), your **one default action** is to enter Authoring Mode and build a FusionFlow G4 workflow. That is the entire point of this skill — the flow runtime spawns and coordinates the sub-agents; **you do not play those sub-agents yourself**.

Do **NOT** offer "我直接帮你做这一次" as an option, and especially do **NOT** make it the default. Building the flow IS how you help — there is no faster "just do it manually" path that's better; doing it by hand throws away the runtime (parallelism, the execution graph, replay, the reusable artifact) and contradicts "one intent = one flow".

❌ **Real failure to never repeat** (observed in testing): user said "让几个 AI 从安全/性能/可读性分别审一段代码再汇总". The agent replied with "方式 A：我直接帮你审这次代码 / 方式 B：给你做成可复用工作流" — offering to personally act as the three reviewers, with the manual path listed first as the default. **Wrong.** The correct response is to go straight into Authoring Mode and build the review flow: three reviewer Steps consume the same input, then one final Step consumes all three review artifacts. No A/B menu, no "I'll just review it myself".

✅ **Correct shape**: "🐾 这是个多 agent 协作任务，我来帮你搭一个工作流：3 个审查 agent（安全/性能/可读性）并行审 → 一个汇总 agent 合并成带严重等级的报告。" Then run the author loop (understand → model → author → validate → one heads-up line → **run it**).

The only time you don't build a flow is when the user **explicitly** says they just want a one-off answer and not a tool ("别给我搭工具，就这一次，你直接说结论"). Even then, confirm — don't assume.

Do **not** activate this skill for `.prose` files — those belong to OpenProse.

## Architectural Difference vs OpenProse

| Aspect | OpenProse | FusionFlow G4 (Fuclaw) |
| --- | --- | --- |
| VM substrate | LLM session simulating prose.md | Node.js running the `@agent-flow/core` runtime |
| Program format | `.prose` markdown DSL | declarative FusionFlow G4 source |
| Sub-agent spawn | OpenClaw `sessions_spawn` | `@agent-flow/core` shelling out to an external agent CLI (claude / openclaw / hermes / psi) |
| State directory | `.prose/runs/<id>/` | `<workDir>/runs/<id>/` |
| Replay artifact | `bindings/*.md` + `state.md` | `bindings/` + `trace/` + **`execution-graph.json`** |
| Control flow | Prose VM keywords | workflow assertions, preset operators, and artifact dependencies |

The skill's job is to:

1. Turn the user's intent into valid FusionFlow G4 source, or resolve the concrete G4 workflow they pointed to.
2. Submit it to the configured workflow runner.
3. Surface the resulting `runs/<id>/` and explain the execution graph.

## Intent Routing

The user talks in natural language. Map what they **mean** to one of these actions. There is **no slash-command syntax** — never echo a `/flow xxx` form back at them.

| What the user says (examples) | Action |
| --- | --- |
| "我能用这个干嘛 / 你能帮我做什么" | Describe capabilities in plain language (see "Capabilities" at the bottom) + offer to build a flow |
| "跑一下这个 / 帮我跑 X / 执行这个 workflow" | Run the concrete FusionFlow G4 source the user points at, then surface `runId` + key bindings. **This skill does not ship runnable demo examples** — there is no keyword→example catalog to resolve against. |
| "接着上次那个跑 / 只重跑改动的部分" | (v0.6) Re-run reusing the old `runs/<runId>/`; cached bindings skip the LLM. See "Resume" section |
| "看看结果 / 刚才那个跑完了吗 / 上次跑得怎么样" | Read `<workDir>/runs/<runId>/execution-graph.json` (or the most recent run) and walk the user through it |
| "环境齐不齐 / 能不能跑 / 帮我检查下" | Verify Node, the configured runner, the engine CLI on PATH, and authoring readiness (`<workDir>` has `node_modules`); run `npm run doctor` if present |
| **"帮我写个工作流做 X / 帮我编排 / 我想让几个 agent ..."** | **Author a new FusionFlow G4 workflow from natural language. See "Authoring Mode" below.** |
| Anything else workflow-shaped | Interpret intent against this table |

## Running a Program

Use the workspace's configured workflow runner for FusionFlow G4 source. It validates and executes the workflow as one operation; do not expose internal runtime artifacts to the user. In the psi-agent workspace, use the `flow_run` protocol described under "A flow run is long".

### G4-only boundary

Only author and run FusionFlow G4 source. If the user points to any non-G4 workflow file, do not execute it, treat it as supported, or translate it implicitly. State that this skill accepts G4 source only. If the user explicitly asks to migrate that workflow, enter Authoring Mode and author one new G4 workflow from its intent.

`<workDir>` is **the directory the user is working in** — almost always the copied `fusion-flow/` bundle folder. How to resolve it:

1. **Bundle or source-repo case:** use the directory that owns the configured FusionFlow runner, `grammar/FusionFlow.g4`, `examples/`, and `runs/`. Authored G4 workflows go in `<workDir>/examples/`; artifacts land in `<workDir>/runs/`.
2. **psi-agent workspace case:** if the workspace system prompt assigns a path such as `flows/<task-slug>/`, use that workspace-managed path instead of `<workDir>/examples/`. The workspace instruction wins; do not relocate the source beside the runtime.
3. **If you genuinely can't tell which folder to work in** (e.g. several candidates), ask the user once in plain language: "你把 fusion-flow 文件夹拷到哪了？我在那个目录里帮你跑。" Then **remember it for the rest of this session** — don't re-ask.

Never guess a path without verification. Never hardcode `D:/...` or any machine-specific path. Don't go scanning the filesystem for "a flow project" — work in the folder the user is actually in (see Hard-stop #4 in Authoring Mode).

Pass named workflow inputs through the configured runner's input-override mechanism. Do not rewrite the G4 source just to inject one run's values.

Capture the `runId` and run directory returned by the runner.

Use that `runId` to walk the user through the run (see "Reading a Run").

### Resume

If a run stopped halfway, or the user wants to re-execute only the parts whose inputs changed, submit the same G4 workflow through the configured runner with the prior `runId` as its resume target.

How it behaves:

- The same `runDir` is reused — no new `runs/<id>/` directory is created.
- For each cacheable Step, the runtime computes an input fingerprint from its resolved instruction, executor configuration, named inputs, and upstream artifacts.
- If `bindings/<name>.md` exists **and** its recorded fingerprint matches, that Step is skipped and the graph node is marked `cached: true`.
- If the G4 assertions, catalog identities, named inputs, or upstream artifacts changed, the fingerprint changes and that Step re-runs. Dependent downstream Steps re-run as their inputs change.
- If the prior run lacks the required fingerprint metadata, do not claim it is reusable; report that resume is unavailable for that run.

When recommending a resume run to the user, surface what will be reused vs re-run by reading the existing `bindings/*.meta.json` files first — never promise "everything cached" without checking.

Tokens reported in `meta.json` only count new calls. Total cost across the original and resumed execution is the sum of both.

## Running an Agent-backed G4 Workflow: Pre-flight

Before executing a FusionFlow G4 workflow with Agent-backed Steps:

1. Confirm the configured engine CLI is on PATH (`claude --version` for the default). v0.7 auth is the engine CLI's own config, not a key in `<workDir>/.env`.
2. Internally estimate cost/latency from the flow's node count (each LLM call is a CLI subprocess, ~3-10s each; a fan-out with N reviewer sessions ≈ N calls). Fold that into the single plain-language heads-up line ("预计几分钟") — don't dump the per-node math on the user.
3. Say the one heads-up line, then **run — do not ask "要不要跑" or wait for approval.** The user already asked for the task; building + running the flow is how you do it. (Only exception: they explicitly said "只生成别跑".)

A workflow whose Steps all use local deterministic Executors and no Agent can skip the LLM pre-flight.

### Running is the runtime's job, not yours

When the user asks to run a workflow, your ENTIRE job is: resolve `<workDir>`, submit the G4 source to the configured runner, then report what the runtime returned. The runtime spawns subprocesses under its own designed environment — git-bash autodetect, clean-env baseline, Windows shell handling. **You do not pre-flight the subprocess environment.** Specifically, before running:

- Do NOT check whether `uv` / `python` / `claude` is "on PATH" in your shell. Your PATH is not the runtime's PATH. A missing binary in your shell does NOT mean the flow will fail.
- Do NOT `pip install` / `npm install` / modify PATH to "fix" a tool you think is missing.
- Do NOT inspect or second-guess a catalog-backed external Executor's command. Run the workflow as-is and let the runtime resolve it.
- Do NOT run ad-hoc interactive probes such as bare `node` or bare `python`. They can open a REPL/prompt and appear to hang.

Just run the flow. If it actually fails, then go to "When a run fails".

### A flow run is long — never let a timeout kill it

A workflow run is not a quick shell command. Every LLM step is an external CLI subprocess (10–20s cold start), and parallel or multi-step work routinely takes minutes. Two hard rules:

1. **If your client exposes a background/long-running run tool, use it — do NOT run the flow through a foreground shell tool that has a short default timeout.** In the psi-agent workspace this tool is `flow_run`: call `flow_run(action="start", flow_path=...)` to launch the flow in the background, then loop `flow_run(action="status", run_token=...)` — each call blocks until the next node finishes, the flow ends, or a keepalive window elapses — and report progress from what it returns; when it reports done, call `flow_run(action="result", run_token=...)`. This is the ONLY correct way to run a flow there.
2. **If no background run tool exists** (plain terminal client), use the configured FusionFlow runner with the longest timeout your run tool allows. The runtime writes node-level progress to `runs/<runId>/progress.jsonl` (one JSON line per node start/end, with the node label) while it runs — read that file to report progress, do not assume a silent run is stuck.

### When a run fails

A non-zero runner exit or failed external-execution Step is a **STOP-and-report point**. Do exactly these three steps, in order, then stop:

1. Report the exit code, the `runId`, and the error tail the runtime already printed.
2. Read AT MOST two files to explain it: `runs/<runId>/meta.json` and the failed node's `bindings/<name>.md`.
3. State your single best hypothesis, then **STOP and hand back to the user. End your turn.**

These actions are FORBIDDEN when a run fails. If you find yourself about to do any of them, STOP — you are drifting:

- ❌ Editing the workflow source or any internal runtime artifact, or creating a `-modified` / `-quick` / `.bak` copy. **Never write or alter files in `examples/` to work around a failure.**
- ❌ Running the wrapped command yourself (`uv run ...`, `python -m ...`, `python -c ...`) "to see what happens".
- ❌ Launching probe processes, trial flags, kill-and-retry loops, or any multi-minute autonomous debug session.
- ❌ `pip install` / `npm install` / editing PATH / env to "fix" the environment.

The user did NOT ask for an autonomous debug session — they asked you to run a file and report. After reporting the failure, **STOP and hand back**. Do NOT create or run a `*-offline` / mock version of the failing flow "to show the skeleton works" — a mock with baked-in output is a forgery, not a proof (see the global no-mock rule below). If you think a deeper check is worth it, propose it as a single command and let the user decide.

### Don't fake or guess progress

While a run is in flight, your only sources of truth are the runtime's stdout and the files it writes under `runs/<runId>/`. Do NOT infer progress by scanning the external tool's output directory and guessing — timestamps on pre-existing files (e.g. a prior run's artifacts) will mislead you into reporting "it's processing" when nothing new is happening. If you can't see fresh runtime output, say "no new output yet", not a fabricated status.

## Reading a Run

When the user wants to see a run's result ("跑完了吗 / 看看结果"), perform these reads in parallel (resolve the target `runId`, or pick the most recent `<workDir>/runs/*/` if they said "上次 / last"):

- `<workDir>/runs/<runId>/meta.json` — top-level status, durations, call counts
- `<workDir>/runs/<runId>/execution-graph.json` — the tree
- `<workDir>/runs/<runId>/bindings/` — list filenames; read `final.md` if present
- `<workDir>/runs/<runId>/trace/*.json` — only on demand (these are large)

Then summarize for the user. **Default (non-technical user): keep it short and friendly** — lead with the result, not the metrics:

1. **一句话结果** — 跑成功了没 + 大概多久，e.g. "🎉 跑完啦，用了约 40 秒。" Don't open with token counts or agent counts.
2. **产出** — point them at the final answer: read `bindings/final.md` (or the most relevant binding) and show *that*, not the graph.
3. **出问题才说细节** — only if a node failed: say which step and your one best hypothesis.

**Developer / on-demand:** if the user asks "用了多少 token / 给我看执行图 / 哪几步" then show the technical verdict line — `ok in 12.3s, 4 agents, 18.2k input tokens, 2.1k output tokens` (read from `meta.json` → `totalTokens`/`llmCalls`/`durationMs`) + graph shape (node `type` tree) + per-branch results. Never dump the full JSON; be a curator.

## File Locations

Paths below are relative to the resolved `<workDir>`:

| File | Location | Purpose |
| --- | --- | --- |
| `<workDir>/examples/` | bundle or source-repo G4 workflow source | Authored FusionFlow G4 programs |
| `flows/<task-slug>/` | psi-agent workspace source location when assigned by the workspace system prompt | Authored FusionFlow G4 programs in workspace mode |
| `<workDir>/runs/<runId>/` | created by each run | Per-run artifacts (graph, bindings, trace) |
| `<workDir>/.env` | user responsibility (optional) | FLOW_ENGINE selection + optional ANTHROPIC_* passthrough for claude engine + FLOW_PSI_* for psi-agent |

## Authoring Mode

This is the flagship: turn a natural-language intent into a runnable FusionFlow G4 workflow. The user just describes what they want in plain words ("帮我写个工作流做 X") — there is no command to invoke and no implementation format to explain.

> **NO-MOCK RULE (global, applies to all of Authoring Mode).** When you build a flow for the user, author **exactly one** real FusionFlow G4 workflow and NEVER fabricate a mock/offline/simplified twin to "test" or "demonstrate" it. A twin with hardcoded sample output, fake numbers, or a fake executor standing in for the real work is a **forgery** — it always "passes" regardless of what the real flow does, so it proves nothing and misleads the user. Validate the one real workflow, then actually run it. If the user *explicitly* later asks for an offline twin, that's a separate request you confirm first — never self-initiate one.
>
> (This rule is about flows **you author for the user**. Repository-owned offline examples are sanctioned test infrastructure; the ban is on *fabricating a new mock of the user's flow*.)

### When to enter Authoring Mode

- User describes a workflow they want built: "帮我写个工作流 ..." / "make a flow that ..." / "帮我编排 ..." / similar.
- User asks "帮我写一个 flow ..." / "make a flow that ..." / similar in any LLM client.
- User edits existing FusionFlow G4 source and asks you to "rewrite" or "扩展".
- **User describes a workflow-shaped task without naming "flow"** — anything needing two or more coordinated agents / parallel branches / a multi-step pipeline / per-item work (see "When to Activate"). In that case, don't wait for the word "flow": offer to build one, then run the author loop below.

### The 5-step author loop

1. **Understand intent** — restate the user's goal in 1 sentence. If genuinely ambiguous, ask **one** clarifying question (don't grill them). Note whether the user looks like a *developer* (asked to edit FusionFlow G4 source or mentioned operators) — that's the only case where you show technical detail later. Everyone else gets the minimal plain-language summary.
2. **Model the workflow** — match the intent to one of the reference patterns below. Identify inputs, outputs, Steps, Executors, Artifacts, dependencies, concurrency, retries, and timeouts.
3. **Author one FusionFlow G4 source** — before writing, read `grammar/FusionFlow.g4` completely and treat it as the sole source of truth for FusionFlow syntax and preset operators. Use only declarations, assertions, terms, and operators documented there. Use the workspace-provided target path; never invent a second copy.
4. **Validate** — run the workspace's FusionFlow validation entry point. Fix reported syntax, arity, type, or capability errors yourself, up to **3 rounds**. After 3 failed rounds, stop and tell the user what remains.
5. **Run it directly** — the user asked you to do a task, not to receive an implementation artifact. Once validation passes, say ONE friendly heads-up line ("🚀 方案定了，正在帮你跑，预计几分钟…" — a notice, NOT a question), then immediately run the flow. **Do NOT ask "要不要跑 / 跑不跑" and do NOT wait for `跑`.** The only exception is when the user explicitly says "只生成别跑 / 先给我看看别执行".

Never mention the source file, its path, G4, operator names, validation stages, or internal runnable artifacts to a non-technical user. From their side you are just doing the task they asked for. If they ask "你在干嘛 / 怎么做的", answer in plain business language ("我让几个分析分头跑、再汇总").

### Talking to the user while you work (友好进度，面向小白)

Assume the user is **non-technical** and just wants to know "你在帮我干活，没卡住". Your messages to them are NOT the runtime logs — the runtime's `[session] / [parallel] / [run]` stdout is for *you* to read, not to paste at the user. Translate each phase into ONE short friendly Chinese status line, then go quiet until the next phase. Do not paste raw logs, file paths, primitive names, token counts, or the execution graph unless the user asks.

Use lines like these (one per phase, not all at once):

| 阶段 | 对用户说（示例，按场景改写） |
| --- | --- |
| 开始理解需求 / 选方案 | 🐾 正在帮你规划… |
| 方案定了、准备开跑 | 🚀 方案定了，正在帮你跑，预计几分钟… |
| 某个子任务在跑 | ⏳ 正在跑「<这一步在干嘛的大白话>」… |
| 多个子任务并行 | ⏳ <N> 个子任务同时进行中… |
| 子任务陆续回来 | ✅ 「<这一步>」完成 |
| 全部跑完 | 🎉 跑完啦，结果在下面 |

Note: there is **no "等批准" phase** — validation 通过后直接进入"正在帮你跑"。Don't insert a "要不要跑？" gate between planning and running.

> **HARD RULE — 先开口，再起跑（遮住执行层的首字延迟）。** 每次 LLM 调用都是一个外部 CLI 子进程，从 spawn 到吐第一个字通常要 **10–20 秒**（子进程启动 + 模型冷启动），这是架构现状、你改不了。但小白看到的不是 runtime 的 stdout，是**你**——所以**在你执行那条会卡十几秒的命令之前，必须先发出一句友好进度行**（上表 `🚀 方案定了，正在帮你跑，预计几分钟…`），让用户那一侧的"首字响应"是秒级的。**顺序是硬的：先把状态行发给用户，再去启动 runner。** 绝不允许"闷头等十几秒、跑完才一次性回话"——那会让用户以为卡死。N 个分支并行时，开跑前那句就说"⏳ N 个子任务同时进行中，预计几分钟…"，把"为什么要等"提前讲清。一句够了，别每隔几秒刷屏。

Rules: keep each line **one sentence**, no jargon (use the 业务语言 map above — say "一次分析" not "session", "同时处理" not "parallel"). Don't narrate every internal tool call; only surface the phase transitions a human cares about. When something is genuinely slow (a cold-starting sub-agent), a single "⏳ 子任务正在生成中，第一次启动会慢一点…" beats silence — but don't spam it every few seconds.

### Hard stops in Authoring Mode (real TUI failures, do not repeat)

These are not style preferences. Each one was observed corrupting a real author run. These bans apply **whether you're mid-author or already running**:

1. **Don't fake a result instead of running.** The user wants the real outcome. After validation passes you RUN the flow (see step 5) — you do not stop and hand back a file, and you never substitute a made-up answer for an actual run. The runtime spawns the sub-agents; your job is to kick it off and report what comes back.
2. **Write OR run any extra workflow source beyond the one file the task needs** — not an offline twin, not a "simpler version", not a "v2", not a "test harness". One intent = one file. An offline twin with baked-in output is a forgery, not a test. If the user later wants one, that is a separate explicit request.
3. **Report numbers you did not get from a real run** — never present mock data, sample data, or figures you pulled from an old `output/` dir / `validation_report.json` as if they are *this* flow's result. The only result you report is what the run you just executed actually returned. If a run fails, report the failure (see "When a run fails") — don't paper over it with invented numbers.
4. **Work anywhere other than the resolved `<workDir>`** — the workflow source belongs in the workspace-managed location under `<workDir>`, and `<workDir>` is the directory resolved in "Running a Program" (the folder the user is actually working in, NOT a copy you found by searching the filesystem). Do NOT scan `D:/tmp`, the workspace root, or any sibling `*-publish` / extra bundle copy for "a flow project" and start writing/`npm install`/running there. If you can't resolve `<workDir>`, ask the user — do not pick a random copy. (Observed: agent ignored the folder the user was in and built files inside an unrelated publish snapshot.)

The real run is how you deliver — there is no "spend-free preview" step to offer the user. Validation is the pre-run check; after it passes, run.

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
| **Artifact pipeline** | Each Step produces the Artifact consumed by the next Step. Use `max_attempts` only as the attempt limit for a configured Step. | Writing process, ETL, refine-and-check work. |
| **Per-item work + merge** | Use `foreach_item` for the repeated Step. Declare a List that also participates in graph relations as `Artifact, List`, then connect it with `produces(step) == [result_collection]` and `consumes(merge_step) == [result_collection]`. | N PRs, issues, docs, or log records into one report. |
| **Conditional term selection** | Keep every candidate result explicit, then use `if(formula, then_term, else_term)` where one term is expected. Compose formulas with `!`, `AND`, and `OR`; nest `if` for priority selection. | Select one checker-compatible term for a downstream assertion without inventing control-flow syntax. |
| **Composite workflow** | Combine only the artifact chains, fan-out/fan-in, per-item relations, and conditional term selections above. | When one simple pattern does not cover the task. |

Before reporting a missing capability for a conditional request, first model it as term selection and validate it. Do not infer branch execution or eager/lazy semantics that belong to the checker/runtime. If the task still cannot be expressed with syntax and preset operators documented in `grammar/FusionFlow.g4`, report the missing capability. Never invent a keyword or operator to make the source look complete.

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

const review_model: Model;
const review_engine: Engine;
const review_api: ApiBase;
const high_effort: ReasoningEffort;
const read_tool: Tool;

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
  max_attempts(security_review) == 2;
  step_name(performance_review) == performance_review_name;
  step_instruction(performance_review) == performance_instruction;
  step_timeout(performance_review) == 300;
  max_attempts(performance_review) == 2;
  step_name(readability_review) == readability_review_name;
  step_instruction(readability_review) == readability_instruction;
  step_timeout(readability_review) == 300;
  max_attempts(readability_review) == 2;
  step_name(synthesize_report) == synthesize_report_name;
  step_instruction(synthesize_report) == synthesis_instruction;

  -- WORKFLOW CONFIGURATION
  max_concurrency(code_review) == 3;
  workflow_timeout(code_review) == 900;

  -- AGENT CONFIGURATION
  agent_config(security_agent, review_model, review_engine, review_api);
  agent_config(performance_agent, review_model, review_engine, review_api);
  agent_config(readability_agent, review_model, review_engine, review_api);
  agent_config(editor_agent, review_model, review_engine, review_api);

  allowed_tool(security_agent, read_tool);
  allowed_tool(performance_agent, read_tool);
  allowed_tool(readability_agent, read_tool);
  reasoning_effort(security_agent) == high_effort;
}
```

### G4 source of truth

Before authoring, read `grammar/FusionFlow.g4` completely. It is the sole authority for file structure, declarations, assertions, formulas, terms, `if(...)`, and preset operator names and signatures. Examples in this skill illustrate modeling only; they do not add syntax or operators. If this skill conflicts with the grammar, follow the grammar.

### Modeling rules

- Group assertions by concern in this exact order: `DATA FLOW`, `EXECUTOR ASSIGNMENT`, `STEP CONFIGURATION`, `WORKFLOW CONFIGURATION`, `AGENT CONFIGURATION`. Omit empty groups.
- In `DATA FLOW`, declare the complete external input List once, then every Step's `consumes`/`produces` edges, then the complete external output List once.
- Use exactly one symmetric Artifact dataflow contract: `input_workflow(workflow) == [artifact_a, artifact_b];`, `consumes(step) == [artifact_a, artifact_b];`, `produces(step) == [artifact_a, artifact_b];`, and `output_workflow(workflow) == [artifact_a, artifact_b];`. All four operators return `List`; even one Artifact requires an explicit List literal such as `[artifact]`. Never use these calls as standalone assertions, with `== True`, with an Artifact as a second argument, or through alternate multi variants.
- Bool shorthand is only for non-dataflow presets that genuinely return Bool, such as `agent_config(...)` and `allowed_tool(...)`. Keep `== False` explicit. Retain the right-hand value for every non-Bool operator.
- When the user supplies a grammar-valid literal as a typed constant name, including a restricted quoted ID or `"./..."` path, preserve that literal and use it directly as the required preset value; do not hide it behind an alias constant and an extra equality.
- Model sequencing through Artifact edges: a Step that produces an Artifact precedes a Step that consumes it. Declaration order does not define execution order; Artifact edges define dependencies.
- Preserve the external data boundary from the user's intent. Fan-out Steps that analyze the same subject reuse one shared input Artifact; do not split it into synthetic per-branch workflow inputs.
- Emit every explicitly requested relation. Every operand must be a declared grammar term: `_` and `...` are not wildcards. Declare typed constants for required operands, or omit an optional configuration instead of inserting placeholders.
- Model fan-out by making several steps consume the same artifact.
- Model fan-in with `consumes(step) == [artifact_a, artifact_b];`.
- Model per-item work with `foreach_item(step, items) == item_result;`. If a List also participates in graph relations, declare it as `Artifact, List` and still place it inside an explicit List RHS.
- Bind each step to its executor with `step_executor`.
- Configure concurrency, retries, timeouts, resources, and agent limits with the corresponding preset operators.
- Use `if(formula, then_term, else_term)` wherever one term is expected. It selects a term; it is not a Step, block, loop, quality gate, or scoring mechanism.
- Variables, quantifiers, rules, implications, biconditionals, query/SAT/optimization requests, local concept declarations, local operator declarations, and imperative blocks are outside this language.
- Never emit imports, imperative runtime calls, `run(...)`, or invented `parallel`/`pipeline`/`for` blocks.

#### Conditional term selection

Keep every candidate result explicit and produced by a Step. The canonical downstream shape is `consumes(final_step) == [if(formula, artifact_a, artifact_b)];`. Use a nested `if` only to select the compatible Artifact consumed by the downstream Step:

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
const final_result: Artifact;

const triage_step: Step;
const primary_handler_step: Step;
const review_handler_step: Step;
const fallback_handler_step: Step;
const final_step: Step;

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
  consumes(final_step) == [
    if(
      (primary_observation = primary_criterion) AND !(block_observation = block_criterion),
      primary_result,
      if(
        (review_observation = review_criterion) OR (exception_observation = exception_criterion),
        review_result,
        fallback_result
      )
    )
  ];
  produces(final_step) == [final_result];
  output_workflow(priority_routing) == [final_result];

  -- EXECUTOR ASSIGNMENT
  step_executor(triage_step) == triage_agent;
  step_executor(primary_handler_step) == primary_handler;
  step_executor(review_handler_step) == review_handler;
  step_executor(fallback_handler_step) == fallback_handler;
  step_executor(final_step) == final_consumer;
}
```

- Build conditions with `=`, `!=`, `<`, `<=`, `>`, or `>=`; reserve `==` for the surrounding assertion.
- Combine comparisons with `!`, `AND`, and `OR`.
- For more choices, continue nesting `if` expressions in priority order.
- Let the checker validate branch concept compatibility. Let lowering/runtime decide dependencies and eager/lazy branch evaluation.
- Do not replace candidate Artifacts with Boolean Step payloads, refuse valid term selection because `if` is not a Step, or invent `switch`, `choice`, or conditional blocks.

#### Worked example: homogeneous per-item work + one summary

Use this shape for N documents, PRs, issues, or log records:

```fusionflow
const items: Artifact, List;
const analyzed_items: Artifact, List;
const item_result: Artifact;
const final_summary: Artifact;

const analyze_item: Step;
const merge_summary: Step;
const analyze_name: StepName;
const merge_name: StepName;
const analyze_instruction: Instruction;
const merge_instruction: Instruction;
const analyst: Agent, Executor;
const editor: Agent, Executor;

workflow summarize_items {
  -- DATA FLOW
  input_workflow(summarize_items) == [items];
  consumes(analyze_item) == [items];
  foreach_item(analyze_item, items) == item_result;
  produces(analyze_item) == [analyzed_items];
  consumes(merge_summary) == [analyzed_items];
  produces(merge_summary) == [final_summary];
  output_workflow(summarize_items) == [final_summary];

  -- EXECUTOR ASSIGNMENT
  step_executor(analyze_item) == analyst;
  step_executor(merge_summary) == editor;

  -- STEP CONFIGURATION
  step_name(analyze_item) == analyze_name;
  step_instruction(analyze_item) == analyze_instruction;
  step_name(merge_summary) == merge_name;
  step_instruction(merge_summary) == merge_instruction;
}
```

`items` and `analyzed_items` are collection-valued Artifacts. The repeated Step is declared once; every dataflow relation still names its Artifact identities inside an explicit List RHS. The merge Step consumes the complete collection and produces one final Artifact.

Do not encode free-form command strings, code, prompts, or secrets as quoted constants. `grammar/FusionFlow.g4` permits restricted quoted IDs and workspace-relative `"./..."` paths; neither is a general string.

### Anti-patterns to refuse

1. **Hand-writing imports or imperative runtime calls.** The authored program is FusionFlow G4 source.
2. **Inventing a keyword or operator.** Flexible call syntax does not make unknown names valid.
3. **Using `==` inside a condition or `=` for a workflow assertion.** These have different grammar roles.
4. **Treating quoted constants as prompt strings.** They are restricted IDs or explicit workspace-relative paths, not free-form text.
5. **Treating `max_attempts` as a workflow loop or score gate.** It only sets the attempt limit for one Step.
6. **Agentic/external execution over a large item list without a cost check.** Each item may start a subprocess; keep expensive executors for work that needs them.
7. **Inlining a large document into an instruction identity or runtime argument.** Store the document under `<workDir>` and pass a path to a read-enabled executor.
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

### Validation self-repair

Run the workspace's FusionFlow validation entry point after authoring. Fix diagnostics in source order:

- declaration/order/name errors → repair the declaration or identifier;
- assertion/formula errors → check `==` versus comparison operators;
- arity errors → use the exact preset signature;
- concept/type errors → correct the declared concept or operator argument;
- unknown capability/operator → report the unmet requirement; never approximate or invent it.

Re-run validation after each repair. Stop after 3 failed rounds and report the remaining diagnostic instead of looping indefinitely.



### Running it (automatic, right after validation)

1. Submit the validated FusionFlow G4 source to the configured workflow runner (no approval step — this follows directly from step 5 of the author loop). In psi-agent, use `flow_run(action="start", flow_path=...)`, then `status`, then `result`.
2. Capture `[run] <runId>` and `[run] dir: ...` from stdout (these are for *you*, not for the user).
3. After completion (or on error), fall back to the "Reading a Run" protocol — summarize the run for the user in plain business language. Lead with the result, not the file or the metrics.
4. Only if the user asks how to re-run it later, tell them the workspace-specific invocation. Do not volunteer the source path or internal execution details.

### What Authoring Mode is NOT

- It is **not** a guarantee the workflow gets good *content*. We control structure, validation, and execution; the task instructions still depend on the user's domain.
- It is **not** auto-iterating on content. The user reads the result and asks for changes, but there is no "要不要跑" gate between validation and the first run.
- It is **not** a reason to show implementation details to a business user. Technical users can ask for the FusionFlow G4 source and structure on demand.

## Doctor Checks

When the user asks to check their environment ("环境齐不齐 / 能不能跑"): in the bundle, the quickest path is `cd <workDir> && npm run doctor` (the bundle ships a `doctor.mjs`). Prefer that over hand-written shell checks.

All manual checks must be non-interactive and bounded. Never run a command that can wait for user input, especially bare `node` or bare `python`, and never install missing packages as part of a doctor check.

If `npm run doctor` is not present, check manually:

```bash
command -v node && node --version        # need >= 20; never run bare `node`
test -f <workDir>/.env                   # optional in v0.7 (FLOW_ENGINE config); the engine CLI's own auth is what matters
test -d <workDir>/node_modules           # must exist; if missing, report "run npm install in <workDir>" and stop
claude --version                         # the configured FLOW_ENGINE CLI must be on PATH (default: claude)
```

Report each as ✓ / ✗. Never echo any API key value. In v0.7 `<workDir>/.env` no longer holds an LLM-direct key — auth is the engine CLI's own config (the claude engine will passthrough `ANTHROPIC_*` from the shell environment if present, else use claude's own login).

### Authoring readiness

Authoring no longer depends on any external reference workflow — the full-featured example is **inlined in this SKILL.md** ("Reference patterns" → "Full-featured in-context example"). Readiness requires readable `grammar/FusionFlow.g4` and the workspace's FusionFlow validation entry point.

Validate the inlined canonical example through the same entry point used for authored workflows. This is an internal readiness check; do not describe its implementation stages to the user.

If validation errors, report:

```
✗ Authoring Mode unsafe (workflow validation failing)
  Reason: <first 3 diagnostics>
  Fix the validation or catalog errors before entering Authoring Mode.
```

Otherwise:

```
✓ Authoring Mode ready (G4 source read, inlined example valid)
```

### Engine readiness (v0.7)

In v0.7 every Agent-backed G4 Step shells out to an external agent CLI (`FLOW_ENGINE`, default `claude`). Check the configured engine's CLI is on PATH. A Step using only a local deterministic or external catalog Executor needs no LLM-engine preflight.

```bash
claude --version                                    # if FLOW_ENGINE=claude (default)
psi-agent run --help                                # if FLOW_ENGINE=psi
# Windows only: git-bash must exist for the claude CLI
test -f /c/Program\ Files/Git/bin/bash.exe || \
  test -f /d/Program\ Files/Git/bin/bash.exe       # one of the candidates
```

For the psi-agent engine, configure the workspace and profile. The runner invokes the configured
engine directly:

```bash
FLOW_ENGINE=psi
FLOW_PSI_WORKSPACE=/abs/path/to/psi-agent/examples/a-simple-bash-only-workspace
FLOW_PSI_PROFILE=fusion          # psi-agent reads ~/.psi-agent/config.toml
# Optional: point at a non-default psi-agent config file
FLOW_PSI_CONFIG=/abs/path/to/config.toml
# Optional: connect to an existing AI backend instead of psi-agent's configured provider
FLOW_PSI_AI_SOCKET=http://127.0.0.1:9000/v1
```

Keep provider URLs and API keys in psi-agent's own profile config, not in this Fuclaw/OpenProse `.env`. `FLOW_PSI_AI` / `FLOW_PSI_MODEL` / `FLOW_PSI_BASE_URL` / `FLOW_PSI_API_KEY` exist only as temporary overrides for local debugging.

If the engine CLI is missing:

```
✗ FLOW_ENGINE=claude not ready
  Install Claude Code from https://docs.claude.com/en/docs/claude-code, or set FLOW_ENGINE to an installed CLI (openclaw / hermes / psi).
```

If on Windows and no git-bash found in any candidate path, the runtime will best-effort probe at call time and surface a clear error. Doctor reports:

```
⚠️ claude engine may fail on Windows: no git-bash found in default paths.
  Set CLAUDE_CODE_GIT_BASH_PATH to your bash.exe location, or install Git for Windows.
```

## Capabilities

When the user asks what this skill can do ("你能帮我做什么 / 我能用这个干嘛"), describe these in plain language — never as slash commands. The user just talks naturally and you map intent (see "Intent Routing"):

```
🐾 FusionFlow G4 — Fuclaw / @agent-flow/core
用自然语言驱动多 agent 工作流，带完整执行图回放。直接跟我说就行，不用记任何命令：

  • "帮我写个工作流做 X / 帮我编排 ..."           → 用大白话描述需求，我帮你搭好并运行
  • "跑一下刚才那个 / 帮我跑这个 workflow"        → 执行你手上的 workflow，跑完报告 runId
  • "接着上次那个跑 / 只重跑改动的部分"           → (v0.6) 复用上次结果，缓存的步骤跳过不重算
  • "刚才那个跑完了吗 / 看看上次结果"             → 带你看懂某次跑的执行图和产出
  • "环境齐不齐 / 能不能跑"                        → 检查 runner + agent engine + authoring 就绪度

我不再附带「现成 demo 例子」——你想要什么工作流，直接描述，我现写给你。
工作目录：就是你拷走的 fusion-flow 文件夹，`npm install` 一次即可（见 "Running a Program" 一节）
```

## Security + Approvals

Workflows run with the privileges of the process executing them. When a user points to existing local FusionFlow G4 source for the first time, show it before execution and review its requested tools, external commands, file access, and secret/config references. This does not add an approval gate to a workflow authored for the user's current request. For remote URLs, refuse: running workflow source directly from a URL is intentionally unsupported.
