"""imessage tool - send and receive iMessages / SMS through the ``imsg`` CLI (macOS only).

Part of the ``apple`` toolset. Wraps the external `imsg <https://github.com/steipete/imsg>`_
command-line tool, which reads Messages.app's local chat database and drives
Messages automation, so the agent can list conversations, read history, search,
send text/files, and check for new messages.

``imsg`` is an external CLI (installed via Homebrew: ``brew install
steipete/tap/imsg``), not a Python package, so this tool shells out to it with
:func:`anyio.run_process` rather than importing a library — no extra dependency
is added. Every command is invoked with ``--json`` where supported so the output
is machine-readable (imsg emits one JSON object per line; progress/warnings go to
stderr to keep the stream parseable).

macOS only, and imsg needs **Full Disk Access** (to read the chat database) plus
**Automation** permission for Messages.app (to send). Because this can read the
entire message history and send on the user's behalf, sends always require an
explicit recipient and the caller should confirm recipient + content first.
"""

from __future__ import annotations

import json
import shutil
import sys

import anyio

# imsg binary name (installed to PATH by the Homebrew formula).
_BIN = "imsg"

_INSTALL_HINT = (
    "Install imsg, then grant permissions:\n"
    "  brew install steipete/tap/imsg\n"
    "  # System Settings → Privacy & Security:\n"
    "  #   - Full Disk Access for your terminal (read chat history)\n"
    "  #   - Automation → allow controlling Messages.app (send)"
)


def _preflight() -> str | None:
    """Return an error string if imsg can't be used here, else None."""
    if sys.platform != "darwin":
        return "[Error] iMessage is only available on macOS (imsg reads Messages.app's local database)."
    if shutil.which(_BIN) is None:
        return f"[Error] `{_BIN}` CLI not found.\n{_INSTALL_HINT}"
    return None


async def _run(args: list[str], *, timeout_seconds: int = 60) -> tuple[int, str]:
    """Run ``imsg <args>`` and return (returncode, combined stdout+stderr)."""
    try:
        with anyio.fail_after(timeout_seconds):
            result = await anyio.run_process([_BIN, *args], check=False)
    except TimeoutError:
        return 124, f"[Error] {_BIN} timed out after {timeout_seconds}s."
    out = result.stdout.decode("utf-8", errors="replace")
    err = result.stderr.decode("utf-8", errors="replace")
    return result.returncode, (out + err).strip()


def _parse_jsonl(text: str) -> list[dict]:
    """Parse imsg's JSON-lines output into a list of objects (skipping noise)."""
    items: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            items.append(obj)
    return items


async def imessage(
    action: str = "chats",
    to: str = "",
    text: str = "",
    file: str = "",
    chat_id: str = "",
    query: str = "",
    service: str = "auto",
    limit: int = 20,
    attachments: bool = False,
    match: str = "contains",
) -> str:
    """Send and receive iMessages / SMS via the ``imsg`` CLI (macOS only).

    Reads Messages.app's local chat database and drives Messages automation.
    Conversations are addressed by a numeric ``chat_id`` (from ``chats``) or, for
    sending, by a recipient ``to`` (phone number, Apple ID, or contact name).

    Always confirm the recipient and message content with the user before
    ``send``; never message unknown numbers without approval, and verify any
    ``file`` path exists before attaching.

    Args:
        action: One of "chats" (list recent conversations), "history" (read a
            conversation), "search" (full-text search local history), "send"
            (send text and/or a file), or "watch" (poll once for recent messages
            in a chat). Defaults to "chats".
        to: Recipient for "send" — phone number (e.g. "+14155551212"), Apple ID,
            or contact name (e.g. "Jane Appleseed").
        text: Message body for "send".
        file: Absolute path to a file to attach on "send".
        chat_id: Numeric conversation id (from "chats") for "history"/"watch",
            or as an alternative recipient for "send".
        query: Text to search for (action="search").
        service: Delivery type for "send": "auto" (default; Messages.app decides),
            "imessage", or "sms".
        limit: Max number of chats / history / search rows to return.
        attachments: Include attachment metadata in history/watch output.
        match: For "search", "contains" (default) or "exact".

    Returns:
        A formatted listing / conversation / search result, a send confirmation,
        or an "[Error] ..." message.
    """
    if err := _preflight():
        return err
    action = action.strip().lower()

    if action == "chats":
        code, text_out = await _run(["chats", "--limit", str(limit), "--json"])
        if code != 0 and not text_out:
            return "[Error] imsg failed to list chats."
        chats = _parse_jsonl(text_out)
        if not chats:
            return text_out or "No chats found."
        lines = []
        for c in chats:
            cid = c.get("chat_id", c.get("id", "?"))
            name = c.get("display_name") or c.get("name") or ""
            handles = c.get("participants") or c.get("handles") or c.get("identifier") or ""
            if isinstance(handles, list):
                handles = ", ".join(str(h) for h in handles)
            label = name or handles or "(unnamed)"
            extra = f" [{handles}]" if name and handles else ""
            lines.append(f"chat {cid}: {label}{extra}")
        return "Chats:\n" + "\n".join(lines)

    if action == "history":
        if not chat_id.strip():
            return "[Error] history requires 'chat_id' (the number from action='chats')."
        args = ["history", "--chat-id", chat_id.strip(), "--limit", str(limit)]
        if attachments:
            args.append("--attachments")
        args.append("--json")
        code, text_out = await _run(args)
        if code != 0 and not text_out:
            return f"[Error] Could not read history for chat {chat_id}."
        return _format_messages(text_out) or f"No messages in chat {chat_id}."

    if action == "search":
        if not query.strip():
            return "[Error] search requires 'query'."
        args = ["search", "--query", query.strip(), "--match", match.strip() or "contains"]
        args += ["--limit", str(limit), "--json"]
        code, text_out = await _run(args)
        if code != 0 and not text_out:
            return f"[Error] Search failed for {query!r}."
        return _format_messages(text_out) or f"No messages matching {query!r}."

    if action == "watch":
        # One-shot poll: imsg watch streams forever, so bound it with a short
        # timeout and return whatever arrived. A chat_id keeps it scoped.
        args = ["watch"]
        if chat_id.strip():
            args += ["--chat-id", chat_id.strip()]
        if attachments:
            args.append("--attachments")
        args.append("--json")
        code, text_out = await _run(args, timeout_seconds=15)
        # A timeout here is expected (watch never exits on its own).
        return _format_messages(text_out) or "No new messages."

    if action == "send":
        recipient_args: list[str] = []
        if to.strip():
            recipient_args = ["--to", to.strip()]
        elif chat_id.strip():
            recipient_args = ["--chat-id", chat_id.strip()]
        else:
            return "[Error] send requires 'to' (recipient) or 'chat_id'."
        if not text.strip() and not file.strip():
            return "[Error] send requires 'text' and/or 'file'."
        args = ["send", *recipient_args]
        if text.strip():
            args += ["--text", text]
        if file.strip():
            args += ["--file", file.strip()]
        svc = service.strip().lower() or "auto"
        if svc != "auto":
            args += ["--service", svc]
        args.append("--json")
        code, text_out = await _run(args)
        if code != 0:
            return f"[Error] Failed to send message (exit {code}): {text_out or '(no output)'}"
        target = to.strip() or f"chat {chat_id.strip()}"
        return text_out.strip() or f"Message sent to {target}."

    return "[Error] Unknown action. Use 'chats', 'history', 'search', 'send', or 'watch'."


def _format_messages(text: str) -> str:
    """Render imsg message JSON-lines into a readable transcript."""
    msgs = _parse_jsonl(text)
    if not msgs:
        return text.strip()
    lines = []
    for m in msgs:
        when = m.get("date") or m.get("timestamp") or ""
        sender = m.get("sender") or m.get("handle") or ("me" if m.get("is_from_me") else "?")
        if m.get("is_from_me"):
            sender = "me"
        body = m.get("text") or m.get("body") or ""
        atts = m.get("attachments") or []
        att_note = f" [attachments: {len(atts)}]" if atts else ""
        prefix = f"[{when}] " if when else ""
        lines.append(f"{prefix}{sender}: {body}{att_note}".rstrip())
    return "\n".join(lines)
