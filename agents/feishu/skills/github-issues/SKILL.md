---
name: github-issues
description: "Manage GitHub issues with the gh CLI: create issues (title/body/labels/assignees/milestone), search and triage the backlog, add or remove labels, assign or unassign people, comment, and open/close/reopen. Also covers the REST endpoints gh doesn't wrap (via gh api) for bulk label/assignee edits and issue transfer. Use when asked to file, triage, label, assign, comment on, or close GitHub issues. Runs gh / gh api through the bash tool; needs gh installed and authenticated (see github-auth)."
category: github
---

# GitHub Issues

Use this skill to manage issues on GitHub with the [`gh` CLI](https://cli.github.com/):
**create** new issues, **triage** the backlog (search, filter, read), apply and remove
**labels**, **assign** and unassign people, comment, and open/close/reopen.

Everything here runs through the `bash` tool. There is no dedicated Python tool; `gh`
(and `gh api` for the REST endpoints `gh` doesn't wrap) does the work. On Windows the
bundled msys64 provides `bash`, but `gh` must be installed separately.

Reply in Chinese unless the user clearly uses another language.

## Prerequisites

- **`gh` installed and authenticated.** Check with `gh auth status`. If it's not logged
  in or not installed, use the [github-auth](../github-auth/SKILL.md) skill first.
- Reading/searching issues only needs read access; **creating, labeling, assigning,
  commenting, or closing needs write (triage) access** to the repo.
- Never print, echo, or log a token. `gh` manages its own credential — you never handle
  the token value directly here.

## Orient first (read-only)

```bash
gh auth status 2>&1 || echo "gh not logged in / not installed"
```

Most commands take `--repo <owner>/<repo>`; inside a cloned repo with a GitHub remote you
can omit it and `gh` infers the repo from the remote. The examples below spell out
`--repo <owner>/<repo>` so they work from anywhere.

## Create an issue

```bash
# Minimal
gh issue create --repo <owner>/<repo> --title "Crash on empty upload" \
  --body "Steps to reproduce: ..."

# With triage metadata up front (labels/assignees/milestone must already exist)
gh issue create --repo <owner>/<repo> \
  --title "Add dark mode" \
  --body-file ./issue-body.md \
  --label bug --label "priority: high" \
  --assignee @me --assignee alice \
  --milestone "v1.2"
```

Notes:
- `--body-file -` reads the body from stdin; `--body-file <path>` from a file. Prefer a
  file for long, multi-line bodies rather than cramming newlines into `--body`.
- `--label` / `--assignee` / `--milestone` **must reference things that already exist** or
  `gh` errors. Create labels/milestones first (see below) or file the issue plain and add
  metadata afterward.
- **Confirm intent before filing** — creating an issue is a public, notifying action on the
  repo. Don't open issues speculatively; make sure the user asked for it.
- `gh issue create` prints the new issue URL — report it back.

## Triage: search, filter, read

```bash
# List open issues (default), newest first
gh issue list --repo <owner>/<repo>

# Filter by state / label / assignee / author, and shape the output
gh issue list --repo <owner>/<repo> --state open \
  --label bug --assignee alice --limit 50 \
  --json number,title,labels,assignees,updatedAt \
  --jq '.[] | "#\(.number) \(.title)  [\(.labels|map(.name)|join(","))]"'

# Untriaged backlog: open issues with no label and no assignee
gh issue list --repo <owner>/<repo> --state open \
  --json number,title,labels,assignees \
  --jq '.[] | select((.labels|length)==0 and (.assignees|length)==0) | "#\(.number) \(.title)"'

# Full-text / qualifier search across issues
gh issue list --repo <owner>/<repo> --search "is:open no:label sort:created-asc"
gh search issues --repo <owner>/<repo> "crash in:title" --state open

# Read one issue (body, labels, assignees, comments)
gh issue view <number> --repo <owner>/<repo>
gh issue view <number> --repo <owner>/<repo> --comments
gh issue view <number> --repo <owner>/<repo> \
  --json number,title,state,labels,assignees,milestone,body,comments
```

For triage at scale, pull structured JSON with `--json` and slice it with `--jq` rather
than eyeballing the pretty output. Use `--limit` to bound large backlogs.

## Labels

```bash
# See what labels exist before applying them
gh label list --repo <owner>/<repo>

# Create a label if the one you need is missing
gh label create "priority: high" --repo <owner>/<repo> --color D93F0B --description "Needs attention soon"

# Add / remove labels on an issue (repeat --add-label / --remove-label)
gh issue edit <number> --repo <owner>/<repo> --add-label bug --add-label "priority: high"
gh issue edit <number> --repo <owner>/<repo> --remove-label "needs triage"
```

`gh issue edit --add-label` fails if the label doesn't exist yet — create it first (or via
the REST endpoint below). To **replace** the entire label set in one call, use the REST
endpoint (this overwrites, it does not merge):

```bash
gh api --method PUT repos/<owner>/<repo>/issues/<number>/labels \
  -f "labels[]=bug" -f "labels[]=priority: high"
```

## Assign / unassign

```bash
# Assign one or more people (use @me for yourself)
gh issue edit <number> --repo <owner>/<repo> --add-assignee @me --add-assignee alice

# Unassign
gh issue edit <number> --repo <owner>/<repo> --remove-assignee bob
```

Assignees must be users with access to the repo, or `gh` silently drops them — verify with
`gh issue view <number> --json assignees` afterward. GitHub caps an issue at **10
assignees**.

## Comment, close, reopen

```bash
# Add a comment (triage note, request for info, resolution summary)
gh issue comment <number> --repo <owner>/<repo> --body "Can't reproduce on main — can you share the input file?"

# Close, optionally with a reason (completed / not planned)
gh issue close <number> --repo <owner>/<repo> --reason "not planned" --comment "Duplicate of #123"
gh issue close <number> --repo <owner>/<repo> --reason completed

# Reopen
gh issue reopen <number> --repo <owner>/<repo>
```

**Closing is a visible, notifying action.** Confirm the user wants an issue closed,
especially in bulk, and prefer a short `--comment` explaining why so the trail is clear.

## Milestones and other edits

```bash
# Set / clear a milestone
gh issue edit <number> --repo <owner>/<repo> --milestone "v1.2"
gh issue edit <number> --repo <owner>/<repo> --remove-milestone

# Edit title / body
gh issue edit <number> --repo <owner>/<repo> --title "New title" --body-file ./new-body.md
```

## REST via `gh api` (what the CLI doesn't wrap)

`gh api` speaks the [Issues REST API](https://docs.github.com/en/rest/issues) directly,
handles auth and pagination, and returns JSON — use it for bulk triage and endpoints `gh
issue` lacks. It is the async-friendly, scriptable layer.

```bash
# List with server-side filters + pagination, then reshape with --jq
gh api --paginate "repos/<owner>/<repo>/issues?state=open&labels=bug&per_page=100" \
  --jq '.[] | select(.pull_request|not) | {number, title, assignees: [.assignees[].login]}'

# Add labels WITHOUT removing existing ones (POST appends; PUT replaces)
gh api --method POST repos/<owner>/<repo>/issues/<number>/labels \
  -f "labels[]=needs triage"

# Remove a single label
gh api --method DELETE "repos/<owner>/<repo>/issues/<number>/labels/needs%20triage"

# Add / set assignees via REST
gh api --method POST repos/<owner>/<repo>/issues/<number>/assignees \
  -f "assignees[]=alice" -f "assignees[]=bob"

# Transfer an issue to another repo in the same owner (destructive-ish — confirm first)
gh issue transfer <number> <owner>/<other-repo> --repo <owner>/<repo>
```

**`/issues` returns PRs too** (a PR is an issue under the hood). Filter them out with
`select(.pull_request|not)` when you only want real issues.

## Typical flows

- **File a bug:** confirm intent → `gh issue create --title --body-file --label` → report URL.
- **Triage sweep:** `gh issue list --state open --json ... --jq 'select no label/assignee'`
  → for each, `gh issue edit --add-label ... --add-assignee ...` → comment if info is
  missing.
- **Bulk relabel:** `gh api --paginate .../issues?labels=old-label` → loop, for each
  `gh issue edit --add-label new --remove-label old`.

## Pitfalls

- **Label / assignee / milestone must already exist.** `--add-label`, `--assignee`, and
  `--milestone` error (or silently drop) on unknown values. `gh label list` first; create
  missing labels/milestones before referencing them.
- **PUT labels replaces, POST appends.** Use POST (`gh api --method POST .../labels`) to add
  without wiping the existing set; `gh issue edit --add-label` also appends.
- **`gh issue list` hides PRs, `gh api /issues` doesn't.** With raw REST, drop PRs via
  `select(.pull_request|not)`.
- **403 / "Resource not accessible"** — the login lacks triage/write access (or the repo is
  private and the token lacks scope). Reading may work while writing fails; fix via
  [github-auth](../github-auth/SKILL.md). Don't hard-code a token.
- **Not logged in** — `gh` prints an auth error. Run `gh auth status` and, if needed, the
  github-auth skill.
- **Default `--limit` is small (30).** Raise `--limit` or use `gh api --paginate` for large
  backlogs, or you'll silently triage only the first page.
- **Interactive prompts don't work in the agent shell.** Always pass `--title`, `--body` /
  `--body-file`, `--repo`, etc. explicitly rather than relying on `gh`'s interactive mode.

## After any operation

- On **success**: report the issue **number and URL** (`gh issue view <n> --json url -q
  .url`), and what changed (labels added, assignee set, closed with reason).
- On **failure**: don't just say "失败了" — map the error to the cause (gh missing → install;
  `gh auth status` not logged in → github-auth; 403 → lacks write access; unknown label →
  create it first) and quote the actual error line. Never ask for tokens in chat or run
  interactive `gh auth login` in the agent shell.
