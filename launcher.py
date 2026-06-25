# launcher.py
"""
DD录播机 启动器（Portable 方案）

功能：
  1. 读取 version.ini 获取当前版本
  2. 启动 dd_rec-{version}/dd_rec_main.exe
  3. 检测 pending update 并应用

用户双击 dd_rec.exe → 启动器启动 → 读取版本 → 启动主程序 → 启动器退出
"""

import os
import sys
import json
import shutil
import subprocess
import zipfile
import tempfile
import logging
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
# 主程序名称（在 dd_rec-{version}/ 目录下）
MAIN_APP_NAME = "dd_rec_main.exe"
# 版本文件
VERSION_INI = "version.ini"
# 待更新标记文件
PENDING_UPDATE_FILE = "pending_update.json"


def _msgbox(title: str, message: str, icon: int = 0x10) -> None:
    """用 Win32 API 弹出消息框（无需 Qt 依赖，适合轻量 launcher）

    Args:
        title: 标题栏文字
        message: 消息正文
        icon: 图标类型（0x10=错误, 0x20=问号, 0x30=警告, 0x40=信息）
    """
    try:
        ctypes.windll.user32.MessageBoxW(0, str(message), str(title), icon)
    except Exception:
        # 极端情况（非 Windows 或 user32 不可用）→ 写日志
        logger.error(f"[MessageBox] {title}: {message}")


def _setup_logging(app_dir: str) -> None:
    """设置日志：同时写入 launcher.log 和 stderr"""
    log_file = os.path.join(app_dir, "launcher.log")
    try:
        file_handler = logging.FileHandler(log_file, "a", encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [launcher] %(levelname)s: %(message)s'
        ))
        # 只给 root logger 添加 FileHandler（避免重复输出到 stderr）
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


def read_pending_update(app_dir: str) -> dict:
    """读取待更新信息"""
    pending_path = os.path.join(app_dir, PENDING_UPDATE_FILE)
    try:
        if os.path.exists(pending_path):
            with open(pending_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"读取 pending_update.json 失败: {e}")
    return {}


def delete_pending_update(app_dir: str):
    """删除待更新标记文件"""
    pending_path = os.path.join(app_dir, PENDING_UPDATE_FILE)
    try:
        if os.path.exists(pending_path):
            os.remove(pending_path)
            logger.info("已删除 pending_update.json")
    except Exception as e:
        logger.error(f"删除 pending_update.json 失败: {e}")


def extract_zip_to_app(archive_path: str, app_dir: str, version: str) -> bool:
    """解压 zip 到 portable 根目录。

    zip 的结构和 portable 根目录一致（build.py assemble_portable_dir 产出）：
      dd_rec.exe                ← 启动器（覆盖现有）
      version.ini               ← 覆盖为新版本
      dd_rec-{version}/         ← 新主程序
        ├── dd_rec_main.exe
        └── _internal/
      ffmpeg/                   ← (如果有，build.py 也放在根)
      temp/

    全部直接解压到 app_dir，覆盖式。launcher 已经在 process 中，正在用
    app_dir/version.ini / 自身 dd_rec.exe，所以应该用临时目录解压再合并，
    避免覆盖运行时文件。但本工具的 portable 模式 dd_rec.exe 是「一次启动
    读完就 _exit」的，不会卡住文件，所以直接覆盖式解压是安全的。
    """
    try:
        logger.info(f"开始解压更新包到 {app_dir}")

        # 0. 清理上一次失败留下的临时文件
        leftover_new = os.path.join(app_dir, "dd_rec.exe.new")
        if os.path.exists(leftover_new):
            try:
                os.remove(leftover_new)
                logger.info("已清理上次的 dd_rec.exe.new 残留")
            except OSError as e:
                logger.warning(f"清理 dd_rec.exe.new 失败: {e}")

        # 1. 先解压到 app_dir 同盘的临时子目录（关键：避免 shutil.move 跨盘！）
        #    跨盘 move = copy+remove 非原子，E 盘 portable + C 盘 Temp 必然踩这个坑。
        #    现在临时目录在 app_dir 下，move 变同盘 os.rename，原子瞬时。
        import tempfile, time
        ts = int(time.time())
        tmp_dir = os.path.join(app_dir, f"_update_tmp_{ts}")
        try:
            logger.info(f"解压到同盘临时目录: {tmp_dir}")
            os.makedirs(tmp_dir, exist_ok=True)
            with zipfile.ZipFile(archive_path, "r") as zf:
                zf.extractall(tmp_dir)
        except Exception:
            # 解压失败也要清掉空目录
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise

        # 2. 把临时目录里的内容搬到 app_dir/。
        #    注意顺序：先 dd_rec-{version}/（主程序），再其它文件，最后 dd_rec.exe（启动器）——
        #    launcher 在主程序重启后才会被覆盖，覆盖自己不会有问题。
        entries = os.listdir(tmp_dir)
        # 排序：dd_rec-{version}/ 优先 → 其它顶层文件 → dd_rec.exe 最后
        def _sort_key(name):
            if name.startswith("dd_rec-") and name != "dd_rec.exe":
                return (0, name)
            if name == "dd_rec.exe":
                return (2, name)
            return (1, name)
        entries.sort(key=_sort_key)

        for entry in entries:
            src = os.path.join(tmp_dir, entry)
            dst = os.path.join(app_dir, entry)
            try:
                if entry == "dd_rec.exe":
                    # 不要覆盖 launcher 自身！当前进程就是 dd_rec.exe，
                    # Windows 文件锁让任何 shutil.copy2/os.replace 都失败（WinError 32）。
                    # launcher 的职责只是"读 version.ini 启动主程序"，本身不需要
                    # 跟主程序一起升。这次更新只升级 dd_rec-{version}/ + version.ini，
                    # launcher 自升级留到以后单独设计。
                    logger.info("跳过: dd_rec.exe（不覆盖启动器自身）")
                    continue
                elif os.path.isdir(src):
                    # 同盘 os.rename（原子）。目标存在则先删。
                    if os.path.exists(dst):
                        shutil.rmtree(dst)
                    os.rename(src, dst)
                    logger.info(f"已搬入: {entry}")
                else:
                    # 其它文件（version.ini / ffmpeg dll 等）覆盖式拷贝
                    shutil.copy2(src, dst)
                    logger.info(f"已覆盖: {entry}")
            except Exception as e:
                logger.error(f"移动 {entry} 失败: {e}")
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return False

        # 3. 清掉临时目录（os.rename 已经把 dd_rec-新版本/ 移走了，
        #    顶层只可能剩 launcher 复制后的源文件副本，正常情况已空）
        shutil.rmtree(tmp_dir, ignore_errors=True)

        logger.info(f"解压完成: {app_dir}")
        return True

    except Exception as e:
        logger.error(f"解压失败: {e}")
        return False


def cleanup_old_app_dirs(app_dir: str, keep_version: str) -> int:
    """删掉 app_dir 下所有 dd_rec-X.Y.Z/ 目录，但保留 dd_rec-{keep_version}/。

    返回删除的目录数。

    为什么要清理:
      - 减少磁盘占用（每个 ~150MB）
      - 用户期望"更新就是替换"，旧版本应该消失
      - 避免 launcher 启动时找不到新主程序（因为旧目录还在，干扰用户判断）

    不会删:
      - dd_rec.exe（启动器本身）
      - version.ini / config.json / ffmpeg/ / temp/
      - dd_rec-{keep_version}/（刚解压的新版主程序）
    """
    keep_name = f"dd_rec-{keep_version}"
    removed = 0
    try:
        for entry in os.listdir(app_dir):
            full = os.path.join(app_dir, entry)
            # 只删 dd_rec-X.Y.Z/ 这种版本化目录，且不是 keep 的
            if (
                entry.startswith("dd_rec-")
                and entry != keep_name
                and os.path.isdir(full)
            ):
                try:
                    shutil.rmtree(full)
                    logger.info(f"已清理旧版本目录: {entry}")
                    removed += 1
                except Exception as e:
                    logger.warning(f"清理旧版本目录失败 {entry}: {e}")
    except Exception as e:
        logger.error(f"扫描 app_dir 失败: {e}")
    return removed


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
    """扫描 app_dir 下所有 dd_rec-X.Y.Z/，返回版本最高的目录名（如 dd_rec-1.0.8）"""
    import re
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
      1. version.ini 必须存在且能解析
      2. version.ini 声明的 dd_rec-{version}/ 目录必须存在 + 含 dd_rec_main.exe
         - 如果不存在 → 扫描可用的 dd_rec-X.Y.Z/ 目录，选最高版本并更新 version.ini
      3. 其他 dd_rec-X.Y.Z/ 目录（孤儿）会被清理掉
      4. 如果没有任何可用版本目录 → 返回 False

    返回 True 表示状态自洽；False 表示无法自洽（launcher 应该报错退出）。
    """
    version_ini = os.path.join(app_dir, VERSION_INI)
    if not os.path.exists(version_ini):
        logger.error(f"version.ini 不存在: {version_ini}")
        return False

    declared_version = read_version(version_ini)
    if not declared_version:
        logger.error(f"version.ini 无法解析: {version_ini}")
        return False

    target_dir_name = f"dd_rec-{declared_version}"
    target_main_exe = os.path.join(app_dir, target_dir_name, MAIN_APP_NAME)

    if not os.path.exists(target_main_exe):
        # version.ini 声明 1.0.7，但 dd_rec-1.0.7/dd_rec_main.exe 不存在
        # 尝试从可用版本中恢复
        logger.warning(
            f"version.ini 声明 {declared_version}，但 {target_main_exe} 不存在"
        )
        available = [
            d for d in os.listdir(app_dir)
            if d.startswith("dd_rec-") and os.path.isdir(os.path.join(app_dir, d))
            and os.path.exists(os.path.join(app_dir, d, MAIN_APP_NAME))
        ]
        logger.info(f"可用的 dd_rec-* 目录: {available}")

        latest_dir = _find_latest_version_dir(app_dir)
        if latest_dir:
            # 从目录名提取版本号
            version_from_dir = latest_dir[len("dd_rec-"):]
            logger.info(f"恢复: 更新 version.ini → {version_from_dir}（目录 {latest_dir}）")
            try:
                with open(version_ini, "w", encoding="utf-8") as f:
                    f.write(f"version={version_from_dir}\n")
                declared_version = version_from_dir
                target_dir_name = latest_dir
                target_main_exe = os.path.join(app_dir, target_dir_name, MAIN_APP_NAME)
            except Exception as e:
                logger.error(f"无法写入 version.ini: {e}")
                return False
        else:
            logger.error("没有任何可用的版本目录，无法恢复")
            return False

    # version.ini 对应的目录存在，清理其他孤儿 dd_rec-X.Y.Z/
    for entry in os.listdir(app_dir):
        full = os.path.join(app_dir, entry)
        if (
            entry.startswith("dd_rec-")
            and entry != target_dir_name
            and os.path.isdir(full)
        ):
            try:
                shutil.rmtree(full)
                logger.info(f"健康检查: 已清理孤儿版本目录 {entry}")
            except Exception as e:
                logger.warning(f"健康检查: 清理孤儿目录失败 {entry}: {e}")

    return True


def apply_pending_update(app_dir: str) -> bool:
    """应用待更新（原子化版本）

    流程:
      1. 读 pending_update.json → 获取 version 和 zip_path
      2. 校验 zip_path 存在
      3. 备份 version.ini（失败时回滚用）
      4. 解压 zip 到临时目录 → 移到 app_dir/
         - 失败 → 回滚：恢复 version.ini.bak → return False
      5. 全部解压成功 → 写入 version.ini（新版本号）
      6. 删除 pending_update.json + 临时 zip + backup → return True

    不再在 apply 阶段清理旧版本目录，留给 ensure_consistent_state
    在新版本成功运行后的下次启动时自然清理。
    """
    pending = read_pending_update(app_dir)
    if not pending:
        return False

    version = pending.get("version", "")
    zip_path = pending.get("zip_path", "")

    if not version or not zip_path:
        logger.error("pending_update.json 格式错误")
        return False

    if not os.path.exists(zip_path):
        logger.error(f"更新包不存在: {zip_path}")
        return False

    # 1. 备份 version.ini（解压失败时回滚用）
    version_ini_path = os.path.join(app_dir, VERSION_INI)
    backup_path = version_ini_path + ".bak"
    backup_content = None
    if os.path.exists(version_ini_path):
        try:
            with open(version_ini_path, "r", encoding="utf-8") as f:
                backup_content = f.read()
            shutil.copy2(version_ini_path, backup_path)
            logger.info("已备份 version.ini")
        except Exception as e:
            logger.warning(f"备份 version.ini 失败: {e}")

    # 2. 解压 zip
    extract_ok = extract_zip_to_app(zip_path, app_dir, version)

    if not extract_ok:
        # 解压失败 → 回滚 version.ini + 清掉半残的 dd_rec-{version}/
        logger.error("解压失败，回滚 version.ini")
        if backup_content is not None:
            try:
                with open(version_ini_path, "w", encoding="utf-8") as f:
                    f.write(backup_content)
                logger.info("已回滚 version.ini")
            except Exception as e:
                logger.error(f"回滚 version.ini 失败: {e}")
        # 清掉半残的新版本目录（防御性：避免下次启动 launcher 状态混乱）
        new_app_dir = os.path.join(app_dir, f"dd_rec-{version}")
        if os.path.isdir(new_app_dir):
            try:
                shutil.rmtree(new_app_dir)
                logger.info(f"已清理半残目录: dd_rec-{version}/")
            except Exception as e:
                logger.warning(f"清理半残目录失败: {e}")
        # 清掉可能留下的 _update_tmp_xxx
        try:
            for name in os.listdir(app_dir):
                if name.startswith("_update_tmp_"):
                    shutil.rmtree(os.path.join(app_dir, name), ignore_errors=True)
        except Exception:
            pass
        return False

    # 3. 全部解压成功 → 写入 version.ini（新版本号）
    try:
        with open(version_ini_path, "w", encoding="utf-8") as f:
            f.write(f"version={version}\n")
        logger.info(f"已更新 version.ini 为 {version}")
    except Exception as e:
        logger.error(f"更新 version.ini 失败: {e}")
        # 尝试回滚
        if backup_content is not None:
            try:
                with open(version_ini_path, "w", encoding="utf-8") as f:
                    f.write(backup_content)
                logger.info("已回滚 version.ini")
            except Exception:
                pass
        return False

    # 4. 删除 pending_update.json + 临时 zip + backup
    delete_pending_update(app_dir)
    try:
        os.remove(zip_path)
        logger.info(f"已删除临时文件: {zip_path}")
    except Exception:
        pass

    # 5. 删除 backup
    try:
        if os.path.exists(backup_path):
            os.remove(backup_path)
    except Exception:
        pass

    return True


def migrate_old_data(app_dir: str) -> bool:
    r"""迁移旧版本数据（从 %AppData%\DDRec 迁移到 portable 目录）"""
    appdata_dir = os.path.join(os.environ.get("APPDATA", ""), "DDRec")
    if not os.path.exists(appdata_dir):
        return False

    # 检查是否有需要迁移的文件
    config_src = os.path.join(appdata_dir, "config.json")
    data_src = os.path.join(appdata_dir, "data.json")

    if not os.path.exists(config_src) and not os.path.exists(data_src):
        return False

    # 目标路径
    config_dst = os.path.join(app_dir, "config.json")
    data_dst = os.path.join(app_dir, "data.json")

    migrated = False
    try:
        if os.path.exists(config_src) and not os.path.exists(config_dst):
            shutil.copy2(config_src, config_dst)
            logger.info(f"已迁移 config.json")
            migrated = True

        if os.path.exists(data_src) and not os.path.exists(data_dst):
            shutil.copy2(data_src, data_dst)
            logger.info(f"已迁移 data.json")
            migrated = True

        if migrated:
            # 写迁移标记，避免重复迁移
            marker_path = os.path.join(appdata_dir, "MIGRATED_TO_PORTABLE.txt")
            with open(marker_path, "w", encoding="utf-8") as f:
                f.write("已迁移到 portable 目录\n")

    except Exception as e:
        logger.error(f"迁移数据失败: {e}")

    return migrated


def cleanup_old_temp(app_dir: str):
    """清理旧临时文件"""
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
    """启动主程序"""
    main_exe = os.path.join(app_dir, f"dd_rec-{version}", MAIN_APP_NAME)

    if not os.path.exists(main_exe):
        logger.error(f"主程序不存在: {main_exe}")
        return False

    try:
        # 启动主程序
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
            # 没有 tty / stdin 关闭 → 直接退出
            pass
    sys.exit(code)


def main():
    app_dir = get_app_dir()

    # ---- 尽早设置文件日志，保证所有日志落在 launcher.log ----
    _setup_logging(app_dir)

    logger.info("=" * 50)
    logger.info("DD录播机 启动器 v1.0")
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

    # 1. 处理待更新
    pending = read_pending_update(app_dir)
    if pending:
        logger.info(f"检测到待更新: v{pending.get('version', '')}")
        if apply_pending_update(app_dir):
            # 更新 version.ini 中的版本
            current_version = pending.get("version", current_version)
            logger.info(f"更新应用成功，当前版本: {current_version}")
        else:
            logger.error("更新应用失败，将使用当前版本继续")
            _msgbox("DD录播机 更新失败",
                    f"自动更新到 v{pending.get('version', '未知')} 失败。\n"
                    f"将使用当前版本 v{current_version} 继续运行。\n\n"
                    "请检查磁盘空间是否足够，或手动下载新版本。",
                    icon=0x30)
            # 删除损坏的待更新标记
            delete_pending_update(app_dir)

    # 2. 迁移旧数据（首次运行）
    migrate_old_data(app_dir)

    # 3. 清理临时文件
    cleanup_old_temp(app_dir)

    # 4. 健康检查（清理孤儿版本目录 + 验证 version.ini 一致性）
    if not ensure_consistent_state(app_dir):
        logger.error("app_dir 状态不自洽，无法启动主程序")
        _msgbox("DD录播机 启动失败",
                f"安装目录状态异常，无法启动。\n"
                f"目录: {app_dir}\n\n"
                "请重新解压便携版，或重新安装。",
                icon=0x10)
        sys.exit(1)

    # 5. 启动主程序
    main_exe = os.path.join(app_dir, f"dd_rec-{current_version}", MAIN_APP_NAME)
    if not os.path.exists(main_exe):
        logger.error(f"主程序不存在: {main_exe}")
        # 尝试列出可用版本
        available = [d for d in os.listdir(app_dir) if d.startswith("dd_rec-")]
        if available:
            logger.info(f"可用版本: {available}")
        _msgbox("DD录播机 启动失败",
                f"找不到主程序:\n{main_exe}\n\n"
                f"可用版本: {', '.join(available) if available else '无'}",
                icon=0x10)
        sys.exit(1)

    # 6. 启动主程序
    if start_main_app(app_dir, current_version):
        logger.info("主程序已启动，启动器退出")
        # 给主程序 200ms 启动时间，确认进程起来了再退 launcher
        # （防止主程序因为 dll 缺失等原因秒退时，launcher 已经 _exit 用户看不到任何提示）
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
