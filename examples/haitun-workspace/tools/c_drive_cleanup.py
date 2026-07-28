"""Scan and clean safe, reclaimable files from the Windows C drive."""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from typing import Any

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

_impl = importlib.import_module("_c_drive_cleanup_impl")


async def c_drive_cleanup(
    action: str,
    scan_approved: bool = False,
    cleanup_approved: bool = False,
    categories: list[str] | None = None,
    min_age_days: int = 7,
    include_large_files: bool = True,
    include_recycle_bin: bool = False,
    empty_recycle_bin: bool = False,
) -> str:
    """Safely scan or clean reclaimable files on the Windows C drive.

    The first scan in a Session requires user confirmation. Once confirmed,
    later scans in that Session may start directly. Cleaning is limited to
    unchanged files in the latest scan and requires affirmative user
    confirmation. Large files outside known temporary/cache locations are
    report-only.

    Args:
        action: One of "scan", "status", or "clean".
        scan_approved: True after the user confirms the first scan in this Session.
        cleanup_approved: True only after the user reviews and approves the plan.
        categories: Optional safe categories: user_temp, windows_temp,
            crash_dumps, error_reports, shader_cache, thumbnail_cache.
        min_age_days: Minimum age; category-specific safety floors still apply.
        include_large_files: Report large user files without making them deletable.
        include_recycle_bin: Include emptying C:\\$Recycle.Bin in the approval plan.
        empty_recycle_bin: Must match the approved plan at clean time.

    Returns:
        JSON containing the scan plan or verified cleanup result.
    """
    normalized = action.strip().lower()
    result: dict[str, Any]
    if normalized == "scan":
        result = await _impl.scan_impl(
            scan_approved=scan_approved,
            categories=categories,
            min_age_days=min_age_days,
            include_large_files=include_large_files,
            include_recycle_bin=include_recycle_bin,
        )
    elif normalized == "status":
        result = await _impl.status_impl()
    elif normalized == "clean":
        result = await _impl.clean_impl(
            cleanup_approved=cleanup_approved,
            empty_recycle_bin=empty_recycle_bin,
        )
    else:
        result = {
            "ok": False,
            "error": "Unknown action. Use scan, status, or clean.",
        }
    return json.dumps(result, ensure_ascii=False)
