---
name: systematic-debugging
description: Reproduce → isolate → hypothesise → test → fix → verify debugging methodology.
---

Follow this process for any bug:

1. **Reproduce** — confirm the bug is reproducible with a minimal case. Do not attempt a fix until you can reproduce it reliably.
2. **Isolate** — narrow the failing scope. Remove code, add logging, binary-search the call stack.
3. **Hypothesise** — state a specific hypothesis: "The bug is caused by X because Y."
4. **Test the hypothesis** — add a targeted test or probe that would falsify or confirm it.
5. **Fix** — implement the minimal change that addresses the root cause. Do not fix symptoms.
6. **Verify** — run the test that reproduced the bug. Confirm it now passes. Check for regressions.

**Rules:**
- Never skip step 1. Debugging without reproduction is guessing.
- One hypothesis at a time. Change one thing, observe, repeat.
- If a fix feels like a workaround, it probably is — go back to step 3.
- Document the root cause in a comment or PR description.
