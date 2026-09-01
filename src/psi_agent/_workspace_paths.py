"""Workspace / agent-package path resolution — mechanism only, no product names.

Shared by Session-spawning runtime code (``SessionManager``) and by Gateway's
``GET /defaults``. Lives outside ``psi_agent.gateway`` for the same reason
``_appdata.py`` does: the managers that create Sessions must not import a
product package.

**This module knows no product concepts** — no tray, no webview, no Windows
drive letters, no desktop login, and in particular no brand folder names. The
two brand literals (the user-workspace folder name and the repo-local agent
package path) are owned by the caller and passed in; see
``gateway/_defaults.py`` for the ToC values.

Three mechanisms live here:

- ``resolve_user_workspace`` — explicit path, else ``{Desktop}/{default_name}``
  (announce only; **no** mkdir).
- ``ensure_workspace_dir`` — mkdir at Session spawn time.
- ``resolve_agent_package`` — explicit path or caller-rooted short name (raises
  when neither exists), else a caller-named candidate under cwd, else cwd itself
  when it looks like an agent package (``tools/`` + ``skills/``).
"""

# 报错文案与 INFO 日志都是**用户可见的 CLI 输出**, 一律英文 (与 ``--help`` 同口径)。
# 模块内注释与 docstring 里的中文照旧 —— 那些不上 CLI。

from __future__ import annotations

import anyio
import platformdirs
from loguru import logger


async def resolve_user_workspace(explicit: str = "", *, default_name: str) -> str:
    """Absolute user workspace path (announce only — does not create).

    *explicit* non-empty → resolve that path. Empty → ``{Desktop}/{default_name}``
    via ``platformdirs.user_desktop_dir`` (never hand-written ``%USERPROFILE%``).
    *default_name* is the caller's folder name — this module has no default of
    its own. Directory creation is deferred to ``ensure_workspace_dir``.
    """
    raw = explicit.strip()
    if raw:
        return str(await anyio.Path(raw).resolve())
    # Sync platformdirs call is path math only (no IO); fine inside async.
    desktop = anyio.Path(platformdirs.user_desktop_dir())
    ws = desktop / default_name
    return str(await ws.resolve())


async def ensure_workspace_dir(path: str) -> str:
    """Create *path* if missing; return absolute path.

    Call from Session spawn only (``SessionManager.create``), not from
    ``GET /defaults`` / Gateway boot — so a soft default folder appears only
    when the user actually starts a conversation.
    """
    ws = anyio.Path(path.strip())
    await ws.mkdir(parents=True, exist_ok=True)
    return str(await ws.resolve())


async def _agent_short_name_choices(root: anyio.Path) -> list[str]:
    """Sub-directory names under *root*, sorted — the values a short name may take.

    Only used to build the error message. Dotted / underscored entries are
    dropped: ``.git`` and ``__pycache__`` are not selectable agent packages, and
    listing them as choices would just add noise to the failure the user reads.
    """
    if not await root.is_dir():
        return []
    names = [
        entry.name async for entry in root.iterdir() if await entry.is_dir() and not entry.name.startswith((".", "_"))
    ]
    return sorted(names)


async def resolve_agent_package(
    explicit: str = "",
    *,
    repo_candidate: str = "",
    short_name_root: str = "",
    label: str = "agent package",
) -> str:
    """Absolute agent package path, or ``""`` for Session workspace fallback.

    An explicit value is resolved against two shapes, in this order:

    1. *explicit* is itself an existing directory (absolute or cwd-relative) →
       use it. Tried first so the two shapes that already worked before short
       names existed (``/abs/path`` and ``<root>/<name>``) keep resolving to the
       exact same place, whatever *short_name_root* happens to hold.
    2. *explicit* names a directory directly under *short_name_root* (the
       caller's short-name search dir) → use it. Second, not first, for the
       reason above; a bare short name only reaches this rule because rule 1
       found no such directory under cwd.
    3. Neither exists → ``FileNotFoundError``. **Deliberately not a silent
       fallback**: before this rule, a typo'd short name resolved to
       ``{cwd}/{typo}`` unchecked, Gateway booted clean (boot never touches the
       path) and the mistake only surfaced much later as a Session with no
       tools/skills. The error names the candidates tried and the short names
       actually available, so the fix is readable off the failure.

    An *empty* explicit value is a meaningful third state (caller asked for no
    particular agent), never an error — it walks the soft-default chain:

    4. *repo_candidate* (caller-supplied, relative to cwd) when it is a
       directory — the repo-local layout where Gateway starts from the repo root.
    5. cwd itself when it holds ``tools/`` + ``skills/`` — the installed layout,
       where the install dir *is* the agent package.
    6. Otherwise ``""`` → Session single-root compat (agent ≡ workspace).

    Both *repo_candidate* and *short_name_root* are caller-owned strings: this
    module never learns which folder the brand keeps its packages under.
    *label* is how the caller names itself in log lines and errors (Gateway
    passes its flag name) — same reason: the flag name belongs to the caller,
    not to the mechanism.

    Every branch logs its outcome at INFO, unconditionally, including which of
    the rules above matched. ``AGENTS.md`` 那条教训: 凡「两处必须一致」的路径,
    各方都应在启动时打印自己的解析结果 —— 一个不打印的解析器, 出错时只能靠猜。
    """
    cwd = await anyio.Path.cwd()
    raw = explicit.strip()
    if raw:
        as_given = anyio.Path(raw)
        if await as_given.is_dir():
            resolved = str(await as_given.resolve())
            logger.info(f"{label}: {resolved} (explicit path {raw!r})")
            return resolved
        root_rel = short_name_root.strip()
        if root_rel:
            under_root = cwd / root_rel / raw
            if await under_root.is_dir():
                resolved = str(await under_root.resolve())
                logger.info(f"{label}: {resolved} (short name {raw!r} under {root_rel!r})")
                return resolved
            choices = await _agent_short_name_choices(cwd / root_rel)
            offer = "{" + ",".join(choices) + "}" if choices else "(none)"
            # 路径用引号夹而不是 ``!r``: repr 会把 Windows 的 ``\`` 转义成 ``\\``,
            # 报错里印出的路径就没法直接复制粘贴去 ls。
            raise FileNotFoundError(
                f"{label} '{raw}' is not an agent package: tried "
                f"'{await as_given.absolute()}' and '{await under_root.absolute()}', "
                f"neither is a directory. Available short names under '{root_rel}': {offer}"
            )
        raise FileNotFoundError(
            f"{label} '{raw}' is not an agent package: '{await as_given.absolute()}' is not a directory"
        )
    candidate_rel = repo_candidate.strip()
    if candidate_rel:
        candidate = cwd / candidate_rel
        if await candidate.is_dir():
            resolved = str(await candidate.resolve())
            logger.info(f"{label}: {resolved} (soft default {candidate_rel!r} under cwd)")
            return resolved
    if await (cwd / "tools").is_dir() and await (cwd / "skills").is_dir():
        resolved = str(await cwd.resolve())
        logger.info(f"{label}: {resolved} (soft default: cwd has tools/ + skills/)")
        return resolved
    logger.info(f"{label}: (none — Session falls back to its own workspace)")
    return ""
