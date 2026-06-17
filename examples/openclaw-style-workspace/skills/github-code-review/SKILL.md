---
name: github-code-review
description: Review a GitHub PR — fetch diff, analyse, post inline and overall review comments.
---

**Fetch the PR diff:**
```bash
gh pr view 42                          # summary
gh pr diff 42                          # full diff
gh pr diff 42 --name-only              # only changed file names
gh pr view 42 --json files             # file list as JSON
```

**Review checklist:**
- [ ] Does the change match the stated purpose?
- [ ] Are there tests for new/changed behaviour?
- [ ] Any security issues (injection, unvalidated input, secrets in code)?
- [ ] Any performance concerns (N+1 queries, missing indexes, large allocations)?
- [ ] API/interface changes — are they backwards-compatible?
- [ ] Error handling — are failure cases handled?
- [ ] Naming and readability — is the code self-documenting?

**Post a review:**
```bash
# Approve
gh pr review 42 --approve --body "LGTM"

# Request changes
gh pr review 42 --request-changes --body "Please address: ..."

# Comment only
gh pr review 42 --comment --body "A few thoughts: ..."
```

**Post inline comment on a specific line:**
```bash
gh api repos/{owner}/{repo}/pulls/42/comments \
  --method POST \
  --field body="Your comment" \
  --field commit_id="$(gh pr view 42 --json headRefOid -q .headRefOid)" \
  --field path="src/file.py" \
  --field line=25
```

**Tips:**
- Read the PR description before the diff — understand intent first.
- Focus feedback on correctness and design, not style (that's what linters are for).
- Be specific: "line 42: this will panic on nil input because X" not "this looks wrong".
