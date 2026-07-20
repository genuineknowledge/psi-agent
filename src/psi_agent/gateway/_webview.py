"""WebViewProcess — runs pywebview in a subprocess, bridged via anyio streams."""

from __future__ import annotations

import contextlib
import multiprocessing
from collections.abc import AsyncGenerator

import anyio
from loguru import logger

from psi_agent.gateway._spa_shell import DEFAULT_APP_NAME
from psi_agent.gateway._webview_main import webview_main


class WebViewProcess:
    """Wraps a pywebview subprocess. Async message-based API.

    Parent sends commands: ``await wv.send("show")``, ``await wv.send("destroy")``, ``await wv.send("flash")``.
    Subprocess emits events: ``"ready"``, ``"hidden"``, ``"closed"`` via ``wv.events`` stream.
    """

    def __init__(
        self,
        url: str,
        icon: str | None = None,
        tray_mode: bool = False,
        app_name: str = DEFAULT_APP_NAME,
    ) -> None:
        self._url = url
        self._icon = icon
        self._tray_mode = tray_mode
        self._app_name = app_name
        self._cmd_q: multiprocessing.Queue = multiprocessing.Queue()
        self._evt_q: multiprocessing.Queue = multiprocessing.Queue()
        self._send: anyio.MemoryObjectSendStream[str] | None = None
        self._recv: anyio.MemoryObjectReceiveStream[str] | None = None
        self._process: multiprocessing.Process | None = None
        self._ready_event = anyio.Event()

    async def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("WebViewProcess already started")

        self._process = multiprocessing.Process(
            target=webview_main,
            args=(self._url, self._icon, self._tray_mode, self._app_name, self._cmd_q, self._evt_q),
            daemon=False,
        )
        self._process.start()

        self._send, self._recv = anyio.create_memory_object_stream[str](max_buffer_size=16)

    async def send(self, cmd: str) -> None:
        if self._cmd_q is not None:
            self._cmd_q.put(cmd)

    async def stop(self) -> None:
        if self._cmd_q is not None:
            with contextlib.suppress(Exception):
                self._cmd_q.put("destroy")
        if self._recv is not None:
            with contextlib.suppress(anyio.ClosedResourceError):
                await self._recv.aclose()
            self._recv = None
        if self._send is not None:
            with contextlib.suppress(anyio.ClosedResourceError):
                await self._send.aclose()
            self._send = None
        if self._process is not None:
            if self._process.is_alive():
                self._process.join(timeout=2)
                if self._process.is_alive():
                    self._process.terminate()
                    self._process.join(timeout=2)
            self._process = None
        logger.info("Gateway webview subprocess stopped")

    @property
    def events(self) -> anyio.MemoryObjectReceiveStream[str]:
        if self._recv is None:
            raise RuntimeError("WebViewProcess not started — no events stream available")
        return self._recv

    def is_alive(self) -> bool:
        return self._process is not None and self._process.is_alive()

    async def _pump_events(self) -> None:
        """Background task: read from mp.Queue and forward to anyio stream."""
        while True:
            try:
                evt: str = await anyio.to_thread.run_sync(self._evt_q.get, abandon_on_cancel=True)
            except anyio.get_cancelled_exc_class():
                break
            try:
                await self._send.send(evt)  # type: ignore[union-attr]
            except anyio.ClosedResourceError, anyio.BrokenResourceError:
                break
            if evt == "ready":
                self._ready_event.set()
            if evt == "closed":
                break


async def merge(
    *streams: anyio.MemoryObjectReceiveStream[str],
) -> AsyncGenerator[str]:
    """Merge multiple MemoryObjectReceiveStreams, yielding items as they arrive."""
    async with anyio.create_task_group() as tg:
        out_send, out_recv = anyio.create_memory_object_stream[str]()
        async with out_send:

            async def _forward(src: anyio.MemoryObjectReceiveStream[str]) -> None:
                async with src:
                    async for item in src:
                        await out_send.send(item)

            for s in streams:
                tg.start_soon(_forward, s)
            async with out_recv:
                async for item in out_recv:
                    yield item
        tg.cancel_scope.cancel()
