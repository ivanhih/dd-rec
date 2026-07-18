#!/usr/bin/env python3
"""简化打包脚本 — PyInstaller + zip，跳过 kachina（builder gen 崩溃）"""
import os, sys, shutil, subprocess, zipfile

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(PROJECT_ROOT, "dist")
BUILD_DIR = os.path.join(PROJECT_ROOT, "build")
APP_SPEC = os.path.join(PROJECT_ROOT, "app.spec")
PYTHON_EXE = r"C:\Users\user\miniconda3\python.exe"
FFMPEG_BIN = r"C:\ffmpeg\bin"
FFMPEG_EXES = ("ffmpeg.exe", "ffprobe.exe")

def run(cmd, **kw):
    print("  $ " + " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)

def main():
    from version import __version__ as VERSION

    # 1. clean
    print("[1/4] 清理...")
    for d in (DIST_DIR, BUILD_DIR):
        if os.path.exists(d): shutil.rmtree(d)

    # 2. build app
    print("[2/4] 打包主程序...")
    run([PYTHON_EXE, "-m", "PyInstaller", APP_SPEC, "--noconfirm"], cwd=PROJECT_ROOT)

    # 3. assemble portable
    print("[3/4] 组装 Portable 目录...")
    portable_root = os.path.join(DIST_DIR, "dd-rec")
    os.makedirs(portable_root, exist_ok=True)

    # app (onedir 平铺，dd_rec.exe 是唯一入口)
    app_src = os.path.join(DIST_DIR, "dd_rec_app")
    if os.path.exists(app_src):
        for entry in os.listdir(app_src):
            src = os.path.join(app_src, entry)
            dst = os.path.join(portable_root, entry)
            if os.path.isdir(src):
                if os.path.exists(dst): shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                if os.path.exists(dst): os.remove(dst)
                shutil.copy2(src, dst)
        shutil.rmtree(app_src)
        print("  平铺主程序 OK")
    main_exe = os.path.join(portable_root, "dd_rec.exe")
    if not os.path.isfile(main_exe):
        raise RuntimeError(f"主程序未生成: {main_exe}")

    # ffmpeg
    ffmpeg_dst = os.path.join(portable_root, "ffmpeg")
    os.makedirs(ffmpeg_dst, exist_ok=True)
    for exe in FFMPEG_EXES:
        shutil.copy2(os.path.join(FFMPEG_BIN, exe), os.path.join(ffmpeg_dst, exe))
    print("  复制 ffmpeg OK")

    # version.ini
    with open(os.path.join(portable_root, "version.ini"), "w") as f:
        f.write(f"version={VERSION}\n")
    print(f"  version.ini: {VERSION}")

    # temp/
    os.makedirs(os.path.join(portable_root, "temp"), exist_ok=True)

    # 4. zip
    print("[4/4] 打包 zip...")
    zip_path = os.path.join(DIST_DIR, f"dd_rec-{VERSION}.zip")
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(portable_root):
            for fn in files:
                fp = os.path.join(root, fn)
                zf.write(fp, os.path.relpath(fp, portable_root))
    mb = os.path.getsize(zip_path) / 1024 / 1024
    print(f"  {os.path.basename(zip_path)} ({mb:.1f} MB)")

    # summary
    print("\n" + "=" * 50)
    print(f"✅ 打包完成! 产物: dist/{os.path.basename(zip_path)} ({mb:.1f} MB)")
    print("=" * 50)
    return 0

if __name__ == "__main__":
    sys.exit(main())
