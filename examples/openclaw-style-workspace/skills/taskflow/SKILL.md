---
name: taskflow
description: Manage structured task workflows with status tracking.
---

Use the `todo` tool and `update_plan` tool to manage a structured task flow.

**Task states:** `pending` → `in_progress` → `done` | `blocked`

**Workflow:**

1. **Initialise** — list all tasks:
   ```
   todo(action="add", text="[pending] Design database schema")
   todo(action="add", text="[pending] Implement API endpoints")
   todo(action="add", text="[pending] Write tests")
   ```

2. **Start a task** — mark it in progress:
   ```
   todo(action="complete", index=1)
   todo(action="add", text="[in_progress] Design database schema")
   ```

3. **Complete a task:**
   ```
   # Remove in_progress, no further action needed (or add [done] for history)
   todo(action="complete", index=N)
   ```

4. **Block a task:**
   ```
   todo(action="add", text="[blocked] Implement API — waiting for schema approval")
   ```

5. **Review status:**
   ```
   todo(action="list")
   ```

**Structured plan format** (use `update_plan` for complex workflows):
```markdown
## Active Tasks
- [ ] Task A — owner, due
- [x] Task B — completed 2025-06-10

## Blocked
- [ ] Task C — blocked by: Task A

## Done
- [x] Task D
```

**Tips:**
- Keep the plan.md as the source of truth; use todo.md for quick tracking.
- Review status at the start of each session.
