"""User interface channels."""

from __future__ import annotations

from aiohttp import BaseConnector, NamedPipeConnector, TCPConnector, UnixConnector


def build_connector(session_socket: str) -> tuple[BaseConnector, str]:
    """Resolve a session address into an aiohttp connector and request endpoint.

    Mirrors ``SessionAgent._build_connector``: a channel can dial the session
    over a TCP URL, a Windows named pipe, or a Unix domain socket path.
    """
    if session_socket.startswith(("http://", "https://")):
        connector: BaseConnector = TCPConnector(ssl=session_socket.startswith("https://"))
        endpoint = session_socket.rstrip("/") + "/chat/completions"
    elif session_socket.startswith("npipe://") or session_socket.startswith("\\\\.\\pipe\\"):
        connector = NamedPipeConnector(path=session_socket.removeprefix("npipe://"))
        endpoint = "http://localhost/chat/completions"
    else:
        connector = UnixConnector(path=session_socket)
        endpoint = "http://localhost/chat/completions"
    return connector, endpoint
