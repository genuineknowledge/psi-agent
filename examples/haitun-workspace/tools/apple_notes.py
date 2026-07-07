"""apple_notes tool - manage Apple Notes through the ``memo`` CLI (macOS only).

Part of the ``apple`` toolset. Wraps the external `memo <https://github.com/antoniorodr/memo>`_
command-line tool, which drives Notes.app over AppleScript, so the agent can
create, search, view, list, and edit Apple Notes. Notes sync across Apple
devices via iCloud.

``memo`` is an external CLI (installed via Homebrew / ``uv tool install``), not
a Python package, so this tool shells out to it with :func:`anyio.run_process`
rather than importing a library — no extra dependency is added.

``memo``'s own create/edit/search flows are interactive (they open ``$EDITOR``
and use an fzf picker). This tool drives them non-interactively: it points
``$EDITOR`` at a throwaway script that injects the note body, feeds the note
number over stdin for edit, and re-implements search as a list + local filter.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile

import anyio

# Numbered note lines printed by ``memo notes`` look like ``1. My title``.
_LIST_LINE = re.compile(r"^\s*(\d+)\.\s+(.*\S)\s*$")

# Editor shim: memo runs ``$EDITOR <tempfile>``; this copies our prepared body
# (path in $APPLE_NOTES_CONTENT) over that tempfile, giving a non-interactive edit.
_EDITOR_SHIM = '#!/bin/sh\ncat "$APPLE_NOTES_CONTENT" > "$1"\n'


def _preflight() -> str | None:
    """Return an error string if memo can't be used here, else None."""
    if sys.platform != "darwin":
        return "[Error] Apple Notes is only available on macOS (memo drives Notes.app via AppleScript)."
    if shutil.which("memo") is None:
        return (
            "[Error] `memo` CLI not found. Install it with: "
            "brew tap antoniorodr/memo && brew install antoniorodr/memo/memo"
        )
    return None


async def _run_memo(
    args: list[str],
    *,
    input_text: str | None = None,
    env_extra: dict[str, str] | None = None,
    timeout_seconds: int = 120,
) -> tuple[int, str]:
    """Run ``memo <args>`` and return (returncode, combined stdout+stderr)."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    input_bytes = input_text.encode("utf-8") if input_text is not None else None
    try:
        with anyio.fail_after(timeout_seconds):
            result = await anyio.run_process(["memo", *args], input=input_bytes, check=False, env=env)
    except TimeoutError:
        return 124, f"[Error] memo timed out after {timeout_seconds}s."
    out = result.stdout.decode("utf-8", errors="replace")
    err = result.stderr.decode("utf-8", errors="replace")
    return result.returncode, (out + err).strip()


async def _run_memo_editing(args: list[str], body: str, *, input_text: str | None = None) -> tuple[int, str]:
    """Run a memo command whose flow opens ``$EDITOR``, injecting *body* non-interactively."""
    tmpdir = anyio.Path(tempfile.mkdtemp(prefix="apple_notes_"))
    try:
        content_file = tmpdir / "content.md"
        editor_file = tmpdir / "editor.sh"
        await content_file.write_text(body, encoding="utf-8")
        await editor_file.write_text(_EDITOR_SHIM, encoding="utf-8")
        await editor_file.chmod(0o755)
        return await _run_memo(
            args,
            input_text=input_text,
            env_extra={"EDITOR": str(editor_file), "APPLE_NOTES_CONTENT": str(content_file)},
        )
    finally:
        await anyio.to_thread.run_sync(lambda: shutil.rmtree(str(tmpdir), ignore_errors=True))


def _parse_notes(text: str) -> list[tuple[int, str]]:
    """Extract ``(index, title)`` pairs from ``memo notes`` list output."""
    notes: list[tuple[int, str]] = []
    for line in text.splitlines():
        m = _LIST_LINE.match(line)
        if m:
            notes.append((int(m.group(1)), m.group(2)))
    return notes


async def apple_notes(
    action: str = "list",
    query: str = "",
    folder: str = "",
    title: str = "",
    content: str = "",
    index: int | None = None,
    no_cache: bool = False,
) -> str:
    """Manage Apple Notes via the ``memo`` CLI: list, search, view, create, or edit.

    macOS only. Notes are numbered by a global index (shown by ``list``/``search``);
    pass that number as ``index`` to ``view`` or ``edit``. Search is title-based:
    it lists notes and filters titles locally (memo's own search is an interactive
    fzf picker that can't be scripted).

    Args:
        action: One of "list", "search", "view", "create", or "edit".
        query: Case-insensitive substring to match note titles on (action="search").
        folder: Restrict list/search to this Notes folder, or the target folder on create (defaults to "Notes").
        title: Title for a new note (action="create"); becomes the note's first heading line.
        content: Markdown body for the note (action="create" or "edit"). On edit it replaces the whole note.
        index: Global note number from list/search, required for "view" and "edit".
        no_cache: Bypass memo's cache and read fresh from Notes.app (slower) for list/search.

    Returns:
        The note listing, matched titles, note content, or a status/error message.
    """
    if err := _preflight():
        return err
    action = action.strip().lower()

    if action in ("list", "search"):
        args = ["notes"]
        if folder.strip():
            args += ["-f", folder.strip()]
        if no_cache:
            args.append("-nc")
        code, text = await _run_memo(args)
        if code != 0 and not text:
            return "[Error] memo failed to list notes."
        notes = _parse_notes(text)
        if action == "search" and query.strip():
            q = query.strip().lower()
            notes = [(i, t) for i, t in notes if q in t.lower()]
        if not notes:
            scope = f" in folder {folder!r}" if folder.strip() else ""
            hint = f" matching {query!r}" if action == "search" and query.strip() else ""
            return f"No notes found{scope}{hint}."
        header = "Matching notes:" if action == "search" else "Notes:"
        return header + "\n" + "\n".join(f"{i}. {t}" for i, t in notes)

    if action == "view":
        if index is None:
            return "[Error] view requires 'index' (the note number from list/search)."
        code, text = await _run_memo(["notes", "-v", str(index)])
        return text or f"[Error] Could not view note {index}."

    if action == "create":
        body = f"# {title}\n\n{content}".strip() if title else content.strip()
        if not body:
            return "[Error] create requires 'title' or 'content'."
        target_folder = folder.strip() or "Notes"
        code, text = await _run_memo_editing(["notes", "-a", "-f", target_folder], body)
        return text or (f"Note created in {target_folder!r}." if code == 0 else "[Error] Could not create note.")

    if action == "edit":
        if index is None:
            return "[Error] edit requires 'index' (the note number from list/search)."
        if not content.strip():
            return "[Error] edit requires 'content' (the new note body)."
        # Run without -f so the global index resolves correctly, and feed the
        # number over stdin to memo's interactive note picker.
        code, text = await _run_memo_editing(["notes", "-e"], content.strip(), input_text=f"{index}\n")
        return text or (f"Note {index} updated." if code == 0 else f"[Error] Could not edit note {index}.")

    return "[Error] Unknown action. Use 'list', 'search', 'view', 'create', or 'edit'."
