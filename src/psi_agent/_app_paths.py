"""Application data paths via platformdirs (history + state roots).

Do not hardcode ``%AppData%`` / XDG paths — always go through ``platformdirs``,
with optional env / explicit override for tests and portable installs.
"""

from __future__ import annotations

import os
from pathlib import Path

import platformdirs

# Env override for the AppData root (tests / portable). Empty = platformdirs default.
APP_DATA_ROOT_ENV = "PSI_APP_DATA_ROOT"
DEFAULT_AGENT_ENV = "PSI_DEFAULT_AGENT"
DEFAULT_WORKSPACE_ENV = "PSI_DEFAULT_WORKSPACE"

_APP_NAME = "Haitun"


def app_data_root(*, override: str | None = None) -> Path:
    """Return the Haitun AppData root directory.

    Resolution order: *override* argument → ``PSI_APP_DATA_ROOT`` →
    ``platformdirs.user_data_dir``.
    """
    if override is not None and override.strip():
        return Path(override).expanduser().resolve()
    env = os.environ.get(APP_DATA_ROOT_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path(platformdirs.user_data_dir(appname=_APP_NAME, appauthor=False))


def state_dir(*, override: str | None = None) -> Path:
    """``{app_data}/state`` — Gateway cross-session registry (latest.json, …)."""
    return app_data_root(override=override) / "state"


def history_dir(*, override: str | None = None) -> Path:
    """``{app_data}/history`` — session JSONL + ``meta.jsonl`` index."""
    return app_data_root(override=override) / "history"


def history_meta_path(*, override: str | None = None) -> Path:
    """Index of sessions: id / name / workspace / agent (JSONL, one object per line)."""
    return history_dir(override=override) / "meta.jsonl"


def todos_dir(*, override: str | None = None) -> Path:
    """``{app_data}/todos`` — session todo JSON (moved off user workspace)."""
    return app_data_root(override=override) / "todos"


def default_agent_path(*, override: str | None = None) -> Path:
    """Default Agent package directory (examples/haitun after rename).

    Order: *override* → ``PSI_DEFAULT_AGENT`` → ``cwd/examples/haitun`` →
    repo-relative ``examples/haitun`` next to the installed package layout.
    """
    if override is not None and override.strip():
        return Path(override).expanduser().resolve()
    env = os.environ.get(DEFAULT_AGENT_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    cwd_candidate = Path.cwd() / "examples" / "haitun"
    if cwd_candidate.is_dir():
        return cwd_candidate.resolve()
    # src/psi_agent/_app_paths.py → parents[2] = repo root when running from source
    repo_candidate = Path(__file__).resolve().parents[2] / "examples" / "haitun"
    if repo_candidate.is_dir():
        return repo_candidate.resolve()
    # Legacy name during migration
    for legacy in (
        Path.cwd() / "examples" / "haitun-workspace",
        Path(__file__).resolve().parents[2] / "examples" / "haitun-workspace",
    ):
        if legacy.is_dir():
            return legacy.resolve()
    return cwd_candidate.resolve()


def default_workspace_path(*, override: str | None = None) -> Path:
    """Default user workspace (Cursor-style open folder).

    Order: *override* → ``PSI_DEFAULT_WORKSPACE`` → ``Path.cwd()``.
    """
    if override is not None and override.strip():
        return Path(override).expanduser().resolve()
    env = os.environ.get(DEFAULT_WORKSPACE_ENV, "").strip()
    if env:
        return Path(env).expanduser().resolve()
    return Path.cwd().resolve()
