"""
更新模块（Portable + NSIS 兼容方案）

Portable 流程（推荐）：
  1. check_update()            → 调 GitHub API 拉最新 release，比版本号
  2. download_update(info, cb) → 下载 bilirec-{ver}.zip 到 temp/
  3. prepare_update(info, path) → 写入 pending_update.json，等待重启
  4. 重启后 launcher.py 自动应用更新

NSIS 流程（遗留，保留兼容）：
  - 主程序: core/updater.py 通过 winreg 找安装目录
  - 启动 updater.exe（NSIS 编译的轻量安装器）:
      taskkill /F /T DD录播机.exe
      → 静默安装新 Setup.exe 到原目录
      → 启动新版本
"""

import os
import sys
import json
import logging
import tempfile
import urllib.request
import urllib.error
import zipfile
import subprocess
from dataclasses import dataclass
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# 检测是否为 Portable 模式
def _detect_portable_mode() -> bool:
    """检测是否为 Portable 模式"""
    if not getattr(sys, "frozen", False):
        return False
    exe_dir = os.path.dirname(sys.executable)
    parent_dir = os.path.dirname(exe_dir)
    return os.path.exists(os.path.join(parent_dir, "version.ini"))

IS_PORTABLE = _detect_portable_mode()

# Portable 模式的导入
if IS_PORTABLE:
    from core.portable_updater import (
        check_update as _portable_check_update,
        download_update as _portable_download_update,
        prepare_update as _portable_prepare_update,
        UpdateInfo as PortableUpdateInfo,
        get_current_version as _portable_get_version,
        has_pending_update as _portable_has_pending,
        get_pending_update_info as _portable_get_pending,
    )


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    size: int
    body: str
    asset_name: str = ""


# ==================== 版本号 ====================
def get_local_version() -> str:
    """本地版本号"""
    if IS_PORTABLE:
        return _portable_get_version()

    try:
        from version import __version__
        return __version__
    except Exception:
        pass
    # fallback: 从注册表读
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\DD\DD录播机",
        ) as key:
            return winreg.QueryValueEx(key, "Version")[0]
    except Exception:
        return "0.0.0"


def get_current_version() -> str:
    """兼容别名"""
    return get_local_version()


# ==================== Portable 模式 ====================
if IS_PORTABLE:
    def check_update(max_retries: int = 3) -> Optional[UpdateInfo]:
        """Portable 模式：检查更新"""
        return _portable_check_update(max_retries)

    def download_update(info: UpdateInfo, progress_callback: Callable = None) -> str:
        """Portable 模式：下载 zip"""
        return _portable_download_update(info, progress_callback)

    def prepare_update(info: UpdateInfo, zip_path: str) -> bool:
        """Portable 模式：准备更新（写入 pending_update.json）"""
        return _portable_prepare_update(info, zip_path)

    def has_pending_update() -> bool:
        """检查是否有待更新"""
        return _portable_has_pending()

    def get_pending_update_info() -> Optional[dict]:
        """获取待更新信息"""
        return _portable_get_pending()

    def apply_update(setup_path: str) -> None:
        """Portable 模式：触发重启更新"""
        # 用户点击更新后，提示重启
        pending = get_pending_update_info()
        if pending:
            logger.info("更新已准备好，重启应用以完成更新")
        # 不在这里退出，让用户手动重启或提示重启


# ==================== NSIS 模式（遗留兼容） ====================
else:
    # GitHub 仓库配置
    GITHUB_REPO = "ivanhih/dd-rec"
    GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
    GITHUB_RELEASES = f"https://github.com/{GITHUB_REPO}/releases"
    UPDATER_EXE_NAME = "DDRecUpdater.exe"
    SETUP_TEMP_NAME = "DDRec_Setup_new.exe"

    def _read_install_dir() -> Optional[str]:
        """从注册表读 NSIS 安装时写入的安装目录。"""
        try:
            import winreg
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\DD\DD录播机",
            ) as key:
                return winreg.QueryValueEx(key, "InstallDir")[0]
        except Exception:
            pass
        if getattr(sys, "frozen", False):
            return os.path.dirname(sys.executable)
        return os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "DD录播机")

    def _find_updater_exe() -> Optional[str]:
        """找内嵌的 updater.exe"""
        candidates = []
        if getattr(sys, "frozen", False):
            candidates.append(os.path.join(os.path.dirname(sys.executable), UPDATER_EXE_NAME))
        try:
            from core.config import RESOURCE_DIR
            candidates.append(os.path.join(RESOURCE_DIR, UPDATER_EXE_NAME))
        except Exception:
            pass
        candidates.append(os.path.join(os.getcwd(), UPDATER_EXE_NAME))
        for p in candidates:
            if os.path.exists(p):
                return p
        return None

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

    def check_update(max_retries: int = 3) -> Optional[UpdateInfo]:
        """NSIS 模式：检查更新"""
        local = get_local_version()
        logger.info(f"本地版本: {local}")

        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(
                    GITHUB_API,
                    headers={"User-Agent": "ddrec-updater/1.0"},
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))

                remote_tag = (data.get("tag_name") or "").lstrip("v")
                if not remote_tag:
                    return None

                download_url = None
                size = 0
                asset_name = ""
                for asset in data.get("assets", []):
                    name = asset.get("name", "")
                    if name.endswith(".exe") and "Setup" in name:
                        download_url = asset.get("browser_download_url")
                        size = asset.get("size", 0)
                        asset_name = name
                        break

                if not download_url:
                    return None

                logger.info(f"最新版本: {remote_tag}")

                if _parse_semver(remote_tag) <= _parse_semver(local):
                    return None

                return UpdateInfo(
                    version=remote_tag,
                    download_url=download_url,
                    size=size,
                    body=data.get("body", "") or "",
                    asset_name=asset_name,
                )
            except Exception as e:
                logger.warning(f"检查更新失败: {e}")

        return None

    def download_update(info: UpdateInfo, progress_callback: Callable = None) -> str:
        """NSIS 模式：下载 Setup.exe"""
        setup_path = os.path.join(tempfile.gettempdir(), SETUP_TEMP_NAME)
        if os.path.exists(setup_path):
            try:
                os.remove(setup_path)
            except OSError:
                pass

        req = urllib.request.Request(
            info.download_url,
            headers={"User-Agent": "ddrec-updater/1.0"},
        )

        with urllib.request.urlopen(req, timeout=600) as resp:
            total = int(resp.headers.get("Content-Length", 0) or 0)
            downloaded = 0
            with open(setup_path, "wb") as f:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total > 0:
                        try:
                            progress_callback(int(downloaded * 100 / total))
                        except Exception:
                            pass

        return setup_path

    def apply_update(setup_path: str) -> None:
        """NSIS 模式：触发更新"""
        updater = _find_updater_exe()
        if not updater:
            raise RuntimeError(f"找不到 {UPDATER_EXE_NAME}，无法更新。")

        install_dir = _read_install_dir()
        if not install_dir:
            raise RuntimeError("找不到安装路径")

        if not os.path.exists(setup_path):
            raise RuntimeError(f"下载文件已丢失: {setup_path}")

        args = [
            updater,
            f"/UPDATER_PATH={setup_path}",
            f"/INSTDIR={install_dir}",
            "/LAUNCH=1",
        ]
        CREATE_NO_WINDOW = 0x08000000
        subprocess.Popen(
            args,
            creationflags=CREATE_NO_WINDOW,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
        )

        import time
        time.sleep(0.2)
        os._exit(0)

    def has_pending_update() -> bool:
        return False

    def get_pending_update_info() -> Optional[dict]:
        return None


# ==================== 兼容层 ====================
# 提前定义 semver 函数（两边都会用）
def _compat_parse_semver(v: str) -> tuple:
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


def _compat_is_newer(remote: str, local: str) -> bool:
    return _compat_parse_semver(remote) > _compat_parse_semver(local)


def _get_compat_semver():
    """获取兼容的 semver 函数"""
    if IS_PORTABLE:
        from core.portable_updater import _parse_semver
        return _parse_semver
    return _compat_parse_semver


def _get_compat_is_newer():
    """获取兼容的 is_newer 函数"""
    if IS_PORTABLE:
        from core.portable_updater import _is_newer
        return _is_newer
    return _compat_is_newer


def check_and_update(progress_callback=None) -> Optional[UpdateInfo]:
    """兼容旧名"""
    return check_update()


def parse_version(version_str: str) -> tuple:
    """兼容旧名"""
    return _get_compat_semver()(version_str)


def is_newer_version(new_version: str, current_version: str) -> bool:
    """兼容旧名"""
    return _get_compat_is_newer()(new_version, current_version)


def download_file(url: str, dest_path: str) -> bool:
    """兼容旧名"""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
        )
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(dest_path, "wb") as f:
                f.write(resp.read())
        return True
    except Exception as e:
        logger.error(f"download_file 失败: {e}")
        return False


def quit_and_update(new_exe_path: str) -> None:
    """兼容旧名"""
    logger.warning("quit_and_update 已弃用")


def check_for_updates(parent=None):
    """兼容旧名"""
    from core.simple_updater import show_update_dialog
    info = check_update()
    if info:
        show_update_dialog(info, parent)
