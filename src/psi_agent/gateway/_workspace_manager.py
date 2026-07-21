from __future__ import annotations

import base64
import ctypes
import os
import sys
from pathlib import Path
from typing import Any

import anyio
import platformdirs
from loguru import logger


def _posix(path: Any) -> str:
    """Normalize path to POSIX / separators for JSON output."""
    return str(path).replace("\\", "/")


def _norm_fs(path: str) -> str:
    """Case- and separator-normalized path for containment checks (sync helper)."""
    return os.path.normcase(os.path.normpath(path))


def _win32_drives() -> list[str]:
    if not hasattr(ctypes, "windll"):
        return []
    kernel32 = ctypes.windll.kernel32
    bitmask = kernel32.GetLogicalDrives()
    drives: list[str] = []
    for i in range(26):
        if bitmask & (1 << i):
            drives.append(f"{chr(ord('A') + i)}:/")
    return drives


class WorkspaceManager:
    @staticmethod
    def _path_segments(path: str) -> list[dict[str, str]]:
        p = Path(path)
        ancestors: list[Path] = [p]
        cur = p
        while True:
            prt = cur.parent
            if prt == cur:
                break
            ancestors.append(prt)
            cur = prt

        segments: list[dict[str, str]] = []
        for a in reversed(ancestors):
            segments.append(
                {
                    "name": a.name or _posix(a),
                    "path": _posix(a),
                }
            )
        return segments

    def get_cwd(self) -> str:
        """Return the current working directory."""
        return _posix(Path.cwd())

    async def list_places(self) -> dict[str, Any]:
        logger.debug("Listing workspace places")
        places: list[dict[str, str]] = []
        drives: list[dict[str, str]] = []

        places.append({"id": "cwd", "label": "Gateway 当前目录", "path": self.get_cwd()})

        places.append({"id": "home", "label": "用户目录", "path": _posix(Path.home())})

        for dir_id, label, raw_path in (
            ("desktop", "桌面", platformdirs.user_desktop_dir()),
            ("documents", "文档", platformdirs.user_documents_dir()),
            ("downloads", "下载", platformdirs.user_downloads_dir()),
        ):
            if await anyio.Path(raw_path).exists():
                resolved = await anyio.Path(raw_path).resolve()
                places.append({"id": dir_id, "label": label, "path": _posix(resolved)})

        if sys.platform == "win32":
            for drive in _win32_drives():
                drives.append({"label": f"本地磁盘 ({drive[0]}):", "path": drive})
        else:
            drives.append({"label": "根目录 /", "path": "/"})

        return {"places": places, "drives": drives}

    async def browse(self, path: str, *, kind: str = "directory", q: str = "") -> dict[str, Any]:
        logger.debug(f"Browsing directory: {path!r} kind={kind!r} q={q!r}")
        raw = path.strip() or str(Path.cwd())
        dir_path = anyio.Path(raw)
        if not await dir_path.exists():
            raise FileNotFoundError(f"Path not found: {raw!r}")
        if not await dir_path.is_dir():
            raise NotADirectoryError(f"Not a directory: {raw!r}")

        resolved = await dir_path.resolve()
        parent = resolved.parent

        entries: list[dict[str, Any]] = []
        query = q.strip().lower()
        include_dirs = kind in {"directory", "all"}
        include_files = kind in {"file", "all"}

        async for entry in dir_path.iterdir():
            name = entry.name
            if name.startswith("."):
                continue
            if query and query not in name.lower():
                continue
            entry_resolved = await entry.resolve()
            if include_dirs and await entry.is_dir():
                entries.append({"name": name, "path": _posix(entry_resolved), "kind": "directory"})
            elif include_files and await entry.is_file():
                try:
                    size = (await entry.stat()).st_size
                except OSError:
                    size = 0
                entries.append({"name": name, "path": _posix(entry_resolved), "kind": "file", "size": size})

        entries.sort(key=lambda e: (0 if e["kind"] == "directory" else 1, e["name"].lower()))

        return {
            "path": _posix(resolved),
            "parent": _posix(parent),
            "segments": WorkspaceManager._path_segments(_posix(resolved)),
            "entries": entries,
        }

    async def read_file(self, path: str, *, root: str = "") -> dict[str, str]:
        """Read a file as base64 for deliverable preview (spa-v2 history reopen).

        When ``root`` is set, the resolved path must stay under that directory.
        """
        raw = path.strip()
        if not raw:
            raise ValueError("path is required")
        file_path = anyio.Path(raw)
        if not await file_path.exists():
            raise FileNotFoundError(f"Path not found: {raw!r}")
        if not await file_path.is_file():
            raise IsADirectoryError(f"Not a file: {raw!r}")
        resolved = await file_path.resolve()
        if root.strip():
            root_resolved = await anyio.Path(root.strip()).resolve()
            root_s = _norm_fs(str(root_resolved))
            file_s = _norm_fs(str(resolved))
            if not (file_s == root_s or file_s.startswith(root_s + os.sep)):
                raise PermissionError(f"Path outside workspace root: {raw!r}")
        data = await resolved.read_bytes()
        name = resolved.name
        logger.debug(f"Read workspace file {str(resolved)!r} ({len(data)} bytes)")
        return {"name": name, "data": base64.b64encode(data).decode(), "path": _posix(resolved)}
