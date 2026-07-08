"""Gateway — lifecycle manager for AI/Session instances over a REST + Web UI surface."""

from __future__ import annotations

import socket
import threading
import webbrowser
from dataclasses import dataclass

import anyio
from aiohttp import web
from loguru import logger

from psi_agent._logging import setup_logging
from psi_agent._sockets import create_site
from psi_agent.gateway._ai_manager import AIManager
from psi_agent.gateway._session_manager import SessionManager
from psi_agent.gateway._state import GatewayState
from psi_agent.gateway._title_manager import TitleManager
from psi_agent.gateway._tray import GatewayTray
from psi_agent.gateway._webview import GatewayWebView
from psi_agent.gateway.server import create_app


def _random_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


@dataclass
class Gateway:
    """Start the gateway REST + Web UI server."""

    listen: str = ""
    """Listen address. Empty = random high port on 127.0.0.1."""

    socket_path: str = "psi"
    """Prefix for AI/Session Unix socket paths."""

    icon: str | None = None
    """Path to icon image file (png/jpg/ico). Used as favicon, tray icon (--tray), and webview icon (--webview)."""

    browser: bool = False
    """Open a browser tab on startup."""

    webview: bool = False
    """Use a native webview window instead of the system browser."""

    tray: bool = False
    """Show a system tray icon (requires --icon)."""

    verbose: bool = False
    """Enable DEBUG-level logging."""

    async def run(self) -> None:
        """Async entry point (used by ``anyio.run``) for non-webview mode.

        The ``--webview`` path does NOT go through here: pywebview must own the
        main thread, so ``cli.main`` calls the synchronous ``run_webview``
        instead. See that method for the threading model.
        """
        setup_logging(verbose=self.verbose)

        if self.browser and self.webview:
            raise ValueError("--browser and --webview are mutually exclusive")

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        stop = threading.Event()
        await self._serve(addr, stop)

    def run_webview(self) -> None:
        """Synchronous entry point for ``--webview`` mode. Must run on the main thread.

        pywebview requires its GUI loop on the main thread (the WinForms backend
        installs a SIGINT handler). So the threading model is inverted relative
        to non-webview mode:

        - main thread: the pywebview GUI loop (blocks until the window closes)
        - background thread: ``anyio.run(self._serve)`` — aiohttp REST server
          plus the optional system tray

        Closing the window (no tray) or choosing "退出" in the tray both stop the
        server and unblock the main thread.
        """
        setup_logging(verbose=self.verbose)

        if self.browser and self.webview:
            raise ValueError("--browser and --webview are mutually exclusive")
        if self.icon is None:
            raise ValueError("--webview requires --icon to be set")

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        stop = threading.Event()
        ready = threading.Event()

        wv = GatewayWebView(addr, has_tray=self.tray, icon=self.icon, on_close=stop.set)

        def _serve_thread() -> None:
            try:
                anyio.run(self._serve, addr, stop, ready, wv)
            except Exception as e:  # pragma: no cover - surfaced via logs
                logger.error(f"Gateway server thread crashed: {e!r}")
            finally:
                stop.set()
                ready.set()  # unblock the main thread even if startup failed

        t = threading.Thread(target=_serve_thread, name="gateway-server", daemon=True)
        t.start()

        # Wait until the REST server is listening before loading it in the window,
        # so the console doesn't flash a connection error. Bounded so a failed
        # startup doesn't hang the main thread forever.
        ready.wait(timeout=30)

        try:
            wv.create()
            wv.run()  # blocks on the main thread until the window is destroyed
        finally:
            stop.set()
            t.join(timeout=10)

    async def _serve(
        self,
        addr: str,
        stop: threading.Event,
        ready: threading.Event | None = None,
        wv: GatewayWebView | None = None,
    ) -> None:
        """Run the REST server (and optional tray) until ``stop`` is set.

        Shared by both entry points. ``ready`` is set once the server is
        listening; ``wv`` (webview mode only) wires the tray "open" action to
        restore the window and the tray "quit" action to destroy it.
        """
        logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

        state = GatewayState()
        snapshot = await state.load()

        async with anyio.create_task_group() as tg:
            aim = AIManager(_prefix=self.socket_path, _tg=tg)
            sm = SessionManager(_aim=aim, _prefix=self.socket_path, _tg=tg)
            tm = TitleManager()

            for cfg in snapshot.get("ais", []):
                try:
                    await aim.create(
                        provider=cfg.get("provider", ""),
                        model=cfg.get("model", ""),
                        api_key=cfg.get("api_key", ""),
                        base_url=cfg.get("base_url", ""),
                        id=cfg.get("id", ""),
                    )
                    logger.info(f"Restored AI {cfg.get('id', '?')!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore AI {cfg.get('id', '?')!r}: {e!r}")

            for cfg in snapshot.get("sessions", []):
                try:
                    await sm.create(
                        ai_id=cfg.get("ai_id", ""),
                        workspace=cfg.get("workspace", ""),
                        id=cfg.get("id", ""),
                    )
                    logger.info(f"Restored Session {cfg.get('id', '?')!r}")
                except Exception as e:
                    logger.warning(f"Failed to restore Session {cfg.get('id', '?')!r}: {e!r}")

            for t in snapshot.get("titles", []):
                await tm.set(t["id"], t["title"])

            app = await create_app(aim, sm, tm, favicon_path=self.icon)

            async def _do_persist() -> None:
                await state.save(
                    ais=[
                        {
                            "id": info.id,
                            "provider": info.provider,
                            "model": info.model,
                            "api_key": info.api_key,
                            "base_url": info.base_url,
                        }
                        for info in await aim.list_all()
                    ],
                    sessions=[
                        {"id": info.id, "ai_id": info.ai_id, "workspace": info.workspace}
                        for info in await sm.list_all()
                    ],
                    titles=[{"id": sid, "title": title} for sid, title in tm.get_all().items()],
                )

            aim._persist = _do_persist
            sm._persist = _do_persist
            tm._persist = _do_persist

            await _do_persist()

            runner = web.AppRunner(app)
            try:
                try:
                    await runner.setup()
                    site = create_site(runner, addr)
                    await site.start()
                except Exception as e:
                    logger.error(f"Failed to start Gateway on {addr}: {e!r}")
                    raise

                logger.info(f"Gateway listening on {addr}")

                # Signal the main thread (webview mode) that it can load the URL.
                if ready is not None:
                    ready.set()

                if self.browser:
                    await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

                tray = None
                if self.tray:
                    if self.icon is None:
                        raise ValueError("--tray requires --icon to be set")
                    # In webview mode the tray restores the window; otherwise
                    # left-click just opens the browser. "退出" always stops the
                    # server and, in webview mode, destroys the window (which
                    # unblocks the main thread's GUI loop).
                    on_open = wv.show if wv is not None else None

                    def _on_quit() -> None:
                        stop.set()
                        if wv is not None:
                            wv.destroy()

                    tray = GatewayTray(addr, self.icon, on_open=on_open, on_quit=_on_quit)
                    try:
                        tray.start()
                    except Exception as e:
                        logger.warning(f"Failed to start system tray: {e!r}")

                try:
                    # Block until asked to stop, from any thread: the webview
                    # window closing (on_close), the tray "退出" item, or a
                    # crash in the serve thread. `stop` is a cross-thread
                    # threading.Event, so wait on it in a worker thread.
                    await anyio.to_thread.run_sync(stop.wait, abandon_on_cancel=True)  # ty: ignore
                finally:
                    if tray is not None:
                        tray.stop()
            finally:
                logger.info("Shutting down Gateway")
                with anyio.CancelScope(shield=True):
                    await runner.cleanup()
                tg.cancel_scope.cancel()
        logger.info("Gateway shutdown complete")
