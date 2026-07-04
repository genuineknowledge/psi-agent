const { app, BrowserWindow } = require('electron')
const { spawn } = require('child_process')
const fs = require('fs')
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
    const appPath = path.join(backendDir, 'psi-agent.app', 'Contents', 'MacOS', 'psi-agent')
    if (fs.existsSync(appPath)) return appPath
    return path.join(backendDir, binary)
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
