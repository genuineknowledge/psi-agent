---
name: taskflow-inbox-triage
description: Triage an inbox of tasks — read, categorise, prioritise, assign next action.
---

**Triage process:**

For each item in the inbox:

1. **Read** — understand what the item is asking.
2. **Decide:** Is it actionable?
   - **No** → archive or delete.
   - **Yes** → continue to step 3.
3. **Categorise:**
   - `bug` — something is broken
   - `feature` — new capability requested
   - `question` — needs an answer or investigation
   - `chore` — maintenance/housekeeping
   - `blocked` — waiting on someone/something else
4. **Prioritise:** P1 (urgent + important) / P2 (important) / P3 (nice-to-have)
5. **Assign next action:** one concrete verb phrase:
   - "investigate X and report findings"
   - "implement Y in file Z"
   - "ask user about X"
   - "wait for: PR #42 to merge"

**Output format per item:**
```
- [category/P1] <title>
  Next: <action>
```

**Bulk triage with todo tool:**
```
todo(action="add", text="[bug/P1] Login fails on mobile — Next: reproduce on iOS Safari")
todo(action="add", text="[feature/P2] Add export to CSV — Next: estimate effort")
todo(action="add", text="[blocked/P2] Deploy to staging — Next: wait for infra ticket #88")
```

**After triage:**
- Start with all P1 items.
- Batch P3 items for a separate review session.
- Anything that takes < 2 minutes: do it now.
