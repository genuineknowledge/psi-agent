from __future__ import annotations

from pathlib import Path

from psi_agent.errors import UserFacingError


def resolve_workspace_path(workspace: str) -> Path:
    if not workspace.strip():
        raise UserFacingError(
            "Workspace is required.",
            "Pass --workspace PATH, for example --workspace examples/fusion-flow-workspace.",
        )

    path = Path(workspace).expanduser()
    if not path.exists():
        raise UserFacingError(
            f"Workspace not found: {path}",
            "Check the path or run from the psi-agent project directory.",
        )
    if not path.is_dir():
        raise UserFacingError(
            f"Workspace is not a directory: {path}",
            "Pass a workspace directory that contains systems/, tools/, or skills/.",
        )
    return path.resolve()
