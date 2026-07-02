# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = []
hiddenimports += collect_submodules('any_llm')
hiddenimports += collect_submodules('mcp')
hiddenimports += collect_submodules('pystray')
hiddenimports += collect_submodules('serper_mcp_server')


a = Analysis(
    ['src\\psi_agent\\cli.py'],
    pathex=[],
    binaries=[],
    datas=[('src\\psi_agent\\gateway\\spa\\dist', 'psi_agent\\gateway\\spa\\dist')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='psi-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
