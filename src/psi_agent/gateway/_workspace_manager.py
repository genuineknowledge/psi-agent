from __future__ import annotations

import os
import re
import string
import sys
from pathlib import Path
from typing import Any, TypedDict

import anyio
from loguru import logger

_WORKFLOW_NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_WINDOWS_RESERVED_WORKFLOW_NAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(f"com{number}" for number in range(1, 10)),
    *(f"lpt{number}" for number in range(1, 10)),
}
_MAX_WORKFLOW_SOURCE_BYTES = 8 * 1024 * 1024


class WorkflowSummary(TypedDict):
    name: str
    path: str


def _norm_path(path: str | Path) -> str:
    return str(Path(path).resolve()).replace("\\", "/")


def _path_segments(path: str) -> list[dict[str, str]]:
    resolved = Path(path).resolve()
    parts = resolved.parts
    if not parts:
        return []
    segments: list[dict[str, str]] = []
    if sys.platform == "win32" and len(parts[0]) == 2 and parts[0][1] == ":":
        drive = f"{parts[0]}/"
        segments.append({"name": parts[0], "path": _norm_path(drive)})
        acc = Path(drive)
        for part in parts[1:]:
            acc = acc / part
            segments.append({"name": part, "path": _norm_path(acc)})
        return segments
    acc = Path(parts[0])
    segments.append({"name": parts[0], "path": _norm_path(acc)})
    for part in parts[1:]:
        acc = acc / part
        segments.append({"name": part, "path": _norm_path(acc)})
    return segments


def _known_user_dirs() -> list[tuple[str, str, str]]:
    home = Path.home()
    return [
        ("desktop", "桌面", str(home / "Desktop")),
        ("documents", "文档", str(home / "Documents")),
        ("downloads", "下载", str(home / "Downloads")),
    ]


class WorkspaceManager:
    def get_cwd(self) -> str:
        """Return the current working directory."""
        return _norm_path(Path.cwd())

    @staticmethod
    def _is_valid_workflow_name(name: str) -> bool:
        """Return whether name is a portable workflow registry segment."""

        return _WORKFLOW_NAME_RE.fullmatch(name) is not None and name not in _WINDOWS_RESERVED_WORKFLOW_NAMES

    async def list_roots(self) -> dict[str, Any]:
        roots: list[dict[str, str]] = []
        drives: list[dict[str, str]] = []

        cwd = self.get_cwd()
        roots.append({"id": "cwd", "label": "Gateway 当前目录", "path": cwd})

        home = _norm_path(Path.home())
        roots.append({"id": "home", "label": "用户目录", "path": home})

        for dir_id, label, raw in _known_user_dirs():
            p = Path(raw)
            if await anyio.Path(p).exists():
                roots.append({"id": dir_id, "label": label, "path": _norm_path(p)})

        if sys.platform == "win32":
            for letter in string.ascii_uppercase:
                drive = f"{letter}:/"
                if await anyio.Path(drive).exists():
                    drives.append({"label": f"本地磁盘 ({letter}:)", "path": drive})
        else:
            drives.append({"label": "根目录 /", "path": "/"})

        return {"roots": roots, "drives": drives}

    async def browse(self, path: str, *, kind: str = "directory", q: str = "") -> dict[str, Any]:
        logger.debug(f"Browsing directory: {path!r} kind={kind!r} q={q!r}")
        raw_path = path.strip() or os.getcwd()
        dir_path = anyio.Path(raw_path)
        if not await dir_path.exists():
            raise FileNotFoundError(f"Path not found: {raw_path!r}")
        if not await dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {raw_path!r}")

        resolved = _norm_path(raw_path)
        parent_raw = os.path.dirname(resolved.replace("/", os.sep))
        parent = _norm_path(parent_raw) if parent_raw else resolved

        entries: list[dict[str, Any]] = []
        query = q.strip().lower()
        include_files = kind in {"file", "all"}

        async for entry in dir_path.iterdir():
            name = entry.name
            if name.startswith("."):
                continue
            if query and query not in name.lower():
                continue
            entry_path = _norm_path(str(await entry.resolve()))
            if await entry.is_dir():
                entries.append({"name": name, "path": entry_path, "kind": "directory"})
            elif include_files and await entry.is_file():
                try:
                    size = (await entry.stat()).st_size
                except OSError:
                    size = 0
                entries.append({"name": name, "path": entry_path, "kind": "file", "size": size})

        entries.sort(key=lambda e: (0 if e["kind"] == "directory" else 1, e["name"].lower()))

        return {
            "path": resolved,
            "parent": parent,
            "segments": _path_segments(resolved),
            "entries": entries,
        }

    async def list_workflows(self, workspace_path: str) -> list[WorkflowSummary]:
        """List canonical reusable workflow declarations from one workspace."""

        raw_workspace = workspace_path.strip() or os.getcwd()
        workspace = anyio.Path(raw_workspace)
        if not await workspace.exists():
            raise FileNotFoundError(f"Path not found: {raw_workspace!r}")
        if not await workspace.is_dir():
            raise NotADirectoryError(f"Not a directory: {raw_workspace!r}")

        workspace = await workspace.resolve()
        flows_root = workspace / "flows"
        if not await flows_root.exists():
            return []
        if await flows_root.is_symlink() or not await flows_root.is_dir():
            logger.warning(f"Skipping workflow registry because flows is not a regular directory: {flows_root!r}")
            return []

        workflow_root = flows_root / "workflows"
        if not await workflow_root.exists():
            return []
        if await workflow_root.is_symlink() or not await workflow_root.is_dir():
            logger.warning(f"Skipping workflow registry because it is not a regular directory: {workflow_root!r}")
            return []

        workflow_root = await workflow_root.resolve()
        workspace_native = Path(str(workspace))
        workflow_root_native = Path(str(workflow_root))
        if not workflow_root_native.is_relative_to(workspace_native):
            logger.warning(f"Skipping workflow registry outside workspace: {workflow_root!r}")
            return []

        workflows: list[WorkflowSummary] = []
        async for entry in workflow_root.iterdir():
            try:
                if await entry.is_symlink() or not await entry.is_dir():
                    continue

                name = entry.name
                if not self._is_valid_workflow_name(name):
                    logger.warning(f"Skipping workflow directory with invalid name: {name!r}")
                    continue

                resolved_entry = await entry.resolve()
                entry_native = Path(str(resolved_entry))
                if not entry_native.is_relative_to(workflow_root_native):
                    logger.warning(f"Skipping workflow directory outside registry: {entry!r}")
                    continue

                source = resolved_entry / f"{name}.workflow"
                if not await source.exists() or not await source.is_file() or await source.is_symlink():
                    logger.warning(f"Skipping incomplete workflow entry: {name!r}")
                    continue

                resolved_source = await source.resolve()
                if not Path(str(resolved_source)).is_relative_to(entry_native):
                    logger.warning(f"Skipping workflow entry with escaped files: {name!r}")
                    continue
                if (await resolved_source.stat()).st_size > _MAX_WORKFLOW_SOURCE_BYTES:
                    logger.warning(f"Skipping oversized workflow source: {name!r}")
                    continue

                workflows.append(
                    {
                        "name": name,
                        "path": f"flows/workflows/{name}/{name}.workflow",
                    }
                )
            except OSError as e:
                logger.warning(f"Skipping invalid workflow entry {entry!r}: {e}")

        workflows.sort(key=lambda workflow: workflow["name"])
        return workflows
