"""Prompt builders for single-backend routing mode."""

from __future__ import annotations

from typing import Any


def _copy_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Copy only valid Chat Completions message objects from untrusted input."""

    return [dict(message) for message in messages if isinstance(message, dict)]


def _socket_catalog(upstream: list[tuple[str, str]] | tuple[tuple[str, str], ...]) -> str:
    merged: dict[str, list[str]] = {}
    for socket, description in upstream:
        merged.setdefault(socket, []).append(description)
    return "\n".join(f'- socket "{socket}": {"; ".join(descriptions)}' for socket, descriptions in merged.items())


def build_routing_messages(
    *,
    messages: list[dict[str, Any]],
    upstream: list[tuple[str, str]] | tuple[tuple[str, str], ...],
) -> list[dict[str, Any]]:
    """Ask the routing model to choose exactly one configured socket in strict JSON."""

    result = _copy_messages(messages)
    result.append(
        {
            "role": "user",
            "content": (
                "You are the routing selector for a multi-backend system.\n"
                "Choose exactly one configured socket for the full request context.\n\n"
                f"Configured sockets and capabilities:\n{_socket_catalog(upstream)}\n\n"
                "Return strict JSON only. No markdown, no explanation, no extra keys.\n"
                'The entire response must be exactly one object shaped like: {"socket": "<configured socket>"}.\n'
                "The socket value must match one configured socket exactly.\n"
                "If the request best fits no backend, still choose the best configured socket.\n"
            ),
        }
    )
    return result


__all__ = ["build_routing_messages"]
