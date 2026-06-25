"""
Portable 更新模块（Portable 方案）

功能：
  1. check_update()           → 调 GitHub API 检查最新 release
  2. download_update(info, cb) → 下载 dd_rec-{version}.zip 到 temp/
  3. prepare_update(info, path) → 写入 pending_update.json，等待重启
  4. apply_pending_update()    → 由 launcher.py 在启动时调用，解压并更新

流程：
  主程序检测更新 → 用户确认 → 下载 zip → 写 pending → 提示重启
  用户重启 → launcher 检测 pending → 解压 → 更新 version.ini → 启动新版本
"""

import os
import sys
import json
import logging
import tempfile
import urllib.request
import urllib.error
import zipfile
from dataclasses import dataclass
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# GitHub 仓库配置
GITHUB_REPO = "ivanhih/dd-rec"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES = f"https://github.com/{GITHUB_REPO}/releases"

# 文件名约定
VERSION_INI = "version.ini"
PENDING_UPDATE_FILE = "pending_update.json"


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    size: int
    body: str
    asset_name: str = ""


def get_app_dir() -> str:
    """获取应用根目录（主程序所在目录）"""
    if getattr(sys, "frozen", False):
        # 主程序在 dd_rec-{version}/dd_rec_main.exe
        # 父目录是 dd_rec-{version}/
        # 再父目录是 dd-rec/ 根目录
        exe_dir = os.path.dirname(sys.executable)
        return os.path.dirname(exe_dir)
    # 开发态：项目根目录
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_current_version() -> str:
    """获取当前版本号（从 version.ini 读取）"""
    app_dir = get_app_dir()
    version_ini = os.path.join(app_dir, VERSION_INI)

    try:
        if os.path.exists(version_ini):
            with open(version_ini, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("version="):
                        return line.split("=", 1)[1].strip()
    except Exception as e:
        logger.error(f"读取 version.ini 失败: {e}")

    # fallback: 从 version.py 读取
    try:
        from version import __version__
        return __version__
    except Exception:
        return "0.0.0"


def _parse_semver(v: str) -> tuple:
    """'1.0.2' → (1, 0, 2)，'v1.0.2' 也行"""
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


def _is_newer(remote: str, local: str) -> bool:
    return _parse_semver(remote) > _parse_semver(local)


# ==================== 检查更新 ====================
def check_update(max_retries: int = 3) -> Optional[UpdateInfo]:
    """调 GitHub API 检查更新。返回 None 表示无更新或检查失败。"""
    local = get_current_version()
    logger.info(f"当前版本: {local}")

    for attempt in range(max_retries):
        try:
            req = urllib.request.Request(
                GITHUB_API,
                headers={"User-Agent": "bilirec-updater/1.0"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            remote_tag = (data.get("tag_name") or "").lstrip("v")
            if not remote_tag:
                logger.info("Release 没有 tag_name，跳过")
                return None

            # 查找 portable zip 包（命名约定：dd_rec-{version}.zip）
            download_url = None
            size = 0
            asset_name = ""
            for asset in data.get("assets", []):
                name = asset.get("name", "")
                # 匹配 dd_rec-1.0.2.zip 或 dd_rec-1.0.2-portable.zip
                # 兼容历史命名：bilirec-1.0.2.zip（旧 build.py 用的名字）
                if name.endswith(".zip") and (
                    "dd_rec" in name.lower() or "bilirec" in name.lower()
                ):
                    download_url = asset.get("browser_download_url")
                    size = asset.get("size", 0)
                    asset_name = name
                    break

            if not download_url:
                logger.warning("未找到 dd_rec-*.zip 资源")
                return None

            logger.info(f"最新版本: {remote_tag}（资产: {asset_name}）")

            if not _is_newer(remote_tag, local):
                logger.info("当前已是最新版本")
                return None

            return UpdateInfo(
                version=remote_tag,
                download_url=download_url,
                size=size,
                body=data.get("body", "") or "",
                asset_name=asset_name,
            )

        except urllib.error.HTTPError as e:
            if e.code == 404:
                logger.info("尚未发布 Release")
                return None
            logger.warning(f"检查更新失败: HTTP {e.code} (尝试 {attempt + 1}/{max_retries})")
        except Exception as e:
            logger.warning(f"检查更新失败: {e} (尝试 {attempt + 1}/{max_retries})")

        if attempt < max_retries - 1:
            import time
            time.sleep(2)

    return None


# ==================== 下载 ====================
ProgressCallback = Optional[Callable[[int], None]]


def download_update(info: UpdateInfo, progress_callback: ProgressCallback = None) -> str:
    """下载新版本 zip 到 temp/ 目录。

    Args:
        info: check_update() 返回的 UpdateInfo
        progress_callback: 进度回调函数，参数为 0~100 的整数

    Returns:
        下载到本地的 zip 路径

    Raises:
        Exception: 下载失败时抛出
    """
    app_dir = get_app_dir()
    temp_dir = os.path.join(app_dir, "temp")
    os.makedirs(temp_dir, exist_ok=True)

    zip_path = os.path.join(temp_dir, info.asset_name)

    # 删旧下载
    if os.path.exists(zip_path):
        try:
            os.remove(zip_path)
        except OSError:
            pass

    logger.info(f"下载更新: {info.download_url}")
    req = urllib.request.Request(
        info.download_url,
        headers={"User-Agent": "bilirec-updater/1.0"},
    )

    with urllib.request.urlopen(req, timeout=600) as resp:
        total = int(resp.headers.get("Content-Length", 0) or 0)
        downloaded = 0
        chunk_size = 64 * 1024
        with open(zip_path, "wb") as f:
            while True:
                chunk = resp.read(chunk_size)
                if not chunk:
                    break
                f.write(chunk)
                downloaded += len(chunk)
                if progress_callback and total > 0:
                    try:
                        progress_callback(int(downloaded * 100 / total))
                    except Exception:
                        pass

    size_mb = os.path.getsize(zip_path) / 1024 / 1024
    logger.info(f"下载完成: {zip_path} ({size_mb:.1f} MB)")
    return zip_path


# ==================== 准备更新（等待重启） ====================
def prepare_update(info: UpdateInfo, zip_path: str) -> bool:
    """写入 pending_update.json，等待用户重启后由 launcher 应用更新。

    Args:
        info: UpdateInfo 对象
        zip_path: 下载的 zip 文件路径

    Returns:
        True 成功，False 失败
    """
    app_dir = get_app_dir()
    pending_path = os.path.join(app_dir, PENDING_UPDATE_FILE)

    try:
        pending_data = {
            "version": info.version,
            "zip_path": zip_path,
            "asset_name": info.asset_name,
            "size": info.size,
        }
        with open(pending_path, "w", encoding="utf-8") as f:
            json.dump(pending_data, f, ensure_ascii=False, indent=2)

        logger.info(f"已写入待更新标记: {pending_path}")
        return True

    except Exception as e:
        logger.error(f"写入 pending_update.json 失败: {e}")
        return False


def has_pending_update() -> bool:
    """检查是否有待更新的标记"""
    app_dir = get_app_dir()
    pending_path = os.path.join(app_dir, PENDING_UPDATE_FILE)
    return os.path.exists(pending_path)


def get_pending_update_info() -> Optional[dict]:
    """获取待更新信息"""
    app_dir = get_app_dir()
    pending_path = os.path.join(app_dir, PENDING_UPDATE_FILE)
    try:
        if os.path.exists(pending_path):
            with open(pending_path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"读取 pending_update.json 失败: {e}")
    return None


# ==================== 验证 zip 完整性 ====================
def verify_zip(zip_path: str) -> bool:
    """验证 zip 文件完整性"""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            # 检查是否有文件
            if zf.namelist():
                # 尝试验证 CRC（可选，快速检查）
                bad_file = zf.testzip()
                if bad_file:
                    logger.error(f"ZIP 损坏: {bad_file}")
                    return False
                return True
    except Exception as e:
        logger.error(f"验证 ZIP 失败: {e}")
    return False


# ==================== 兼容层 ====================
def check_and_update(progress_callback=None) -> Optional[UpdateInfo]:
    """兼容旧名"""
    return check_update()


def parse_version(version_str: str) -> tuple:
    """兼容旧名"""
    return _parse_semver(version_str)


def is_newer_version(new_version: str, current_version: str) -> bool:
    """兼容旧名"""
    return _is_newer(new_version, current_version)


def quit_and_update(new_exe_path: str) -> None:
    """兼容旧名 - 已弃用"""
    logger.warning("quit_and_update 已弃用，请使用 prepare_update + 重启")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    info = check_update()
    if info:
        print(f"新版本: v{info.version}")
        print(f"大小: {info.size / 1024 / 1024:.1f} MB")
        print(f"下载 URL: {info.download_url}")
    else:
        print("已是最新版本或尚未发布")
