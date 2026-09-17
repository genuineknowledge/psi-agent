"""Windows backend for ``computer_use``.

Drives Windows apps in the background via ``cua-driver`` (UIAutomation +
SendInput backend).

Unlike macOS, Windows does **not** provide the full background matrix. Per the
cua-driver E2E action-support ledger, the background capabilities on Windows
are a strict subset: left click and child-window AX/PX targets deliver, while
right/double click, drag, type, key, hotkey and scroll are refused in the
background. Platform-specific refusal codes also exist (``background_occluded``,
``background_unavailable``, ``background_uipi_blocked``).

So this backend keeps :attr:`REFUSALS` populated from that ledger and returns a
clear ``[Refused]`` string for actions Windows cannot do in the background —
rather than silently attempting them.

Installer differs from macOS: ``install.ps1`` on Windows, plus granting
UIAccess to the driver.
"""

from __future__ import annotations

from typing import ClassVar

from .base import Backend

_INSTALL_HINT = (
    "Install cua-driver for Windows, then grant permissions:\n"
    "  1. Open PowerShell as Administrator and run:\n"
    "     powershell -ExecutionPolicy Bypass -File install.ps1\n"
    "  2. Grant the driver UIAccess (Windows UI Automation integrity level)\n"
    "  3. `cua-driver permissions grant`  # approve Accessibility\n"
    "  4. `cua-driver doctor`             # verify the install"
)

# Background refusals observed per the cua-driver action-support ledger.
# Key: friendly action name. Value: human reason + official refusal code.
_REFUSALS = {
    "right_click": "background_occluded on Windows",
    "double_click": "background_occluded on Windows",
    "drag": "background_occluded on Windows",
    "type": "background_unavailable on Windows",
    "press_key": "background_unavailable on Windows",
    "hotkey": "background_unavailable on Windows",
    "scroll": "background_unavailable on Windows",
    "editor_save": "background_unavailable on Windows",
}


class WinBackend(Backend):
    """Windows UIA + SendInput backend (strict background subset per ledger)."""

    SYSTEM = "win32"
    INSTALL_HINT = _INSTALL_HINT
    PERMISSIONS_CMD: ClassVar[tuple[str, ...]] = ("permissions", "status")
    REFUSALS: ClassVar[dict[str, str]] = _REFUSALS
