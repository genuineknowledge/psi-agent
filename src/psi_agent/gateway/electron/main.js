const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const crypto = require('crypto')
const fs = require('fs')
const path = require('path')

if (process.platform === 'linux' && process.env.WAYLAND_DISPLAY) {
  app.commandLine.appendSwitch('ozone-platform', 'wayland')
}

const singleInstance = app.requestSingleInstanceLock()
if (!singleInstance) {
  app.quit()
  return
}

const STARTUP_TIMEOUT_MS = 30_000
const STDOUT_MAX_BUF = 100_000

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

const LOADING_URL = `data:text/html;charset=utf-8,${encodeURIComponent(LOADING_HTML)}`

let mainWindow = null
let gatewayProc = null
let gatewayAddr = null
let shuttingDown = false

function isGatewayAlive() {
  return gatewayProc && gatewayProc.exitCode === null && gatewayProc.signalCode === null
}

function resolveBackendPath() {
  const ext = process.platform === 'win32' ? '.exe' : ''
  const binary = `psi-agent${ext}`

  if (app.isPackaged) {
    const dir = path.join(process.resourcesPath, 'backend')
    if (process.platform === 'darwin') {
      return path.join(dir, 'psi-agent.app', 'Contents', 'MacOS', 'psi-agent')
    }
    return path.join(dir, binary)
  }

  const devDir = path.join(__dirname, 'backend')
  if (process.platform === 'darwin') {
    const appPath = path.join(devDir, 'psi-agent.app', 'Contents', 'MacOS', 'psi-agent')
    if (fs.existsSync(appPath)) return appPath
  }
  return path.join(devDir, binary)
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
        cleanupGateway()
        reject(new Error(`Gateway did not report address within ${STARTUP_TIMEOUT_MS / 1000}s`))
      })
    }, STARTUP_TIMEOUT_MS)

    const onCrash = (code) => {
      gatewayAddr = null
      if (!shuttingDown && mainWindow) {
        dialog.showErrorBox('Gateway Error', `Gateway process exited unexpectedly (code ${code}).`)
        mainWindow.loadURL(LOADING_URL)
      }
    }

    const onStdoutData = (data) => {
      stdoutBuf += data.toString()
      let idx
      while ((idx = stdoutBuf.indexOf('\n')) >= 0) {
        const line = stdoutBuf.slice(0, idx)
        stdoutBuf = stdoutBuf.slice(idx + 1)
        const m = line.match(/^GATEWAY_ADDR=(.+)$/)
        if (m) {
          gatewayAddr = m[1].trim().replace(/\/$/, '')
          if (!gatewayAddr) {
            finish(() => {
              clearTimeout(timeoutHandle)
              cleanupGateway()
              reject(new Error('Gateway reported empty address'))
            })
            return
          }
          gatewayProc.stdout.removeListener('data', onStdoutData)
          gatewayProc.stdout.removeListener('error', onStartupError)
          gatewayProc.removeListener('error', onStartupError)
          gatewayProc.removeListener('exit', onStartupExit)
          gatewayProc.stdout.on('error', () => {})
          gatewayProc.on('exit', onCrash)
          finish(() => {
            clearTimeout(timeoutHandle)
            resolve(gatewayAddr)
          })
          return
        }
      }
      if (stdoutBuf.length > STDOUT_MAX_BUF) {
        finish(() => {
          clearTimeout(timeoutHandle)
          cleanupGateway()
          reject(new Error('Gateway stdout exceeded max buffer size'))
        })
      }
    }

    const onStartupError = (err) => {
      finish(() => {
        clearTimeout(timeoutHandle)
        reject(err)
      })
    }

    const onStartupExit = (code) => {
      finish(() => {
        clearTimeout(timeoutHandle)
        reject(new Error(`Gateway exited with code ${code} before printing address`))
      })
    }

    gatewayProc.stdout.on('data', onStdoutData)
    gatewayProc.stdout.on('error', onStartupError)
    gatewayProc.on('error', onStartupError)
    gatewayProc.on('exit', onStartupExit)
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

  mainWindow.loadURL(LOADING_URL)

  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }))

  mainWindow.once('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.webContents.on('will-navigate', (_event, url) => {
    const allowed = (gatewayAddr && url.startsWith(gatewayAddr + '/'))
      || url === LOADING_URL
    if (!allowed) _event.preventDefault()
  })

  mainWindow.webContents.on('will-redirect', (_event, url) => {
    if (!gatewayAddr || !url.startsWith(gatewayAddr + '/')) _event.preventDefault()
  })

  mainWindow.webContents.on('did-fail-load', (_event, errorCode, errorDescription) => {
    if (!shuttingDown && mainWindow && gatewayAddr) {
      dialog.showErrorBox('Connection Error', `Failed to load Gateway (${errorCode}):\n${errorDescription}`)
    }
  })

  if (process.platform !== 'darwin') {
    mainWindow.setMenuBarVisibility(false)
  }

  mainWindow.on('closed', () => {
    mainWindow = null
  })
}

function navigateToSPA() {
  if (mainWindow && gatewayAddr) {
    mainWindow.loadURL(gatewayAddr + '/spa/index.html')
  }
}

function cleanupGateway() {
  if (gatewayProc && gatewayProc.stderr && !gatewayProc.stderr.destroyed) {
    gatewayProc.stderr.unpipe()
    gatewayProc.stderr.destroy()
  }
  if (gatewayProc && !gatewayProc.killed) {
    gatewayProc.kill('SIGTERM')
  }
}

function cleanup() {
  shuttingDown = true
  cleanupGateway()
}

app.on('second-instance', () => {
  if (mainWindow) {
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.focus()
  }
})

app.whenReady().then(async () => {
  createWindow()

  try {
    await startGateway()
  } catch (err) {
    if (!shuttingDown) {
      dialog.showErrorBox('Startup Error', `Failed to start Gateway:\n${err.message}`)
    }
    app.quit()
    return
  }

  navigateToSPA()
}).catch(() => {
  app.quit()
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
    if (isGatewayAlive() && gatewayAddr) {
      navigateToSPA()
    }
  }
})
