# Gateway 系统托盘 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gateway 添加 `--icon` 和 `--tray` 参数：`--icon` 指定图标文件（同时用作 favicon），`--tray` 开启系统托盘（需 `--icon`）。未设置时不创建托盘。

**Architecture:** `Gateway` dataclass 新增 `icon: str | None = None` 和 `tray: bool = False` 字段，`run()` 中若 `tray` 为 True 且 `icon` 为 None 则报错，否则创建 `GatewayTray`。`GatewayTray` 从文件加载图标，删除 `_create_icon_image`。

**Tech Stack:** pystray 0.19.5, Pillow 12.2.0, anyio, webbrowser

---

### Task 1: 修改 `_tray.py` — 从文件加载图标

**Files:**
- Modify: `src/psi_agent/gateway/_tray.py`

- [ ] **Step 1: `__init__` 加 `icon_path` 参数**

```python
def __init__(self, url: str, icon_path: str) -> None:
    self._url = url
    self._icon_path = icon_path
    self._stop_event = threading.Event()
    self._icon: pystray.Icon | None = None
    self._thread: threading.Thread | None = None
```

- [ ] **Step 2: `start()` 用 `Image.open(icon_path)` 替换 `_create_icon_image()`**

```python
def start(self) -> None:
    if not _HAS_PYSTRAY:
        logger.warning("pystray not available, skipping system tray icon")
        return

    try:
        image = Image.open(self._icon_path)
    except Exception as e:
        logger.warning(f"Failed to load tray icon from {self._icon_path}: {e}")
        return
    ...
```

- [ ] **Step 3: 删除 `_create_icon_image()`、`_DEFAULT_WIDTH`、`ImageDraw`、`ImageFont`**

删除模块级函数 `_create_icon_image()` 和常量 `_DEFAULT_WIDTH`。import 中移除 `ImageDraw`、`ImageFont`。

- [ ] **Step 4: 运行 type check 和 lint**

```bash
uv run ty check src/psi_agent/gateway/_tray.py
uv run ruff check src/psi_agent/gateway/_tray.py
```

---

### Task 2: 修改 `__init__.py` — 加 `icon` + `tray` 参数 + 条件分支 + 校验

**Files:**
- Modify: `src/psi_agent/gateway/__init__.py`

- [ ] **Step 1: `Gateway` dataclass 加 `icon` 和 `tray` 字段**

```python
icon: str | None = None
"""Path to icon image file (png/jpg/ico). Used as favicon and—if --tray is set—system tray icon."""

tray: bool = False
"""Show a system tray icon (requires --icon)."""
```

- [ ] **Step 2: `run()` 中条件创建托盘 + 校验**

```python
if self.tray and self.icon is None:
    raise ValueError("--tray requires --icon to be set")

tray = None
if self.tray:
    tray = GatewayTray(addr, self.icon)  # type: ignore[arg-type]
    try:
        tray.start()
    except Exception as e:
        logger.warning(f"Failed to start system tray: {e!r}")

try:
    if tray is not None and tray.is_running():
        await anyio.to_thread.run_sync(tray.wait_stop, abandon_on_cancel=True)  # ty: ignore
    else:
        await anyio.sleep_forever()
finally:
    if tray is not None:
        tray.stop()
```

- [ ] **Step 3: 运行 type check 和 lint**

---

### Task 3: 更新测试

**Files:**
- Modify: `tests/psi_agent/gateway/test_tray.py`

- [ ] **Step 1: 删除 `test_create_icon_image_returns_pil_image`**
- [ ] **Step 2: `GatewayTray` 构造调用加 `icon_path` 参数**

用一个临时图片文件：

```python
import tempfile
from PIL import Image as PILImage

@pytest.fixture
def icon_file():
    img = PILImage.new("RGBA", (64, 64), (41, 98, 255, 255))
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        img.save(f, "PNG")
    yield f.name
    import os
    os.unlink(f.name)
```

所有 `GatewayTray("http://...")` → `GatewayTray("http://...", icon_file)`。

- [ ] **Step 3: 运行测试**

```bash
uv run pytest tests/psi_agent/gateway/test_tray.py -v
```

---

### Task 4: 更新 AGENTS.md

**Files:**
- Modify: `src/psi_agent/gateway/AGENTS.md`

- [ ] **Step 1: 更新系统托盘章节**

- "启动后会自动创建系统托盘图标" → "可通过 `--tray --icon PATH` 开启系统托盘"
- 图标来源从"Pillow 程序化生成" → "用户指定图片文件"
- 补充：`--tray` 未设置 `--icon` 时报错；`--tray` 未设置时不创建托盘，Gateway 通过 `anyio.sleep_forever()` 等待 cancel
- CLI 示例加 `--tray --icon` 选项

- [ ] **Step 2: 更新启动流程**

步骤 7 改为条件创建 + 校验：
```
7. if self.tray and self.icon is None: raise ValueError
8. if self.tray: GatewayTray(addr, self.icon).start()  ← 可选托盘
9. if tray: wait_stop(); else: sleep_forever()         ← 条件等待
10. finally: 清理托盘（如有）+ runner.cleanup() + tg.__aexit__()
```

---

### Task 5: Final verification

- [ ] **Step 1: Run all lint, type check, tests**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest -v
```
