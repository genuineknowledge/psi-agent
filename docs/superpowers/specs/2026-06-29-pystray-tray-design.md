# Gateway 系统托盘设计

## 目标

Gateway 启动时可通过 `--tray` 参数指定图标文件，在系统托盘显示图标。左键点击打开浏览器，右键菜单可退出 Gateway。`--tray` 未设置时不创建托盘。

## 依赖

`pystray` + `Pillow`：
- `pystray`：跨平台系统托盘库（Windows / macOS / Linux）
- `Pillow`：加载用户指定的图标文件

## 架构

```
Gateway 进程
├── anyio event loop (主线程)
│   ├── AIManager + SessionManager
│   ├── aiohttp REST Server
│   └── if stray: tray._stop_event.wait()  ← 托盘退出时 shutdown
│       else:     await anyio.sleep_forever()  ← 通过 cancel 退出
└── gateway-tray thread (daemon) [仅当 --tray 设置时]
    └── pystray.Icon.run()
        ├── 左键点击 → webbrowser.open(url)
        └── 右键菜单
            ├── "打开控制台" → webbrowser.open(url)
            └── "退出"       → 设置 stop_event
```

## 模块：`src/psi_agent/gateway/_tray.py`

```python
class GatewayTray:
    def __init__(self, url: str, icon_path: str):
        self._url = url
        self._icon_path = icon_path
        self._stop_event = threading.Event()
        self._icon = None
        self._thread = None

    def start(self) -> None:
        """在独立线程中启动 pystray"""
        image = Image.open(self._icon_path)  # 用户指定图标
        menu = pystray.Menu(
            pystray.MenuItem("打开控制台", self._open_browser, default=True),
            pystray.MenuItem("退出", self._quit),
        )
        self._icon = pystray.Icon("psi-agent", image, "psi-agent", menu)
        self._thread = threading.Thread(target=self._icon.run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._icon:
            with contextlib.suppress(Exception):
                self._icon.stop()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2)

    def is_stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def _open_browser(self) -> None:
        webbrowser.open(self._url)

    def _quit(self) -> None:
        self._stop_event.set()
        if self._icon:
            with contextlib.suppress(Exception):
                self._icon.stop()
```

删除 `_create_icon_image()` 函数（不再使用 Pillow 程序化生成图标）。

## 修改 `Gateway.run()` 生命周期

```
1. setup_logging(verbose)
2. anyio.create_task_group()
3. AIManager + SessionManager
4. create_app + runner.setup() + site.start()
5. logger.info("Gateway listening on {addr}")
6. if self.browser: webbrowser.open(addr)
7. if self.tray:
       tray = GatewayTray(addr, self.tray)
       try: tray.start()
       except Exception: logger.warning(...)
   else: tray = None
8. try:
       if tray is not None and tray._thread is not None:
           await anyio.to_thread.run_sync(tray._stop_event.wait)
       else:
           await anyio.sleep_forever()
   finally:
       if tray is not None: tray.stop()
       runner.cleanup()  (shielded)
       tg.__aexit__()    (shielded)
```

## CLI

```
psi-agent gateway --tray /path/to/icon.png  # 启用托盘
psi-agent gateway --tray ~/icon.png          # 同上
psi-agent gateway                              # 不启用托盘
psi-agent gateway --no-browser --tray icon.png  # 组合使用
```

`--tray` 接受图片文件路径（png/jpg/ico 等 Pillow 支持的格式）。不设置时无托盘。

## 异常安全

- `tray.stop()` 放在 `finally` 中，确保无论 Gateway 如何退出，托盘都会被清理
- 如果 `tray.start()` 抛异常（如系统不支持托盘、图标文件无效），Gateway 继续正常运行（不阻塞启动）
- pystray 在无桌面环境上可能失败 —— catch 并 log warning，不阻止 Gateway 启动
- 无 `--tray` 时 `anyio.sleep_forever()` 通过 cancel 退出，`finally` 正常清理

## 测试策略

- **单元测试**：`GatewayTray` 构造方法、`is_stop_requested`、`stop()`、`_quit()` 回调
- 删除 `_create_icon_image` 相关测试
- `GatewayTray` 构造需要传入 `icon_path`，测试用临时图片文件
- **集成测试**：暂无（pystray 需要真实桌面环境，CI 中不执行）
- **手动验证**：`psi-agent gateway --tray icon.png`，确认托盘图标出现、功能正常
