---
name: flow
description: Fuclaw / @agent-flow/core skill pack. Activate whenever a task needs a multi-agent workflow or agent collaboration — `.flow.ts` files, "agent-flow" / "Fuclaw" mentions, OR a user asking to coordinate several agents / run sub-tasks in parallel / build a multi-step pipeline / inspect a workflow result / "帮我编排 / 并行跑 / 多个 agent 协作 / 工作流 / 跑完了吗 / 帮我写个工作流". Runs and inspects TypeScript-based multi-agent workflows. Triggered by natural-language intent, not by any slash command.
metadata: { "openclaw": { "emoji": "🐾", "homepage": "https://github.com/fuclaw" } }
---

# OpenFlow Skill (Fuclaw)

This skill is the OpenClaw integration for **`@agent-flow/core`** (alias: Fuclaw) — a TypeScript runtime that executes multi-agent workflows and emits a full **execution graph** for replay. Unlike OpenProse, where the LLM *is* the VM, here the VM is a Node.js process; the LLM only orchestrates running it and reading its artifacts.

> **No slash commands.** This skill is triggered by **natural-language intent**, never by a `/flow xxx` command. The user just talks: "帮我写个并行调研的工作流" / "跑一下刚生成的那个" / "刚才那个跑完了吗". Do NOT teach, suggest, or expect any `/flow run` / `/flow show` / `/flow author` syntax — those slash commands do not exist and printing them to the user is a bug (a user in an environment without this skill installed will see "命令没找到"). Map what the user *means* to the actions below.

## When to Activate

Activate this skill when the user:

- Asks to run a `.flow.ts` file they already have — e.g. one you just generated in Authoring Mode, or a file they point you at ("跑一下这个 / 帮我跑 / 执行"). (This skill does **not** ship runnable demo examples; "run" always means a concrete `.flow.ts` the user has.)
- Asks to see the result of a previous run ("跑完了吗 / 看看结果 / 上次那个怎么样了")
- Mentions "agent-flow", "Fuclaw", or "@agent-flow/core"
- **Describes any task that needs a multi-agent workflow or agent collaboration**, even without saying "flow" — e.g. "让几个 agent 分别审一遍再汇总", "并行跑 N 个子任务再合并", "一步接一步处理(先 A 再 B 再 C)", "多角度评审 / 打分选边", "把这件事拆成多个 agent 协作". If the task clearly benefits from orchestrating more than one agent / parallel branches / a multi-step pipeline, enter **Authoring Mode** (below) and offer to build a flow.

When in doubt about whether a task is "workflow-shaped": if it would take **two or more coordinated LLM steps** (fan-out, pipeline, loop, or judge-then-branch), it qualifies — activate and propose a flow. A single one-shot question does not.

### HARD RULE: when you recognize a multi-agent task, your job is to BUILD A FLOW — not to do it yourself

Once a task is workflow-shaped (multiple agents / parallel branches / multi-step pipeline / judge-then-branch), your **one default action** is to enter Authoring Mode and build a `.flow.ts`. That is the entire point of this skill — the flow runtime spawns and coordinates the sub-agents; **you do not play those sub-agents yourself**.

Do **NOT** offer "我直接帮你做这一次" as an option, and especially do **NOT** make it the default. Building the flow IS how you help — there is no faster "just do it manually" path that's better; doing it by hand throws away the runtime (parallelism, the execution graph, replay, the reusable artifact) and contradicts "one intent = one generated flow".

❌ **Real failure to never repeat** (observed in testing): user said "让几个 AI 从安全/性能/可读性分别审一段代码再汇总". The agent replied with "方式 A：我直接帮你审这次代码 / 方式 B：给你做成可复用工作流" — offering to personally act as the three reviewers, with the manual path listed first as the default. **Wrong.** The correct response is to go straight into Authoring Mode and build the review flow (3 reviewer agents in `flow.parallel` → a synthesizer). No A/B menu, no "I'll just review it myself".

✅ **Correct shape**: "🐾 这是个多 agent 协作任务，我来帮你搭一个工作流：3 个审查 agent（安全/性能/可读性）并行审 → 一个汇总 agent 合并成带严重等级的报告。" Then run the author loop (understand → pick primitives → generate → typecheck → one heads-up line → **run it**).

The only time you don't build a flow is when the user **explicitly** says they just want a one-off answer and not a tool ("别给我搭工具，就这一次，你直接说结论"). Even then, confirm — don't assume.

Do **not** activate this skill for `.prose` files — those belong to OpenProse.

## Architectural Difference vs OpenProse

| Aspect | OpenProse | OpenFlow (Fuclaw) |
| --- | --- | --- |
| VM substrate | LLM session simulating prose.md | Node.js + tsx running `core/src` |
| Program format | `.prose` markdown DSL | `.flow.ts` TypeScript |
| Sub-agent spawn | OpenClaw `sessions_spawn` | `@agent-flow/core` shelling out to an external agent CLI (claude / openclaw / hermes / psi) |
| State directory | `.prose/runs/<id>/` | `core/runs/<id>/` |
| Replay artifact | `bindings/*.md` + `state.md` | `bindings/` + `trace/` + **`execution-graph.json`** |
| Control flow | Prose VM keywords | `flow.parallel / if / loopUntil / map / pipeline / retry / block / ...` |

The skill's job is **not** to interpret a DSL. The job is to:

1. Resolve the user's `.flow.ts` target.
2. Shell out to `tsx <file>` inside the core checkout.
3. Surface the resulting `runs/<id>/` and explain the execution graph.

## Intent Routing

The user talks in natural language. Map what they **mean** to one of these actions. There is **no slash-command syntax** — never echo a `/flow xxx` form back at them.

| What the user says (examples) | Action |
| --- | --- |
| "我能用这个干嘛 / 你能帮我做什么" | Describe capabilities in plain language (see "Capabilities" at the bottom) + offer to build a flow |
| "跑一下这个 / 帮我跑 X / 执行这个 .flow.ts" | Run a `.flow.ts` the user already has (e.g. one you just generated in Authoring Mode) via `tsx`, surface `runId` + key bindings. **This skill no longer ships runnable demo examples** — there is no keyword→example catalog to resolve against; the only thing you run is a `.flow.ts` the user points at or that author just produced. |
| "接着上次那个跑 / 只重跑改动的部分" | (v0.6) Re-run reusing the old `runs/<runId>/`; cached bindings skip the LLM. See "Resume" section |
| "看看结果 / 刚才那个跑完了吗 / 上次跑得怎么样" | Read `core/runs/<runId>/execution-graph.json` (or the most recent run) and walk the user through it |
| "环境齐不齐 / 能不能跑 / 帮我检查下" | Verify Node, tsx, `core/.env` (API keys), `corePath` config, and authoring readiness |
| **"帮我写个工作流做 X / 帮我编排 / 我想让几个 agent ..."** | **Generate a new `.flow.ts` from natural language. See "Authoring Mode" below.** |
| Anything else workflow-shaped | Interpret intent against this table |

## Running a Program

```bash
# Inside core checkout, with .env present
cd <corePath>
npx tsx <abs-path-to-flow-file>
```

`<corePath>` is plugin config (optional in schema). Resolution order on every run/show invocation:

1. Read it from the plugin config in `~/.openclaw/openclaw.json` under `plugins.entries.open-flow.config.corePath`.
2. If unset, **try auto-detect**: if the OpenClaw process CWD (or any ancestor of it) contains a `core/package.json` whose `name` field equals `@agent-flow/core`, treat that `<dir>/core` as `corePath`.
3. If still unset, **ask the user once** in plain Chinese / English: "你的 FuClaw repo 根目录的绝对路径是？需要 .../core 这一层。"
4. Once you have a verified path (the directory exists and contains `package.json` with `@agent-flow/core`), **remember it for the rest of this session** and use it for all subsequent runs. Do not re-ask within the same session.
5. If the user wants to persist it across sessions, tell them this command (verified against OpenClaw 2026.5.18):
   ```bash
   openclaw config set plugins.entries.open-flow.config.corePath /abs/path/to/FuClaw-OpenProse/core
   ```
   The dot-path writes into `~/.openclaw/openclaw.json`. Verify with:
   ```bash
   openclaw config get plugins.entries.open-flow.config.corePath
   ```

Never guess a path without verification. Never hardcode `D:/...` or any user-specific path.

To pass `flow.input` overrides, append `--input.<name>=<value>` after the file path:

```bash
npx tsx examples/flow-author-20260606-001.flow.ts --input.question="MySQL 还是 Postgres？" --input.context="..."
```

After the run, the script prints two lines you must capture:

```
[run] <runId>
[run] dir: <abs-path-to-runs/runId>
```

Use that `runId` to walk the user through the run (see "Reading a Run").

### Resume (`--resume`) — v0.6

If a run blew up halfway, or the user wants to re-execute only the parts whose inputs changed, append `--resume=<runId>` to the `tsx` command:

```bash
npx tsx examples/flow-author-20260606-001.flow.ts --resume=20260529-030614-slnw7h
npx tsx examples/flow-author-20260606-001.flow.ts --resume=last        # latest run in core/runs/
```

How it behaves:

- The same `runDir` is reused — no new `runs/<id>/` directory is created.
- For each `flow.session` / `flow.call`, the runtime computes an `inputHash` over `(provider, model, system, userPrompt, temperature, maxTokens)` for sessions, or `(serviceName, args)` for calls.
- If `bindings/<name>.md` exists **and** the meta's `inputHash` matches, the LLM call is skipped — the cached content is returned, and the graph node is marked `cached: true`.
- If the user changed a prompt, model, or `--input.*`, the hash mismatches and that node re-runs. Downstream nodes that depend on it will also re-run (their inputs differ now).
- Old runs from before v0.6 don't have `inputHash` in their meta. `--resume` falls back to lenient mode: name-only match. After the first re-run, those bindings get hashes written and become strict.

When recommending a resume run to the user, surface what will be reused vs re-run by reading the existing `bindings/*.meta.json` files first — never promise "everything cached" without checking.

Caveats to mention if asked:

- `flow.service` body changes are invisible to the hash. If the user rewrites a service implementation, tell them to either rename the service or delete its `bindings/*.md` first.
- Tokens reported in `meta.json` only count *new* calls. Total cost across the original + resume is the sum of both runs' meta.

## Running an LLM Flow: Pre-flight

Before executing anything that calls the LLM (any `.flow.ts` the user wrote or that author generated, i.e. anything using `flow.agent` / `flow.session` / `flow.evaluate`):

1. Confirm the configured engine CLI is on PATH (`claude --version` for the default). v0.7 auth is the engine CLI's own config, not a key in `core/.env`.
2. Internally estimate cost/latency from the flow's node count (each LLM call is a CLI subprocess, ~3-10s each; a fan-out with N reviewer sessions ≈ N calls). Fold that into the single plain-language heads-up line ("预计几分钟") — don't dump the per-node math on the user.
3. Say the one heads-up line, then **run — do not ask "要不要跑" or wait for approval.** The user already asked for the task; building + running the flow is how you do it. (Only exception: they explicitly said "只生成别跑".)

A flow whose LLM calls are all replaced by `flow.service` mocks (no `flow.agent` / `flow.session`) hits no network and can skip the pre-flight.

### Running is the runtime's job, not yours

When the user asks to run a `.flow.ts`, your ENTIRE job is: resolve `corePath`, run `cd <corePath> && npx tsx <file>` (one command), then report what the runtime printed. The runtime spawns subprocesses (`uv`, `python`, etc.) under its own designed environment — git-bash autodetect, clean-env baseline, Windows shell handling. **You do not pre-flight the subprocess environment.** Specifically, before running:

- Do NOT check whether `uv` / `python` / `claude` is "on PATH" in your shell. Your PATH is not the runtime's PATH. A missing binary in your shell does NOT mean the flow will fail.
- Do NOT `pip install` / `npm install` / modify PATH to "fix" a tool you think is missing.
- Do NOT inspect or second-guess the flow's `command:` field. If `flow.exec` says `command: "uv"`, run the flow as-is and let the runtime resolve it.

Just run the flow. If it actually fails, then go to "When a run fails".

### When a run fails

A non-zero exit (or a `flow.exec` non-zero `exitCode`) is a **STOP-and-report point**. Do exactly these three steps, in order, then stop:

1. Report the exit code, the `runId`, and the error tail the runtime already printed.
2. Read AT MOST two files to explain it: `runs/<runId>/meta.json` and the failed node's `bindings/<name>.md`.
3. State your single best hypothesis, then **STOP and hand back to the user. End your turn.**

These actions are FORBIDDEN when a run fails. If you find yourself about to do any of them, STOP — you are drifting:

- ❌ Editing the `.flow.ts`, or creating a `-modified` / `-quick` / `.bak` copy of it. **Never write or alter files in `examples/` to work around a failure.**
- ❌ Running the wrapped command yourself (`uv run ...`, `python -m ...`, `python -c ...`) "to see what happens".
- ❌ Launching probe processes, trial flags, kill-and-retry loops, or any multi-minute autonomous debug session.
- ❌ `pip install` / `npm install` / editing PATH / env to "fix" the environment.

The user did NOT ask for an autonomous debug session — they asked you to run a file and report. After reporting the failure, **STOP and hand back**. Do NOT create or run a `*-offline` / mock version of the failing flow "to show the skeleton works" — a mock with baked-in output is a forgery, not a proof (see the global no-mock rule below). If you think a deeper check is worth it, propose it as a single command and let the user decide.

### Don't fake or guess progress

While a run is in flight, your only sources of truth are the runtime's stdout and the files it writes under `runs/<runId>/`. Do NOT infer progress by scanning the external tool's output directory and guessing — timestamps on pre-existing files (e.g. a prior run's artifacts) will mislead you into reporting "it's processing" when nothing new is happening. If you can't see fresh runtime output, say "no new output yet", not a fabricated status.

## Reading a Run

When the user wants to see a run's result ("跑完了吗 / 看看结果"), perform these reads in parallel (resolve the target `runId`, or pick the most recent `core/runs/*/` if they said "上次 / last"):

- `core/runs/<runId>/meta.json` — top-level status, durations, call counts
- `core/runs/<runId>/execution-graph.json` — the tree
- `core/runs/<runId>/bindings/` — list filenames; read `final.md` if present
- `core/runs/<runId>/trace/*.json` — only on demand (these are large)

Then summarize for the user. **Default (non-technical user): keep it short and friendly** — lead with the result, not the metrics:

1. **一句话结果** — 跑成功了没 + 大概多久，e.g. "🎉 跑完啦，用了约 40 秒。" Don't open with token counts or agent counts.
2. **产出** — point them at the final answer: read `bindings/final.md` (or the most relevant binding) and show *that*, not the graph.
3. **出问题才说细节** — only if a node failed: say which step and your one best hypothesis.

**Developer / on-demand:** if the user asks "用了多少 token / 给我看执行图 / 哪几步" then show the technical verdict line — `ok in 12.3s, 4 agents, 18.2k input tokens, 2.1k output tokens` (read from `meta.json` → `totalTokens`/`llmCalls`/`durationMs`) + graph shape (node `type` tree) + per-branch results. Never dump the full JSON; be a curator.

## File Locations

| File | Location | Purpose |
| --- | --- | --- |
| `prose.md` analogue | None — the runtime IS the VM | TS code in `core/src/` |
| `core/src/flow.ts` | Plugin install root | All `flow.xxx` API impl |
| `core/src/run.ts` | Plugin install root | `run()` wrapper + Agent factory |
| `core/examples/` | Plugin install root | Bundled examples |
| `core/runs/<runId>/` | Plugin install root | Per-run artifacts (graph, bindings, trace) |
| `core/.env` | User responsibility | FLOW_ENGINE selection + optional ANTHROPIC_* passthrough for claude engine + FLOW_PSI_* for psi-agent |

## Authoring Mode

This is the v0.3 flagship: turn a natural-language intent into a runnable `.flow.ts`. The user does not write TypeScript; you do. The user just describes what they want in plain words ("帮我写个工作流做 X") — there is no command to invoke.

> **NO-MOCK RULE (global, applies to all of Authoring Mode).** When you build a flow for the user, you generate **exactly one** real `.flow.ts` and you NEVER fabricate a mock/offline/simplified twin of it to "test" or "demonstrate" it. A generated `*-offline.flow.ts` (or any file with hardcoded sample output, fake numbers, or a stubbed `flow.service` standing in for the real `flow.session`/`flow.exec`) is a **forgery** — it always "passes" regardless of what the real flow does, so it proves nothing and misleads the user. Verifying the generated code = `npm run typecheck`; the real results come from actually running the one real flow (which you do automatically after typecheck — see the 5-step loop). If the user *explicitly* later asks for an offline twin, that's a separate request you confirm first — never self-initiate one.
>
> (This rule is about flows **you generate for the user**. The repo's own 6 reference `flow-*-offline.flow.ts` under `core/examples/` are sanctioned test infrastructure maintained by the project — running those when the user asks is fine. The ban is on *fabricating a new mock of the user's flow*.)

### When to enter Authoring Mode

- User describes a workflow they want built: "帮我写个工作流 ..." / "make a flow that ..." / "帮我编排 ..." / similar.
- User asks "帮我写一个 flow ..." / "make a flow that ..." / similar in OpenClaw TUI.
- User edits an existing `.flow.ts` and asks you to "rewrite" or "扩展".
- **User describes a workflow-shaped task without naming "flow"** — anything needing two or more coordinated agents / parallel branches / a multi-step pipeline / judge-then-branch (see "When to Activate"). In that case, don't wait for the word "flow": offer to build one, then run the author loop below.

### The 5-step author loop

1. **Understand intent** — restate the user's goal in 1 sentence. If genuinely ambiguous, ask **one** clarifying question (don't grill them). While doing this, note whether the user looks like a *developer* (asked to edit a `.flow.ts`, mentioned primitives/TS) — that's the only case where you show technical detail in step 5. Everyone else gets the minimal plain-language summary.
2. **Pick primitives** — match intent to one of the 5 patterns (see "Reference Patterns" below). Decide which `flow.xxx` are needed. Don't be exotic; reach for the primitives the inlined example uses.
3. **Generate** the file at `<corePath>/examples/flow-author-<YYYYMMDD>-<NNN>.flow.ts`. Follow the standard skeleton in "Code Template".
4. **Compile** — run `cd <corePath> && npm run typecheck` (which is `tsc --noEmit` with the project's tsconfig). **Do NOT use `npx tsc --noEmit <file>`** on a single file — that bypasses tsconfig and falls back to commonjs defaults, producing fake errors about top-level await / import.meta. Always use the npm script. Fix any real errors yourself, up to **3 rounds**. After 3 rounds of failures, stop and tell the user what you tried.
5. **Run it directly** — the user asked you to do a task, not to hand them a file. Once typecheck passes, say ONE friendly heads-up line ("🚀 方案定了，正在帮你跑，预计几分钟…" — a notice, NOT a question), then immediately run the flow. **Do NOT ask "要不要跑 / 跑不跑" and do NOT wait for `跑`.** Building the file is an internal step toward doing the task; the user wants the result, so produce it. (The only exception: the user *explicitly* said "只生成别跑 / 先给我看看别执行" — then stop after typecheck. Absent that, run.)

Never mention the `.flow.ts` file, its path, primitive names, or "我生成了一个文件" to the user. From their side you are just doing the task they asked for. If they ask "你在干嘛 / 怎么做的", answer in plain business language ("我让几个分析分头跑、再汇总"), never "我写了个 TypeScript 文件然后执行它".

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

Note: there is **no "等批准" phase** — typecheck 通过后直接进入"正在帮你跑"。Don't insert a "要不要跑？" gate between planning and running.

> **HARD RULE — 先开口，再起跑（遮住执行层的首字延迟）。** 每次 LLM 调用都是一个外部 CLI 子进程，从 spawn 到吐第一个字通常要 **10–20 秒**（子进程启动 + 模型冷启动），这是架构现状、你改不了。但小白看到的不是 runtime 的 stdout，是**你**——所以**在你执行那条会卡十几秒的命令之前，必须先发出一句友好进度行**（上表 `🚀 方案定了，正在帮你跑，预计几分钟…`），让用户那一侧的"首字响应"是秒级的。**顺序是硬的：先把状态行发给用户，再去 spawn/`tsx`。** 绝不允许"闷头等十几秒、跑完才一次性回话"——那会让用户以为卡死。`flow.parallel` 有 N 个分支时，开跑前那句就说"⏳ N 个子任务同时进行中，预计几分钟…"，把"为什么要等"提前讲清。一句够了，别每隔几秒刷屏。

Rules: keep each line **one sentence**, no jargon (use the 业务语言 map above — say "一次分析" not "session", "同时处理" not "parallel"). Don't narrate every internal tool call; only surface the phase transitions a human cares about. When something is genuinely slow (a cold-starting sub-agent), a single "⏳ 子任务正在生成中，第一次启动会慢一点…" beats silence — but don't spam it every few seconds.

### Hard stops in Authoring Mode (real TUI failures, do not repeat)

These are not style preferences. Each one was observed corrupting a real author run. These bans apply **whether you're mid-author or already running**:

1. **Don't fake a result instead of running.** The user wants the real outcome. After typecheck passes you RUN the flow (see step 5) — you do not stop and hand back a file, and you never substitute a made-up answer for an actual run. The runtime spawns the sub-agents; your job is to kick it off and report what comes back.
2. **Write OR run any extra `.flow.ts` beyond the one file the task needs** — not an `*-offline.flow.ts` twin, not a "simpler version", not a "v2", not a "test harness". One intent = one file. An offline twin with baked-in output (mock numbers, OR the real numbers you looked up) is a forgery, not a test: it always "passes" regardless of what the real tool does. The real run IS the verification. If the user later wants an offline twin, that is a separate explicit request.
3. **Report numbers you did not get from a real run** — never present mock data, sample data, or figures you pulled from an old `output/` dir / `validation_report.json` as if they are *this* flow's result. The only result you report is what the run you just executed actually returned. If a run fails, report the failure (see "When a run fails") — don't paper over it with invented numbers.
4. **Work anywhere other than the resolved `corePath`** — the generated file goes in `<corePath>/examples/`, and `corePath` is the directory resolved in "Running a Program" (the symlinked skill's repo, NOT a copy you found by searching the filesystem). Do NOT scan `D:/tmp`, the OpenClaw workspace, or any sibling `*-publish` / bundle copy for "a flow project" and start writing/`npm install`/running there. If you can't resolve `corePath`, ask the user — do not pick a random copy. (Observed: agent ignored the real repo and built files inside an unrelated publish snapshot.)

The real run is how you verify and how you deliver — there is no "spend-free preview" step to offer the user. typecheck is your only pre-run check; after it passes, run.

### Heads-up line (说一句就开跑，不是 gate)

Keep this **minimal**. A real investor ("悠悠") and an internal teammate ("张浩") both bailed on the authoring flow because the summary was wall-to-wall framework jargon (`parallel / pmap / reduce / evaluate / choice / 原语 / 异构复合工作流`). Their words: "这么专业应该不是给我这种用户用的吧" / "这表述太专业太多专有名词了，我都不知道咋聊了". This line is a **告知**, not a go/no-go gate — you say it and then immediately run. It exists so the user isn't surprised by a few minutes' wait / the cost, NOT to ask permission.

**Default heads-up (use this for everyone unless they're clearly a developer):** one plain-language sentence on what they'll get + a rough time estimate. No primitive names, no API names, no pattern names, no file path, no per-step breakdown, no token math, no "要不要跑".

```
🚀 我来帮你做：<一句话讲清楚要产出什么，e.g. "并行调研 5 个 AI 方向，汇总打分后给你一份带『重点关注 / 投资机会』的总报告">，预计几分钟，这就开始。
```

That's it — one line, then you run. Do **not** add `做什么 / 要多久 / 你会拿到` as separate fields, do not list steps, do not show 🔧/🎯/📝 lines, do not show the file path, do not ask for approval. If the user is clearly a **developer** (asked to edit a `.flow.ts`, mentioned primitives/TS, or explicitly asks "用了哪些原语 / 给我看结构 / 文件在哪"), you may then show the technical detail **on demand**:

```
🔧 parallel × 1, choice × 1, session × 5 ｜ 套路：异构 fan-out + 选边 ｜ 文件：examples/flow-author-<id>.flow.ts
```

Only show that line when a developer explicitly asks for it. Never push it at a business user, and never volunteer the file path unprompted.

**Jargon → plain-language map (so the default sentence stays clean).** Never say the left; say the right:

| 框架黑话（别说） | 业务语言（要这么说） |
| --- | --- |
| 原语 / primitive | 步骤 |
| session | 一次调研 / 一次分析 |
| evaluate / choice | 打分 / 选出重点 |
| pmap / parallel | 同时做 / 并行处理 |
| reduce | 逐层汇总 |
| pipeline | 一步接一步 |
| 异构复合工作流 | 多方向 + 分层汇总 |
| token / LLM 调用 | （折成）几分钟 / 花多少钱 |


Token estimate rule of thumb: each `flow.session` ≈ 1500 input + 800 output tokens; each `flow.evaluate`/`choice` ≈ 2000 input + 50 output. Sum, then convert to RMB at provider's listed rate (火山 ARK Agent Plan 包月里这是 0 元，flag it as "≈ 0 (Agent Plan)").

### Reference Patterns (the 5 archetypes)

Match the user's intent to one of these five shapes, then write the flow yourself. There is **no external reference file to read** — the single full-featured example **inlined below** is your only reference. Study it, then adapt; do not fetch or copy a separate `.flow.ts` from disk (author writes fresh each time).

| Pattern | Shape + primitives | When to use |
| --- | --- | --- |
| **Heterogeneous fan-out + verdict** | N different agents on same input → LLM picks a label. `flow.parallel` + `flow.choice` | PR review, multi-perspective audit, content moderation. |
| **Multi-step pipeline + final gate** | each step's output feeds the next → quality gate. `flow.pipeline` + `flow.retry` around the last evaluator | writing process, ETL, refine loop. |
| **Homogeneous pmap + reduce** | same processing on N items → merge. `flow.pmap` + `flow.reduce` | N PRs / issues / docs / log lines → one summary. |
| **LLM-decided loop** | open-ended brainstorm where the LLM decides "enough". `flow.loopUntil` + an `flow.evaluate` stop gate | hypothesis generation, alternative listing, exploratory analysis. |
| **Composite with reusable block** | several of the above mixed; variable N, each through a `defineBlock` subgraph | use when one simpler pattern is *almost* right but some axis is variable. |

#### Full-featured in-context example (your only reference — read this, don't fetch a file)

This composite flow (technology selection) exercises **11 primitives** and folds in every pattern above: `defineBlock`/`runBlock` (reusable subgraph), `parallel` (heterogeneous fan-out over N candidates), `evaluate` (numeric self-score inside the block), `choice` (LLM picks the winner), `session`/`agent` (researcher + synthesizer), plus `input`/`save`/`output`. For a simpler single-pattern shape, use only the matching slice — e.g. just `parallel` + `choice` for fan-out + verdict, or `pmap` + `reduce` for homogeneous N→1. Keep the skeleton; strip what you don't need.

```ts
// PRIMITIVES: defineBlock, runBlock, parallel, evaluate, choice, session, agent, save, output, input
// SCENARIO: 技术选型综合工作流 —— 多 expert 并行调研 + 同构评分 + LLM 选边 + 综合
// 套路：复合工作流。候选数可变；每个候选内部跑一个可复用 block（研究 + 自评）。

import { run } from "../runtime/agent-flow-core.bundle.mjs"; // bundle mode (this folder). Source-repo mode: "../src/index.js"

await run(
  async ({ flow, save }) => {
    const question = await flow.input(
      "question",
      "我们做一个 SaaS 后台，需要全文搜索 + 模糊查询 + 简单排序，选哪个？",
    );
    const context = await flow.input(
      "context",
      "TS 单体，10 万文档级别，团队熟 PostgreSQL，预算紧",
    );
    const candidatesRaw = await flow.input(
      "candidates",
      ["Postgres FTS", "Meilisearch", "Typesense", "Elasticsearch"].join("\n"),
    );
    const candidates = candidatesRaw
      .split("\n")
      .map((s) => s.trim())
      .filter(Boolean);

    // researcher agent：一个就够，每个候选共用同一个 system，prompt 换名字
    const researcher = flow.agent({
      name: "researcher",
      system: [
        "你是技术选型分析师。对一个具体候选方案，给出：",
        "## 一句话定位",
        "## 关键能力（≤ 3 条，带数据/事实）",
        "## 部署成本（运维、依赖、学习曲线）",
        "## 适配性（给定场景下契合度 1-5 分 + 一行解释）",
        "## 风险（≤ 2 条，要具体）",
        "信息不足时明说，不凑数。",
      ].join("\n"),
    });

    // 1) defineBlock：把「研究单个候选 + 自评打分」封装成可复用 block。
    //    每个候选并行跑一个独立 block，graph 上有独立子树，方便事后回看。
    const researchAndScore = flow.defineBlock<{
      candidate: string;
      report: string;
      score: number;
    }>({
      name: "research_and_score",
      description: "研究一个候选方案 + 给适配度打分",
      body: async (args) => {
        const candidate = args.candidate;
        const report = await flow.session(
          researcher,
          `请对候选方案"${candidate}"在用户场景下做调研。`,
          { candidate, user_question: args.question, user_context: args.context },
        );
        await save(`research-${candidate}`, report);
        // block 内部自评：evaluator 给报告打分（kind:"number"）
        const score = await flow.evaluate({
          kind: "number",
          question: `候选"${candidate}"在用户场景下的整体适配度（1-10，综合能力/成本/风险）。`,
          context: { user_context: args.context, report },
          min: 1,
          max: 10,
          integer: true,
          bindingName: `score-${candidate}`,
        });
        return { candidate, report, score };
      },
    });
    // 2) parallel：N 个候选并行跑 block（异构 fan-out，args 不同 → parallel 不是 pmap）
    const allResults = await flow.parallel<{
      candidate: string;
      report: string;
      score: number;
    }>(
      candidates.map((c) => async () =>
        flow.runBlock(researchAndScore, { candidate: c, question, context }),
      ),
    );
    const ranked = [...allResults].sort((a, b) => b.score - a.score);

    // 3) choice：让 LLM 在候选里综合选边（score 只是参考，不直接取第一）
    const winner = await flow.choice<string>({
      question:
        "在用户场景下，下面哪个候选最值得选？综合评分 + 部署成本 + 团队适配，不只看分数。",
      context: {
        user_question: question,
        user_context: context,
        ranked_summary: ranked.map((r) => `${r.candidate}: score=${r.score}`).join("\n"),
        ...Object.fromEntries(ranked.map((r) => [`report_${r.candidate}`, r.report])),
      },
      branches: candidates.map((c) => ({ label: c, fn: async () => c })),
      defaultLabel: ranked[0]?.candidate ?? candidates[0],
    });
    await save("winner", winner);

    // 4) synthesizer：写最终报告
    const synthesizer = flow.agent({
      name: "synthesizer",
      system: [
        "你是给团队写选型笔记的人。拿到所有候选研究 + 分数 + winner，写 ≤ 500 字 markdown：",
        "1) **推荐**：明确 winner + 一行理由",
        "2) **对比表**：候选 / 适配度 / 关键能力 / 主要风险",
        "3) **不推荐 winner 的场景**（一行）  4) **下一步**（一句话）",
      ].join("\n"),
    });
    const final = await flow.session(synthesizer, "写最终选型笔记。", {
      user_question: question,
      user_context: context,
      winner_label: winner,
      all_candidates: ranked
        .map((r) => `${r.candidate} (score=${r.score})\n${r.report}`)
        .join("\n\n---\n\n"),
    });
    await flow.output("final", final);
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(/^\/([A-Za-z]:)/, "$1"),
  },
);
```

If the user's intent doesn't match any pattern cleanly, **say so explicitly**. Don't shoehorn. Ask if they want to compose two patterns (which is what the composite example above does).

### Primitive usage rules

When generating, choose primitives this way. Each entry is "when to use" + "when NOT to use".

- `flow.session(agent, prompt, ctx?)` — the workhorse for any single LLM call. **Don't** use `flow.call` for LLM (that's for `flow.service`).
- `flow.agent({name, system, engine?, tools?, maxTurns?, contextSchema?})` — declare an LLM persona. v0.7: every LLM call shells out to an external agent CLI (claude / openclaw / hermes / psi). `engine` overrides `FLOW_ENGINE` per agent. **Leave `tools` unset (or `[]`) for plain text-in-text-out** (the default — cheapest, fastest). **Set `tools: ["Read", "Edit", "Grep", ...]` to make this agent agentic** — the CLI then runs a multi-turn tool-use loop (read/edit files, grep, run shell) instead of single-shot text. `maxTurns` caps the agentic loop. For `engine: "psi"`, configure `FLOW_PSI_WORKSPACE`; tool availability comes from that psi-agent workspace, not from this list. **Set `contextSchema: ["x", "y"] as const`** when this agent's `flow.session` takes a 3rd-arg context — it locks the allowed context keys to that literal union so typos / missing / extra keys fail at `tsc` (see anti-pattern #3). **Don't** create a new agent inside a loop body; create once outside, call N times inside. **Don't** set `tools` when you only need text — agentic mode is slower and pricier.
- `flow.service({name, signature, body})` — local JS logic (regex, math, file). **Don't** make a service that just wraps a call to an LLM; that's `flow.session` on a `flow.agent`. **Don't** use to run an external command / Python script / CLI — that's `flow.exec`.
- `flow.exec({name, command, args?, stdin?, cwd?, env?, timeout?})` — **(v0.7)** run an arbitrary **non-LLM** external command (a Python pipeline, a build tool, an offline twin). Returns `{ stdout, raw, exitCode, durationMs }`; a non-zero `exitCode` **throws** (so it composes with `flow.retry`). **Use** for any external process that is *not* an LLM call. **Don't** use to call an LLM — that's `flow.session` (with `tools` on the agent if you need agentic behaviour). **Don't** thread an external tool's API key through `env` (see anti-patterns); set `cwd` to the tool's dir and let it read its own config.
- `flow.parallel(fns[])` — N **different** tasks concurrently. **Don't** use when all fns do the same thing on different items → use `flow.pmap`.
- `flow.pmap(items, fn)` — N **same** task on N items concurrently. **Don't** use when items must be processed in order → use `flow.forEach` or `flow.pipeline`.
- `flow.pipeline(label, steps[])` — chained steps where each step's output is next step's input. **Don't** use when steps are independent → use `flow.parallel`.
- `flow.if / ifElse(branches, else?)` — branch on a **boolean computable in code**. **Don't** use when the decision needs LLM judgement → use `flow.choice`.
- `flow.choice({question, branches, ...})` — branch on a **decision the LLM must make**. **Don't** use when there are 5+ branches; collapse to a `flow.evaluate({kind:"choice"})` plus your own switch.
- `flow.evaluate({kind, question, ...})` — get a structured judgement from LLM. `kind: "boolean" | "number" | "choice"`. **Prefer `engine: "claude"`** — it uses the CLI's native `--json-schema` for reliable strict JSON; other engines fall back to text parsing + a warn (so JSON can flake). **Don't** rely on a flaky non-claude engine here when correctness matters.
- `flow.evaluateStatic({rules})` — code-only check (regex/contains/range). **Don't** use when criteria is fuzzy ("is this professional enough?") — that needs `flow.evaluate`.
- `flow.loopUntil(condFn, fn, opts?)` — repeat until condFn returns true (do-while; runs fn first). **Don't** forget `maxIterations` — default is 8.
- `flow.loopWhile(condFn, fn, opts?)` — repeat while condFn returns true (while). Same caveat.
- `flow.repeat(n, fn)` — fixed N times. **Don't** use when N is data-dependent → use `flow.forEach` over an array.
- `flow.retry(fn, opts?)` — flaky external call or quality gate that may need redo. Default 3 attempts with exponential backoff. **Don't** wrap deterministic code; that's just slowness.
- `flow.reduce(items, fn, init)` — incremental merge. **`fn` is `(acc, item, index) => Promise<R>`** — accumulator FIRST, exactly like JS `Array.reduce` (NOT `(item, acc)`). `init` is the starting accumulator (the `acc` on the first call). **Don't** use when you can `JSON.stringify(items)` and one-shot to LLM (cheaper if context fits).
- `flow.defineBlock({name, body})` + `flow.runBlock(handle, args)` — reusable subgraph. **Don't** define inline if used once; `defineBlock`'s value is reuse + cleaner graph.
- `flow.input(name, default)` — CLI-overridable input. **Don't** hardcode values that the user might want to change.
- `flow.output(name, value)` — final user-facing artifact. **Don't** confuse with `save(name, value)`; `save` is intermediate, `output` is "this is the answer".

#### Worked example: homogeneous `pmap` + `reduce` (the "N items → one summary" pattern)

This is the one archetype with no separate reference file, so here is the full shape. Use it for: N reviews / N PRs / N docs / N log lines → one merged report. Copy the structure; swap the agents and prompts.

```ts
// 1) declare agents once, OUTSIDE the loops (never inside pmap/reduce — anti-pattern)
const analyzer = flow.agent({
  name: "analyzer",
  system: "你是分析师。给你一条记录，抽出要点。",
  contextSchema: ["item"] as const,        // locks the 3rd-arg keys at tsc time
});
const merger = flow.agent({
  name: "merger",
  system: "把新要点并进报告，输出更新后的完整报告。",
  contextSchema: ["current_report", "new_point"] as const,
});

// 2) pmap: SAME task on each item, concurrently. fn is (item, index) => Promise<R>.
//    `item` is the array element itself; the 2nd arg `index` is optional.
const points = await flow.pmap(items, async (item, index) => {
  const point = await flow.session(analyzer, `分析：\n${item}`, { item });
  await save(`point-${index + 1}`, point);   // each branch saves its own binding — no name clash
  return point;                              // pmap returns the array of these return values, in order
});
```

```ts
// 3) reduce: fold the N results into one. fn is (acc, item, index) => Promise<R>.
//    acc is FIRST (init on the first call). The fn body may be async and may call flow.session.
const report = await flow.reduce(
  points,
  async (currentReport, newPoint /*, index */) =>
    flow.session(merger, "把这条要点并进报告，返回完整报告。", {
      current_report: currentReport,   // the accumulator so far ("" on the first item)
      new_point: newPoint,             // the current item
    }),
  "",                                  // init: empty report to start from
);

await flow.output("final", report);    // the answer; `save` is for intermediates
```

**Two traps `tsc` will NOT catch** (both compile fine because everything is `string`, so get them right by hand):
1. **`reduce` arg order is `(acc, item)`, not `(item, acc)`.** Reversing them silently swaps "report so far" and "new item" — the flow runs, the report comes out wrong. Mirror JS `Array.reduce`: accumulator first.
2. **The `contextSchema` keys must match the 3rd-arg object keys exactly.** With `contextSchema` declared on the agent, a typo *does* fail `tsc`; without it, a misspelled key silently passes an empty context. Always declare `contextSchema` on any agent whose `flow.session` takes a context object (anti-pattern #3).

#### Decision: `flow.session` vs `flow.evaluate` vs `flow.service` vs `flow.exec`

The three runtime jobs are now orthogonal: **`flow.session` calls an LLM / `flow.exec` runs an external command / `flow.service` calls a local JS function.**

| User intent | Pick |
|---|---|
| Validate / classify / pick (boolean / number / one-of-N) | `flow.evaluate` (use `engine: "claude"` — native `--json-schema` is the reliable JSON path) |
| Static rule check (regex / contains / range) | `flow.evaluateStatic` |
| Polish / translate / summarize / draft (text-in-text-out, single shot) | `flow.session` on a plain `flow.agent` (no `tools`) |
| Repeat the above on N items | `flow.session` inside `flow.pmap` / `flow.forEach` |
| Call a known local JS function | `flow.service` |
| **LLM must autonomously read files, edit code, grep, run shell** | `flow.session` on `flow.agent({..., tools: ["Read","Edit","Grep",...]})` |
| **Non-LLM external command** (Python script, build tool, offline twin) | `flow.exec` |
| LLM must autonomously do web research / use MCP tools | `flow.agent({ engine: "hermes", tools: [...] })` ⏸ engine support firming up |

**Rule of thumb**: 80% should be plain session / evaluate / service. Reach for agentic mode (agent with `tools`) only when a single-shot text call can't do it. Reach for `flow.exec` only for genuine non-LLM subprocesses. Never put an agentic session or `flow.exec` inside a tight loop — each is a ~3-10s subprocess.

### Anti-patterns to refuse

The author skill must catch and refuse to emit code that does any of these:

1. **Unawaited (detached) `flow.*` calls** — a bare `.then()`, `setTimeout(() => flow.session(...))`, or any `flow.*` promise you don't `await`. Node's `AsyncLocalStorage` *does* propagate across `setTimeout`/`setImmediate`/`queueMicrotask`, so the parent node isn't lost the way older docs claimed. The real bug: `run()` only drains writes it can `await`. A detached promise can resolve **after** `execution-graph.json` is finalized → its binding lands on disk but isn't in the graph, so replay/resume never matches. It can also keep the process alive past `run()`. Always `await` every `flow.*`; for a delay use `await new Promise(r => setTimeout(r, ms))` (await the wrapper, then call `flow.*`) or `flow.repeat`.
2. **`flow.evaluate` on > 30k token input.** Large inputs make strict-JSON output less reliable (especially on non-claude engines that fall back to text parsing). Compress upstream or split into multiple smaller evals, and prefer `engine: "claude"` for the eval node.
3. **Misspelled binding name in `flow.session`'s 3rd arg.** By default the 3rd arg is `Record<string, string>`, so a typo (`summray` for `summary`) compiles fine and the agent silently gets an empty context. **The fix shipped in v0.7: declare `contextSchema: ["summary", "plan"] as const` on the downstream `flow.agent`.** That locks the 3rd-arg keys to the exact literal union — a typo, a missing field, or an extra key all fail at `tsc` (and a runtime `assertContextSchema` backs it up if `as any` bypasses tsc). **Prefer emitting `contextSchema` on any agent whose `flow.session` takes a 3rd-arg context** built from upstream bindings; it turns binding names into a compile-time contract. Minimal shape: `flow.agent({ name, system, contextSchema: ["summary", "plan"] as const })` — then `flow.session(agent, prompt, { summary, plan })` is checked against that literal union. If you don't use `contextSchema`, copy-paste binding names from the upstream `save` / `output` calls.
4. **Sharing mutable state across `flow.parallel` branches.** Each branch should be self-contained; pass inputs in, get outputs back. No closures over a shared array (race conditions, graph confusion).
5. **Agentic session or `flow.exec` inside `flow.pmap` / `flow.forEach` over many items.** Each agentic CLI call (`flow.agent` with `tools`) and each `flow.exec` cold-starts a subprocess (~3-10s, and on the claude engine roughly $0.25/call with no cross-session prompt-cache reuse); a 100-item loop = many minutes + stacked cost. If you need a plain LLM call on N items, use a `flow.session` on a tool-less agent inside `flow.pmap`. Reserve agentic mode / `flow.exec` for one-off tasks.
6. **Using an agentic session or `flow.exec` to get a boolean / single value.** Both return free-form output, not strict JSON. Use `flow.evaluate({kind: "boolean" | "number" | "choice"})` (preferably on `engine: "claude"` for the native `--json-schema` path) instead. If the user really wants agentic autonomy *and* a structured answer, parse `result.text` / `result.stdout` with a regex/heuristic and document the fragility.
7. **Relaying an external tool's API key through Fuclaw's `env`.** When generating a `flow.exec` that wraps an external tool (a Python pipeline, a CLI with its own config), do NOT thread that tool's API key through `env: { SOME_KEY: process.env.SOME_KEY }`. The key usually lives in the *external tool's own* config (read by its own dotenv at its `cwd`), not in Fuclaw's `process.env` — so the relay resolves to `""` and silently breaks the tool's auth while looking correct. It also drags the secret into Fuclaw's process memory for no reason. Pass through `env` only what the subprocess genuinely can't obtain itself (e.g. `PYTHONPATH`); for credentials, set `cwd` to the tool's dir and let it read its own config.

### Runtime mode detection (which `import` path to use)

Before writing the import line, decide which of three runtime modes the user is in. The author skill MUST pick the right one — wrong import = `tsc` fails or runtime crashes.

**Mode A — source repo mode** (the user is inside the cloned Fuclaw / OpenProse repo, generated file goes to `core/examples/flow-author-*.flow.ts`):

```ts
import { run } from "../src/index.js";
```

How to detect: the working directory contains `core/src/index.ts` and `core/examples/`, OR the user said "in the repo" / corePath is set.

**Mode B — skill bundle mode** (the user copied the `dist/fusion-flow/` folder standalone, generated file goes to `examples/flow-author-*.flow.ts` next to a `runtime/` folder):

```ts
import { run } from "../runtime/agent-flow-core.bundle.mjs";
```

How to detect: the working directory has `runtime/agent-flow-core.bundle.mjs` and a sibling `examples/` folder, OR the user said "from the skill bundle" / "from dist/fusion-flow" / "I copied the folder".

**Mode C — workspace-adhoc mode** (the flow is managed by `flow_manage` at `flows/adhoc/<slug>/flow.ts`, and the skill bundle lives at `skills/fusion-flow/` with its `runtime/` folder):

```ts
import { run } from "../../../skills/fusion-flow/runtime/agent-flow-core.bundle.mjs";
```

How to detect: `flows/adhoc/` and `skills/fusion-flow/runtime/agent-flow-core.bundle.mjs` both exist at the workspace root, and `skills/fusion-flow/tsconfig.json` already includes `../../flows/**/*.ts` in its `include` array. The typecheck command is still `cd skills/fusion-flow && npm run typecheck`. This is the preferred mode when `flow_manage` manages flows rather than placing files in `core/examples/` or `examples/` alongside the runtime.

**If unsure, ask the user once.** Cost of guessing wrong: every file fails `tsc`. Cost of asking: one short question.

### Code template

Every generated file follows this skeleton. Comments at top are mandatory:

```ts
// PRIMITIVES: <list of flow.xxx used, comma-separated>
// SCENARIO: <one-line user-facing description>
// AUTHORED: <YYYY-MM-DD HH:mm:ss> by Fuclaw authoring mode from intent: "<original user intent>"

import { run } from "<PICK_ONE_PER_RUNTIME_MODE_BELOW>";

await run(
  async ({ flow, save }) => {
    // 1. Inputs (optional, with sensible defaults)
    const someInput = await flow.input("some_input", "default value");

    // 2. Agents (declared once, reused N times below)
    const someAgent = flow.agent({
      name: "some_agent",
      system: ["你是 ...", "要求 ...", "输出 ..."].join("\n"),
    });

    // 3. Main flow (use one of the 5 patterns; see the inlined full-featured example)
    const result = await flow.session(someAgent, `... ${someInput} ...`);

    // 4. Save intermediates as needed; emit final
    await save("intermediate", result);
    await flow.output("final", result);

    console.log("\n========== Final ==========\n");
    console.log(result);
    console.log("\n===========================\n");
  },
  {
    programPath: new URL(import.meta.url).pathname.replace(
      /^\/([A-Za-z]:)/,
      "$1",
    ),
  },
);
```

The Windows-path regex in `programPath` is **always** required — it normalizes `import.meta.url` for both POSIX and Windows. Don't omit; don't simplify.

### TSC error self-repair

After generation, run `npx tsc --noEmit`. Common errors and fixes:

- `Property 'paralel' does not exist on type 'FlowAPI'` — typo. The correct name is `parallel`. Check [`core/README.md`](../../../core/README.md) primitive table.
- `Type 'X' is not assignable to type 'Y'` on a `flow.evaluate` call — most often `kind` was wrong (e.g. `"num"` instead of `"number"`).
- `'someVar' is possibly 'undefined'` after `flow.call` or `flow.ifElse` — these return `T | undefined`. Use `?? <fallback>` or pull the call inside a function that always provides else.
- `error TS2307: Cannot find module '../src/index.js'` (or `'../runtime/agent-flow-core.bundle.mjs'`) — **wrong runtime mode import path**, not a missing dependency. This is the #1 high-frequency trap (see "Runtime mode detection"). Bundle mode (`dist/fusion-flow/`, has a `runtime/` folder) must import `../runtime/agent-flow-core.bundle.mjs`; source-repo mode (has `core/src/`) must import `../src/index.js`; workspace-adhoc mode (`flows/adhoc/` + `skills/fusion-flow/`) must import `../../../skills/fusion-flow/runtime/agent-flow-core.bundle.mjs`. Check which folder you're actually in and match the path — don't add a dependency.
- `Cannot find name 'flow'` — the closure parameter is destructured: `async ({ flow, save }) => { ... }`. Check the skeleton.
- `Property 'X' does not exist on type` for context bindings — the binding-name string in `flow.session(...)`'s 3rd arg is opaque to TS. Re-read the corresponding `save`/`output` calls and align.

If the same error survives 3 rewrites, stop and ask the user. Don't loop indefinitely.

### Running it (automatic, right after typecheck)

1. Run the file (no approval step — this follows directly from step 5 of the author loop):
   - **Mode A (source repo):** `cd <corePath> && npx tsx examples/flow-author-<id>.flow.ts`
   - **Mode B (skill bundle):** `cd <bundleDir> && npx tsx examples/flow-author-<id>.flow.ts`
2. Capture `[run] <runId>` and `[run] dir: ...` from stdout (these are for *you*, not for the user).
3. After completion (or on error), fall back to the "Reading a Run" protocol — summarize the run for the user in plain business language. Lead with the result, not the file or the metrics.
4. Only if the user asks how to re-run it later: tell them the command. Don't volunteer the file path or `npx tsx` line unprompted — for a non-technical user it's noise.

### What Authoring Mode is NOT

- It is **not** a guarantee the generated flow gets good *content*. We control structure (right primitives, compiles, runs); the LLM agent prompts within still depend on user's domain.
- It is **not** auto-iterating. v0.3 is human-in-the-loop on *content* (the user reads the result and asks for changes); but it does **not** gate on a "要不要跑" approval — author generates, typechecks, and runs in one go.
- It is **not** a replacement for hand-written `.flow.ts`. Power users will still write directly. Author is for the 80% who want one-shot.

## Doctor Checks

When the user asks to check their environment ("环境齐不齐 / 能不能跑"):

```bash
node --version                 # need ≥ 20
npx tsx --version              # any
ls <corePath>/.env             # optional in v0.7 (FLOW_ENGINE config); the engine CLI's own auth is what matters
ls <corePath>/node_modules     # must exist; if not, run `npm install` in <corePath>
claude --version               # the configured FLOW_ENGINE CLI must be on PATH (default: claude)
```

Report each as ✓ / ✗. Never echo any API key value. In v0.7 `core/.env` no longer holds an LLM-direct key — auth is the engine CLI's own config (the claude engine will passthrough `ANTHROPIC_*` from the shell environment if present, else use claude's own login).

### Authoring readiness (v0.3)

Authoring no longer depends on any external reference `.flow.ts` — the full-featured example is **inlined in this SKILL.md** ("Reference Patterns" → "Full-featured in-context example"), so author works as long as the SKILL.md is loaded. The only environment prerequisite is a clean typecheck (generated code must not inherit broken types):

```bash
cd <corePath> && npx tsc --noEmit                    # everything compiles
```

If `tsc --noEmit` errors, report:

```
✗ Authoring Mode unsafe (typecheck failing)
  Reason: <first 3 lines of tsc output>
  Fix the typecheck errors before entering Authoring Mode, otherwise generated code may inherit broken types.
```

Otherwise:

```
✓ Authoring Mode ready (inlined reference present, typecheck clean)
```

### Engine readiness (v0.7)

In v0.7 every LLM call (`flow.session` / `flow.evaluate` / `flow.choice`) shells out to an external agent CLI (`FLOW_ENGINE`, default `claude`). Check the configured engine's CLI is on PATH. `flow.exec` needs no preflight (any command goes).

```bash
claude --version                                    # if FLOW_ENGINE=claude (default)
psi-agent run --help                                # if FLOW_ENGINE=psi
# Windows only: git-bash must exist for the claude CLI
test -f /c/Program\ Files/Git/bin/bash.exe || \
  test -f /d/Program\ Files/Git/bin/bash.exe       # one of the candidates
```

For the psi-agent engine, configure:

```bash
FLOW_ENGINE=psi
FLOW_PSI_WORKSPACE=/abs/path/to/psi-agent/examples/a-simple-bash-only-workspace
FLOW_PSI_PROFILE=fusion          # psi-agent reads ~/.psi-agent/config.toml
# Optional when psi-agent is not installed globally:
FLOW_PSI_COMMAND=uv
FLOW_PSI_COMMAND_ARGS=--project /abs/path/to/psi-agent run psi-agent
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
🐾 OpenFlow — Fuclaw / @agent-flow/core
用自然语言驱动多 agent 工作流，带完整执行图回放。直接跟我说就行，不用记任何命令：

  • "帮我写个工作流做 X / 帮我编排 ..."           → 用大白话描述需求，我帮你生成 .flow.ts
  • "跑一下刚生成的那个 / 帮我跑这个 .flow.ts"     → 执行你手上的 .flow.ts，跑完报告 runId
  • "接着上次那个跑 / 只重跑改动的部分"           → (v0.6) 复用上次结果，缓存的步骤跳过不重算
  • "刚才那个跑完了吗 / 看看上次结果"             → 带你看懂某次跑的执行图和产出
  • "环境齐不齐 / 能不能跑"                        → 检查 Node + tsx + .env + authoring 就绪度

我不再附带「现成 demo 例子」——你想要什么工作流，直接描述，我现写给你。
corePath: 需要先配置（见 "Running a Program" 一节）
```

## Security + Approvals

`.flow.ts` files are **TypeScript code** — they run with the privileges of the OpenClaw process. Always show the user the file you're about to execute when they reference it for the first time. For remote URLs, refuse: running a `.flow.ts` from a URL is intentionally not supported in v0.1.
