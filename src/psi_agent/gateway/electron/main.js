const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const crypto = require('crypto')
const fs = require('fs')
const path = require('path')

if (process.platform === 'linux' && process.env.WAYLAND_DISPLAY) {
  app.commandLine.appendSwitch('ozone-platform', 'wayland')
}

const STARTUP_TIMEOUT_MS = 30_000

const LOADING_HTML = `<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body {
    display: flex; flex-direction: column; align-items: center; justify-content: center;
    height: 100vh; width: 100vw;
    background: linear-gradient(135deg, #0d1117 0%, #161b22 100%);
    color: #c9d1d9; font-family: system-ui, -apple-system, sans-serif;
    user-select: none; -webkit-user-select: none;
  }
  .title { font-size: 28px; font-weight: 300; letter-spacing: 1px; margin-bottom: 32px; }
  .dots { display: flex; gap: 8px; }
  .dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: #6366f1;
    animation: pulse 1.4s ease-in-out infinite;
  }
  .dot:nth-child(2) { animation-delay: 0.2s; }
  .dot:nth-child(3) { animation-delay: 0.4s; }
  @keyframes pulse {
    0%, 100% { opacity: 0.2; transform: scale(0.8); }
    50% { opacity: 1; transform: scale(1.2); }
  }
</style></head><body>
  <div class="title">Psi Agent</div>
  <div class="dots"><div class="dot"></div><div class="dot"></div><div class="dot"></div></div>
</body></html>`

let mainWindow = null
let gatewayProc = null
let gatewayAddr = null

function resolveBackendPath() {
  const ext = process.platform === 'win32' ? '.exe' : ''
  const binary = `psi-agent${ext}`

  const devPath = path.join(__dirname, 'backend', binary)
  const devAppPath = path.join(__dirname, 'backend', 'psi-agent.app', 'Contents', 'MacOS', 'psi-agent')

  const darwinDevPath = process.platform === 'darwin' && fs.existsSync(devAppPath)
    ? devAppPath : (fs.existsSync(devPath) ? devPath : null)

  if (darwinDevPath) return darwinDevPath
  if (!app.isPackaged) return devPath

  const resourcesPath = path.join(process.resourcesPath, 'backend')
  if (process.platform === 'darwin') {
    const appPath = path.join(resourcesPath, 'psi-agent.app', 'Contents', 'MacOS', 'psi-agent')
    if (fs.existsSync(appPath)) return appPath
    return path.join(resourcesPath, binary)
  }
  return path.join(resourcesPath, binary)
}

function startGateway() {
  const backend = resolveBackendPath()
  const socketPath = `psi-desktop-${crypto.randomUUID()}`

  gatewayProc = spawn(backend, ['gateway', '--desktop', `--socket-path=${socketPath}`], {
    stdio: ['ignore', 'pipe', 'pipe'],
  })

  gatewayProc.stderr.pipe(process.stderr)

  return new Promise((resolve, reject) => {
    let resolved = false
    let stdoutBuf = ''

    const finish = (fn) => {
      if (resolved) return
      resolved = true
      fn()
    }

    const timeoutHandle = setTimeout(() => {
      finish(() => {
        cleanupStderr()
        gatewayProc.kill('SIGTERM')
        reject(new Error('Gateway did not report address within 30s'))
      })
    }, STARTUP_TIMEOUT_MS)

    gatewayProc.stdout.on('data', (data) => {
      stdoutBuf += data.toString()
      let idx
      while ((idx = stdoutBuf.indexOf('\n')) >= 0) {
        const line = stdoutBuf.slice(0, idx)
        stdoutBuf = stdoutBuf.slice(idx + 1)
        const m = line.match(/^GATEWAY_ADDR=(.+)$/)
        if (m) {
          gatewayAddr = m[1].trim()
          finish(() => {
            clearTimeout(timeoutHandle)
            resolve(gatewayAddr)
          })
          return
        }
      }
    })

    gatewayProc.on('error', (err) => {
      finish(() => {
        clearTimeout(timeoutHandle)
        reject(err)
      })
    })

    gatewayProc.on('exit', (code) => {
      finish(() => {
        clearTimeout(timeoutHandle)
        reject(new Error(`Gateway exited with code ${code} before printing address`))
      })
    })
  })
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    minWidth: 400,
    minHeight: 300,
    show: false,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  })

  mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(LOADING_HTML)}`)

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.on('will-navigate', (_event, url) => {
    if (!url.startsWith(gatewayAddr) && !url.startsWith('data:')) _event.preventDefault()
  })

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription) => {
    console.error(`Page load failed (${errorCode}): ${errorDescription}`)
  })

  if (process.platform !== 'darwin') {
    mainWindow.setMenuBarVisibility(false)
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function navigateToSPA() {
  mainWindow.loadURL(gatewayAddr + '/spa/index.html')
}

function cleanupStderr() {
  if (gatewayProc && gatewayProc.stderr) {
    gatewayProc.stderr.unpipe()
    gatewayProc.stderr.destroy()
  }
}

function cleanup() {
  cleanupStderr()
  if (gatewayProc && !gatewayProc.killed) {
    gatewayProc.kill('SIGTERM')
  }
}

app.whenReady().then(async () => {
  createWindow()

  try {
    await startGateway()
  } catch (err) {
    dialog.showErrorBox('Startup Error', `Failed to start Gateway:\n${err.message}`)
    app.quit()
    return
  }

  gatewayProc.on('exit', (code) => {
    if (mainWindow) {
      dialog.showErrorBox('Gateway Error', `Gateway process exited unexpectedly (code ${code}).`)
    }
  })

  navigateToSPA()
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    cleanup()
    app.quit()
  }
})

app.on('before-quit', () => {
  cleanup()
})

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow()
  }
})
