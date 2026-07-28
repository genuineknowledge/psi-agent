"""Test C-drive cleanup using isolated temporary trees only."""

from __future__ import annotations

import importlib
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from psi_agent.session.runtime_context import runtime_scope
from psi_agent.session.tool_registry import ToolFunction

TOOLS_DIR = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

impl: Any = importlib.import_module("_c_drive_cleanup_impl")
public: Any = importlib.import_module("c_drive_cleanup")


def _old_file(path: Path, size: int = 32) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    old = time.time() - 30 * 86400
    os.utime(path, (old, old))


def _set_test_environment(monkeypatch, root: Path) -> None:
    user = root / "Users" / "tester"
    local = user / "AppData" / "Local"
    monkeypatch.setenv("USERPROFILE", str(user))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setenv("PROGRAMDATA", str(root / "ProgramData"))
    monkeypatch.setenv("WINDIR", str(root / "Windows"))
    monkeypatch.setattr(impl.tempfile, "gettempdir", lambda: str(local / "Temp"))


async def _confirmed_first_scan(**kwargs: Any) -> dict[str, Any]:
    return await impl.scan_impl(scan_approved=True, **kwargs)


async def test_only_first_scan_in_session_requires_confirmation(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    denied = await impl.scan_impl(
        root_override=str(tmp_path),
        plan_dir_override=str(plans),
    )
    assert denied["ok"] is False
    assert denied["requires_scan_confirmation"] is True

    first = await impl.scan_impl(
        scan_approved=True,
        include_large_files=False,
        root_override=str(tmp_path),
        plan_dir_override=str(plans),
    )
    assert first["ok"] is True

    later = await impl.scan_impl(
        include_large_files=False,
        root_override=str(tmp_path),
        plan_dir_override=str(plans),
    )
    assert later["ok"] is True


async def test_failed_first_scan_does_not_record_confirmation(tmp_path: Path) -> None:
    plans = tmp_path / "plans"
    failed = await impl.scan_impl(
        scan_approved=True,
        min_age_days=0,
        root_override=str(tmp_path),
        plan_dir_override=str(plans),
    )
    assert failed["ok"] is False

    denied = await impl.scan_impl(
        root_override=str(tmp_path),
        plan_dir_override=str(plans),
    )
    assert denied["requires_scan_confirmation"] is True


async def test_scan_confirmation_is_isolated_by_session(tmp_path: Path) -> None:
    plans = str(tmp_path / "plans")
    with runtime_scope(session_id="session-a"):
        accepted = await impl.scan_impl(
            scan_approved=True,
            include_large_files=False,
            root_override=str(tmp_path),
            plan_dir_override=plans,
        )
        assert accepted["ok"] is True

    with runtime_scope(session_id="session-b"):
        denied = await impl.scan_impl(
            include_large_files=False,
            root_override=str(tmp_path),
            plan_dir_override=plans,
        )
        assert denied["requires_scan_confirmation"] is True


async def test_scan_excludes_recent_and_user_documents(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "drive"
    plans = tmp_path / "plans"
    _set_test_environment(monkeypatch, root)
    old_temp = root / "Users" / "tester" / "AppData" / "Local" / "Temp" / "old.tmp"
    recent_temp = old_temp.with_name("recent.tmp")
    document = root / "Users" / "tester" / "Documents" / "keep.txt"
    _old_file(old_temp, 40)
    _old_file(recent_temp, 50)
    os.utime(recent_temp, None)
    _old_file(document, 60)

    result = await _confirmed_first_scan(
        include_large_files=False,
        root_override=str(root),
        plan_dir_override=str(plans),
    )

    assert result["ok"] is True
    assert result["candidate_files"] == 1
    assert result["candidate_bytes"] == 40
    assert "plan_id" not in result
    assert "expires_at" not in result
    plan_path = impl._plan_path(str(plans))
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert [item["path"] for item in plan["items"]] == [str(old_temp)]
    assert str(document) not in json.dumps(plan)


async def test_scan_excludes_its_own_state_directory(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "drive"
    _set_test_environment(monkeypatch, root)
    local_temp = root / "Users" / "tester" / "AppData" / "Local" / "Temp"
    plans = local_temp / "haitun-c-drive-cleanup-plans"
    state_file = plans / "old-state.json"
    cleanup_file = local_temp / "old.tmp"
    _old_file(state_file, 100)
    _old_file(cleanup_file, 40)

    result = await _confirmed_first_scan(
        include_large_files=False,
        root_override=str(root),
        plan_dir_override=str(plans),
    )

    assert result["candidate_files"] == 1
    plan = json.loads(impl._plan_path(str(plans)).read_text(encoding="utf-8"))
    assert [item["path"] for item in plan["items"]] == [str(cleanup_file)]


async def test_large_file_scan_prunes_appdata_before_traversal(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "drive"
    plans = tmp_path / "plans"
    _set_test_environment(monkeypatch, root)
    user = root / "Users" / "tester"
    appdata = user / "AppData"
    appdata.mkdir(parents=True)
    document = user / "Documents" / "large.bin"
    document.parent.mkdir(parents=True)
    document.touch()
    os.truncate(document, 100 * 1024**2)
    real_scandir = impl.os.scandir

    def guarded_scandir(path):
        if impl._normalized_scan_path(Path(path)) == impl._normalized_scan_path(appdata):
            raise AssertionError("large-file reporting must prune AppData")
        return real_scandir(path)

    monkeypatch.setattr(impl.os, "scandir", guarded_scandir)
    result = await _confirmed_first_scan(
        include_large_files=True,
        large_file_bytes=100 * 1024**2,
        root_override=str(root),
        plan_dir_override=str(plans),
    )

    assert [item["path"] for item in result["large_files_report_only"]] == [str(document)]


async def test_overlapping_category_roots_do_not_duplicate_candidates(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "drive"
    plans = tmp_path / "plans"
    _set_test_environment(monkeypatch, root)
    shared_temp = root / "Windows" / "Temp"
    monkeypatch.setattr(impl.tempfile, "gettempdir", lambda: str(shared_temp))
    candidate = shared_temp / "old.tmp"
    _old_file(candidate, 41)

    result = await _confirmed_first_scan(
        categories=["user_temp", "windows_temp"],
        include_large_files=False,
        root_override=str(root),
        plan_dir_override=str(plans),
    )

    assert result["candidate_files"] == 1
    assert result["candidate_bytes"] == 41
    plan = json.loads(impl._plan_path(str(plans)).read_text(encoding="utf-8"))
    assert [item["path"] for item in plan["items"]] == [str(candidate)]


async def test_new_scan_replaces_previous_pending_scan(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "drive"
    plans = tmp_path / "plans"
    _set_test_environment(monkeypatch, root)
    first = root / "Users" / "tester" / "AppData" / "Local" / "Temp" / "first.tmp"
    _old_file(first, 20)
    await _confirmed_first_scan(
        include_large_files=False,
        root_override=str(root),
        plan_dir_override=str(plans),
    )
    first.unlink()
    second = first.with_name("second.tmp")
    _old_file(second, 30)
    await impl.scan_impl(
        include_large_files=False,
        root_override=str(root),
        plan_dir_override=str(plans),
    )

    plan = json.loads(impl._plan_path(str(plans)).read_text(encoding="utf-8"))
    assert [item["path"] for item in plan["items"]] == [str(second)]


async def test_clean_requires_affirmation_and_revalidates_snapshot(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "drive"
    plans = tmp_path / "plans"
    _set_test_environment(monkeypatch, root)
    stable = root / "Users" / "tester" / "AppData" / "Local" / "Temp" / "stable.tmp"
    changed = stable.with_name("changed.tmp")
    _old_file(stable, 20)
    _old_file(changed, 30)

    await _confirmed_first_scan(
        include_large_files=False,
        root_override=str(root),
        plan_dir_override=str(plans),
    )
    denied = await impl.clean_impl(
        cleanup_approved=False,
        root_override=str(root),
        plan_dir_override=str(plans),
    )
    assert denied["ok"] is False
    assert stable.exists()

    changed.write_bytes(b"replacement")
    cleaned = await impl.clean_impl(
        cleanup_approved=True,
        root_override=str(root),
        plan_dir_override=str(plans),
    )
    assert cleaned["ok"] is True
    assert cleaned["deleted_files"] == 1
    assert cleaned["skipped_changed_or_unsafe"] == 1
    assert not stable.exists()
    assert changed.exists()
    assert not impl._plan_path(str(plans)).exists()


async def test_recycle_bin_choice_mismatch_is_rejected(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "drive"
    plans = tmp_path / "plans"
    _set_test_environment(monkeypatch, root)
    _old_file(root / "Users" / "tester" / "AppData" / "Local" / "Temp" / "old.tmp")
    await _confirmed_first_scan(
        include_large_files=False,
        include_recycle_bin=False,
        root_override=str(root),
        plan_dir_override=str(plans),
    )

    mismatch = await impl.clean_impl(
        cleanup_approved=True,
        empty_recycle_bin=True,
        root_override=str(root),
        plan_dir_override=str(plans),
    )
    assert mismatch["ok"] is False
    assert "Recycle Bin choice" in mismatch["error"]


async def test_corrupt_plan_is_rejected_without_deleting(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "drive"
    plans = tmp_path / "plans"
    _set_test_environment(monkeypatch, root)
    candidate = root / "Users" / "tester" / "AppData" / "Local" / "Temp" / "old.tmp"
    _old_file(candidate)
    plans.mkdir()
    impl._plan_path(str(plans)).write_text(
        json.dumps({"items": "not-a-list", "drive": {"root": str(root)}, "include_recycle_bin": False}),
        encoding="utf-8",
    )

    result = await impl.clean_impl(
        cleanup_approved=True,
        root_override=str(root),
        plan_dir_override=str(plans),
    )

    assert result["ok"] is False
    assert "invalid" in result["error"].lower()
    assert candidate.exists()


def test_reparse_attribute_detection() -> None:
    reparse = getattr(impl.stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    assert impl._is_reparse_point(SimpleNamespace(st_file_attributes=reparse))
    assert not impl._is_reparse_point(SimpleNamespace(st_file_attributes=0))


def test_cleanup_rejects_reparse_allowlist_roots(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "safe-root"
    root.mkdir()
    monkeypatch.setattr(impl, "_is_reparse_point", lambda _stat_result: True)

    assert impl._resolved_safe_roots([root]) == []


async def test_cleanup_rejects_snapshot_for_another_system_drive(tmp_path: Path, monkeypatch) -> None:
    plans = tmp_path / "plans"
    plans.mkdir()
    impl._plan_path(str(plans)).write_text(
        json.dumps(
            {
                "drive": {"root": "C:\\"},
                "include_recycle_bin": False,
                "items": [],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("SYSTEMDRIVE", "D:")

    result = await impl.clean_impl(
        cleanup_approved=True,
        plan_dir_override=str(plans),
    )

    assert result["ok"] is False
    assert "current Windows system drive" in result["error"]


def test_public_tool_schema_exposes_only_supported_parameters() -> None:
    metadata = ToolFunction.from_callable(public.c_drive_cleanup)
    assert set(metadata.parameters["properties"]) == {
        "action",
        "scan_approved",
        "cleanup_approved",
        "categories",
        "min_age_days",
        "include_large_files",
        "include_recycle_bin",
        "empty_recycle_bin",
    }
    assert metadata.parameters["required"] == ["action"]


async def test_public_tool_rejects_unknown_action() -> None:
    result = json.loads(await public.c_drive_cleanup("erase_everything"))
    assert result["ok"] is False
    assert "Unknown action" in result["error"]
