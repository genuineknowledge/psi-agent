"""Deterministic redaction for private Router transport addresses."""

from __future__ import annotations

from collections.abc import Collection


def redact_private_sockets(*, text: str, sockets: Collection[str], limit: int = 512) -> str:
    """Replace raw and represented private sockets, then bound the summary."""

    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError("limit must be a non-negative integer")
    representations: set[str] = set()
    for socket in sockets:
        represented = repr(socket)
        representations.update(value for value in (socket, represented, represented[1:-1]) if value)
    for representation in sorted(representations, key=lambda value: (-len(value), value)):
        text = text.replace(representation, "<private-socket>")
    return text[:limit]


__all__ = ["redact_private_sockets"]
