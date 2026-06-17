---
name: github-repo-management
description: GitHub repo-level operations — branch protection, labels, milestones, settings — via gh CLI.
---

**Branch protection:**
```bash
# Enable protection on main (require PR reviews, status checks)
gh api repos/{owner}/{repo}/branches/main/protection \
  --method PUT \
  --field required_pull_request_reviews='{"required_approving_review_count":1}' \
  --field enforce_admins=true \
  --field required_status_checks='{"strict":true,"contexts":["ci/tests"]}'

# View current protection
gh api repos/{owner}/{repo}/branches/main/protection
```

**Labels:**
```bash
gh label list
gh label create "needs-triage" --color "#e11d48" --description "Awaiting triage"
gh label edit "bug" --color "#dc2626"
gh label delete "wontfix"
# Clone labels from another repo
gh label clone source-owner/source-repo
```

**Milestones:**
```bash
gh api repos/{owner}/{repo}/milestones --method POST \
  --field title="v1.0" --field due_on="2025-12-31T00:00:00Z"
gh api repos/{owner}/{repo}/milestones    # list
```

**Repo settings:**
```bash
gh repo edit --default-branch main
gh repo edit --enable-issues --enable-wiki=false
gh repo edit --delete-branch-on-merge
```

**Collaborators / teams:**
```bash
gh api repos/{owner}/{repo}/collaborators/{username} --method PUT --field permission=push
```

**Tips:**
- Replace `{owner}/{repo}` with actual values or omit for current repo context.
- Use `--jq` flag to filter JSON output: `gh api ... --jq '.[] | .name'`
