# Gateway 系统托盘设计

## 目标

Gateway 启动后显示系统托盘图标，用户左键点击可打开浏览器访问 Gateway Web Console，右键显示中文菜单（"打开控制台"、"退出"），托盘退出时关闭整个 Gateway 进程。

## 依赖

新增 `pystray` + `Pillow`：
- `pystray`：跨平台系统托盘库（Windows / macOS / Linux）
- `Pillow`：程序化生成 psi 图标（无外部 png 依赖），自动适配系统要求尺寸

## 架构

```
Gateway 进程
├── anyio event loop (主线程)
│   ├── AIManager + SessionManager
│   ├── aiohttp REST Server
│   └── await anyio.sleep_forever()  ← 主循环
└── gateway-tray thread (daemon)
    └── pystray.Icon.run()
        ├── 左键点击 → webbrowser.open(url)
        └── 右键菜单
            ├── "打开控制台" → webbrowser.open(url)
            └── "退出"       → 设置 stop_event
```

pystray 在独立线程中运行（其内部 `Icon.run()` 阻塞主线程），与 anyio 事件循环并行。

## 新增模块：`src/psi_agent/gateway/_tray.py`

```python
class GatewayTray:
    def __init__(self, url: str):
        self._url = url
        self._stop_event = threading.Event()
        self._icon = None
        self._thread = None

    def start(self) -> None:
        """在独立线程中启动 pystray"""
        icon = _create_psi_icon()  # Pillow 程序化生成
        menu = pystray.Menu(
            pystray.MenuItem("打开控制台", self._open_browser, default=True),
            pystray.MenuItem("退出", self._quit),
        )
        self._icon = pystray.Icon("psi-agent", icon, "psi-agent", menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """停止托盘（从 anyio 主线程调用）"""
        self._stop_event.set()
        if self._icon:
            self._icon.stop()
        if self._thread:
            self._thread.join(timeout=2)

    def _open_browser(self) -> None:
        webbrowser.open(self._url)

    def _quit(self) -> None:
        self._icon.stop()
        self._stop_event.set()
```

图标通过 Pillow 纯程序生成 —— 一个 64x64 的 psi 文字图标，按需 resize 到系统托盘所需尺寸。

## 修改 `Gateway.run()` 生命周期

```
1. setup_logging(verbose)
2. anyio.create_task_group()
3. AIManager + SessionManager
4. create_app + runner.setup() + site.start()
5. logger.info("Gateway listening on {addr}")
6. if self.browser: webbrowser.open(addr)   ← 保留原自动打开
7. tray = GatewayTray(addr); tray.start()    ← 新增托盘
8. try:
       await anyio.sleep_forever()
   finally:
       tray.stop()                           ← 先停托盘
       runner.cleanup()
       tg.__aexit__()
```

- `self.browser` 参数不变，仅控制启动时是否自动打开浏览器
- 托盘始终启用（无论 browser 是真还是假）
- `finally` 中先 `tray.stop()` 确保托盘退出后再清理 runner 和 task group
- `tray.stop()` 不需要 shield 保护（跨线程通信，无 cancel 风险）

## CLI 不变

```
psi-agent gateway [--listen ...] [--socket-path ...] [--verbose] [--no-browser]
```

`--browser` / `--no-browser` 含义不变。

## 异常安全

- `tray.stop()` 放在 `finally` 中，确保无论 Gateway 如何退出（正常 / cancel / 异常），托盘都会被清理
- 如果 `tray.start()` 抛异常（如系统不支持托盘），Gateway 继续正常运行（不阻塞启动）
- pystray 在无桌面环境的 Linux 服务器上可能失败 —— 应 catch 并 log warning，不阻止 Gateway 启动

## 测试策略

- **单元测试**：`GatewayTray` 的公开方法签名、图标生成尺寸
- **集成测试**：暂无（pystray 需要真实桌面环境，CI 中不执行）
- **手动验证**：启动 Gateway，确认托盘图标出现、左键打开浏览器、右键菜单功能正常
