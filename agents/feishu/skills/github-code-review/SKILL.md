---
name: github-code-review
description: "Review GitHub pull requests with the gh CLI: read a PR's overview and changed files, view its full unified diff, list existing inline (file/line) and top-level review comments, and post your own top-level or inline comments. Use when asked to review a PR, look at what a PR changed, read its diff, see or reply to review feedback, or leave inline comments. Runs gh / gh api through the bash tool; needs gh installed and authenticated (see github-auth)."
category: github
---

# GitHub PR Code Review

Use this skill to review pull requests on GitHub with the [`gh` CLI](https://cli.github.com/):
understand what a PR changes, read its diff, read existing review feedback, and leave your
own comments — either on the whole PR or inline on a specific file and line.

Everything here runs through the `bash` tool. There is no dedicated tool; `gh` (and `gh api`
for the REST endpoints `gh` doesn't wrap) does the work. On Windows the bundled msys64
provides `bash`, but `gh` must be installed separately.

Reply in Chinese unless the user clearly uses another language.

## Prerequisites

- **`gh` installed and authenticated.** Check with `gh auth status`. If it's not logged in
  or not installed, use the [github-auth](../github-auth/SKILL.md) skill first.
- Reading a PR only needs read access; **posting a comment needs write access** to the repo.
- Never print, echo, or log a token. `gh` manages its own credential — you never handle the
  token value directly here.

## Orient first (read-only)

```bash
gh auth status 2>&1 || echo "gh not logged in / not installed"
```

Most commands take `--repo <owner>/<repo>`; inside a cloned repo with a GitHub remote you can
omit it and `gh` infers the repo from the remote.

## Read a PR

```bash
# Overview: title, state, author, base/head branches, labels, body
gh pr view <number> --repo <owner>/<repo>

# As JSON for precise fields (mergeable, files, additions/deletions, headRefOid, ...)
gh pr view <number> --repo <owner>/<repo> \
  --json number,title,state,isDraft,author,baseRefName,headRefName,headRefOid,mergeable,additions,deletions,changedFiles,files,url

# List changed files with per-file additions/deletions
gh pr view <number> --repo <owner>/<repo> --json files --jq '.files[] | "\(.path)  +\(.additions) -\(.deletions)"'
```

## Read the diff

```bash
# Full unified diff (same as git diff) — the core of a review
gh pr diff <number> --repo <owner>/<repo>

# Just the file names that changed
gh pr diff <number> --repo <owner>/<repo> --name-only
```

For a large PR, review file-by-file: get the file list from `--json files`, then read each
file's diff hunk. Long diffs can blow up the context window — narrow when you can.

## Read existing comments

Two comment streams live on a PR: **review comments** (inline, anchored to a file and line)
and **issue comments** (the top-level PR conversation).

```bash
# Inline review comments (path, line, diff_hunk, body, author)
gh api repos/<owner>/<repo>/pulls/<number>/comments --paginate \
  --jq '.[] | {user: .user.login, path, line, body}'

# Top-level PR conversation comments
gh api repos/<owner>/<repo>/issues/<number>/comments --paginate \
  --jq '.[] | {user: .user.login, body}'

# Submitted reviews (APPROVE / REQUEST_CHANGES / COMMENT) with their summary body
gh api repos/<owner>/<repo>/pulls/<number>/reviews --paginate \
  --jq '.[] | {user: .user.login, state, body}'
```

## Post a comment

**Write operations — need write access.**

```bash
# Top-level PR conversation comment
gh pr comment <number> --repo <owner>/<repo> --body "LGTM, 已 review"
```

Inline comment (anchored to a file/line of the diff). `gh` has no first-class flag for this,
so use `gh api`. `line` is the line in the file's new version; `side` is RIGHT (new) or LEFT
(old); `commit_id` is the PR head SHA:

```bash
HEAD_SHA=$(gh pr view <number> --repo <owner>/<repo> --json headRefOid --jq .headRefOid)
gh api --method POST repos/<owner>/<repo>/pulls/<number>/comments \
  -f body="这里建议加个空值判断" \
  -f commit_id="$HEAD_SHA" \
  -f path="src/foo.py" \
  -F line=42 \
  -f side="RIGHT"
```

Submit a whole review (a summary verdict plus optional inline comments in one shot):

```bash
gh api --method POST repos/<owner>/<repo>/pulls/<number>/reviews \
  -f body="整体没问题，有几处小建议" \
  -f event="COMMENT"      # or APPROVE / REQUEST_CHANGES
```

## Typical flow

1. `gh pr view <n> --json ...` — get the shape of the change.
2. `gh pr diff <n>` — read exactly what changed.
3. `gh api .../comments` — see what reviewers already said.
4. `gh pr comment` / `gh api .../pulls/<n>/comments` — leave top-level or inline feedback.

## Pitfalls

- **Inline `line` is a position in the diff, not any source line.** GitHub returns 422 if the
  line isn't part of the PR's diff — comment on a changed line, or fall back to a top-level
  comment.
- **403 / "Resource not accessible"** — the token lacks write access (or the repo is private
  and the login lacks scope). Reading may still work while writing fails; fix via github-auth.
- **Not logged in** — `gh` prints an auth error. Run `gh auth status` and, if needed, the
  github-auth skill; don't hard-code a token.
- **Large PRs** — `gh pr diff` can be huge. Use `--name-only` first and review per file.
