"""Safety-critical implementation for the Windows C-drive cleanup tool."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import stat
import tempfile
import time
from collections.abc import Iterable
from contextlib import suppress
from pathlib import Path
from typing import Any

import anyio
from anyio import to_thread

from psi_agent.session.runtime_context import get_session_id

MAX_CANDIDATES = 100_000
DEFAULT_LARGE_FILE_BYTES = 1024**3
MAX_LARGE_FILES = 20

CATEGORY_MIN_AGE_DAYS: dict[str, int] = {
    "user_temp": 7,
    "windows_temp": 7,
    "crash_dumps": 14,
    "error_reports": 14,
    "shader_cache": 7,
    "thumbnail_cache": 7,
}


def _error(message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, **extra}


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _same_volume(path: Path, drive_root: Path) -> bool:
    return os.path.normcase(path.drive) == os.path.normcase(drive_root.drive)


def _category_roots(drive_root: Path) -> dict[str, list[Path]]:
    user_profile = Path(os.environ.get("USERPROFILE", ""))
    local_appdata = Path(os.environ.get("LOCALAPPDATA", ""))
    program_data = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    windows_dir = Path(os.environ.get("WINDIR", str(drive_root / "Windows")))
    temp_dir = Path(tempfile.gettempdir())
    raw: dict[str, list[Path]] = {
        "user_temp": [temp_dir],
        "windows_temp": [windows_dir / "Temp"],
        "crash_dumps": [local_appdata / "CrashDumps"],
        "error_reports": [
            program_data / "Microsoft" / "Windows" / "WER" / "ReportArchive",
            program_data / "Microsoft" / "Windows" / "WER" / "ReportQueue",
            local_appdata / "Microsoft" / "Windows" / "WER",
        ],
        "shader_cache": [local_appdata / "D3DSCache"],
        "thumbnail_cache": [local_appdata / "Microsoft" / "Windows" / "Explorer"],
    }
    roots: dict[str, list[Path]] = {}
    for category, paths in raw.items():
        accepted: list[Path] = []
        for path in paths:
            if not str(path) or not path.is_absolute() or not _same_volume(path, drive_root):
                continue
            if user_profile and path == user_profile:
                continue
            accepted.append(path)
        roots[category] = accepted
    return roots


def _is_reparse_point(stat_result: os.stat_result) -> bool:
    attrs = getattr(stat_result, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))


def _normalized_scan_path(path: Path) -> str:
    """Normalize lexically for scan-time comparisons without filesystem resolution."""
    return os.path.normcase(os.path.abspath(path))


def _is_excluded_scan_path(path: str, excluded_roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(root + os.sep) for root in excluded_roots)


def _iter_regular_files(root: Path, *, excluded_roots: tuple[Path, ...] = ()) -> Iterable[tuple[Path, os.stat_result]]:
    """Walk once per entry without following reparse points; tolerate access errors."""
    try:
        root_stat = root.stat(follow_symlinks=False)
        if stat.S_ISLNK(root_stat.st_mode) or _is_reparse_point(root_stat):
            return
    except OSError:
        return
    excluded = tuple(_normalized_scan_path(path) for path in excluded_roots)
    if _is_excluded_scan_path(_normalized_scan_path(root), excluded):
        return
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        path = Path(entry.path)
                        normalized = _normalized_scan_path(path)
                        if _is_excluded_scan_path(normalized, excluded):
                            continue
                        entry_stat = entry.stat(follow_symlinks=False)
                        if stat.S_ISLNK(entry_stat.st_mode) or _is_reparse_point(entry_stat):
                            continue
                        if stat.S_ISDIR(entry_stat.st_mode):
                            stack.append(path)
                        elif stat.S_ISREG(entry_stat.st_mode):
                            yield path, entry_stat
                    except OSError:
                        continue
        except OSError:
            continue


def _scan_sync(
    *,
    drive_root: Path,
    categories: list[str],
    min_age_days: int,
    include_large_files: bool,
    include_recycle_bin: bool,
    large_file_bytes: int,
    excluded_roots: tuple[Path, ...] = (),
) -> dict[str, Any]:
    now = time.time()
    category_roots = _category_roots(drive_root)
    items: list[dict[str, Any]] = []
    seen_candidate_paths: set[str] = set()
    summaries: dict[str, dict[str, Any]] = {}
    truncated = False
    for category in categories:
        roots = category_roots[category]
        count = 0
        size = 0
        cutoff = now - max(min_age_days, CATEGORY_MIN_AGE_DAYS[category]) * 86400
        for root in roots:
            if not root.exists() or not root.is_dir():
                continue
            for path, _scan_stat in _iter_regular_files(root, excluded_roots=excluded_roots):
                if len(items) >= MAX_CANDIDATES:
                    truncated = True
                    break
                if category == "thumbnail_cache" and not path.name.lower().startswith("thumbcache_"):
                    continue
                normalized_path = _normalized_scan_path(path)
                if normalized_path in seen_candidate_paths:
                    continue
                try:
                    stat_result = path.stat(follow_symlinks=False)
                except OSError:
                    continue
                if stat_result.st_mtime > cutoff:
                    continue
                seen_candidate_paths.add(normalized_path)
                items.append(
                    {
                        "path": str(path),
                        "category": category,
                        "size": stat_result.st_size,
                        "mtime_ns": stat_result.st_mtime_ns,
                        "device": stat_result.st_dev,
                        "inode": stat_result.st_ino,
                    }
                )
                count += 1
                size += stat_result.st_size
            if truncated:
                break
        summaries[category] = {
            "files": count,
            "bytes": size,
            "roots": [str(root) for root in roots],
        }
        if truncated:
            break

    large_files: list[dict[str, Any]] = []
    user_profile = Path(os.environ.get("USERPROFILE", ""))
    if (
        include_large_files
        and user_profile.is_absolute()
        and _same_volume(user_profile, drive_root)
        and user_profile.exists()
    ):
        large_file_exclusions = (*excluded_roots, user_profile / "AppData")
        for path, stat_result in _iter_regular_files(user_profile, excluded_roots=large_file_exclusions):
            if stat_result.st_size >= large_file_bytes:
                large_files.append(
                    {
                        "path": str(path),
                        "bytes": stat_result.st_size,
                        "mtime_ns": stat_result.st_mtime_ns,
                    }
                )
        large_files.sort(key=lambda item: item["bytes"], reverse=True)
        large_files = large_files[:MAX_LARGE_FILES]

    usage = shutil.disk_usage(drive_root)
    recycle_bin = {"included": include_recycle_bin, "files": 0, "bytes": 0}
    if include_recycle_bin:
        for _path, stat_result in _iter_regular_files(drive_root / "$Recycle.Bin", excluded_roots=excluded_roots):
            recycle_bin["files"] += 1
            recycle_bin["bytes"] += stat_result.st_size
    return {
        "items": items,
        "categories": summaries,
        "candidate_files": len(items),
        "candidate_bytes": sum(item["size"] for item in items),
        "large_files_report_only": large_files,
        "recycle_bin": recycle_bin,
        "truncated": truncated,
        "drive": {
            "root": str(drive_root),
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
        },
    }


def _plan_dir(path_override: str = "") -> Path:
    return Path(path_override) if path_override else Path(tempfile.gettempdir()) / "haitun-c-drive-cleanup-plans"


def _current_session_id() -> str:
    return get_session_id()


def _plan_path(path_override: str = "") -> Path:
    session_id = _current_session_id() or "default"
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    return _plan_dir(path_override) / f"current-{key}.json"


def _scan_consent_path(path_override: str = "") -> Path:
    session_id = _current_session_id() or "default"
    key = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    return _plan_dir(path_override) / f"scan-consent-{key}"


def _has_scan_consent(path_override: str = "") -> bool:
    return _scan_consent_path(path_override).is_file()


def _record_scan_consent(path_override: str = "") -> None:
    directory = _plan_dir(path_override)
    directory.mkdir(parents=True, exist_ok=True)
    path = _scan_consent_path(path_override)
    temporary = path.with_suffix(".tmp")
    temporary.write_text("confirmed\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_plan(plan: dict[str, Any], path_override: str = "") -> None:
    directory = _plan_dir(path_override)
    directory.mkdir(parents=True, exist_ok=True)
    path = _plan_path(path_override)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _read_plan(path_override: str = "") -> tuple[dict[str, Any] | None, str]:
    path = _plan_path(path_override)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "Cleanup plan not found."
    except OSError, json.JSONDecodeError:
        return None, "Cleanup plan cannot be read."
    if not _is_valid_plan(plan):
        return None, "Cleanup plan is invalid."
    return plan, ""


def _is_valid_plan(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    drive = value.get("drive")
    items = value.get("items")
    if not isinstance(drive, dict):
        return False
    drive_root = drive.get("root")
    if not isinstance(drive_root, str) or not Path(drive_root).is_absolute():
        return False
    if not isinstance(value.get("include_recycle_bin"), bool) or not isinstance(items, list):
        return False
    required_ints = ("size", "mtime_ns", "device", "inode")
    for item in items:
        if not isinstance(item, dict):
            return False
        item_path = item.get("path")
        if not isinstance(item_path, str) or not Path(item_path).is_absolute():
            return False
        if item.get("category") not in CATEGORY_MIN_AGE_DAYS:
            return False
        for key in required_ints:
            numeric_value = item.get(key)
            if not isinstance(numeric_value, int) or isinstance(numeric_value, bool):
                return False
            if key != "mtime_ns" and numeric_value < 0:
                return False
    return True


async def scan_impl(
    *,
    scan_approved: bool = False,
    categories: list[str] | None = None,
    min_age_days: int = 7,
    include_large_files: bool = True,
    include_recycle_bin: bool = False,
    large_file_bytes: int = DEFAULT_LARGE_FILE_BYTES,
    root_override: str = "",
    plan_dir_override: str = "",
) -> dict[str, Any]:
    consent_exists = await to_thread.run_sync(lambda: _has_scan_consent(plan_dir_override))
    if not consent_exists and not scan_approved:
        return {
            "ok": False,
            "requires_scan_confirmation": True,
            "error": "The first C-drive scan in this Session requires user confirmation.",
            "instruction": "Ask for confirmation before retrying with scan_approved=true.",
        }
    if os.name != "nt" and not root_override:
        return _error("C-drive cleanup is available only on Windows.")
    if min_age_days < 1:
        return _error("min_age_days must be at least 1.")
    if large_file_bytes < 100 * 1024**2:
        return _error("large_file_bytes must be at least 100 MiB.")
    requested = categories or list(CATEGORY_MIN_AGE_DAYS)
    unknown = sorted(set(requested) - set(CATEGORY_MIN_AGE_DAYS))
    if unknown:
        return _error("Unknown cleanup categories.", unknown_categories=unknown)

    drive_root = await to_thread.run_sync(
        lambda: Path(root_override or f"{os.environ.get('SYSTEMDRIVE', 'C:')}\\").resolve()
    )
    scanned = await to_thread.run_sync(
        lambda: _scan_sync(
            drive_root=drive_root,
            categories=requested,
            min_age_days=min_age_days,
            include_large_files=include_large_files,
            include_recycle_bin=include_recycle_bin,
            large_file_bytes=large_file_bytes,
            excluded_roots=(_plan_dir(plan_dir_override),),
        )
    )
    plan = {
        **scanned,
        "ok": True,
        "created_at": time.time(),
        "include_recycle_bin": include_recycle_bin,
    }
    await to_thread.run_sync(lambda: _write_plan(plan, plan_dir_override))
    if not consent_exists:
        await to_thread.run_sync(lambda: _record_scan_consent(plan_dir_override))
    return {key: value for key, value in plan.items() if key != "items"}


def _validate_item(item: dict[str, Any], allowed_roots: dict[str, list[Path]], drive_root: Path) -> Path | None:
    try:
        path = Path(str(item["path"]))
        resolved = path.resolve(strict=True)
        roots = _resolved_safe_roots(allowed_roots.get(str(item["category"]), []))
        if not _same_volume(resolved, drive_root) or not any(_is_relative_to(resolved, root) for root in roots):
            return None
        if path.is_symlink() or not path.is_file():
            return None
        result = path.stat(follow_symlinks=False)
        if _is_reparse_point(result):
            return None
        expected = (int(item["size"]), int(item["mtime_ns"]), int(item["device"]), int(item["inode"]))
        actual = (result.st_size, result.st_mtime_ns, result.st_dev, result.st_ino)
        return path if actual == expected else None
    except KeyError, OSError, TypeError, ValueError:
        return None


def _resolved_safe_roots(roots: list[Path]) -> list[Path]:
    safe: list[Path] = []
    for root in roots:
        try:
            if root.is_symlink() or not root.is_dir():
                continue
            if _is_reparse_point(root.stat(follow_symlinks=False)):
                continue
            safe.append(root.resolve(strict=True))
        except OSError:
            continue
    return safe


def _empty_recycle_bin() -> tuple[bool, str]:
    if os.name != "nt":
        return False, "Recycle Bin cleanup is available only on Windows."
    result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, "C:\\", 0x1 | 0x2 | 0x4)
    return (True, "") if result == 0 else (False, f"SHEmptyRecycleBinW failed with HRESULT {result}.")


def _clean_sync(plan: dict[str, Any], *, drive_root: Path, empty_recycle_bin: bool) -> dict[str, Any]:
    before = shutil.disk_usage(drive_root)
    allowed_roots = _category_roots(drive_root)
    deleted_files = 0
    deleted_bytes = 0
    skipped = 0
    failures: list[dict[str, str]] = []
    for item in plan.get("items", []):
        if not isinstance(item, dict):
            skipped += 1
            continue
        path = _validate_item(item, allowed_roots, drive_root)
        if path is None:
            skipped += 1
            continue
        try:
            size = path.stat(follow_symlinks=False).st_size
            path.unlink()
            deleted_files += 1
            deleted_bytes += size
        except OSError as exc:
            failures.append({"path": str(path), "error": str(exc)})
    recycle_bin_emptied = False
    recycle_bin_error = ""
    if empty_recycle_bin:
        recycle_bin_emptied, recycle_bin_error = _empty_recycle_bin()
    after = shutil.disk_usage(drive_root)
    return {
        "ok": True,
        "deleted_files": deleted_files,
        "deleted_bytes": deleted_bytes,
        "skipped_changed_or_unsafe": skipped,
        "failed_count": len(failures),
        "failures": failures[:50],
        "recycle_bin_emptied": recycle_bin_emptied,
        "recycle_bin_error": recycle_bin_error,
        "free_bytes_before": before.free,
        "free_bytes_after": after.free,
        "measured_free_bytes_delta": after.free - before.free,
    }


async def clean_impl(
    *,
    cleanup_approved: bool,
    empty_recycle_bin: bool = False,
    root_override: str = "",
    plan_dir_override: str = "",
) -> dict[str, Any]:
    if not cleanup_approved:
        return _error("Cleanup requires the user's explicit approval after reviewing the scan.")
    plan, error = await to_thread.run_sync(lambda: _read_plan(plan_dir_override))
    if plan is None:
        return _error(error)
    if empty_recycle_bin != bool(plan.get("include_recycle_bin")):
        return _error("Recycle Bin choice does not match the approved scan plan.")
    stored_drive_root = await to_thread.run_sync(lambda: Path(plan["drive"]["root"]).resolve())
    if root_override:
        drive_root = await to_thread.run_sync(lambda: Path(root_override).resolve())
    else:
        drive_root = await to_thread.run_sync(lambda: Path(f"{os.environ.get('SYSTEMDRIVE', 'C:')}\\").resolve())
        if stored_drive_root != drive_root:
            return _error("Cleanup snapshot drive does not match the current Windows system drive.")
    result = await to_thread.run_sync(
        lambda: _clean_sync(plan, drive_root=drive_root, empty_recycle_bin=empty_recycle_bin)
    )
    with suppress(OSError):
        await anyio.Path(_plan_path(plan_dir_override)).unlink()
    return result


async def status_impl(*, plan_dir_override: str = "") -> dict[str, Any]:
    plan, error = await to_thread.run_sync(lambda: _read_plan(plan_dir_override))
    return _error(error) if plan is None else {key: value for key, value in plan.items() if key != "items"}
