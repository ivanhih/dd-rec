#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
dd-rec 打包脚本（Portable 方案）

流程：
  1. 检查依赖（pyinstaller、ffmpeg）
  2. 清理 dist/build
  3. PyInstaller 打包 launcher → dist/dd_rec/dd_rec.exe
  4. PyInstaller 打包主程序 → dist/dd_rec-{VERSION}/dd_rec_main.exe
  5. 复制 ffmpeg 到根目录
  6. 创建 version.ini
  7. 打包成 zip（dd_rec-{VERSION}.zip）

产物：
  dist/dd_rec-{VERSION}.zip  ← 发布到 GitHub Release

目录结构：
  dd-rec/
  ├── dd_rec.exe              # 启动器
  ├── version.ini              # 当前版本
  ├── config.json              # 用户配置（保留）
  ├── data.json                # 用户数据（保留）
  ├── dd_rec.db              # SQLite 数据（保留）
  ├── ffmpeg/                 # ffmpeg 二进制（保留）
  │   ├── ffmpeg.exe
  │   └── ffprobe.exe
  ├── dd_rec-1.0.2/         # 版本化应用包
  │   ├── dd_rec_main.exe    # 主程序
  │   └── _internal/          # Python 运行时 + dll
  └── temp/                   # 下载临时目录

使用：
  python build.py
"""

import os
import sys
import shutil
import subprocess
import zipfile
import time

from version import __version__ as VERSION

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")
INSTALLER_DIR = os.path.join(PROJECT_ROOT, "installer")
LAUNCHER_SPEC = os.path.join(PROJECT_ROOT, "launcher.spec")
APP_SPEC = os.path.join(PROJECT_ROOT, "app.spec")
FFMPEG_BIN = r"C:\ffmpeg\bin"
FFMPEG_EXES = ("ffmpeg.exe", "ffprobe.exe")


def find_pyinstaller() -> str | None:
    """找 PyInstaller 的 python"""
    import shutil as _shutil

    pyi = _shutil.which("pyinstaller")
    if pyi:
        scripts_dir = os.path.dirname(pyi)
        python_exe = os.path.join(os.path.dirname(scripts_dir), "python.exe")
        if os.path.exists(python_exe):
            return python_exe

    candidates = [
        r"C:\Users\user\miniconda3\python.exe",
        r"C:\miniconda3\python.exe",
        r"C:\ProgramData\miniconda3\python.exe",
        r"C:\ProgramData\Anaconda3\python.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c

    try:
        out = subprocess.run(
            ["py", "-3.13", "-c", "import PyInstaller; print('OK')"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and "OK" in out.stdout:
            return "py -3.13"
    except Exception:
        pass

    return None


def check_dependencies() -> bool:
    """检查打包依赖"""
    errors = []

    py_exe = find_pyinstaller()
    if not py_exe:
        errors.append(
            "PyInstaller 未安装或找不到带 PyInstaller 的 Python。\n"
            "   pip install pyinstaller 后重试。"
        )
    else:
        try:
            test_cmd = [py_exe, "-c", "import PyInstaller, curl_cffi, PySide6; print('OK')"] \
                if not isinstance(py_exe, str) or not py_exe.startswith("py ") \
                else py_exe.split() + ["-c", "import PyInstaller, curl_cffi, PySide6; print('OK')"]
            r = subprocess.run(test_cmd, capture_output=True, text=True, timeout=30)
            if r.returncode != 0 or "OK" not in r.stdout:
                errors.append(f"Python {py_exe} 缺关键包")
            else:
                global PYTHON_EXE
                PYTHON_EXE = py_exe
                print(f"   找到 Python: {py_exe}")
        except Exception as e:
            errors.append(f"Python 测试失败: {e}")

    for exe in FFMPEG_EXES:
        p = os.path.join(FFMPEG_BIN, exe)
        if not os.path.exists(p):
            errors.append(f"ffmpeg 缺失: {p}")

    if errors:
        print("依赖检查失败:")
        for e in errors:
            print(f"   - {e}")
        return False

    print("依赖检查通过")
    return True


PYTHON_EXE = sys.executable


def run(cmd: list, **kwargs) -> int:
    """subprocess.run 包装"""
    print("  $ " + " ".join(cmd))
    return subprocess.run(cmd, check=True, **kwargs).returncode


def clean() -> None:
    """清理旧构建产物（失败时跳过）"""
    print("\n[1/7] 清理旧构建...")
    for d in (DIST_DIR, BUILD_DIR):
        if os.path.exists(d):
            try:
                shutil.rmtree(d)
            except Exception as e:
                print(f"   警告: 无法清理 {d}: {e}")
                # 尝试只清理我们关心的目录
                if d == DIST_DIR:
                    for sub in os.listdir(d):
                        sub_path = os.path.join(d, sub)
                        try:
                            if os.path.isfile(sub_path):
                                os.remove(sub_path)
                            elif os.path.isdir(sub_path):
                                shutil.rmtree(sub_path)
                        except:
                            pass


def build_launcher() -> None:
    """PyInstaller 打包启动器（onefile 模式）"""
    print(f"\n[2/7] PyInstaller 打包启动器...")
    if isinstance(PYTHON_EXE, str) and " " in PYTHON_EXE and not PYTHON_EXE.startswith("py "):
        cmd = PYTHON_EXE.split() + ["-m", "PyInstaller", LAUNCHER_SPEC, "--noconfirm"]
    elif PYTHON_EXE.startswith("py "):
        cmd = PYTHON_EXE.split() + ["-m", "PyInstaller", LAUNCHER_SPEC, "--noconfirm"]
    else:
        cmd = [PYTHON_EXE, "-m", "PyInstaller", LAUNCHER_SPEC, "--noconfirm"]
    run(cmd, cwd=PROJECT_ROOT)


def build_app() -> None:
    """PyInstaller 打包主程序"""
    print(f"\n[3/7] PyInstaller 打包主程序...")
    if isinstance(PYTHON_EXE, str) and " " in PYTHON_EXE and not PYTHON_EXE.startswith("py "):
        cmd = PYTHON_EXE.split() + ["-m", "PyInstaller", APP_SPEC, "--noconfirm"]
    elif PYTHON_EXE.startswith("py "):
        cmd = PYTHON_EXE.split() + ["-m", "PyInstaller", APP_SPEC, "--noconfirm"]
    else:
        cmd = [PYTHON_EXE, "-m", "PyInstaller", APP_SPEC, "--noconfirm"]
    run(cmd, cwd=PROJECT_ROOT)


def assemble_portable_dir() -> None:
    """组装 Portable 目录结构"""
    print(f"\n[4/7] 组装 Portable 目录...")

    # 目标根目录
    portable_root = os.path.join(DIST_DIR, "dd-rec")
    os.makedirs(portable_root, exist_ok=True)

    # 0. 清理残留的旧版本目录（上次构建产物可能因文件锁没删干净）
    for entry in os.listdir(portable_root):
        full = os.path.join(portable_root, entry)
        if entry.startswith("dd_rec-") and os.path.isdir(full):
            try:
                shutil.rmtree(full)
                print(f"   清理残留旧目录: {entry}")
            except Exception as e:
                print(f"   警告: 无法清理残留目录 {entry}: {e}")
                # 尝试改名隐藏，至少不要让 create_zip 把它打进去
                try:
                    hidden = os.path.join(portable_root, f".old_{entry}")
                    if os.path.exists(hidden):
                        shutil.rmtree(hidden, ignore_errors=True)
                    shutil.move(full, hidden)
                    print(f"   已将 {entry} 重命名为 .old_{entry}（不参与打包）")
                except Exception:
                    pass

    # 1. 复制启动器（onefile 模式：单 exe）
    #    把 dist/dd_rec.exe 复制成 dd-rec/dd_rec.exe（直接在根目录）
    launcher_src = os.path.join(DIST_DIR, "dd_rec.exe")
    if os.path.exists(launcher_src):
        launcher_dst = os.path.join(portable_root, "dd_rec.exe")
        if os.path.exists(launcher_dst):
            os.remove(launcher_dst)
        shutil.copy2(launcher_src, launcher_dst)
        print(f"   复制启动器: dd_rec.exe")
    else:
        print(f"   警告: 找不到启动器 {launcher_src}")

    # 2. 复制主程序目录（PyInstaller 输出 dist/dd_rec_app/）
    # 注意：重命名为 dd_rec-{VERSION}/
    app_src = os.path.join(DIST_DIR, "dd_rec_app")  # PyInstaller 输出的目录
    app_dst = os.path.join(portable_root, f"dd_rec-{VERSION}")  # 目标目录名

    if os.path.exists(app_src):
        if os.path.exists(app_dst):
            shutil.rmtree(app_dst)
        # 直接重命名，而不是复制
        shutil.move(app_src, app_dst)
        print(f"   重命名主程序: dd_rec_app/ → dd_rec-{VERSION}/")
    else:
        print(f"   警告: 找不到主程序 {app_src}")

    # 3. 复制 ffmpeg 到根目录
    ffmpeg_dst = os.path.join(portable_root, "ffmpeg")
    os.makedirs(ffmpeg_dst, exist_ok=True)
    for exe in FFMPEG_EXES:
        src = os.path.join(FFMPEG_BIN, exe)
        dst = os.path.join(ffmpeg_dst, exe)
        if os.path.exists(src):
            shutil.copy2(src, dst)
            print(f"   复制 ffmpeg: {exe}")
        else:
            print(f"   警告: 源文件不存在 {src}")

    # 4. 创建 version.ini
    version_ini = os.path.join(portable_root, "version.ini")
    with open(version_ini, "w", encoding="utf-8") as f:
        f.write(f"version={VERSION}\n")
    print(f"   创建 version.ini: {VERSION}")

    # 5. 创建 temp 目录
    temp_dir = os.path.join(portable_root, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    print("   创建 temp/ 目录")


def create_zip() -> str:
    """打包成 zip"""
    print(f"\n[5/7] 打包成 zip...")
    portable_root = os.path.join(DIST_DIR, "dd-rec")
    zip_name = f"dd_rec-{VERSION}.zip"
    zip_path = os.path.join(DIST_DIR, zip_name)

    if os.path.exists(zip_path):
        os.remove(zip_path)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(portable_root):
            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, portable_root)
                zf.write(file_path, arcname)

    size_mb = os.path.getsize(zip_path) / 1024 / 1024
    print(f"   创建: {zip_name} ({size_mb:.1f} MB)")
    return zip_path


def copy_to_installer() -> None:
    """复制到 installer 目录"""
    print(f"\n[6/7] 复制到 installer 目录...")
    os.makedirs(INSTALLER_DIR, exist_ok=True)
    zip_name = f"dd_rec-{VERSION}.zip"
    src = os.path.join(DIST_DIR, zip_name)
    dst = os.path.join(INSTALLER_DIR, zip_name)
    if os.path.exists(src):
        shutil.copy2(src, dst)
        print(f"   复制: {dst}")


def print_summary(zip_path: str) -> None:
    """打印最终产物"""
    print("\n" + "=" * 60)
    print("打包完成!")
    print("=" * 60)
    if os.path.exists(zip_path):
        size_mb = os.path.getsize(zip_path) / 1024 / 1024
        print(f"\n发布产物:")
        print(f"   - {zip_path}  ({size_mb:.1f} MB)")
    print(f"\n目录结构预览:")
    print(f"   dd-rec/")
    print(f"   ├── dd_rec.exe          # 启动器")
    print(f"   ├── version.ini         # 当前版本")
    print(f"   ├── config.json         # 用户配置")
    print(f"   ├── data.json           # 用户数据")
    print(f"   ├── ffmpeg/")
    print(f"   │   ├── ffmpeg.exe")
    print(f"   │   └── ffprobe.exe")
    print(f"   ├── dd_rec-{VERSION}/")
    print(f"   │   ├── dd_rec_main.exe # 主程序")
    print(f"   │   └── _internal/      # Python 运行时")
    print(f"   └── temp/")
    print(f"\n下一步:")
    print(f"   1. 在 GitHub 创建 Release v{VERSION}")
    print(f"   2. 上传 dd_rec-{VERSION}.zip 到 Release")
    print(f"   3. 用户解压后双击 dd_rec.exe 即可运行")
    print("=" * 60)


def main() -> int:
    if not check_dependencies():
        return 1

    clean()
    build_launcher()
    build_app()
    assemble_portable_dir()
    zip_path = create_zip()
    copy_to_installer()
    print_summary(zip_path)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except subprocess.CalledProcessError as e:
        print(f"\n构建失败: 命令返回非零码 {e.returncode}")
        print(f"   命令: {e.cmd}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n用户中断")
        sys.exit(130)
