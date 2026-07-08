---
name: github-repo-management
description: "GitHub repository lifecycle on github.com: clone/create/fork repos, inspect and manage remotes (origin/upstream), and create or list releases. Use when the task is about getting a repo onto disk, creating or forking a GitHub repo, wiring remotes after a fork, or publishing a release — not for day-to-day commits, branches, or PRs (use git-workflow for those)."
category: coding
---

# GitHub Repository Management

Use this skill when the user wants to **work with GitHub repositories as remote
resources**: clone one down, create a new repo, fork an existing repo, fix remotes
after a fork, or manage **releases**. For commits, branches, pushes, and pull
requests on a repo that is already checked out, use `skills/git-workflow/SKILL.md`
instead.

Reply in Chinese unless the user clearly uses another language.

## Prerequisites (dependency stack)

This skill uses the workspace **`bash` tool**, which shells out to programs on the
**user's machine**. Nothing here is bundled inside the workspace Python tools except
the `bash` wrapper itself.

| Layer | What it is | Who provides it |
|-------|------------|-----------------|
| 1 | Workspace tools (`bash`, `read`, …) | Always loaded with this workspace |
| 2 | `bash.exe` / shell | User OS — Git Bash, MSYS2 (Haitun installer), WSL, macOS/Linux |
| 3 | `git` | User OS — Git for Windows, MSYS2, or system package |
| 4 | `gh` (GitHub CLI) | **User must install separately** — not in Haitun installer |
| 5 | GitHub login / SSH key | **User must configure locally** — agent cannot run interactive login |

Before the first GitHub operation in a session (or after any environment error), run
**Environment preflight** (below). Prefer `gh` over raw REST/curl — it handles auth
and pagination.

**Scope** — read-only public inspection may proceed when only layer 2–3 work and
`gh` is missing (limited `git ls-remote` / public HTTPS). Private repos, create,
fork, release, and most `gh` commands need layers 4–5. **Creating, forking,
deleting, or changing visibility** always requires clear user intent.

## Safety Rules (highest priority)

- **Never create, fork, delete, transfer, or change repo visibility** unless the
  user explicitly asked for that action.
- **Never delete a repo or release** without explicit confirmation — deletions are
  irreversible on GitHub.
- **Never publish a release** (tag + GitHub Release assets) unless the user asked to
  ship/release; creating a tag pushes a permanent ref.
- **Never commit secrets** into a new repo (.env, keys, tokens). Scan before the
  first push.
- **Do not change git user config** (`user.name`, `user.email`) unless asked.
- **Interactive flags are unavailable** (`-i`, `gh auth login` inside the agent
  shell). If auth is missing, stop and instruct the user — do not embed tokens in
  commands.
- **On any failure, diagnose before retrying.** Map the error to the missing layer
  (bash / git / gh / auth / SSH / permissions). Tell the user **exactly what is
  missing** and paste the **Install & configure commands** for their OS (below).
  Do not blindly rerun the same command more than once without fixing the environment.

## Environment preflight

Run this **once** before the first GitHub task, or immediately after any
`command not found`, auth, or SSH error. Use the workspace `bash` tool:

```bash
echo "=== bash tool / shell ==="
command -v bash >/dev/null 2>&1 && bash --version | head -1 || echo "MISSING: bash"

echo "=== git ==="
command -v git >/dev/null 2>&1 && git --version || echo "MISSING: git"

echo "=== GitHub CLI ==="
command -v gh >/dev/null 2>&1 && gh --version || echo "MISSING: gh"

echo "=== GitHub auth ==="
if command -v gh >/dev/null 2>&1; then
  gh auth status 2>&1 || echo "MISSING or INVALID: gh auth (not logged in)"
else
  echo "SKIP: gh auth (gh not installed)"
fi

echo "=== SSH to GitHub (optional) ==="
ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -T git@github.com 2>&1 | head -3 || true
```

Interpret `MISSING:` lines and failed `gh auth status` as blockers. If preflight
shows gaps, **stop the GitHub task** and send the user a **Missing environment**
message (template below) before continuing.

## Failure diagnosis

Match tool output (from `bash` or from `gh`/`git`) to the cause and fix. When
several apply, list **all** missing items in one reply.

| Symptom in output | Missing | Agent can fix? |
|-------------------|---------|----------------|
| `[Error] bash executable was not found on PATH` (from `bash` tool, no shell ran) | Layer 2: **bash** | No — user installs shell |
| `bash: …: command not found` / `MISSING: bash` | Layer 2: **bash** | No |
| `git: command not found` / `MISSING: git` | Layer 3: **git** | No |
| `gh: command not found` / `MISSING: gh` | Layer 4: **GitHub CLI** | No |
| `gh auth status` → not logged in / `MISSING or INVALID: gh auth` | Layer 5: **GitHub login** | No — user runs `gh auth login` locally |
| `HTTP 401` / `Bad credentials` / `authentication failed` | Layer 5: login or expired token | No — re-login |
| `HTTP 403` / `Resource not accessible by integration` | Wrong account or insufficient repo/org permission | Partial — user switches account or grants access |
| `Permission denied (publickey)` / `Could not read from remote` (SSH URL) | Layer 5: **SSH key** not on GitHub or wrong key | No — user adds SSH key or uses HTTPS |
| `Hi USER! You've successfully authenticated` (SSH) or `gh auth status` OK | SSH / HTTPS auth OK | — |
| `repository not found` (private repo, user should have access) | Often auth (wrong user) or typo in `OWNER/REPO` | Check slug + `gh auth status` |
| `GraphQL: Could not resolve to a Repository` | Bad repo name or no access | Verify name and login |
| `release not found` | Tag/release does not exist yet | Create tag first or fix version string |
| Non-zero exit with network errors | Offline / proxy / firewall | User fixes network; do not embed proxy secrets in chat |

**Agent must not:** paste PATs into commands, run `gh auth login` inside the agent
shell, or ask the user to send tokens in chat. **Agent must:** quote the relevant
error lines, name the missing layer(s), and give the user copy-paste setup commands.

## Install & configure commands (user runs locally)

Pick the block that matches the user's OS. These run in the **user's own terminal**
(PowerShell, cmd, Terminal.app, etc.) — **not** via the agent's non-interactive shell,
except for read-only checks like `gh --version` after the user says they installed.

### Windows — Haitun Agent 安装包用户

安装包自带 MSYS2（`{安装目录}\msys64`），启动器会把 `msys64\usr\bin` 和
`msys64\ucrt64\bin` 加到 PATH，因此 **bash** 和 **git** 通常已有。**gh 不在安装包内**，
需用户自行安装。

在用户本机 PowerShell 或「Git Bash / MSYS2 UCRT64」终端中：

```powershell
# 安装 GitHub CLI（任选其一）
winget install --id GitHub.cli -e
# 或: choco install gh

# 安装后关闭并重新打开终端，然后登录（交互式，必须在用户终端执行）
gh auth login

# 验证
gh auth status
git --version
gh --version
```

若 **bash 仍缺失**（非安装包、或未通过 Haitun 启动）：安装 Git for Windows：

```powershell
winget install --id Git.Git -e
```

### Windows — 开发机（未用安装包 / `uv run psi-agent`）

```powershell
winget install --id Git.Git -e
winget install --id GitHub.cli -e
# 新开终端
gh auth login
gh auth status
```

### macOS

```bash
# 若未装 git（Xcode CLT 通常已有）
xcode-select --install   # 若提示已安装可跳过

brew install gh
gh auth login
gh auth status
```

### Linux (Debian/Ubuntu 示例)

```bash
sudo apt update
sudo apt install -y git gh
# 若 apt 无 gh: 见 https://github.com/cli/cli/blob/trunk/docs/install_linux.md

gh auth login
gh auth status
```

### SSH 远程（可选，仅当用户使用 `git@github.com:` URL 且出现 publickey 错误）

在用户本机终端：

```bash
# 生成密钥（若无 ~/.ssh/id_ed25519）
ssh-keygen -t ed25519 -C "your_email@example.com"

# 启动 agent 并添加密钥（macOS/Linux）
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519

# 复制公钥，添加到 GitHub → Settings → SSH and GPG keys → New SSH key
cat ~/.ssh/id_ed25519.pub

# 验证
ssh -T git@github.com
```

Windows (Git Bash)：`eval $(ssh-agent -s)` 与 `ssh-add` 同上。

若用户不愿配置 SSH，改用 HTTPS 远程并让 `gh` 管理凭据：

```bash
gh auth login   # 选 HTTPS
git remote set-url origin https://github.com/OWNER/REPO.git
```

## Missing environment — user reply template

When preflight or a failed command shows the environment is incomplete, **stop the
task** and reply using this structure (in Chinese unless the user uses another language).
Fill every section; omit only sections that are already OK.

```markdown
## 无法继续：GitHub 环境未就绪

当前任务需要先配置本机环境。Agent 的 `bash` 工具只能调用您机器上已安装的程序，
无法在对话里替您完成交互式登录。

### 缺失项
- [ ] **bash（shell）** — …（若缺失；否则写「已就绪」）
- [ ] **git** — …
- [ ] **GitHub CLI (gh)** — …
- [ ] **GitHub 登录 (gh auth)** — …
- [ ] **SSH 密钥（仅 SSH 克隆/推送时需要）** — …

### 检测到的错误摘要
（粘贴 1–3 行关键报错，例如 `gh: command not found` 或 `bash executable was not found`）

### 请您在本机终端执行（按顺序）
1. …（安装命令，来自上一节 Install & configure）
2. …
3. `gh auth login`（在**您自己的终端**里完成浏览器/令牌流程）
4. 验证：`gh auth status` 和 `gh repo view OWNER/REPO`（或您的目标仓库）

### 完成后
请告诉我「环境已配好」或贴 `gh auth status` 的输出，我再继续执行：{原计划的一步，如 clone / fork / create release}。

### Agent 无法代劳
- 交互式 `gh auth login`、浏览器 OAuth、粘贴 PAT 到聊天
- 在对话中发送 GitHub token / 密码
```

After the user confirms setup, **re-run Environment preflight** before retrying the
original GitHub command.

## Orient Before Acting

For an existing local checkout:

```bash
git rev-parse --is-inside-work-tree 2>/dev/null && git remote -v
git branch --show-current
gh repo view --json name,owner,url,defaultBranchRef,isFork,parent 2>/dev/null
```

For a remote-only question (no local clone yet):

```bash
gh repo view OWNER/REPO --json name,owner,url,defaultBranchRef,visibility,isFork,parent
```

Replace `OWNER/REPO` with the slug the user gave, or derive it from a GitHub URL.

## Clone

Pick HTTPS or SSH to match how the user already authenticates (`gh auth status`
shows the preferred protocol).

```bash
# HTTPS (default for gh)
gh repo clone OWNER/REPO
gh repo clone OWNER/REPO -- --depth 1          # shallow
gh repo clone OWNER/REPO -- --branch main      # single branch

# Into a specific directory
gh repo clone OWNER/REPO my-dir

# SSH
gh repo clone OWNER/REPO -- --config core.sshCommand=ssh
# or: git clone git@github.com:OWNER/REPO.git
```

After clone, `cd` into the directory and run the orient commands. Report the absolute
path to the user.

## Create a New Repository

Use when the user wants a **new GitHub repo** (empty or from local files).

```bash
# Empty repo on GitHub, then clone locally
gh repo create my-project --public --description "Short description"
gh repo create my-project --private

# Create from current directory (must already be a git repo or use --source)
gh repo create my-project --public --source=. --remote=origin --push

# Create under an org (needs permission)
gh repo create my-org/my-project --public
```

Workflow when starting from scratch in workspace:

1. `git init` in the target directory (if not already a repo).
2. Add `.gitignore` / initial files as the user requested.
3. `gh repo create ... --source=. --remote=origin` — **only push when the user
   asked to publish** (same rule as git-workflow: no surprise pushes).
4. Report the repo URL (`gh repo view --json url -q .url`).

## Fork

```bash
# Fork to your account (default)
gh repo fork OWNER/REPO

# Fork into a specific org
gh repo fork OWNER/REPO --org my-org

# Clone the fork in one step
gh repo fork OWNER/REPO --clone
```

After forking, wire **upstream** so sync stays easy (see Remotes below).

## Manage Remotes

Common patterns:

| Situation | `origin` | `upstream` |
|-----------|----------|------------|
| You created the repo | your repo | (none) |
| You cloned someone else's repo | their repo | (optional, add if you will PR) |
| You forked | your fork | original repo |

```bash
# Show remotes
git remote -v

# Add upstream after a fork
git remote add upstream https://github.com/OWNER/ORIGINAL.git
# or: git remote add upstream git@github.com:OWNER/ORIGINAL.git

# Change URL (e.g. HTTPS → SSH)
git remote set-url origin git@github.com:YOU/REPO.git

# Rename or remove (confirm with user before remove)
git remote rename origin old-origin
git remote remove upstream
```

Sync fork with upstream (read-only fetch + merge/rebase on your branch — use
git-workflow for conflict resolution):

```bash
git fetch upstream
git switch main
git merge upstream/main    # or: git rebase upstream/main
git push origin main       # only if user asked to update their fork on GitHub
```

## Releases

Releases attach notes and optional assets to an **existing tag**. The tag must
exist locally or on the remote before `gh release create`.

```bash
# List releases
gh release list --repo OWNER/REPO

# View one release
gh release view v1.0.0 --repo OWNER/REPO

# Create release from an existing tag
gh release create v1.0.0 --repo OWNER/REPO --title "1.0.0" --notes "Changelog here"

# Create tag + release together (creates annotated tag on current HEAD)
gh release create v1.0.0 --title "1.0.0" --generate-notes

# Upload build artifacts
gh release upload v1.0.0 ./dist/app.zip --repo OWNER/REPO
```

Before creating a release:

1. Confirm the user wants this version tag published.
2. Ensure `HEAD` (or the chosen commit) is the intended artifact — run `git log -1 --oneline`.
3. Prefer `--generate-notes` or a user-supplied changelog; do not invent version numbers.

To delete a release (destructive — confirm first):

```bash
gh release delete v1.0.0 --repo OWNER/REPO --yes
```

## Inspect & Light Metadata Edits

Read-only inspection:

```bash
gh repo view OWNER/REPO
gh repo view OWNER/REPO --json description,visibility,defaultBranchRef,diskUsage
gh api repos/OWNER/REPO/branches --jq '.[].name'
```

Metadata edits (confirm intent):

```bash
gh repo edit OWNER/REPO --description "New description"
gh repo edit --enable-issues=false    # in local repo context
```

Do not toggle visibility (`--private` / `--public`) unless the user explicitly
requested it.

## Relationship to `git-workflow`

| Task | Use this skill | Use git-workflow |
|------|----------------|------------------|
| Clone / create / fork repo | yes | |
| Add upstream remote | yes | |
| Create GitHub Release | yes | |
| Branch, commit, push | | yes |
| Open/update PR, resolve conflicts | | yes |

Load **both** when the user says e.g. "fork this repo, fix conflicts, and open a PR":
fork/remotes here, then git-workflow for the rest.

## After Any Operation

- On **success**: report the **repo URL**, local **absolute path** (if cloned), and
  remotes (`git remote -v`). For releases, report the release URL
  (`gh release view TAG --json url -q .url`).
- On **failure**: do **not** only say "失败了". Follow **Failure diagnosis** → send
  **Missing environment — user reply template** with the specific missing layers and
  copy-paste install/login commands for the user's OS. Quote the actual error lines.
- Never ask for tokens in chat; never run interactive `gh auth login` in the agent shell.
