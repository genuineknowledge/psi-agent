"""cua-driver platform backends for the ``computer_use`` tool.

Private implementation package — NOT scanned as tools (the tool index globs
``tools/*.py`` non-recursively). ``computer_use.py`` is the only public entry
point; it calls :func:`get_backend` and dispatches to the platform backend.

**The leading underscore is load-bearing, not a naming convention.** The kernel
keys private-module isolation off it in two places, and a bare ``platforms``
name fails both: ``LayerImportHook.find_spec`` declines any module whose first
segment does not start with ``_`` (so the package resolves through the ordinary
``sys.path`` instead of per-layer), and ``_stash_private_modules`` only stashes
``_``-prefixed names out of ``sys.modules`` when a scope closes (so it stays
bound afterwards). Measured with a two-layer load, one ``platforms/`` per layer:
the second layer's tool imported the **first** layer's package
(``layer=beta platforms=alpha``) and the module was still in ``sys.modules``
after the scope closed. Renaming to ``_platforms`` made each layer see its own
(``layer=beta platforms=beta``) and left ``sys.modules`` clean. That is the same
cross-layer binding ``_stash_private_modules``' own docstring records for
``_feishu``, so this package would have re-introduced a bug already fixed once.

The platform is a **deployment-time fact**, not a runtime choice: one agent
instance runs on one OS. So the selector resolves it once and caches it.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .base import Backend

_IMPLS: dict[str, Backend] = {}

# platform -> backend class, resolved lazily to avoid importing platform
# modules (and their heavy deps) on every call.
_REGISTRY: dict[str, type[Backend]] = {}


def _load_registry() -> dict[str, type[Backend]]:
    if _REGISTRY:
        return _REGISTRY
    # Deliberately lazy (hence the noqa): importing a backend pulls its platform
    # deps in, and only one of the two is ever the running OS.
    from _platforms.mac import MacBackend  # noqa: PLC0415
    from _platforms.win import WinBackend  # noqa: PLC0415

    _REGISTRY.update({MacBackend.SYSTEM: MacBackend, WinBackend.SYSTEM: WinBackend})
    return _REGISTRY


def get_backend() -> Backend:
    """Return the backend for the current OS (cached single instance)."""
    platform = sys.platform
    if platform in _IMPLS:
        return _IMPLS[platform]

    registry = _load_registry()
    cls = registry.get(platform)
    if cls is None:
        supported = ", ".join(sorted(registry))
        # Name only what ``_REGISTRY`` actually holds. The message used to add
        # "targets macOS, Windows, and Linux", which reads as a promise the
        # registry does not keep: on Linux this very branch is what raises, and
        # both private containers are Linux.
        raise RuntimeError(
            f"computer_use: unsupported platform '{platform}' (supported: {supported}). "
            "cua-driver desktop automation runs on macOS and Windows only."
        )
    impl = cls()
    _IMPLS[platform] = impl
    return impl
