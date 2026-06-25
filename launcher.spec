# launcher.spec
# -*- mode: python ; coding: utf-8 -*-
"""
dd-rec 启动器 PyInstaller 打包配置（onefile 模式）

产物：dist/dd_rec.exe（单文件，~200KB）
build.py 复制到 dd-rec/dd_rec.exe（portable 根目录，双击启动）
"""

import os

block_cipher = None

a = Analysis(
    ['launcher.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'PySide6', 'PySide6.*',
        'PyQt4', 'PyQt5', 'PyQt6',
        'tkinter', 'Tkinter',
        'matplotlib', 'matplotlib.*',
        'numpy', 'numpy.*',
        'pandas', 'pandas.*',
        'scipy', 'scipy.*',
        'sklearn', 'sklearn.*',
        'PIL', 'PIL.*', 'Pillow', 'Pillow.*',
        'requests', 'requests.*',
        'httpx', 'httpx.*',
        'curl_cffi', 'curl_cffi.*',
        'liquid', 'liquid.*',
        'plugins', 'plugins.*',
        'discord', 'discord.*',
        'grpc', 'grpc.*',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# onefile 模式（启动器：体积小，启动频率低，可接受解压延迟）
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='dd_rec',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,  # 不弹黑窗
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
