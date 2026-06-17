---
name: subagent-driven-development
description: Decompose a large task into independent parallel sub-tasks suitable for delegation to sub-agents.
---

Use this skill when a task is too large for a single session or has clearly independent parts.

**Process:**

1. **Map the work** — list all sub-tasks needed to complete the goal.
2. **Identify independence** — mark which sub-tasks can run in parallel (no shared state, no ordering dependency).
3. **Define interfaces** — for each sub-task, specify: input, output, and acceptance criteria.
4. **Assign** — describe each sub-task as a self-contained instruction that a fresh agent session could execute without additional context.
5. **Integrate** — define how outputs from parallel sub-tasks will be merged or verified.

**Sub-task instruction template:**
```
Goal: <one-sentence objective>
Context: <minimum context needed>
Input: <files, data, or state to start from>
Output: <what to produce>
Done when: <verifiable completion criterion>
```

**Notes:**
- Sub-tasks that share a file or database cannot safely run in parallel.
- Keep sub-tasks small enough to complete in one session.
- Always define the integration step before starting parallel work.
