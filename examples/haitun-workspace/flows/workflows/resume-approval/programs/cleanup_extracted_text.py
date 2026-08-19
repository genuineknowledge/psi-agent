"""Delete only SHA-addressed temporary files created by this workflow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from collections.abc import Iterator
from typing import Any

_EXTRACTED_NAME = re.compile(r"^[0-9a-f]{64}\.txt$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _inside(child: str, parent: str) -> bool:
    try:
        return os.path.commonpath((child, parent)) == parent
    except ValueError:
        return False


def _load_inputs() -> dict[str, Any]:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict):
        raise TypeError("Program stdin must be a JSON object")
    inputs = payload.get("inputs")
    if not isinstance(inputs, dict):
        raise TypeError("Program stdin must contain an inputs object")
    return inputs


def _decode_json_string(value: str) -> Any:
    stripped = value.strip()
    if not stripped or stripped[0] not in "[{":
        return value
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return value


def _extracted_paths(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        decoded = _decode_json_string(value)
        if decoded is not value:
            yield from _extracted_paths(decoded)
        return
    if isinstance(value, list):
        for item in value:
            yield from _extracted_paths(item)
        return
    if not isinstance(value, dict):
        return
    extracted_path = value.get("extracted_text_path")
    if isinstance(extracted_path, str) and extracted_path:
        yield extracted_path
    for key, item in value.items():
        if key != "extracted_text_path":
            yield from _extracted_paths(item)


def _staged_sources(value: Any) -> Iterator[tuple[str, str]]:
    if isinstance(value, str):
        decoded = _decode_json_string(value)
        if decoded is not value:
            yield from _staged_sources(decoded)
        return
    if isinstance(value, list):
        for item in value:
            yield from _staged_sources(item)
        return
    if not isinstance(value, dict):
        return
    path = value.get("path")
    digest = value.get("sha256")
    if value.get("temporary") is True and isinstance(path, str) and isinstance(digest, str):
        yield path, digest
    for item in value.values():
        yield from _staged_sources(item)


def _safe_target(path: str, workspace_root: str, extracted_root: str) -> str:
    candidate = path if os.path.isabs(path) else os.path.join(workspace_root, path)
    target = os.path.realpath(candidate)
    if not _inside(target, extracted_root):
        raise PermissionError(f"Cleanup target is outside the extracted-text directory: {path}")
    if not _EXTRACTED_NAME.fullmatch(os.path.basename(target)):
        raise PermissionError(f"Cleanup target is not a SHA-addressed text file: {path}")
    if os.path.islink(candidate):
        raise PermissionError(f"Cleanup target must not be a symbolic link: {path}")
    return target


def _sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_staged_target(path: str, digest: str, workspace_root: str, inbox_root: str) -> str:
    if _SHA256.fullmatch(digest) is None:
        raise PermissionError(f"Staged source has an invalid SHA-256 value: {path}")
    candidate = path if os.path.isabs(path) else os.path.join(workspace_root, path)
    target = os.path.realpath(candidate)
    if not _inside(target, inbox_root):
        raise PermissionError(f"Staged source is outside the workflow inbox: {path}")
    if os.path.basename(os.path.dirname(target)) != digest:
        raise PermissionError(f"Staged source is not inside its SHA-addressed directory: {path}")
    if os.path.islink(candidate):
        raise PermissionError(f"Staged source must not be a symbolic link: {path}")
    return target


def run(inputs: dict[str, Any], workspace_root: str | None = None) -> dict[str, Any]:
    """Delete extracted-text paths found in the supplied Workflow inputs."""
    resolved_workspace = os.path.realpath(workspace_root if workspace_root is not None else os.getcwd())
    extracted_root = os.path.realpath(os.path.join(resolved_workspace, ".psi", "resume-approval", "extracted"))
    inbox_root = os.path.realpath(os.path.join(resolved_workspace, ".psi", "resume-approval", "inbox"))
    requested = sorted(set(_extracted_paths(inputs)))
    deleted: list[str] = []
    missing: list[str] = []
    for path in requested:
        target = _safe_target(path, resolved_workspace, extracted_root)
        relative = os.path.relpath(target, resolved_workspace).replace(os.sep, "/")
        if not os.path.exists(target):
            missing.append(relative)
            continue
        if not os.path.isfile(target):
            raise PermissionError(f"Cleanup target is not a regular file: {path}")
        os.unlink(target)
        deleted.append(relative)
    staged_requested = sorted(set(_staged_sources(inputs)))
    deleted_sources: list[str] = []
    missing_sources: list[str] = []
    for path, digest in staged_requested:
        target = _safe_staged_target(path, digest, resolved_workspace, inbox_root)
        relative = os.path.relpath(target, resolved_workspace).replace(os.sep, "/")
        if not os.path.exists(target):
            missing_sources.append(relative)
            continue
        if not os.path.isfile(target) or _sha256(target) != digest:
            raise PermissionError(f"Staged source does not match its declared SHA-256: {path}")
        os.unlink(target)
        deleted_sources.append(relative)
        sha_dir = os.path.dirname(target)
        if os.path.isdir(sha_dir) and not os.listdir(sha_dir):
            os.rmdir(sha_dir)
    return {
        "schema_version": "1.0",
        "status": "complete",
        "requested_count": len(requested),
        "deleted": deleted,
        "missing": missing,
        "staged_requested_count": len(staged_requested),
        "deleted_staged_sources": deleted_sources,
        "missing_staged_sources": missing_sources,
    }


def _program_outputs(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "cleanup_receipt": result,
        "cleanup_scope_manifest": {
            "schema_version": "1.0",
            "status": result["status"],
            "requested_count": result["requested_count"],
            "deleted_count": len(result["deleted"]),
            "missing_count": len(result["missing"]),
            "staged_requested_count": result["staged_requested_count"],
            "deleted_staged_count": len(result["deleted_staged_sources"]),
            "missing_staged_count": len(result["missing_staged_sources"]),
        },
    }


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="strict")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    result = run(_load_inputs())
    sys.stdout.write(json.dumps(_program_outputs(result), ensure_ascii=False, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
