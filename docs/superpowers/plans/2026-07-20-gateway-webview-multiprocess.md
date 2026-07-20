# Gateway webview 多进程 + 异步消息架构 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 webview 从 daemon 线程改为独立子进程（满足 pywebview 主线程要求），webview + tray + Gateway 三者通过 anyio MemoryObjectStream 解耦为 Actor 模式。

**Architecture:** 新建 `_webview_main.py` 为子进程入口（纯 pywebview，不 import psi_agent），重写 `_webview.py` 为 `WebViewProcess` wrapper（封装 mp.Process + anyio MemoryObjectStream 桥接），修改 `_tray.py` 增加 async events 流（threading.Queue + anyio pump task），简化 `__init__.py` 为 `merge(wv.events, tray.events)` 事件循环。

**Tech Stack:** anyio (MemoryObjectStream, create_task_group), multiprocessing (Process, Queue), threading, pywebview, pystray

**Spec:** `docs/superpowers/specs/2026-07-20-gateway-webview-multiprocess-design.md`

## Global Constraints

- `setup_logging` 第一行
- 零 `sys.exit`，错误用 `raise`
- 全部 anyio，禁止 `asyncio` / `pathlib` / `time.sleep`
- 零 noqa / per-file-ignores
- `from __future__ import annotations`
- `X | None` 非 `Optional[X]`
- 子进程代码不 import psi_agent
- ty:ignore 仅用于第三方动态类型（`from_thread.run`, `mp.Queue`, `window.events.closing`）

---

### File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/psi_agent/gateway/_webview_main.py` | **Create** | pywebview subprocess entry — pure webview + mp, no psi_agent |
| `src/psi_agent/gateway/_webview.py` | **Rewrite** | WebViewProcess — parent-side wrapper around mp.Process + anyio streams |
| `src/psi_agent/gateway/_tray.py` | **Modify** | Add async events stream — threading.Queue → anyio MemoryObjectStream |
| `src/psi_agent/gateway/_attention.py` | **Modify** | AttentionHub calls `wv.send("flash")` instead of `wv.request_attention()` |
| `src/psi_agent/gateway/__init__.py` | **Modify** | Simplified Gateway.run() — merge-based event loop |
| `src/psi_agent/gateway/_event_bus.py` | **Create (inlined in _webview.py)** | `merge()` async generator |
| `tests/psi_agent/gateway/test_webview_main.py` | **Create** | Tests for _webview_main subprocess entry |

---

### Task 1: Create `_webview_main.py` — subprocess entry point

**Files:**
- Create: `src/psi_agent/gateway/_webview_main.py`

**Interfaces:**
- Produces: `webview_main(url: str, icon: str | None, tray_mode: bool, app_name: str, cmd_q: multiprocessing.Queue, evt_q: multiprocessing.Queue) -> None`

- [ ] **Step 1: Write `_webview_main.py`**

```python
"""pywebview subprocess entry point. No psi_agent imports."""
from __future__ import annotations

import ctypes
import multiprocessing
import sys
import threading
from ctypes import wintypes
from typing import Any


def _flash_hwnd(hwnd: int) -> None:
    if sys.platform != "win32":
        return

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.UINT),
            ("hwnd", wintypes.HWND),
            ("dwFlags", wintypes.DWORD),
            ("uCount", wintypes.UINT),
            ("dwTimeout", wintypes.DWORD),
        ]

    info = FLASHWINFO()
    info.cbSize = ctypes.sizeof(FLASHWINFO)
    info.hwnd = hwnd
    info.dwFlags = 0x02 | 0x0C
    info.uCount = 5
    info.dwTimeout = 0
    ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))


def webview_main(
    url: str,
    icon: str | None,
    tray_mode: bool,
    app_name: str,
    cmd_q: multiprocessing.Queue,
    evt_q: multiprocessing.Queue,
) -> None:
    webview = __import__("webview")

    window = webview.create_window(app_name, url)

    def on_closing() -> bool:
        if tray_mode:
            window.hide()
            evt_q.put("hidden")
            return False
        evt_q.put("closed")
        return True

    window.events.closing += on_closing  # ty: ignore

    def cmd_loop() -> None:
        while True:
            cmd = cmd_q.get()
            if cmd == "show":
                try:
                    window.show()
                except Exception:
                    pass
            elif cmd == "destroy":
                try:
                    window.destroy()
                except Exception:
                    pass
                break
            elif cmd == "flash":
                try:
                    native = window.native
                    hwnd: Any = getattr(native, "Handle", None)
                    if hwnd is not None:
                        _flash_hwnd(int(hwnd))
                except Exception:
                    pass

    t = threading.Thread(target=cmd_loop, daemon=True)
    t.start()
    evt_q.put("ready")
    webview.start(icon=icon)
```

- [ ] **Step 2: Commit**

```bash
git add src/psi_agent/gateway/_webview_main.py
git commit -m "feat(gateway): add webview subprocess entry point with mp.Queue IPC"
```

---

### Task 2: Rewrite `_webview.py` — WebViewProcess parent wrapper

**Files:**
- Modify: `src/psi_agent/gateway/_webview.py` (full rewrite)

**Interfaces:**
- Produces: `class WebViewProcess` with `async start()`, `async send(cmd: str)`, `async stop()`, `events`, `is_alive()`
- Produces: `async def merge(*streams) -> AsyncGenerator[str]` — merge multiple MemoryObjectReceiveStreams

- [ ] **Step 1: Write the rewritten `_webview.py`**

```python
"""WebViewProcess — runs pywebview in a subprocess, bridged via anyio streams."""
from __future__ import annotations

import contextlib
import multiprocessing
from collections.abc import AsyncGenerator
from typing import Any

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
        self._pump_task: Any = None
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
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                break
            if evt == "ready":
                self._ready_event.set()
            if evt == "closed":
                break


async def merge(
    *streams: anyio.MemoryObjectReceiveStream[str],
) -> AsyncGenerator[str, None]:
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
```

- [ ] **Step 2: Commit**

```bash
git add src/psi_agent/gateway/_webview.py
git commit -m "refactor(gateway): rewrite GatewayWebView as WebViewProcess with subprocess + anyio streams"
```

---

### Task 3: Modify `_tray.py` — add async events stream

**Files:**
- Modify: `src/psi_agent/gateway/_tray.py`

**Interfaces:**
- Changes: `__init__` removes `on_open` parameter. Adds `events` property (MemoryObjectReceiveStream).
- Changes: `start()` now requires and receives a TaskGroup parameter (for spawning pump task).
- Produces: `events` stream yields `"open"`, `"quit"`

- [ ] **Step 1: Rewrite `_tray.py`**

Changed sections:

```python
"""System tray icon for Gateway. Left-click opens browser or restores webview; right-click shows menu."""

from __future__ import annotations

import contextlib
import queue
import threading
import webbrowser
from typing import Any

import anyio
from loguru import logger
from PIL import Image

from psi_agent.gateway._attention import _make_highlight_image, pulse_tray_icon
from psi_agent.gateway._spa_shell import DEFAULT_APP_NAME


class GatewayTray:
    """System tray icon. Emits "open" and "quit" events on its `events` stream."""

    def __init__(
        self,
        url: str,
        icon_path: str,
        app_name: str = DEFAULT_APP_NAME,
    ) -> None:
        self._url = url
        self._icon_path = icon_path
        self._app_name = app_name
        self._icon: Any = None
        self._thread: threading.Thread | None = None
        self._normal_image: Any = None
        self._highlight_image: Any = None
        self._q: queue.Queue[str] = queue.Queue()
        self._send_stream: anyio.MemoryObjectSendStream[str] | None = None
        self._recv_stream: anyio.MemoryObjectReceiveStream[str] | None = None

    @property
    def events(self) -> anyio.MemoryObjectReceiveStream[str]:
        if self._recv_stream is None:
            raise RuntimeError("GatewayTray not started")
        return self._recv_stream

    def start(self) -> None:
        """Start the system tray icon in a background daemon thread.

        Must be called from an async context. Events will be pumped to the stream
        by a background task that must be spawned separately (see _pump_events).
        """
        if self._thread is not None:
            raise RuntimeError("GatewayTray already started")

        self._send_stream, self._recv_stream = anyio.create_memory_object_stream[str](max_buffer_size=10)

        pystray = __import__("pystray")

        try:
            image = Image.open(self._icon_path)
            self._normal_image = image
            self._highlight_image = _make_highlight_image(image)
        except Exception as e:
            logger.warning(f"Failed to load tray icon from {self._icon_path!r}: {e!r}")
            return

        try:
            menu = pystray.Menu(
                pystray.MenuItem(f"打开 {self._app_name}", self._on_open, default=True),
                pystray.MenuItem("退出", self._on_quit),
            )
            self._icon = pystray.Icon("psi-agent", image, self._app_name, menu)
        except Exception as e:
            logger.warning(f"Failed to create tray icon: {e!r}")
            self._icon = None
            return

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        logger.info("Gateway system tray icon started")

    async def _pump_events(self) -> None:
        """Background task: read from threading.Queue and forward to anyio stream."""
        while True:
            try:
                evt: str = await anyio.to_thread.run_sync(self._q.get, abandon_on_cancel=True)
            except anyio.get_cancelled_exc_class():
                break
            try:
                await self._send_stream.send(evt)  # type: ignore[union-attr]
            except (anyio.ClosedResourceError, anyio.BrokenResourceError):
                break

    def stop(self) -> None:
        """Stop the tray icon and wait for its thread to finish."""
        if self._icon is not None:
            with contextlib.suppress(Exception):
                self._icon.stop()
            self._icon = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
            self._thread = None
        logger.info("Gateway system tray icon stopped")

    def is_running(self) -> bool:
        """True if the tray icon thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def request_attention(self) -> None:
        """Pulse tray icon (+ Windows balloon) to draw attention (best-effort)."""
        if self._icon is None or self._normal_image is None or self._highlight_image is None:
            return
        logger.info("Tray attention pulse starting")
        with contextlib.suppress(Exception):
            self._icon.notify("有对话已完成", self._app_name)
        pulse_tray_icon(self._icon, self._normal_image, self._highlight_image)

    def _on_open(self, icon: Any = None) -> None:
        self._q.put("open")

    def _on_quit(self, icon: Any = None) -> None:
        self._q.put("quit")
```

- [ ] **Step 2: Commit**

```bash
git add src/psi_agent/gateway/_tray.py
git commit -m "refactor(gateway): replace on_open callback with async events stream in GatewayTray"
```

---

### Task 4: Modify `_attention.py` — adapt to WebViewProcess

**Files:**
- Modify: `src/psi_agent/gateway/_attention.py`

- [ ] **Step 1: Update AttentionHub for WebViewProcess**

The TYPE_CHECKING import changes, `_webview` ref renamed, `request_attention` → `send("flash")`.

Changes:

```python
if TYPE_CHECKING:
    from psi_agent.gateway._tray import GatewayTray
    from psi_agent.gateway._webview import WebViewProcess
```

And in `notify_sync`:

```python
def notify_sync(self) -> None:
    if self._webview is not None:
        # WebViewProcess.send is async, but notify_sync runs in a daemon thread.
        # Fire-and-forget via anyio.from_thread.
        from anyio import from_thread

        async def _send_flash():
            with contextlib.suppress(Exception):
                await self._webview.send("flash")

        try:
            from_thread.run(_send_flash)
        except Exception:
            pass
    if self._tray is not None:
        self._tray.request_attention()
    if self._webview is None and self._tray is None:
        logger.debug("Attention notify with no tray/webview bound")
    else:
        logger.info("Attention notify dispatched")
```

- [ ] **Step 2: Commit**

```bash
git add src/psi_agent/gateway/_attention.py
git commit -m "refactor(gateway): adapt AttentionHub to WebViewProcess async send API"
```

---

### Task 5: Simplify `__init__.py` — event-driven Gateway.run()

**Files:**
- Modify: `src/psi_agent/gateway/__init__.py`

- [ ] **Step 1: Rewrite imports and the webview/tray/wait section of `run()`**

The import `GatewayWebView` → `WebViewProcess`, add `merge`.

```python
from psi_agent.gateway._webview import WebViewProcess, merge
```

Old tray/browser/wait code (lines 154-199) replaced with:

```python
                wv = None
                if self.webview:
                    if self.icon is None:
                        raise ValueError("--webview requires --icon to be set")
                    wv = WebViewProcess(addr, icon=self.icon, tray_mode=bool(self.tray), app_name=self.app_name)
                    try:
                        await wv.start()
                        tg.start_soon(wv._pump_events)
                    except Exception as e:
                        logger.warning(f"Failed to start webview window: {e!r}")

                if self.browser:
                    await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

                tray = None
                if self.tray:
                    if self.icon is None:
                        raise ValueError("--tray requires --icon to be set")
                    tray = GatewayTray(addr, self.icon, app_name=self.app_name)
                    try:
                        tray.start()
                        if tray.is_running():
                            tg.start_soon(tray._pump_events)
                    except Exception as e:
                        logger.warning(f"Failed to start system tray: {e!r}")

                if wv is not None and wv.is_alive():
                    attention.bind(webview=wv)
                if tray is not None and tray.is_running():
                    attention.bind(tray=tray)

                try:
                    if wv is not None and tray is not None and tray.is_running():
                        async for evt in merge(wv.events, tray.events):
                            match evt:
                                case "tray.open":
                                    await wv.send("show")
                                case "tray.quit":
                                    await wv.send("destroy")
                                    break
                                case "wv.closed":
                                    break
                    elif wv is not None:
                        async for evt in merge(wv.events):
                            match evt:
                                case "wv.closed":
                                    break
                    else:
                        await anyio.sleep_forever()
                finally:
                    if tray is not None:
                        tray.stop()
                    if wv is not None:
                        await wv.stop()
```

- [ ] **Step 2: Run ruff check + format**

```bash
uv run ruff check src/psi_agent/gateway/__init__.py src/psi_agent/gateway/_webview.py src/psi_agent/gateway/_tray.py src/psi_agent/gateway/_attention.py src/psi_agent/gateway/_webview_main.py
uv run ruff format src/psi_agent/gateway/__init__.py src/psi_agent/gateway/_webview.py src/psi_agent/gateway/_tray.py src/psi_agent/gateway/_attention.py src/psi_agent/gateway/_webview_main.py
```

- [ ] **Step 3: Commit**

```bash
git add src/psi_agent/gateway/__init__.py
git commit -m "refactor(gateway): use merge-based async event loop in Gateway.run()"
```

---

### Task 6: Update existing test for `_tray.py`

**Files:**
- Modify: `tests/psi_agent/gateway/test_tray.py`

The tray no longer accepts `on_open` constructor argument. Tests that pass `on_open=` must be updated.

- [ ] **Step 1: Read current test_tray.py**

```bash
grep -n 'on_open' tests/psi_agent/gateway/test_tray.py
```

- [ ] **Step 2: Remove `on_open=` from test constructor calls**

Replace any `GatewayTray(..., on_open=...)` with `GatewayTray(...)`.

- [ ] **Step 3: Run tray tests**

```bash
uv run pytest tests/psi_agent/gateway/test_tray.py -v
```

- [ ] **Step 4: Commit**

```bash
git add tests/psi_agent/gateway/test_tray.py
git commit -m "test(gateway): update tray tests for removed on_open parameter"
```

---

### Task 7: Update existing test for `_webview.py`

**Files:**
- Modify: `tests/psi_agent/gateway/test_webview.py`

The class was renamed from `GatewayWebView` → `WebViewProcess`, API changed. Tests need complete rewrite.

- [ ] **Step 1: Rewrite `test_webview.py`**

```python
from __future__ import annotations

import pytest
from psi_agent.gateway._webview import WebViewProcess


def test_webviewprocess_init():
    wv = WebViewProcess("http://127.0.0.1:9999")
    assert wv._url == "http://127.0.0.1:9999"
    assert wv._tray_mode is False
    assert wv._process is None


def test_webviewprocess_init_with_tray():
    wv = WebViewProcess("http://127.0.0.1:9999", tray_mode=True)
    assert wv._tray_mode is True


def test_webviewprocess_is_alive_before_start():
    wv = WebViewProcess("http://127.0.0.1:9999")
    assert wv.is_alive() is False


def test_webviewprocess_double_start_raises():
    import multiprocessing

    wv = WebViewProcess("http://127.0.0.1:9999")

    def fake_main(*args):
        pass

    async def test():
        wv._process = multiprocessing.Process(target=fake_main)
        with pytest.raises(RuntimeError, match="already started"):
            await wv.start()
    import anyio
    anyio.run(test)
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/psi_agent/gateway/test_webview.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/psi_agent/gateway/test_webview.py
git commit -m "test(gateway): rewrite webview tests for WebViewProcess API"
```

---

### Task 8: Update `AGENTS.md` documentation

**Files:**
- Modify: `src/psi_agent/gateway/AGENTS.md`

- [ ] **Step 1: Update architecture diagram**

Add `_webview_main.py` entry. Change `GatewayWebView` description to reflect subprocess architecture.

- [ ] **Step 2: Update startup flow**

Steps 14-18 should reflect the new merge-based event loop.

- [ ] **Step 3: Commit**

```bash
git add src/psi_agent/gateway/AGENTS.md
git commit -m "docs(gateway): document multiprocess webview architecture in AGENTS.md"
```

---

### Task 9: Full verification

- [ ] **Step 1: Run ruff check all changed files**

```bash
uv run ruff check src/psi_agent/gateway/_webview_main.py src/psi_agent/gateway/_webview.py src/psi_agent/gateway/_tray.py src/psi_agent/gateway/__init__.py src/psi_agent/gateway/_attention.py
```

- [ ] **Step 2: Run ruff format check**

```bash
uv run ruff format --check src/psi_agent/gateway/_webview_main.py src/psi_agent/gateway/_webview.py src/psi_agent/gateway/_tray.py src/psi_agent/gateway/__init__.py src/psi_agent/gateway/_attention.py
```

- [ ] **Step 3: Run ty check**

```bash
uv run ty check src/psi_agent/gateway/
```

- [ ] **Step 4: Run all gateway tests**

```bash
uv run pytest tests/psi_agent/gateway/ -v
```

- [ ] **Step 5: Run full test suite**

```bash
uv run pytest -v -m "not schedule"
```
