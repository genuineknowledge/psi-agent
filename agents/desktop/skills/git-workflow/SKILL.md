---
name: git-workflow
description: "Safe git operations and workflow conventions: creating/switching branches, staging and writing commits, opening and updating pull requests (gh/glab), and resolving merge/rebase conflicts. Use whenever a task involves committing changes, branching, pushing, PRs, syncing with a remote, or fixing a conflicted merge/rebase. Emphasizes not committing/pushing without explicit user intent, never force-pushing shared branches, and never committing secrets."
category: coding
---

# Git Workflow

Use this skill whenever a task touches git: branching, staging, committing, pushing,
pull/merge requests, syncing with a remote, or resolving conflicts. Follow the safety
rules first — they override convenience.

Reply in Chinese unless the user clearly uses another language.

## Safety Rules (highest priority)

- **Never commit or push unless the user explicitly asks.** Making edits is fine; turning
  them into commits is a separate, user-initiated step. If intent is unclear, ask first.
- **Never commit to `main`/`master` directly.** Create or switch to a feature branch first,
  unless the user explicitly says to commit on the current main branch.
- **Never force-push a shared branch** (`main`, release branches, anything others may pull).
  `git push --force` and `--force-with-lease` require explicit user permission, and even then
  prefer `--force-with-lease` over `--force`. Force-pushing your own unshared feature branch
  after a rebase is acceptable when the user asked for the rebase.
- **Never run destructive commands without explicit permission**: `git reset --hard`,
  `git clean -fd`, `git branch -D`, `git checkout -- <file>` / `git restore` that discards
  uncommitted work, `git rebase`/`git merge --abort` mid-conflict when work would be lost.
  Explain what would be lost and confirm before running.
- **Never commit secrets.** Before staging, scan for `.env`, credentials, tokens, private
  keys, `*.pem`, config with passwords. Prefer staging specific files over `git add .`.
- **Leave git config alone.** Do not change `user.name`, `user.email`, remotes, or hooks
  unless asked. Do not add `--no-verify` (skipping hooks) unless the user asks.
- **Interactive flags are unavailable** in this environment (`-i`, `git rebase -i`,
  `git add -i`). Use non-interactive equivalents.

## Orient Before Acting

Run these first (all read-only) to understand the current state:

```bash
git status              # working tree + current branch
git branch --show-current
git log --oneline -5    # recent history
git remote -v           # remotes (origin, upstream, ...)
```

## Branching

Create work on a feature branch off an up-to-date base:

```bash
git fetch origin
git switch -c feat/<short-topic> origin/main   # branch from latest main
```

Naming: use a short kebab-case topic with a type prefix when the project uses one
(`feat/`, `fix/`, `chore/`, `docs/`). Match the repo's existing branch naming if it has one
(check `git branch -a`).

## Committing

1. Review what will be committed: `git status` then `git diff` (unstaged) and, after staging,
   `git diff --cached`.
2. Stage specific paths, not `git add .`, to avoid sweeping in unrelated or secret files.
3. Write a clear message. Follow the repo's convention — this repo uses Conventional Commits
   (`feat(scope): ...`, `fix(scope): ...`, `docs: ...`). Check `git log` to confirm the style.
   Keep the subject imperative and under ~70 chars; use the body for the why.
4. Prefer a new commit over `--amend`. Only amend your own unpushed commit when the user asks
   or to fold in pre-commit hook fixes.

```bash
git add path/to/file.py path/to/other.py
git commit -m "feat(scope): concise summary" -m "Optional body explaining the why."
```

## Pushing & Pull Requests

Push a feature branch and set upstream:

```bash
git push -u origin feat/<short-topic>
```

Open a PR with the platform CLI (detect from `git remote -v`):

- GitHub: `gh pr create --base main --head feat/<topic> --title "..." --body "..."`
- GitLab: `glab mr create --source-branch feat/<topic> --target-branch main --title "..." --description "..."`

PR guidance:
- Title concise (< ~70 chars); put detail in the body.
- Body structure: summary of changes, what was tested, and anything blocked or out of scope.
- To update an existing PR, just push more commits to the same branch — no new PR needed.

Pushing/opening a PR sends code to a remote and is outward-facing. Confirm the user wants it
published before pushing, unless they already asked.

## Syncing With the Remote

Prefer rebase to keep feature-branch history linear, but only on branches you own:

```bash
git fetch origin
git rebase origin/main        # replay your commits on top of latest main
# or, if the project prefers merges:
git merge origin/main
```

If a rebase goes wrong and no local work is at risk of being lost, `git rebase --abort`
returns to the pre-rebase state.

## Resolving Merge / Rebase Conflicts

1. Identify conflicted files: `git status` (look for "both modified") or
   `git diff --name-only --diff-filter=U`.
2. Open each file and find the conflict markers:
   - `<<<<<<< HEAD` — current branch's version (during merge) / the commit being replayed onto (during rebase).
   - `=======` — divider.
   - `>>>>>>> <ref>` — incoming version.
   During a **rebase** the sides are swapped relative to a merge (HEAD is the upstream base,
   the incoming side is your commit) — read the ref labels, don't assume.
3. Resolve by editing to the intended final content and **removing all conflict markers**.
   Do not blindly keep one side; understand both changes and combine intent. When unsure which
   side is correct, show the user both versions and ask.
4. Verify no markers remain: search for `<<<<<<<`, `=======`, `>>>>>>>` across the repo.
5. Stage resolved files (`git add <file>`), then continue:
   - Merge: `git commit` (uses the prepared merge message).
   - Rebase: `git rebase --continue`.
6. After resolving, run the build/tests before pushing — a mechanically clean merge can still
   be semantically broken.

If the conflict is large or risky and the user hasn't committed to a resolution, summarize the
conflicting hunks and confirm the intended outcome before editing.

## After Any Git Change

- Re-run `git status` to confirm the tree is in the expected state.
- Report plainly what was done (branch created, files committed, PR URL) and what was not.
