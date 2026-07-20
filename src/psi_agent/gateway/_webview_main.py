"""pywebview subprocess entry point. No psi_agent imports."""

from __future__ import annotations

import contextlib
import ctypes
import multiprocessing
import sys
import threading
from ctypes import wintypes


def _flash_hwnd(hwnd: int) -> None:
    if sys.platform != "win32":
        return

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    info = FLASHWINFO()
    info.cbSize = ctypes.sizeof(FLASHWINFO)
    info.hwnd = hwnd
    info.dwFlags = 0x02 | 0x0C
    info.uCount = 5
    info.dwTimeout = 0
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def webview_main(
    url: str,
    icon: str | None,
    tray_mode: bool,
    app_name: str,
    cmd_q: multiprocessing.Queue,
    evt_q: multiprocessing.Queue,
) -> None:
    webview = __import__("webview")

    window = webview.create_window(app_name, url)

    def on_closing() -> bool:
        if tray_mode:
            window.hide()
            evt_q.put("hidden")
            return False
        evt_q.put("closed")
        return True

    window.events.closing += on_closing  # ty: ignore

    def cmd_loop() -> None:
        while True:
            cmd = cmd_q.get()
            if cmd == "show":
                with contextlib.suppress(Exception):
                    window.show()
            elif cmd == "destroy":
                with contextlib.suppress(Exception):
                    window.destroy()
                break
            elif cmd == "flash":
                with contextlib.suppress(Exception):
                    native = window.native
                    hwnd = getattr(native, "Handle", None)
                    if hwnd is not None:
                        _flash_hwnd(int(hwnd))

    t = threading.Thread(target=cmd_loop, daemon=True)
    t.start()
    evt_q.put("ready")
    webview.start(icon=icon)
