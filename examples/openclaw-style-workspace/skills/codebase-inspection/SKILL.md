---
name: codebase-inspection
description: Systematically map an unfamiliar codebase — structure, entry points, key flows.
---

**Step 1: Map the structure**
```bash
# Directory tree (2 levels)
find . -maxdepth 2 -not -path '*/__pycache__/*' -not -path '*/.git/*' | sort
# Or: ls -R
```
Note: language, package manager, test framework, CI config.

**Step 2: Find entry points**
- Look for `main.py`, `__main__.py`, `index.ts`, `app.py`, `server.py`, `cli.py`.
- Check `pyproject.toml` `[project.scripts]`, `package.json` `"main"` / `"scripts"`.
- Check `Dockerfile`, `Makefile`, `docker-compose.yml` for startup commands.

**Step 3: Read key files**
- `README.md` or `CLAUDE.md` — purpose and architecture overview.
- Entry point(s) — understand the startup sequence.
- Config/settings file — understand environment and dependencies.

**Step 4: Trace a key flow**
Pick one important user action and trace it through the code:
- How does a request arrive? → how is it processed? → how is a response returned?
- Use `grep` to find function/class names as you follow the chain.

**Step 5: Summarise**
Write a short report:
- Purpose of the project
- Key modules and their roles
- Main data flow
- Notable patterns or conventions used
- Anything surprising or unclear
