"""Gateway path defaults for SPA / Session create (no AppData yet).

``default_agent`` / ``default_workspace`` are CLI overrides. When agent is
empty and ``examples/haitun-workspace`` exists under cwd, that path is used
as a soft default so repo-local Gateway open-and-use works; otherwise agent
stays empty and Session keeps single-root compat (agent ≡ workspace).
"""

from __future__ import annotations

import anyio


async def resolve_default_workspace(explicit: str = "") -> str:
    """Absolute user workspace path; empty *explicit* → process cwd."""
    raw = explicit.strip()
    if raw:
        return str(await anyio.Path(raw).resolve())
    return str(await anyio.Path.cwd())


async def resolve_default_agent(explicit: str = "") -> str:
    """Absolute agent package path, or ``\"\"`` for Session workspace fallback."""
    raw = explicit.strip()
    if raw:
        return str(await anyio.Path(raw).resolve())
    candidate = (await anyio.Path.cwd()) / "examples" / "haitun-workspace"
    if await candidate.is_dir():
        return str(await candidate.resolve())
    return ""
