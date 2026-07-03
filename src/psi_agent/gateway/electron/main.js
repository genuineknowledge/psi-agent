"use strict"

const { app, BrowserWindow, dialog } = require("electron")
const fs = require("node:fs")
const http = require("node:http")
const net = require("node:net")
const path = require("node:path")
const { spawn } = require("node:child_process")

const repoRoot = path.resolve(__dirname, "..", "..", "..", "..")
const isWindows = process.platform === "win32"

let backendProcess = null
let backendLogStream = null
let backendErrStream = null

function getArgValue(name) {
  const prefix = `--${name}=`
  const arg = process.argv.find((entry) => entry.startsWith(prefix))
  return arg ? arg.slice(prefix.length) : null
}

function timestamp() {
  const now = new Date()
  const pad = (value) => String(value).padStart(2, "0")
  return [
    now.getFullYear(),
    pad(now.getMonth() + 1),
    pad(now.getDate()),
    "-",
    pad(now.getHours()),
    pad(now.getMinutes()),
    pad(now.getSeconds()),
  ].join("")
}

function ensureDirectory(dirPath) {
  fs.mkdirSync(dirPath, { recursive: true })
}

function pathExists(filePath) {
  try {
    fs.accessSync(filePath)
    return true
  } catch {
    return false
  }
}

function copyDirectory(sourceDir, destinationDir) {
  fs.cpSync(sourceDir, destinationDir, {
    recursive: true,
    force: false,
    errorOnExist: false,
  })
}

function getWorkspaceTemplateDir() {
  if (app.isPackaged) {
    return path.join(process.resourcesPath, "workspace-template")
  }
  return path.join(repoRoot, "examples", "haitun-workspace")
}

function getAppIconPath() {
  if (app.isPackaged) {
    const packagedIcon = path.join(process.resourcesPath, "app-icon.ico")
    if (pathExists(packagedIcon)) {
      return packagedIcon
    }
  }

  const sourceIcon = path.join(repoRoot, ".github", "inno-setup", "haitun.ico")
  if (pathExists(sourceIcon)) {
    return sourceIcon
  }
  return null
}

function ensureRuntimeWorkspace() {
  const workspaceDir = path.join(app.getPath("userData"), "workspace")
  const templateDir = getWorkspaceTemplateDir()

  if (!pathExists(workspaceDir)) {
    copyDirectory(templateDir, workspaceDir)
  }
  ensureDirectory(path.join(workspaceDir, "histories"))
  return workspaceDir
}

function prependBundledRuntimePaths(env) {
  const nextEnv = { ...env }
  const msysUsrBin = path.join(process.resourcesPath, "msys64", "usr", "bin")
  const msysUcrtBin = path.join(process.resourcesPath, "msys64", "ucrt64", "bin")
  const extraPaths = [msysUsrBin, msysUcrtBin].filter(pathExists)
  if (extraPaths.length > 0) {
    nextEnv.PATH = `${extraPaths.join(path.delimiter)}${path.delimiter}${nextEnv.PATH || ""}`
    nextEnv.CHERE_INVOKING = "1"
  }
  return nextEnv
}

function getBackendSpec(port, workspaceDir) {
  const listenAddr = `http://127.0.0.1:${port}`
  if (app.isPackaged) {
    const exeName = isWindows ? "psi-agent.exe" : "psi-agent"
    return {
      command: path.join(process.resourcesPath, "backend", exeName),
      args: ["gateway", "--listen", listenAddr, "--no-browser"],
      cwd: workspaceDir,
      env: prependBundledRuntimePaths(process.env),
    }
  }
  return {
    command: "uv",
    args: ["run", "--project", repoRoot, "psi-agent", "gateway", "--listen", listenAddr, "--no-browser"],
    cwd: workspaceDir,
    env: { ...process.env },
  }
}

function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer()
    server.unref()
    server.on("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const address = server.address()
      const port = address && typeof address === "object" ? address.port : null
      server.close((error) => {
        if (error) {
          reject(error)
          return
        }
        if (port === null) {
          reject(new Error("Failed to resolve a free local port."))
          return
        }
        resolve(port)
      })
    })
  })
}

function waitForGateway(url, timeoutMs) {
  return new Promise((resolve, reject) => {
    const startedAt = Date.now()

    const tryOnce = () => {
      const request = http.get(url, (response) => {
        response.resume()
        if (response.statusCode === 200) {
          resolve()
          return
        }
        retry(new Error(`Gateway responded with HTTP ${response.statusCode}`))
      })
      request.on("error", retry)
    }

    const retry = (error) => {
      if (Date.now() - startedAt >= timeoutMs) {
        reject(error)
        return
      }
      setTimeout(tryOnce, 400)
    }

    tryOnce()
  })
}

function createBackendLogStreams() {
  const logsDir = path.join(app.getPath("userData"), "logs")
  ensureDirectory(logsDir)
  const baseName = timestamp()
  backendLogStream = fs.createWriteStream(path.join(logsDir, `${baseName}.out.log`))
  backendErrStream = fs.createWriteStream(path.join(logsDir, `${baseName}.err.log`))
}

function closeBackendLogStreams() {
  if (backendLogStream) {
    backendLogStream.end()
    backendLogStream = null
  }
  if (backendErrStream) {
    backendErrStream.end()
    backendErrStream = null
  }
}

function killBackendProcess() {
  if (!backendProcess) {
    closeBackendLogStreams()
    return
  }

  const pid = backendProcess.pid
  if (backendProcess.exitCode === null) {
    if (isWindows && pid) {
      spawn("taskkill", ["/pid", String(pid), "/T", "/F"], { windowsHide: true })
    } else {
      backendProcess.kill("SIGTERM")
    }
  }

  backendProcess = null
  closeBackendLogStreams()
}

async function startBackend() {
  const workspaceDir = ensureRuntimeWorkspace()
  const port = await getFreePort()
  const spec = getBackendSpec(port, workspaceDir)
  const healthUrl = `http://127.0.0.1:${port}/openapi.json`

  createBackendLogStreams()

  backendProcess = spawn(spec.command, spec.args, {
    cwd: spec.cwd,
    env: spec.env,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true,
  })

  backendProcess.stdout.on("data", (chunk) => {
    if (backendLogStream) {
      backendLogStream.write(chunk)
    }
  })
  backendProcess.stderr.on("data", (chunk) => {
    if (backendErrStream) {
      backendErrStream.write(chunk)
    }
  })

  backendProcess.once("exit", (code, signal) => {
    if (backendErrStream) {
      backendErrStream.write(
        Buffer.from(`\n[desktop] backend exited (code=${code}, signal=${signal})\n`, "utf-8"),
      )
    }
  })

  try {
    await waitForGateway(healthUrl, 30000)
  } catch (error) {
    killBackendProcess()
    throw error
  }

  return {
    healthUrl,
    uiUrl: `http://127.0.0.1:${port}/spa/index.html`,
  }
}

async function createMainWindow(uiUrl) {
  const icon = getAppIconPath()
  const mainWindow = new BrowserWindow({
    width: 1440,
    height: 960,
    minWidth: 1024,
    minHeight: 720,
    title: "psi-agent Desktop",
    autoHideMenuBar: true,
    ...(icon ? { icon } : {}),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  })

  mainWindow.removeMenu()
  await mainWindow.loadURL(uiUrl)
}

async function bootstrap() {
  try {
    const externalUiUrl = getArgValue("url")
    if (externalUiUrl) {
      await createMainWindow(externalUiUrl)
      return
    }
    const urls = await startBackend()
    await createMainWindow(urls.uiUrl)
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    dialog.showErrorBox("psi-agent Desktop Failed to Start", message)
    app.quit()
  }
}

app.on("window-all-closed", () => {
  app.quit()
})

app.on("before-quit", () => {
  killBackendProcess()
})

if (isWindows) {
  app.setAppUserModelId("com.psiagent.desktop")
}

app.whenReady().then(bootstrap)
