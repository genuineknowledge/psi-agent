# Gateway Electron Desktop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package Gateway as an Electron desktop app with `--desktop` flag.

**Architecture:** `Gateway` dataclass gains `desktop: bool = False`. When set, it prints `GATEWAY_ADDR=<url>` to stdout instead of opening browser/tray. An Electron main process spawns the Nuitka binary with `--desktop`, parses the addr from stdout, and loads it in a BrowserWindow.

**Tech Stack:** Python (Gateway, tyro CLI), Electron 36, electron-builder 26, Node 26, Nuitka (existing CI)

---

### Task 1: Add `desktop` field to Gateway dataclass

**Files:**
- Modify: `src/psi_agent/gateway/__init__.py`

- [ ] **Step 1: Add field and desktop behavior**

Edit `src/psi_agent/gateway/__init__.py`. Add `desktop` field after `tray`:

```python
    desktop: bool = False
    """Electron desktop mode. Suppresses browser/tray and prints GATEWAY_ADDR=<url> to stdout."""
```

After `setup_logging(verbose=self.verbose)` line, insert override logic:

```python
        if self.desktop:
            self.browser = False
            self.tray = None
```

After `logger.info(f"Gateway listening on {addr}")`, insert desktop stdout print:

```python
                if self.desktop:
                    print(f"GATEWAY_ADDR={addr}", flush=True)
```

The modified `run()` method (full replacement):

```python
    async def run(self) -> None:
        setup_logging(verbose=self.verbose)

        if self.desktop:
            self.browser = False
            self.tray = None

        addr = self.listen or f"http://127.0.0.1:{_random_port()}"
        logger.info(f"Starting Gateway service on {addr} (socket_path={self.socket_path})")

        async with anyio.create_task_group() as tg:
            aim = AIManager(_prefix=self.socket_path, _tg=tg)
            sm = SessionManager(_aim=aim, _prefix=self.socket_path, _tg=tg)

            app = await create_app(aim, sm, favicon_path=self.tray)
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

                if self.desktop:
                    print(f"GATEWAY_ADDR={addr}", flush=True)

                if self.browser:
                    await anyio.to_thread.run_sync(webbrowser.open, addr)  # ty: ignore

                tray = None
                if self.tray:
                    tray = GatewayTray(addr, self.tray)
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
            finally:
                logger.info("Shutting down Gateway")
                with anyio.CancelScope(shield=True):
                    await runner.cleanup()
                tg.cancel_scope.cancel()
        logger.info("Gateway shutdown complete")
```

- [ ] **Step 2: Verify ruff + ty pass**

```bash
uv run ruff check src/psi_agent/gateway/__init__.py && uv run ruff format --check src/psi_agent/gateway/__init__.py && uv run ty check src/psi_agent/gateway/__init__.py
```

- [ ] **Step 3: Commit**

```bash
git add src/psi_agent/gateway/__init__.py
git commit -m "feat(gateway): add --desktop flag for Electron mode"
```

---

### Task 2: Write Gateway `--desktop` unit test

**Files:**
- Create: `tests/psi_agent/gateway/test_desktop.py`

- [ ] **Step 1: Write the test**

Create `tests/psi_agent/gateway/test_desktop.py`:

```python
from __future__ import annotations

import socket

import anyio
import pytest

from psi_agent.gateway import Gateway


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]
    finally:
        s.close()


def test_desktop_field_exists_and_defaults_false():
    g = Gateway()
    assert g.desktop is False


def test_desktop_constructor_accepts_params():
    g = Gateway(desktop=True, browser=True, tray="/some/icon.png")
    assert g.desktop is True


@pytest.mark.anyio
async def test_desktop_flag_outputs_gateway_addr(capsys):
    port = _free_port()
    g = Gateway(desktop=True, listen=f"http://127.0.0.1:{port}")

    async with anyio.create_task_group() as tg:
        tg.start_soon(g.run)
        await anyio.sleep(0.5)
        tg.cancel_scope.cancel()

    captured = capsys.readouterr()
    assert f"GATEWAY_ADDR=http://127.0.0.1:{port}" in captured.out
```

- [ ] **Step 2: Run test to verify it passes**

```bash
uv run pytest tests/psi_agent/gateway/test_desktop.py -v
```
Expected: 3 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/psi_agent/gateway/test_desktop.py
git commit -m "test: add --desktop flag unit tests"
```

---

### Task 3: Scaffold Electron project

**Files:**
- Create: `src/psi_agent/gateway/electron/package.json`
- Create: `src/psi_agent/gateway/electron/main.js`
- Create: `src/psi_agent/gateway/electron/.gitignore`
- Create: `src/psi_agent/gateway/electron/assets/.gitkeep`

- [ ] **Step 1: Create directory and .gitignore**

```bash
mkdir -p src/psi_agent/gateway/electron/assets
```

Create `src/psi_agent/gateway/electron/.gitignore`:

```
node_modules/
dist/
backend/
```

Create `src/psi_agent/gateway/electron/assets/.gitkeep` (empty placeholder for icon).

- [ ] **Step 2: Write package.json**

Create `src/psi_agent/gateway/electron/package.json`:

```json
{
  "name": "psi-gateway",
  "version": "0.1.0",
  "description": "Psi Gateway desktop application",
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
    "files": [
      "main.js"
    ],
    "extraResources": [
      {
        "from": "backend",
        "to": "backend"
      }
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

- [ ] **Step 3: Write main.js**

Create `src/psi_agent/gateway/electron/main.js`:

```javascript
const { app, BrowserWindow } = require('electron')
const { spawn } = require('child_process')
const path = require('path')

let mainWindow = null
let gatewayProc = null
let gatewayAddr = null

function resolveBackendPath() {
  const ext = process.platform === 'win32' ? '.exe' : ''
  const binary = `psi-agent${ext}`
  const backendDir = app.isPackaged
    ? path.join(process.resourcesPath, 'backend')
    : path.join(__dirname, 'backend')

  if (process.platform === 'darwin') {
    return path.join(backendDir, 'psi-agent.app', 'Contents', 'MacOS', 'psi-agent')
  }
  return path.join(backendDir, binary)
}

function startGateway() {
  const backend = resolveBackendPath()

  gatewayProc = spawn(backend, ['gateway', '--desktop', '--socket-path=psi-desktop'], {
    stdio: ['pipe', 'pipe', 'pipe'],
  })

  return new Promise((resolve, reject) => {
    let resolved = false

    gatewayProc.stdout.on('data', (data) => {
      const lines = data.toString().split('\n')
      for (const line of lines) {
        const m = line.match(/GATEWAY_ADDR=(.+)/)
        if (m && !resolved) {
          gatewayAddr = m[1].trim()
          resolved = true
          resolve(gatewayAddr)
        }
      }
    })

    gatewayProc.stderr.pipe(process.stderr)

    gatewayProc.on('error', (err) => {
      if (!resolved) reject(err)
    })

    gatewayProc.on('exit', (code) => {
      if (!resolved) reject(new Error(`Gateway exited with code ${code} before printing addr`))
    })
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 400,
    minHeight: 300,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  mainWindow.loadURL(gatewayAddr + '/spa/index.html')

  if (process.platform !== 'darwin') {
    mainWindow.setMenuBarVisibility(false)
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function cleanup() {
  if (gatewayProc && !gatewayProc.killed) {
    gatewayProc.kill('SIGTERM')
  }
}

app.whenReady().then(async () => {
  try {
    await startGateway()
    createWindow()
  } catch (err) {
    console.error('Failed to start Gateway:', err.message)
    app.quit()
  }
})

app.on('window-all-closed', () => {
  cleanup()
  app.quit()
})

app.on('before-quit', () => {
  cleanup()
})

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow()
  }
})
```

- [ ] **Step 4: Commit**

```bash
git add src/psi_agent/gateway/electron/
git commit -m "feat(electron): scaffold Electron project with main process"
```

---

### Task 4: Add Electron packaging job to nuitka.yml

**Files:**
- Modify: `.github/workflows/nuitka.yml`

- [ ] **Step 1: Append electron job**

Add after the `haitun-inno-setup` job (after line 119):

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
            mv backend-temp/psi-agent.dist src/psi_agent/gateway/electron/backend/ 2>/dev/null || true
            mv backend-temp/psi-agent.onefile-build src/psi_agent/gateway/electron/backend/ 2>/dev/null || true
          elif [ "$RUNNER_OS" = "macOS" ]; then
            cp -a backend-temp/psi-agent.app src/psi_agent/gateway/electron/backend/
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

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/nuitka.yml
git commit -m "ci: add Electron packaging job to nuitka workflow"
```

---

### Task 5: Add Electron packaging job to pyinstaller.yml

**Files:**
- Modify: `.github/workflows/pyinstaller.yml`

- [ ] **Step 1: Append electron job**

Add after the `haitun-inno-setup` job (after line 98):

```yaml
  electron:
    needs: pyinstaller
    runs-on: ${{ matrix.os }}
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
    steps:
      - uses: actions/checkout@v7
      - uses: actions/download-artifact@v8
        with:
          name: psi-agent-pyinstaller-${{ matrix.os }}
          path: backend-temp
      - shell: bash
        run: |
          mkdir -p src/psi_agent/gateway/electron/backend
          if [ "$RUNNER_OS" = "Windows" ]; then
            mv backend-temp/psi-agent.exe src/psi_agent/gateway/electron/backend/
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
          name: psi-gateway-electron-pyinstaller-${{ matrix.os }}
          path: src/psi_agent/gateway/electron/dist/*
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/pyinstaller.yml
git commit -m "ci: add Electron packaging job to pyinstaller workflow"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run all existing tests**

```bash
uv run pytest -v -m "not schedule"
```
Expected: all existing tests still PASS.

- [ ] **Step 2: Run lint + typecheck on all changed files**

```bash
uv run ruff check . && uv run ruff format --check . && uv run ty check .
```
Expected: all PASS, no errors.
