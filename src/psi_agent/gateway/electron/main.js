const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const crypto = require('crypto')
const fs = require('fs')
const path = require('path')

const STARTUP_TIMEOUT_MS = 30_000

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
    const appPath = path.join(backendDir, 'psi-agent.app', 'Contents', 'MacOS', 'psi-agent')
    if (fs.existsSync(appPath)) return appPath
    return path.join(backendDir, binary)
  }
  return path.join(backendDir, binary)
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
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
    },
  })

  mainWindow.loadURL(gatewayAddr + '/spa/index.html')

  mainWindow.webContents.on('will-navigate', (_event, url) => {
    if (!url.startsWith(gatewayAddr)) _event.preventDefault()
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

  createWindow()
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
