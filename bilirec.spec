# -*- mode: python ; coding: utf-8 -*-
"""
DD录播机 PyInstaller 打包配置
用法: pyinstaller bilirec.spec
"""

import os
import sys

block_cipher = None

# ffmpeg 源路径
FFMPEG_SOURCE = r"C:\ffmpeg\bin"

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[
        # 把 ffmpeg 作为数据文件打包
        (os.path.join(FFMPEG_SOURCE, 'ffmpeg.exe'), 'ffmpeg'),
        (os.path.join(FFMPEG_SOURCE, 'ffprobe.exe'), 'ffmpeg'),
    ],
    hiddenimports=[
        'PySide6',
        'PySide6.QtCore',
        'PySide6.QtGui',
        'PySide6.QtWidgets',
        'PySide6.QtMultimedia',
        'PySide6.QtNetwork',
        'PySide6.scripts',
        'shiboken6',
        'curl_cffi',
        'liquid',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib',
        'pandas',
        'scipy',
        'sklearn',
        'PIL',
        'tkinter',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='DD录播机',
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
)
