from __future__ import annotations

import os
from collections.abc import Mapping, Sequence


def _socket_name_for_model(model: str) -> str:
    normalized = model.strip()
    if os.sep:
        normalized = normalized.replace(os.sep, "_")
    if os.altsep:
        normalized = normalized.replace(os.altsep, "_")
    normalized = normalized.replace(" ", "_")
    return f"{normalized}.sock"


def build_model_ai_sockets(
    models: Sequence[str],
    *,
    socket_dir: str,
    explicit_model_ai_sockets: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build a model-to-AI-socket mapping from model names.

    Explicitly configured sockets win over auto-derived ones. Auto-derived
    sockets are generated as ``<socket_dir>/<model>.sock``.
    """
    routes = {
        str(model): str(socket)
        for model, socket in (explicit_model_ai_sockets or {}).items()
        if model and socket
    }

    resolved_socket_dir = socket_dir or "."
    for model in models:
        model_name = str(model).strip()
        if not model_name or model_name in routes:
            continue
        routes[model_name] = os.path.join(resolved_socket_dir, _socket_name_for_model(model_name))
    return routes


def build_effective_model_ai_sockets(
    ai_socket: str,
    models: Sequence[str],
    *,
    explicit_model_ai_sockets: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the final model-to-socket mapping for a Session.

    If the session backend is a local filesystem socket, model names are
    expanded next to ``ai_socket``. If the backend is remote (http[s] or a
    named pipe), only explicit mappings are kept.
    """
    routes = {
        str(model): str(socket)
        for model, socket in (explicit_model_ai_sockets or {}).items()
        if model and socket
    }

    if not models:
        return routes

    if ai_socket.startswith(("http://", "https://", "\\\\.\\pipe\\")):
        return routes

    socket_dir = os.path.dirname(ai_socket) or "."
    return build_model_ai_sockets(
        models,
        socket_dir=socket_dir,
        explicit_model_ai_sockets=routes,
    )


def select_ai_socket_for_model(
    model: str | None,
    *,
    default_ai_socket: str,
    model_ai_sockets: Mapping[str, str] | None = None,
) -> str:
    """Select the AI socket for a request model.

    If the request model is configured in ``model_ai_sockets``, route to the
    matching AI backend. Otherwise fall back to the session's default
    ``ai_socket``.
    """
    routes = model_ai_sockets or {}
    if isinstance(model, str):
        routed_socket = routes.get(model)
        if routed_socket:
            return routed_socket
    return default_ai_socket
