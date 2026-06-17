---
name: github-issues
description: Manage GitHub Issues — list, view, create, comment, close — via the gh CLI.
---

All operations use the `gh` CLI. Run via `bash` tool.

**List issues:**
```bash
gh issue list                          # open issues in current repo
gh issue list --state closed           # closed issues
gh issue list --label bug --limit 20   # filter by label
gh issue list --assignee @me           # assigned to you
```

**View an issue:**
```bash
gh issue view 42
gh issue view 42 --comments            # include all comments
```

**Create an issue:**
```bash
gh issue create --title "Bug: ..." --body "Description..." --label bug
gh issue create --title "..." --body "..." --assignee username
```

**Comment:**
```bash
gh issue comment 42 --body "My comment here"
```

**Close / reopen:**
```bash
gh issue close 42
gh issue close 42 --comment "Closing because..."
gh issue reopen 42
```

**Edit:**
```bash
gh issue edit 42 --title "New title"
gh issue edit 42 --add-label enhancement --remove-label bug
gh issue edit 42 --assignee username
```

**Tips:**
- Add `--repo owner/repo` to operate on a different repo.
- Use `gh issue list --json number,title,state,labels` for structured output.
- Check auth: `gh auth status`
