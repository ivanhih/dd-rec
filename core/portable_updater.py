"""
Portable 更新模块

流程（kachina-installer 模式）：
  1. check_update()           → 调 GitHub API 检查最新 release
  2. download_update(info, cb) → 下载 7z 到 temp/
  3. 用户确认 → launch_kachina_update() spawn DDRec.update.exe → 主程序退出
  4. kachina update.exe: 关闭 dd_rec.exe → HDiffPatch/替换文件 → 启动新版

主程序只有 dd_rec.exe 一个入口；更新由独立的 dd_rec.update.exe 接管。
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
    published_at: str = ""   # GitHub release 发布时间 (ISO 8601 UTC)


def get_app_dir() -> str:
    """获取应用根目录（主程序所在目录）

    单入口结构中 dd_rec.exe 直接位于 portable 根目录，所以 sys.executable 的父目录
    就是 app_dir。

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
    """通过 GitHub API 检查更新；下载阶段仍可选择 GitHub 或 CNB。"""
    local = get_current_version()
    logger.info(f"当前版本: {local}")
    return _check_update_github_only(local, max_retries)


def _check_update_github_only(local: str, max_retries: int) -> Optional[UpdateInfo]:
    """原 check_update() 逻辑(改名,逻辑零修改)。GitHub API 不可达 = 返回 None。"""
    for attempt in range(max_retries):
        try:
            from core.http_ssl import urlopen as _ssl_urlopen
            req = urllib.request.Request(
                GITHUB_API,
                headers={"User-Agent": "bilirec-updater/1.0"},
            )
            with _ssl_urlopen(req, timeout=30) as resp:
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
# kachina.config.json 的 source[] 里实际配置的 id 白名单,用于过滤用户传入的 source_id。
# 任何不在此集合内的值一律拒绝,避免把字符串塞进 ShellExecuteExW 时被 Windows 解析成别的。
_VALID_KACHINA_SOURCE_IDS = ("github", "cnb")


def launch_kachina_update(app_dir: str, source_id: Optional[str] = None) -> None:
    """主程序退出,让 kachina update.exe 接管剩余更新流程。

    kachina update.exe (DDRec.update.exe) 是带 UI 的独立 exe,会:
      1. 弹"DD录播机 安装程序"窗口(类似 BetterGI 安装器)
      2. 读 kachina.config.json 的 source(指向 GitHub release Install.exe)
      3. 用户点"更新"按钮 → 下载远端 Install.exe
      4. 关闭当前 dd_rec.exe
      5. HDiffPatch 增量替换程序文件
      6. 启动新版 dd_rec.exe

    调用本函数后必须 os._exit(0) 立即退出主程序,避免双进程冲突。

    Args:
        app_dir: portable 根目录(即 dd-rec/ 那个目录)
        source_id: kachina source 的 id(github / cnb)。None 表示让 kachina
            在自己 UI 里让用户选;否则会以 `--source <id>` 形式传过去,kachina
            跳过选择直接用对应通道下载(参照 BetterGI 的写法)。

    Raises:
        FileNotFoundError: 找不到 update.exe
        ValueError: source_id 给了一个非白名单的值
    """
    # ---- 校验 source_id(白名单,防止拼参数注入)----
    if source_id is not None:
        source_id = str(source_id).strip()
        if source_id not in _VALID_KACHINA_SOURCE_IDS:
            raise ValueError(
                f"非法的 update source_id: {source_id!r} "
                f"(仅允许 {list(_VALID_KACHINA_SOURCE_IDS)})"
            )

    update_exe = os.path.join(app_dir, KACHINA_UPDATE_EXE)
    if not os.path.exists(update_exe):
        raise FileNotFoundError(
            f"找不到 kachina update.exe: {update_exe}\n"
            "请重新下载完整 7z 包并解压覆盖。\n"
            "(绿色版的自更新依赖此文件,kachina update.exe 与主程序必须配套)"
        )

    # ---- 拼命令行参数 ----
    # kachina CLI 形式: -I(--self-update) + --source <id>(可选)
    # 没传 source_id 就让 kachina 自己弹窗让用户选(传 None)
    parameters = f"-I --source {source_id}" if source_id else None

    logger.info(
        f"启动 kachina update.exe: {update_exe}"
        + (f" (args={parameters!r})" if parameters else "")
    )
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
    sei.lpParameters = parameters  # ← None=让 kachina 自己弹窗选;否则强制指定
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