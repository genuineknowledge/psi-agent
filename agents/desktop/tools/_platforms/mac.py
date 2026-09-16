"""macOS backend for ``computer_use``.

Drives native macOS apps in the background via ``cua-driver`` (Accessibility
AX tree + SkyLight synthesized input). This is the **existing** macOS behavior
moved verbatim into a backend; nothing about it changes on the refactor.

Per the cua-driver E2E ledger, macOS supports the broad background matrix
(left/right/double click, type, key, hotkey, scroll, …), so
:attr:`REFUSALS` is empty. The only platform-specific bits here are the
installer hint (``install.sh``) and permission grants (Accessibility + Screen
Recording).
"""

from __future__ import annotations

from typing import ClassVar

from .base import Backend

_INSTALL_HINT = (
    "Install cua-driver, then grant permissions:\n"
    '  /bin/bash -c "$(curl -fsSL '
    'https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh)"\n'
    "  cua-driver permissions grant   # approve Accessibility + Screen Recording\n"
    "  cua-driver doctor              # verify the install"
)


class MacBackend(Backend):
    """macOS AX + SkyLight backend (full background capability matrix)."""

    SYSTEM = "darwin"
    INSTALL_HINT = _INSTALL_HINT
    PERMISSIONS_CMD: ClassVar[tuple[str, ...]] = ("permissions", "status")
    REFUSALS: ClassVar[dict[str, str]] = {}  # macOS: no background refusals per cua ledger
