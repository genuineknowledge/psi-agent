---
name: gh-issues
description: GitHub Issues via the gh CLI — list, view, create, comment, close.
---

Quick reference for working with GitHub Issues using `gh`.

```bash
# List
gh issue list
gh issue list --state all --limit 50
gh issue list --label "bug" --assignee "@me"

# View
gh issue view 42
gh issue view 42 --comments

# Create
gh issue create --title "Title here" --body "Body here"
gh issue create --title "..." --label bug,enhancement

# Comment
gh issue comment 42 --body "Comment text"

# Close
gh issue close 42
gh issue close 42 --comment "Resolved in #PR"

# Reopen
gh issue reopen 42

# Edit
gh issue edit 42 --title "Updated title"
gh issue edit 42 --add-label priority --remove-label triage

# Search
gh issue list --search "error in title"
gh issue list --search "is:open label:bug"

# JSON output (for scripting)
gh issue list --json number,title,state,labels,assignees
```

**Tips:**
- `--repo owner/repo` works on any repo, not just the current one.
- Pipe to `jq` for filtering: `gh issue list --json number,title | jq '.[].title'`
