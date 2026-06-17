---
name: technology-research
description: Multi-framework/technology comparison research — parallel source gathering, structured comparison matrix, and actionable selection guide.
category: general
created_by: agent
created_at: 2026-06-16T16:37:26Z
updated_at: 2026-06-16T16:47:56Z
---

## Check-availability-first rule

When a user says "帮我用 fusion-flow 调研 X / 帮我用 fusion-flow 做 Y", **always follow this sequence**:

1. **FIRST**: verify fusion-flow availability — check Node/tsx, `claude --version`, `skills/fusion-flow/runtime/agent-flow-core.bundle.mjs` exists, and `npm run typecheck` passes in the fusion-flow skill dir.
2. **If available**: use fusion-flow. Build the `.flow.ts`, typecheck it, run it. The flow runtime gives parallel execution + execution graph + replay — better than manual fetch.
3. **If genuinely unavailable** (missing runtime, no `claude`, etc.): fall back silently to manual parallel `web_fetch` → local write. Do NOT ask the user to set up a complex runtime — they asked for research, not infrastructure.

**Never skip step 1.** In a real session, the agent jumped straight to manual `web_fetch` without even checking fusion-flow availability. The runtime was fully available; this wasted a flow artifact and produced lower-quality results (sequential instead of parallel LLM, no graph, no replay).