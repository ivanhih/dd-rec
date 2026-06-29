"""
Portable 更新模块

流程（kachina-installer 模式）：
  1. check_update()           → 调 GitHub API 检查最新 release
  2. download_update(info, cb) → 下载 7z 到 temp/
  3. 用户确认 → launch_kachina_update() spawn DDRec.update.exe → 主程序退出
  4. kachina update.exe: 关闭主程序 → HDiffPatch → 替换 launcher / dd_rec-{ver}/ → 启动新版

不再走 pending_update.json 流程：
  - launcher 已被简化为只启动主程序（不再负责解包）
  - kachina update.exe 自身会处理 launcher 替换（因为 launcher 已退出，可写）
"""

import os
import sys
import json
import logging
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# GitHub 仓库配置
GITHUB_REPO = "ivanhih/dd-rec"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
GITHUB_RELEASES = f"https://github.com/{GITHUB_REPO}/releases"

# 文件名约定
VERSION_INI = "version.ini"
KACHINA_UPDATE_EXE = "dd_rec.update.exe"


@dataclass
class UpdateInfo:
    version: str
    download_url: str
    size: int
    body: str
    asset_name: str = ""
    published_at: str = ""   # GitHub release 发布时间 (ISO 8601 UTC),mirror 酱路径可能为空


def get_app_dir() -> str:
    """获取应用根目录（主程序所在目录）

    平坦化后:dd_rec_main.exe 直接在 portable 根目录,所以 sys.executable 的父目录
    就是 app_dir。Launcher 也是 onefile 模式,逻辑一致。

    兼容老版本结构(用 dd_rec-{ver}/ 子目录):如果父目录有 version.ini 就在父目录。
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        # 平坦化主路径:version.ini 在主程序同目录
        if os.path.exists(os.path.join(exe_dir, VERSION_INI)):
            return exe_dir
        # 兼容老结构(主程序在 dd_rec-{ver}/ 子目录)
        parent_dir = os.path.dirname(exe_dir)
        if os.path.exists(os.path.join(parent_dir, VERSION_INI)):
            return parent_dir
        # 兜底:返回自己,让上层报错更明确
        return exe_dir
    # 开发态:项目根目录
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
    """检查更新(GitHub 优先,失败/无版本回落 mirror 酱)

    优先级:
      1) GitHub API(主通道,默认行为,用户最信任)
      2) Mirror 酱 API(fallback,仅在 GitHub 完全失败时顶上)

    返回:
      UpdateInfo — 有可用更新
      None       — 已是最新 / 两个源都失败 / 没匹配资产
    """
    local = get_current_version()
    logger.info(f"当前版本: {local}")

    # 1) GitHub(主通道)
    github_info = _check_update_github_only(local, max_retries)
    if github_info is not None:
        return github_info

    # 2) Mirror 酱(fallback,GitHub 失败时)
    mirror_info = _try_mirror_chyan(local)
    if mirror_info is None:
        return None
    if mirror_info.raw_code != 0:
        logger.info(f"mirror 酱 code={mirror_info.raw_code},跳过")
        return None

    remote_tag = mirror_info.version
    if not remote_tag or not _is_newer(remote_tag, local):
        logger.info("mirror 酱:当前已是最新版本")
        return None

    # 下载 URL 仍拼 GitHub 直链(mirror 酱 url 带时效,kachina 接不了 — 留给未来重构)
    github_url = _build_github_release_url(remote_tag)
    if not github_url:
        logger.warning("mirror 酱:有更新但拼不出 GitHub 直链,跳过")
        return None

    logger.info(f"mirror 酱 fallback:最新 v{remote_tag} (GitHub 直链下载)")
    return UpdateInfo(
        version=remote_tag,
        download_url=github_url,
        size=0,  # mirror 酱不返回 size,这里无法预知
        body=mirror_info.release_note,
        asset_name="(mirror-chyan-via-github)",
        published_at="",  # mirror 酱 API 当前未返回此字段
    )


def _try_mirror_chyan(local: str):
    """调 mirror 酱。None=不可用/未启用/异常,本次跳过。"""
    try:
        from core.config import get_global_setting
        if not get_global_setting("mirror_chyan_enabled"):
            return None
        from core.mirror_chyan import check_mirror_chyan
        cdk = (get_global_setting("mirror_chyan_cdk") or "").strip()
        return check_mirror_chyan(local, cdk)
    except Exception as e:
        logger.warning(f"mirror 酱入口异常: {e}")
        return None


def _build_github_release_url(version: str) -> Optional[str]:
    """拼 GitHub release 直链(portable.7z,主程序优先找的资产)

    实际资产名可能是 -portable.7z / -portable.zip,这里只兜底给一个
    最常见的 -portable.7z 模板。如果 release 用别的命名,这里会 404,
    kachina 安装器会自己处理(它用的是 kachina.config.json 的 source.uri)。
    """
    if not version:
        return None
    return f"https://github.com/{GITHUB_REPO}/releases/download/v{version}/dd_rec-{version}-portable.7z"


def _check_update_github_only(local: str, max_retries: int) -> Optional[UpdateInfo]:
    """原 check_update() 逻辑(改名,逻辑零修改)。GitHub API 不可达 = 返回 None。"""
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

            # 查找 portable 包（kachina 模式）
            # 优先级（按文件类型）:
            #   1) -portable.7z  (新标准，主推)
            #   2) -portable.zip (过渡期兼容老用户)
            #
            # 显式不接受:
            #   - dd_rec-{ver}.zip / bilirec-{ver}.zip (旧 launcher 流，已被新方案替代)
            #   - -patch-from-X.Y.Z.zip (增量补丁,kachina update.exe 启动后自己会
            #     检测并下载,不需要在这里选)
            download_url = None
            size = 0
            asset_name = ""
            for asset in data.get("assets", []):
                name = asset.get("name", "").lower()
                if name.endswith("-portable.7z"):
                    download_url = asset.get("browser_download_url")
                    size = asset.get("size", 0)
                    asset_name = asset.get("name", "")
                    break  # 7z 优先级最高
            if not download_url:
                for asset in data.get("assets", []):
                    name = asset.get("name", "").lower()
                    if name.endswith("-portable.zip"):
                        download_url = asset.get("browser_download_url")
                        size = asset.get("size", 0)
                        asset_name = asset.get("name", "")
                        break

            if not download_url:
                logger.warning("未找到 portable.7z / -portable.zip 资源")
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
                published_at=str(data.get("published_at", "") or ""),
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


# ==================== 启动 kachina update.exe ====================
def launch_kachina_update(app_dir: str) -> None:
    """主程序退出,让 kachina update.exe 接管剩余更新流程。

    kachina update.exe (DDRec.update.exe) 是带 UI 的独立 exe,会:
      1. 弹"DD录播机 安装程序"窗口(类似 BetterGI 安装器)
      2. 读 kachina.config.json 的 source(指向 GitHub release Install.exe)
      3. 用户点"更新"按钮 → 下载远端 Install.exe
      4. 关闭 dd_rec_main.exe(占用检测) + dd_rec.exe(launcher)
      5. HDiffPatch 增量替换 dd_rec-{version}/ + 替换 launcher.exe
      6. 启动新版本

    调用本函数后必须 os._exit(0) 立即退出主程序,避免双进程冲突。

    Args:
        app_dir: portable 根目录(即 dd-rec/ 那个目录)

    Raises:
        FileNotFoundError: 找不到 update.exe
    """
    update_exe = os.path.join(app_dir, KACHINA_UPDATE_EXE)
    if not os.path.exists(update_exe):
        raise FileNotFoundError(
            f"找不到 kachina update.exe: {update_exe}\n"
            "请重新下载完整 7z 包并解压覆盖。\n"
            "(绿色版的自更新依赖此文件,kachina update.exe 与主程序必须配套)"
        )

    logger.info(f"启动 kachina update.exe: {update_exe}")
    # 用 ShellExecuteExW + runas verb 启动 update.exe —— 让它自己请求 UAC 提权
    # (Windows 行为:非 elevated 进程 spawn 普通 EXE 会被 Windows 自动拦截弹 UAC,
    #  用 runas 让 spawn 那一刻就显式提权,只弹一次 UAC 且用户体验更明确)
    import ctypes
    from ctypes import wintypes

    SEE_MASK_NOASYNC = 0x00000100
    SW_SHOWNORMAL = 1

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", ctypes.c_ulong),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", ctypes.c_void_p),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", ctypes.c_void_p),
            ("dwHotKey", ctypes.c_ulong),
            ("hIcon", ctypes.c_void_p),
            ("hProcess", ctypes.c_void_p),
        ]
    sei = SHELLEXECUTEINFOW()
    sei.cbSize = ctypes.sizeof(SHELLEXECUTEINFOW)
    sei.fMask = SEE_MASK_NOASYNC
    sei.hwnd = None
    sei.lpVerb = "runas"          # ← 关键:runas 让 Windows 立刻弹 UAC 提权
    sei.lpFile = update_exe
    sei.lpParameters = None
    sei.lpDirectory = app_dir     # ← 跟 cwd=app_dir 效果一样,让 update.exe 找到 portable 根
    sei.nShow = SW_SHOWNORMAL

    logger.info("通过 ShellExecuteExW (runas) 启动 update.exe...")
    if not ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(sei)):
        # 提权失败(用户取消 UAC 或其他原因)
        err = ctypes.GetLastError()
        raise OSError(f"ShellExecuteExW 失败,Windows error code: {err}")

    # 给 update.exe 一点启动时间,再退出主程序
    import time
    time.sleep(0.5)
    os._exit(0)


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
    logger.warning("quit_and_update 已弃用，请使用 launch_kachina_update")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    info = check_update()
    if info:
        print(f"新版本: v{info.version}")
        print(f"大小: {info.size / 1024 / 1024:.1f} MB")
        print(f"下载 URL: {info.download_url}")
    else:
        print("已是最新版本或尚未发布")