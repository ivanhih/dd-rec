#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
dd-rec 打包脚本（Portable 方案）

流程：
  1. 检查依赖（pyinstaller、ffmpeg）
  2. 清理 dist/build
  3. PyInstaller 打包主程序 → dist/dd_rec_app/dd_rec.exe
  4. 生成 dd_rec.update.exe
  5. 平铺主程序并复制 ffmpeg、创建 version.ini
  6. 生成 kachina metadata / hashed
  7. 打包成 7z（dd_rec-{VERSION}-portable.7z，失败时回退 zip）
  8. 生成 Install.exe（正式发布必需）
  9. 复制 portable 和 installer 到 installer/

产物：
  dist/dd_rec-{VERSION}-portable.7z      ← 发布到 GitHub Release
  dist/dd_rec.Install.{VERSION}.exe      ← 完整安装器
  installer/                            ← 发布产物副本，方便手动检查/上传

目录结构:
  dd-rec/
  ├── dd_rec.exe              # 唯一主程序入口
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

import ast
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
            resolved = subprocess.run(
                ["py", "-3.13", "-c", "import sys; print(sys.executable)"],
                capture_output=True, text=True, timeout=10,
            )
            python_exe = (resolved.stdout or "").strip().splitlines()[-1:]
            if python_exe and os.path.isfile(python_exe[0]):
                return python_exe[0]
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


def _pyinstaller_env() -> dict[str, str]:
    """返回用于 PyInstaller 的干净 DLL 搜索环境。

    PyInstaller 会根据 PATH 解析 Qt6Core.dll 的未带版本 ICU 依赖。
    Codex 的运行时会把 Poppler 的 native/bin 放到 PATH 前面，其中的
    ICU 78 导出名带 ``_78``，与 PySide6/Qt 需要的 Windows ICU ABI 不兼容。
    如果直接继承该 PATH，错误的 icuuc.dll 会被打进安装包，运行时就会在
    ``from PySide6 import QtWidgets`` 处报 WinError 127。

    只保留当前 Python/Conda 和 Windows 系统目录，避免其它 native
    运行时目录参与依赖解析。
    """
    env = os.environ.copy()

    python_exe = PYTHON_EXE
    if isinstance(python_exe, str) and python_exe.startswith("py "):
        try:
            resolved = subprocess.run(
                python_exe.split() + ["-c", "import sys; print(sys.executable)"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            resolved_path = (resolved.stdout or "").strip().splitlines()[-1:]
            if resolved.returncode == 0 and resolved_path:
                python_exe = resolved_path[0]
        except (OSError, subprocess.SubprocessError):
            pass
    if not isinstance(python_exe, str) or not os.path.isfile(python_exe):
        python_exe = sys.executable

    python_dir = os.path.dirname(os.path.abspath(python_exe))
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    preferred = [
        python_dir,
        os.path.join(python_dir, "Library", "bin"),
        os.path.join(python_dir, "DLLs"),
        os.path.join(python_dir, "Scripts"),
        os.path.join(system_root, "System32"),
        system_root,
        os.path.join(system_root, "System32", "Wbem"),
        os.path.join(system_root, "System32", "WindowsPowerShell", "v1.0"),
        os.path.join(system_root, "System32", "OpenSSH"),
    ]

    entries = []
    for entry in preferred:
        if not entry:
            continue
        normalized = os.path.normcase(os.path.normpath(entry)).replace("/", "\\")
        if normalized not in entries:
            entries.append(normalized)
    env["PATH"] = os.pathsep.join(entries)
    return env


def _assert_no_accidental_poppler_dlls() -> None:
    """阻止把外部 Poppler native DLL 误打进正式包。"""
    analysis_toc = os.path.join(BUILD_DIR, "app", "Analysis-00.toc")
    if not os.path.isfile(analysis_toc):
        raise RuntimeError(
            f"PyInstaller 分析结果不存在，拒绝继续打包: {analysis_toc}"
        )
    with open(analysis_toc, "r", encoding="utf-8", errors="replace") as f:
        try:
            analysis_toc_data = ast.literal_eval(f.read())
        except (SyntaxError, ValueError) as e:
            raise RuntimeError(
                f"无法解析 PyInstaller 分析结果，拒绝继续打包: {analysis_toc}"
            ) from e

    def _strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, (list, tuple, set)):
            for item in value:
                yield from _strings(item)
        elif isinstance(value, dict):
            for item in value.values():
                yield from _strings(item)

    sources = []
    for source in _strings(analysis_toc_data):
        normalized = source.lower().replace("/", "\\")
        if "\\poppler\\library\\bin\\" in normalized or "\\codex-runtimes\\" in normalized:
            sources.append(source)
    if sources:
        raise RuntimeError(
            "PyInstaller 解析到了外部 Poppler/Codex native DLL；"
            "已拒绝继续打包，避免生成 QtWidgets DLL 启动错误的安装器。"
            f" 首个来源: {sources[0]}"
        )


def _assert_qt_runtime_loadable(bundle_root: str) -> None:
    """直接加载打包后的 Qt DLL，阻止 QtWidgets 启动回归。"""
    if os.name != "nt":
        return

    internal = os.path.join(bundle_root, "_internal")
    qt_dir = os.path.join(internal, "PySide6")
    shiboken_dir = os.path.join(internal, "shiboken6")
    required = (
        os.path.join(qt_dir, "Qt6Core.dll"),
        os.path.join(qt_dir, "Qt6Gui.dll"),
        os.path.join(qt_dir, "Qt6Widgets.dll"),
    )
    missing = [path for path in required if not os.path.isfile(path)]
    if missing:
        raise RuntimeError(
            "Qt runtime 文件不完整，拒绝继续打包: "
            + ", ".join(missing)
        )

    import ctypes

    dll_dirs = []
    loaded_handles = []
    try:
        for directory in (internal, qt_dir, shiboken_dir):
            if os.path.isdir(directory):
                dll_dirs.append(os.add_dll_directory(directory))

        # LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR | LOAD_LIBRARY_SEARCH_DEFAULT_DIRS
        load_flags = 0x00000100 | 0x00001000
        for path in required:
            try:
                library = ctypes.WinDLL(path, winmode=load_flags)
                loaded_handles.append(library._handle)
            except OSError as e:
                raise RuntimeError(
                    f"Qt runtime 加载失败，拒绝继续打包: {path}: {e}"
                ) from e
    finally:
        try:
            # CDLL does not unload libraries automatically on Windows. Release
            # the Qt handles so their shared runtime DLLs can be removed when
            # the PyInstaller staging directory is flattened below.
            free_library = ctypes.windll.kernel32.FreeLibrary
            free_library.argtypes = [ctypes.c_void_p]
            free_library.restype = ctypes.c_int
            for handle in reversed(loaded_handles):
                if not free_library(ctypes.c_void_p(handle)):
                    raise ctypes.WinError(ctypes.get_last_error())
        finally:
            for directory in dll_dirs:
                directory.close()


def clean() -> None:
    """清理旧构建产物，并移除 installer/ 中当前版本的旧发布文件。"""
    print("\n[1/10] 清理旧构建...")
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
                        except Exception:
                            pass

    # installer/ 不随 dist 一起删除，但当前版本的旧文件必须先移除。
    # 否则本轮 pack 失败时，上一轮同版本安装器会被误认为是新产物。
    release_names = (
        f"dd_rec-{VERSION}-portable.7z",
        f"dd_rec-{VERSION}-portable.zip",
        f"dd_rec.Install.{VERSION}.exe",
    )
    if os.path.isdir(INSTALLER_DIR):
        for name in release_names:
            old_path = os.path.join(INSTALLER_DIR, name)
            if os.path.isfile(old_path):
                os.remove(old_path)
                print(f"   删除旧发布产物: {old_path}")


def build_app() -> None:
    """PyInstaller 打包主程序"""
    print(f"\n[2/10] PyInstaller 打包主程序...")
    if isinstance(PYTHON_EXE, str) and PYTHON_EXE.startswith("py "):
        cmd = PYTHON_EXE.split() + ["-m", "PyInstaller", APP_SPEC, "--noconfirm"]
    else:
        cmd = [PYTHON_EXE, "-m", "PyInstaller", APP_SPEC, "--noconfirm"]
    run(cmd, cwd=PROJECT_ROOT, env=_pyinstaller_env())
    _assert_no_accidental_poppler_dlls()
    _assert_qt_runtime_loadable(os.path.join(DIST_DIR, "dd_rec_app"))


def assemble_portable_dir() -> None:
    """组装 Portable 目录结构"""
    print(f"\n[4/10] 组装 Portable 目录...")

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

    # 1. 平铺真正的主程序到 portable 根。dd_rec.exe 是唯一入口，
    #    不再额外构建或复制 launcher。
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
        main_exe = os.path.join(portable_root, "dd_rec.exe")
        if not os.path.isfile(main_exe) or os.path.getsize(main_exe) == 0:
            raise RuntimeError(f"主程序未正确生成: {main_exe}")
        for legacy_name in ("dd_rec_main.exe", "launcher.log"):
            legacy_path = os.path.join(portable_root, legacy_name)
            if os.path.isfile(legacy_path):
                os.remove(legacy_path)
                print(f"   删除旧 launcher 架构残留: {legacy_name}")
    else:
        raise RuntimeError(f"找不到主程序构建目录: {app_src}")

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
      dd_rec.exe / version.ini / dd_rec.update.exe / _internal/ / ffmpeg/ / temp/
    """
    print(f"\n[6/10] 打包成 7z/zip...")
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

    print(f"\n[3/10] 生成 kachina update.exe...")
    update_exe = os.path.join(DIST_DIR, "dd_rec.update.exe")
    if os.path.exists(update_exe):
        os.remove(update_exe)
    cmd = [kachina, "pack", "-c", KACHINA_CONFIG, "-o", update_exe]
    if os.path.exists(APP_ICON):
        cmd += ["--icon", APP_ICON]
    print(f"  $ " + " ".join(cmd))
    # kachina 第一次跑会解压运行时 + 处理图标,慢则 60-180 秒,给 5 分钟
    _run_kachina(cmd, wait_for=update_exe, timeout=300)
    print(f"   创建: dd_rec.update.exe")
    return update_exe


def build_kachina_metadata(portable_root: str) -> tuple:
    """生成 metadata.json + hashed/ 目录

    kachina 增量更新 (HDiffPatch) 需要的元数据。
    - portable_root: assemble 完之后的 portable 根目录(dist/dd-rec/),
      必须包含主程序 dd_rec.exe + version.ini + update.exe + _internal + ffmpeg，
      这样更新元数据会覆盖完整的单入口程序目录
    """
    kachina = find_kachina_builder()
    print(f"\n[5/10] 生成 kachina metadata + hashed...")
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
        "-t", f"v{VERSION}",
        "-u", update_exe_abs,
    ]
    print("  $ " + " ".join(cmd))
    # gen 跑全量 hash + 压缩,几百到几千个文件
    # PyInstaller onedir 通常 1500-3000 个文件,180MB 左右 → 15 分钟保守
    _run_kachina(cmd, wait_for=metadata, timeout=3600)
    return metadata, hashed


def build_kachina_installer(metadata: str, hashed: str) -> str:
    """生成完整的 Install.exe — 给想要安装器的用户"""
    kachina = find_kachina_builder()
    print(f"\n[7/10] 生成 kachina Install.exe...")
    install_exe = os.path.join(DIST_DIR, f"dd_rec.Install.{VERSION}.exe")
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
    # pack 需要处理完整 hashed 数据；慢磁盘或杀毒扫描时可能超过 5 分钟。
    _run_kachina(cmd, wait_for=install_exe, timeout=3600)
    if not os.path.isfile(install_exe) or os.path.getsize(install_exe) == 0:
        raise RuntimeError(f"Install.exe 未成功生成: {install_exe}")
    size_mb = os.path.getsize(install_exe) / 1024 / 1024
    print(f"   创建: {os.path.basename(install_exe)} ({size_mb:.1f} MB)")
    return install_exe


def copy_release_artifacts(
    archive_path: str,
    install_exe: str,
    *optional_paths: Optional[str],
) -> list[str]:
    """把本轮正式发布产物原子复制到 installer/。

    portable 和 Install.exe 都是必需产物；任何一个缺失都让打包失败，
    避免 installer/ 中只出现绿色版，或沿用上一轮的旧安装器。
    """
    print(f"\n[10/10] 复制发布产物到 installer/...")
    required_paths = (archive_path, install_exe)
    missing = [p for p in required_paths if not p or not os.path.isfile(p)]
    if missing:
        raise RuntimeError("缺少正式发布产物: " + ", ".join(missing))

    os.makedirs(INSTALLER_DIR, exist_ok=True)
    copied = []
    for src in (*required_paths, *optional_paths):
        if not src or not os.path.isfile(src):
            continue
        dst = os.path.join(INSTALLER_DIR, os.path.basename(src))
        tmp_dst = dst + ".tmp"
        if os.path.exists(tmp_dst):
            os.remove(tmp_dst)
        try:
            shutil.copy2(src, tmp_dst)
            os.replace(tmp_dst, dst)
        finally:
            if os.path.exists(tmp_dst):
                os.remove(tmp_dst)
        copied.append(dst)
        print(f"   复制: {dst}")
    return copied


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
    build_app()
    # update.exe 必须在 metadata 之前生成(metadata 的 -u 参数要它)
    build_kachina_update()
    # assemble 必须先于 metadata:metadata 现在用 assemble 完之后的 portable 根作为输入,
    # 这样 dd_rec.exe 和 version.ini 都进入 hashed 列表并随更新替换
    assemble_portable_dir()
    # metadata 输入为 assemble 后的 portable 根，包含主程序/version.ini/_internal/ffmpeg/update.exe
    portable_root = os.path.join(DIST_DIR, "dd-rec")
    metadata, hashed = build_kachina_metadata(portable_root)

    # portable 和 Install.exe 都是正式发布的必需产物。
    # 任一步失败都应让 build.py 失败，不能留下“打包完成但没有新 installer”的状态。
    archive_path = create_7z()
    install_exe = build_kachina_installer(metadata, hashed)

    # HDiffPatch is optional; the portable archive and installer remain required.
    patch_path = build_hdiff_patch_auto(metadata)
    archive_current_patches(metadata, hashed)
    copy_release_artifacts(archive_path, install_exe, patch_path)
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
    print(f"\n[9/10] 归档 patches/{VERSION}/ (给下个版本做 patch 源)...")
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
                f"dd_rec v{VERSION} 的 kachina 更新元数据\n"
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

    print(f"\n[8/10] 生成 HDiffPatch ({prev_version} → {VERSION})...")
    patch = os.path.join(DIST_DIR, f"dd_rec-{VERSION}-patch-from-{prev_version}.zip")
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
