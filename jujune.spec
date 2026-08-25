# -*- mode: python ; coding: utf-8 -*-
import os

binaries = []
if os.path.exists(os.path.join('vendor', 'PresentMon.exe')):
    binaries.append((os.path.join('vendor', 'PresentMon.exe'), '.'))

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=[('assets', 'assets')],
    hiddenimports=[
        'jujune.update',
        'pystray._win32',
        'pystray._darwin',
        'pystray._gtk',
        'PIL._tkinter_finder',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon = 'assets/jujune.ico' if os.path.exists('assets/jujune.ico') else 'assets/jujune_icon.png'

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SaiMonitor',
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
    icon=icon,
)
