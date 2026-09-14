# -*- mode: python ; coding: utf-8 -*-
# Build:  py -m PyInstaller --noconfirm --clean chat2ftp.spec
# Output: dist\Chat2FTP.exe   (single file, no console window)
#
# UPX is deliberately OFF. UPX-packed single-file exes are a common cause of
# "nothing happens when I double click it" - antivirus quietly kills them.

import os

icon = "chat2ftp.ico" if os.path.isfile("chat2ftp.ico") else None

a = Analysis(
    ["chat2ftp.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=["paramiko"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["numpy", "pandas", "matplotlib", "PIL", "pytest", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="Chat2FTP",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
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
