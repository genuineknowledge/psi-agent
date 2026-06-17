---
name: github-auth
description: Authenticate with GitHub via gh CLI and verify token scopes.
---

**Check current auth status:**
```bash
gh auth status
```
This shows: logged-in account, token scopes, and active host.

**Login:**
```bash
gh auth login
# Follow interactive prompts, or:
gh auth login --hostname github.com --git-protocol https --web
```

**Login with a token (non-interactive):**
```bash
echo "$GITHUB_TOKEN" | gh auth login --with-token
```

**Required scopes for common operations:**
- Basic repo operations: `repo`
- Read-only public repos: `public_repo`
- Create/manage issues and PRs: `repo`
- Manage Actions: `workflow`
- Manage packages: `write:packages`
- Org management: `admin:org`

**Check token scopes:**
```bash
gh auth status
# Or via API:
curl -H "Authorization: Bearer $(gh auth token)" \
  https://api.github.com/rate_limit -I | grep x-oauth-scopes
```

**Refresh/add scopes:**
```bash
gh auth refresh --scopes repo,workflow
```

**Switch accounts or hosts:**
```bash
gh auth login --hostname github.enterprise.com
gh auth switch --user other-account
```

**Revoke:**
```bash
gh auth logout
```
