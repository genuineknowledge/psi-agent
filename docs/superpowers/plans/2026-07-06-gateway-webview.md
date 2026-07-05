# Gateway --webview Feature Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `--webview` flag to Gateway, replacing browser-open with a native pywebview window; closing the window shuts down Gateway (unless `--tray` is active).

**Architecture:** New `_webview.py` module wraps pywebview lifecycle in a daemon thread with close detection via `threading.Event`. `GatewayTray.__init__` gains an optional `on_open` callback to substitute webview.show() for `webbrowser.open()`. Gateway `run()` adds a third wait path: `tray.wait_stop()` / `wv.wait_closed()` / `sleep_forever()`.

**Tech Stack:** pywebview (optional dependency), threading, anyio.to_thread.run_sync

**Spec:** `docs/superpowers/specs/2026-07-06-gateway-webview-design.md`

---

### File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `src/psi_agent/gateway/_webview.py` | **Create** | GatewayWebView — pywebview lifecycle, show/hide, close detection |
| `src/psi_agent/gateway/_tray.py` | **Modify** | Add `on_open` callback parameter to `__init__`; `_open_browser` delegates to it |
| `src/psi_agent/gateway/__init__.py` | **Modify** | Add `webview` field, mutual-exclusion validation, webview+wait orchestration in `run()` |
| `src/psi_agent/gateway/AGENTS.md` | **Modify** | Document webview feature in architecture + CLI section |
| `tests/psi_agent/gateway/test_webview.py` | **Create** | Unit tests for GatewayWebView threading/event logic |

---

### Task 1: Create `_webview.py` — GatewayWebView class

**Files:**
- Create: `src/psi_agent/gateway/_webview.py`

- [ ] **Step 1: Write the file**

```python
"""Native webview window for Gateway. Uses pywebview to display the Web Console."""

from __future__ import annotations

import contextlib
import threading
from typing import Any

from loguru import logger


class GatewayWebView:
    """Manages a pywebview window for the Gateway Web Console.

    Runs pywebview in a background daemon thread. The main anyio loop
    can `to_thread.run_sync(wv.wait_closed)` to block until the window
    is closed by the user.
    """

    def __init__(self, url: str, has_tray: bool = False) -> None:
        self._url = url
        self._has_tray = has_tray
        self._window: Any = None
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the webview window in a background daemon thread.

        Raises ImportError if pywebview is not installed.
        Raises RuntimeError if already started.
        """
        if self._thread is not None:
            raise RuntimeError("GatewayWebView already started")

        webview = __import__("webview")

        self._window = webview.create_window("psi-agent Gateway", self._url)
        self._window.events.closing += self._on_closing

        self._thread = threading.Thread(target=webview.start, daemon=True)
        self._thread.start()
        logger.info("Gateway webview window started")

    def show(self) -> None:
        """Restore a previously hidden webview window (called from tray callback)."""
        if self._window is not None:
            try:
                self._window.show()
            except Exception:
                pass

    def stop(self) -> None:
        """Destroy the webview window and wait for the thread to finish."""
        if self._window is not None:
            with contextlib.suppress(Exception):
                self._window.destroy()
            self._window = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
            self._thread = None
        logger.info("Gateway webview window stopped")

    def wait_closed(self) -> None:
        """Block (in a worker thread) until the webview window is closed."""
        self._closed.wait()

    def is_running(self) -> bool:
        """True if the webview thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    def _on_closing(self) -> bool:
        """Handle window close event.

        Returns True to allow closing, False to prevent it.
        With tray: hide instead of closing, return False.
        Without tray: signal close and return True.
        """
        if self._has_tray:
            if self._window is not None:
                self._window.hide()
            return False
        self._closed.set()
        return True
```

- [ ] **Step 2: Commit**

```bash
git add src/psi_agent/gateway/_webview.py
git commit -m "feat(gateway): add GatewayWebView class for native webview window"
```

---

### Task 2: Modify `_tray.py` — add `on_open` callback

**Files:**
- Modify: `src/psi_agent/gateway/_tray.py:14-17` (constructor signature)
- Modify: `src/psi_agent/gateway/_tray.py:66-67` (`_open_browser` body)

- [ ] **Step 1: Add `on_open` parameter to `__init__`**

Change the constructor signature from:
```python
def __init__(self, url: str, icon_path: str) -> None:
    self._url = url
    self._icon_path = icon_path
    self._stop_event = threading.Event()
    self._icon: Any = None
    self._thread: threading.Thread | None = None
```

To:
```python
def __init__(
    self,
    url: str,
    icon_path: str,
    on_open: Any = None,
) -> None:
    self._url = url
    self._icon_path = icon_path
    self._on_open = on_open
    self._stop_event = threading.Event()
    self._icon: Any = None
    self._thread: threading.Thread | None = None
```

- [ ] **Step 2: Change `_open_browser` to delegate to `_on_open`**

Change from:
```python
def _open_browser(self, icon: Any = None) -> None:
    webbrowser.open(self._url)
```

To:
```python
def _open_browser(self, icon: Any = None) -> None:
    if self._on_open is not None:
        self._on_open()
    else:
        webbrowser.open(self._url)
```

- [ ] **Step 3: Commit**

```bash
git add src/psi_agent/gateway/_tray.py
git commit -m "feat(gateway): add on_open callback to GatewayTray for webview support"
```

---

### Task 3: Write unit tests for `GatewayWebView`

**Files:**
- Create: `tests/psi_agent/gateway/test_webview.py`

- [ ] **Step 1: Write the test file**

```python
from __future__ import annotations

import threading

import pytest

from psi_agent.gateway._webview import GatewayWebView


def test_webview_init():
    wv = GatewayWebView("http://127.0.0.1:9999")
    assert wv._url == "http://127.0.0.1:9999"
    assert wv._has_tray is False
    assert wv._window is None
    assert wv._thread is None
    assert not wv._closed.is_set()


def test_webview_init_with_tray():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=True)
    assert wv._has_tray is True


def test_webview_on_closing_no_tray_sets_closed_and_returns_true():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=False)
    result = wv._on_closing()
    assert result is True
    assert wv._closed.is_set()


def test_webview_on_closing_with_tray_returns_false():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=True)
    result = wv._on_closing()
    assert result is False
    assert not wv._closed.is_set()


def test_webview_wait_closed_blocks_and_unblocks():
    wv = GatewayWebView("http://127.0.0.1:9999", has_tray=False)
    signal = {"done": False}

    def closer():
        wv._on_closing()
        signal["done"] = True

    t = threading.Thread(target=closer)
    t.start()
    wv.wait_closed()
    t.join()
    assert signal["done"]


def test_webview_stop_when_not_started():
    wv = GatewayWebView("http://127.0.0.1:9999")
    wv.stop()


def test_webview_is_running_before_start():
    wv = GatewayWebView("http://127.0.0.1:9999")
    assert wv.is_running() is False


def test_webview_double_start_raises():
    wv = GatewayWebView("http://127.0.0.1:9999")

    mock_module = type("module", (), {})
    mock_module.create_window = lambda title, url: type("win", (), {"events": type("ev", (), {})()})()
    mock_module.start = lambda: None

    import sys
    sys.modules["webview"] = mock_module
    try:
        wv.start()
        with pytest.raises(RuntimeError, match="already started"):
            wv.start()
    finally:
        del sys.modules["webview"]


def test_webview_show_no_window():
    wv = GatewayWebView("http://127.0.0.1:9999")
    wv.show()
```

- [ ] **Step 2: Run tests and verify they pass**

```bash
uv run pytest tests/psi_agent/gateway/test_webview.py -v
```

- [ ] **Step 3: Commit**

```bash
git add tests/psi_agent/gateway/test_webview.py
git commit -m "test(gateway): add unit tests for GatewayWebView"
```

---

### Task 4: Modify `__init__.py` — add `webview` field and orchestration

**Files:**
- Modify: `src/psi_agent/gateway/__init__.py:32-53` (dataclass fields)
- Modify: `src/psi_agent/gateway/__init__.py:54-160` (`run()` logic)
- Modify: `src/psi_agent/gateway/__init__.py:1-20` (imports)

- [ ] **Step 1: Add `webview` field and import**

Add import after `import webbrowser`:
```python
from psi_agent.gateway._webview import GatewayWebView
```

Add `webview` field after `browser`:
```python
webview: bool = False
"""Use a native webview window instead of the system browser."""
```

- [ ] **Step 2: Add mutual-exclusion validation in `run()`**

After `setup_logging(verbose=self.verbose)`:
```python
if self.browser and self.webview:
    raise ValueError("--browser and --webview are mutually exclusive")
```

- [ ] **Step 3: Add webview lifecycle in `run()`**

After `logger.info(f"Gateway listening on {addr}")` and its surrounding site.start() block, replace the browser/tray/wait block. The full replacement for lines 134-154:

```python
                wv = None
                if self.webview:
                    wv = GatewayWebView(addr, has_tray=self.tray)
                    try:
                        wv.start()
                    except Exception as e:
                        logger.warning(f"Failed to start webview window: {e!r}")

                if self.browser:
                    await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

                tray = None
                if self.tray:
                    if self.icon is None:
                        raise ValueError("--tray requires --icon to be set")
                    on_open = wv.show if wv is not None and wv.is_running() else None
                    tray = GatewayTray(addr, self.icon, on_open=on_open)
                    try:
                        tray.start()
                    except Exception as e:
                        logger.warning(f"Failed to start system tray: {e!r}")

                try:
                    if tray is not None and tray.is_running():
                        await anyio.to_thread.run_sync(tray.wait_stop, abandon_on_cancel=True)  # ty: ignore
                    elif wv is not None and wv.is_running():
                        await anyio.to_thread.run_sync(wv.wait_closed, abandon_on_cancel=True)  # ty: ignore
                    else:
                        await anyio.sleep_forever()
                finally:
                    if tray is not None:
                        tray.stop()
                    if wv is not None:
                        wv.stop()
```

- [ ] **Step 4: Run existing tests to verify no regression**

```bash
uv run pytest tests/psi_agent/gateway/ -v
```

- [ ] **Step 5: Commit**

```bash
git add src/psi_agent/gateway/__init__.py
git commit -m "feat(gateway): add --webview flag with native webview window support"
```

---

### Task 5: Update `AGENTS.md` documentation

**Files:**
- Modify: `src/psi_agent/gateway/AGENTS.md`

- [ ] **Step 1: Add `GatewayWebView` to architecture diagram**

Change line 11:
```
Gateway 进程
├── AIManager          — AI 实例注册表 + 生命周期管理
├── SessionManager     — Session 实例注册表 + 生命周期管理
├── TitleManager       — 会话标题 CRUD + AI 自动生成
├── WorkspaceManager   — 目录浏览
├── ChatManager        — SSE 流式对话管理
├── HistoryManager     — JSONL 历史读取
├── GatewayState       — 状态持久化到 state/latest.json
├── aiohttp REST Server  — OpenAPI CRUD + Web UI chat
├── spa/               — Vue 3 SPA 前端项目 (Vite + SFC)
├── GatewayTray        — 系统托盘图标 (pystray)
└── _openapi.py       — OpenAPI schema 提供
```

To:
```
Gateway 进程
├── AIManager          — AI 实例注册表 + 生命周期管理
├── SessionManager     — Session 实例注册表 + 生命周期管理
├── TitleManager       — 会话标题 CRUD + AI 自动生成
├── WorkspaceManager   — 目录浏览
├── ChatManager        — SSE 流式对话管理
├── HistoryManager     — JSONL 历史读取
├── GatewayState       — 状态持久化到 state/latest.json
├── aiohttp REST Server  — OpenAPI CRUD + Web UI chat
├── spa/               — Vue 3 SPA 前端项目 (Vite + SFC)
├── GatewayWebView     — 原生 webview 窗口 (pywebview)
├── GatewayTray        — 系统托盘图标 (pystray)
└── _openapi.py       — OpenAPI schema 提供
```

- [ ] **Step 2: Add `GatewayWebView` module entry in the 模块 table**

After the `_tray.py` row (line 41):
```
| `_webview.py` | 原生 webview 窗口（pywebview），`--webview` 参数开启。窗口关闭信号通过 `threading.Event` 传递给主 loop |
```

- [ ] **Step 3: Update startup flow (step 13)**

Replace the existing "browser/tray" flow section. Old lines 59-63:
```
13. if self.browser: webbrowser.open(addr)
14. if self.tray and self.icon is None: raise ValueError("--tray requires --icon")
15. if self.tray: GatewayTray(addr, self.icon).start()
16. try: wait (if tray started) / sleep_forever (if no tray or tray failed)
17. finally: tray.stop()（如有）+ runner.cleanup() + tg.__aexit__()
```

To:
```
13. if self.browser and self.webview: raise ValueError("--browser and --webview are mutually exclusive")
14. if self.webview: GatewayWebView(addr, has_tray=self.tray).start()
15. if self.browser: webbrowser.open(addr)
16. if self.tray and self.icon is None: raise ValueError("--tray requires --icon")
17. if self.tray: GatewayTray(addr, self.icon, on_open=wv.show).start()
18. try: 三路等待 — tray.wait_stop() / wv.wait_closed() / sleep_forever()
19. finally: tray.stop()（如有）+ wv.stop()（如有）+ runner.cleanup() + tg.__aexit__()
```

- [ ] **Step 4: Add webview section in tray documentation**

After the "实现细节" bullet about `self.browser` (line 84), add:
```
- `self.webview` 参数（默认 False）：设为 True 时替代 `--browser`，使用原生 webview 窗口展示 Web Console。与 `--browser` 互斥。`--tray` 开启时关闭窗口仅隐藏到托盘（托盘左键可恢复）；否则关闭窗口即终止 Gateway
```

- [ ] **Step 5: Update CLI section**

Change line 450:
```
psi-agent gateway [--listen http://127.0.0.1:PORT] [--socket-path psi] [--icon PATH] [--browser] [--tray/--no-tray] [--verbose]
```

To:
```
psi-agent gateway [--listen http://127.0.0.1:PORT] [--socket-path psi] [--icon PATH] [--browser/--no-browser] [--webview/--no-webview] [--tray/--no-tray] [--verbose]
```

Add after line 458:
```
`--webview` 使用原生 pywebview 窗口展示 Web Console（需安装 `pywebview`）。与 `--browser` 互斥，两者同时设为 True 时报错。关闭窗口行为取决于 `--tray`：有托盘时仅隐藏窗口，无托盘时退出 Gateway 进程。
```

- [ ] **Step 6: Commit**

```bash
git add src/psi_agent/gateway/AGENTS.md
git commit -m "docs(gateway): document --webview feature in AGENTS.md"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full lint + type check**

```bash
uv run ruff check src/psi_agent/gateway/_webview.py src/psi_agent/gateway/_tray.py src/psi_agent/gateway/__init__.py tests/psi_agent/gateway/test_webview.py
uv run ruff format --check src/psi_agent/gateway/_webview.py src/psi_agent/gateway/_tray.py src/psi_agent/gateway/__init__.py tests/psi_agent/gateway/test_webview.py
uv run ty check src/psi_agent/gateway/
```

- [ ] **Step 2: Run all gateway tests**

```bash
uv run pytest tests/psi_agent/gateway/ -v
```

- [ ] **Step 3: Run full test suite**

```bash
uv run pytest -v
```
