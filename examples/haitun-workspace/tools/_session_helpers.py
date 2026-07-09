"""Session discovery helpers — histories, background registry, optional Gateway."""

from __future__ import annotations

import json
import re
import shlex
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import _background_process_registry as _bg
import _subagent_helpers as _sub
import anyio


def _argv_flag(argv: list[str], flag: str) -> str:
    try:
        index = argv.index(flag)
    except ValueError:
        return ""
    if index + 1 >= len(argv):
        return ""
    return argv[index + 1].strip()


def current_session_id() -> str:
    """Session id from this process argv, when running as ``psi-agent session``."""
    if "session" not in sys.argv:
        return ""
    return _argv_flag(sys.argv, "--session-id")


def _session_id_from_process_id(process_id: str) -> str:
    process_id = process_id.strip()
    for suffix in ("-session", "-ai"):
        if process_id.endswith(suffix):
            return process_id[: -len(suffix)]
    return process_id


def _session_id_from_command(command: str) -> str:
    command = command.strip()
    if not command or "session" not in command:
        return ""
    try:
        tokens = shlex.split(command, posix=(sys.platform != "win32"))
    except ValueError:
        return ""
    sid = _argv_flag(tokens, "--session-id")
    return sid


def _infer_background_session_id(row: dict[str, Any]) -> str:
    process_id = str(row.get("process_id", "")).strip()
    sid = _session_id_from_process_id(process_id)
    if sid and sid != process_id:
        return sid
    command = str(row.get("command", ""))
    sid = _session_id_from_command(command)
    if sid:
        return sid
    return process_id


async def _count_jsonl_messages(path: anyio.Path) -> int:
    count = 0
    try:
        async with await path.open(encoding="utf-8") as handle:
            async for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(msg, dict) and msg.get("role"):
                    count += 1
    except OSError:
        return 0
    return count


async def _scan_history_sessions(workspace: anyio.Path) -> dict[str, dict[str, Any]]:
    histories_dir = workspace / "histories"
    rows: dict[str, dict[str, Any]] = {}
    if not await histories_dir.exists():
        return rows
    async for entry in histories_dir.glob("*.jsonl"):
        session_id = entry.name.removesuffix(".jsonl").strip()
        if not session_id:
            continue
        try:
            stat = await entry.stat()
            mtime = datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat()
        except OSError:
            mtime = ""
        rows[session_id] = {
            "session_id": session_id,
            "sources": ["history"],
            "running": False,
            "history_path": str(entry),
            "history_mtime": mtime,
            "message_count": await _count_jsonl_messages(entry),
            "background_processes": [],
            "gateway": None,
            "title": "",
            "is_current": session_id == current_session_id(),
        }
    return rows


def _ensure_session_row(rows: dict[str, dict[str, Any]], session_id: str) -> dict[str, Any]:
    row = rows.get(session_id)
    if row is not None:
        return row
    row = {
        "session_id": session_id,
        "sources": [],
        "running": False,
        "history_path": "",
        "history_mtime": "",
        "message_count": 0,
        "background_processes": [],
        "gateway": None,
        "title": "",
        "is_current": session_id == current_session_id(),
    }
    rows[session_id] = row
    return row


def _add_source(row: dict[str, Any], source: str) -> None:
    sources = row.setdefault("sources", [])
    if not isinstance(sources, list):
        row["sources"] = [str(sources)]
        sources = row["sources"]
    if source not in sources:
        sources.append(source)


async def _merge_background_sessions(
    rows: dict[str, dict[str, Any]],
    *,
    workspace_raw: str,
) -> None:
    bg = await _bg.list_processes(workspace_raw=workspace_raw)
    processes = bg.get("processes")
    if not isinstance(processes, list):
        return
    for proc in processes:
        if not isinstance(proc, dict):
            continue
        session_id = _infer_background_session_id(proc)
        if not session_id:
            continue
        row = _ensure_session_row(rows, session_id)
        _add_source(row, "background")
        alive = bool(proc.get("alive"))
        if alive:
            row["running"] = True
        bg_entry = {
            "process_id": str(proc.get("process_id", "")),
            "pid": proc.get("pid", 0),
            "alive": alive,
            "command": str(proc.get("command", "")),
        }
        processes_list = row.setdefault("background_processes", [])
        if isinstance(processes_list, list):
            processes_list.append(bg_entry)


async def _merge_gateway_sessions(
    rows: dict[str, dict[str, Any]],
    *,
    workspace: Path,
) -> str:
    gateway_url = await _sub.resolve_gateway_url(workspace)
    if not gateway_url:
        return ""

    titles: dict[str, str] = {}
    try:
        raw_titles = await _sub._fetch_gateway_json(f"{gateway_url}/titles")
        if isinstance(raw_titles, dict):
            titles = {str(k): str(v) for k, v in raw_titles.items()}
    except Exception:
        pass

    try:
        raw_sessions = await _sub._fetch_gateway_json(f"{gateway_url}/sessions")
    except Exception:
        return gateway_url

    if not isinstance(raw_sessions, list):
        return gateway_url

    for item in raw_sessions:
        if not isinstance(item, dict):
            continue
        session_id = str(item.get("id", "")).strip()
        if not session_id:
            continue
        ws = str(item.get("workspace", "")).strip()
        if ws and not _sub._workspaces_match(ws, workspace):
            continue
        row = _ensure_session_row(rows, session_id)
        _add_source(row, "gateway")
        row["running"] = True
        row["gateway"] = {
            "ai_id": str(item.get("ai_id", "")),
            "workspace": ws,
            "channel_socket": str(item.get("channel_socket", "")),
        }
        title = titles.get(session_id, "")
        if title:
            row["title"] = title

    return gateway_url


async def _collect_session_rows(
    *,
    workspace_raw: str = "",
    include_gateway: bool = True,
) -> tuple[anyio.Path, str, dict[str, dict[str, Any]]]:
    workspace = _bg.resolve_workspace(workspace_raw)
    workspace_path = Path(str(workspace))

    rows = await _scan_history_sessions(workspace)
    await _merge_background_sessions(rows, workspace_raw=workspace_raw)

    gateway_url = ""
    if include_gateway:
        gateway_url = await _merge_gateway_sessions(rows, workspace=workspace_path)

    return workspace, gateway_url, rows


def resolve_session_id(session_id: str) -> str:
    """Use explicit id, else current process session id."""
    sid = session_id.strip()
    if sid:
        return sid
    return current_session_id()


def _history_path(workspace: anyio.Path, session_id: str) -> anyio.Path:
    return workspace / "histories" / f"{session_id}.jsonl"


def _normalize_history_message(msg: dict[str, Any], *, include_tool_messages: bool) -> dict[str, Any] | None:
    role = str(msg.get("role", "")).strip()
    if role == "tool":
        if not include_tool_messages:
            return None
        content = msg.get("content", "")
        return {
            "role": role,
            "name": str(msg.get("name", "")),
            "content": content if isinstance(content, str) else str(content),
        }
    if role not in ("user", "assistant", "system"):
        return None
    content = msg.get("content", "")
    if content is None:
        content = ""
    if not isinstance(content, str):
        content = str(content)
    row: dict[str, Any] = {"role": role, "content": content}
    reasoning = msg.get("reasoning", "")
    if isinstance(reasoning, str) and reasoning.strip():
        row["reasoning"] = reasoning
    tool_calls = msg.get("tool_calls")
    if include_tool_messages and isinstance(tool_calls, list) and tool_calls:
        row["tool_calls"] = tool_calls
    if role in ("user", "assistant") or include_tool_messages:
        return row
    return None


async def _read_history_messages(
    path: anyio.Path,
    *,
    limit: int,
    include_tool_messages: bool,
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    try:
        async with await path.open(encoding="utf-8") as handle:
            async for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                normalized = _normalize_history_message(raw, include_tool_messages=include_tool_messages)
                if normalized is not None:
                    messages.append(normalized)
    except OSError:
        return []
    if limit > 0 and len(messages) > limit:
        return messages[-limit:]
    return messages


async def get_session_status(
    *,
    session_id: str = "",
    workspace_raw: str = "",
    include_gateway: bool = True,
) -> dict[str, Any]:
    sid = resolve_session_id(session_id)
    if not sid:
        return {
            "ok": False,
            "message": "session_id is required when not running inside a session process",
            "session_id": "",
        }

    workspace, gateway_url, rows = await _collect_session_rows(
        workspace_raw=workspace_raw,
        include_gateway=include_gateway,
    )
    row = rows.get(sid)
    if row is None:
        return {
            "ok": False,
            "message": f"session {sid!r} not found in workspace histories, background registry, or Gateway",
            "session_id": sid,
            "workspace": str(workspace),
            "gateway_url": gateway_url,
        }

    channel_socket = ""
    gateway_info = row.get("gateway")
    if isinstance(gateway_info, dict):
        channel_socket = str(gateway_info.get("channel_socket", "")).strip()
    if not channel_socket:
        for proc in row.get("background_processes", []):
            if not isinstance(proc, dict):
                continue
            command = str(proc.get("command", ""))
            if "--channel-socket" in command:
                try:
                    tokens = shlex.split(command, posix=(sys.platform != "win32"))
                except ValueError:
                    tokens = []
                channel_socket = _argv_flag(tokens, "--channel-socket")
                if channel_socket:
                    break

    session = dict(row)
    if channel_socket:
        session["channel_socket"] = channel_socket

    return {
        "ok": True,
        "workspace": str(workspace),
        "gateway_url": gateway_url,
        "current_session_id": current_session_id(),
        "session_id": sid,
        "session": session,
    }


async def get_session_history(
    *,
    session_id: str = "",
    workspace_raw: str = "",
    limit: int = 50,
    include_tool_messages: bool = False,
    include_gateway: bool = True,
) -> dict[str, Any]:
    sid = resolve_session_id(session_id)
    if not sid:
        return {
            "ok": False,
            "message": "session_id is required when not running inside a session process",
            "session_id": "",
            "messages": [],
        }

    limit = max(1, min(500, int(limit)))
    workspace, gateway_url, rows = await _collect_session_rows(
        workspace_raw=workspace_raw,
        include_gateway=include_gateway,
    )
    path = _history_path(workspace, sid)
    messages: list[dict[str, Any]] = []
    history_source = ""

    if await path.exists():
        history_source = "history"
        messages = await _read_history_messages(
            path,
            limit=limit,
            include_tool_messages=include_tool_messages,
        )
    elif include_gateway and gateway_url:
        try:
            raw = await _sub._fetch_gateway_json(f"{gateway_url.rstrip('/')}/sessions/{sid}/history")
        except Exception as exc:
            return {
                "ok": False,
                "message": f"failed to read history for session {sid!r}: {exc}",
                "session_id": sid,
                "workspace": str(workspace),
                "gateway_url": gateway_url,
                "messages": [],
            }
        if isinstance(raw, list):
            history_source = "gateway"
            for item in raw:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role", "")).strip()
                text = item.get("text", item.get("content", ""))
                if role in ("user", "assistant") and isinstance(text, str) and text:
                    messages.append({"role": role, "content": text})
            if limit > 0 and len(messages) > limit:
                messages = messages[-limit:]

    row = rows.get(sid, {})
    if not messages and sid not in rows:
        return {
            "ok": False,
            "message": f"no history found for session {sid!r}",
            "session_id": sid,
            "workspace": str(workspace),
            "gateway_url": gateway_url,
            "history_path": str(path),
            "messages": [],
        }

    if not history_source and await path.exists():
        history_source = "history"

    return {
        "ok": True,
        "session_id": sid,
        "workspace": str(workspace),
        "gateway_url": gateway_url,
        "history_path": str(path) if history_source == "history" else "",
        "history_source": history_source,
        "title": str(row.get("title", "")) if isinstance(row, dict) else "",
        "running": bool(row.get("running")) if isinstance(row, dict) else False,
        "count": len(messages),
        "messages": messages,
    }


TASK_CATEGORIES: tuple[str, ...] = (
    "subagent",
    "github",
    "gateway",
    "background",
    "untitled",
    "recent",
    "all",
)

_SNIPPET_MAX_CHARS = 200
_MAX_SNIPPETS_PER_SESSION = 3
_RECENT_DAYS = 7

_GITHUB_HINTS = ("github", "gh pr", "pull request", "gh repo")


def _truncate(text: str, max_chars: int = _SNIPPET_MAX_CHARS) -> str:
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1] + "…"


def _searchable_message_text(msg: dict[str, Any]) -> str:
    role = str(msg.get("role", "")).strip()
    if role not in ("user", "assistant"):
        return ""
    content = msg.get("content", "")
    if not isinstance(content, str):
        content = str(content)
    return content


async def _recent_user_texts(path: anyio.Path, *, limit: int = 5) -> list[str]:
    texts: list[str] = []
    try:
        async with await path.open(encoding="utf-8") as handle:
            async for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict) or raw.get("role") != "user":
                    continue
                content = raw.get("content", "")
                if isinstance(content, str) and content.strip():
                    texts.append(content.strip())
    except OSError:
        return texts
    if len(texts) > limit:
        return texts[-limit:]
    return texts


def _parse_mtime_iso(value: str) -> datetime | None:
    raw = value.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _infer_task_categories(row: dict[str, Any], *, user_texts: list[str]) -> list[str]:
    categories: list[str] = []
    session_id = str(row.get("session_id", "")).strip()
    title = str(row.get("title", "")).strip()
    sources = row.get("sources")
    source_list = sources if isinstance(sources, list) else []

    if session_id.startswith("sub-"):
        categories.append("subagent")

    processes = row.get("background_processes")
    alive_background = False
    if isinstance(processes, list):
        alive_background = any(isinstance(proc, dict) and proc.get("alive") for proc in processes)
    if alive_background:
        categories.append("background")

    if row.get("gateway") is not None or "gateway" in source_list:
        categories.append("gateway")

    blob = f"{title}\n" + "\n".join(user_texts)
    blob_lower = blob.lower()
    if any(hint in blob_lower for hint in _GITHUB_HINTS):
        categories.append("github")

    if not title and int(row.get("message_count", 0) or 0) > 0:
        categories.append("untitled")

    mtime = _parse_mtime_iso(str(row.get("history_mtime", "")))
    if mtime is not None and mtime >= datetime.now(tz=UTC) - timedelta(days=_RECENT_DAYS):
        categories.append("recent")

    return categories


async def _keyword_search_file(
    path: anyio.Path,
    *,
    query: str,
    session_row: dict[str, Any],
) -> dict[str, Any] | None:
    needle = query.casefold()
    if not needle:
        return None

    snippets: list[dict[str, Any]] = []
    message_count = 0
    hit_count = 0

    try:
        async with await path.open(encoding="utf-8") as handle:
            async for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                searchable = _searchable_message_text(raw)
                if not searchable:
                    continue
                message_count += 1
                if needle not in searchable.casefold():
                    continue
                hit_count += 1
                if len(snippets) < _MAX_SNIPPETS_PER_SESSION:
                    match = re.search(re.escape(query), searchable, flags=re.IGNORECASE)
                    span = [match.start(), match.end()] if match else []
                    snippets.append(
                        {
                            "role": str(raw.get("role", "")),
                            "text": _truncate(searchable),
                            "match_span": span,
                        }
                    )
    except OSError:
        return None

    if hit_count == 0:
        return None

    score = hit_count / max(1, message_count)
    return {
        "session_id": session_row.get("session_id", path.name.removesuffix(".jsonl")),
        "title": session_row.get("title", ""),
        "running": bool(session_row.get("running")),
        "message_count": message_count or session_row.get("message_count", 0),
        "history_mtime": session_row.get("history_mtime", ""),
        "score": round(score, 4),
        "hit_count": hit_count,
        "snippets": snippets,
    }


async def keyword_search_sessions(
    *,
    query: str,
    session_id: str = "",
    workspace_raw: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    query = query.strip()
    if not query:
        return {
            "ok": False,
            "message": "query must not be empty",
            "query": "",
            "hits": [],
        }

    limit = max(1, min(50, int(limit)))
    workspace, gateway_url, rows = await _collect_session_rows(
        workspace_raw=workspace_raw,
        include_gateway=True,
    )

    scope = session_id.strip()
    if scope:
        row = rows.get(scope)
        path = _history_path(workspace, scope)
        if row is None and not await path.exists():
            return {
                "ok": False,
                "message": f"session {scope!r} not found",
                "query": query,
                "session_id_scope": scope,
                "hits": [],
            }
        session_row = row or _ensure_session_row(rows, scope)
        hit = await _keyword_search_file(path, query=query, session_row=session_row)
        hits = [hit] if hit is not None else []
    else:
        hits = []
        for sid, row in rows.items():
            path = _history_path(workspace, sid)
            if not await path.exists():
                continue
            hit = await _keyword_search_file(path, query=query, session_row=row)
            if hit is not None:
                hits.append(hit)
        hits.sort(key=lambda item: (float(item.get("score", 0)), int(item.get("hit_count", 0))), reverse=True)
        hits = hits[:limit]

    return {
        "ok": True,
        "workspace": str(workspace),
        "gateway_url": gateway_url,
        "query": query,
        "session_id_scope": scope,
        "count": len(hits),
        "hits": hits,
    }


async def task_search_sessions(
    *,
    category: str,
    workspace_raw: str = "",
    limit: int = 10,
    include_gateway: bool = True,
) -> dict[str, Any]:
    category = category.strip().lower()
    if category not in TASK_CATEGORIES:
        return {
            "ok": False,
            "message": f"category must be one of: {', '.join(TASK_CATEGORIES)}",
            "category": category,
            "hits": [],
        }

    limit = max(1, min(50, int(limit)))
    workspace, gateway_url, rows = await _collect_session_rows(
        workspace_raw=workspace_raw,
        include_gateway=include_gateway,
    )

    hits: list[dict[str, Any]] = []
    for sid, row in rows.items():
        path = _history_path(workspace, sid)
        user_texts: list[str] = []
        if await path.exists():
            user_texts = await _recent_user_texts(path)
        categories = _infer_task_categories(row, user_texts=user_texts)
        if category != "all" and category not in categories:
            continue
        hits.append(
            {
                "session_id": sid,
                "title": row.get("title", ""),
                "running": bool(row.get("running")),
                "message_count": row.get("message_count", 0),
                "history_mtime": row.get("history_mtime", ""),
                "categories": categories,
                "sources": row.get("sources", []),
            }
        )

    hits.sort(key=lambda item: str(item.get("history_mtime", "")), reverse=True)
    hits = hits[:limit]

    return {
        "ok": True,
        "workspace": str(workspace),
        "gateway_url": gateway_url,
        "category": category,
        "count": len(hits),
        "hits": hits,
    }


async def list_sessions(
    *,
    workspace_raw: str = "",
    running_only: bool = False,
    include_gateway: bool = True,
) -> dict[str, Any]:
    """List sessions for a workspace (histories + background + optional Gateway)."""
    workspace, gateway_url, rows = await _collect_session_rows(
        workspace_raw=workspace_raw,
        include_gateway=include_gateway,
    )

    sessions = list(rows.values())
    if running_only:
        sessions = [row for row in sessions if row.get("running")]

    sessions.sort(
        key=lambda row: (
            str(row.get("history_mtime", "")),
            str(row.get("session_id", "")),
        ),
        reverse=True,
    )

    return {
        "ok": True,
        "workspace": str(workspace),
        "gateway_url": gateway_url,
        "current_session_id": current_session_id(),
        "count": len(sessions),
        "sessions": sessions,
    }


EXPORT_FORMATS: tuple[str, ...] = ("markdown", "json", "jsonl", "text")


async def _read_all_raw_messages(path: anyio.Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        async with await path.open(encoding="utf-8") as handle:
            async for line in handle:
                text = line.strip()
                if not text:
                    continue
                try:
                    raw = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if isinstance(raw, dict):
                    rows.append(raw)
    except OSError:
        return []
    return rows


def _resolve_output_path(workspace: anyio.Path, output_path: str) -> anyio.Path:
    raw = output_path.strip()
    if not raw:
        msg = "output_path must not be empty"
        raise ValueError(msg)
    path = anyio.Path(raw)
    if not Path(raw).is_absolute():
        path = workspace / raw
    return path


def _format_export_markdown(messages: list[dict[str, Any]]) -> str:
    """Render user/assistant turns only — no metadata, system, tools, or reasoning."""
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role", "")).strip()
        if role not in ("user", "assistant"):
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        content = content.strip()
        if not content:
            continue
        label = "User" if role == "user" else "Assistant"
        lines.extend([f"### {label}", "", content, ""])
    if not lines:
        return "\n"
    return "\n".join(lines).rstrip() + "\n"


def _format_export_text(
    messages: list[dict[str, Any]],
    *,
    include_tool_messages: bool,
) -> str:
    lines: list[str] = []
    for msg in messages:
        role = str(msg.get("role", "")).strip()
        if role == "tool" and not include_tool_messages:
            continue
        if role not in ("user", "assistant", "tool", "system"):
            continue
        content = msg.get("content", "")
        if not isinstance(content, str):
            content = str(content)
        label = role.upper()
        if role == "tool":
            name = str(msg.get("name", "")).strip()
            label = f"TOOL:{name}" if name else "TOOL"
        lines.extend([f"{label}:", content.strip(), ""])
    return "\n".join(lines).rstrip() + "\n"


def _format_export_jsonl(messages: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(msg, ensure_ascii=False) + "\n" for msg in messages)


async def export_session(
    *,
    session_id: str = "",
    output_path: str,
    export_format: str = "markdown",
    workspace_raw: str = "",
    include_tool_messages: bool = False,
    include_gateway: bool = True,
) -> dict[str, Any]:
    sid = resolve_session_id(session_id)
    if not sid:
        return {
            "ok": False,
            "message": "session_id is required when not running inside a session process",
            "session_id": "",
        }

    fmt = export_format.strip().lower() or "markdown"
    if fmt not in EXPORT_FORMATS:
        return {
            "ok": False,
            "message": f"export_format must be one of: {', '.join(EXPORT_FORMATS)}",
            "session_id": sid,
            "export_format": fmt,
        }

    try:
        out_path = _resolve_output_path(_bg.resolve_workspace(workspace_raw), output_path)
    except ValueError as exc:
        return {"ok": False, "message": str(exc), "session_id": sid}

    workspace, gateway_url, rows = await _collect_session_rows(
        workspace_raw=workspace_raw,
        include_gateway=include_gateway,
    )
    row = rows.get(sid, {})
    title = str(row.get("title", "")) if isinstance(row, dict) else ""

    history_path = _history_path(workspace, sid)
    raw_messages = await _read_all_raw_messages(history_path)

    if not raw_messages and include_gateway and gateway_url:
        hist = await get_session_history(
            session_id=sid,
            workspace_raw=workspace_raw,
            limit=500,
            include_tool_messages=include_tool_messages,
            include_gateway=True,
        )
        if hist.get("ok") and isinstance(hist.get("messages"), list):
            raw_messages = [
                {"role": m.get("role", ""), "content": m.get("content", "")}
                for m in hist["messages"]
                if isinstance(m, dict)
            ]

    if not raw_messages:
        return {
            "ok": False,
            "message": f"no history found for session {sid!r}",
            "session_id": sid,
            "workspace": str(workspace),
            "history_path": str(history_path),
        }

    if fmt == "markdown":
        dialogue_messages = await _read_history_messages(
            history_path,
            limit=0,
            include_tool_messages=False,
        )
        if not dialogue_messages and include_gateway and gateway_url:
            hist = await get_session_history(
                session_id=sid,
                workspace_raw=workspace_raw,
                limit=0,
                include_tool_messages=False,
                include_gateway=True,
            )
            if hist.get("ok") and isinstance(hist.get("messages"), list):
                dialogue_messages = [
                    m for m in hist["messages"] if isinstance(m, dict) and m.get("role") in ("user", "assistant")
                ]
        if not dialogue_messages:
            return {
                "ok": False,
                "message": f"no user/assistant dialogue found for session {sid!r}",
                "session_id": sid,
                "workspace": str(workspace),
                "history_path": str(history_path),
            }
        body = _format_export_markdown(dialogue_messages)
    elif fmt == "json":
        if include_tool_messages:
            payload = raw_messages
        else:
            payload = [m for m in raw_messages if m.get("role") in ("user", "assistant", "system")]
        body = json.dumps(
            {
                "session_id": sid,
                "title": title,
                "message_count": len(payload),
                "messages": payload,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n"
    elif fmt == "jsonl":
        body = _format_export_jsonl(raw_messages)
    else:
        body = _format_export_text(raw_messages, include_tool_messages=include_tool_messages)

    parent = out_path.parent
    if not await parent.exists():
        await parent.mkdir(parents=True, exist_ok=True)
    await out_path.write_text(body, encoding="utf-8")
    size = len(body.encode("utf-8"))

    return {
        "ok": True,
        "session_id": sid,
        "workspace": str(workspace),
        "gateway_url": gateway_url,
        "history_path": str(history_path),
        "export_format": fmt,
        "output_path": str(out_path),
        "title": title,
        "message_count": len(raw_messages),
        "bytes_written": size,
    }
