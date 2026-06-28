# Gateway 系统托盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gateway 启动时在系统托盘显示图标，左键点击/右键菜单可打开浏览器，右键菜单可选择退出 Gateway。

**Architecture:** 新增 `GatewayTray` 类管理 pystray 生命周期（独立 daemon 线程），`Gateway.run()` 将 `anyio.sleep_forever()` 替换为轮询 `GatewayTray.is_stop_requested()` 的循环，使托盘"退出"可触发 Gateway 正常 shutdown。

**Tech Stack:** pystray 0.19.5, Pillow 12.2.0, anyio, webbrowser

---

### Task 1: Create `GatewayTray` module

**Files:**
- Create: `src/psi_agent/gateway/_tray.py`

- [ ] **Step 1: Write `_tray.py`**

```python
"""System tray icon for Gateway. Left-click opens browser; right-click shows menu."""

from __future__ import annotations

import threading
import webbrowser

from loguru import logger

try:
    import pystray
    from PIL import Image, ImageDraw
    _HAS_PYSTRAY = True
except Exception:
    _HAS_PYSTRAY = False


_DEFAULT_WIDTH = 64


def _create_icon_image() -> Image.Image:
    img = Image.new("RGBA", (_DEFAULT_WIDTH, _DEFAULT_WIDTH), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    margin = 4
    r = 14
    draw.rounded_rectangle(
        [margin, margin, _DEFAULT_WIDTH - margin, _DEFAULT_WIDTH - margin],
        radius=r,
        fill=(41, 98, 255, 255),
    )
    x = _DEFAULT_WIDTH / 2
    y = _DEFAULT_WIDTH / 2
    psi = "\u03c8"
    try:
        from PIL import ImageFont
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", 38)
        except OSError:
            font = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), psi, font=font, anchor="mm")
        draw.text((x, y), psi, fill=(255, 255, 255, 255), font=font, anchor="mm")
    except Exception:
        draw.text((x, y), psi, fill=(255, 255, 255, 255), anchor="mm")
    return img


class GatewayTray:
    """System tray icon that provides quick access to Gateway Web Console."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._stop_event = threading.Event()
        self._icon: pystray.Icon | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the system tray icon in a background daemon thread."""
        if not _HAS_PYSTRAY:
            logger.warning("pystray not available, skipping system tray icon")
            return

        try:
            image = _create_icon_image()
        except Exception as e:
            logger.warning(f"Failed to create tray icon image: {e}")
            return

        try:
            menu = pystray.Menu(
                pystray.MenuItem("打开控制台", self._open_browser, default=True),
                pystray.MenuItem("退出", self._quit),
            )
            self._icon = pystray.Icon("psi-agent", image, "psi-agent", menu)
        except Exception as e:
            logger.warning(f"Failed to create tray icon: {e}")
            self._icon = None
            return

        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()
        logger.info("Gateway system tray icon started")

    def stop(self) -> None:
        """Stop the tray icon and wait for its thread to finish."""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        logger.info("Gateway system tray icon stopped")

    def is_stop_requested(self) -> bool:
        """Returns True when user selected "退出" from the tray menu."""
        return self._stop_event.is_set()

    def _open_browser(self, icon: pystray.Icon | None = None) -> None:
        webbrowser.open(self._url)

    def _quit(self, icon: pystray.Icon | None = None) -> None:
        self._stop_event.set()
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
```

- [ ] **Step 2: Run type check to verify no import errors**

```bash
uv run ty check src/psi_agent/gateway/_tray.py
```
Expected: PASS (no type errors)

- [ ] **Step 3: Commit**

```bash
git add src/psi_agent/gateway/_tray.py
git commit -m "feat: add GatewayTray system tray module"
```

---

### Task 2: Integrate tray into `Gateway.run()`

**Files:**
- Modify: `src/psi_agent/gateway/__init__.py`

- [ ] **Step 1: Read current `__init__.py` to verify content matches plan assumptions**

File: `src/psi_agent/gateway/__init__.py`

Check that `run()` has the structure:
```python
async def run(self) -> None:
    setup_logging(...)
    ...
    if self.browser:
        await anyio.to_thread.run_sync(webbrowser.open, addr)
    try:
        await anyio.sleep_forever()
    finally:
        ...
```

- [ ] **Step 2: Modify `__init__.py` - add import and replace `sleep_forever()` with tray loop**

Add import after existing imports:
```python
from psi_agent.gateway._tray import GatewayTray
```

Replace the trailing section of `run()` (starting from `if self.browser:` through the end) with:

```python
        if self.browser:
            await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

        tray = GatewayTray(addr)
        try:
            tray.start()
        except Exception as e:
            logger.warning(f"Failed to start system tray: {e}")

        try:
            while not tray.is_stop_requested():
                await anyio.sleep(0.5)
        finally:
            tray.stop()
            logger.info("Shutting down Gateway")
            with anyio.CancelScope(shield=True):
                await runner.cleanup()
            with anyio.CancelScope(shield=True):
                await tg.__aexit__(None, None, None)
            logger.info("Gateway shutdown complete")
```

Full new `run()` method:

```python
async def run(self) -> None:
    setup_logging(verbose=self.verbose)

    addr = self.listen or f"http://127.0.0.1:{_random_port()}"
    logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

    tg = anyio.create_task_group()
    await tg.__aenter__()

    aim = AIManager(_prefix=self.socket_path, _tg=tg)
    sm = SessionManager(_aim=aim, _prefix=self.socket_path, _tg=tg)

    app = create_app(aim, sm)
    runner = web.AppRunner(app)
    try:
        await runner.setup()
        site = create_site(runner, addr)
        await site.start()
    except Exception as e:
        logger.error(f"Failed to start Gateway on {addr}: {e}")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        with anyio.CancelScope(shield=True):
            await tg.__aexit__(None, None, None)
        raise

    logger.info(f"Gateway listening on {addr}")

    if self.browser:
        await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

    tray = GatewayTray(addr)
    try:
        tray.start()
    except Exception as e:
        logger.warning(f"Failed to start system tray: {e}")

    try:
        while not tray.is_stop_requested():
            await anyio.sleep(0.5)
    finally:
        tray.stop()
        logger.info("Shutting down Gateway")
        with anyio.CancelScope(shield=True):
            await runner.cleanup()
        with anyio.CancelScope(shield=True):
            await tg.__aexit__(None, None, None)
        logger.info("Gateway shutdown complete")
```

- [ ] **Step 3: Run type check**

```bash
uv run ty check src/psi_agent/gateway/__init__.py
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/gateway/__init__.py
git commit -m "feat: integrate GatewayTray into Gateway.run() lifecycle"
```

---

### Task 3: Write tests for `GatewayTray`

**Files:**
- Create: `tests/psi_agent/gateway/test_tray.py`

- [ ] **Step 1: Write test file**

```python
from __future__ import annotations

import multiprocessing
import threading
import time

import pytest

from psi_agent.gateway._tray import GatewayTray, _create_icon_image, _HAS_PYSTRAY


def test_create_icon_image_returns_pil_image() -> None:
    img = _create_icon_image()
    assert img is not None
    assert img.size == (64, 64)
    assert img.mode == "RGBA"


def test_gateway_tray_init() -> None:
    tray = GatewayTray("http://127.0.0.1:8888")
    assert tray._url == "http://127.0.0.1:8888"
    assert not tray.is_stop_requested()
    assert tray._stop_event is not None


def test_gateway_tray_is_stop_requested() -> None:
    tray = GatewayTray("http://127.0.0.1:8888")
    assert not tray.is_stop_requested()
    tray._stop_event.set()
    assert tray.is_stop_requested()


def test_gateway_tray_stop_when_not_started() -> None:
    """stop() on a never-started tray should not raise."""
    tray = GatewayTray("http://127.0.0.1:8888")
    tray.stop()


def test_gateway_tray_quit_callback_sets_stop_event() -> None:
    tray = GatewayTray("http://127.0.0.1:8888")
    tray._quit()
    assert tray.is_stop_requested()


def test_gateway_tray_start_no_display() -> None:
    """start() should not raise even when DISPLAY is missing."""
    tray = GatewayTray("http://127.0.0.1:8888")
    tray.start()
    try:
        tray.stop()
    except Exception:
        pass


@pytest.mark.skipif(not _HAS_PYSTRAY, reason="pystray not available")
def test_gateway_tray_thread_terminates_on_stop() -> None:
    """Verify tray thread exits after stop()."""
    tray = GatewayTray("http://127.0.0.1:8888")
    tray.start()

    # Give tray thread time to start
    time.sleep(0.3)

    tray.stop()

    assert tray._thread is not None
    assert not tray._thread.is_alive()
```

- [ ] **Step 2: Run tests**

```bash
uv run pytest tests/psi_agent/gateway/test_tray.py -v
```
Expected: All tests pass. Tests that require pystray/display may be skipped or pass depending on environment.

- [ ] **Step 3: Commit**

```bash
git add tests/psi_agent/gateway/test_tray.py
git commit -m "test: add GatewayTray unit tests"
```

---

### Task 4: Update Gateway AGENTS.md

**Files:**
- Modify: `src/psi_agent/gateway/AGENTS.md`

- [ ] **Step 1: Add tray section to AGENTS.md**

In the architecture diagram, add `GatewayTray`:

```
Gateway 进程
├── AIManager, SessionManager, ...
├── aiohttp REST Server
├── spa/ — Vue 3 SPA 前端
├── GatewayTray — 系统托盘图标 (pystray)
└── _openapi.py
```

Add a new section after the "Gateway 启动流程" section:

```markdown
## 系统托盘 (GatewayTray)

Gateway 启动后会在系统托盘显示一个图标（pystray + Pillow 程序化生成），提供快速访问 Web Console 的入口。

**交互**：
| 操作 | 行为 |
|------|------|
| 左键点击 | 打开浏览器访问 Gateway 地址 |
| 右键 → "打开控制台" | 同上 |
| 右键 → "退出" | 关闭托盘并终止 Gateway 进程 |

**实现细节**：
- `GatewayTray` 在独立 daemon 线程中运行 pystray event loop
- `Gateway.run()` 不再使用 `anyio.sleep_forever()`，改为轮询 `tray.is_stop_requested()` 的 0.5s 循环
- 托盘"退出"设置 `threading.Event`，主循环检测到后正常 shutdown
- 托盘启动失败（无桌面环境等）不阻塞 Gateway 启动，仅记录 warning
- `self.browser` 参数不变：启动时自动打开一次浏览器，托盘提供后续手动"重新打开"
- 图标使用 Pillow 程序化生成，无外部 png 依赖，φ 字符蓝色圆角矩形底
```

- [ ] **Step 2: Run ruff check**

```bash
uv run ruff check src/psi_agent/gateway/AGENTS.md
```
(This is an .md file, ruff should skip it. No lint needed for markdown.)

- [ ] **Step 3: Commit**

```bash
git add src/psi_agent/gateway/AGENTS.md
git commit -m "docs: add GatewayTray documentation to AGENTS.md"
```

---

### Task 5: Final verification

- [ ] **Step 1: Run all lint, type check, tests**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest -v
```
Expected: All pass (ruff, format, ty, pytest)

- [ ] **Step 2: Commit any fixes if needed**
