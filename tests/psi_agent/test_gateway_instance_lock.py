"""Exclusive AppData ownership for one Gateway process."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from psi_agent._gateway_instance_lock import (
    AppDataInstanceLock,
    GatewayInstanceError,
    instance_lock_path,
)


def test_instance_lock_path_under_appdata(tmp_path: Path) -> None:
    root = str(tmp_path)
    assert instance_lock_path(root) == os.path.join(root, "gateway.instance.lock")


def test_second_acquire_same_appdata_fails(tmp_path: Path) -> None:
    root = str(tmp_path)
    first = AppDataInstanceLock(root)
    first.acquire()
    try:
        second = AppDataInstanceLock(root)
        with pytest.raises(GatewayInstanceError) as ei:
            second.acquire()
        assert ei.value.appdata_root == root
        msg = str(ei.value)
        assert "--appdata" in msg
        # CLI message must stay clean (no errno / Permission denied noise).
        assert "Errno" not in msg
        assert "Permission" not in msg
    finally:
        first.release()


def test_release_allows_reacquire(tmp_path: Path) -> None:
    root = str(tmp_path)
    first = AppDataInstanceLock(root)
    first.acquire()
    first.release()
    second = AppDataInstanceLock(root)
    second.acquire()
    second.release()


def test_different_appdata_roots_do_not_conflict(tmp_path: Path) -> None:
    a = str(tmp_path / "a")
    b = str(tmp_path / "b")
    la = AppDataInstanceLock(a)
    lb = AppDataInstanceLock(b)
    la.acquire()
    try:
        lb.acquire()
        lb.release()
    finally:
        la.release()


def test_context_manager_releases_on_exit(tmp_path: Path) -> None:
    root = str(tmp_path)
    with AppDataInstanceLock(root), pytest.raises(GatewayInstanceError):
        AppDataInstanceLock(root).acquire()
    # After exit, another process (here: another lock object) can take it.
    with AppDataInstanceLock(root):
        pass


def test_lock_file_records_pid(tmp_path: Path) -> None:
    root = str(tmp_path)
    lock = AppDataInstanceLock(root)
    lock.acquire()
    try:
        assert f"pid={os.getpid()}" in lock.held_pid_line()
    finally:
        lock.release()
