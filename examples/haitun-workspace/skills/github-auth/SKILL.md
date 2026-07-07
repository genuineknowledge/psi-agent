---
name: github-auth
description: "GitHub authentication setup: HTTPS personal access tokens (git credential helper), SSH keys (ssh-keygen + ssh-agent + adding the public key to GitHub), and gh CLI login (gh auth login / gh auth status). Use whenever a task needs to authenticate to GitHub for git push/pull or gh/API calls, fix 'Authentication failed' / 'Permission denied (publickey)' / 403 errors, switch between HTTPS and SSH remotes, rotate or store a token, or verify current auth. Emphasizes never printing or committing tokens/private keys and preferring the OS credential store."
category: github
---

# GitHub Authentication Setup

Use this skill to get a machine authenticated to GitHub over any of the three common paths:
HTTPS with a personal access token (PAT), SSH keys, or the `gh` CLI. All of these are local
shell operations — run them through the `bash` tool (on Windows the bundled msys64 provides
`git`, `ssh`, `ssh-keygen`, and `ssh-agent`; `gh` must be installed separately).

Reply in Chinese unless the user clearly uses another language.

## Safety Rules (highest priority)

- **Never print, echo, or log a token or private key.** Don't `cat` a PAT, `~/.ssh/id_*`
  (the private key), `.netrc`, or `gh` config. When you must reference one, name it, don't
  show it. The **public** key (`*.pub`) is safe to display — that's the one you upload.
- **Never commit secrets.** A PAT, private key, or `.netrc` must never enter git. If a repo
  needs one, it belongs in the OS credential store or an ignored file, not the tree.
- **Prefer the OS credential store** over plaintext. Use a git credential helper
  (`manager` / `osxkeychain` / `libsecret`) so the token is encrypted at rest, not written to
  `~/.git-credentials` in the clear.
- **Don't overwrite an existing SSH key.** `ssh-keygen` silently clobbers on some prompts;
  check for `~/.ssh/id_ed25519` first and pick a new filename rather than replacing a key the
  user may rely on elsewhere.
- **Uploading a key or creating a token is the user's action.** You can generate a keypair and
  print the public half, but adding it to the GitHub account (via the website or `gh`) sends
  data to an external service — confirm the user wants it, or hand them the value to paste.
- **Ask for the token; don't invent one.** If a step needs a PAT, have the user create it at
  GitHub → Settings → Developer settings → Personal access tokens and paste it into a prompt —
  never fabricate or guess a value.

## Orient First (read-only)

Figure out what's already configured before changing anything:

```bash
gh auth status 2>&1 || echo "gh not logged in / not installed"
ssh -T git@github.com 2>&1 | head -3          # "Hi <user>!" means SSH works
git config --get-regexp '^credential' 2>&1     # existing credential helper(s)
git -C <repo> remote -v 2>&1                    # is the remote https:// or git@ ?
ls -1 ~/.ssh/*.pub 2>/dev/null                  # existing public keys (safe to list)
```

The remote URL scheme decides which method authenticates a push: `https://github.com/...`
uses a PAT (or gh), `git@github.com:...` uses an SSH key. Match the fix to the remote, or
switch the remote (see the last section).

## Option A — HTTPS with a Personal Access Token

Best when the remote is `https://` and you can't or don't want to manage SSH keys.

1. **Create the token** on GitHub: Settings → Developer settings → Personal access tokens.
   Prefer a **fine-grained** token scoped to the specific repos, with `Contents: Read/Write`
   (add more scopes only as needed). Set a sane expiry. For classic tokens, `repo` scope
   covers private-repo push.
2. **Store it in the OS credential manager** (don't paste it into a URL or a plaintext file):

   ```bash
   # Windows (bundled): Git Credential Manager
   git config --global credential.helper manager
   # macOS:
   git config --global credential.helper osxkeychain
   # Linux:
   git config --global credential.helper libsecret   # or: cache --timeout=3600
   ```

   Then trigger one authenticated operation (e.g. `git push`); git prompts for username and
   password — **paste the PAT as the password** (not your account password). The helper caches
   it from then on.
3. **Verify:** `git ls-remote https://github.com/<owner>/<repo>.git` succeeds without
   re-prompting.

To rotate or remove a stored token, use the helper (`git credential-manager erase`, Keychain
Access, or `git config --global --unset credential.helper` + clear the store) rather than
editing files by hand.

## Option B — SSH Keys

Best for a machine you control and push from regularly.

1. **Check for an existing key** so you don't clobber it:

   ```bash
   ls -1 ~/.ssh/id_ed25519.pub 2>/dev/null && echo "key already exists — reuse it"
   ```
2. **Generate an Ed25519 keypair** if none suitable exists (Ed25519 preferred over RSA):

   ```bash
   ssh-keygen -t ed25519 -C "your_email@example.com" -f ~/.ssh/id_ed25519
   ```

   Use a passphrase when possible. To avoid clobbering, give `-f` a fresh name if the default
   exists.
3. **Start the agent and add the key:**

   ```bash
   eval "$(ssh-agent -s)"
   ssh-add ~/.ssh/id_ed25519          # macOS: ssh-add --apple-use-keychain ~/.ssh/id_ed25519
   ```
4. **Upload the PUBLIC key to GitHub** (the `.pub` file — safe to display):

   ```bash
   cat ~/.ssh/id_ed25519.pub          # copy this, or:
   gh ssh-key add ~/.ssh/id_ed25519.pub --title "$(hostname)"   # if gh is logged in
   ```

   Without `gh`, hand the user the printed public key to paste at GitHub → Settings →
   SSH and GPG keys → New SSH key. Never upload or print the private key (no `.pub` suffix).
5. **Verify:** `ssh -T git@github.com` should greet you with `Hi <username>!`
   (exit code 1 with that greeting is normal — GitHub doesn't grant a shell).

## Option C — gh CLI Login

`gh` is the simplest path and can also configure git to use itself as a credential helper.

1. **Check install:** `gh --version` (install via the platform package manager if missing —
   e.g. `winget install GitHub.cli`, `brew install gh`, or the apt/dnf repo).
2. **Log in** — `gh auth login` is interactive (it opens a browser device flow or takes a
   pasted token). In a **non-interactive** environment, pipe a token via stdin instead:

   ```bash
   # Interactive (preferred when a browser/TTY is available):
   gh auth login --hostname github.com --git-protocol https

   # Non-interactive (token from an env var you set, never hard-coded):
   printf '%s' "$GH_TOKEN" | gh auth login --hostname github.com --with-token
   ```

   Note: interactive prompts may not work through the agent's shell — prefer `--with-token`
   with a token the user supplies, or have the user run `gh auth login` themselves and report
   back.
3. **Wire git through gh** so `git push` over HTTPS reuses the gh credential:

   ```bash
   gh auth setup-git
   ```
4. **Verify:** `gh auth status` shows the logged-in account and token scopes.

## Switching a Remote Between HTTPS and SSH

If the auth method you set up doesn't match the remote scheme, switch the remote:

```bash
# HTTPS → SSH
git remote set-url origin git@github.com:<owner>/<repo>.git
# SSH → HTTPS
git remote set-url origin https://github.com/<owner>/<repo>.git
git remote -v          # confirm
```

## Troubleshooting

- **`remote: Support for password authentication was removed` / HTTP 403 on push** — you're
  sending an account password over HTTPS. Use a PAT as the password (Option A) or gh (Option C).
- **`Permission denied (publickey)`** — SSH key isn't loaded or isn't on the account. Re-check
  `ssh-add -l` and that the matching `.pub` is uploaded; test with `ssh -T git@github.com`.
- **`Could not resolve host` / proxy errors** — network/proxy issue, not auth; check
  connectivity and any `https_proxy` / `~/.ssh/config` settings.
- **Wrong account pushes** — a stale credential is cached. Clear it from the credential helper
  (Option A) or run `gh auth switch` / re-login.

## After Setup

- Verify with the method's check command (`gh auth status`, `ssh -T git@github.com`, or an
  authenticated `git ls-remote`), and report which method is now active for the repo.
- Confirm no secret was written to the tree or printed: the private key and any PAT stay in
  the credential store / `~/.ssh` (mode 600), never in git.
