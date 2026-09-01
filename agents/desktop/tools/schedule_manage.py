"""Manage workspace scheduled tasks (schedules/<name>/TASK.md).

Workspace-layer tool: creates/updates files under the current session workspace.
Session's ``ScheduleRegistry`` loads those files and runs them on cron.
"""

from __future__ import annotations

import json
import os
import re
from contextlib import suppress
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import _runtime_paths as _paths
import anyio
import yaml
from croniter import croniter


def _schedules_dir() -> anyio.Path:
    # Schedules are session/user data under the workspace (刻意为之: they belong to
    # the user workspace, not the agent pack — the Gateway runs many Sessions in
    # one process and Feishu gives each open_id its own, so putting them in the
    # agent pack would make everyone share one set of scheduled tasks).
    return _paths.resolve_workspace() / "schedules"


def _validate_schedule_name(schedule_name: str) -> str | None:
    if not schedule_name.strip():
        return "Invalid schedule name: name cannot be empty."
    if "/" in schedule_name or "\\" in schedule_name:
        return f"Invalid schedule name {schedule_name!r}: must not contain path separators."
    if ".." in schedule_name:
        return f"Invalid schedule name {schedule_name!r}: must not contain '..'."
    if "\x00" in schedule_name:
        return f"Invalid schedule name {schedule_name!r}: must not contain null characters."
    if not re.fullmatch(r"[A-Za-z0-9_-]+", schedule_name):
        return f"Invalid schedule name {schedule_name!r}: only letters, digits, hyphens, and underscores are allowed."
    return None


def _validate_cron(cron: str) -> str | None:
    if not cron.strip():
        return "Invalid cron: expression cannot be empty."
    try:
        croniter(cron)
    except Exception as e:  # croniter raises assorted error types
        return f"Invalid cron expression {cron!r}: {e}"
    return None


def _validate_visibility(visibility: str) -> str | None:
    v = visibility.strip().casefold()
    if v not in {"", "display", "silent"}:
        return f"Invalid visibility {visibility!r}: use 'display' or 'silent'."
    return None


def _schedule_tz() -> ZoneInfo | None:
    """Resolve the zone cron fields are interpreted in, exactly as Session does.

    刻意为之: this mirrors ``ScheduleRegistry._schedule_tz`` rather than importing
    it. Workspace tools are loaded standalone by the tool loader and must not
    depend on psi_agent internals; the contract that has to match is the env var
    (``TZ``) and the fallback (``None`` → machine-local naive clock), not the code.
    """
    name = os.environ.get("TZ", "").strip()
    if not name:
        return None
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError, ValueError:
        return None


def _now_local() -> datetime:
    """Now as a naive value on the same clock ``once_at`` / cron fields are read on.

    刻意为之: ``datetime.now()`` alone was wrong here. Session fires cron against
    ``datetime.now(ZoneInfo(TZ))`` (``ScheduleRegistry._seconds_until_next``),
    while this tool validated "is it in the future?" against the *bare machine*
    clock. On the 214 deployment — a UTC base image with ``TZ=Asia/Shanghai`` —
    those two clocks sit 8 hours apart, so a moment already past in Beijing but
    still ahead in UTC passed validation, got written as a bare
    ``minute hour day month *`` cron, and was then scheduled by Session for that
    date *next year*: a reminder that silently never arrives.
    """
    tz = _schedule_tz()
    return datetime.now(tz).replace(tzinfo=None) if tz is not None else datetime.now()


def _parse_once_at(once_at: str) -> tuple[datetime | None, str | None]:
    """Parse once_at into a naive local datetime. Returns (dt, error)."""
    raw = once_at.strip()
    if not raw:
        return None, "once_at cannot be empty."
    dt: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            break
        except ValueError:
            continue
    if dt is None:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None, (f"Invalid once_at {once_at!r}: use 'YYYY-MM-DD HH:MM' (local time) or an ISO-8601 datetime.")
        if parsed.tzinfo is not None:
            # Convert into the schedule zone, not the machine's — astimezone()
            # with no argument would reintroduce the UTC/Beijing skew above.
            tz = _schedule_tz()
            parsed = parsed.astimezone(tz) if tz is not None else parsed.astimezone()
            dt = parsed.replace(tzinfo=None)
        else:
            dt = parsed
    now = _now_local()
    if dt <= now:
        return None, (
            f"once_at {once_at!r} is not in the future — it is {dt:%Y-%m-%d %H:%M} "
            f"but the current schedule-zone time is {now:%Y-%m-%d %H:%M} "
            f"({os.environ.get('TZ', '').strip() or 'machine-local'})."
        )
    return dt, None


_WEEKDAY_ZH = ("一", "二", "三", "四", "五", "六", "日")


def _describe_instant(dt: datetime) -> str:
    """``2026-08-03 20:30 Monday 周一`` — the weekday spelled out, not implied."""
    return f"{dt:%Y-%m-%d %H:%M} {dt:%A} 周{_WEEKDAY_ZH[dt.weekday()]}"


def _cron_from_once_at(dt: datetime) -> str:
    """5-field cron for that local minute (fires annually without run_once cleanup)."""
    return f"{dt.minute} {dt.hour} {dt.day} {dt.month} *"


def _once_at_requires_tool_fire(*, fire: str, tool: str, tool_args: dict[str, object]) -> str | None:
    """one-shot (once_at) must be fire=tool so Session does not depend on LLM at fire time."""
    mode = fire.strip().casefold() or "prompt"
    if mode != "tool":
        return (
            "one-shot (once_at) requires fire='tool' + tool= + tool_args JSON in the "
            "same create call. Do not use fire=prompt or embed feishu_message_send in "
            "content. Read skills/feishu-schedule-message/SKILL.md, then create once."
        )
    return _validate_fire_tool(fire=mode, tool=tool, tool_args=tool_args)


def _parse_tool_args(tool_args: str) -> tuple[dict[str, object] | None, str | None]:
    raw = tool_args.strip()
    if not raw:
        return {}, None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        return None, f"tool_args must be a JSON object string: {e}"
    if not isinstance(parsed, dict):
        return None, "tool_args must be a JSON object (dict), not a list/string."
    return parsed, None


def _validate_fire_tool(*, fire: str, tool: str, tool_args: dict[str, object]) -> str | None:
    mode = fire.strip().casefold() or "prompt"
    if mode not in {"prompt", "tool"}:
        return f"Invalid fire {fire!r}: use 'prompt' or 'tool'."
    if mode != "tool":
        return None
    if not tool.strip():
        return "fire='tool' requires tool= (e.g. feishu_message_send)."
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", tool.strip()):
        return f"Invalid tool name {tool!r}."
    # Feishu IM reminders: require the usual keys when targeting that tool.
    if tool.strip() == "feishu_message_send":
        rid = tool_args.get("receive_id")
        text = tool_args.get("text")
        if not isinstance(rid, str) or not rid.strip():
            return "feishu_message_send tool_args need non-empty string receive_id."
        if not isinstance(text, str) or not text.strip():
            return "feishu_message_send tool_args need non-empty string text."
        lowered = rid.casefold()
        if lowered in {"oc_xxx", "ou_xxx"} or "replace" in lowered or rid.startswith("<"):
            return "tool_args.receive_id still looks like a placeholder; use real chat_id/open_id."
    return None


def _parse_header(content: str) -> tuple[dict[str, object], str]:
    """Parse the YAML front matter the same way the schedule registry does."""
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return {}, content
    try:
        header = yaml.safe_load(match.group(1))
    except yaml.YAMLError:
        return {}, content
    if not isinstance(header, dict):
        return {}, content
    return header, content[match.end() :]


def _format_task_document(
    *,
    schedule_name: str,
    cron: str,
    description: str,
    content: str,
    created_by: str = "agent",
    created_at: str = "",
    updated_at: str = "",
    visibility: str = "display",
    run_once: bool = False,
    fire: str = "prompt",
    tool: str = "",
    tool_args: dict[str, object] | None = None,
) -> str:
    fire_mode = fire.strip().casefold() or "prompt"
    header: dict[str, object] = {
        "name": schedule_name,
        "description": description or "(no description)",
        "cron": cron,
        "visibility": visibility if visibility in {"display", "silent"} else "display",
        "run_once": bool(run_once),
        "created_by": created_by,
        "fire": fire_mode,
    }
    if created_at:
        header["created_at"] = created_at
    if updated_at:
        header["updated_at"] = updated_at
    if fire_mode == "tool":
        header["tool"] = tool.strip()
        header["tool_args"] = tool_args or {}
    dumped = yaml.safe_dump(header, allow_unicode=True, sort_keys=False, default_flow_style=False)
    # Force double-quoted cron so spaces / * / are unambiguous across PyYAML versions.
    if "cron:" in dumped and cron:
        lines = []
        for line in dumped.splitlines(keepends=True):
            if line.startswith("cron:"):
                lines.append(f'cron: "{cron}"\n')
            else:
                lines.append(line)
        dumped = "".join(lines)
    body = content.strip()
    return f"---\n{dumped}---\n\n" + (f"{body}\n" if body else "")


async def _atomic_write(path: anyio.Path, content: str) -> None:
    await path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f"{path.name}.tmp"
    await tmp.write_text(content, encoding="utf-8")
    # replace(), not rename() — os.rename can't overwrite an existing file on Windows.
    await tmp.replace(path)


async def schedule_manage(
    action: str = "list",
    schedule_name: str = "",
    cron: str = "",
    description: str = "",
    content: str = "",
    once_at: str = "",
    visibility: str = "display",
    fire: str = "prompt",
    tool: str = "",
    tool_args: str = "",
) -> str:
    """Create, patch, view, list, or delete workspace scheduled tasks.

    Scheduled tasks live in ``schedules/<name>/TASK.md``. Session loads them and
    fires on cron. Use either:

    - **Recurring**: ``action=create`` + ``cron`` (5-field cron), ``run_once: false``.
    - **One-shot**: ``action=create`` + ``once_at`` ('YYYY-MM-DD HH:MM' local time);
      writes a matching cron and ``run_once: true`` so Session deletes the task
      after the first successful fire. **Must** also pass ``fire=tool`` + ``tool`` +
      ``tool_args`` in the same call (prompt-fire one-shots are rejected).

    **Fire modes** (YAML ``fire``):

    - ``fire=prompt`` (default for recurring ``cron`` only): Session injects TASK body;
      LLM may call tools. Not allowed for ``once_at``.
    - ``fire=tool``: Session calls ``tool(**tool_args)`` directly at fire time —
      no LLM. Required for one-shot and for Feishu IM reminders.

    Do not pass both ``cron`` and ``once_at`` on create.

    Args:
        action: One of "list", "view", "create", "patch", or "delete".
        schedule_name: Schedule directory name for view/create/patch/delete.
        cron: Cron expression for recurring create, or to change it on patch.
        description: One-line description used on create/patch.
        content: Optional TASK.md body notes (ignored for tool execution).
        once_at: One-shot fire time — 'YYYY-MM-DD HH:MM' (local) or ISO-8601.
        visibility: ``display`` (default; may surface in chat) or ``silent``.
        fire: ``prompt`` (LLM turn) or ``tool`` (direct tool call).
        tool: Tool name when ``fire=tool`` (e.g. ``feishu_message_send``).
        tool_args: JSON object string of kwargs when ``fire=tool``.

    Returns:
        A result message, list output, or TASK.md content.
    """
    schedules_dir = _schedules_dir()
    action = action.strip().lower()

    if err := _validate_visibility(visibility):
        return f"[Error] {err}"
    vis = visibility.strip().casefold() or "display"

    parsed_args, aerr = _parse_tool_args(tool_args)
    if aerr or parsed_args is None:
        return f"[Error] {aerr}"
    fire_mode = fire.strip().casefold() or "prompt"
    if ferr := _validate_fire_tool(fire=fire_mode, tool=tool, tool_args=parsed_args):
        return f"[Error] {ferr}"

    if action == "list":
        if not await schedules_dir.exists():
            return "No schedules found."

        entries: list[str] = []
        async for task_dir in schedules_dir.iterdir():
            if not await task_dir.is_dir() or task_dir.name.startswith("."):
                continue
            task_md = task_dir / "TASK.md"
            if not await task_md.exists():
                continue

            raw = await task_md.read_text(encoding="utf-8", errors="replace")
            header, _body = _parse_header(raw)
            name = header.get("name") or task_dir.name
            desc = header.get("description") or "(no description)"
            cron_expr = header.get("cron") or "(no cron)"
            tags: list[str] = []
            if header.get("created_by") == "agent":
                tags.append("agent")
            if header.get("run_once") in (True, "true", "True", "yes", "1"):
                tags.append("once")
            if str(header.get("fire") or "").casefold() == "tool":
                tags.append(f"tool:{header.get('tool') or '?'}")
            tag = f" [{', '.join(tags)}]" if tags else ""
            entries.append(f"- {name} [{cron_expr}]{tag}: {desc}")

        return "Schedules:\n" + "\n".join(sorted(entries)) if entries else "No schedules found."

    if action == "view":
        if err := _validate_schedule_name(schedule_name):
            return f"[Error] {err}"
        task_md = schedules_dir / schedule_name / "TASK.md"
        if not await task_md.exists():
            return f"[Error] Schedule not found: {schedule_name!r}"
        return await task_md.read_text(encoding="utf-8", errors="replace")

    if action == "create":
        if err := _validate_schedule_name(schedule_name):
            return f"[Error] {err}"

        once_raw = once_at.strip()
        cron_raw = cron.strip()
        if once_raw and cron_raw:
            return "[Error] Pass either once_at (one-shot) or cron (recurring), not both."

        run_once = False
        fires_at: datetime | None = None
        if once_raw:
            dt, perr = _parse_once_at(once_raw)
            if perr or dt is None:
                return f"[Error] {perr}"
            cron_raw = _cron_from_once_at(dt)
            run_once = True
            fires_at = dt
        elif not cron_raw:
            return "[Error] Provide cron (recurring) or once_at (one-shot) when creating."

        if err := _validate_cron(cron_raw):
            return f"[Error] {err}"
        # 刻意为之: once_at always fire=tool; reject prompt + prose/pseudocode creates.
        if run_once:
            if cerr := _once_at_requires_tool_fire(fire=fire_mode, tool=tool, tool_args=parsed_args):
                return f"[Error] {cerr}"
        elif "feishu_message_send" in content and fire_mode != "tool":
            return (
                "[Error] feishu_message_send belongs in fire='tool' + tool_args, "
                "not in content with fire=prompt. "
                "Read skills/feishu-schedule-message/SKILL.md."
            )
        task_dir = schedules_dir / schedule_name
        if await task_dir.exists():
            return f"[Error] Schedule already exists: {schedule_name!r}. Use action='patch' to update it."

        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        await _atomic_write(
            task_dir / "TASK.md",
            _format_task_document(
                schedule_name=schedule_name,
                cron=cron_raw,
                description=description,
                content=content,
                created_at=now,
                visibility=vis,
                run_once=run_once,
                fire=fire_mode,
                tool=tool,
                tool_args=parsed_args,
            ),
        )
        kind = "one-shot" if run_once else "recurring"
        extra = f", fire={fire_mode!r}"
        if fire_mode == "tool":
            extra += f", tool={tool.strip()!r}"
        if fires_at is not None:
            # Echo the resolved instant with its weekday: the caller supplied an
            # absolute date it had to derive from something like "周一晚上", and
            # this is the one place a wrong derivation is still cheap to catch.
            extra += f", fires at {_describe_instant(fires_at)}"
        return f"Schedule created: {schedule_name!r} ({kind}, cron: {cron_raw!r}, visibility: {vis!r}{extra})"

    if action == "patch":
        if err := _validate_schedule_name(schedule_name):
            return f"[Error] {err}"
        if once_at.strip():
            return "[Error] once_at is only valid on create; delete and recreate for a new one-shot time."
        task_md = schedules_dir / schedule_name / "TASK.md"
        if not await task_md.exists():
            return f"[Error] Schedule not found: {schedule_name!r}"

        raw = await task_md.read_text(encoding="utf-8", errors="replace")
        header, body = _parse_header(raw)

        next_cron = cron.strip() or str(header.get("cron") or "")
        if err := _validate_cron(next_cron):
            return f"[Error] {err}"

        prev_once = header.get("run_once", False)
        if isinstance(prev_once, str):
            run_once = prev_once.strip().casefold() in {"1", "true", "yes", "on"}
        else:
            run_once = bool(prev_once)

        next_vis = vis if visibility.strip() else str(header.get("visibility") or "display")
        if next_vis not in {"display", "silent"}:
            next_vis = "display"

        next_fire = fire_mode if fire.strip() else str(header.get("fire") or "prompt")
        next_tool = tool.strip() if tool.strip() else str(header.get("tool") or "")
        if tool_args.strip():
            next_args = parsed_args
        else:
            prev_args = header.get("tool_args", {})
            next_args = dict(prev_args) if isinstance(prev_args, dict) else {}
        if ferr := _validate_fire_tool(fire=next_fire, tool=next_tool, tool_args=next_args):
            return f"[Error] {ferr}"

        await _atomic_write(
            task_md,
            _format_task_document(
                schedule_name=str(header.get("name") or schedule_name),
                cron=next_cron,
                description=description or str(header.get("description") or ""),
                content=content.strip() or body.strip(),
                created_by=str(header.get("created_by") or "agent"),
                created_at=str(header.get("created_at") or ""),
                updated_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                visibility=next_vis,
                run_once=run_once,
                fire=next_fire,
                tool=next_tool,
                tool_args=next_args,
            ),
        )
        return f"Schedule patched: {schedule_name!r} (cron: {next_cron!r}, fire: {next_fire!r})"

    if action == "delete":
        if err := _validate_schedule_name(schedule_name):
            return f"[Error] {err}"
        task_dir = schedules_dir / schedule_name
        task_md = task_dir / "TASK.md"
        if not await task_md.exists():
            return f"[Error] Schedule not found: {schedule_name!r}"

        await task_md.unlink()
        # Remove the now-empty task directory; ignore if other files remain.
        with suppress(OSError):
            await task_dir.rmdir()
        return f"Schedule deleted: {schedule_name!r}"

    return "[Error] Unknown action. Use 'list', 'view', 'create', 'patch', or 'delete'."
