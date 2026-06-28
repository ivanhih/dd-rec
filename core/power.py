"""
电源/开机自启管理（Windows）

提供:
  - set_auto_start(enabled, exe_path) -> 增删 Run 注册表项
  - get_auto_start() -> 当前是否启用了开机自启
  - PowerKeepAlive() -> 持续阻止系统休眠/熄屏（守护线程）
"""

import sys
import threading
import logging

logger = logging.getLogger(__name__)


# ==================== 开机自启 ====================

def _run_key_path() -> str:
    """注册表 Run 路径（HKCU）"""
    return r"Software\Microsoft\Windows\CurrentVersion\Run"


def _run_value_name() -> str:
    """注册表值名（程序唯一标识）"""
    return "dd_rec_launcher"


def set_auto_start(enabled: bool, exe_path: str = None) -> bool:
    """增删开机自启注册表项。

    Args:
        enabled: True = 添加，False = 删除
        exe_path: 启动器 exe 完整路径；不传则用 sys.executable

    Returns:
        是否设置成功（非 Windows 平台返回 False）
    """
    if sys.platform != "win32":
        return False
    if exe_path is None:
        exe_path = sys.executable
    try:
        import winreg
        if enabled:
            # 注册表值用引号包住路径（防止路径含空格）
            cmd = f'"{exe_path}"'
            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, _run_key_path(), 0, winreg.KEY_SET_VALUE
            ) as key:
                winreg.SetValueEx(key, _run_value_name(), 0, winreg.REG_SZ, cmd)
            logger.info(f"开机自启已启用: {cmd}")
        else:
            try:
                with winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER, _run_key_path(), 0, winreg.KEY_SET_VALUE
                ) as key:
                    winreg.DeleteValue(key, _run_value_name())
                logger.info("开机自启已禁用")
            except FileNotFoundError:
                # 本来就没启用，幂等
                pass
        return True
    except Exception as e:
        logger.error(f"设置开机自启失败: {e}")
        return False


def get_auto_start() -> bool:
    """检查当前是否已启用开机自启（注册表 Run 项）"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, _run_key_path(), 0, winreg.KEY_READ
        ) as key:
            value, _ = winreg.QueryValueEx(key, _run_value_name())
            return bool(value)
    except (FileNotFoundError, OSError):
        return False


# ==================== 防止休眠 ====================

# Windows API 常量（kernel32.SetThreadExecutionState）
ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002


class PowerKeepAlive:
    """阻止系统休眠/熄屏。

    用法:
        keepalive = PowerKeepAlive()
        keepalive.start()    # 启动守护线程，每 30s 调一次 SetThreadExecutionState
        ...
        keepalive.stop()     # 停止 + 恢复默认电源策略

    也可以作为上下文管理器:
        with PowerKeepAlive():
            ... 录播中 ...
    """

    def __init__(self):
        self._thread = None
        self._stop_event = threading.Event()

    def start(self) -> None:
        """启动守护线程"""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="PowerKeepAlive"
        )
        self._thread.start()
        logger.info("防止休眠已启用")

    def stop(self) -> None:
        """停止守护线程 + 恢复默认电源策略"""
        if not self._thread:
            return
        self._stop_event.set()
        self._thread.join(timeout=2)
        self._thread = None
        # 恢复默认电源策略（清掉 ES_SYSTEM_REQUIRED 标志）
        if sys.platform == "win32":
            try:
                import ctypes
                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except Exception as e:
                logger.warning(f"恢复电源策略失败: {e}")
        logger.info("防止休眠已禁用")

    def _run(self) -> None:
        """守护线程主循环：每 30 秒刷新一次 SetThreadExecutionState"""
        if sys.platform != "win32":
            return
        try:
            import ctypes
        except ImportError:
            logger.warning("ctypes 不可用，跳过 SetThreadExecutionState")
            return

        while not self._stop_event.is_set():
            try:
                ctypes.windll.kernel32.SetThreadExecutionState(
                    ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED
                )
            except Exception as e:
                logger.warning(f"SetThreadExecutionState 失败: {e}")
            # 30 秒刷新一次（Windows 默认 idle 阈值远大于此，安全）
            self._stop_event.wait(30)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *args):
        self.stop()