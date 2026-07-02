# Electron Desktop Packaging

This repo now includes a dedicated Electron shell that loads the current Gateway-hosted SPA in a desktop window.

## Architecture

The Electron app does not replace the existing frontend/backend split. Instead it wraps the current production path:

1. Electron starts a local `psi-agent` Gateway process
2. Gateway serves the already-built SPA from `spa/dist`
3. `BrowserWindow` loads `http://127.0.0.1:<port>/spa/index.html`

This matches the current frontend design, because the SPA resolves API calls from `window.location.origin`.

## Files

- Electron shell: `desktop/`
- Resource preparation script: `packaging/windows/prepare-electron-resources.ps1`

Key files:

- `desktop/package.json`
- `desktop/main.js`
- `desktop/installer.nsh`
- `desktop/preload.js`
- `packaging/windows/prepare-electron-resources.ps1`

## What gets bundled

The Electron package includes:

- a frozen `psi-agent.exe` backend built with PyInstaller
- the current `examples/haitun-workspace` as a workspace template
- the shared dolphin icon used by the installer, app window, and installed shortcuts
- an optional bundled `msys64/` runtime tree for bash/node/npm/uv tools

On first launch, the desktop shell copies the workspace template to:

```text
%APPDATA%/../Local/<AppName>/User Data/workspace
```

and then starts Gateway from there. This keeps `workspace/histories/*.jsonl` writable.

## Local setup

Install Electron-side dependencies:

```powershell
cd desktop
npm.cmd install
```

## One-step installer build

If you want a single command that prepares resources and emits the Windows installer, run from the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build-electron-installer.ps1 -UseMirror
```

This wrapper:

1. installs Electron dependencies in `desktop/`
2. runs `prepare-electron-resources.ps1`
3. invokes Electron Builder for the NSIS installer

If your workspace needs bundled `bash` / `node` / `npm` / `uv`, include a local MSYS2 tree:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build-electron-installer.ps1 `
  -UseMirror `
  -BundleMsys2 `
  -Msys2Root C:\msys64
```

## Development run

Run the Electron shell against the current source checkout:

```powershell
cd desktop
npm.cmd start
```

In development, Electron spawns:

```text
uv run --project <repo-root> psi-agent gateway --listen http://127.0.0.1:<port> --no-browser
```

It still runs Gateway from Electron's own runtime workspace under the app user-data directory, so the development shell and packaged app use the same writable `histories/` location.

## Prepare packaged resources

From the repo root or from `desktop/`, generate the backend/resources Electron will embed:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/prepare-electron-resources.ps1
```

To include a local MSYS2 runtime:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/prepare-electron-resources.ps1 `
  -BundleMsys2 `
  -Msys2Root C:\msys64
```

This stages resources under:

```text
desktop/.build/resources/
```

## Build a Windows desktop app

From `desktop/`:

```powershell
npm.cmd run dist:win
```

This manual path is fine when you do not need to customize the resource preparation step. If you do need `-BundleMsys2`, prefer `build-electron-installer.ps1`, because `npm.cmd run dist:win` always reruns `prepare-electron-resources.ps1` with its default arguments.

## GitHub Actions CI

The repo now includes a Windows GitHub Actions workflow at:

```text
.github/workflows/electron-desktop.yml
```

It runs on:

- manual trigger (`workflow_dispatch`)
- pushes to `main`
- tags matching `v*`

The workflow:

1. sets up `uv` + Python 3.14
2. sets up Node.js
3. installs an MSYS2 runtime on the runner
4. calls `packaging/windows/build-electron-installer.ps1`
5. uploads the generated installer from `desktop/dist/`

The uploaded artifact name is:

```text
psi-agent-electron-installer
```

If you want a smaller installer and your target machines already provide `bash` / `node` / `uv`, remove `-BundleMsys2` from the workflow and drop the `msys2/setup-msys2` step.

For an unpacked directory build:

```powershell
npm.cmd run pack:win
```

The packaged app is emitted under:

```text
desktop/dist/
```

## Notes

- The packaged app loads the current frontend in Electron without changing SPA API code.
- `--no-browser` is always passed, because Electron owns the main window.
- If you do not bundle MSYS2, shell/node-based tools fall back to whatever exists on the target machine.
- The Electron config sets `win.signAndEditExecutable = false` by default. This avoids a common local Windows packaging failure where Electron Builder cannot unpack `winCodeSign` because the current user lacks symbolic-link privileges.
- In Windows PowerShell, prefer `npm.cmd` over `npm` to avoid `npm.ps1` execution-policy failures.
- If Electron downloads time out on your network, set `ELECTRON_MIRROR=https://npmmirror.com/mirrors/electron/` and `ELECTRON_BUILDER_BINARIES_MIRROR=https://npmmirror.com/mirrors/electron-builder-binaries/` before running `npm.cmd install` or `npm.cmd run dist:win`.
