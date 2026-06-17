---
name: github-pr-workflow
description: Full PR lifecycle — branch, commit, push, create PR, request review.
---

**Standard PR workflow:**

1. **Create branch:**
   ```bash
   git checkout -b feat/my-feature
   ```

2. **Make changes** — use `read`, `edit`, `write`, `bash` tools.

3. **Stage and commit:**
   ```bash
   git add -p                          # review changes before staging
   git commit -m "feat: short description"
   ```

4. **Push:**
   ```bash
   git push -u origin feat/my-feature
   ```

5. **Create PR:**
   ```bash
   gh pr create --title "feat: short description" \
     --body "## What\n...\n\n## Why\n...\n\n## Testing\n..." \
     --reviewer username
   ```

6. **Check PR status:**
   ```bash
   gh pr status
   gh pr checks
   ```

7. **Merge after approval:**
   ```bash
   gh pr merge --squash --delete-branch
   ```

**Commit message format:**
- `feat:` new feature
- `fix:` bug fix
- `refactor:` code change without feature/fix
- `docs:` documentation only
- `test:` adding tests
- `chore:` build/tooling changes

**Tips:**
- Never push directly to `main`. Always use a branch.
- Keep commits small and focused on one change.
- Use `gh pr view --web` to open the PR in browser.
