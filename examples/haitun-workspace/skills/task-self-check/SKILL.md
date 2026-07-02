---
name: task-self-check
description: Before sending a task-completing reply, silently verify tool calls, tool results, and final output. LOAD on every turn that will end with a user-facing answer — not only when the user asks for a review.
category: agent
---

# Task self-check (C2)

## Decision rule

Before you send a **final** user-facing message that completes the current request (`finish_reason: stop`), run a **silent** mental pass. Do **not** describe this pass in the reply — no "让我自检一下", no checklist bullets, no separate self-review section.

If the check finds a fixable problem, **fix it first** (another tool round if needed), then send the answer. If blocked, say so plainly in the final reply only.

## What to verify (3 passes)

### 1. Tool calls

- Did you call the right tools for what the user asked?
- Missing steps? (e.g. user required subagent but you inlined; user asked for live data but you guessed)
- Wrong args, wrong paths, or tools called when unnecessary?
- Parallel work: did each branch get its own call when isolation was required?

### 2. Tool results

- Any `ok: false`, empty, timeout, or error strings?
- Results contradict each other or the user's facts?
- Weak results: retry with a varied query/path before concluding?
- Subagent / long chains: did you stop background sessions you will not reuse (`subagent_stop`)?

### 3. Final output

- Does the answer **directly** satisfy the user's request (format, count, language)?
- Claims backed by tool output or explicit reasoning — not invented file contents or API results?
- Apply **structured-output-tables** (C1) when 3+ parallel items share the same shape?
- Tone and length appropriate; no half-finished plan when work could continue?

## When to skip

- Mid-task progress updates (you are not done yet).
- Pure `HEARTBEAT_OK` / `NO_REPLY` paths.
- User explicitly asked for raw tool dumps only.

## Anti-patterns

| Wrong | Right |
|-------|-------|
| Publish a "自检报告" section | Keep the check internal; only ship the corrected answer |
| Skip check after many tool rounds | More tools → more important to verify before stop |
| Ignore failed tool results in the final answer | Acknowledge failure or retry; do not pretend success |
