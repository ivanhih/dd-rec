# app.spec
# -*- mode: python ; coding: utf-8 -*-
"""
dd-rec 主程序 PyInstaller 打包配置（Portable 方案）

产物：dist\dd_rec-{VERSION}\dd_rec_main.exe + *.dll
输出到 dd_rec-{VERSION}/ 目录，配合 launcher 使用

注意：ffmpeg 不打包进 app/，因为根目录已有 ffmpeg/
"""

import os

block_cipher = None

_binaries = []
_datas = []  # 不再包含 ffmpeg（根目录已有）

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=_binaries,
    datas=_datas,
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
        'plugins',
        'plugins.manager',
        'plugins.store',
        'plugins.page',
        'core.portable_updater',
        'core.config',
        'core.recorder',
        'version',
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

# onedir 模式
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='dd_rec_main',
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

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name='dd_rec_app',  # 临时目录名，build.py 会重命名
)
