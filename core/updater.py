# core/updater.py
"""
自动更新模块 - GitHub 下载
直接下载单个 exe 文件更新
"""
import os
import sys
import json
import logging
import tempfile
import subprocess
import platform
import urllib.request
import urllib.error
import re

# 当前版本号（发布新版本时修改这里）
CURRENT_VERSION = "0.1.1"

# 更新源配置（GitHub）
UPDATE_SOURCES = [
    {
        "name": "GitHub",
        "api_url": "https://api.github.com/repos/ivanhih/dd-rec/releases/latest",
        "timeout": 15,
    },
]


def get_current_version():
    """获取当前版本号"""
    return CURRENT_VERSION


def parse_version(version_str):
    """解析版本号字符串为元组，用于比较"""
    version_str = version_str.lstrip('v')
    match = re.match(r'(\d+)\.(\d+)\.(\d+)', version_str)
    if match:
        return tuple(int(x) for x in match.groups())
    return (0, 0, 0)


def is_newer_version(new_version, current_version):
    """判断新版本是否比当前版本更新"""
    return parse_version(new_version) > parse_version(current_version)


def fetch_json(url, timeout=15):
    """下载 JSON 数据，带超时"""
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.URLError as e:
        logging.warning(f"网络错误: {e}")
        return None
    except Exception as e:
        logging.warning(f"获取 {url} 失败: {e}")
        return None


def check_update():
    """
    检查更新

    返回:
        (has_update, latest_version, download_url, release_notes)
    """
    for source in UPDATE_SOURCES:
        source_name = source["name"]
        api_url = source["api_url"]
        timeout = source.get("timeout", 15)

        logging.info(f"从 {source_name} 检查更新...")

        data = fetch_json(api_url, timeout=timeout)

        if not data:
            logging.warning(f"{source_name}: 检查更新失败")
            continue

        try:
            # 解析版本号
            latest_version = data.get("tag_name", data.get("name", ""))
            if not latest_version:
                logging.warning(f"{source_name}: 未获取到版本号")
                continue

            # 获取下载链接和更新说明
            download_url = None
            release_notes = data.get("body", "") or data.get("name", "")

            # 从 assets 中查找 exe 文件
            assets = data.get("assets", [])
            for asset in assets:
                name = asset.get("name", "")
                # 匹配 .exe 文件
                if name.lower().endswith('.exe'):
                    download_url = asset.get("browser_download_url")
                    break

            if not download_url:
                logging.warning(f"{source_name}: 未找到 DD录播机.exe 附件")
                continue

            # 比较版本
            current = get_current_version()
            if is_newer_version(latest_version, current):
                logging.info(f"发现新版本: {latest_version} (当前: {current})")
                return (True, latest_version, download_url, release_notes)
            else:
                logging.info(f"当前已是最新版本: {current}")
                return (False, current, None, None)

        except Exception as e:
            logging.error(f"解析 {source_name} 数据失败: {e}")
            continue

    # 所有源都失败
    error_msg = "无法连接到更新服务器，请检查网络"
    logging.error(error_msg)
    return (False, None, None, None)


def download_file(url, dest_path):
    """
    下载文件

    返回:
        True 成功，False 失败
    """
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        with urllib.request.urlopen(req, timeout=120) as response:
            with open(dest_path, 'wb') as f:
                f.write(response.read())

        logging.info("下载完成")
        return True

    except urllib.error.URLError as e:
        logging.error(f"下载失败（网络错误）: {e}")
        return False
    except Exception as e:
        logging.error(f"下载失败: {e}")
        return False


def quit_and_update(new_exe_path):
    """
    退出程序并用新版本替换

    参数:
        new_exe_path: 下载的新版本 exe 路径
    """
    try:
        # 获取当前 exe 路径
        if getattr(sys, 'frozen', False):
            current_exe = sys.executable
        else:
            current_exe = sys.executable

        app_dir = os.path.dirname(current_exe)
        new_exe_name = "DD录播机.exe"
        target_path = os.path.join(app_dir, new_exe_name)

        # 创建批处理文件来替换 exe 并重启
        batch_content = f'''@echo off
chcp 65001 >nul
echo 正在更新...
timeout /t 2 /nobreak >nul
del /f /q "{current_exe}" 2>nul
move /y "{new_exe_path}" "{target_path}"
start "" "{target_path}"
del "%~f0"
'''
        batch_path = os.path.join(tempfile.gettempdir(), "bilirec_update.bat")
        with open(batch_path, 'w', encoding='utf-8') as f:
            f.write(batch_content)

        # 使用隐藏窗口启动批处理
        if platform.system() == "Windows":
            CREATE_NO_WINDOW = 0x08000000
            subprocess.Popen(
                ['cmd', '/c', batch_path],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        # 退出当前程序
        os._exit(0)

    except Exception as e:
        logging.error(f"准备更新失败: {e}")
        raise


# ==================== 测试代码 ====================
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print(f"当前版本: {get_current_version()}")
    print("检查更新中...")

    has_update, latest, url, notes = check_update()
    if has_update:
        print(f"发现新版本: {latest}")
        print(f"下载链接: {url}")
        print(f"更新说明: {notes[:200]}..." if notes else "")
    else:
        print("已是最新版本或检查失败")
