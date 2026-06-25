"""
更新对话框 UI（Portable + NSIS 兼容方案）

Portable 流程：
  1. 下载 zip（带进度回调）
  2. prepare_update() 写入 pending_update.json
  3. 提示用户重启应用

NSIS 流程（遗留）：
  1. 下载 Setup.exe（带进度回调）
  2. apply_update() 触发更新
"""

import os
import sys
import logging
import threading
import subprocess
from typing import Optional

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QProgressBar
)
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtGui import QFont

from core.updater import (
    UpdateInfo,
    get_local_version,
    download_update,
    IS_PORTABLE,
)

logger = logging.getLogger(__name__)


class _UpdateSignals(QObject):
    """跨线程 Signal — 子线程下载进度 → 主线程 UI 更新"""
    progress = Signal(int)
    complete = Signal(bool, str)
    failed = Signal(str)


def _restart_app():
    """重启应用（Portable 模式）"""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        parent_dir = os.path.dirname(exe_dir)
        launcher = os.path.join(parent_dir, "dd_rec.exe")
        CREATE_NO_WINDOW = 0x08000000
        if os.path.exists(launcher):
            subprocess.Popen(
                [launcher],
                cwd=parent_dir,
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            # launcher 不存在，尝试找到最新版本的 dd_rec_main.exe
            latest_exe = _find_latest_main_exe(parent_dir)
            if latest_exe:
                logger.info(f"launcher 不存在，直接启动最新主程序: {latest_exe}")
                subprocess.Popen(
                    [latest_exe],
                    cwd=parent_dir,
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                logger.error("找不到任何可用主程序，无法重启")
    else:
        # 开发态：重启自身
        subprocess.Popen(
            [sys.executable] + sys.argv,
        )


def _find_latest_main_exe(app_dir: str) -> Optional[str]:
    """扫描 app_dir 下所有 dd_rec-*/dd_rec_main.exe，返回版本最高的路径"""
    import re
    best_version = (0, 0, 0)
    best_path = None
    try:
        for entry in os.listdir(app_dir):
            if not entry.startswith("dd_rec-"):
                continue
            full = os.path.join(app_dir, entry)
            if not os.path.isdir(full):
                continue
            exe = os.path.join(full, "dd_rec_main.exe")
            if not os.path.exists(exe):
                continue
            # 解析版本号
            m = re.match(r"dd_rec-(\d+)\.(\d+)\.(\d+)", entry)
            if m:
                v = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
                if v > best_version:
                    best_version = v
                    best_path = exe
    except Exception as e:
        logger.warning(f"扫描版本目录失败: {e}")
    return best_path


def show_update_dialog(info: UpdateInfo, parent=None) -> bool:
    """显示更新对话框

    Portable 模式：
      1. 下载 zip（带进度回调）
      2. 下载完成 → prepare_update() → 提示重启

    NSIS 模式：
      1. 下载 Setup.exe（带进度回调）
      2. 下载完成 → apply_update() → 主程序退出 + 重启

    Returns: True 表示用户选择了更新；False 表示取消
    """
    dialog = QDialog(parent)
    dialog.setWindowTitle("发现新版本")
    dialog.setFixedSize(420, 280)
    dialog.setModal(True)
    dialog.setStyleSheet("QDialog { background-color: #1A1B21; }")

    layout = QVBoxLayout(dialog)
    layout.setSpacing(16)
    layout.setContentsMargins(24, 24, 24, 24)

    title = QLabel(f"发现新版本 v{info.version}")
    title.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
    title.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")
    title.setAlignment(Qt.AlignCenter)

    local_ver = get_local_version() or "未知"
    version_label = QLabel(f"当前版本: {local_ver} → 新版本: {info.version}")
    version_label.setStyleSheet("color: #94A3B8; background: transparent; border: none;")
    version_label.setAlignment(Qt.AlignCenter)

    size_mb = info.size / 1024 / 1024 if info.size else 0
    update_type = "Portable" if IS_PORTABLE else "安装版"
    size_label = QLabel(f"大小: {size_mb:.1f} MB（{update_type}）")
    size_label.setStyleSheet("color: #64748B; background: transparent; border: none;")
    size_label.setAlignment(Qt.AlignCenter)

    body_label = QLabel(info.body[:200] if info.body else "点击立即更新")
    body_label.setStyleSheet("color: #CBD5E1; background: transparent; border: none;")
    body_label.setWordWrap(True)
    body_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)

    progress_bar = QProgressBar()
    progress_bar.setStyleSheet("""
        QProgressBar {
            background-color: #252631;
            border: none;
            border-radius: 6px;
            height: 8px;
            text-align: center;
        }
        QProgressBar::chunk {
            background-color: #3B82F6;
            border-radius: 6px;
        }
    """)
    progress_bar.hide()

    btn_layout = QHBoxLayout()
    btn_layout.setSpacing(12)

    cancel_btn = QPushButton("稍后再说")
    cancel_btn.setFixedHeight(40)
    cancel_btn.setStyleSheet("""
        QPushButton {
            background-color: #252631;
            color: #94A3B8;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 500;
        }
        QPushButton:hover {
            background-color: #2D2E3A;
            color: #E2E8F0;
        }
    """)

    update_btn = QPushButton("立即更新")
    update_btn.setFixedHeight(40)
    update_btn.setStyleSheet("""
        QPushButton {
            background-color: #3B82F6;
            color: white;
            border: none;
            border-radius: 8px;
            font-size: 14px;
            font-weight: 600;
        }
        QPushButton:hover {
            background-color: #2563EB;
        }
    """)

    btn_layout.addWidget(cancel_btn)
    btn_layout.addWidget(update_btn)

    layout.addWidget(title)
    layout.addWidget(version_label)
    layout.addWidget(size_label)
    layout.addWidget(body_label)
    layout.addWidget(progress_bar)
    layout.addLayout(btn_layout)

    # Signal 桥接
    signals = _UpdateSignals()
    downloaded_path = [None]

    def _on_progress(pct: int):
        progress_bar.setValue(pct)
        update_btn.setText(f"正在下载... {pct}%")

    def _do_restart():
        """关闭弹窗 + 启动 launcher + 退出当前进程"""
        try:
            dialog.accept()
        except Exception:
            pass
        _restart_app()
        # 给 launcher 一点时间启动，再退出当前进程
        import time
        time.sleep(0.5)
        os._exit(0)

    def _on_complete(ok: bool, path: str):
        if ok:
            if IS_PORTABLE:
                # Portable: prepare_update + 倒计时自动重启
                progress_bar.setValue(100)
                update_btn.setText("更新已准备")
                update_btn.setEnabled(False)
                cancel_btn.setEnabled(True)
                try:
                    from core.portable_updater import prepare_update as _pu_prepare
                    from core.portable_updater import UpdateInfo as PortableUpdateInfo
                    update_info = PortableUpdateInfo(
                        version=info.version,
                        download_url=info.download_url,
                        size=info.size,
                        body=info.body,
                        asset_name=info.asset_name,
                    )
                    _pu_prepare(update_info, path)

                    # 5 秒倒计时自动重启（用户也可点"立即重启"提前触发）
                    countdown = [5]
                    cancel_btn.setText("立即重启")

                    def _tick():
                        n = countdown[0]
                        if n <= 0:
                            _do_restart()
                            return
                        body_label.setText(
                            f"更新已准备好！{n} 秒后自动重启..."
                        )
                        countdown[0] = n - 1
                        try:
                            from PySide6.QtCore import QTimer as _QT
                            _QT.singleShot(1000, _tick)
                        except Exception:
                            pass

                    body_label.setText("更新已准备好！5 秒后自动重启...")
                    from PySide6.QtCore import QTimer as _QT
                    _QT.singleShot(0, _tick)
                except Exception as e:
                    logger.error(f"准备更新失败: {e}")
                    _on_failed(str(e))
            else:
                # NSIS: apply_update
                progress_bar.setValue(100)
                update_btn.setText("正在安装...")
                body_label.setText("正在安装更新，请稍候...")
                try:
                    from core.updater import apply_update
                    apply_update(path)
                except Exception as e:
                    logger.error(f"应用更新失败: {e}")
                    _on_failed(str(e))
        else:
            _on_failed(path)

    def _on_failed(msg: str):
        update_btn.setEnabled(True)
        cancel_btn.setEnabled(True)
        update_btn.setText("重试")
        progress_bar.hide()
        body_label.setText(f"更新失败: {msg[:100]}")

    signals.progress.connect(_on_progress)
    signals.complete.connect(_on_complete)
    signals.failed.connect(_on_failed)

    def do_update():
        update_btn.setEnabled(False)
        cancel_btn.setEnabled(False)
        update_btn.setText("正在下载...")
        progress_bar.show()
        progress_bar.setValue(0)
        body_label.setText("正在下载更新包，请稍候...")

        def download_thread():
            try:
                path = download_update(
                    info,
                    progress_callback=lambda p: signals.progress.emit(p),
                )
                downloaded_path[0] = path
                signals.complete.emit(True, path)
            except Exception as e:
                logger.error(f"下载失败: {e}")
                signals.failed.emit(str(e))

        threading.Thread(target=download_thread, daemon=True).start()

    def on_cancel():
        # 下载完成（按钮显示"立即重启"）→ 立即触发重启，不用等倒计时
        if cancel_btn.text() == "立即重启":
            _do_restart()
        else:
            dialog.reject()

    cancel_btn.clicked.connect(on_cancel)
    update_btn.clicked.connect(do_update)

    return dialog.exec() == QDialog.Accepted


# ==================== 兼容层 ====================
def check_and_update(progress_callback=None) -> Optional[UpdateInfo]:
    """兼容旧名"""
    from core.updater import check_update
    return check_update()


def get_local_version() -> Optional[str]:
    """兼容旧名 - 代理到 core.updater.get_local_version（处理 portable/NSIS 分支）"""
    from core.updater import get_local_version as _impl
    return _impl()


def set_local_version(version: str):
    """兼容旧名"""
    logger.info(f"set_local_version({version}) 已弃用")


def parse_version_from_filename(filename: str) -> Optional[str]:
    """兼容旧名"""
    import re
    m = re.search(r'[_\-]v?(\d+\.\d+\.\d+)', filename, re.IGNORECASE)
    return m.group(1) if m else None


class SimpleUpdater:
    """兼容类"""
    def __init__(self, *args, **kwargs):
        pass

    def check_update(self, max_retries: int = 3):
        from core.updater import check_update
        return check_update(max_retries=max_retries)

    def download_update(self, info, progress_callback=None):
        from core.updater import download_update
        try:
            return download_update(info, progress_callback=progress_callback)
        except Exception:
            return None


_LAST_DOWNLOADED_SETUP: Optional[str] = None


def get_last_downloaded_setup() -> Optional[str]:
    """兼容旧名"""
    return _LAST_DOWNLOADED_SETUP


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    info = check_and_update()
    if info:
        print(f"新版本: v{info.version}")
        print(f"大小: {info.size / 1024 / 1024:.1f} MB")
        print(f"下载 URL: {info.download_url}")
    else:
        print("已是最新版本或尚未发布")
