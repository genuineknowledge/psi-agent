"""Gateway path defaults (agent / workspace / AppData root) — ToC brand values.

What this module is for
-----------------------
Callers that create Sessions (spa v1/v2, Feishu, haitun ``sessions_create``, …)
need a shared answer to: "what is the default agent package?" and "what is the
default user workspace?". ``GET /defaults`` and ``SessionManager`` both use
these resolvers.

**This module owns only the brand literals.** The mechanism (desktop path math,
mkdir, ``tools/`` + ``skills/`` probing) lives in ``psi_agent._workspace_paths``,
outside this package, so Session-spawning managers can reach it without
importing a product package. Splitting it this way keeps the ToC names
(``haitun交付``, ``agents/feishu``) in exactly one place — renaming
the workspace touches only ``gateway/``.

AppData path helpers live in ``psi_agent._appdata`` (Session-safe; no circular
import). This module re-exports them, and ``ensure_workspace_dir``, for existing
Gateway / workspace-tool call sites.

Explicit agent (``--default-agent`` non-empty)
---------------------------------------------
Two shapes, tried in order: the value as given (absolute or cwd-relative dir),
then ``agents/<value>`` — so a short name like ``desktop`` selects
``agents/desktop``. Neither existing is a **startup error**, not a silent
fallback: 指到不存在的目录时 Gateway 启动期完全不碰这个路径, 日志干净、端口正常,
错要等建 Session 时才暴露成「这个 Session 没有 tools/skills」。

Soft default (agent)
--------------------
If CLI ``--default-agent`` is empty:

1. Prefer ``cwd/agents/feishu`` when present (repo-local Gateway).
2. Else if *cwd itself* looks like a haitun agent package (``tools/`` + ``skills/``
   directories) — the Inno install layout, where ``{app}`` *is* the workspace —
   use cwd. This keeps ``psi-agent.exe gateway`` usable from the install dir
   even without the ``haitun.exe`` launcher flags.
3. Otherwise agent stays ``\"\"`` → Session single-root compat (agent ≡ workspace).

Soft default (workspace)
------------------------
If CLI ``--default-workspace`` is empty, announce ``{Desktop}/haitun交付``
(**path only** — do not mkdir here). Ordinary users get deliverables on the
Desktop without picking a folder; power users override via CLI / spa settings.
Intentional: mkdir only in ``SessionManager.create`` (start chat / new task),
so opening Haitun does not leave an empty Desktop folder. Not AppData.
"""

from __future__ import annotations

from psi_agent._appdata import (
    appdata_history_path,
    appdata_state_dir,
    appdata_state_latest_path,
    appdata_todo_path,
    appdata_todo_segments_path,
    legacy_history_path,
    legacy_state_latest_path,
    legacy_todo_path,
    resolve_appdata_root,
    resolve_history_read_path,
    resolve_state_read_path,
    resolve_todo_read_path,
)
from psi_agent._workspace_paths import (
    ensure_workspace_dir,
    resolve_agent_package,
    resolve_user_workspace,
)

# Soft default under the OS Desktop — layered for non-technical users.
DEFAULT_USER_WORKSPACE_NAME = "haitun交付"
# Repo-local agent package, relative to cwd (developers starting from repo root).
DEFAULT_AGENT_REPO_CANDIDATE = "agents/feishu"
# 短名的搜索目录: ``--default-agent desktop`` → ``agents/desktop``。
#
# 从上面那个常量**推导**而不是再写一遍 "agents": 两处各写一份字面量, 改布局时漏掉一处
# 就会出现「软默认指 agents/feishu、短名却在别处找」的错位, 而这种错位不报错 —— 短名
# 找不到会报错, 但报的是「没这个包」, 指不到真正的原因。
DEFAULT_AGENT_SHORT_NAME_ROOT = DEFAULT_AGENT_REPO_CANDIDATE.rsplit("/", 1)[0]

__all__ = [
    "DEFAULT_AGENT_REPO_CANDIDATE",
    "DEFAULT_AGENT_SHORT_NAME_ROOT",
    "DEFAULT_USER_WORKSPACE_NAME",
    "appdata_history_path",
    "appdata_state_dir",
    "appdata_state_latest_path",
    "appdata_todo_path",
    "appdata_todo_segments_path",
    "ensure_workspace_dir",
    "legacy_history_path",
    "legacy_state_latest_path",
    "legacy_todo_path",
    "resolve_appdata_root",
    "resolve_default_agent",
    "resolve_default_workspace",
    "resolve_history_read_path",
    "resolve_state_read_path",
    "resolve_todo_read_path",
]


async def resolve_default_workspace(explicit: str = "") -> str:
    """Absolute user workspace path (announce only — does not create).

    Thin brand wrapper over ``_workspace_paths.resolve_user_workspace``: supplies
    ``haitun交付`` as the soft Desktop folder name. Directory creation is
    deferred to ``ensure_workspace_dir`` at Session create time.
    """
    return await resolve_user_workspace(explicit, default_name=DEFAULT_USER_WORKSPACE_NAME)


async def resolve_default_agent(explicit: str = "") -> str:
    """Absolute agent package path, or ``\"\"`` for Session workspace fallback.

    Thin brand wrapper over ``_workspace_paths.resolve_agent_package``: supplies
    ``agents/feishu`` as the repo-local candidate and ``agents`` as the dir short
    names are looked up in, so ``--default-agent desktop`` finds
    ``agents/desktop``. Raises ``FileNotFoundError`` when a non-empty value
    matches neither shape (empty stays a legal third state).
    """
    return await resolve_agent_package(
        explicit,
        repo_candidate=DEFAULT_AGENT_REPO_CANDIDATE,
        short_name_root=DEFAULT_AGENT_SHORT_NAME_ROOT,
        label="--default-agent",
    )
