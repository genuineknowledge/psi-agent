---
name: coding-agent
description: Autonomous coding loop — read context, plan, edit, test, repeat.
---

**The loop:**

1. **Read** — understand the codebase before changing it.
   - Read entry points, relevant modules, existing tests.
   - Use `read`, `grep`, `find` to explore.

2. **Plan** — state the change clearly before making it.
   - What file(s) will change?
   - What is the exact modification?
   - What could break?

3. **Edit** — make the minimal change.
   - Prefer `edit` for targeted changes, `write` for new files.
   - One logical change per step.

4. **Test** — verify the change works.
   - Run the test suite: `bash("pytest")` or `bash("npm test")`.
   - If no tests exist, manually verify with a targeted check.
   - Fix failures before moving on.

5. **Repeat** — continue until the goal is reached.

**Rules:**
- Read before editing. Never edit a file you haven't read.
- Run tests after every non-trivial change.
- If tests fail, fix them before continuing — don't accumulate failures.
- When stuck, use `grep` and `find` to gather more context before guessing.
- Commit incrementally with clear messages when work is checkpointable.

**Done when:**
- All tests pass.
- The acceptance criterion stated in the task is met.
- No TODO/fixme left from this task.
