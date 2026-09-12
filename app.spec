# app.spec
# -*- mode: python ; coding: utf-8 -*-
"""
dd-rec 主程序 PyInstaller 打包配置（Portable 方案）

产物：dist/dd_rec_app/dd_rec.exe + _internal/
build.py 会平铺到 dist/dd-rec/，dd_rec.exe 是唯一程序入口

注意：ffmpeg 不打包进 app/，因为根目录已有 ffmpeg/
"""

import os

block_cipher = None

_binaries = []
_datas = [
    ('assets/icon.ico', 'assets'),  # 运行时窗口/托盘图标
    ('docs/wiki', 'docs/wiki'),     # 应用内本地帮助
]

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
        'core.portable_updater',
        'core.config',
        'core.recorder',
        'core.media_pipeline',
        'core.stream_source',
        'core.ffmpeg_tools',
        'core.flv',
        'core.flv.parser',
        'core.flv.session',
        'core.flv.writer',
        'core.flv.timestamps',
        'core.flv.types',
        'version',
        'markdown',
        'markdown.extensions.fenced_code',
        'markdown.extensions.tables',
        'markdown.extensions.nl2br',
        'markdown.extensions.sane_lists',
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
    name='dd_rec',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    icon='assets/icon.ico',
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
