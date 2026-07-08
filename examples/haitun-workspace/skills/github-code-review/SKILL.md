---
name: github-code-review
description: "Review GitHub pull requests: read a PR's overview and changed files, fetch its full unified diff, list existing inline (file/line) and top-level review comments, and post your own top-level or inline comments. Use when asked to review a PR, look at what a PR changed, read its diff, see or reply to review feedback, or leave inline comments. Backed by the GitHub REST API over the github toolset (review_pull_request / get_pull_request_diff / list_pull_request_comments / add_pull_request_comment); needs a GitHub token."
category: github
---

# GitHub PR Code Review

Use this skill to review pull requests on GitHub: understand what a PR changes,
read its diff, read existing review feedback, and leave your own comments —
either on the whole PR or inline on a specific file and line.

It is backed by the `github` toolset, which talks to the GitHub REST API v3
directly (no `gh` binary required). Related: the [github-auth](../github-auth/SKILL.md)
skill sets up the token this skill uses.

Reply in Chinese unless the user clearly uses another language.

## Authentication

Every tool needs a GitHub token, resolved in this order:

1. `GH_TOKEN` environment variable
2. `GITHUB_TOKEN` environment variable
3. `gh auth token` (when the `gh` CLI is installed and logged in)

Reading a PR only needs read access; **posting a comment needs write access** to
the repo. If a tool returns `No GitHub token found`, set one of the env vars or
run `gh auth login` (see the github-auth skill). Never print, echo, or hard-code
a token.

## When to use

Trigger on requests like:

- "Review PR #123 in owner/repo" / "看一下这个 PR 改了什么"
- "Show me the diff of this pull request"
- "What review comments are on this PR?" / "有哪些 inline 评论"
- "Leave a comment on line 42 of foo.py in this PR" / "在这个 PR 上留言"

## Tools

### `review_pull_request(owner, repo, number, include_files=True, include_patch=False)`

PR overview: title, body, author, state/draft, base/head branches, head SHA,
mergeable state, changed_files / additions / deletions, and (by default) the list
of changed files with per-file stats. Set `include_patch=True` to embed each
file's diff hunk — handy for a small PR, but prefer `get_pull_request_diff` for
the full picture.

### `get_pull_request_diff(owner, repo, number, max_chars=60000)`

The complete unified diff as text (same as `git diff`). Long diffs are truncated
to protect the context window (`truncated` flag in the result); raise `max_chars`
if you need more, or review file-by-file via `review_pull_request(include_patch=True)`.

### `list_pull_request_comments(owner, repo, number, kind="all")`

Existing comments. Two streams: **review comments** (inline — carry `path`,
`line`, `diff_hunk`) and **issue comments** (top-level PR conversation). `kind`
selects `"review"`, `"issue"`, or `"all"`.

### `add_pull_request_comment(owner, repo, number, body, path="", line=0, side="RIGHT", commit_id="")`

Post a comment. **Write operation.**

- No `path`/`line` → a top-level PR conversation comment.
- With `path` + `line` → an inline review comment anchored to that file/line of
  the diff. `line` refers to the line in the diff; `side` is `RIGHT` (new version,
  default) or `LEFT` (old version). `commit_id` defaults to the PR head SHA.

## Typical flow

1. `review_pull_request(owner, repo, number)` — get the shape of the change.
2. `get_pull_request_diff(...)` — read exactly what changed.
3. `list_pull_request_comments(...)` — see what reviewers already said.
4. `add_pull_request_comment(...)` — leave top-level or inline feedback.

## Pitfalls

- **Inline `line` is a diff position, not necessarily the source line.** If GitHub
  rejects an inline comment (422), the line isn't part of the diff — comment on a
  changed line, or fall back to a top-level comment.
- **403 / "Resource not accessible"** — the token lacks write access (or the repo
  is private and the token lacks `repo`/`pull_requests` scope). Fix auth via
  github-auth; reading may still work while writing fails.
- **Large PRs are truncated.** `files_truncated` / `truncated` flags signal there
  was more; narrow with `get_pull_request_diff(max_chars=...)` or review per file.

## Fallback: gh CLI

If the tools are unavailable, the `gh` CLI does the same over `bash`:

```bash
gh pr view <number> --repo <owner>/<repo>              # overview
gh pr diff <number> --repo <owner>/<repo>              # unified diff
gh api repos/<owner>/<repo>/pulls/<number>/comments    # inline review comments
gh pr comment <number> --repo <owner>/<repo> --body "…"  # top-level comment
```
