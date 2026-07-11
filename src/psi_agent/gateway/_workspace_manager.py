from __future__ import annotations

import os
import string
import sys
from pathlib import Path
from typing import Any

import anyio
from loguru import logger


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
            entry_path = _norm_path(await entry.resolve())
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
