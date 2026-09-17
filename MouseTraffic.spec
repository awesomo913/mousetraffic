# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['pystray._win32', 'websocket._abnf']
hiddenimports += collect_submodules('pystray')


a = Analysis(
    ['C:\\Users\\computer\\Desktop\\AI\\mousetraffic\\__main__.py'],
    pathex=[],
    binaries=[],
    datas=[('C:\\Users\\computer\\Desktop\\AI\\diagnostics_logger.py', '.')],
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
    name='MouseTraffic',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=r'C:\Users\computer\Desktop\AI\mousetraffic\assets\mousetraffic.ico',
)
