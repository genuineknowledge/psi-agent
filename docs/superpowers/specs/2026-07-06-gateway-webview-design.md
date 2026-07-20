# Gateway `--webview` 功能设计

## 概述

为 Gateway 新增 `--webview` / `--no-webview` 选项（默认 `False`）。
开启后，所有"弹出浏览器"的动作改为弹出原生 webview 窗口，与浏览器行为互斥。
需要 `--icon` 设置窗口图标；当 `--tray` 未设置时，关闭 webview 窗口将关闭 Gateway 进程。

## 当前状态

Gateway 有三个相关字段：

| 字段 | 类型 | 默认 | 作用 |
|------|------|------|------|
| `browser` | `bool` | `False` | 启动时调用 `webbrowser.open(addr)` 打开系统浏览器 |
| `tray` | `bool` | `False` | 显示系统托盘图标（需 `--icon`） |
| `icon` | `str \| None` | `None` | 图标文件路径，用作 favicon 和托盘图标 |

`GatewayTray._open_browser()` 也使用 `webbrowser.open()`。

## 目标行为

新增 `webview: bool = False` 字段。约束：`browser=True, webview=True` → 报错。

| # | browser | webview | tray | 行为 |
|---|---------|---------|------|------|
| 1 | True | False | False | 弹出系统浏览器，`sleep_forever()` |
| 2 | True | False | True | 弹出系统浏览器 + 托盘，`tray.wait_stop()` |
| 3 | False | False | False | 不弹窗口，`sleep_forever()` |
| 4 | False | False | True | 不弹窗口 + 托盘，`tray.wait_stop()` |
| 5 | False | True | False | 弹出 webview 窗口，关闭 webview → gateway 退出 |
| 6 | False | True | True | 弹出 webview + 托盘，关闭 webview → 隐藏到托盘；托盘"退出"才退出 |

**特别注意**：case 6 时，托盘左键/右键"打开 {app_name}"应 restore 已隐藏的 webview，而非打开系统浏览器。

## 技术选型

使用 **pywebview**（https://github.com/r0x0r/pywebview），最成熟的 Python webview 库：
- Linux 用 GTK WebKit 后端
- 提供 `events.closing` 可拦截窗口关闭
- `window.hide()` / `window.show()` 可隐藏/恢复窗口

pywebview 作为主依赖加入 `pyproject.toml`。

## 架构变更

### 新文件：`_webview.py` — GatewayWebView

```python
class GatewayWebView:
    """管理 pywebview 窗口生命周期。"""

    def __init__(self, url: str, has_tray: bool = False, icon: str | None = None):
        self._url = url
        self._has_tray = has_tray
        self._icon = icon
        self._window: Any = None
        self._closed_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """后台 daemon 线程启动 webview。若 _thread 非 None 则 raise RuntimeError。"""

    def show(self, _icon: Any = None) -> None:
        """托盘回调：restore 已隐藏的窗口。_icon 接受 pystray 传入的 icon 参数并忽略。"""

    def stop(self) -> None:
        """销毁窗口，join 线程（timeout=2s）。然后 _window=None, _thread=None。"""

    def wait_closed(self) -> None:
        """阻塞直到窗口关闭。用于主 anyio loop。"""

    def is_running(self) -> bool:
        """线程是否存活。"""

    def _on_closing(self) -> bool:
        """窗口关闭事件：有 tray → hide + 阻止关闭；无 tray → 允许关闭。"""
```

**关键设计**：
- `webview.create_window(app_name, url)` 创建窗口，`app_name` 由 `Gateway.app_name` 传入（默认 `"控制台"`）
- `webview.start(icon=self._icon)` 在 daemon 线程中运行，传入 Gateway 的 `--icon` 作为窗口图标
- `events.closing` 中：`_has_tray` → `self._window.hide()` + `return False`；否则 `self._closed_event.set()` + `return True`
- `_closed_event` Event 供主 anyio loop 通过 `anyio.to_thread.run_sync(wv.wait_closed)` 等待退出
- `show(_icon)` 接受忽略的 `_icon` 参数，用于 pystray 直接作为回调

### 修改：`_tray.py` — GatewayTray 加回调

```python
class GatewayTray:
    def __init__(
        self, url: str, icon_path: str,
        on_open: Any = None,
    ):
        ...
        self._on_open = on_open if on_open is not None else self._open_browser

    def _open_browser(self, icon: Any = None) -> None:
        webbrowser.open(self._url)
```

- 新增 `on_open` 可选回调，默认 `_open_browser`
- pystray 菜单直接调用 `self._on_open`（不再是 `_open_browser`）
- 托盘完全不感知 webview
- `start()` 新增 `_thread` 重复调用 guard（`raise RuntimeError`）与 webview 一致
- `stop()` 清理后 `_icon = None`, `_thread = None` 与 webview 一致

### 修改：`__init__.py` — Gateway 编排

新增字段和校验：

```python
webview: bool = False
"""Use a native webview window instead of system browser."""

# 在 run() 中 setup_logging 之后:
if self.browser and self.webview:
    raise ValueError("--browser and --webview are mutually exclusive")
```

编排逻辑变更——当前等待逻辑：
```python
if tray is not None and tray.is_running():
    await anyio.to_thread.run_sync(tray.wait_stop, ...)
else:
    await anyio.sleep_forever()
```

变更为三路：
```python
# 1. webview 初始化（需 --icon）
wv = None
if self.webview:
    if self.icon is None:
        raise ValueError("--webview requires --icon to be set")
    wv = GatewayWebView(addr, has_tray=self.tray, icon=self.icon)
    try:
        wv.start()
    except:
        logger.warning(...)

# 2. 打开 browser / webview（原有 browser 逻辑不变）
if self.browser:
    await anyio.to_thread.run_sync(webbrowser.open, addr)

# 3. tray 初始化，注入 webview 回调
tray = None
if self.tray:
    on_open = wv.show if wv is not None and wv.is_running() else None
    tray = GatewayTray(addr, self.icon, on_open=on_open)
    try:
        tray.start()
    except:
        logger.warning(...)

# 4. 三路等待 + finally 清理
try:
    if tray is not None and tray.is_running():
        await anyio.to_thread.run_sync(tray.wait_stop, ...)
    elif wv is not None and wv.is_running():
        await anyio.to_thread.run_sync(wv.wait_closed, ...)
    else:
        await anyio.sleep_forever()
finally:
    if tray is not None:
        tray.stop()
    if wv is not None:
        wv.stop()
```

## 边缘情况

1. **pywebview 未安装**：`__import__("webview")` 自然抛出 `ImportError`，在 `Gateway.run()` 中被 catch 并记录 warning
2. **webview 线程崩溃**：线程退出后 `is_running()` 返回 False，主 loop 的 `elif wv.is_running()` 分支不命中，回退到 `sleep_forever()`；若同时有 tray 则正常等托盘退出
3. **无桌面环境**：pywebview 在无显示器环境启动失败，Gtk 报错，在 `start()` 中由 `Gateway.run()` catch 并给出 warning
4. **多次关闭窗口**（tray 模式下 hide 后再关闭）：pywebview `events.closing` 每次关闭都会触发，`_has_tray` 判断始终生效

## 不涉及

- Gateway 不在 `_run.py` 批量启动中，无需修改批量配置
- `cli.py` 无需修改——tyro 自动从 dataclass 字段推导 CLI flag
- pywebview 作为主依赖加入 `pyproject.toml`
- SPA 前端无需改动
