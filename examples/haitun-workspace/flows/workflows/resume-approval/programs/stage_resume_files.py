"""Stage uploaded resumes into a SHA-addressed workspace inbox."""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import sys
import tempfile
from typing import Any

_SUPPORTED_SUFFIXES = frozenset({".docx", ".md", ".pdf", ".txt"})
# feishu_drive_upload uses the single-request endpoint and rejects larger files.
_MAX_SOURCE_BYTES = 20 * 1024 * 1024
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")
_WINDOWS_DOWNLOADS_FOLDER_ID = "{374DE290-123F-4565-9164-39C4925E467B}"


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _user_downloads_dir() -> str:
    """Resolve the user Downloads directory using only the Python standard library."""
    home = os.path.expanduser("~")
    if os.name == "nt":
        try:
            winreg = importlib.import_module("winreg")

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, _WINDOWS_DOWNLOADS_FOLDER_ID)
            if isinstance(value, str) and value.strip():
                return os.path.expandvars(value.strip())
        except OSError:
            pass
    else:
        config_root = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
        user_dirs_path = os.path.join(config_root, "user-dirs.dirs")
        try:
            with open(user_dirs_path, encoding="utf-8") as user_dirs:
                for line in user_dirs:
                    match = re.fullmatch(r'\s*XDG_DOWNLOAD_DIR\s*=\s*"([^"]*)"\s*', line)
                    if match:
                        value = match.group(1).replace("$HOME", home)
                        if value.strip():
                            return os.path.expandvars(value.strip())
        except OSError:
            pass
    return os.path.join(home, "Downloads")


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("Program stdin must contain an inputs object")
    return inputs


def _descriptor(value: Any, *, index: int) -> tuple[str, str]:
    if isinstance(value, str):
        value = {"path": value}
    if not isinstance(value, dict):
        raise TypeError(f"resume_files[{index}] must be a path string or object")
    path = value.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"resume_files[{index}].path must be non-empty text")
    name = value.get("name", os.path.basename(path))
    if not isinstance(name, str) or not name.strip():
        name = os.path.basename(path)
    return path, name


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_batch_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("batch_id must be non-empty text")
    normalized = _SAFE_COMPONENT.sub("-", value.strip()).strip(".-_")
    if not normalized:
        raise ValueError("batch_id contains no safe filename characters")
    return normalized[:100]


def _resolve_source(
    path: str,
    workspace_root: str,
    allowed_external_root: str,
) -> str:
    candidate = path if os.path.isabs(path) else os.path.join(workspace_root, path)
    source = os.path.realpath(candidate)
    if not (_inside(source, workspace_root) or _inside(source, allowed_external_root)):
        raise PermissionError(
            "Resume source must be inside the current workspace or the Feishu Downloads/.psi directory"
        )
    if os.path.islink(candidate):
        raise PermissionError("Resume source must not be a symbolic link")
    if not os.path.isfile(source):
        raise FileNotFoundError(f"Resume source is not a regular file: {path}")
    size = os.path.getsize(source)
    if size <= 0:
        raise ValueError(f"Resume source is empty: {path}")
    if size > _MAX_SOURCE_BYTES:
        raise ValueError(f"Resume source exceeds {_MAX_SOURCE_BYTES} bytes: {path}")
    return source


def _atomic_copy(source: str, target: str) -> None:
    os.makedirs(os.path.dirname(target), exist_ok=True)
    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(delete=False, dir=os.path.dirname(target)) as temporary:
            temporary_path = temporary.name
            with open(source, "rb") as source_file:
                shutil.copyfileobj(source_file, temporary)
        os.replace(temporary_path, target)
    finally:
        if temporary_path and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def run(
    inputs: dict[str, Any],
    workspace_root: str | None = None,
    allowed_external_root: str | None = None,
) -> list[dict[str, Any]]:
    """Copy supported resumes into the workspace and deduplicate by content hash."""
    resolved_workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    resolved_external = os.path.realpath(
        allowed_external_root if allowed_external_root is not None else os.path.join(_user_downloads_dir(), ".psi")
    )
    values = inputs.get("resume_files")
    if not isinstance(values, list) or not values:
        raise ValueError("resume_files must be a non-empty list")
    batch_id = _safe_batch_id(inputs.get("batch_id"))
    inbox_root = os.path.realpath(os.path.join(resolved_workspace, ".psi", "resume-approval", "inbox", batch_id))
    if not _inside(inbox_root, resolved_workspace):
        raise PermissionError("Resolved inbox must stay inside the current workspace")

    resolved_sources: list[tuple[int, int, str, str]] = []
    for index, value in enumerate(values):
        requested_path, requested_name = _descriptor(value, index=index)
        source = _resolve_source(requested_path, resolved_workspace, resolved_external)
        source_priority = 0 if _inside(source, resolved_external) else 1
        resolved_sources.append((source_priority, index, source, requested_name))

    staged: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for _, _, source, requested_name in sorted(resolved_sources):
        suffix = os.path.splitext(source)[1].lower()
        if suffix not in _SUPPORTED_SUFFIXES:
            supported = ", ".join(sorted(_SUPPORTED_SUFFIXES))
            raise ValueError(f"Unsupported resume format {suffix!r}; expected one of {supported}")
        digest = _sha256(source)
        if digest in seen_hashes:
            continue
        seen_hashes.add(digest)
        # The hash directory is the stable source identity.  Keep the user-supplied
        # filename only as local provenance and expose a neutral name to downstream
        # assessment Agents so contact data embedded in filenames cannot leak into
        # immutable candidate metadata.
        target = os.path.join(inbox_root, digest, f"resume{suffix}")
        if os.path.exists(target):
            if not os.path.isfile(target) or _sha256(target) != digest:
                raise RuntimeError(f"Existing staged path does not match its content hash: {target}")
        else:
            _atomic_copy(source, target)
        relative = os.path.relpath(target, resolved_workspace).replace(os.sep, "/")
        staged.append(
            {
                "path": relative,
                "name": os.path.basename(target),
                "original_name": os.path.basename(requested_name.strip()),
                "sha256": digest,
                "format": suffix,
                "size_bytes": os.path.getsize(source),
                "temporary": True,
            }
        )
    if not staged:
        raise ValueError("No unique supported resume files remain after staging")
    return staged


def _program_outputs(inputs: dict[str, Any], staged: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "staged_resume_files": staged,
        "resume_staging_manifest": {
            "schema_version": "1.0",
            "status": "complete",
            "batch_id": _safe_batch_id(inputs.get("batch_id")),
            "staged_count": len(staged),
            "source_sha256s": [item["sha256"] for item in staged],
        },
    }


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    inputs = _load_inputs()
    outputs = _program_outputs(inputs, run(inputs))
    sys.stdout.write(json.dumps(outputs, ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
