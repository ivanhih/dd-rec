# launcher.py
"""
DD录播机 启动器（kachina 模式）

职责（极简化）：
  1. 读 version.ini 获取当前版本
  2. 启动 dd_rec-{version}/dd_rec_main.exe
  3. 启动器退出

更新流程已完全交给 kachina update.exe（DDRec.update.exe）:
  - kachina update.exe 在主程序退出后接管:
    1. 关闭 dd_rec_main.exe / dd_rec.exe
    2. HDiffPatch 增量更新 dd_rec-{ver}/
    3. 替换 launcher 自身（launcher 已在内存中退出,文件可写）
    4. 重启 launcher → 启动新主程序

launcher 自身不再需要自升级逻辑（kachina update.exe 会直接换掉它），
也彻底不再使用 pending_update.json。
"""

import os
import sys
import re
import shutil
import logging
import subprocess
import ctypes
from typing import Optional

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [launcher] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
    ]
)
logger = logging.getLogger(__name__)

# 启动器名称（打包后为 dd_rec.exe）
LAUNCHER_NAME = "dd_rec.exe"
# 主程序名称（直接在 portable 根目录,与 update.exe 平铺,这样 kachina update.exe
# 启动时检查 current_exe().parent() / exe_name 时能命中,自动走就地升级模式）
MAIN_APP_NAME = "dd_rec_main.exe"
# 版本文件
VERSION_INI = "version.ini"


def _msgbox(title: str, message: str, icon: int = 0x10) -> None:
    """用 Win32 API 弹出消息框（无需 Qt 依赖，适合轻量 launcher）"""
    try:
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), icon)
    except Exception:
        logger.error(f"[MessageBox] {title}: {message}")


def _setup_logging(app_dir: str) -> None:
    """设置日志：同时写入 launcher.log 和 stderr"""
    log_file = os.path.join(app_dir, "launcher.log")
    try:
        file_handler = logging.FileHandler(log_file, "a", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [launcher] %(levelname)s: %(message)s'
        ))
        root_logger = logging.getLogger()
        root_logger.addHandler(file_handler)
        logger.info(f"日志文件: {log_file}")
    except Exception as e:
        logger.warning(f"无法创建日志文件 {log_file}: {e}")


def get_app_dir() -> str:
    """获取应用根目录（启动器所在目录）

    启动器以 onefile 模式打包到 portable 根目录（dd-rec/dd_rec.exe），
    所以 sys.executable 的父目录就是 portable 根目录 = app_dir。

    注意：PyInstaller onefile 模式下 sys.executable 指向真实 exe 路径
    （在 dd-rec/dd_rec.exe），sys._MEIPASS 才是临时解压目录。这里我们
    需要的是真实 exe 路径所在的目录，所以用 sys.executable 没问题。
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    # 开发态：返回项目根目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_version(ini_path: str) -> str:
    """读取 version.ini 中的版本号"""
    try:
        if os.path.exists(ini_path):
            with open(ini_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("version="):
                        return line.split("=", 1)[1].strip()
    except Exception as e:
        logger.error(f"读取 version.ini 失败: {e}")
    return ""


def _parse_semver(v: str) -> tuple:
    """'1.0.2' → (1, 0, 2)"""
    s = str(v).strip().lstrip("v")
    try:
        parts = []
        for x in s.split("."):
            x = x.strip()
            if x.isdigit():
                parts.append(int(x))
            else:
                head = ""
                for ch in x:
                    if ch.isdigit():
                        head += ch
                    else:
                        break
                parts.append(int(head) if head else 0)
        return tuple(parts[:3])
    except Exception:
        return (0, 0, 0)


def _find_latest_version_dir(app_dir: str) -> Optional[str]:
    """扫描 app_dir 下所有 dd_rec-X.Y.Z/，返回版本最高的目录名（如 dd_rec-1.0.8）

    已废弃:主程序现在直接平铺在 portable 根,不再用版本化子目录。
    保留此函数仅用于 ensure_consistent_state() 清理孤儿目录前的探测。
    """
    best_version = (0, 0, 0)
    best_dir = None
    try:
        for entry in os.listdir(app_dir):
            if not entry.startswith("dd_rec-"):
                continue
            full = os.path.join(app_dir, entry)
            if not os.path.isdir(full):
                continue
            exe = os.path.join(full, MAIN_APP_NAME)
            if not os.path.exists(exe):
                continue
            m = re.match(r"dd_rec-(\d+)\.(\d+)\.(\d+)", entry)
            if m:
                v = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if v > best_version:
                    best_version = v
                    best_dir = entry
    except Exception as e:
        logger.warning(f"扫描版本目录失败: {e}")
    return best_dir


def ensure_consistent_state(app_dir: str) -> bool:
    """确保 app_dir 状态自洽（launcher 启动时调用）。

    检查项:
      1. dd_rec_main.exe 必须存在（现在直接平铺在 portable 根）
      2. version.ini 仍读取并校验（兼容老用户、launcher 日志用）
      3. 老版本的 dd_rec-X.Y.Z/ 孤儿目录会被清理（兼容老用户升级上来的残留）
      4. 如果主程序不在 → 返回 False

    返回 True 表示状态自洽；False 表示无法自洽（launcher 应该报错退出）。
    """
    main_exe = os.path.join(app_dir, MAIN_APP_NAME)
    if not os.path.exists(main_exe):
        logger.error(f"主程序不存在: {main_exe}")
        # 兜底:看看有没有 dd_rec-X.Y.Z/ 子目录(老 portable 结构)
        legacy = _find_latest_version_dir(app_dir)
        if legacy:
            logger.warning(
                f"检测到老版本 portable 结构 {legacy}/,将版本号 {legacy[len('dd_rec-'):]} 写入 version.ini"
            )
            try:
                version_ini = os.path.join(app_dir, VERSION_INI)
                with open(version_ini, "w", encoding="utf-8") as f:
                    f.write(f"version={legacy[len('dd_rec-'):]}\n")
            except Exception as e:
                logger.error(f"无法写入 version.ini: {e}")
            # 不返回 False —— 让 start_main_app() 报错给用户更清楚的提示
        return False

    # 主程序存在 → 清理老版本残留的 dd_rec-X.Y.Z/ 子目录(不参与新 portable)
    for entry in os.listdir(app_dir):
        full = os.path.join(app_dir, entry)
        if (
            entry.startswith("dd_rec-")
            and entry != MAIN_APP_NAME.removesuffix(".exe")
            and os.path.isdir(full)
        ):
            try:
                shutil.rmtree(full)
                logger.info(f"健康检查: 已清理孤儿版本目录 {entry}")
            except Exception as e:
                logger.warning(f"健康检查: 清理孤儿目录失败 {entry}: {e}")

    return True


def cleanup_old_temp(app_dir: str):
    """清理 temp/ 目录里的旧下载文件（kachina update.exe 完成后可能留残）"""
    temp_dir = os.path.join(app_dir, "temp")
    if os.path.exists(temp_dir):
        try:
            for f in os.listdir(temp_dir):
                path = os.path.join(temp_dir, f)
                if os.path.isfile(path):
                    os.remove(path)
        except Exception as e:
            logger.warning(f"清理临时目录失败: {e}")


def start_main_app(app_dir: str, version: str) -> bool:
    """启动主程序（平坦化后直接在 portable 根,不再有 dd_rec-{version}/ 子目录）"""
    main_exe = os.path.join(app_dir, MAIN_APP_NAME)

    if not os.path.exists(main_exe):
        logger.error(f"主程序不存在: {main_exe}")
        return False

    try:
        logger.info(f"启动主程序: {main_exe}")
        subprocess.Popen(
            [main_exe],
            cwd=app_dir,
        )
        return True
    except Exception as e:
        logger.error(f"启动主程序失败: {e}")
        return False


def _wait_or_exit(msg: str, code: int = 1):
    """GUI 模式（onefile + console=False）无 stdin，不能 input()，直接退出。
       dev 模式有终端，等待用户按回车方便看错误。
    """
    if sys.stdout and sys.stdin:
        try:
            if sys.stdin.isatty():
                input(msg)
        except (EOFError, ValueError, OSError):
            pass
    sys.exit(code)


def main():
    app_dir = get_app_dir()

    # ---- 尽早设置文件日志，保证所有日志落在 launcher.log ----
    _setup_logging(app_dir)

    logger.info("=" * 50)
    logger.info("DD录播机 启动器 (kachina 模式)")
    logger.info("=" * 50)

    version_ini_path = os.path.join(app_dir, VERSION_INI)

    # 读取当前版本
    current_version = read_version(version_ini_path)
    if not current_version:
        logger.error(f"无法读取版本信息: {version_ini_path}")
        _msgbox("DD录播机 启动失败",
                f"无法读取版本文件:\n{version_ini_path}\n"
                "请检查安装是否完整。",
                icon=0x10)
        sys.exit(1)

    logger.info(f"应用目录: {app_dir}")
    logger.info(f"当前版本: {current_version}")

    # 1. 清理临时文件
    cleanup_old_temp(app_dir)

    # 2. 健康检查
    if not ensure_consistent_state(app_dir):
        logger.error("app_dir 状态不自洽，无法启动主程序")
        _msgbox("DD录播机 启动失败",
                f"安装目录状态异常，无法启动。\n"
                f"目录: {app_dir}\n\n"
                "请重新解压便携版，或重新安装。",
                icon=0x10)
        sys.exit(1)

    # 3. 启动主程序(平坦化后直接在 portable 根)
    main_exe = os.path.join(app_dir, MAIN_APP_NAME)
    if not os.path.exists(main_exe):
        logger.error(f"主程序不存在: {main_exe}")
        available = [d for d in os.listdir(app_dir) if d.startswith("dd_rec-")]
        if available:
            logger.info(f"可用版本: {available}")
        _msgbox("DD录播机 启动失败",
                f"找不到主程序:\n{main_exe}\n\n"
                f"可用版本: {', '.join(available) if available else '无'}",
                icon=0x10)
        sys.exit(1)

    if start_main_app(app_dir, current_version):
        logger.info("主程序已启动，启动器退出")
        # 给主程序 200ms 启动时间，确认进程起来了再退 launcher
        import time as _t
        _t.sleep(0.2)
        os._exit(0)
    else:
        logger.error("启动主程序失败")
        _msgbox("DD录播机 启动失败",
                f"无法启动主程序:\n{main_exe}\n\n"
                "请检查杀毒软件是否拦截了程序。",
                icon=0x10)
        sys.exit(1)


if __name__ == "__main__":
    main()