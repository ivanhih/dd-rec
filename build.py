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

目录结构:
  dd-rec/
  ├── dd_rec.exe              # 启动器
  ├── dd_rec_main.exe         # 主程序(平坦化,跟 launcher 同目录)
  ├── dd_rec.update.exe       # kachina 自更新器
  ├── version.ini              # 当前版本
  ├── _internal/              # PyInstaller 运行时
  ├── ffmpeg/                 # ffmpeg 二进制(保留)
  │   ├── ffmpeg.exe
  │   └── ffprobe.exe
  ├── userdata/               # 用户数据(kachina update.exe 自动保护)
  │   ├── config.json
  │   ├── data.json
  │   ├── dd_rec.db
  │   ├── log/
  │   └── cache/
  └── 录播文件/              # 用户视频(kachina ignoreFolderPath 保护)
      └── <room_id>-<uname>/<date>/<file>.mp4

使用：
  python build.py
"""

import os
import sys
import shutil
import subprocess
import zipfile
import time
from typing import Optional

from version import __version__ as VERSION

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")
INSTALLER_DIR = os.path.join(PROJECT_ROOT, "installer")
LAUNCHER_SPEC = os.path.join(PROJECT_ROOT, "launcher.spec")
APP_SPEC = os.path.join(PROJECT_ROOT, "app.spec")
FFMPEG_BIN = r"C:\ffmpeg\bin"
FFMPEG_EXES = ("ffmpeg.exe", "ffprobe.exe")

# ==================== kachina-installer 配置 ====================
# kachina-builder 路径(自动探测以下位置,优先级从高到低)
_KACHINA_CANDIDATES = [
    r"C:\tools\kachina-builder\kachina-builder.exe",
    r"C:\Users\user\Downloads\ABDM\Programs\kachina-builder.exe",
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "kachina-builder", "kachina-builder.exe"),
    os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "kachina-builder", "kachina-builder.exe"),
]
KACHINA_CONFIG = os.path.join(PROJECT_ROOT, "kachina.config.json")
APP_ICON = os.path.join(PROJECT_ROOT, "assets", "icon.ico")
KACHINA_RID = "dd_rec"  # AppId,跟 kachina.config.json regName 一致


def find_kachina_builder() -> str | None:
    """找 kachina-builder.exe,多个候选位置 + PATH

    注意:shutil.which("kachina-builder") 默认会优先搜当前工作目录,
    如果项目根里有 kachina-builder.exe 会优先被找到 — 这是个坑。
    所以这里只信绝对路径候选列表,不用 PATH(避免项目根污染)。
    """
    for c in _KACHINA_CANDIDATES:
        if os.path.exists(c):
            return c
    # 兜底:真的 PATH 里有时才用它(但要排除项目根)
    import shutil
    p = shutil.which("kachina-builder")
    if p and os.path.normpath(os.path.dirname(p)) != os.path.normpath(PROJECT_ROOT):
        return p
    return None


def find_7z() -> str | None:
    """找 7z.exe(打包 7z 格式用)"""
    import shutil
    p = shutil.which("7z")
    if p:
        return p
    candidates = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return None


def _run_kachina(cmd: list, wait_for: str = None, timeout: int = 120) -> None:
    """调 kachina-builder。

    已知背景:
      - Python subprocess 直接 spawn kachina-builder 报 WinError 740
      - cmd /c 中间层可以绕过,但要小心引号处理
      - 这次用最直接的:shell=True,让 Windows 自己处理引号

    Args:
        cmd: 完整命令行(元素 0 是 exe 路径,后面是参数)
        wait_for: 等待此文件路径出现(kachina 跑完的标志)
        timeout: 等待超时(秒)
    """
    import subprocess
    # 用空格连接的字符串作为 shell 命令
    cmdline = subprocess.list2cmdline(cmd)
    print(f"  $ {cmdline}")

    CREATE_NO_WINDOW = 0x08000000
    # shell=True 让 Windows cmd.exe 自己解析 + 执行(处理引号等)
    r = subprocess.run(
        cmdline,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        shell=True,
        creationflags=CREATE_NO_WINDOW,
    )
    stdout = (r.stdout or "").strip()
    stderr = (r.stderr or "").strip()
    if stdout:
        print(f"  stdout: {stdout[:500]}")
    if stderr:
        print(f"  stderr: {stderr[:500]}")
    if r.returncode != 0:
        raise RuntimeError(
            f"kachina-builder 退出码 {r.returncode}\n"
            f"stdout: {stdout[:500]}\n"
            f"stderr: {stderr[:500]}"
        )
    if wait_for and not os.path.exists(wait_for):
        raise RuntimeError(
            f"kachina-builder 报告成功但产物未出现: {wait_for}"
        )


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

    kachina = find_kachina_builder()
    if not kachina:
        errors.append(
            "kachina-builder 未找到（Phase 0 步骤 0.1-0.2 没完成）。\n"
            "   下载地址: https://github.com/YuehaiTeam/kachina-installer/releases\n"
            "   解压到 C:\\tools\\kachina-builder\\ 后重试。"
        )
    else:
        print(f"   找到 kachina-builder: {kachina}")

    if not find_7z():
        # 不是致命错误 — 没有 7z 时 fallback zip,build.py 后续会处理
        print("   警告: 没找到 7z.exe，会回退到 zip 格式（产物稍大）")

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

    # 2. 平铺主程序到 portable 根(不再有 dd_rec-{VERSION}/ 子目录)
    #    原因: kachina update.exe 启动时检查 current_exe().parent() / exe_name,
    #    找不到就走"装到 Program Files"模式。所以主程序必须跟 update.exe 平铺,
    #    这样 update.exe 看到 dd_rec_main.exe 就走"就地升级"模式(参考 BetterGI)。
    app_src = os.path.join(DIST_DIR, "dd_rec_app")  # PyInstaller onedir 输出
    if os.path.exists(app_src):
        # 把 onedir 里所有条目(文件 + 子目录,主要是 _internal/)复制/合并到 portable 根
        for entry in os.listdir(app_src):
            src_path = os.path.join(app_src, entry)
            dst_path = os.path.join(portable_root, entry)
            try:
                if os.path.isdir(src_path):
                    if os.path.exists(dst_path):
                        shutil.rmtree(dst_path)
                    shutil.copytree(src_path, dst_path)
                else:
                    if os.path.exists(dst_path):
                        os.remove(dst_path)
                    shutil.copy2(src_path, dst_path)
            except Exception as e:
                print(f"   警告: 平铺 {entry} 失败: {e}")
        shutil.rmtree(app_src)
        print(f"   平铺主程序到 portable 根({len(os.listdir(portable_root))} 项)")
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

    # 4.5 拷贝 kachina update.exe（assemble 阶段之前先 build 了 update.exe）
    update_src = os.path.join(DIST_DIR, "dd_rec.update.exe")
    if os.path.exists(update_src):
        update_dst = os.path.join(portable_root, "dd_rec.update.exe")
        if os.path.exists(update_dst):
            os.remove(update_dst)
        shutil.copy2(update_src, update_dst)
        print(f"   复制 kachina update.exe")
    else:
        print(f"   警告: 找不到 update.exe {update_src}，kachina 自更新不可用")

    # 5. 创建 temp 目录
    temp_dir = os.path.join(portable_root, "temp")
    os.makedirs(temp_dir, exist_ok=True)
    print("   创建 temp/ 目录")


def create_7z() -> str:
    """打包成 7z（优先）或 zip（fallback）

    文件名: dd_rec-{VERSION}-portable.7z
    内部结构跟原 zip 一致：
      dd_rec.exe / version.ini / dd_rec.update.exe / dd_rec-{VERSION}/ / ffmpeg/ / temp/
    """
    print(f"\n[5/9] 打包成 7z/zip...")
    portable_root = os.path.join(DIST_DIR, "dd-rec")
    archive_name = f"dd_rec-{VERSION}-portable"
    archive_path = os.path.join(DIST_DIR, archive_name)

    seven_z = find_7z()
    if seven_z:
        # 7z 模式：体积小 ~25%
        archive_path_7z = archive_path + ".7z"
        if os.path.exists(archive_path_7z):
            os.remove(archive_path_7z)
        subprocess.check_call([
            seven_z, "a",
            "-t7z", "-mx=9", "-mfb=64", "-md=32m",
            archive_path_7z,
            os.path.join(portable_root, "*"),
        ], cwd=portable_root)
        size_mb = os.path.getsize(archive_path_7z) / 1024 / 1024
        print(f"   创建: {os.path.basename(archive_path_7z)} ({size_mb:.1f} MB, 7z)")
        return archive_path_7z
    else:
        # zip fallback
        archive_path_zip = archive_path + ".zip"
        if os.path.exists(archive_path_zip):
            os.remove(archive_path_zip)
        with zipfile.ZipFile(archive_path_zip, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(portable_root):
                for file in files:
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, portable_root)
                    zf.write(file_path, arcname)
        size_mb = os.path.getsize(archive_path_zip) / 1024 / 1024
        print(f"   创建: {os.path.basename(archive_path_zip)} ({size_mb:.1f} MB, zip fallback)")
        return archive_path_zip


def build_kachina_update() -> str:
    """生成 dd_rec.update.exe — kachina 的便携更新器

    必须在 assemble_portable_dir 之前调用，因为后者要把 update.exe 拷到 portable 根。
    """
    kachina = find_kachina_builder()
    if not kachina:
        raise RuntimeError("kachina-builder 不可用，请先完成 Phase 0")

    print(f"\n[2.5/9] 生成 kachina update.exe...")
    update_exe = os.path.join(DIST_DIR, "dd_rec.update.exe")
    if os.path.exists(update_exe):
        os.remove(update_exe)
    cmd = [kachina, "pack", "-c", KACHINA_CONFIG, "-o", update_exe]
    if os.path.exists(APP_ICON):
        cmd += ["--icon", APP_ICON]
    print(f"  $ " + " ".join(cmd))
    _run_kachina(cmd, wait_for=update_exe)
    print(f"   创建: dd_rec.update.exe")
    return update_exe


def build_kachina_metadata(portable_root: str) -> tuple:
    """生成 metadata.json + hashed/ 目录

    kachina 增量更新 (HDiffPatch) 需要的元数据。
    - portable_root: assemble 完之后的 portable 根目录(dist/dd-rec/),
      必须包含 launcher (dd_rec.exe) + version.ini + 主程序 + _internal + ffmpeg,
      这样 patch 时这些文件都会被替换(否则 launcher 和 version.ini 永远不更新)
    """
    kachina = find_kachina_builder()
    print(f"\n[6/9] 生成 kachina metadata + hashed...")
    metadata = os.path.join(DIST_DIR, "metadata.json")
    hashed = os.path.join(DIST_DIR, "hashed")
    if os.path.exists(hashed):
        shutil.rmtree(hashed)
    # -u:updater 文件路径(绝对路径,避免 kachina 找不到)
    update_exe_abs = os.path.join(DIST_DIR, "dd_rec.update.exe")
    cmd = [
        kachina, "gen",
        "-j", "8",
        "-i", portable_root,
        "-m", metadata,
        "-o", hashed,
        "-r", KACHINA_RID,
        "-t", VERSION,
        "-u", update_exe_abs,
    ]
    print("  $ " + " ".join(cmd))
    # gen 跑全量 hash + 压缩,几百到几千个文件
    # PyInstaller onedir 通常 1500-3000 个文件,180MB 左右 → 15 分钟保守
    _run_kachina(cmd, wait_for=metadata, timeout=900)
    return metadata, hashed


def build_kachina_installer(metadata: str, hashed: str) -> str:
    """生成完整的 Install.exe — 给想要安装器的用户"""
    kachina = find_kachina_builder()
    print(f"\n[7/9] 生成 kachina Install.exe...")
    install_exe = os.path.join(DIST_DIR, f"DD录播机.Install.{VERSION}.exe")
    if os.path.exists(install_exe):
        os.remove(install_exe)
    cmd = [
        kachina, "pack",
        "-c", KACHINA_CONFIG,
        "-m", metadata,
        "-d", hashed,
        "-o", install_exe,
    ]
    if os.path.exists(APP_ICON):
        cmd += ["--icon", APP_ICON]
    print("  $ " + " ".join(cmd))
    # pack Install.exe 也是写大文件,5 分钟够
    _run_kachina(cmd, wait_for=install_exe, timeout=300)
    size_mb = os.path.getsize(install_exe) / 1024 / 1024
    print(f"   创建: {os.path.basename(install_exe)} ({size_mb:.1f} MB)")
    return install_exe


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


def print_summary(archive_path: str, install_exe: str = "", patch_path: str = "") -> None:
    """打印最终产物"""
    print("\n" + "=" * 60)
    print("打包完成!")
    print("=" * 60)

    def _print_art(p: str, label: str):
        if p and os.path.exists(p):
            size_mb = os.path.getsize(p) / 1024 / 1024
            print(f"   - {os.path.basename(p):50s} {size_mb:7.1f} MB  ({label})")

    print("\n发布产物:")
    _print_art(archive_path, "绿色版(kachina 自更新)")
    _print_art(install_exe, "完整安装器(可选)")
    _print_art(patch_path, "增量补丁(可选)")
    _print_art(os.path.join(DIST_DIR, "dd_rec.update.exe"), "kachina 更新器(已嵌入 7z)")

    print(f"\n下一步:")
    print(f"   1. 在 GitHub 创建 Release v{VERSION}")
    print(f"   2. 上传以下文件(全部):")
    if archive_path:
        print(f"      - {os.path.basename(archive_path)}")
    if install_exe:
        print(f"      - {os.path.basename(install_exe)}")
    if patch_path:
        print(f"      - {os.path.basename(patch_path)}")
    print(f"   3. 7z/Install.exe 用户:走 kachina update.exe 自动增量更新")
    print("=" * 60)


def _is_admin() -> bool:
    """检查当前进程是否以管理员身份运行"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def _request_admin_relaunch() -> None:
    """请求 UAC 提权,失败则退出

    用 runas 启动新进程:
      - 整个 python 进程提权,后续 subprocess.check_call kachina-builder
        不会再报 WinError 740
      - 一次性提权,build.py 后续所有 kachina 调用都跑得通
    """
    import subprocess
    import sys
    script = os.path.abspath(__file__)
    args = " ".join(f'"{a}"' for a in sys.argv[1:])
    cmd = f'"{sys.executable}" "{script}" {args}'
    print("=" * 60)
    print("build.py 需要管理员权限(因为 kachina-builder 要求 UAC)")
    print("即将弹 UAC 框,点'是'后 build.py 会以管理员身份重新启动")
    print("=" * 60)
    # runas 启动新进程;父进程直接退出,等提权后的子进程接管
    subprocess.call(["runas", "/user:Administrator", cmd])
    sys.exit(0)


def main() -> int:
    if not check_dependencies():
        return 1

    clean()
    build_launcher()
    build_app()
    # update.exe 必须在 metadata 之前生成(metadata 的 -u 参数要它)
    update_exe = build_kachina_update()
    # assemble 必须先于 metadata:metadata 现在用 assemble 完之后的 portable 根作为输入,
    # 这样 launcher (dd_rec.exe) 和 version.ini 也进 hashed 列表,patch 时会被替换
    # —— 否则 kachina update.exe 跑完后 version.ini 还是旧版号,下次启动又弹更新
    assemble_portable_dir()
    # metadata 输入改成 portable 根(assemble 后),包含 launcher / version.ini / 主程序 / _internal / ffmpeg / update.exe
    portable_root = os.path.join(DIST_DIR, "dd-rec")
    metadata, hashed = build_kachina_metadata(portable_root)
    install_exe = build_kachina_installer(metadata, hashed)
    archive_path = create_7z()
    patch_path = build_hdiff_patch_auto(metadata)  # 可能 None
    archive_current_patches(metadata, hashed)
    print_summary(archive_path, install_exe, patch_path)
    return 0


def archive_current_patches(metadata: str, hashed: str) -> None:
    """把当前版本的 metadata + hashed/ 归档到 patches/{version}/

    下一次发版时,build_hdiff_patch_auto 会从这里读 prev_version 的 hashed,
    生成 patch。

    归档策略:
      - 只存哈希索引(几千 KB,极小),不存 hashed/ 里的文件实体
      - metadata.json 完整保留(几 KB)

    提示:发布到 GitHub Release 之前,记得把 patches/{version}/ 提交到 git。
    """
    target_dir = os.path.join(PROJECT_ROOT, "patches", VERSION)
    print(f"\n[9/9] 归档 patches/{VERSION}/ (给下个版本做 patch 源)...")
    try:
        # 清理旧归档(可能有遗漏的临时文件)
        if os.path.exists(target_dir):
            shutil.rmtree(target_dir)
        os.makedirs(target_dir, exist_ok=True)

        # 拷贝 metadata.json
        if os.path.exists(metadata):
            shutil.copy2(metadata, os.path.join(target_dir, "metadata.json"))
            print(f"   写入 metadata.json")

        # 拷贝 hashed/(只是哈希索引,小)
        target_hashed = os.path.join(target_dir, "hashed")
        if os.path.isdir(hashed):
            shutil.copytree(hashed, target_hashed)
            count = sum(len(files) for _, _, files in os.walk(target_hashed))
            print(f"   写入 hashed/ ({count} 个索引文件)")

        # 写一个 .gitkeep + 提示
        readme = os.path.join(target_dir, "README.txt")
        with open(readme, "w", encoding="utf-8") as f:
            f.write(
                f"DD录播机 v{VERSION} 的 kachina 更新元数据\n"
                f"由 build.py 自动生成\n"
                f"提交到 git,下个版本 build 时会用来生成增量 patch\n"
            )
        print(f"   归档完成: {target_dir}")
        print(f"   提示: 记得 git add patches/{VERSION}/ && git commit")
    except Exception as e:
        print(f"   警告: 归档失败 {e}（不影响本次 build 产物）")


def build_hdiff_patch_auto(new_metadata: str) -> Optional[str]:
    """自动检测前一版 git tag,生成 HDiffPatch(可选)

    需要 patches/{prev_version}/metadata.json + hashed/ 在 git 里
    """
    kachina = find_kachina_builder()
    try:
        prev_tag = subprocess.check_output(
            ["git", "describe", "--tags", "--abbrev=0", "HEAD"],
            cwd=PROJECT_ROOT, text=True, stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        print("   跳过 HDiffPatch: 没有可用的 git tag")
        return None

    prev_version = prev_tag.lstrip("v")
    prev_hashed = os.path.join(PROJECT_ROOT, "patches", prev_version, "hashed")
    prev_meta = os.path.join(PROJECT_ROOT, "patches", prev_version, "metadata.json")
    if not (os.path.isdir(prev_hashed) and os.path.exists(prev_meta)):
        print(f"   跳过 HDiffPatch: 找不到 patches/{prev_version}/")
        return None

    print(f"\n[8/9] 生成 HDiffPatch ({prev_version} → {VERSION})...")
    patch = os.path.join(DIST_DIR, f"DD录播机-{VERSION}-patch-from-{prev_version}.zip")
    cmd = [
        kachina, "diff",
        "-c", KACHINA_CONFIG,
        "-i", prev_hashed,
        "-o", patch,
        "--old-meta", prev_meta,
        "--new-meta", new_metadata,
    ]
    print("  $ " + " ".join(cmd))
    try:
        _run_kachina(cmd, wait_for=patch)
    except Exception as e:
        print(f"   警告: HDiffPatch 生成失败 {e}，跳过 patch 产物")
        return None
    return patch


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
