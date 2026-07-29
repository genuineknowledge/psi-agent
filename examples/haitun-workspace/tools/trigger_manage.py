"""Manage agent-package event triggers (triggers/<name>/TRIGGER.md).

Agent-package tool: creates/updates files under the Session agent root
(same zone as ``schedules/``). Session's ``TriggerRegistry`` loads those
files and fires on ``POST /events``.
"""

from __future__ import annotations

import json
import re
from contextlib import suppress
from datetime import UTC, datetime

import _runtime_paths as _paths
import anyio
import yaml

# Mirror common feishu channel_events names — soft hint only (Session has no catalog gate).
_KNOWN_EVENTS = frozenset(
    {
        "feishu.chat.member_added",
        "feishu.chat.member_removed",
        "feishu.im.message_received",
        "telegram.chat.member_joined",
    }
)

# Catalog → platform-native type (Feishu WS event). Used to dual-write raw_event.
_EVENT_TO_RAW: dict[str, str] = {
    "feishu.chat.member_added": "im.chat.member.user.added_v1",
    "feishu.chat.member_removed": "im.chat.member.user.deleted_v1",
    "feishu.im.message_received": "im.message.receive_v1",
}


def _triggers_dir() -> anyio.Path:
    # Same root as schedules: Session.agent package (falls back to workspace when unbound).
    return _paths.resolve_agent() / "triggers"


def _validate_trigger_name(trigger_name: str) -> str | None:
    if not trigger_name.strip():
        return "Invalid trigger name: name cannot be empty."
    if "/" in trigger_name or "\\" in trigger_name:
        return f"Invalid trigger name {trigger_name!r}: must not contain path separators."
    if ".." in trigger_name:
        return f"Invalid trigger name {trigger_name!r}: must not contain '..'."
    if "\x00" in trigger_name:
        return f"Invalid trigger name {trigger_name!r}: must not contain null characters."
    if not re.fullmatch(r"[A-Za-z0-9_-]+", trigger_name):
        return f"Invalid trigger name {trigger_name!r}: only letters, digits, hyphens, and underscores are allowed."
    return None


def _validate_visibility(visibility: str) -> str | None:
    v = visibility.strip().casefold()
    if v not in {"", "display", "silent"}:
        return f"Invalid visibility {visibility!r}: use 'display' or 'silent'."
    return None


def _parse_tool_args(tool_args: str) -> tuple[dict[str, object] | None, str | None]:
    raw = tool_args.strip()
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"Invalid tool_args JSON: {e}"
    if not isinstance(parsed, dict):
        return None, "tool_args must be a JSON object."
    return parsed, None


def _parse_filter(filter_json: str) -> tuple[dict[str, object] | None, str | None]:
    raw = filter_json.strip()
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"Invalid filter JSON: {e}"
    if not isinstance(parsed, dict):
        return None, "filter must be a JSON object (exact key match against payload)."
    return parsed, None


def _validate_fire_tool(*, fire: str, tool: str, tool_args: dict[str, object]) -> str | None:
    mode = fire.strip().casefold() or "prompt"
    if mode not in {"prompt", "tool"}:
        return f"Invalid fire {fire!r}: use 'prompt' or 'tool'."
    if mode != "tool":
        return None
    if not tool.strip():
        return "fire='tool' requires tool= (e.g. feishu_message_send)."
    if not tool_args:
        return "fire='tool' requires non-empty tool_args JSON object."
    return None


def _validate_event(event: str) -> str | None:
    name = event.strip()
    if not name:
        return "event cannot be empty (e.g. feishu.chat.member_added)."
    # Prefer names declared under agent channel_events/; unknown still allowed
    # (Channel may forward raw) but warn via return None — hard reject only empty.
    if name not in _KNOWN_EVENTS:
        # Soft: allow; Channel defs are source of truth for what actually fires.
        return None
    return None


def _resolve_raw_event(event: str, raw_event: str) -> str:
    """Prefer explicit raw_event; else fill from catalog→platform map."""
    explicit = raw_event.strip()
    if explicit:
        return explicit
    return _EVENT_TO_RAW.get(event.strip(), "")


def _format_trigger_document(
    *,
    trigger_name: str,
    event: str,
    description: str,
    content: str,
    filter: dict[str, object],
    source: str = "feishu",
    created_by: str = "agent",
    created_at: str = "",
    visibility: str = "silent",
    run_once: bool = False,
    fire: str = "prompt",
    tool: str = "",
    tool_args: dict[str, object] | None = None,
    raw_event: str = "",
    raw_filter: dict[str, object] | None = None,
) -> str:
    fire_mode = fire.strip().casefold() or "prompt"
    header: dict[str, object] = {
        "name": trigger_name,
        "description": description or "(no description)",
        "event": event,
        "source": source.strip().casefold() or "feishu",
        "filter": filter,
        "visibility": visibility if visibility in {"display", "silent"} else "silent",
        "run_once": bool(run_once),
        "created_by": created_by,
        "fire": fire_mode,
    }
    if created_at:
        header["created_at"] = created_at
    platform_raw = raw_event.strip()
    if platform_raw:
        header["raw_event"] = platform_raw
    if raw_filter:
        header["raw_filter"] = raw_filter
    if fire_mode == "tool":
        header["tool"] = tool.strip()
        header["tool_args"] = tool_args or {}
    dumped = yaml.safe_dump(header, allow_unicode=True, sort_keys=False, default_flow_style=False)
    body = content.strip()
    return f"---\n{dumped}---\n\n" + (f"{body}\n" if body else "")


async def _atomic_write(path: anyio.Path, content: str) -> None:
    await path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    await tmp.write_text(content, encoding="utf-8")
    await tmp.replace(path)


def _parse_header(raw: str) -> tuple[dict[str, object], str]:
    if not raw.startswith("---"):
        return {}, raw
    end = raw.find("\n---", 3)
    if end < 0:
        return {}, raw
    try:
        header = yaml.safe_load(raw[3:end]) or {}
    except Exception:
        return {}, raw
    if not isinstance(header, dict):
        return {}, raw
    body = raw[end + 4 :].lstrip("\n")
    return header, body


async def trigger_manage(
    action: str = "list",
    trigger_name: str = "",
    event: str = "",
    filter: str = "",
    description: str = "",
    content: str = "",
    source: str = "feishu",
    visibility: str = "silent",
    fire: str = "tool",
    tool: str = "",
    tool_args: str = "",
    run_once: bool = False,
    raw_event: str = "",
    raw_filter: str = "",
) -> str:
    """Create, view, list, or delete agent-package event triggers.

    Triggers live in ``triggers/<name>/TRIGGER.md`` under the **agent** root
    (parallel to ``schedules/``). Session loads them and fires when Channel
    ``POST /events`` delivers a matching catalog event.

    **Create** requires ``event`` from the Session catalog (e.g.
    ``feishu.chat.member_added`` for「有人进群」). Optional ``filter`` is a JSON
    object of exact payload matches (e.g. ``{"chat_id":"oc_xxx"}``).

    **Dual match (二者兼得)**: create also writes ``raw_event`` (Feishu native
    type). Session matches normalized ``event``+``filter`` first; if that
    misses, falls back to ``raw_event``+``raw_filter``. Omitting ``raw_event``
    auto-fills from the known catalog→platform map when available.

    **Fire modes** (same as schedules):

    - ``fire=tool`` (default for Feishu IM reminders): Session calls
      ``tool(**tool_args)`` directly — no LLM at fire time.
    - ``fire=prompt``: Session injects TRIGGER body for an LLM turn.

    Args:
        action: list | view | create | delete
        trigger_name: Directory/name under triggers/
        event: Catalog event name (required on create)
        filter: JSON object string for payload exact-match filter
        description: Short description
        content: Optional body (notes / prompt text)
        source: Envelope source filter (default feishu)
        visibility: display | silent (default silent)
        fire: prompt | tool
        tool: Tool name when fire=tool
        tool_args: JSON kwargs when fire=tool
        run_once: Delete TRIGGER after first successful fire
        raw_event: Platform-native type (optional; auto-filled when known)
        raw_filter: JSON object for raw_payload exact-match (optional)
    """
    triggers_dir = _triggers_dir()
    act = action.strip().casefold()

    if err := _validate_visibility(visibility):
        return f"[Error] {err}"
    vis = visibility.strip().casefold() or "silent"

    filt, ferr = _parse_filter(filter)
    if ferr or filt is None:
        return f"[Error] {ferr}"

    raw_filt, rferr = _parse_filter(raw_filter)
    if rferr or raw_filt is None:
        return f"[Error] raw_filter: {rferr}"

    if act == "list":
        if not await triggers_dir.exists():
            return "No triggers found."
        entries: list[str] = []
        async for task_dir in triggers_dir.iterdir():
            if not await task_dir.is_dir() or task_dir.name.startswith("."):
                continue
            task_md = task_dir / "TRIGGER.md"
            if not await task_md.exists():
                continue
            raw = await task_md.read_text(encoding="utf-8", errors="replace")
            header, _body = _parse_header(raw)
            name = header.get("name") or task_dir.name
            desc = header.get("description") or "(no description)"
            ev = header.get("event") or "(no event)"
            tags: list[str] = []
            if header.get("created_by") == "agent":
                tags.append("agent")
            if header.get("run_once") in (True, "true", "True", "yes", "1"):
                tags.append("once")
            if str(header.get("fire") or "").casefold() == "tool":
                tags.append(f"tool:{header.get('tool') or '?'}")
            if header.get("raw_event"):
                tags.append(f"raw:{header.get('raw_event')}")
            tag = f" [{', '.join(tags)}]" if tags else ""
            entries.append(f"- {name} [{ev}]{tag}: {desc}")
        return "Triggers:\n" + "\n".join(sorted(entries)) if entries else "No triggers found."

    if act == "view":
        if err := _validate_trigger_name(trigger_name):
            return f"[Error] {err}"
        task_md = triggers_dir / trigger_name / "TRIGGER.md"
        if not await task_md.exists():
            return f"[Error] Trigger not found: {trigger_name!r}"
        return await task_md.read_text(encoding="utf-8", errors="replace")

    if act == "delete":
        if err := _validate_trigger_name(trigger_name):
            return f"[Error] {err}"
        task_dir = triggers_dir / trigger_name
        task_md = task_dir / "TRIGGER.md"
        if not await task_md.exists():
            return f"[Error] Trigger not found: {trigger_name!r}"
        await task_md.unlink()
        with suppress(OSError):
            await task_dir.rmdir()
        return f"Deleted trigger {trigger_name!r}."

    if act == "create":
        parsed_args, aerr = _parse_tool_args(tool_args)
        if aerr or parsed_args is None:
            return f"[Error] {aerr}"
        fire_mode = fire.strip().casefold() or "tool"
        if ferr := _validate_fire_tool(fire=fire_mode, tool=tool, tool_args=parsed_args):
            return f"[Error] {ferr}"
        if err := _validate_trigger_name(trigger_name):
            return f"[Error] {err}"
        if err := _validate_event(event):
            return f"[Error] {err}"
        if fire_mode == "tool" and tool.strip() == "feishu_message_send" and not parsed_args.get("receive_id"):
            return (
                "[Error] feishu_message_send tool_args must include receive_id "
                "(chat_id or open_id from <feishu_context>)."
            )
        task_dir = triggers_dir / trigger_name
        if await task_dir.exists():
            return f"[Error] Trigger already exists: {trigger_name!r}."

        resolved_raw = _resolve_raw_event(event, raw_event)
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        await _atomic_write(
            task_dir / "TRIGGER.md",
            _format_trigger_document(
                trigger_name=trigger_name,
                event=event.strip(),
                description=description,
                content=content,
                filter=filt,
                source=source,
                created_at=now,
                visibility=vis,
                run_once=bool(run_once),
                fire=fire_mode,
                tool=tool,
                tool_args=parsed_args,
                raw_event=resolved_raw,
                raw_filter=raw_filt or None,
            ),
        )
        raw_note = f" raw_event={resolved_raw!r}" if resolved_raw else ""
        return (
            f"Created trigger {trigger_name!r} event={event.strip()!r}{raw_note} "
            f"fire={fire_mode!r} filter={json.dumps(filt, ensure_ascii=False)}"
        )

    return f"[Error] Unknown action {action!r}: use list|view|create|delete."
