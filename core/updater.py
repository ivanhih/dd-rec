"""
更新模块（Portable + kachina 模式）

Portable 流程：
  1. check_update()            → 调 GitHub API 拉最新 release，比版本号
  2. 用户点"立即更新" → spawn DDRec.update.exe → 主程序退出
  3. kachina update.exe 自己下载/HDiffPatch/替换 launcher 自身

不再走 launcher 自更新 / pending_update.json / 主程序下载的流程。
"""

import os
import sys
import json
import logging
import urllib.request
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# 检测是否为 Portable 模式
def _detect_portable_mode() -> bool:
    """检测是否为 Portable 模式

    平坦化后:version.ini 和 dd_rec_main.exe 在 portable 根(同目录)
    老版本结构(用 dd_rec-{ver}/ 子目录)升级上来的用户:兼容,允许父目录
    """
    if not getattr(sys, "frozen", False):
        return False
    exe_dir = os.path.dirname(sys.executable)
    # 平坦化主路径:version.ini 在主程序同目录
    if os.path.exists(os.path.join(exe_dir, "version.ini")):
        return True
    # 兼容老结构(主程序在 dd_rec-{ver}/ 子目录)
    parent_dir = os.path.dirname(exe_dir)
    return os.path.exists(os.path.join(parent_dir, "version.ini"))

IS_PORTABLE = _detect_portable_mode()

# Portable 模式的导入
if IS_PORTABLE:
    from core.portable_updater import (
        check_update as _portable_check_update,
        get_current_version as _portable_get_version,
    )


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    size: int
    body: str
    asset_name: str = ""
    published_at: str = ""   # GitHub release 发布时间 (ISO 8601 UTC)


# ==================== 版本号 ====================
def get_local_version() -> str:
    """本地版本号"""
    if IS_PORTABLE:
        return _portable_get_version()

    try:
        from version import __version__
        return __version__
    except Exception:
        return "0.0.0"


def get_current_version() -> str:
    """兼容别名"""
    return get_local_version()


# ==================== Portable 模式 ====================
def check_update(max_retries: int = 3) -> Optional[UpdateInfo]:
    """检查更新（Portable 模式）"""
    if not IS_PORTABLE:
        logger.warning("非 Portable 模式，check_update 不支持")
        return None
    return _portable_check_update(max_retries)


# ==================== 兼容层 ====================
def check_and_update(progress_callback=None) -> Optional[UpdateInfo]:
    """兼容旧名"""
    return check_update()


def parse_version(version_str: str) -> tuple:
    """兼容旧名"""
    from core.portable_updater import _parse_semver
    return _parse_semver(version_str)


def is_newer_version(new_version: str, current_version: str) -> bool:
    """兼容旧名"""
    from core.portable_updater import _is_newer
    return _is_newer(new_version, current_version)


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
    """兼容旧名 - 已弃用，请使用 launch_kachina_update"""
    logger.warning("quit_and_update 已弃用，请使用 launch_kachina_update")


def check_for_updates(parent=None):
    """兼容旧名"""
    from core.simple_updater import show_update_dialog
    info = check_update()
    if info:
        show_update_dialog(info, parent)