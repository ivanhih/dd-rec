# main.py
import sys
import os
import re
import platform
import subprocess
import datetime
import logging
import threading as _threading
import traceback as _traceback
import time
from logging.handlers import RotatingFileHandler
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QScrollArea, QLineEdit, QPushButton, QLabel, QGridLayout,
    QFrame, QToolButton, QGraphicsDropShadowEffect, QSizePolicy,
    QGraphicsOpacityEffect, QMenu, QStackedWidget, QSystemTrayIcon,
    QDialog, QMessageBox
)
from PySide6.QtCore import (
    QTimer, Qt, QThread, QObject, QPropertyAnimation, QEasingCurve,
    QPoint, QRect, QEvent, Signal, QUrl,
)
from PySide6.QtGui import QIcon, QFont, QColor, QActionGroup, QPixmap, QPainter, QPen, QPainterPath, QAction, QDesktopServices

from core.config import load_app_data, save_app_data, VIDEO_SAVE_DIR, get_room_config, get_global_setting, get_room_setting, get_effective_format, ensure_default_config, APP_DIR
from core.updater import get_local_version, check_update as _check_update, IS_PORTABLE
from core.simple_updater import show_update_dialog
from core.power import PowerKeepAlive, set_auto_start
from version import __version__
from core.bili_api import get_bili_info
from core.recorder import BiliRecorder
from core.utils import render_path_template
from ui.room_card import RoomCard, HoverLabel, _HoverToolButton, _HoverPushButton
from ui.settings_dialog import RoomSettingsDialog, GlobalSettingsOldStyleReplicaPage, AddChannelDialog, open_room_settings_overlay, open_room_settings_overlay
from ui.log_viewer_dialog import open_log_viewer_overlay
from ui.splash import StartupOverlay
from ui.slide_stack import SlideStackedWidget
from ui.theme import theme_manager, enable_surface


def _resource_path(relative_path: str) -> str:
    """返回开发环境或 PyInstaller onedir 中的资源绝对路径。"""
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base_dir, relative_path)


def _load_app_icon() -> QIcon:
    """加载正式软件图标；资源缺失时保留一个简单的 DD 回退图标。"""
    icon = QIcon(_resource_path(os.path.join("assets", "icon.ico")))
    if not icon.isNull():
        return icon

    pix = QPixmap(64, 64)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor("#3B82F6"))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(4, 4, 56, 56)
    painter.setPen(QColor("white"))
    painter.setFont(QFont("Microsoft YaHei UI", 26, QFont.Bold))
    painter.drawText(pix.rect(), Qt.AlignCenter, "DD")
    painter.end()
    return QIcon(pix)



def _set_windows_app_user_model_id() -> None:
    """让任务栏、快捷方式和运行中的窗口使用稳定的同一应用身份。"""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ivanhih.ddrec")
    except Exception:
        logging.exception("设置 Windows AppUserModelID 失败")


# ==================== Path preview (mirrors recorder._build_save_path) ====================
def _preview_save_path(room_id: str, uname: str, title: str, now_dt) -> str:
    """跟 core/recorder.py 里的 _build_save_path **完全同步**的路径预览。

    用于"打开目录"按钮在还没录过任何文件时，给出跟模板渲染一致的目录。
    用同样的 path_template / save_dir / custom_dir / get_effective_format 计算。
    """
    fmt = get_effective_format(room_id)
    global_dir = get_global_setting("save_dir") or VIDEO_SAVE_DIR
    room_cfg = get_room_config(room_id)
    custom_dir = room_cfg.get("custom_dir", "").strip()

    template_str = get_global_setting("path_template") or (
        "{{ download_dir }}/{{ channel }}_{{ ctime | date: '%Y%m%d_%H%M%S' }}.{{ format }}"
    )

    # 清理文件名中的非法字符（保留盘符冒号不处理）
    invalid_chars = r'[\\/:*?"<>|]' if platform.system() == "Windows" else r'[/]'
    safe_channel = re.sub(invalid_chars, '_', str(room_id))
    safe_uname = re.sub(invalid_chars, '_', str(uname))
    safe_title = re.sub(invalid_chars, '_', str(title))

    try:
        rendered = render_path_template(
            template_str,
            out_dir=custom_dir,
            download_dir=global_dir,
            platform="bilibili",
            channel=safe_channel,
            user_name=safe_uname,
            title=safe_title,
            ctime=now_dt,
            format=fmt,
        )
        return os.path.normpath(rendered.strip())
    except Exception as e:
        logging.error(f"模板解析失败，回退到默认路径: {e}")
        now_str = now_dt.strftime("%Y%m%d_%H%M%S")
        fallback_dir = custom_dir if custom_dir else global_dir
        return os.path.join(fallback_dir, f"room_{room_id}_{now_str}.{fmt}")


# 日志配置 —— 写到文件 + stdout 双输出，方便关窗后回溯崩溃
# 放到 userdata/log/ —— 跟 portable 根/录播文件/ 独立,只放主程序日志
LOG_DIR = os.path.join(APP_DIR, "log")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, "bilirec.log")
CRASH_FILE = os.path.join(LOG_DIR, "crash.log")

_stdout_handler = logging.StreamHandler(stream=sys.stdout)
if hasattr(_stdout_handler.stream, 'reconfigure'):
    _stdout_handler.stream.reconfigure(encoding='utf-8')

class _FlushRotatingFileHandler(RotatingFileHandler):
    """硬崩前尽量把日志刷到盘，避免 bilirec.log 停在旧时间戳。"""

    def emit(self, record):
        super().emit(record)
        try:
            self.flush()
        except Exception:
            pass


_file_handler = _FlushRotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=5,             # 保留 5 个备份
    encoding="utf-8",
)

# 某些导入路径可能已初始化 logging；强制替换 root handlers，保证写到 userdata/log
_root = logging.getLogger()
for _h in list(_root.handlers):
    try:
        _root.removeHandler(_h)
        _h.close()
    except Exception:
        pass
_root.setLevel(logging.INFO)
_fmt = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
_stdout_handler.setFormatter(_fmt)
_file_handler.setFormatter(_fmt)
_root.addHandler(_stdout_handler)
_root.addHandler(_file_handler)

# 硬崩溃诊断：segfault / abort 等写入 crash.log（Python 异常仍走 bilirec.log）
try:
    import faulthandler
    _crash_fp = open(CRASH_FILE, "a", encoding="utf-8")
    faulthandler.enable(file=_crash_fp, all_threads=True)
    # 再镜像一份到 stderr，方便终端直接看到
    try:
        faulthandler.enable(all_threads=True)
    except Exception:
        pass
    logging.info(f"faulthandler enabled → {CRASH_FILE}")
    # 启动探针：确认当前进程确实写到这个 log 目录
    logging.info(f"logging to {LOG_FILE}")
    for h in logging.getLogger().handlers:
        try:
            h.flush()
        except Exception:
            pass
except Exception:
    logging.exception("启用 faulthandler 失败")

# 多房间同时拉流/下封面/弹幕时会并发建 SSL；Windows OpenSSL 下
# create_default_context 不是线程安全路径，主线程先预热共享上下文。
try:
    from core.http_ssl import warm_up as _ssl_warm_up
    _ssl_warm_up()
except Exception:
    logging.exception("SSL context warm-up failed")


# —— 兜底 1：主线程未捕获异常（Qt 事件循环内 / QThread run() 顶层）——
def _bilirec_excepthook(exc_type, exc_value, exc_tb):
    msg = "".join(_traceback.format_exception(exc_type, exc_value, exc_tb))
    logging.error(f"[main] 未捕获异常:\n{msg}")
    sys.__excepthook__(exc_type, exc_value, exc_tb)

sys.excepthook = _bilirec_excepthook


# —— 兜底 2：子线程 (QThread / daemon) 未捕获异常 ——
# Python 3.8+ 把子线程异常投递到 threading.excepthook 而不是 sys.excepthook，
# 不接住的话会"静默崩 + 进程存活但房间监控死掉"，看起来就是"软件自己关了"。
def _thread_excepthook(args):
    msg = "".join(_traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback))
    logging.error(
        f"[thread {args.thread.name}] 未捕获异常:\n{msg}"
    )
    sys.__excepthook__(args.exc_type, args.exc_value, args.exc_traceback)

_threading.excepthook = _thread_excepthook


class Notification(QWidget):
    """通知组件"""
    dismissed = Signal(object)
    STYLE_MAP = {
        "success": {"icon": "✓"},
        "info": {"icon": "i"},
        "error": {"icon": "!"},
    }

    def __init__(self, message, title="提示", level="info", parent=None, duration_ms=3200, merge_key=None):
        super().__init__(parent)
        self.base_message = message
        self.base_title = title
        self.level = level if level in self.STYLE_MAP else "info"
        self.duration_ms = duration_ms
        self.merge_key = merge_key
        self.count = 1
        self._closing = False
        self.setup_ui()
        self._setup_animation()
        self._setup_timer()
        self._refresh_content()

    def setup_ui(self):
        colors = self.STYLE_MAP[self.level]
        self.setObjectName("notificationCard")
        self.setProperty("level", self.level)
        self.setMinimumWidth(340)
        self.setMaximumWidth(340)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 12, 12)
        layout.setSpacing(10)

        accent = QFrame()
        accent.setObjectName("notificationAccent")
        accent.setFixedWidth(4)

        icon_label = QLabel(colors["icon"])
        icon_label.setObjectName("notificationIcon")
        icon_label.setFixedSize(28, 28)
        icon_label.setAlignment(Qt.AlignCenter)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        text_layout.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel()
        self.title_label.setObjectName("notificationTitle")
        self.title_label.setWordWrap(True)

        self.count_label = QLabel()
        self.count_label.setObjectName("notificationBadge")
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setMinimumWidth(28)
        self.count_label.hide()

        self.message_label = QLabel()
        self.message_label.setObjectName("notificationMessage")
        self.message_label.setWordWrap(True)

        close_btn = QToolButton()
        close_btn.setObjectName("notificationClose")
        close_btn.setText("×")
        close_btn.setFixedSize(24, 24)
        close_btn.clicked.connect(self.dismiss)

        title_row.addWidget(self.title_label, 1)
        title_row.addWidget(self.count_label, 0, Qt.AlignVCenter)

        text_layout.addLayout(title_row)
        text_layout.addWidget(self.message_label)

        layout.addWidget(accent)
        layout.addWidget(icon_label, 0, Qt.AlignTop)
        layout.addLayout(text_layout, 1)
        layout.addWidget(close_btn, 0, Qt.AlignTop)

    def _setup_animation(self):
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_in_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_in_anim.setDuration(180)
        self._fade_in_anim.setStartValue(0.0)
        self._fade_in_anim.setEndValue(1.0)
        self._fade_in_anim.setEasingCurve(QEasingCurve.OutCubic)

        self._fade_out_anim = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_out_anim.setDuration(220)
        self._fade_out_anim.setStartValue(1.0)
        self._fade_out_anim.setEndValue(0.0)
        self._fade_out_anim.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out_anim.finished.connect(self._on_fade_out_finished)

    def _setup_timer(self):
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)

    def _refresh_content(self):
        self.title_label.setText(self.base_title)
        self.message_label.setText(self.base_message)
        if self.count > 1:
            self.count_label.setText(f"x{self.count}")
            self.count_label.show()
        else:
            self.count_label.hide()

    def show_toast(self):
        self.show()
        self.raise_()
        self._closing = False
        self._fade_out_anim.stop()
        self._fade_in_anim.stop()
        self._opacity_effect.setOpacity(0.0)
        self._fade_in_anim.start()
        self._timer.start(self.duration_ms)

    def restart(self):
        self._closing = False
        self._fade_out_anim.stop()
        self._fade_in_anim.stop()
        self._opacity_effect.setOpacity(1.0)
        self._timer.start(self.duration_ms)
        self._refresh_content()
        self.raise_()

    def bump(self, title=None, message=None):
        self.count += 1
        if title is not None:
            self.base_title = title
        if message is not None:
            self.base_message = message
        self._refresh_content()
        self.restart()

    def dismiss(self):
        if self._closing:
            return
        self._closing = True
        self._timer.stop()
        self._fade_in_anim.stop()
        self._fade_out_anim.stop()
        self._fade_out_anim.setStartValue(self._opacity_effect.opacity())
        self._fade_out_anim.setEndValue(0.0)
        self._fade_out_anim.start()

    def _on_fade_out_finished(self):
        if self._closing:
            self.dismissed.emit(self)


class MainWindow(QMainWindow):
    CARD_SPACING = 24
    CARD_MIN_WIDTH = 540

    def __init__(self, show_startup_overlay: bool = False):
        super().__init__()
        self.setWindowTitle("DD录播机")  # 启动语言应用后由 retranslate_ui 覆盖
        self.resize(1360, 780)
        self.setObjectName("appRoot")

        # 窗口、任务栏和托盘统一使用 assets/icon.ico。
        self.setWindowIcon(_load_app_icon())

        self.cards = {}           # room_id -> RoomCard
        self.threads = {}         # room_id -> QThread
        self.recorders = {}       # room_id -> BiliRecorder
        # 关监听/删除时,QThread 不能立即销毁(还在跑会段错误),先移到这里异步等退出
        self._dying_threads = []  # [(thread, recorder), ...]
        self._stopping_recorders = {}  # room_id -> (thread, recorder)
        self._pending_recorder_restarts = set()
        # auto-heal: room_id -> [restart_timestamps]
        self._heal_restart_times = {}
        self._heal_blocked = set()  # rooms that exceeded restart budget

        # 通知队列
        self.notifications = []
        self.notification_lookup = {}
        self.filter_mode = "all"
        self.sort_mode = "default"
        self._last_grid_signature = None
        self._layout_ready = False
        self.current_page = "channels"
        self._is_shutting_down = False

        # 启动性能计时
        self._startup_t0 = time.perf_counter() * 1000
        self._startup_overlay = None

        self.setup_ui(show_startup_overlay=show_startup_overlay)
        self._center_window()  # 窗口居中

        # 代码生成的工具栏图标随主题重绘
        theme_manager().changed.connect(self._on_theme_changed)

        # 语言切换时重译外壳静态文案
        from ui.i18n import language_manager
        language_manager().changed.connect(self.retranslate_ui)
        self.retranslate_ui()

        # 启动阶段在 bootstrap() 里分步推进（经事件循环空闲片段），
        # 避免一次性阻塞首帧、让开屏动画能走。
        # bootstrap() 由入口 __main__ 调用，不再在 __init__ 里直接做重活。

    def bootstrap(self):
        """分阶段完成启动：配置 → 卡片 → 托盘/定时器 → 电源 → 揭示主界面 → 分批启动监听。

        每个阶段用 QTimer.singleShot(0, ...) 排进事件循环，阶段之间让出
        主线程处理 paint/动画事件；进度通过内部覆盖层推进。
        """
        self._log_startup("main UI built")
        from ui.i18n import t
        self._startup_report(15, t("startup.init"))
        QTimer.singleShot(0, self._startup_prepare_config)

    # ---------- 启动阶段 ----------
    def _startup_report(self, percent: int, status: str = ""):
        overlay = getattr(self, "_startup_overlay", None)
        if overlay is not None:
            overlay.set_progress(percent)
            if status:
                overlay.set_status_text(status)

    def _startup_prepare_config(self):
        try:
            ensure_default_config()
        except Exception:
            logging.exception("ensure_default_config 失败")
        self._log_startup("config ready")
        from ui.i18n import t
        self._startup_report(20, t("startup.loading_channels"))
        QTimer.singleShot(0, self._startup_load_data)

    def _startup_load_data(self):
        self._startup_channels = load_app_data().get("channels", [])
        self._startup_total = max(1, len(self._startup_channels))
        self._layout_ready = False
        self._startup_report(25)
        QTimer.singleShot(0, self._startup_create_cards_batch)

    def _startup_create_cards_batch(self):
        start = getattr(self, "_startup_card_index", 0)
        total = getattr(self, "_startup_total", 1)
        channels = getattr(self, "_startup_channels", [])
        batch_size = 3
        end = min(start + batch_size, total)

        for ch in channels[start:end]:
            try:
                self.add_card(ch, save=False, rearrange=False, start_recorder=False, animate=False)
            except Exception:
                logging.exception(f"加载频道失败: {ch.get('room_id')}")

        self._startup_card_index = end
        if end < total:
            pct = 25 + int((end / total) * 45)
            self._startup_report(pct)
            QTimer.singleShot(0, self._startup_create_cards_batch)
        else:
            self._layout_ready = True
            self.request_rearrange_cards(0)
            self._log_startup("cards created")
            from ui.i18n import t
            self._startup_report(80, t("startup.system"))
            QTimer.singleShot(0, self._startup_setup_tray)

    def _startup_setup_tray(self):
        try:
            self._setup_tray()
        except Exception:
            logging.exception("_setup_tray 失败")
        QTimer.singleShot(0, self._startup_setup_timers)

    def _startup_setup_timers(self):
        try:
            self.start_refresh_timer()
        except Exception:
            logging.exception("start_refresh_timer 失败")
        self._startup_report(92)
        QTimer.singleShot(0, self._startup_setup_power)

    def _startup_setup_power(self):
        try:
            self._power_keepalive = PowerKeepAlive()
            if get_global_setting("prevent_sleep"):
                self._power_keepalive.start()
            if get_global_setting("auto_start"):
                set_auto_start(True)
        except Exception:
            logging.exception("电源/自启初始化失败")
        self._log_startup("recorders started")
        from ui.i18n import t
        self._startup_report(100, t("startup.finishing"))
        QTimer.singleShot(0, self._startup_reveal_main_content)

    def _startup_reveal_main_content(self):
        """主画面淡入，开屏层淡出并删除，然后分批启动录播监听。

        保证覆盖层至少让用户看完进入动画并短暂停留后再揭示，
        避免初始化太快导致 logo 一闪而过。
        """
        overlay = getattr(self, "_startup_overlay", None)
        if overlay is None:
            self._startup_start_recorders_in_batches()
            return

        earliest = self._earliest_reveal_time_ms()
        now = int(time.perf_counter() * 1000)
        wait_ms = max(0, earliest - now)

        if wait_ms > 0:
            QTimer.singleShot(wait_ms, self._do_reveal_main_content)
        else:
            self._do_reveal_main_content()

    def _earliest_reveal_time_ms(self) -> int:
        overlay = getattr(self, "_startup_overlay", None)
        if overlay is None:
            return 0
        return (
            self._startup_overlay_shown_at_ms
            + overlay.intro_duration_ms
            + overlay.HOLD_BEFORE_REVEAL_MS
        )

    def _do_reveal_main_content(self):
        overlay = getattr(self, "_startup_overlay", None)
        if overlay is None or overlay._closing:
            self._startup_start_recorders_in_batches()
            return

        # 主内容交叉淡入
        opacity_effect = QGraphicsOpacityEffect(self.main_content)
        opacity_effect.setOpacity(0.0)
        self.main_content.setGraphicsEffect(opacity_effect)

        main_fade = QPropertyAnimation(opacity_effect, b"opacity", self)
        main_fade.setDuration(StartupOverlay.FADE_OUT_DURATION_MS)
        main_fade.setStartValue(0.0)
        main_fade.setEndValue(1.0)
        main_fade.setEasingCurve(QEasingCurve.OutCubic)
        main_fade.start()

        def _on_revealed():
            if self._startup_overlay is overlay:
                self._startup_overlay = None
            self._log_startup("main window interactive")
            self.main_content.setGraphicsEffect(None)
            self._startup_start_recorders_in_batches()

        overlay.fade_out_and_close(_on_revealed)

    def _startup_start_recorders_in_batches(self):
        """揭示主界面后再分批启动监听，避免启动阶段同时创建大量线程。"""
        pending = [
            (room_id, card.room_info)
            for room_id, card in self.cards.items()
            if card.room_info.get("enabled", True) and room_id not in self.recorders
        ]
        if not pending:
            self._startup_cleanup()
            return

        batch_size = 2
        interval_ms = 80
        index_ref = [0]

        def _next_batch():
            start = index_ref[0]
            end = min(start + batch_size, len(pending))
            for room_id, room_info in pending[start:end]:
                try:
                    self._start_recorder(room_id, room_info)
                except Exception:
                    logging.exception(f"启动录播监听失败: {room_id}")
            index_ref[0] = end
            if end < len(pending):
                QTimer.singleShot(interval_ms, _next_batch)
            else:
                self._startup_cleanup()

        _next_batch()

    def _startup_cleanup(self):
        # 释放临时启动数据
        for attr in ("_startup_channels", "_startup_total", "_startup_card_index"):
            if hasattr(self, attr):
                delattr(self, attr)

    def add_card(self, room_info: dict, save=True, rearrange=True, start_recorder=True, animate=True):
        room_id = str(room_info["room_id"])
        if room_id in self.cards:
            return False

        card = RoomCard(room_info)
        card.toggle_signal.connect(self.on_card_toggle)
        card.cut_signal.connect(self.on_cut)
        card.delete_signal.connect(self.on_delete)
        card.settings_signal.connect(self.on_settings)
        card.open_folder_signal.connect(self.on_open_folder)

        self.cards[room_id] = card

        # 只有 enabled=True 才启 QThread(节省资源 + 避免卡顿)
        if start_recorder and room_info.get("enabled", True):
            self._start_recorder(room_id, room_info)
        else:
            # 启动阶段或未开启监听：手动设一次初始状态
            card.update_status(
                "⚙️ 已暂停", "🌙 未开播", "⏳ 闲置中",
                room_info.get("title", ""), "", "", "",
                room_info.get("parent_area_name", ""),
                room_info.get("area_name", ""),
                animate=False
            )

        if rearrange:
            self.request_rearrange_cards(0)

        if animate:
            QTimer.singleShot(0, card.play_entry_animation)

        if save:
            self.save_data()

        return True

    def load_saved_data(self):
        data = load_app_data()
        self._layout_ready = False
        for ch in data.get("channels", []):
            self.add_card(ch, save=False, rearrange=False, start_recorder=False, animate=False)
        self._layout_ready = True
        self.request_rearrange_cards(0)
        self._startup_start_recorders_in_batches()
        """供外部/未来使用：保证 _power_keepalive 字段存在（closeEvent 会用到）。"""
        if not hasattr(self, "_power_keepalive"):
            self._power_keepalive = PowerKeepAlive()
        return self._power_keepalive

    def _center_window(self):
        """将窗口居中显示"""
        screen = self.screen()
        if screen:
            screen_geometry = screen.geometry()
            window_geometry = self.geometry()
            x = (screen_geometry.width() - window_geometry.width()) // 2 + screen_geometry.x()
            y = (screen_geometry.height() - window_geometry.height()) // 2 + screen_geometry.y()
            self.move(x, y)

    def _on_theme_changed(self, _name):
        """主题切换后重绘代码生成的图标；不重建页面/菜单/卡片。"""
        if hasattr(self, "filter_btn") and self.filter_btn is not None:
            self.filter_btn.setIcon(self._create_toolbar_icon("filter"))
        if hasattr(self, "sort_btn") and self.sort_btn is not None:
            self.sort_btn.setIcon(self._create_toolbar_icon("sort"))

    def retranslate_ui(self):
        """按当前语言重设外壳与高频交互文案；语言切换时被 language_manager.changed 触发。"""
        from ui.i18n import t
        self.setWindowTitle(t("app.title"))
        if hasattr(self, "_nav_channels_tip") and self._nav_channels_tip is not None:
            self._nav_channels_tip.setText(t("nav.channels"))
        if hasattr(self, "_nav_settings_tip") and self._nav_settings_tip is not None:
            self._nav_settings_tip.setText(t("nav.settings"))
        if hasattr(self, "_nav_history_tip") and self._nav_history_tip is not None:
            self._nav_history_tip.setText(t("nav.history"))
        if hasattr(self, "_nav_about_tip") and self._nav_about_tip is not None:
            self._nav_about_tip.setText(t("nav.about"))
        if hasattr(self, "channels_title_label") and self.channels_title_label is not None:
            self.channels_title_label.setText(t("channels.title"))
        if hasattr(self, "search_input") and self.search_input is not None:
            self.search_input.setPlaceholderText(t("channels.search_placeholder"))
        if hasattr(self, "btn_add") and self.btn_add is not None:
            self.btn_add.setText(t("channels.add"))
            if hasattr(self.btn_add, "_hover_tip") and self.btn_add._hover_tip is not None:
                self.btn_add._hover_tip.setText(t("channels.add_tip"))
            elif hasattr(self, "_btn_add_tip") and self._btn_add_tip is not None:
                self._btn_add_tip.setText(t("channels.add_tip"))
        if hasattr(self, "about_title_label") and self.about_title_label is not None:
            self.about_title_label.setText(t("about.title"))
        if hasattr(self, "history_page") and self.history_page is not None and hasattr(self.history_page, "retranslate_ui"):
            self.history_page.retranslate_ui()
        if hasattr(self, "_tray_show_action") and self._tray_show_action is not None:
            self._tray_show_action.setText(t("tray.show"))
        if hasattr(self, "_tray_quit_action") and self._tray_quit_action is not None:
            self._tray_quit_action.setText(t("tray.quit"))
        if hasattr(self, "_tray_icon") and self._tray_icon is not None:
            self._tray_icon.setToolTip(t("tray.tooltip"))
        # 过滤/排序菜单与 tip
        if hasattr(self, "filter_btn") and self.filter_btn is not None:
            self._setup_filter_menu()
        if hasattr(self, "sort_btn") and self.sort_btn is not None:
            self._setup_sort_menu()
        # 卡片 tip/状态
        for card in getattr(self, "cards", {}).values():
            if hasattr(card, "retranslate_ui"):
                card.retranslate_ui()
        # 关于页动态区块
        if hasattr(self, "_about_app_name") and self._about_app_name is not None:
            self._about_app_name.setText(t("about.app_name"))
        if hasattr(self, "_about_logs_title") and self._about_logs_title is not None:
            self._about_logs_title.setText(t("about.logs"))
        if hasattr(self, "_about_open_logs_btn") and self._about_open_logs_btn is not None:
            self._about_open_logs_btn.setText(t("about.open_logs"))
        if hasattr(self, "_about_open_log_dir_btn") and self._about_open_log_dir_btn is not None:
            self._about_open_log_dir_btn.setText(t("about.open_log_dir"))
        if hasattr(self, "_about_logs_desc") and self._about_logs_desc is not None:
            self._about_logs_desc.setText(t("about.logs_desc"))
        if hasattr(self, "_about_check_update_btn") and self._about_check_update_btn is not None:
            self._about_check_update_btn.setText(t("about.check_update"))
        if hasattr(self, "_about_help_btn") and self._about_help_btn is not None:
            self._about_help_btn.setText("📖 " + t("help.open"))
        if hasattr(self, "_refresh_about_info_label"):
            self._refresh_about_info_label()

    def _create_toolbar_icon(self, kind, color=None):
        if color is None:
            color = theme_manager().tokens["text_secondary"]
        pixmap = QPixmap(20, 20)
        pixmap.fill(Qt.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        pen = QPen(QColor(color))
        pen.setWidth(2)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        painter.setPen(pen)

        if kind == "filter":
            path = QPainterPath()
            path.moveTo(3, 5)
            path.lineTo(17, 5)
            path.lineTo(12, 10)
            path.lineTo(12, 15)
            path.lineTo(8, 17)
            path.lineTo(8, 10)
            path.closeSubpath()
            painter.drawPath(path)
        elif kind == "sort":
            painter.drawLine(4, 5, 14, 5)
            painter.drawLine(4, 10, 11, 10)
            painter.drawLine(4, 15, 8, 15)
            painter.drawLine(16, 4, 16, 16)
            painter.drawLine(14, 14, 16, 16)
            painter.drawLine(18, 14, 16, 16)

        painter.end()
        return QIcon(pixmap)

    def _create_sidebar_nav_button(self, text, tooltip):
        # tooltip 参数保留 API 兼容,但不调 setToolTip — 系统 tooltip 在 Win11 暗色
        # 主题下是黑底黑字,盖在自定义 HoverLabel 上看不到字。完全用 HoverLabel。
        del tooltip
        button = _HoverToolButton(text, "")
        button.setFixedSize(56, 56)
        button.setCursor(Qt.PointingHandCursor)
        return button

    def _wire_hover(self, button, text, attr_name=None):
        """给 sidebar / 顶部按钮挂自定义 hover tip。

        attr_name: 可选, 把 tip 引用存到 self.<attr_name>, 后续可动态 setText。
        """
        tip = HoverLabel(button, text)
        # 兼容两种 button: _HoverToolButton (有 attach_tip) / 普通 QToolButton
        if hasattr(button, "attach_tip"):
            button.attach_tip(tip)
        else:
            from ui.room_card import _HoverFilter
            button.installEventFilter(_HoverFilter(tip))
        if attr_name:
            setattr(self, attr_name, tip)
        return tip

    def _sidebar_nav_buttons(self):
        return (
            ("channels", self.nav_channels_btn),
            ("history", self.nav_history_btn),
            ("settings", self.nav_settings_btn),
            ("about", self.nav_about_btn),
        )

    def _active_sidebar_nav_button(self):
        for name, btn in self._sidebar_nav_buttons():
            if self.current_page == name:
                return btn
        return self.nav_channels_btn

    def _nav_indicator_rect_for(self, button) -> QRect:
        """侧栏左侧指示条目标几何（相对 sidebar）。"""
        sidebar = getattr(self, "_sidebar", None)
        if sidebar is None or button is None:
            return QRect()
        top_left = button.mapTo(sidebar, QPoint(0, 0))
        # 3px 竖条，垂直对齐按钮并略缩进
        bar_h = max(20, button.height() - 20)
        y = top_left.y() + (button.height() - bar_h) // 2
        return QRect(6, y, 3, bar_h)

    def _move_sidebar_nav_indicator(self, *, animate: bool = True):
        indicator = getattr(self, "_nav_indicator", None)
        sidebar = getattr(self, "_sidebar", None)
        if indicator is None or sidebar is None:
            return
        button = self._active_sidebar_nav_button()
        target = self._nav_indicator_rect_for(button)
        if not target.isValid():
            return

        indicator.show()
        indicator.raise_()
        # 按钮始终盖在指示条之上，避免挡住点击
        for _, btn in self._sidebar_nav_buttons():
            btn.raise_()

        anim = getattr(self, "_nav_indicator_anim", None)
        current = indicator.geometry()
        if (
            not animate
            or not current.isValid()
            or current.height() <= 0
            or current == target
        ):
            if anim is not None:
                anim.stop()
            indicator.setGeometry(target)
            return

        if anim is None:
            anim = QPropertyAnimation(indicator, b"geometry", self)
            anim.setDuration(200)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            self._nav_indicator_anim = anim
        anim.stop()
        anim.setStartValue(current)
        anim.setEndValue(target)
        anim.start()

    def _update_sidebar_nav_styles(self, *, animate_indicator: bool = True):
        from ui.theme import repolish

        for name, btn in self._sidebar_nav_buttons():
            btn.setProperty("active", self.current_page == name)
            repolish(btn)
        self._move_sidebar_nav_indicator(animate=animate_indicator)

    def setup_ui(self, show_startup_overlay: bool = False):
        central = QWidget()
        self.setCentralWidget(central)
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # 主内容容器
        self.main_content = QWidget()
        main_layout = QHBoxLayout(self.main_content)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ==================== 侧边栏 ====================
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        enable_surface(sidebar)
        sidebar.setFixedWidth(72)
        self._sidebar = sidebar

        # 滑动选中指示条（绝对定位，不进 layout）
        self._nav_indicator = QFrame(sidebar)
        self._nav_indicator.setObjectName("sidebarNavIndicator")
        self._nav_indicator.hide()
        self._nav_indicator_anim = QPropertyAnimation(self._nav_indicator, b"geometry", self)
        self._nav_indicator_anim.setDuration(200)
        self._nav_indicator_anim.setEasingCurve(QEasingCurve.OutCubic)

        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 20, 8, 20)
        sidebar_layout.setSpacing(16)

        self.nav_channels_btn = self._create_sidebar_nav_button("📋", "")
        self._nav_channels_tip = self._wire_hover(self.nav_channels_btn, "", attr_name="_nav_channels_tip")
        self.nav_channels_btn.clicked.connect(self.show_channels_page)
        self.nav_history_btn = self._create_sidebar_nav_button("🎞️", "")
        self._nav_history_tip = self._wire_hover(self.nav_history_btn, "", attr_name="_nav_history_tip")
        self.nav_history_btn.clicked.connect(self.show_history_page)
        self.nav_settings_btn = self._create_sidebar_nav_button("⚙️", "")
        self._nav_settings_tip = self._wire_hover(self.nav_settings_btn, "", attr_name="_nav_settings_tip")
        self.nav_settings_btn.clicked.connect(self.show_global_settings_page)
        self.nav_about_btn = self._create_sidebar_nav_button("ℹ️", "")
        self._nav_about_tip = self._wire_hover(self.nav_about_btn, "", attr_name="_nav_about_tip")
        self.nav_about_btn.clicked.connect(self.show_about_page)

        sidebar_layout.addWidget(self.nav_channels_btn)
        sidebar_layout.addWidget(self.nav_history_btn)
        sidebar_layout.addWidget(self.nav_settings_btn)
        sidebar_layout.addStretch()
        sidebar_layout.addWidget(self.nav_about_btn)
        # 首次布局后再定位指示条，避免 0 尺寸
        self._update_sidebar_nav_styles(animate_indicator=False)
        QTimer.singleShot(0, lambda: self._move_sidebar_nav_indicator(animate=False))

        # ==================== 内容区 ====================
        self.page_stack = SlideStackedWidget(duration=280)
        self.page_stack.setObjectName("pageStack")
        self.page_stack.setStyleSheet(
            "QStackedWidget#pageStack, QStackedWidget#pageStack > QWidget {"
            " background-color: transparent; border: none; }"
        )

        self.channels_page = QWidget()
        content_layout = QVBoxLayout(self.channels_page)
        content_layout.setContentsMargins(32, 24, 32, 24)
        content_layout.setSpacing(20)

        # 顶部工具栏
        toolbar = QFrame()
        toolbar.setObjectName("channelsToolbar")
        enable_surface(toolbar)
        top_bar = QHBoxLayout(toolbar)
        top_bar.setContentsMargins(16, 12, 16, 12)
        top_bar.setSpacing(12)

        # 标题
        self.channels_title_label = QLabel()
        self.channels_title_label.setFont(QFont("Microsoft YaHei UI", 24, QFont.Bold))
        self.channels_title_label.setProperty("role", "title")

        # 搜索框
        self.search_input = QLineEdit()
        self.search_input.setObjectName("channelSearch")
        self.search_input.setFixedWidth(320)
        self.search_input.textChanged.connect(lambda _text: self.request_rearrange_cards(120))

        # 过滤和排序按钮
        from ui.i18n import t as _t0
        self.filter_btn = _HoverToolButton("", _t0("channels.filter"))
        self.filter_btn.setObjectName("toolbarButton")
        self.filter_btn.setFixedSize(44, 44)
        self.filter_btn.setIcon(self._create_toolbar_icon("filter"))
        self.filter_btn.setIconSize(QPixmap(20, 20).size())
        self.filter_btn.setPopupMode(QToolButton.InstantPopup)
        self._wire_hover(self.filter_btn, _t0("channels.filter"), attr_name="_filter_tip")

        self.sort_btn = _HoverToolButton("", _t0("channels.sort"))
        self.sort_btn.setObjectName("toolbarButton")
        self.sort_btn.setFixedSize(44, 44)
        self.sort_btn.setIcon(self._create_toolbar_icon("sort"))
        self.sort_btn.setIconSize(QPixmap(20, 20).size())
        self.sort_btn.setPopupMode(QToolButton.InstantPopup)
        self._wire_hover(self.sort_btn, _t0("channels.sort"), attr_name="_sort_tip")

        # 添加按钮
        self.btn_add = _HoverPushButton(_t0("channels.add"), _t0("channels.add_tip"))
        self.btn_add.setProperty("variant", "primary")
        self.btn_add.setFixedHeight(44)
        self.btn_add.setMinimumWidth(100)
        self._wire_hover(self.btn_add, _t0("channels.add_tip"), attr_name="_btn_add_tip")
        self.btn_add.clicked.connect(self.add_channel)

        top_bar.addWidget(self.channels_title_label)
        top_bar.addStretch()
        top_bar.addWidget(self.search_input)
        top_bar.addWidget(self.filter_btn)
        top_bar.addWidget(self.sort_btn)
        top_bar.addWidget(self.btn_add)

        content_layout.addWidget(toolbar)

        # 卡片滚动区域
        self.scroll = QScrollArea()
        self.scroll.setObjectName("mainScroll")
        self.scroll.setWidgetResizable(True)

        self.cards_widget = QWidget()
        self.grid_layout = QGridLayout(self.cards_widget)
        self.grid_layout.setSpacing(24)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll.setWidget(self.cards_widget)
        content_layout.addWidget(self.scroll)

        # 设置页面：容器先建，内容延迟到首次切换时创建
        self.settings_page = QWidget()
        settings_layout = QVBoxLayout(self.settings_page)
        settings_layout.setContentsMargins(32, 24, 32, 24)
        settings_layout.setSpacing(20)
        self.global_settings_page = None

        self.page_stack.addWidget(self.channels_page)
        self.page_stack.addWidget(self.settings_page)

        # 最近录制 / 关于：延迟创建
        self.history_page = None
        self._history_page_index = -1
        self.about_page = None
        self._about_page_index = -1

        main_layout.addWidget(sidebar)
        main_layout.addWidget(self.page_stack, 1)

        root_layout.addWidget(self.main_content, 1)

        # 启动覆盖层
        if show_startup_overlay:
            self._startup_overlay = StartupOverlay(version=__version__, parent=central)
            self._startup_overlay.setGeometry(central.rect())
            self._startup_overlay.raise_()
            self._startup_overlay.show()
            self._startup_overlay_shown_at_ms = int(time.perf_counter() * 1000)
        else:
            self._startup_overlay_shown_at_ms = 0

        # 通知区域
        self.notification_container = QWidget(central)
        self.notification_container.setAttribute(Qt.WA_TransparentForMouseEvents, False)

        self.notification_layout = QVBoxLayout(self.notification_container)
        self.notification_layout.setSpacing(10)
        self.notification_layout.setContentsMargins(0, 0, 0, 0)
        self.notification_layout.setAlignment(Qt.AlignTop)

        # 设置容器大小和初始位置
        self.notification_container.setFixedWidth(360)

        # 安装事件过滤器
        central.installEventFilter(self)
        self.scroll.viewport().installEventFilter(self)
        self._setup_filter_menu()
        self._setup_sort_menu()

        # 布局更新定时器
        self._layout_update_timer = QTimer(self)
        self._layout_update_timer.setSingleShot(True)
        self._layout_update_timer.timeout.connect(self._rearrange_cards)
        self._layout_ready = True

        # 初始定位
        self._position_notification_container()

    def _ensure_global_settings_page(self):
        """延迟创建设置页内容。"""
        if self.global_settings_page is not None:
            return
        self.global_settings_page = GlobalSettingsOldStyleReplicaPage(self.settings_page)
        self.global_settings_page.saved.connect(self._on_global_setting_saved)
        self.settings_page.layout().addWidget(self.global_settings_page)

    def _on_global_setting_saved(self, key=""):
        from ui.i18n import t
        self.show_notification(
            t("common.success"),
            t("common.save_settings"),
            "success",
            merge_key="global-settings:saved",
        )
        if key == "tag_library":
            for card in self.cards.values():
                if hasattr(card, "refresh_custom_tags"):
                    card.refresh_custom_tags()
            # 如果当前筛选引用的标签刚被删除，回退到全部
            if self.filter_mode.startswith("tag:"):
                from core.config import get_tag
                if get_tag(self.filter_mode.split(":", 1)[1]) is None:
                    self.filter_mode = "all"
            self._setup_filter_menu()
            self.request_rearrange_cards(0)

    def _ensure_history_page(self):
        """延迟创建最近录制页面。"""
        if self.history_page is not None:
            return
        from ui.recent_recordings import RecentRecordingsPage
        self.history_page = RecentRecordingsPage(self)
        self._history_page_index = self.page_stack.addWidget(self.history_page)

    def _ensure_about_page(self):
        """延迟创建关于页面。"""
        if self.about_page is not None:
            return
        self.about_page = self._build_about_page()
        self._about_page_index = self.page_stack.addWidget(self.about_page)

    def resizeEvent(self, event):
        """覆盖层跟随 central widget 大小。"""
        super().resizeEvent(event)
        if getattr(self, "_startup_overlay", None) is not None:
            central = self.centralWidget()
            if central is not None:
                self._startup_overlay.setGeometry(central.rect())
        self._position_notification_container()

    def showEvent(self, event):
        super().showEvent(event)
        self._log_startup("window visible")

    def _log_startup(self, label: str):
        if getattr(self, "_startup_t0", None) is None:
            return
        elapsed = int(time.perf_counter() * 1000 - self._startup_t0)
        logging.info(f"[startup] {label}: {elapsed}ms")

    def _setup_tray(self):
        """系统托盘：X 隐藏到托盘，右键菜单显示/退出。"""
        from ui.i18n import t

        self._tray_menu = QMenu()

        self._tray_show_action = QAction(t("tray.show"))
        self._tray_show_action.triggered.connect(self._tray_show)
        self._tray_menu.addAction(self._tray_show_action)

        self._tray_quit_action = QAction(t("tray.quit"))
        self._tray_quit_action.triggered.connect(self._tray_quit)
        self._tray_menu.addAction(self._tray_quit_action)

        self._tray_icon = QSystemTrayIcon(self)
        self._tray_icon.setToolTip(t("tray.tooltip"))
        self._tray_icon.setContextMenu(self._tray_menu)
        self._tray_icon.activated.connect(self._on_tray_activated)

        # 托盘图标与窗口/可执行文件图标保持一致。
        tray_icon = self.windowIcon()
        if tray_icon.isNull():
            tray_icon = _load_app_icon()
        self._tray_icon.setIcon(tray_icon)
        self._tray_icon.show()

    def _on_tray_activated(self, reason):
        # 左键点击和双击都切换显示/隐藏
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._tray_show_hide()

    def _tray_show(self):
        """托盘 -> 显示窗口"""
        self.showNormal()
        self.activateWindow()
        self.raise_()

    def _tray_show_hide(self):
        """左键点击托盘图标：显示则隐藏，隐藏则显示"""
        if self.isVisible():
            self.hide()
        else:
            self.showNormal()
            self.activateWindow()
            self.raise_()

    def _tray_quit(self):
        """托盘 -> 退出：先统一停录并尽量收尾，再退出。"""
        self._is_shutting_down = True
        for room_id in list(self.recorders.keys()):
            try:
                self._stop_recorder(room_id)
            except Exception as e:
                logging.warning(f"退出时停止 {room_id} 失败: {e}")
        self.hide()
        if self._tray_icon:
            self._tray_icon.hide()
        app = QApplication.instance()
        if app is None:
            return
        if not self.recorders and not self._stopping_recorders:
            app.quit()
        else:
            # 保留事件循环等待 recorder 发 finished；超时后沿用原来的尽力退出语义。
            QTimer.singleShot(8000, app.quit)

    def show_notification(self, message, title=None, level="info", merge_key=None, duration_ms=3200):
        """显示通知"""
        from ui.i18n import t

        if title is None:
            title = t("common.tip")
        key = merge_key or f"{level}|{title}|{message}"

        existing = self.notification_lookup.get(key)
        if existing and existing in self.notifications:
            existing.bump(title=title, message=message)
            self._position_notification_container()
            return

        notification = Notification(
            message, title, level, self.notification_container,
            duration_ms=duration_ms, merge_key=key
        )
        notification.dismissed.connect(self._finalize_notification)
        self.notifications.append(notification)
        self.notification_lookup[key] = notification
        
        # 添加到通知布局
        self.notification_layout.addWidget(notification)
        
        # 显示通知
        notification.show_toast()
        
        # 更新容器位置和大小
        self._position_notification_container()
    
    def remove_notification(self, notification):
        """移除通知"""
        if notification in self.notifications:
            notification.dismiss()

    def _finalize_notification(self, notification):
        if notification not in self.notifications:
            return
        self.notifications.remove(notification)
        merge_key = getattr(notification, "merge_key", None)
        if merge_key in self.notification_lookup:
            del self.notification_lookup[merge_key]
        self.notification_layout.removeWidget(notification)
        notification.deleteLater()
        self._position_notification_container()

    def _start_recorder(self, room_id, room_info):
        """启动房间的录播监控 — 创建 BiliRecorder + QThread,只在用户开监听时调用。

        QThread + moveToThread 是为了让 BiliRecorder.run() 的长循环不阻塞主线程。
        """
        if room_id in self.recorders:
            return  # 已经在跑
        if room_id in self._stopping_recorders:
            # 用户在旧线程收尾期间重新开启，等 finished 后再安全重启。
            self._pending_recorder_restarts.add(room_id)
            return

        card = self.cards.get(room_id)
        if not card:
            return

        try:
            recorder = BiliRecorder(room_info)
        except Exception as exc:
            logging.error(f"[main] 创建 BiliRecorder 失败 room_id={room_id}: {exc}", exc_info=True)
            return
        thread = QThread()
        recorder.moveToThread(thread)

        # 必须 QueuedConnection：lambda 没有 QObject 接收端亲和性，
        # Auto/Direct 会让 worker 线程直接进 card.update_status → repolish，
        # 长时间录制后在 Windows 上触发 access violation。
        recorder.status_updated.connect(
            card.update_status,
            Qt.QueuedConnection,
        )
        recorder.segment_health_updated.connect(
            self._on_segment_health_updated,
            Qt.QueuedConnection,
        )
        recorder.cut_completed.connect(self.on_cut_completed, Qt.QueuedConnection)
        recorder.cut_failed.connect(self.on_cut_failed, Qt.QueuedConnection)

        thread.started.connect(recorder.run)
        recorder.finished.connect(recorder.deleteLater)
        recorder.finished.connect(thread.quit)
        thread.finished.connect(
            lambda rid=room_id, rec=recorder, worker_thread=thread:
            self._on_recorder_thread_finished(rid, rec, worker_thread),
            Qt.QueuedConnection,
        )
        thread.finished.connect(thread.deleteLater)

        self.recorders[room_id] = recorder
        self.threads[room_id] = thread
        thread.start()

    def _on_segment_health_updated(self, room_id: str, health: str, detail: str = ""):
        card = self.cards.get(str(room_id))
        if card is None:
            return
        if hasattr(card, "update_health"):
            card.update_health(health, detail)

    def _heal_settings(self):
        from core.config import get_global_setting
        enabled = bool(get_global_setting("recorder_auto_heal"))
        try:
            max_restarts = int(get_global_setting("recorder_auto_heal_max_restarts") or 3)
        except (TypeError, ValueError):
            max_restarts = 3
        try:
            window_sec = float(get_global_setting("recorder_auto_heal_window_sec") or 600)
        except (TypeError, ValueError):
            window_sec = 600.0
        return enabled, max(1, max_restarts), max(60.0, window_sec)

    def _should_auto_heal(self, room_id: str) -> bool:
        """Decide whether an unexpected recorder exit should restart this room."""
        if self._is_shutting_down:
            return False
        if room_id in self._heal_blocked:
            return False
        card = self.cards.get(room_id)
        if card is None:
            return False
        # 用户已关监听：不重启
        if not card.room_info.get("enabled", False):
            return False
        if hasattr(card, "is_monitoring_enabled") and not card.is_monitoring_enabled():
            return False
        enabled, max_restarts, window_sec = self._heal_settings()
        if not enabled:
            return False
        now = time.time()
        times = [t for t in self._heal_restart_times.get(room_id, []) if now - t < window_sec]
        if len(times) >= max_restarts:
            self._heal_blocked.add(room_id)
            self._heal_restart_times[room_id] = times
            logging.error(
                f"[heal] room={room_id} auto-heal circuit open: "
                f"{len(times)} restarts within {int(window_sec)}s (max={max_restarts})"
            )
            if hasattr(card, "update_status"):
                card.update_status(
                    "❌ 出错", "🌙 未开播", "❌ 自愈熔断",
                    card.room_info.get("title", ""),
                    "", "", "",
                    card.room_info.get("parent_area_name", ""),
                    card.room_info.get("area_name", ""),
                )
            return False
        times.append(now)
        self._heal_restart_times[room_id] = times
        return True

    def _on_recorder_thread_finished(self, room_id, recorder, thread):
        if self.recorders.get(room_id) is recorder:
            self.recorders.pop(room_id, None)
        if self.threads.get(room_id) is thread:
            self.threads.pop(room_id, None)

        stopping = self._stopping_recorders.get(room_id)
        user_stop = stopping == (thread, recorder)
        if user_stop:
            self._stopping_recorders.pop(room_id, None)

        self._dying_threads = [
            (t, r) for (t, r) in self._dying_threads
            if t is not thread and r is not recorder
        ]

        should_restart = room_id in self._pending_recorder_restarts
        was_pending_restart = should_restart
        self._pending_recorder_restarts.discard(room_id)

        # 用户主动关监听/删除：不自愈
        # 用户在 stopping 期间又打开：走 pending restart
        # 其它意外 finished：尝试 auto-heal
        if not should_restart and not user_stop and not self._is_shutting_down:
            exit_reason = str(getattr(recorder, "exit_reason", "") or "")
            if exit_reason in ("requested_stop", "disabled"):
                logging.info(
                    f"[heal] room={room_id} recorder exit not eligible: reason={exit_reason}"
                )
            elif self._should_auto_heal(room_id):
                should_restart = True
                logging.warning(
                    f"[heal] room={room_id} unexpected recorder exit; "
                    f"reason={exit_reason or 'unknown'}; scheduling restart"
                )

        if (
            should_restart
            and not self._is_shutting_down
            and room_id in self.cards
            and self.cards[room_id].room_info.get("enabled", False)
        ):
            # 用户主动重开：立即；意外自愈：延迟 3s 避开瞬时抖动
            delay_ms = 0 if was_pending_restart else 3000
            QTimer.singleShot(
                delay_ms,
                lambda rid=room_id: self._start_recorder(rid, self.cards[rid].room_info)
                if rid in self.cards else None,
            )

        if self._is_shutting_down and not self.recorders and not self._stopping_recorders:
            app = QApplication.instance()
            if app is not None:
                app.quit()

    def _stop_recorder(self, room_id):
        """请求 recorder 在线程内完成收尾，主线程只做即时 UI 状态更新。"""
        if room_id in self._stopping_recorders:
            return
        if room_id not in self.recorders:
            return

        logging.info(f"[main] stop monitor requested room={room_id}")
        recorder = self.recorders.pop(room_id)
        thread = self.threads.pop(room_id, None)
        self._pending_recorder_restarts.discard(room_id)

        if thread is not None:
            self._stopping_recorders[room_id] = (thread, recorder)
            self._dying_threads.append((thread, recorder))

        try:
            if hasattr(recorder, "request_stop_async"):
                recorder.request_stop_async(reset_time=True)
            else:
                # 兼容旧 recorder：至少不要在 GUI 线程执行潜在阻塞的收尾。
                def _legacy_stop():
                    try:
                        recorder.request_stop(reset_time=True)
                    except Exception:
                        logging.exception(f"停止录播失败: {room_id}")
                _threading.Thread(target=_legacy_stop, daemon=True).start()
        except Exception as exc:
            logging.warning(f"request_stop_async 失败: {exc}")

        if room_id in self.cards:
            card = self.cards[room_id]
            card.update_status(
                "⚙️ 已暂停", "🌙 未开播", "⏳ 正在停止…",
                card.room_info.get("title", ""), "", "", "",
                card.room_info.get("parent_area_name", ""),
                card.room_info.get("area_name", "")
            )

        if thread is None:
            recorder.deleteLater()
        logging.info(f"[main] stop monitor signal sent room={room_id}")

    def load_saved_data(self):
        data = load_app_data()
        self._layout_ready = False
        for ch in data.get("channels", []):
            self.add_card(ch, save=False, rearrange=False, start_recorder=False, animate=False)
        self._layout_ready = True
        self.request_rearrange_cards(0)

    # ==================== 宿主 API（保留给未来内置模块调用） ====================
    def add_card(self, room_info: dict, save=True, rearrange=True, start_recorder=True, animate=True):
        room_id = str(room_info["room_id"])
        if room_id in self.cards:
            return False

        card = RoomCard(room_info)
        card.toggle_signal.connect(self.on_card_toggle)
        card.cut_signal.connect(self.on_cut)
        card.delete_signal.connect(self.on_delete)
        card.settings_signal.connect(self.on_settings)
        card.open_folder_signal.connect(self.on_open_folder)

        self.cards[room_id] = card

        # 只有 enabled=True 才启 QThread(节省资源 + 避免卡顿)
        if start_recorder and room_info.get("enabled", True):
            self._start_recorder(room_id, room_info)
        else:
            # 启动阶段或未开启监听：手动设一次初始状态
            card.update_status(
                "⚙️ 已暂停", "🌙 未开播", "⏳ 闲置中",
                room_info.get("title", ""), "", "", "",
                room_info.get("parent_area_name", ""),
                room_info.get("area_name", ""),
                animate=False
            )

        if rearrange:
            self.request_rearrange_cards(0)

        if animate:
            QTimer.singleShot(0, card.play_entry_animation)

        if save:
            self.save_data()

        return True

    def _rearrange_cards(self):
        """重新排列卡片"""
        if not self._layout_ready:
            return

        cards_list = self._get_display_cards()
        columns = self._calculate_grid_columns()
        signature = (columns, tuple(card.room_id for card in cards_list))
        if signature == self._last_grid_signature:
            return
        self._last_grid_signature = signature

        # 清除现有布局
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                pass
        
        for i, card in enumerate(cards_list):
            row = i // columns
            col = i % columns
            self.grid_layout.addWidget(card, row, col)

    def request_rearrange_cards(self, delay_ms=16):
        if not getattr(self, "_layout_ready", False):
            return
        self._layout_update_timer.start(delay_ms)

    def _calculate_grid_columns(self):
        viewport_width = self.scroll.viewport().width()
        if viewport_width <= 0:
            return 1
        columns = max(1, (viewport_width + self.CARD_SPACING) // (self.CARD_MIN_WIDTH + self.CARD_SPACING))
        return columns

    def _matches_search_and_filter(self, card):
        text = self.search_input.text().strip().lower()
        tags = card.custom_tags() if hasattr(card, "custom_tags") else []
        tag_names = [str(tag.get("name") or "") for tag in tags if isinstance(tag, dict)]
        search_hit = (
            text in card.room_info.get("uname", "").lower() or
            text in card.room_id.lower() or
            text in card.room_info.get("title", "").lower() or
            any(text in name.lower() for name in tag_names)
        )
        if not search_hit:
            return False

        if self.filter_mode == "all":
            return True
        if self.filter_mode == "monitoring":
            return card.is_monitoring_enabled()
        if self.filter_mode == "recording":
            return card.is_recording_active()
        if self.filter_mode == "live":
            return card.is_live()
        if self.filter_mode == "paused":
            return not card.is_monitoring_enabled()
        if self.filter_mode == "untagged":
            return not tags
        if self.filter_mode.startswith("tag:"):
            wanted = self.filter_mode.split(":", 1)[1]
            return any(str(tag.get("id") or "") == wanted for tag in tags if isinstance(tag, dict))
        return True

    def _sort_cards(self, cards_list):
        if self.sort_mode == "name":
            return sorted(cards_list, key=lambda c: c.room_info.get("uname", "").lower())
        if self.sort_mode == "room_id":
            return sorted(cards_list, key=lambda c: int(c.room_id) if c.room_id.isdigit() else c.room_id)
        if self.sort_mode == "status":
            return sorted(
                cards_list,
                key=lambda c: (c.status_priority(), c.room_info.get("uname", "").lower())
            )
        return cards_list

    def _get_display_cards(self):
        filtered_cards = []
        for card in self.cards.values():
            visible = self._matches_search_and_filter(card)
            card.setVisible(visible)
            if visible:
                filtered_cards.append(card)
        return self._sort_cards(filtered_cards)

    def _setup_filter_menu(self):
        from ui.i18n import t

        menu = QMenu(self)

        group = QActionGroup(self)
        group.setExclusive(True)
        options = [
            ("all", t("channels.filter.all")),
            ("monitoring", t("channels.filter.monitoring")),
            ("recording", t("channels.filter.recording")),
            ("live", t("channels.filter.live")),
            ("paused", t("channels.filter.paused")),
        ]
        for key, label in options:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self.filter_mode)
            action.triggered.connect(lambda checked=False, mode=key: self._set_filter_mode(mode))
            group.addAction(action)

        from core.config import get_tag_library
        tags = get_tag_library()
        if tags:
            menu.addSeparator()
            tag_section = menu.addAction(t("channels.filter.tags"))
            tag_section.setEnabled(False)
            untagged = menu.addAction(t("channels.filter.untagged"))
            untagged.setCheckable(True)
            untagged.setChecked(self.filter_mode == "untagged")
            untagged.triggered.connect(lambda checked=False: self._set_filter_mode("untagged"))
            group.addAction(untagged)
            for tag in tags:
                action = menu.addAction(f"● {tag['name']}")
                action.setCheckable(True)
                mode = f"tag:{tag['id']}"
                action.setChecked(mode == self.filter_mode)
                action.triggered.connect(lambda checked=False, m=mode: self._set_filter_mode(m))
                group.addAction(action)

        self.filter_btn.setMenu(menu)
        self._update_filter_button()

    def _setup_sort_menu(self):
        from ui.i18n import t

        menu = QMenu(self)

        group = QActionGroup(self)
        group.setExclusive(True)
        options = [
            ("default", t("channels.sort.default")),
            ("status", t("channels.sort.status")),
            ("name", t("channels.sort.name")),
            ("room_id", t("channels.sort.room_id")),
        ]
        for key, label in options:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self.sort_mode)
            action.triggered.connect(lambda checked=False, mode=key: self._set_sort_mode(mode))
            group.addAction(action)

        self.sort_btn.setMenu(menu)
        self._update_sort_button()

    def _set_filter_mode(self, mode):
        self.filter_mode = mode
        self._update_filter_button()
        self.request_rearrange_cards(0)

    def _set_sort_mode(self, mode):
        self.sort_mode = mode
        self._update_sort_button()
        self.request_rearrange_cards(0)

    def _update_filter_button(self):
        from ui.i18n import t

        labels = {
            "all": t("channels.filter_tip.all"),
            "monitoring": t("channels.filter_tip.monitoring"),
            "recording": t("channels.filter_tip.recording"),
            "live": t("channels.filter_tip.live"),
            "paused": t("channels.filter_tip.paused"),
            "untagged": t("channels.filter_tip.untagged"),
        }
        label = labels.get(self.filter_mode)
        if self.filter_mode.startswith("tag:"):
            from core.config import get_tag
            tag = get_tag(self.filter_mode.split(":", 1)[1])
            label = t("channels.filter_tip.tag", name=(tag or {}).get("name", "?"))
        # 关键: 不再 setToolTip — 系统 tooltip 在 Win11 暗色主题下是黑底黑字。
        # 改用 _filter_tip / _sort_tip(由 _wire_hover 创建)动态更新文字。
        if hasattr(self, "_filter_tip"):
            self._filter_tip.setText(label or t("channels.filter"))

    def _update_sort_button(self):
        from ui.i18n import t

        labels = {
            "default": t("channels.sort_tip.default"),
            "status": t("channels.sort_tip.status"),
            "name": t("channels.sort_tip.name"),
            "room_id": t("channels.sort_tip.room_id"),
        }
        if hasattr(self, "_sort_tip"):
            self._sort_tip.setText(labels.get(self.sort_mode, t("channels.sort")))
    
    def _position_notification_container(self):
        """将通知容器定位到右下角"""
        if hasattr(self, 'notification_container') and self.notification_container.parent():
            parent = self.notification_container.parent()
            parent_size = parent.size()

            # 更新容器大小以适应内容
            self.notification_container.adjustSize()

            # 定位到右下角，留出边距
            container_size = self.notification_container.size()
            x = parent_size.width() - container_size.width() - 20
            y = parent_size.height() - container_size.height() - 20

            self.notification_container.move(x, y)
            self.notification_container.raise_()  # 确保在最上层

    def eventFilter(self, obj, event):
        """事件过滤器，用于响应窗口大小变化"""
        if obj == self.notification_container.parent() and event.type() == QEvent.Resize:
            self._position_notification_container()
        if obj == self.scroll.viewport() and event.type() == QEvent.Resize:
            self.request_rearrange_cards()
        return super().eventFilter(obj, event)

    def add_channel(self):
        from ui.settings_dialog import open_add_channel_overlay
        open_add_channel_overlay(self)

    # ==================== 信号槽 ====================
    def on_card_toggle(self, room_id, enabled):
        from ui.i18n import t

        uname = self.cards[room_id].room_info.get("uname", room_id) if room_id in self.cards else room_id
        if enabled:
            # 用户手动重新启用时重置自愈熔断与计数；自动重启不会经过这里
            self._heal_blocked.discard(room_id)
            self._heal_restart_times.pop(room_id, None)
            # 启监听 — 创建 QThread
            if room_id in self.cards:
                room_info = self.cards[room_id].room_info
                self._start_recorder(room_id, room_info)
            self.show_notification(
                t("notify.monitor_on_body", name=uname),
                t("notify.monitor_on_title"),
                "info",
                merge_key=f"monitor:on:{room_id}",
            )
        else:
            # 关监听 — 销毁 QThread
            self._stop_recorder(room_id)
            self.show_notification(
                t("notify.monitor_off_body", name=uname),
                t("notify.monitor_off_title"),
                "info",
                merge_key=f"monitor:off:{room_id}",
            )
        self.save_data()

    def on_cut(self, room_id):
        from ui.i18n import t

        if room_id in self.recorders and room_id in self.cards:
            if not self.cards[room_id].can_trigger_cut():
                self.show_notification(
                    t("notify.cut_disabled_body"),
                    t("notify.cut_disabled_title"),
                    "error",
                    merge_key=f"cut:disabled:{room_id}",
                )
                return
            self.recorders[room_id].trigger_cut()
            uname = self.cards[room_id].room_info.get("uname", room_id)
            self.show_notification(
                t("notify.cut_progress_body", name=uname),
                t("notify.cut_progress_title"),
                "info",
                merge_key=f"cut:progress:{room_id}",
                duration_ms=2200,
            )

    def on_cut_completed(self, room_id, file_name):
        from ui.i18n import t

        uname = self.cards[room_id].room_info.get("uname", room_id) if room_id in self.cards else room_id
        self.show_notification(
            t("notify.cut_ok_body", name=uname, file=file_name),
            t("notify.cut_ok_title"),
            "success",
            merge_key=f"cut:success:{room_id}:{file_name}",
            duration_ms=4200,
        )

    def on_cut_failed(self, room_id, error):
        from ui.i18n import t

        uname = self.cards[room_id].room_info.get("uname", room_id) if room_id in self.cards else room_id
        self.show_notification(
            t("notify.cut_fail_body", name=uname, error=error),
            t("notify.cut_fail_title"),
            "error",
            merge_key=f"cut:error:{room_id}:{error}",
            duration_ms=4500,
        )

    def on_delete(self, room_id):
        from ui.i18n import t

        if room_id not in self.cards:
            return

        uname = self.cards[room_id].room_info.get('uname', t("common.room"))

        # 如果在监听,先停掉(走 _stop_recorder 干净退出,会 wait QThread 退出)
        if room_id in self.recorders:
            self._stop_recorder(room_id)
        # 不在监听时,没 QThread,直接走 UI 清理

        self._pending_recorder_restarts.discard(room_id)

        # UI 清理 — 立即把卡片从 UI 拿掉(用户立刻看到反馈)
        self.cards[room_id].setParent(None)
        self.cards[room_id].deleteLater()
        del self.cards[room_id]

        # 重排 + 存盘 + 通知
        self.request_rearrange_cards(0)
        self.save_data()
        self.show_notification(
            t("notify.delete_body", name=uname),
            t("notify.delete_title"),
            "success",
            merge_key=f"delete:{room_id}",
        )

    def on_settings(self, room_id):
        from ui.i18n import t

        if room_id in self.cards:
            uname = self.cards[room_id].room_info.get('uname', t("common.room"))
            open_room_settings_overlay(room_id, uname, self)

    def on_open_folder(self, room_id):
        try:
            # 关键：跟实际录制用的 path_template / save_dir 完全同步，不要自己拼。
            # 1) 如果正在录或刚录过：current_save_path 是模板渲染后的**真实**路径，父目录绝对准
            if room_id in self.recorders and self.recorders[room_id].current_save_path:
                save_dir = os.path.dirname(self.recorders[room_id].current_save_path)
            else:
                # 2) 还没录过：用跟 _build_save_path **完全相同**的模板渲染逻辑预览
                uname = self.cards[room_id].room_info.get('uname', '') if room_id in self.cards else ''
                now_dt = datetime.datetime.now()
                save_dir = os.path.dirname(_preview_save_path(room_id, uname, "", now_dt))

                # 2a) 保底：如果日期子目录还不存在（没开播过、或日期变了），
                #     自动爬到**房间根目录**（`录播文件/<room_id>-<uname>`），
                #     这样既能看到历史日期的子目录、也不会让用户卡在"空文件夹"
                if not os.path.isdir(save_dir):
                    parent = save_dir
                    while parent and not os.path.isdir(parent):
                        new_parent = os.path.dirname(parent)
                        if new_parent == parent:
                            break  # 已经到根
                        parent = new_parent
                    # 如果找到了**包含日期之前的某一级**目录，就用它
                    if parent and os.path.isdir(parent):
                        save_dir = parent
                    # 如果连上一级都不存在（房间从来没录过），save_dir 就是房间根目录
                    #   os.makedirs 下面会创建

            # 确保目录存在
            os.makedirs(save_dir, exist_ok=True)

            # 打开文件夹
            if platform.system() == 'Windows':
                os.startfile(save_dir)
            elif platform.system() == 'Darwin':
                subprocess.Popen(['open', save_dir])
            else:
                subprocess.Popen(['xdg-open', save_dir])

        except Exception as e:
            logging.error(f"打开文件夹失败: {e}")
            from ui.i18n import t
            self.show_notification(
                t("notify.open_folder_fail_body"),
                t("notify.open_folder_fail_title"),
                "error",
                merge_key="open-folder:error",
            )

    def filter_cards(self):
        self.request_rearrange_cards(0)

    def save_data(self):
        channels = []
        for card in self.cards.values():
            channels.append({
                "room_id": card.room_id,
                "uname": card.room_info.get("uname"),
                "title": card.room_info.get("title"),
                "enabled": card.switch_btn.isChecked(),
                "face": card.room_info.get("face", ""),
                "parent_area_name": card.room_info.get("parent_area_name", ""),
                "area_name": card.room_info.get("area_name", "")
            })
        save_app_data(channels)

    def show_channels_page(self):
        self.current_page = "channels"
        self.page_stack.slide_to(self.channels_page)
        self._update_sidebar_nav_styles()
        self.request_rearrange_cards(0)

    def show_global_settings_page(self):
        self.current_page = "settings"
        self._ensure_global_settings_page()
        self.global_settings_page.load_settings()
        self.page_stack.slide_to(self.settings_page)
        self._update_sidebar_nav_styles()

    def show_global_settings(self):
        self.show_global_settings_page()

    def show_history_page(self):
        """切换到最近录制独立页面。"""
        self.current_page = "history"
        self._ensure_history_page()
        if self.history_page is not None and hasattr(self.history_page, "refresh"):
            self.history_page.refresh()
        self.page_stack.slide_to(self.history_page)
        self._update_sidebar_nav_styles()

    def show_about_page(self):
        """切换到关于页面（用 page_stack，和其他页面一致，不弹 dialog）"""
        self.current_page = "about"
        self._ensure_about_page()
        self.page_stack.slide_to(self.about_page)
        self._update_sidebar_nav_styles()

    def _build_about_page(self):
        """构建关于页面（信息卡片 + 操作按钮）"""
        from ui.i18n import t

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)

        # ==================== 顶部 toolbar ====================
        top_bar = QHBoxLayout()
        self.about_title_label = QLabel()
        self.about_title_label.setFont(QFont("Microsoft YaHei UI", 24, QFont.Bold))
        self.about_title_label.setProperty("role", "title")
        top_bar.addWidget(self.about_title_label)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # ==================== 信息卡片 ====================
        info_card = QFrame()
        info_card.setObjectName("aboutCard")
        enable_surface(info_card)
        card_layout = QVBoxLayout(info_card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(14)

        self._about_app_name = QLabel(t("about.app_name"))
        self._about_app_name.setFont(QFont("Microsoft YaHei UI", 20, QFont.Bold))
        self._about_app_name.setProperty("role", "title")

        sep = QFrame()
        sep.setObjectName("cardDivider")
        sep.setFrameShape(QFrame.HLine)

        self._about_info_label = QLabel()
        self._about_info_label.setProperty("role", "secondary")
        self._about_info_label.setStyleSheet("font-size: 14px; line-height: 1.8;")
        self._about_info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self._about_info_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self._refresh_about_info_label()

        card_layout.addWidget(self._about_app_name)
        card_layout.addWidget(sep)
        card_layout.addWidget(self._about_info_label)
        layout.addWidget(info_card)

        # ==================== 日志与诊断卡片 ====================
        log_card = QFrame()
        log_card.setObjectName("aboutCard")
        enable_surface(log_card)
        log_layout = QVBoxLayout(log_card)
        log_layout.setContentsMargins(28, 22, 28, 22)
        log_layout.setSpacing(12)

        self._about_logs_title = QLabel(t("about.logs"))
        self._about_logs_title.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
        self._about_logs_title.setProperty("role", "title")

        self._about_logs_desc = QLabel(t("about.logs_desc"))
        self._about_logs_desc.setProperty("role", "secondary")
        self._about_logs_desc.setWordWrap(True)

        log_path_label = QLabel(f"{LOG_FILE}")
        log_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        log_path_label.setWordWrap(True)
        log_path_label.setProperty("role", "muted")

        log_btn_row = QHBoxLayout()
        log_btn_row.setSpacing(10)
        self._about_open_logs_btn = QPushButton(t("about.open_logs"))
        self._about_open_log_dir_btn = QPushButton(t("about.open_log_dir"))
        for button in (self._about_open_logs_btn, self._about_open_log_dir_btn):
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(36)
            button.setProperty("variant", "secondary")
        log_btn_row.addWidget(self._about_open_logs_btn)
        log_btn_row.addWidget(self._about_open_log_dir_btn)
        log_btn_row.addStretch()

        def _view_current_log():
            open_log_viewer_overlay(self, LOG_FILE, CRASH_FILE)

        def _open_log_directory():
            try:
                os.makedirs(LOG_DIR, exist_ok=True)
                if sys.platform == "win32":
                    os.startfile(LOG_DIR)
                    return
                if QDesktopServices.openUrl(QUrl.fromLocalFile(LOG_DIR)):
                    return
                raise OSError("system rejected open-directory request")
            except Exception as exc:
                logging.error("打开日志文件夹失败: %s", exc)
                self.show_notification(
                    t("about.open_log_dir_failed", error=exc),
                    t("common.tip"), "error", merge_key="about:open-log-dir-failed"
                )

        self._about_open_logs_btn.clicked.connect(_view_current_log)
        self._about_open_log_dir_btn.clicked.connect(_open_log_directory)

        log_layout.addWidget(self._about_logs_title)
        log_layout.addWidget(self._about_logs_desc)
        log_layout.addWidget(log_path_label)
        log_layout.addLayout(log_btn_row)
        layout.addWidget(log_card)

        # ==================== 操作按钮区 ====================
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        self._about_help_btn = QPushButton("📖 " + t("help.open"))
        self._about_help_btn.setFixedHeight(40)
        self._about_help_btn.setCursor(Qt.PointingHandCursor)
        self._about_help_btn.setProperty("variant", "secondary")

        github_btn = QPushButton("🌐 GitHub")
        github_btn.setFixedHeight(40)
        github_btn.setCursor(Qt.PointingHandCursor)
        github_btn.setProperty("variant", "secondary")

        self._about_check_update_btn = QPushButton("🔄 " + t("about.check_update"))
        self._about_check_update_btn.setFixedHeight(40)
        self._about_check_update_btn.setCursor(Qt.PointingHandCursor)
        self._about_check_update_btn.setProperty("variant", "primary")

        btn_layout.addWidget(self._about_help_btn)
        btn_layout.addWidget(github_btn)
        btn_layout.addWidget(self._about_check_update_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addStretch()

        def open_help():
            from ui.help_dialog import open_help_overlay
            open_help_overlay(self)

        def open_github():
            QDesktopServices.openUrl(QUrl("https://github.com/ivanhih/dd-rec"))

        def check_for_update():
            if not IS_PORTABLE:
                self.show_notification(
                    "源码运行模式不支持自动更新，请使用 Portable 构建检查更新。",
                    t("common.tip"), "info", merge_key="about:update-unsupported"
                )
                return

            current_ver = get_local_version() or __version__

            def _on_done(info, err_msg):
                if info is not None:
                    show_update_dialog(info, self)
                else:
                    if err_msg:
                        self.show_notification(
                            f"检查更新失败: {err_msg[:80]}",
                            t("common.tip"), "error", merge_key="about:check-failed"
                        )
                    else:
                        self.show_notification(
                            f"当前已是最新版本 (v{current_ver})",
                            t("common.tip"), "success", merge_key="about:up-to-date"
                        )
                self._about_check_update_btn.setEnabled(True)
                self._about_check_update_btn.setText("🔄 " + t("about.check_update"))

            class _Sigs(QObject):
                done = Signal(object, str)

            sigs = _Sigs()
            sigs.done.connect(_on_done)

            def _do_check():
                try:
                    info = _check_update()
                    err_msg = ""
                except Exception as e:
                    logging.error(f"检查更新异常: {e}", exc_info=True)
                    info = None
                    err_msg = str(e)
                sigs.done.emit(info, err_msg)

            self._about_check_update_btn.setEnabled(False)
            self._about_check_update_btn.setText(t("about.checking"))
            import threading
            threading.Thread(target=_do_check, daemon=True).start()

        github_btn.clicked.connect(open_github)
        self._about_help_btn.clicked.connect(open_help)
        self._about_check_update_btn.clicked.connect(check_for_update)

        self.retranslate_ui()
        return page

    def _refresh_about_info_label(self):
        if not hasattr(self, "_about_info_label") or self._about_info_label is None:
            return
        from ui.i18n import t
        current_ver = get_local_version() or __version__
        if IS_PORTABLE:
            mode_str = t("about.mode_portable")
        elif not getattr(sys, "frozen", False):
            mode_str = t("about.mode_source")
        else:
            mode_str = t("about.mode_installed")
        self._about_info_label.setText(
            f"{t('about.version', version=current_ver)}\n"
            f"{t('about.mode', mode=mode_str)}\n"
            f"GitHub:    github.com/ivanhih/dd-rec"
        )

    def start_refresh_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(2000)

    def refresh_all(self):
        pass

    def closeEvent(self, event):
        """点 X → 隐藏到系统托盘（不退出）。

        只有通过托盘右键"退出"才会真正退出进程。
        """
        if self._is_shutting_down:
            # 真正退出：停止防止休眠线程 + 恢复电源策略
            try:
                self._power_keepalive.stop()
            except Exception:
                pass
            event.accept()
            return
        # 阻止窗口关闭，隐藏到托盘
        event.ignore()
        self.hide()


def apply_startup_theme(app):
    """创建 QApplication 后、MainWindow 首帧前应用持久化主题（不含回退写配置）。"""
    try:
        saved = get_global_setting("theme")
    except Exception:
        saved = None
    return theme_manager().apply(app, saved)


def apply_startup_language():
    """创建 QApplication 后、构建 UI 前应用持久化语言（不含回退写配置）。"""
    try:
        saved = get_global_setting("language")
    except Exception:
        saved = None
    from ui.i18n import set_language
    return set_language(saved)


if __name__ == "__main__":
    import logging
    logging.info(f"DD录播机 v{__version__} 启动中...")

    _set_windows_app_user_model_id()
    app = QApplication(sys.argv)
    app.setWindowIcon(_load_app_icon())
    app.setQuitOnLastWindowClosed(False)

    # 首帧前应用已保存主题，浅色启动不会先闪深色
    apply_startup_theme(app)
    # 构建 UI 前应用已保存语言
    apply_startup_language()

    # 单窗口启动：主窗口立即显示，开屏覆盖层在其内部播放动画
    window = MainWindow(show_startup_overlay=True)
    window.show()

    # 进入一次事件循环后再开始重初始化，保证第一帧立即绘制
    QTimer.singleShot(0, window.bootstrap)

    # 窗口就绪后异步检查更新（线程版，避免主线程网络请求卡首屏）
    class _UpdateCheckSignals(QObject):
        done = Signal(object, str)  # (info, err_msg)

    _update_sigs = _UpdateCheckSignals()

    def _on_update_done(info, err_msg):
        if info:
            logging.info(f"[auto-update] 发现新版本 v{info.version}，弹出更新 dialog")
            show_update_dialog(info, window)
        else:
            logging.info(f"[auto-update] 已是最新版本或检查失败 {err_msg}".strip())

    _update_sigs.done.connect(_on_update_done)

    def check_update_async():
        def _do_check():
            try:
                logging.info("[auto-update] 开始检查更新...")
                info = _check_update()
                _update_sigs.done.emit(info, "")
            except Exception as e:
                # 任何异常都不能阻断应用启动
                logging.error(f"[auto-update] 检查更新异常: {e}", exc_info=True)
                _update_sigs.done.emit(None, str(e))
        # 后台线程跑网络请求，Signal 自动切回主线程
        import threading
        threading.Thread(target=_do_check, daemon=True).start()

    if IS_PORTABLE:
        QTimer.singleShot(800, check_update_async)

    sys.exit(app.exec())