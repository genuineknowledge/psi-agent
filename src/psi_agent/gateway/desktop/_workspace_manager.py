from __future__ import annotations

import base64
import contextlib
import ctypes
import io
import json
import os
import sys
import zipfile
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


# Global personal skill layer ~/.agent/skills. A module constant (not expanduser at
# each call) so tests can monkeypatch it to a tmp_path, never touching the real home.
# Mirrors the agent package's _GLOBAL_AGENT_SKILLS_DIR -- keep the literal in sync.
# gateway cannot import the agent package, so list_skills below is a SECOND merge
# implementation; its consistency with _build_skills_index is guarded by
# tests/psi_agent/gateway/test_workspace_skills.py.
_GLOBAL_SKILLS_DIR = os.path.expanduser("~/.agent/skills")

# Tombstone file for user-disabled OFFICIAL skills (B). Lives in ~/.agent (the
# parent of the global skills dir) -- the SAME file agents/desktop reads
# (_GLOBAL_AGENT_HOME/skill-tombstones.json), format {"disabled": [names]}.
# gateway cannot import the agent package, so this is a second reader/writer;
# the drift is guarded by test_tombstone_consistent_agent_gateway (G6).
_SKILL_TOMBSTONES_FILE = "skill-tombstones.json"


def _tombstone_path() -> anyio.Path:
    """~/.agent/skill-tombstones.json -- the sibling of the global skills dir."""
    return anyio.Path(_GLOBAL_SKILLS_DIR).parent / _SKILL_TOMBSTONES_FILE


async def _read_tombstones() -> set[str]:
    """Read the disabled-official-skill list. Fail-open (empty on absent/malformed).

    Mirrors agents/desktop _read_skill_tombstones -- keep the format in sync.
    """
    path = _tombstone_path()
    with contextlib.suppress(OSError, json.JSONDecodeError, AttributeError, TypeError):
        if await path.exists():
            data = json.loads(await path.read_text(encoding="utf-8"))
            disabled = data.get("disabled", [])
            if isinstance(disabled, list):
                return {str(n) for n in disabled}
    return set()


async def _write_tombstones(names: set[str]) -> None:
    """Write the disabled list back (sorted for stable diffs); creates ~/.agent."""
    path = _tombstone_path()
    await path.parent.mkdir(parents=True, exist_ok=True)
    await path.write_text(json.dumps({"disabled": sorted(names)}), encoding="utf-8")


def _parse_skill_frontmatter(content: str) -> dict[str, str]:
    """Parse leading --- frontmatter into a dict (mirrors system.py:561-577)."""
    info: dict[str, str] = {}
    if not content.startswith("---"):
        return info
    end = content.find("\n---", 3)
    if end == -1:
        return info
    for line in content[3:end].splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            info[key.strip()] = val.strip().strip('"').strip("'")
    return info


def _first_body_line(content: str) -> str:
    """First non-empty body line after frontmatter, '#' stripped (description fallback)."""
    body = content
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            body = content[end + 4 :]
    for line in body.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return stripped
    return ""


async def _rmtree_anyio(path: anyio.Path) -> None:
    """Recursively delete a directory tree with anyio.

    gateway AGENTS.md forbids sync os.unlink / shutil, and anyio has no rmtree,
    so walk the tree: unlink files, rmdir empty dirs bottom-up.
    """
    if await path.is_dir():
        async for child in path.iterdir():
            await _rmtree_anyio(child)
        await path.rmdir()
    else:
        await path.unlink()


def _zip_skill_dir(dir_path: str, arc_root: str) -> bytes:
    """Zip dir_path's file tree under arc_root/ (sync; run via anyio.to_thread).

    zipfile is blocking IO, so the whole walk runs in a worker thread (the
    gateway/server.py precedent). Entry names use POSIX separators so the archive
    is portable across platforms.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, _dirnames, filenames in os.walk(dir_path):
            for fn in sorted(filenames):
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, dir_path).replace(os.sep, "/")
                zf.write(full, f"{arc_root}/{rel}")
    return buf.getvalue()


def _import_skill_sync(zip_bytes: bytes, skills_dir: str) -> dict[str, Any]:
    """Validate + extract a shared skill zip into skills_dir (sync; via to_thread).

    Two guards run BEFORE anything is written:
    1. zip slip -- every entry's resolved dest must stay inside skills_dir.
    2. single top-level dir -- the zip must carry exactly one skill dir <name>/.
    Then reject an existing <name> (decision 1: never silently overwrite the
    user's skill). Raises ValueError / FileExistsError.
    """
    base = Path(skills_dir)
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names = [n for n in zf.namelist() if n.strip()]
        if not names:
            raise ValueError("Empty skill zip")
        base_resolved = _norm_fs(str(base.resolve()))
        for n in names:
            dest_s = _norm_fs(str((base / n).resolve()))
            if not dest_s.startswith(base_resolved + os.sep):
                raise ValueError(f"Zip entry escapes the skills dir: {n!r}")
        top = {n.split("/", 1)[0] for n in names}
        if len(top) != 1:
            raise ValueError(f"Zip must carry a single top-level skill dir, got {sorted(top)}")
        skill_name = next(iter(top))
        if (base / skill_name).exists():
            raise FileExistsError(f"Skill already exists: {skill_name!r}")
        base.mkdir(parents=True, exist_ok=True)
        zf.extractall(base)
    return {"name": skill_name, "ok": True}


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

    async def reveal(self, path: str) -> dict[str, Any]:
        """Open the OS file manager and select ``path`` (deliverable 「在文件夹中显示」).

        Local Gateway trust model — same as ``browse`` / ``read_file`` without root
        gate: SPA already holds the ``[SEND:]`` path from this machine's Session.
        """
        raw = path.strip()
        if not raw:
            raise ValueError("path is required")
        target = anyio.Path(raw)
        if not await target.exists():
            raise FileNotFoundError(f"Path not found: {raw!r}")
        resolved = await target.resolve()
        resolved_s = str(resolved)
        logger.info(f"Revealing path in file manager: {resolved_s!r}")

        if sys.platform == "win32":
            # explorer exit codes are unreliable; /select,<path> is one argv.
            await anyio.run_process(
                ["explorer", f"/select,{resolved_s}"],
                check=False,
            )
        elif sys.platform == "darwin":
            await anyio.run_process(["open", "-R", resolved_s], check=True)
        else:
            folder = resolved_s if await resolved.is_dir() else str(resolved.parent)
            await anyio.run_process(["xdg-open", folder], check=False)

        return {"path": _posix(resolved), "ok": True}

    async def list_skills(self, agent_dir: str) -> list[dict[str, Any]]:
        """Enumerate skills across official (agent_dir/skills) + global (~/.agent/skills).

        The global personal layer wins on name conflict (official scanned first,
        global overrides) -- mirrors agents/desktop _build_skills_index priority.
        Returns [{name, description, category, source, path}] sorted by dir name.
        A missing layer is normal (skipped); an empty agent_dir scans only global.
        """
        layers: list[tuple[anyio.Path, str]] = []
        if agent_dir.strip():
            layers.append((anyio.Path(agent_dir) / "skills", "official"))
        layers.append((anyio.Path(_GLOBAL_SKILLS_DIR), "global"))

        merged: dict[str, dict[str, Any]] = {}
        for layer_dir, source in layers:  # official first, global last -> global wins
            if not await layer_dir.exists():
                continue
            async for entry in layer_dir.iterdir():
                if not await entry.is_dir() or entry.name.startswith("."):
                    continue
                skill_md = entry / "SKILL.md"
                if not await skill_md.exists():
                    continue
                raw = await skill_md.read_text(encoding="utf-8", errors="replace")
                fm = _parse_skill_frontmatter(raw)
                merged[entry.name] = {
                    "name": fm.get("name") or entry.name,
                    "description": fm.get("description") or _first_body_line(raw),
                    "category": fm.get("category") or "general",
                    "source": source,
                    "path": _posix(await skill_md.resolve()),
                }
        # Tombstone flag (B): mark official-won skills the user disabled. Matched
        # by the DIR NAME (merged key), not the frontmatter name, to agree with
        # the agent index filter. Only source=="official" is flagged -- a
        # global-won entry (user override / personal) is never tombstoned even if
        # its name is listed (mirrors agent B2/B4).
        tombstones = await _read_tombstones()
        for dir_name, entry in merged.items():
            entry["tombstoned"] = entry["source"] == "official" and dir_name in tombstones
        return [merged[key] for key in sorted(merged)]

    async def delete_skill(self, name: str) -> dict[str, Any]:
        """Delete a personal (global-layer) skill dir ~/.agent/skills/<name>.

        Official skills are NEVER deleted here (read-only in production, restored
        on upgrade); hiding them is the tombstone mechanism (a later stage). The
        name is validated and the resolved target confined to the global layer, so
        a traversal or an escaping symlink cannot reach anything else. Raises
        ValueError (bad name / escape) or FileNotFoundError (absent).
        """
        clean = (name or "").strip()
        if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean or "\x00" in clean:
            raise ValueError(f"Invalid skill name: {name!r}")
        base = anyio.Path(_GLOBAL_SKILLS_DIR)
        target = base / clean
        if not await target.exists():
            raise FileNotFoundError(f"Skill not found: {name!r}")
        # Confine to the global layer even if the name is a symlink pointing out.
        base_s = _norm_fs(str(await base.resolve()))
        target_s = _norm_fs(str(await target.resolve()))
        if not (target_s == base_s or target_s.startswith(base_s + os.sep)):
            raise ValueError(f"Skill path escapes the global layer: {name!r}")
        await _rmtree_anyio(target)
        logger.info(f"Deleted personal skill {target_s!r}")
        return {"name": clean, "ok": True}

    async def disable_skill(self, name: str) -> dict[str, Any]:
        """Disable an OFFICIAL skill by tombstone (B): add its dir name to
        ~/.agent/skill-tombstones.json so the agent index hides it.

        The official package is read-only in production and restored on upgrade,
        so it can only be hidden, never deleted (that is delete_skill's job for
        the personal layer). The name is validated like delete_skill (rejects
        separators / .. / empty) so junk cannot enter the tombstone list.
        """
        clean = (name or "").strip()
        if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean or "\x00" in clean:
            raise ValueError(f"Invalid skill name: {name!r}")
        tombstones = await _read_tombstones()
        tombstones.add(clean)
        await _write_tombstones(tombstones)
        logger.info(f"Disabled official skill {clean!r} (tombstone)")
        return {"name": clean, "disabled": True}

    async def enable_skill(self, name: str) -> dict[str, Any]:
        """Re-enable a tombstoned OFFICIAL skill (B): remove its name from the
        tombstone list. Name validated like disable_skill. Idempotent -- enabling
        a name that is not listed is a harmless no-op.
        """
        clean = (name or "").strip()
        if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean or "\x00" in clean:
            raise ValueError(f"Invalid skill name: {name!r}")
        tombstones = await _read_tombstones()
        tombstones.discard(clean)
        await _write_tombstones(tombstones)
        logger.info(f"Enabled official skill {clean!r} (tombstone removed)")
        return {"name": clean, "disabled": False}

    async def export_skill(self, name: str, agent_dir: str) -> bytes:
        """Zip a skill directory (winning layer) for sharing.

        Official skills can be exported too (decision 2 -- read-only, safe). The
        name is validated like delete_skill. The located dir is the global personal
        layer if present, else the official layer (agent_dir/skills). zipfile is
        blocking IO, so packing runs in a worker thread.
        """
        clean = (name or "").strip()
        if not clean or clean in {".", ".."} or "/" in clean or "\\" in clean or "\x00" in clean:
            raise ValueError(f"Invalid skill name: {name!r}")
        target = anyio.Path(_GLOBAL_SKILLS_DIR) / clean  # global personal layer wins
        if not await target.exists() and agent_dir.strip():
            target = anyio.Path(agent_dir) / "skills" / clean  # official fallback
        if not await target.exists():
            raise FileNotFoundError(f"Skill not found: {name!r}")
        return await anyio.to_thread.run_sync(_zip_skill_dir, str(target), clean)  # ty: ignore

    async def import_skill(self, zip_bytes: bytes) -> dict[str, Any]:
        """Import a shared skill zip into ~/.agent/skills.

        Rejects zip slip (any entry escaping the skills dir), a multi-dir zip, and
        an existing dir (decision 1: never silently overwrite). Extraction runs in
        a worker thread; a malformed zip surfaces as ValueError.
        """
        try:
            result = await anyio.to_thread.run_sync(_import_skill_sync, zip_bytes, _GLOBAL_SKILLS_DIR)  # ty: ignore
        except zipfile.BadZipFile as e:
            raise ValueError(f"Invalid zip file: {e}") from e
        logger.info(f"Imported shared skill {result.get('name')!r}")
        return result
