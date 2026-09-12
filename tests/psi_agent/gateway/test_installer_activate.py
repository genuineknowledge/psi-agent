"""Installer second-click activate Event (Windows haitun.exe)."""

from __future__ import annotations

import ctypes
import re
import sys
import time
from pathlib import Path

import pytest

from psi_agent.gateway.desktop._installer_activate import (
    ACTIVATE_EVENT_NAME,
    InstallerActivateListener,
)

_HAITUN_C = Path(__file__).resolve().parents[3] / ".github" / "inno-setup" / "haitun.c"


def test_activate_event_name_matches_haitun_c() -> None:
    """C launcher and Python must agree on the named Event (byte-for-byte)."""
    text = _HAITUN_C.read_text(encoding="utf-8")
    m = re.search(
        r'#define\s+HAITUN_ACTIVATE_EVENT\s+L"([^"]+)"',
        text,
    )
    assert m is not None, "HAITUN_ACTIVATE_EVENT missing from haitun.c"
    # L"..." in source uses \\ for one backslash in the wide string.
    c_name = m.group(1).replace("\\\\", "\\")
    assert c_name == ACTIVATE_EVENT_NAME


def test_single_mutex_define_present_in_haitun_c() -> None:
    text = _HAITUN_C.read_text(encoding="utf-8")
    assert "GenuineKnowledge.HaitunAgent.SingleInstance" in text
    assert "ERROR_ALREADY_EXISTS" in text


@pytest.mark.skipif(sys.platform != "win32", reason="named Event is Windows-only")
def test_activate_listener_invokes_callback() -> None:
    hits: list[int] = []
    listener = InstallerActivateListener(lambda: hits.append(1))
    listener.start()
    try:
        assert listener._event is not None
        windll = getattr(ctypes, "windll", None)
        assert windll is not None
        kernel32 = windll.kernel32
        # Open the same named Event the listener created, then signal it.
        handle = kernel32.OpenEventW(0x0002, False, ACTIVATE_EVENT_NAME)  # EVENT_MODIFY_STATE
        assert handle
        try:
            kernel32.SetEvent(handle)
        finally:
            kernel32.CloseHandle(handle)
        deadline = time.monotonic() + 2.0
        while not hits and time.monotonic() < deadline:
            time.sleep(0.05)
        assert hits == [1]
    finally:
        listener.stop()
