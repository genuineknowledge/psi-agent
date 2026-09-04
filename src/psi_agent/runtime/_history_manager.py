from __future__ import annotations

import json

from loguru import logger

from psi_agent._appdata import (
    appdata_history_path,
    legacy_history_path,
    resolve_appdata_root,
    resolve_history_read_path,
)
from psi_agent.session.history_display import (
    KIND_CHAT,
    compact_tool_result_for_event,
    extract_send_paths,
    is_displayable_chat_message,
    message_kind,
    strip_transfer_markers,
    wire_role,
)


def _merge_reasoning(existing: object, extra: str) -> str:
    parts: list[str] = []
    if isinstance(existing, str) and existing.strip():
        parts.append(existing.strip())
    if extra.strip():
        parts.append(extra.strip())
    return "\n".join(parts)


def _tool_calls_payload(msg: dict[str, object]) -> list[dict[str, str]]:
    """Project JSONL ``tool_calls`` to ``[{name, arguments}, …]`` for SPA tool UI.

    Session streams ``[Tool Call:…]`` only on the live SSE; JSONL keeps structured
    ``tool_calls``. History exposes them as a separate ``tools`` field — never
    stuffed into ``reasoning``.
    """
    raw = msg.get("tool_calls")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, str]] = []
    for tc in raw:
        if not isinstance(tc, dict):
            continue
        fn = tc.get("function")
        if not isinstance(fn, dict):
            continue
        name = fn.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        args = fn.get("arguments", "{}")
        if not isinstance(args, str):
            args = "{}"
        out.append({"name": name.strip(), "arguments": args})
    return out


def _extend_tools(existing: object, extra: list[dict[str, str]]) -> list[dict[str, str]]:
    base: list[dict[str, str]] = []
    if isinstance(existing, list):
        for item in existing:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            arguments = item.get("arguments")
            if isinstance(name, str) and isinstance(arguments, str):
                base.append({"name": name, "arguments": arguments})
    base.extend(extra)
    return base


def _attach_process(
    row: dict[str, object],
    *,
    reasoning: str,
    tools: list[dict[str, str]],
) -> None:
    if reasoning:
        row["reasoning"] = reasoning
    if tools:
        row["tools"] = tools


class HistoryManager:
    async def get(self, workspace: str, session_id: str, *, appdata: str = "") -> list[dict[str, object]]:
        appdata_root = appdata.strip() or await resolve_appdata_root()
        path = await resolve_history_read_path(
            appdata_root=appdata_root,
            workspace=workspace,
            session_id=session_id,
        )
        messages: list[dict[str, object]] = []
        # Tool-round thinking / tools held until the next displayable assistant.
        pending_reasoning = ""
        pending_tools: list[dict[str, str]] = []
        # Tool results (the JSONL ``role: tool`` rows) in global order.  They
        # are paired with ``tools`` after the scan: results follow their
        # assistant turn in the same order as the projected tool_calls, so the
        # k-th result belongs to the k-th tool entry across the whole session.
        tool_results: list[str] = []
        try:
            content = await path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.debug(f"No history file for session {session_id!r} at {path!r}")
            return messages
        except OSError as e:
            logger.warning(f"Failed to read history for session {session_id!r}: {e!r}")
            return messages
        for line in content.strip().split("\n"):
            if not line.strip():
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(msg, dict):
                continue

            role = wire_role(msg.get("role"))
            kind = message_kind(msg)
            reasoning_raw = msg.get("reasoning")
            reasoning = reasoning_raw.strip() if isinstance(reasoning_raw, str) and reasoning_raw.strip() else ""
            tools = _tool_calls_payload(msg)

            if not is_displayable_chat_message(msg):
                # Tool rounds: assistant + tool_calls (+ optional thinking), no
                # chat content.  ``role: tool`` rows carry the result text —
                # compacted for the wire so history responses stay small while
                # the provenance view still gets caliber/snapshot (the mock
                # envelope now fronts those fields, so head-truncated payloads
                # keep them too).
                if role == "tool":
                    raw_result = msg.get("content")
                    if isinstance(raw_result, str) and raw_result.strip():
                        tool_results.append(compact_tool_result_for_event(raw_result))
                    continue
                if role == "assistant" and kind == KIND_CHAT and (reasoning or tools):
                    if messages and messages[-1].get("role") == "assistant":
                        prev = messages[-1]
                        if reasoning:
                            prev["reasoning"] = _merge_reasoning(prev.get("reasoning"), reasoning)
                        if tools:
                            prev["tools"] = _extend_tools(prev.get("tools"), tools)
                    else:
                        if reasoning:
                            pending_reasoning = _merge_reasoning(pending_reasoning, reasoning)
                        if tools:
                            pending_tools = _extend_tools(pending_tools, tools)
                continue

            text = msg.get("content", "")
            if role not in ("user", "assistant") or not isinstance(text, str):
                continue
            sends = extract_send_paths(text) if role == "assistant" else []
            cleaned = strip_transfer_markers(text)

            if role == "user" and (pending_reasoning or pending_tools):
                if messages and messages[-1].get("role") == "assistant":
                    prev = messages[-1]
                    if pending_reasoning:
                        prev["reasoning"] = _merge_reasoning(
                            prev.get("reasoning"),
                            pending_reasoning,
                        )
                    if pending_tools:
                        prev["tools"] = _extend_tools(prev.get("tools"), pending_tools)
                pending_reasoning = ""
                pending_tools = []

            # SEND-only assistant turns: fold paths into the previous assistant
            # bubble so spa v1 does not render an empty message.
            if not cleaned and sends:
                if messages and messages[-1].get("role") == "assistant":
                    prev = messages[-1]
                    prev_raw = prev.get("sends")
                    prev_sends = list(prev_raw) if isinstance(prev_raw, list) else []
                    prev_sends.extend(sends)
                    prev["sends"] = prev_sends
                    if reasoning or pending_reasoning:
                        prev["reasoning"] = _merge_reasoning(
                            prev.get("reasoning"),
                            _merge_reasoning(pending_reasoning, reasoning),
                        )
                        pending_reasoning = ""
                    if tools or pending_tools:
                        prev["tools"] = _extend_tools(
                            prev.get("tools"),
                            _extend_tools(pending_tools, tools),
                        )
                        pending_tools = []
                else:
                    row: dict[str, object] = {"role": role, "text": "", "sends": sends}
                    merged = _merge_reasoning(pending_reasoning, reasoning)
                    merged_tools = _extend_tools(pending_tools, tools)
                    pending_reasoning = ""
                    pending_tools = []
                    _attach_process(row, reasoning=merged, tools=merged_tools)
                    messages.append(row)
                continue
            if not cleaned:
                if reasoning or tools:
                    if messages and messages[-1].get("role") == "assistant":
                        prev = messages[-1]
                        if reasoning:
                            prev["reasoning"] = _merge_reasoning(prev.get("reasoning"), reasoning)
                        if tools:
                            prev["tools"] = _extend_tools(prev.get("tools"), tools)
                    else:
                        if reasoning:
                            pending_reasoning = _merge_reasoning(pending_reasoning, reasoning)
                        if tools:
                            pending_tools = _extend_tools(pending_tools, tools)
                continue

            row: dict[str, object] = {"role": role, "text": cleaned}
            if kind != "chat":
                row["kind"] = kind
            if sends:
                row["sends"] = sends
            if role == "assistant":
                merged = _merge_reasoning(pending_reasoning, reasoning)
                merged_tools = _extend_tools(pending_tools, tools)
                pending_reasoning = ""
                pending_tools = []
                _attach_process(row, reasoning=merged, tools=merged_tools)
            messages.append(row)

        if messages and messages[-1].get("role") == "assistant":
            prev = messages[-1]
            if pending_reasoning:
                prev["reasoning"] = _merge_reasoning(prev.get("reasoning"), pending_reasoning)
            if pending_tools:
                prev["tools"] = _extend_tools(prev.get("tools"), pending_tools)

        # Pair results with their tool entries, in global order: each row's
        # tools take the next slice of the results stream.
        if tool_results:
            result_idx = 0
            for row in messages:
                row_tools = row.get("tools")
                if not isinstance(row_tools, list) or not row_tools:
                    continue
                count = len(row_tools)
                if result_idx < len(tool_results):
                    row["tool_results"] = tool_results[result_idx : result_idx + count]
                    result_idx += count

        logger.debug(f"History for session {session_id!r}: {len(messages)} displayable message(s)")
        return messages

    async def delete(self, workspace: str, session_id: str, *, appdata: str = "") -> None:
        """Remove AppData and legacy history files if present (best-effort)."""
        appdata_root = appdata.strip() or await resolve_appdata_root()
        for path in (
            appdata_history_path(appdata_root, session_id),
            legacy_history_path(workspace, session_id),
        ):
            try:
                await path.unlink()
                logger.info(f"Deleted history file for session {session_id!r} at {path!r}")
            except FileNotFoundError:
                logger.debug(f"No history file to delete for session {session_id!r} at {path!r}")
            except OSError as e:
                logger.warning(f"Failed to delete history for session {session_id!r}: {e!r}")
