#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打包脚本 - 将 DD录播机 打包成 exe（包含 ffmpeg）

使用方法:
    python build.py
"""

import os
import sys
import subprocess


def check_dependencies():
    """检查依赖是否满足"""
    errors = []

    # 检查 PyInstaller
    try:
        subprocess.run(['pyinstaller', '--version'],
                      capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        errors.append("PyInstaller 未安装，请运行: pip install pyinstaller")

    # 检查 ffmpeg 源文件
    if not os.path.exists(r"C:\ffmpeg\bin\ffmpeg.exe"):
        errors.append("ffmpeg 未找到，请确认 C:\\ffmpeg\\bin\\ffmpeg.exe 存在")

    if errors:
        print("❌ 依赖检查失败:")
        for err in errors:
            print(f"   - {err}")
        return False

    print("✅ 依赖检查通过")
    return True


def build():
    """执行打包"""
    print("=" * 50)
    print("开始打包 DD录播机 (包含 ffmpeg)")
    print("=" * 50)

    # 清理之前的构建
    dist_dir = 'dist'
    build_dir = 'build'

    if os.path.exists(dist_dir):
        print(f"清理旧构建目录: {dist_dir}")
        import shutil
        shutil.rmtree(dist_dir)

    if os.path.exists(build_dir):
        print(f"清理旧构建目录: {build_dir}")
        import shutil
        shutil.rmtree(build_dir)

    # 执行 PyInstaller
    print("\n执行 PyInstaller...")
    result = subprocess.run(
        ['pyinstaller', 'bilirec.spec', '--noconfirm'],
        capture_output=False
    )

    if result.returncode != 0:
        print("❌ 打包失败!")
        return False

    print("\n" + "=" * 50)
    print("✅ 打包完成!")
    print(f"   输出目录: {os.path.abspath(dist_dir)}")
    print("   已包含 ffmpeg.exe，用户无需额外安装")
    print("=" * 50)

    return True


if __name__ == '__main__':
    if not check_dependencies():
        sys.exit(1)

    if not build():
        sys.exit(1)