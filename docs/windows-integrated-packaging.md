# Windows Integrated Packaging

This repo already ships the pieces needed for an all-in-one Windows bundle:

- the Python backend is frozen into `psi-agent.exe`
- the Web Console frontend is prebuilt into `src/psi_agent/gateway/spa/dist`
- the packaged app runs a workspace locally and opens the browser-based Web Console

The local entry point added for this flow is:

```powershell
packaging/windows/build-haitun-integrated.ps1
```

## What it builds

The script targets `examples/haitun-workspace` and produces:

- a frozen backend executable via PyInstaller
- a staged portable directory under `build/windows/haitun-stage/`
- a portable zip under `build/windows/Haitun-Agent-portable.zip`
- an optional Inno Setup installer under `build/windows/installer/Haitun Agent Setup.exe`

The frontend and backend are packaged together. The target machine does not need Python or Node.js just to start the app.

## Runtime model

This is an integrated bundle, not an Electron shell:

- `psi-agent.exe` serves the frontend from the bundled `spa/dist`
- the launcher starts `psi-agent gateway --tray haitun.ico --verbose`
- the UI opens in the system browser on `127.0.0.1`

If you later want an Electron shell, this portable bundle is the right backend payload to embed.

## Prerequisites

Required for local packaging:

- Windows
- `uv`
- `npm`
- Inno Setup, unless you use `-NoInstaller`

Optional for a fuller Haitun bundle:

- a local MSYS2 tree containing:
  - `usr/bin/bash.exe`
  - `usr/bin/git.exe`
  - `ucrt64/bin/node.exe`
  - `ucrt64/bin/npm.cmd`
  - `ucrt64/bin/uv.exe`

Bundling MSYS2 is recommended for `examples/haitun-workspace`, because that workspace exposes `bash` and Fusion Flow tooling.

## Basic usage

From the repo root:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build-haitun-integrated.ps1
```

This will:

1. run `npm ci` and `npm run build` in `src/psi_agent/gateway/spa`
2. run `uv sync`
3. install PyInstaller build dependencies if needed
4. freeze `src/psi_agent/cli.py` into `psi-agent.exe`
5. stage `examples/haitun-workspace` plus the launcher and icon
6. build a portable zip and an Inno Setup installer

## Useful switches

Skip frontend install when `node_modules` is already present:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build-haitun-integrated.ps1 -SkipSpaInstall
```

Build only the portable bundle and skip Inno Setup:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build-haitun-integrated.ps1 -NoInstaller
```

Bundle a local MSYS2 installation into the app:

```powershell
powershell -ExecutionPolicy Bypass -File packaging/windows/build-haitun-integrated.ps1 `
  -BundleMsys2 `
  -Msys2Root C:\msys64
```

## Notes

- The script removes any `histories/*.jsonl` from the staged bundle so local chat logs are not shipped.
- The installer defaults to `{localappdata}\Programs\Haitun Agent`, which keeps the workspace writable for `histories/`.
- If you skip `-BundleMsys2`, the packaged app still starts, but tools that rely on `bash`, `node`, `npm`, or `uv` will fall back to whatever is installed on the target machine.
