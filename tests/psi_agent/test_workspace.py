from __future__ import annotations

from pathlib import Path

import pytest

from psi_agent.errors import UserFacingError
from psi_agent.workspace import resolve_workspace_path


def test_resolve_workspace_path_rejects_empty() -> None:
    with pytest.raises(UserFacingError, match="Workspace is required"):
        resolve_workspace_path("")


def test_resolve_workspace_path_rejects_file(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-workspace.txt"
    file_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(UserFacingError, match="Workspace is not a directory"):
        resolve_workspace_path(str(file_path))


def test_resolve_workspace_path_returns_resolved_directory(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    assert resolve_workspace_path(str(workspace)) == workspace.resolve()
