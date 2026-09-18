"""Shared cua-driver backend base for the ``computer_use`` tool.

This module is a private implementation library (NOT registered as a tool —
the tool index only scans ``tools/*.py`` non-recursively and only exposes
top-level ``async def``). It holds the platform-neutral machinery every
backend reuses:

- shelling out to the external ``cua-driver`` CLI (:meth:`Backend._run`)
- merging raw JSON arg overrides (:meth:`Backend._merge_args`)
- writing captures to ``generated/computer_use/`` and returning their path
- the diagnostic subcommands (``doctor`` / ``permissions`` / ``list_tools`` /
  ``version`` / ``setup``) that differ per platform only in their permission
  hints
- the action → ``cua-driver call <tool> '<json>'`` mapping (identical schema
  across macOS / Windows / Linux, so it lives here once)

Each concrete backend (:mod:`.mac`, :mod:`.win`) subclasses :class:`Backend`
and only overrides the parts that genuinely differ per platform:

- :attr:`Backend.SYSTEM`
- :attr:`Backend.INSTALL_HINT`      (install.sh vs install.ps1 + permission grant)
- :attr:`Backend.PERMISSIONS_CMD`   (per-platform permission inspection)
- :attr:`Backend.REFUSALS`          (actions unavailable in background on this OS)

External contract (must not change across backend refactors):
- captures are written under ``generated/computer_use/`` and returned as the
  absolute path with a ``MEDIA:`` marker for delivery
- refused / unavailable actions return a clear ``[Refused] <reason>`` string
"""

from __future__ import annotations

import json
import os
import shutil
import time
from collections.abc import Sequence
from typing import Any, ClassVar

import anyio

# cua-driver binary name (symlinked to ~/.local/bin/cua-driver by the installer).
_BIN = "cua-driver"

# Where captured PNGs are written (relative to the workspace cwd, git-ignored).
_SHOT_DIR = os.path.join("generated", "computer_use")


class Backend:
    """Platform backend for driving the desktop through ``cua-driver``."""

    # Override in subclasses ------------------------------------------------
    SYSTEM = "unknown"  # sys.platform value this backend handles
    INSTALL_HINT = ""  # per-platform one-line installer + permission grant
    PERMISSIONS_CMD: ClassVar[tuple[str, ...]] = ("permissions", "status")

    # action -> reason when the OS cannot do it in the background.
    # Empty = no background restrictions (macOS full matrix).
    REFUSALS: ClassVar[dict[str, str]] = {}

    # -----------------------------------------------------------------------
    # Real cua-driver subcommands (not MCP tools invoked through ``call``).
    #
    # ``permissions`` is resolved in :meth:`_subcommand`, not here: a class body
    # reading ``PERMISSIONS_CMD`` binds the value *at class-definition time*, so
    # a subclass override never reached this dict — the dict is also a single
    # object shared by every subclass. Measured by setting
    # ``WinBackend.PERMISSIONS_CMD`` to a distinctive value: the instance
    # reported it, while ``_SUBCOMMANDS["permissions"]`` still returned the base
    # ``("permissions", "status")``. Latent today only because both backends
    # happen to declare the same value.
    _SUBCOMMANDS: ClassVar[dict[str, tuple[str, ...]]] = {
        "doctor": ("doctor",),
        "list_tools": ("list-tools",),
        "version": ("--version",),
    }

    def _subcommand(self, action: str) -> tuple[str, ...] | None:
        """The cua-driver subcommand for *action*, honouring subclass overrides."""
        if action == "permissions":
            return self.PERMISSIONS_CMD
        return self._SUBCOMMANDS.get(action)

    # -----------------------------------------------------------------------
    async def _run(self, args: Sequence[str], *, timeout_seconds: int = 120) -> tuple[int, str]:
        """Run ``cua-driver <args>`` and return (returncode, combined stdout+stderr).

        ``Sequence`` rather than ``list``: the subcommand tables are tuples and
        this method only unpacks *args* into a fresh list, so narrowing to
        ``list`` would be a promise the callers never needed to keep.
        """
        try:
            with anyio.fail_after(timeout_seconds):
                result = await anyio.run_process([_BIN, *args], check=False)
        except TimeoutError:
            return 124, f"[Error] {_BIN} timed out after {timeout_seconds}s."
        out = result.stdout.decode("utf-8", errors="replace")
        err = result.stderr.decode("utf-8", errors="replace")
        return result.returncode, (out + err).strip()

    def _preflight(self) -> str | None:
        """Return an error string if cua-driver isn't usable on this OS, else None."""
        if shutil.which(_BIN) is None:
            return f"[Error] `{_BIN}` CLI not found.\n{self.INSTALL_HINT}"
        return None

    @staticmethod
    def _merge_args(base: dict[str, Any], raw: str) -> dict[str, Any]:
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

    # -----------------------------------------------------------------------
    async def execute(self, action: str = "capture", **kwargs: Any) -> str:
        """Dispatch one desktop action on this platform's backend.

        Accepts the full ``computer_use`` parameter surface as ``**kwargs``
        (action, app, mode, tool, args, element, coordinate, text, keys,
        direction, amount, from_element, to_element, from_coordinate,
        to_coordinate, modifiers, raise_window, seconds, capture_after).
        """
        action = action.strip().lower()

        if pre := self._preflight():
            return pre
        if action in self.REFUSALS:
            return f"[Refused] {action} on {self.SYSTEM}: {self.REFUSALS[action]}"

        # --- service actions ------------------------------------------------
        if action == "setup":
            code, text_out = await self._run(["doctor"])
            status = text_out or "(no output)"
            return f"{self.INSTALL_HINT}\n\n--- cua-driver doctor ---\n{status}"
        if (subcommand := self._subcommand(action)) is not None:
            code, text_out = await self._run(subcommand)
            return text_out or f"[Error] `{_BIN} {' '.join(subcommand)}` produced no output."
        if action == "wait":
            seconds = float(kwargs.get("seconds", 0.0) or 0.0)
            await anyio.sleep(max(0.0, seconds))
            return f"Waited {max(0.0, seconds)}s."

        # --- drive actions (via `cua-driver call <tool> '<json>'`) ----------
        tool_name = kwargs.get("tool", "").strip() or self._action_tool(action)
        payload = self._build_payload(action, kwargs)

        call_args = ["call", tool_name, json.dumps(payload)]

        wants_image = bool(kwargs.get("capture_after")) or (
            action == "capture" and (kwargs.get("mode", "") or "som") != "ax"
        )
        shot_path = ""
        if wants_image:
            shot_dir = anyio.Path(_SHOT_DIR)
            await shot_dir.mkdir(parents=True, exist_ok=True)
            shot_path = str(await (shot_dir / f"shot-{int(time.time() * 1000)}.png").resolve())
            call_args += ["--screenshot-out-file", shot_path]

        code, out = await self._run(call_args)
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

    def _action_tool(self, action: str) -> str:
        """Map a friendly action to a cua-driver MCP tool name (schema is cross-platform)."""
        return "screenshot" if action == "capture" else action

    @staticmethod
    def _build_payload(action: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Assemble the JSON payload for a drive action from parsed kwargs."""
        payload: dict[str, Any] = {}
        app = kwargs.get("app", "").strip()
        if app:
            payload["app"] = app
        if action == "capture":
            payload["mode"] = (kwargs.get("mode", "") or "som").strip() or "som"
        for key in (
            "element",
            "coordinate",
            "text",
            "keys",
            "direction",
            "amount",
            "from_element",
            "to_element",
            "from_coordinate",
            "to_coordinate",
            "modifiers",
        ):
            if kwargs.get(key) not in (None, "", 0):
                payload[key] = kwargs[key]
        if action == "focus_app":
            payload["raise_window"] = bool(kwargs.get("raise_window"))
        if kwargs.get("capture_after") and action != "capture":
            payload["capture_after"] = True

        raw = kwargs.get("args", "") or ""
        return Backend._merge_args(payload, raw)  # raw wins on key clashes
