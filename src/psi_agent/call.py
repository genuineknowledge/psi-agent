from __future__ import annotations

import time
import urllib.parse
import uuid
from dataclasses import dataclass

import anyio
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent.channel._core import ChannelCore
from psi_agent.channel._types import TextChunk
from psi_agent.session import Session

_DEFAULT_SOCKET_TIMEOUT: float = 10.0  # max seconds to wait for socket to appear
_DEFAULT_POLL_INTERVAL: float = 0.05   # seconds between existence checks
_SOCKET_ACCEPT_GRACE: float = 0.3      # extra wait after socket detected, for aiohttp accept()


async def call_agent(
    workspace: str,
    ai_socket: str,
    message: str,
    verbose: bool = False,
) -> str:
    """Start a session, send a message, and return the agent's response.

    Creates a temporary Session with its own channel socket, sends
    ``message`` to the agent defined by ``workspace``, and collects
    the full response as a string.

    The session is torn down automatically before returning.
    """
    channel_socket = f"/tmp/psi-call-{uuid.uuid4().hex}.sock"

    session = Session(
        workspace=workspace,
        channel_socket=channel_socket,
        ai_socket=ai_socket,
        verbose=verbose,
    )

    result_parts: list[str] = []

    async with anyio.create_task_group() as tg:
        tg.start_soon(session.run)

        await _wait_for_socket(channel_socket)

        try:
            async with ChannelCore(channel_socket, interval=0.0) as core:
                async for chunk in core.post([TextChunk(message)]):
                    if isinstance(chunk, TextChunk):
                        result_parts.append(chunk.text)
        finally:
            tg.cancel_scope.cancel()

    return "".join(result_parts)


@dataclass
class Call:
    """Start a session, send a message, print the response to stdout, then exit."""

    workspace: str
    """Path to the workspace directory."""

    ai_socket: str
    """AI backend address: Unix socket path, ``http://host:port``, or Named Pipe."""

    message: str
    """Message to send to the agent."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)
        try:
            text = await call_agent(
                workspace=self.workspace,
                ai_socket=self.ai_socket,
                message=self.message,
                verbose=self.verbose,
            )
            print(text)
        except Exception as e:
            logger.error(f"Call error: {e}")
            print(f"\n[Error: {e}]")


async def _wait_for_socket(
    address: str,
    *,
    max_wait: float = _DEFAULT_SOCKET_TIMEOUT,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
) -> None:
    """Wait until a server socket is ready to accept connections.

    Supports three transport types using the same prefix detection as
    ``psi_agent._socket``:

    * bare filesystem path → Unix domain socket
    * ``http(s)://host:port`` → TCP
    * ``\\\\\\\\.\\\\pipe\\\\\\name`` → Windows Named Pipe
    """
    transport = _detect_transport(address)

    deadline = time.monotonic() + max_wait
    while True:
        ready = await _check_ready(transport, address)
        if ready:
            return
        if time.monotonic() > deadline:
            raise TimeoutError(f"Server at {address} did not become ready within {max_wait}s")
        await anyio.sleep(poll_interval)


def _detect_transport(address: str) -> str:
    """Classify an address into one of the three supported transport types."""
    if address.startswith(("http://", "https://")):
        return "tcp"
    if address.startswith("\\\\.\\pipe\\"):
        return "pipe"
    return "unix"


async def _check_ready(transport: str, address: str) -> bool:
    """Return ``True`` when the server at ``address`` is ready for connections."""
    match transport:
        case "unix":
            if not await anyio.Path(address).exists():
                return False
            await anyio.sleep(_SOCKET_ACCEPT_GRACE)
            return True

        case "tcp":
            parsed = urllib.parse.urlparse(address)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            try:
                _, writer = await anyio.connect_tcp(host, port)
                await writer.aclose()
                return True
            except (OSError, ConnectionError):
                return False

        case "pipe":
            try:
                _, writer = await anyio.connect_unix(address)
                await writer.aclose()
                return True
            except Exception:
                return False

        case _:
            return False
