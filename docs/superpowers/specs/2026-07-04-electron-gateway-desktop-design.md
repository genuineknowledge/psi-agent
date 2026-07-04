# Gateway Electron Desktop 打包设计

**日期**: 2026-07-04
**状态**: approved

## 1. 目标

将 Gateway 打包为 Electron 桌面应用，`psi-agent gateway --desktop` 时启动 Electron 窗口而非浏览器。

## 2. 架构

```
Electron 主进程
├── spawn psi-agent gateway --desktop    (Nuitka 编译的二进制)
│   └── stdout → "GATEWAY_ADDR=http://127.0.0.1:PORT"
├── BrowserWindow.loadURL(GATEWAY_ADDR)   (加载 SPA)
├── Tray (v2 扩展, v1 不实现)
└── on window-all-closed → kill 子进程 → app.quit()
```

- Gateway 作为无头后端仅提供 HTTP 服务
- Electron 负责窗口生命周期
- SPA 通过 HTTP 与 Gateway 通信，不变

## 3. 文件变更清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/psi_agent/gateway/__init__.py` | 修改 | `Gateway` dataclass 新增 `desktop: bool = False`，`--desktop` 时强制 `browser=False`、`tray=None`，成功绑定后 stdout 输出 `GATEWAY_ADDR=<url>` |
| `src/psi_agent/gateway/electron/package.json` | 新建 | Electron 项目配置 + electron-builder 配置 |
| `src/psi_agent/gateway/electron/main.js` | 新建 | 主进程：spawn Gateway 二进制，启动 BrowserWindow |
| `src/psi_agent/gateway/electron/.gitignore` | 新建 | 忽略 node_modules、dist、backend/ |
| `src/psi_agent/gateway/electron/assets/icon.png` | 新建 | 应用图标 |
| `.github/workflows/nuitka.yml` | 修改 | 新增 `electron` job，依赖 `nuitka` job，复用 Nuitka 产物打包 Electron |
| `.github/workflows/pyinstaller.yml` | 修改 | 新增 `electron` job，依赖 `pyinstaller` job，复用 PyInstaller 产物打包 Electron |

## 4. Gateway `--desktop` 行为

```python
@dataclass
class Gateway:
    listen: str = ""
    socket_path: str = "psi"
    verbose: bool = False
    browser: bool = True
    tray: str | None = None
    desktop: bool = False  # NEW

    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        if self.desktop:
            self.browser = False
            self.tray = None

        # ... same binding logic ...

        if self.desktop:
            print(f"GATEWAY_ADDR={addr}", flush=True)  # stdout

        # rest unchanged
```

- `--desktop` 时，`browser`/`tray` 被强制忽略（Electron 自己管窗口生命周期）
- stdout 打印 `GATEWAY_ADDR=http://127.0.0.1:PORT`，单行、flush，Electron 主进程用正则提取
- Gateway 自身不关心 stdout 下游是谁（Electron 或任何启动器），保持解耦

## 5. Electron 主进程 (`main.js`)

```
1. app.isPackaged ?
     backend = path.join(process.resourcesPath, 'backend', 'psi-agent')
   : backend = path.join(__dirname, 'backend', 'psi-agent')

2. const proc = spawn(backend, ['gateway', '--desktop'])

3. 监听 proc.stdout 逐行解析：
   const m = line.match(/GATEWAY_ADDR=(.+)/)
   若匹配到 → const gatewayAddr = m[1]

4. 等待网关就绪：向 gatewayAddr 发送 GET /ais 轮询直到 200

5. createWindow(gatewayAddr)：
   new BrowserWindow({ width: 1200, height: 800, minWidth: 400, minHeight: 300,
       webPreferences: { nodeIntegration: false, contextIsolation: true } })
   win.loadURL(gatewayAddr + '/spa/index.html')
   隐藏 menubar（Linux/Windows），macOS 保留标准菜单栏

6. proc.stderr.pipe(process.stderr) — 转发日志

7. proc.on('exit', (code) => { if (win) win.close(); app.quit() })
   app.on('window-all-closed', () => { proc.kill('SIGTERM'); app.quit() })
   app.on('before-quit', () => { proc.kill('SIGTERM') })
```

关键设计：
- **安全**：`nodeIntegration: false`、`contextIsolation: true`，SPA 无 Node.js 访问权限
- **SPA 不变**：`base: '/spa/'` 已配置，Electron 访问 `/spa/index.html` 正常工作
- **开发模式**：`app.isPackaged` 为 false 时，后端路径指向 `electron/backend/` 目录，便于本地调试

## 6. electron-builder 配置

`electron/package.json` 中：

```json
{
  "name": "psi-gateway",
  "version": "0.1.0",
  "main": "main.js",
  "private": true,
  "scripts": {
    "start": "electron .",
    "build": "electron-builder --publish=never"
  },
  "devDependencies": {
    "electron": "^36.0.0",
    "electron-builder": "^26.0.0"
  },
  "build": {
    "appId": "com.hzhangxyz.psi-gateway",
    "productName": "Psi Gateway",
    "files": ["main.js", "preload.js"],
    "extraResources": [
      {"from": "backend", "to": "backend"}
    ],
    "linux": {
      "target": ["AppImage", "deb"],
      "icon": "assets/icon.png",
      "category": "Utility"
    },
    "win": {
      "target": ["nsis"],
      "icon": "assets/icon.png"
    },
    "mac": {
      "target": ["dmg"],
      "icon": "assets/icon.icns",
      "category": "public.app-category.utilities"
    }
  }
}
```

## 7. CI 流程

在 `nuitka.yml` 末尾追加：

```yaml
  electron:
    needs: nuitka
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    steps:
      - uses: actions/checkout@v7
      - uses: actions/download-artifact@v8
        with:
          name: psi-agent-nuitka-${{ matrix.os }}
          path: backend-temp
      - shell: bash
        run: |
          mkdir -p src/psi_agent/gateway/electron/backend
          if [ "$RUNNER_OS" = "Windows" ]; then
            mv backend-temp/psi-agent.exe src/psi_agent/gateway/electron/backend/
            mv backend-temp/psi-agent.build src/psi_agent/gateway/electron/backend/ 2>/dev/null || true
          else
            mv backend-temp/psi-agent src/psi_agent/gateway/electron/backend/
          fi
      - uses: actions/setup-node@v6
        with:
          node-version: "26"
      - run: npm ci
        working-directory: src/psi_agent/gateway/electron
      - run: npx electron-builder --publish=never
        working-directory: src/psi_agent/gateway/electron
      - uses: actions/upload-artifact@v7
        with:
          name: psi-gateway-electron-${{ matrix.os }}
          path: src/psi_agent/gateway/electron/dist/*
```

PyInstaller 线同理。

## 8. 注意事项

- **仅 Gateway 模式**：`--desktop` 是 `Gateway` 独有的参数，不影响 `psi-agent ai`、`psi-agent session`、`psi-agent channel` 等命令
- **Socket 路径**：Electron 版使用固定 `--socket-path=psi-desktop`，与普通 Gateway 隔离
- **每次窗口打开 = 全新 Gateway 进程**：关闭窗口 kill SIGTERM，Gateway `finally` 正常清理。不持久化后台进程
- **Icon 准备**：需要提供 512x512 或 1024x1024 的 PNG，electron-builder 自动转换为各平台要求的格式（ico/icns）
- **ci.yml 不触发**：Electron 打包仅在 `workflow_dispatch` 和 tag push 时触发（与 nuitka.yml 同触发条件），不在 pushing 时触发
