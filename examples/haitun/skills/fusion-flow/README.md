# Fusion Flow — Fuclaw skill bundle (v0.7.3)

Self-contained skill: SKILL.md + bundled runtime. This bundle ships **no demo flows** —
you describe what you want in natural language and the LLM (reading SKILL.md) authors a
`.flow.ts` for you, then you run that. You don't need to clone the Fuclaw repo.

## 3 steps

```bash
# 0. (if you cloned the source repo) copy this folder somewhere outside the repo.
#    git-bash / macOS / Linux:
cp -r dist/fusion-flow ~/my-flow-test && cd ~/my-flow-test
#    Windows PowerShell:
#      Copy-Item -Recurse dist\fusion-flow $HOME\my-flow-test; cd $HOME\my-flow-test

# 1. install (only once)
npm install

# 1b. (optional) self-check your environment — node / tsx / engine CLI / git-bash / auth
npm run doctor

# 2. have an LLM author a flow for you (see "Authoring new flows" below), then run it:
npx tsx examples/<the-file-it-generated>.flow.ts
```

## What's inside

- `SKILL.md`  - feed this to any long-context LLM client (Cherry Studio / Claude.ai /
  Cursor / Claude Code) so it can write `.flow.ts` files for you in natural language.
  The full-featured reference example is **inlined inside SKILL.md** — no separate demo
  files to read.
- `runtime/agent-flow-core.bundle.mjs`  - the @agent-flow/core runtime, single-file ESM bundle.
- `runtime/agent-flow-core.bundle.d.mts`  - bundled types for `tsc --noEmit`.
- `examples/`  - empty (placeholder only). This is where author writes the flows it
  generates for you. Each generated file imports from `../runtime/agent-flow-core.bundle.mjs`.
- `tsconfig.json` / `package.json` / `package-lock.json`  - so `npm install` pins exact
  dependency versions and `npm run typecheck` / `npx tsx ...` work out of the box.
- `doctor.mjs`  - `npm run doctor` checks node / tsx / engine CLI / git-bash / auth (read-only, no secrets printed).

## LLM engine & auth (v0.7)

v0.7 routes every LLM call through an external **agent CLI** (claude / openclaw / hermes / psi) —
there is no HTTP-direct provider and no API key lives in this bundle. Auth is whatever the
chosen CLI already uses. Everything below is **optional** and has a working default; set them
as real environment variables (export in your shell, or create a `.env` file in this folder —
the runtime loads `.env` via dotenv if present).

| Env var | Default | What it does |
| --- | --- | --- |
| `FLOW_ENGINE` | `claude` | Which CLI backs `flow.session` / `evaluate` / `choice`. Supported: `claude`, `openclaw`, `hermes`, `psi`. Override per-agent with `flow.agent({ engine })`. |
| `FLOW_MODEL` | CLI's own default | Default model (claude accepts `opus`/`sonnet` aliases). Override per-agent with `flow.agent({ model })`. |
| `FLOW_MAX_CONCURRENCY` | `4` | Max concurrent CLI subprocesses (guards against process blow-up / rate limits under `flow.parallel`). |
| `FLOW_CLI_TIMEOUT_MS` | `300000` | Per-call subprocess timeout (5 min). |
| `FLOW_RUNS_KEEP_COUNT` | `50` | (v0.7.1) runs/ ring-buffer GC: keep the newest N run dirs. `0` disables this axis. |
| `FLOW_RUNS_KEEP_DAYS` | `7` | (v0.7.1) runs/ ring-buffer GC: also keep anything modified within N days. Manual sweep: `npm run runs:gc` (in the source repo's `core/`). |

**Auth per engine:**
- **claude** — uses your local `claude` login. If `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN`
  (+ optional `ANTHROPIC_BASE_URL` pointing at a gateway / Volcano ARK) are in the environment,
  they pass through to the subprocess. Otherwise it falls back to claude's own OAuth/keychain.
  > Heads-up: `--bare` (a cost-saving flag) is only added for plain-text agents. Agents with
  > `tools` set (agentic / web search) run in full mode so server-side tools like WebSearch work.
  > **Cost heads-up:** `--bare` is only enabled when `ANTHROPIC_API_KEY`/`ANTHROPIC_AUTH_TOKEN`
  > is set. With **no token (OAuth fallback)** the run can't use `--bare`, so the claude CLI injects
  > its full system prompt + tool definitions on every call — input tokens are roughly **2× the bare
  > mode** (measured: in≈3.3k with token vs ≈7.5k without, for the same tiny prompt). For batch jobs,
  > set `ANTHROPIC_AUTH_TOKEN` to roughly halve input-token spend.
- **openclaw** / **hermes** — use that CLI's own config (provider / key it manages itself).
- **psi** — calls `psi-agent run`. Set `FLOW_PSI_WORKSPACE` to a psi-agent workspace. Keep provider URL/API key in psi-agent's own `~/.psi-agent/config.toml` profile, selected with optional `FLOW_PSI_PROFILE` / `FLOW_PSI_CONFIG`. Optional: `FLOW_PSI_AI_SOCKET` to use an existing AI backend. `FLOW_PSI_AI` / `FLOW_PSI_MODEL` / `FLOW_PSI_BASE_URL` / `FLOW_PSI_API_KEY` are temporary local overrides, not the recommended persistent configuration path.

**Windows:** claude needs git-bash. If `CLAUDE_CODE_GIT_BASH_PATH` is unset the runtime probes
common install paths; if that fails, set it manually, e.g.
`CLAUDE_CODE_GIT_BASH_PATH=C:\Program Files\Git\bin\bash.exe`.

## Authoring new flows in natural language

Open SKILL.md in any LLM client that supports long context (>= ~32k tokens). Tell it:

> Read this SKILL.md and write me a .flow.ts that <your task>.
> Save it to examples/flow-author-<timestamp>.flow.ts.
> Use `import { run } from "../runtime/agent-flow-core.bundle.mjs";` at the top.

After it generates the file, run `npm run typecheck` and `npx tsx examples/<file>`.

## Source

Full source: https://git.kclab.cloud/industry-academia-research/FuClaw-OpenProse
