"""Computer use tool — control the desktop via GUI automation."""

from __future__ import annotations


async def tool(
    action: str = "capture",
    mode: str = "som",
    element: int | None = None,
    text: str = "",
    keys: str = "",
    direction: str = "down",
    amount: int = 3,
    app: str = "",
) -> str:
    """Drive the desktop via GUI automation (computer use).

    Args:
        action: One of ``"capture"``, ``"click"``, ``"type"``, ``"key"``,
            ``"scroll"``, or ``"focus_app"``.
        mode: Capture mode — ``"som"`` (set-of-marks) or ``"screenshot"``.
        element: Element index to click (used with action="click").
        text: Text to type (used with action="type").
        keys: Key combo to press, e.g. ``"cmd+s"`` (used with action="key").
        direction: Scroll direction — ``"up"`` or ``"down"``
            (used with action="scroll").
        amount: Number of scroll steps (used with action="scroll").
        app: App name to target for capture or focus.

    Returns:
        Screenshot data or confirmation, or a not-supported message.
    """
    return (
        "computer_use is not supported in this workspace. "
        "No desktop automation backend is available. "
        "To enable computer use, install and configure the cua-driver backend "
        "and replace this stub with real implementation."
    )
