"""computer_use tool - drive the macOS desktop in the background via ``cua-driver``.

Part of the ``apple`` toolset. Wraps the external `cua-driver
<https://github.com/trycua/cua>`_ command-line tool, which drives native apps
through Apple's Accessibility (AX) tree and synthesized input events built on
the private SkyLight framework — so screenshots, clicks, typing, scrolling and
drags land on a target app **without moving the user's cursor, stealing
keyboard focus, or switching Spaces**. Works with any tool-capable model.

``cua-driver`` is an external app + CLI (installed via the one-line installer,
not a Python package), so this tool shells out to it with
:func:`anyio.run_process` rather than importing a library — no extra
dependency is added. Every action maps to ``cua-driver call <tool> '<json>'``,
the same handler the driver's MCP server uses; diagnostic actions use the
driver's own subcommands (``doctor``, ``permissions``, ``list-tools``,
``describe``).

Screenshots come back as PNG bytes; this tool writes them under
``generated/computer_use/`` and returns the absolute path so the caller can
deliver it with a ``MEDIA:`` / ``[SEND:]`` marker.
"""

from __future__ import annotations

import json
import os
import shutil
import time

import anyio

# cua-driver binary name (symlinked to ~/.local/bin/cua-driver by the installer).
_BIN = "cua-driver"

# One-line installer, printed by action="setup" (we never run it automatically:
# it is a networked, system-wide install and needs the user's consent).
_INSTALL_HINT = (
    "Install cua-driver, then grant permissions:\n"
    '  /bin/bash -c "$(curl -fsSL '
    'https://raw.githubusercontent.com/trycua/cua/main/libs/cua-driver/scripts/install.sh)"\n'
    "  cua-driver permissions grant   # approve Accessibility + Screen Recording\n"
    "  cua-driver doctor              # verify the install"
)

# Real cua-driver subcommands (not MCP tools invoked through ``call``).
_SUBCOMMANDS = {
    "setup": None,
    "doctor": ["doctor"],
    "permissions": ["permissions", "status"],
    "list_tools": ["list-tools"],
    "version": ["--version"],
}

# Where captured PNGs are written (relative to the workspace cwd, git-ignored).
_SHOT_DIR = os.path.join("generated", "computer_use")


def _preflight() -> str | None:
    """Return an error string if cua-driver can't be used here, else None."""
    if shutil.which(_BIN) is None:
        return f"[Error] `{_BIN}` CLI not found.\n{_INSTALL_HINT}"
    return None


async def _run(args: list[str], *, timeout_seconds: int = 120) -> tuple[int, str]:
    """Run ``cua-driver <args>`` and return (returncode, combined stdout+stderr)."""
    try:
        with anyio.fail_after(timeout_seconds):
            result = await anyio.run_process([_BIN, *args], check=False)
    except TimeoutError:
        return 124, f"[Error] {_BIN} timed out after {timeout_seconds}s."
    out = result.stdout.decode("utf-8", errors="replace")
    err = result.stderr.decode("utf-8", errors="replace")
    return result.returncode, (out + err).strip()


def _merge_args(base: dict[str, object], raw: str) -> dict[str, object]:
    """Merge a raw JSON overrides string into *base* (raw wins on key clashes)."""
    if not raw.strip():
        return base
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"'args' is not valid JSON: {exc}") from exc
    if not isinstance(extra, dict):
        raise ValueError("'args' must be a JSON object, e.g. '{\"pid\": 512}'.")
    return {**base, **extra}


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
    """Drive the macOS desktop in the background through ``cua-driver``.

    Captures and input events target a specific app via its Accessibility tree
    and do NOT move the user's cursor, steal keyboard focus, or switch Spaces.
    Typical loop: ``capture`` (mode="som" for a screenshot with numbered element
    overlays + AX index) → act by ``element`` index → re-``capture`` to verify
    (or pass ``capture_after=True`` to fold the follow-up screenshot in).

    Actions:
      - capture: screenshot the desktop/app. mode="som" (screenshot+overlays+AX
        index, default), "vision" (plain screenshot), "ax" (AX tree text only,
        no image). Scope with ``app``. PNG is saved under generated/computer_use/.
      - click / double_click / right_click / middle_click: target by ``element``
        index (preferred) or ``coordinate`` [x, y]; optional ``modifiers``.
      - type: enter ``text``.  key: press ``keys`` (e.g. "cmd+s", "return").
      - scroll: ``direction`` up/down/left/right, ``amount``, at ``element``/``coordinate``.
      - drag: from ``from_element``/``from_coordinate`` to ``to_element``/``to_coordinate``.
      - focus_app / list_apps: focus (``raise_window`` stays False unless asked) or enumerate apps.
      - wait: sleep ``seconds``.
      - call: escape hatch — invoke MCP ``tool`` with raw JSON ``args`` verbatim.
      - list_tools / describe (``tool``) / doctor / permissions / version / setup: diagnostics.

    Underlying tool names/schemas can vary by cua-driver version; if a drive
    action is rejected, run action="list_tools" / action="describe" to see the
    exact schema, then use action="call" with ``tool`` + ``args`` to match it.

    Args:
        action: What to do (see list above). Defaults to "capture".
        app: App name/bundle id to scope a capture or focus to.
        mode: Capture mode: "som" (default), "vision", or "ax".
        tool: MCP tool name for action="call"/"describe" (overrides the default mapping).
        args: Raw JSON object merged into the call payload (wins on key clashes).
        element: Element index (from a "som"/"ax" capture) to target.
        coordinate: Pixel [x, y] fallback when no element index fits.
        text: Text to type (action="type").
        keys: Key chord to press, e.g. "cmd+s", "return", "escape" (action="key").
        direction: Scroll direction: up/down/left/right (action="scroll").
        amount: Scroll amount, in the driver's scroll units (action="scroll").
        from_element: Source element index for a drag.
        to_element: Destination element index for a drag.
        from_coordinate: Source pixel [x, y] for a drag.
        to_coordinate: Destination pixel [x, y] for a drag.
        modifiers: Held modifier keys, e.g. ["cmd", "shift"], for click/drag.
        raise_window: focus_app only — raise the window to the front (default False = stay in background).
        seconds: Sleep duration for action="wait".
        capture_after: Fold a follow-up screenshot into the same call after acting.

    Returns:
        The driver's JSON/text output, an app/tool listing, or a status/error
        message; for captures, the absolute path of the saved PNG.
    """
    if err := _preflight():
        return err
    action = action.strip().lower()

    # --- Diagnostic subcommands (not MCP tools) ------------------------------
    if action == "setup":
        code, text_out = await _run(["doctor"])
        status = text_out or "(no output)"
        return f"{_INSTALL_HINT}\n\n--- cua-driver doctor ---\n{status}"
    if action == "describe":
        if not tool.strip():
            return "[Error] describe requires 'tool' (the MCP tool name to inspect)."
        code, text_out = await _run(["describe", tool.strip()])
        return text_out or f"[Error] Could not describe tool {tool!r}."
    if action in _SUBCOMMANDS:
        code, text_out = await _run(_SUBCOMMANDS[action])  # ty: ignore
        return text_out or f"[Error] `{_BIN} {' '.join(_SUBCOMMANDS[action])}` produced no output."  # ty: ignore

    if action == "wait":
        await anyio.sleep(max(0.0, seconds))
        return f"Waited {max(0.0, seconds)}s."

    # --- Drive / MCP-tool actions (via `cua-driver call <tool> '<json>'`) ----
    # Map the friendly action to a cua-driver MCP tool name (overridable by `tool`).
    tool_name = tool.strip() or ("screenshot" if action == "capture" else action)

    payload: dict[str, object] = {}
    if app.strip():
        payload["app"] = app.strip()
    if action == "capture":
        payload["mode"] = mode.strip() or "som"
    if element is not None:
        payload["element"] = element
    if coordinate:
        payload["coordinate"] = coordinate
    if text:
        payload["text"] = text
    if keys.strip():
        payload["keys"] = keys.strip()
    if direction.strip():
        payload["direction"] = direction.strip()
    if amount:
        payload["amount"] = amount
    if from_element is not None:
        payload["from_element"] = from_element
    if to_element is not None:
        payload["to_element"] = to_element
    if from_coordinate:
        payload["from_coordinate"] = from_coordinate
    if to_coordinate:
        payload["to_coordinate"] = to_coordinate
    if modifiers:
        payload["modifiers"] = modifiers
    if action == "focus_app":
        payload["raise_window"] = raise_window
    if capture_after and action != "capture":
        payload["capture_after"] = True

    try:
        payload = _merge_args(payload, args)
    except ValueError as exc:
        return f"[Error] {exc}"

    call_args = ["call", tool_name, json.dumps(payload)]

    # A screenshot comes back as an image content block; extract it to a file.
    # capture (unless ax-only) and any action with capture_after produce one.
    wants_image = capture_after or (action == "capture" and (mode.strip() or "som") != "ax")
    shot_path = ""
    if wants_image:
        shot_dir = anyio.Path(_SHOT_DIR)
        await shot_dir.mkdir(parents=True, exist_ok=True)
        shot_path = str(await (shot_dir / f"shot-{int(time.time() * 1000)}.png").resolve())
        call_args += ["--screenshot-out-file", shot_path]

    code, out = await _run(call_args)

    if code != 0:
        detail = out or "(no output)"
        return (
            f"[Error] `{_BIN} call {tool_name}` failed (exit {code}): {detail}\n"
            f"Hint: run action='list_tools' / action='describe' tool='{tool_name}' to check the schema."
        )

    if shot_path and await anyio.Path(shot_path).exists():
        note = out.strip()
        suffix = f"\n{note}" if note else ""
        return f"Screenshot saved: {shot_path}\nDeliver it to the user with MEDIA:{shot_path}{suffix}"
    return out or f"{tool_name} ok."
