"""computer_use tool - drive the desktop in the background via ``cua-driver``.

This is the **single public entry point** for desktop automation across
platforms. It is intentionally a *thin dispatcher*: it resolves the platform
backend once (:func:`_platforms.get_backend`) and delegates every action to that
backend's :meth:`~_platforms.base.Backend.execute`. The platform is a
deployment fact, not a model-visible choice — the model always calls
``computer_use`` and never has to pick macOS vs Windows.

Backends (private package ``_platforms/``, not scanned as tools):
  - mac.py  — macOS AX + SkyLight: full background matrix
  - win.py  — Windows UIA + SendInput: strict subset; refused actions return
    a clear ``[Refused]`` string (per the cua-driver action-support ledger)

The public parameter surface is unchanged from the standalone macOS tool: every
named argument is forwarded into ``**kwargs`` and each backend maps it to the
``cua-driver call <tool> '<json>'`` payload. Screenshots are written under
``generated/computer_use/`` and returned as an absolute path for ``MEDIA:`` /
``[SEND:]`` delivery.
"""

from __future__ import annotations

from importlib import import_module

# `_platforms` is a sibling package under tools/ (not a tool itself). Tools are
# loaded as top-level modules with tools/ on sys.path, so we import it by name
# rather than with a package-relative ``from . import``.
_PLATFORMS = import_module("_platforms")
get_backend = _PLATFORMS.get_backend


async def computer_use(
    action: str = "capture",
    app: str = "",
    mode: str = "som",
    tool: str = "",
    args: str = "",
    element: int | None = None,
    coordinate: list[int] | None = None,
    text: str = "",
    keys: str = "",
    direction: str = "",
    amount: int = 0,
    from_element: int | None = None,
    to_element: int | None = None,
    from_coordinate: list[int] | None = None,
    to_coordinate: list[int] | None = None,
    modifiers: list[str] | None = None,
    raise_window: bool = False,
    seconds: float = 0.0,
    capture_after: bool = False,
) -> str:
    """Drive the desktop in the background through a platform backend.

    Dispatches to the backend for the current OS (macOS / Windows / Linux).
    Captures and input events target a specific app and do NOT move the user's
    cursor, steal keyboard focus, or switch Spaces.

    Actions:
      - capture: screenshot the desktop/app. mode="som" (screenshot+overlays+AX
        index, default), "vision" (plain screenshot), "ax" (AX tree text only).
      - click / double_click / right_click / middle_click: by ``element`` or
        ``coordinate``.
      - type: enter ``text``.  key: press ``keys`` (e.g. "cmd+s", "return").
      - scroll / drag / focus_app / list_apps / wait.
      - call: escape hatch — invoke MCP ``tool`` with raw JSON ``args``.
      - setup / doctor / permissions / list_tools / version: diagnostics.

    Platform availability differs: macOS supports the full background matrix,
    while Windows refuses some background actions (right/double click, drag,
    type, key, hotkey, scroll) with a ``[Refused]`` notice.

    Args:
        action: What to do (see list above). Defaults to "capture".
        app: App name/bundle id to scope a capture or focus to.
        mode: Capture mode: "som" (default), "vision", or "ax".
        tool: MCP tool name for action="call"/"describe".
        args: Raw JSON object merged into the call payload (wins on key clashes).
        element: Element index (from a "som"/"ax" capture) to target.
        coordinate: Pixel [x, y] fallback when no element index fits.
        text: Text to type (action="type").
        keys: Key chord to press, e.g. "cmd+s", "return", "escape" (action="key").
        direction: Scroll direction: up/down/left/right (action="scroll").
        amount: Scroll amount, in the driver's scroll units (action="scroll").
        from_element / to_element: Drag source/destination element indices.
        from_coordinate / to_coordinate: Drag source/destination pixels.
        modifiers: Held modifier keys, e.g. ["cmd", "shift"].
        raise_window: focus_app only — raise the window to the front.
        seconds: Sleep duration for action="wait".
        capture_after: Fold a follow-up screenshot into the same call.

    Returns:
        The driver's JSON/text output, an app/tool listing, a ``[Refused]``
        note for platform-unavailable actions, or a status/error message; for
        captures, the absolute path of the saved PNG.
    """
    impl = get_backend()
    return await impl.execute(
        action=action,
        app=app,
        mode=mode,
        tool=tool,
        args=args,
        element=element,
        coordinate=coordinate,
        text=text,
        keys=keys,
        direction=direction,
        amount=amount,
        from_element=from_element,
        to_element=to_element,
        from_coordinate=from_coordinate,
        to_coordinate=to_coordinate,
        modifiers=modifiers,
        raise_window=raise_window,
        seconds=seconds,
        capture_after=capture_after,
    )
