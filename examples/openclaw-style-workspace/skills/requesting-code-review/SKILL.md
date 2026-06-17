---
name: requesting-code-review
description: Prepare a diff summary, reviewer checklist, and review request for a code change.
---

Before requesting a code review:

1. **Summarise the change** — one paragraph: what changed, why, and what was not changed.
2. **List changed files** — run `bash` with `git diff --stat HEAD~1` or `git status`.
3. **Write a checklist for reviewers:**
   - What to focus on (logic, security, performance, API design)
   - What to ignore (auto-generated files, formatting-only changes)
   - Known trade-offs or TODOs left intentionally
4. **Check your own work first:**
   - Does the diff match the intent?
   - Are there leftover debug statements or TODOs?
   - Do tests cover the changed paths?
5. **Draft the review request message:**
   ```
   ## What changed
   <summary>

   ## Why
   <motivation>

   ## Testing
   <how it was tested>

   ## Focus areas
   <what reviewers should look at closely>
   ```

Use `bash` tool to inspect the diff and `write` tool to draft the PR description.
