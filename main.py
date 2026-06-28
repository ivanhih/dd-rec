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
from logging.handlers import RotatingFileHandler
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QScrollArea, QLineEdit, QPushButton, QLabel, QGridLayout,
    QFrame, QToolButton, QGraphicsDropShadowEffect, QSizePolicy,
    QGraphicsOpacityEffect, QMenu, QStackedWidget, QSystemTrayIcon,
    QDialog, QMessageBox, QCheckBox
)
from PySide6.QtCore import QTimer, Qt, QThread, QObject, QPropertyAnimation, QEasingCurve, QPoint, QEvent, Signal, QUrl
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

# 插件系统
from plugins import PluginManager
from plugins.page import PluginsPage
from plugins.host import PluginHostBar, PluginStackController


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

_stdout_handler = logging.StreamHandler(stream=sys.stdout)
if hasattr(_stdout_handler.stream, 'reconfigure'):
    _stdout_handler.stream.reconfigure(encoding='utf-8')

_file_handler = RotatingFileHandler(
    LOG_FILE,
    maxBytes=5 * 1024 * 1024,  # 5MB
    backupCount=5,             # 保留 5 个备份
    encoding="utf-8",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[_stdout_handler, _file_handler],
)


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
        "success": {
            "bg": "#10261A",
            "border": "#22C55E",
            "accent": "#4ADE80",
            "title": "#86EFAC",
            "icon": "✓",
        },
        "info": {
            "bg": "#122033",
            "border": "#3B82F6",
            "accent": "#60A5FA",
            "title": "#93C5FD",
            "icon": "i",
        },
        "error": {
            "bg": "#2A1417",
            "border": "#EF4444",
            "accent": "#F87171",
            "title": "#FCA5A5",
            "icon": "!",
        },
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
        self.setMinimumWidth(340)
        self.setMaximumWidth(340)
        self.setStyleSheet(f"""
            QWidget#notificationCard {{
                background-color: {colors['bg']};
                border: 1px solid {colors['border']};
                border-radius: 10px;
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 12, 12)
        layout.setSpacing(10)

        accent = QFrame()
        accent.setFixedWidth(4)
        accent.setStyleSheet(
            f"background-color: {colors['accent']}; border: none; border-radius: 2px;"
        )

        icon_label = QLabel(colors["icon"])
        icon_label.setFixedSize(28, 28)
        icon_label.setAlignment(Qt.AlignCenter)
        icon_label.setStyleSheet(f"""
            color: #F8FAFC;
            background-color: {colors['accent']};
            border-radius: 14px;
            font-size: 14px;
            font-weight: 700;
            border: none;
        """)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(4)
        text_layout.setContentsMargins(0, 0, 0, 0)

        title_row = QHBoxLayout()
        title_row.setSpacing(8)
        title_row.setContentsMargins(0, 0, 0, 0)

        self.title_label = QLabel()
        self.title_label.setStyleSheet(
            f"color: {colors['title']}; font-size: 14px; font-weight: 600; border: none; background: transparent;"
        )
        self.title_label.setWordWrap(True)

        self.count_label = QLabel()
        self.count_label.setAlignment(Qt.AlignCenter)
        self.count_label.setMinimumWidth(28)
        self.count_label.setStyleSheet(f"""
            color: #F8FAFC;
            background-color: {colors['accent']};
            border-radius: 9px;
            font-size: 11px;
            font-weight: 700;
            padding: 1px 6px;
        """)
        self.count_label.hide()

        self.message_label = QLabel()
        self.message_label.setStyleSheet(
            "color: #E5E7EB; font-size: 13px; border: none; background: transparent;"
        )
        self.message_label.setWordWrap(True)

        close_btn = QToolButton()
        close_btn.setText("✕")
        close_btn.setFixedSize(24, 24)
        close_btn.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                color: #94A3B8;
                border-radius: 4px;
                font-size: 14px;
                border: none;
            }
            QToolButton:hover {
                color: white;
                background-color: #334155;
            }
        """)
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

    def __init__(self):
        super().__init__()
        self.setWindowTitle("DD录播机")
        self.resize(1360, 780)
        self.setStyleSheet("background-color: #0F0F13;")

        # 生成并设置窗口图标（任务栏、窗口左上角统一用 DD 图标）
        window_icon = QIcon()
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.LosslessImageRendering)
        p.setBrush(QColor("#3B82F6"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, 56, 56)
        p.setPen(QColor("white"))
        font = QFont("Microsoft YaHei UI", 26, QFont.Bold)
        font.setStyleHint(QFont.SansSerif)
        p.setFont(font)
        p.drawText(pix.rect(), Qt.AlignCenter, "DD")
        p.end()
        window_icon.addPixmap(pix)
        self.setWindowIcon(window_icon)

        self.cards = {}           # room_id -> RoomCard
        self.threads = {}         # room_id -> QThread
        self.recorders = {}       # room_id -> BiliRecorder
        # 关监听/删除时,QThread 不能立即销毁(还在跑会段错误),先移到这里异步等退出
        self._dying_threads = []  # [(thread, recorder), ...]

        # 通知队列
        self.notifications = []
        self.notification_lookup = {}
        self.filter_mode = "all"
        self.sort_mode = "default"
        self._last_grid_signature = None
        self._layout_ready = False
        self.current_page = "channels"
        self._is_shutting_down = False

        self.setup_ui()
        self._center_window()  # 窗口居中
        self._setup_tray()
        ensure_default_config()  # config.json 不存在时生成默认配置
        self.load_saved_data()
        self.start_refresh_timer()

        # ==================== 电源管理 ====================
        # 防止电脑休眠：按 prevent_sleep 配置启动守护线程
        self._power_keepalive = PowerKeepAlive()
        if get_global_setting("prevent_sleep"):
            self._power_keepalive.start()
        # 开机自启：按 auto_start 配置同步注册表
        if get_global_setting("auto_start"):
            set_auto_start(True)

    def _center_window(self):
        """将窗口居中显示"""
        screen = self.screen()
        if screen:
            screen_geometry = screen.geometry()
            window_geometry = self.geometry()
            x = (screen_geometry.width() - window_geometry.width()) // 2 + screen_geometry.x()
            y = (screen_geometry.height() - window_geometry.height()) // 2 + screen_geometry.y()
            self.move(x, y)

    def _create_toolbar_icon(self, kind, color="#94A3B8"):
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

    def _update_sidebar_nav_styles(self):
        active_style = """
            QToolButton {
                background-color: #252631;
                color: #E2E8F0;
                border-radius: 12px;
                font-size: 24px;
                border: none;
            }
            QToolButton:hover {
                background-color: #2D2E3A;
            }
        """
        inactive_style = """
            QToolButton {
                background-color: transparent;
                color: #64748B;
                border-radius: 12px;
                font-size: 24px;
                border: none;
            }
            QToolButton:hover {
                background-color: #252631;
                color: #94A3B8;
            }
        """
        self.nav_channels_btn.setStyleSheet(active_style if self.current_page == "channels" else inactive_style)
        self.nav_plugins_btn.setStyleSheet(active_style if self.current_page == "plugins" else inactive_style)
        self.nav_settings_btn.setStyleSheet(active_style if self.current_page == "settings" else inactive_style)
        self.nav_about_btn.setStyleSheet(active_style if self.current_page == "about" else inactive_style)

    def setup_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QHBoxLayout(central)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # ==================== 侧边栏 ====================
        sidebar = QWidget()
        sidebar.setFixedWidth(72)
        sidebar.setStyleSheet("background-color: #1A1B21;")
        
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(8, 20, 8, 20)
        sidebar_layout.setSpacing(16)
        
        # Logo/图标按钮
        logo_btn = QToolButton()
        logo_btn.setText("🎮")
        logo_btn.setFixedSize(56, 56)
        logo_btn.setStyleSheet("""
            QToolButton {
                background-color: #3b82f6;
                color: white;
                border-radius: 12px;
                font-size: 24px;
            }
        """)
        
        self.nav_channels_btn = self._create_sidebar_nav_button("📋", "频道")
        self._wire_hover(self.nav_channels_btn, "频道")
        self.nav_channels_btn.clicked.connect(self.show_channels_page)
        self.nav_plugins_btn = self._create_sidebar_nav_button("🧩", "插件")
        self._wire_hover(self.nav_plugins_btn, "插件")
        self.nav_plugins_btn.clicked.connect(self.show_plugins_page)
        self.nav_settings_btn = self._create_sidebar_nav_button("⚙️", "全局设置")
        self._wire_hover(self.nav_settings_btn, "全局设置")
        self.nav_settings_btn.clicked.connect(self.show_global_settings_page)
        self.nav_about_btn = self._create_sidebar_nav_button("ℹ️", "关于")
        self._wire_hover(self.nav_about_btn, "关于")
        self.nav_about_btn.clicked.connect(self.show_about_page)

        sidebar_layout.addWidget(logo_btn)
        sidebar_layout.addWidget(self.nav_channels_btn)
        sidebar_layout.addWidget(self.nav_plugins_btn)

        # 插件宿主 sidebar 区 —— 已启用插件的图标会动态追加到这里
        # 注:plugin_manager 还在后面才创建,先放占位 widget,稍后回填
        self.plugin_host_bar = PluginHostBar(None)
        sidebar_layout.addWidget(self.plugin_host_bar)

        sidebar_layout.addWidget(self.nav_settings_btn)
        sidebar_layout.addStretch()
        sidebar_layout.addWidget(self.nav_about_btn)
        self._update_sidebar_nav_styles()

        # ==================== 内容区 ====================
        self.page_stack = QStackedWidget()
        self.page_stack.setStyleSheet("background-color: transparent; border: none;")

        self.channels_page = QWidget()
        content_layout = QVBoxLayout(self.channels_page)
        content_layout.setContentsMargins(32, 24, 32, 24)
        content_layout.setSpacing(20)

        # 顶部工具栏
        top_bar = QHBoxLayout()
        
        # 标题
        title_label = QLabel("频道")
        title_label.setFont(QFont("Microsoft YaHei UI", 24, QFont.Bold))
        title_label.setStyleSheet("color: #F8FAFC;")
        
        # 搜索框
        self.search_input = QLineEdit()
        self.search_input.setFixedWidth(320)
        self.search_input.setPlaceholderText("🔍 搜索")
        self.search_input.setStyleSheet("""
            QLineEdit {
                background-color: #181920;
                border: 1px solid #2D2E3A;
                border-radius: 10px;
                padding: 10px 16px;
                color: #E2E8F0;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #3B82F6;
            }
        """)
        self.search_input.textChanged.connect(self.filter_cards)

        # 过滤和排序按钮
        self.filter_btn = _HoverToolButton("", "过滤")
        self.filter_btn.setFixedSize(44, 44)
        self.filter_btn.setIcon(self._create_toolbar_icon("filter"))
        self.filter_btn.setIconSize(QPixmap(20, 20).size())
        self.filter_btn.setPopupMode(QToolButton.InstantPopup)
        self._wire_hover(self.filter_btn, "过滤", attr_name="_filter_tip")
        self.filter_btn.setStyleSheet("""
            QToolButton {
                background-color: #181920;
                border-radius: 10px;
                border: none;
            }
            QToolButton:hover {
                background-color: #252631;
            }
        """)

        self.sort_btn = _HoverToolButton("", "排序")
        self.sort_btn.setFixedSize(44, 44)
        self.sort_btn.setIcon(self._create_toolbar_icon("sort"))
        self.sort_btn.setIconSize(QPixmap(20, 20).size())
        self.sort_btn.setPopupMode(QToolButton.InstantPopup)
        self._wire_hover(self.sort_btn, "排序", attr_name="_sort_tip")
        self.sort_btn.setStyleSheet("""
            QToolButton {
                background-color: #181920;
                border-radius: 10px;
                border: none;
            }
            QToolButton:hover {
                background-color: #252631;
            }
        """)
        
        # 添加按钮
        self.btn_add = _HoverPushButton("添加", "添加直播间")
        self.btn_add.setFixedHeight(44)
        self.btn_add.setMinimumWidth(100)
        self.btn_add.setStyleSheet("""
            QPushButton {
                background-color: #3B82F6;
                color: white;
                border-radius: 10px;
                padding: 10px 24px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2563EB;
            }
        """)
        self._wire_hover(self.btn_add, "添加直播间")
        self.btn_add.clicked.connect(self.add_channel)

        top_bar.addWidget(title_label)
        top_bar.addStretch()
        top_bar.addWidget(self.search_input)
        top_bar.addWidget(self.filter_btn)
        top_bar.addWidget(self.sort_btn)
        top_bar.addWidget(self.btn_add)

        content_layout.addLayout(top_bar)

        # 卡片滚动区域
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                background-color: #15161D;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background-color: #3B3D4F;
                border-radius: 4px;
                min-height: 40px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
        """)

        self.cards_widget = QWidget()
        self.grid_layout = QGridLayout(self.cards_widget)
        self.grid_layout.setSpacing(24)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        self.scroll.setWidget(self.cards_widget)
        content_layout.addWidget(self.scroll)

        self.settings_page = QWidget()
        settings_layout = QVBoxLayout(self.settings_page)
        settings_layout.setContentsMargins(32, 24, 32, 24)
        settings_layout.setSpacing(20)

        self.global_settings_page = GlobalSettingsOldStyleReplicaPage(self.settings_page)
        self.global_settings_page.saved.connect(
            lambda: self.show_notification("全局设置已保存", "保存成功", "success", merge_key="global-settings:saved")
        )
        settings_layout.addWidget(self.global_settings_page)

        self.page_stack.addWidget(self.channels_page)
        self.page_stack.addWidget(self.settings_page)

        # 关于页面（不是 dialog，是和频道/插件/全局设置一样的主内容页面）
        self.about_page = self._build_about_page()
        self._about_page_index = self.page_stack.addWidget(self.about_page)

        # 插件页面（懒加载）
        self.plugins_page = None
        self._plugins_page_index = -1

        main_layout.addWidget(sidebar)
        main_layout.addWidget(self.page_stack, 1)

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

        # 插件管理器
        self.plugin_manager = PluginManager()
        self.plugin_manager.initialize(self)
        # 把 manager 回填到 host bar(它构造时还没有 manager)
        self.plugin_host_bar.set_plugin_manager(self.plugin_manager)

        # 插件宿主:把已启用插件挂到 sidebar + page_stack
        self._plugin_stack_controller = PluginStackController(
            self.plugin_manager, self.page_stack, self.plugin_host_bar, parent=self
        )
        self._plugin_stack_controller.refresh_from_manager()

        # 初始定位
        self._position_notification_container()

    def _setup_tray(self):
        """系统托盘：X 隐藏到托盘，右键菜单显示/退出。"""
        self._tray_menu = QMenu()

        self._tray_show_action = QAction("显示")
        self._tray_show_action.triggered.connect(self._tray_show)
        self._tray_menu.addAction(self._tray_show_action)

        self._tray_quit_action = QAction("退出")
        self._tray_quit_action.triggered.connect(self._tray_quit)
        self._tray_menu.addAction(self._tray_quit_action)

        self._tray_icon = QSystemTrayIcon(self)
        self._tray_icon.setToolTip("DD录播机")
        self._tray_icon.setContextMenu(self._tray_menu)
        self._tray_icon.activated.connect(self._on_tray_activated)

        # 生成 DD 托盘图标：蓝底白字圆形
        icon = QIcon()
        pix = QPixmap(64, 64)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.LosslessImageRendering)
        p.setBrush(QColor("#3B82F6"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(4, 4, 56, 56)
        p.setPen(QColor("white"))
        font = QFont("Microsoft YaHei UI", 26, QFont.Bold)
        font.setStyleHint(QFont.SansSerif)
        p.setFont(font)
        p.drawText(pix.rect(), Qt.AlignCenter, "DD")
        p.end()
        icon.addPixmap(pix)
        self._tray_icon.setIcon(icon)
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
        """托盘 -> 退出程序（直接退出，操作系统会清理所有线程和子进程）。"""
        self._is_shutting_down = True
        # 停所有 recorder（发退出信号让线程自然停）
        for room_id in list(self.recorders.keys()):
            self._stop_recorder(room_id)
        # 隐藏窗口 + 托盘
        self.hide()
        if self._tray_icon:
            self._tray_icon.hide()
        # 直接 quit()，不等线程退出。操作系统会在进程退出时强制杀掉所有线程和 ffmpeg 子进程。
        QApplication.instance().quit()

    def show_notification(self, message, title="提示", level="info", merge_key=None, duration_ms=3200):
        """显示通知"""
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

        card = self.cards.get(room_id)
        if not card:
            return

        recorder = BiliRecorder(room_info)
        thread = QThread()
        recorder.moveToThread(thread)

        # 状态信号连到卡片
        recorder.status_updated.connect(
            lambda m, l, r, t, d, sp, sz, p, a:
            card.update_status(m, l, r, t, d, sp, sz, p, a)
        )
        recorder.cut_completed.connect(self.on_cut_completed)
        recorder.cut_failed.connect(self.on_cut_failed)

        thread.started.connect(recorder.run)
        thread.start()

        self.recorders[room_id] = recorder
        self.threads[room_id] = thread

    def _stop_recorder(self, room_id):
        """停掉房间的录播监控 — 异步销毁 BiliRecorder + QThread,不阻塞 GUI。

        关键策略 — 用户视角立即响应,QThread 后台异步退出:
          1) 主线程关 ffmpeg(stdin 'q',跨线程安全)
          2) 清空 current_save_path / current_session_id 避免 run loop 触发转码
          3) 设 is_monitoring=False + is_running=False
          4) **不调 thread.wait()** — 立即从 self.recorders / self.threads 移到
             self._dying_threads(主窗口持有,避免 GC 销毁还在跑的 QThread)
          5) 用 thread.finished 信号异步清理 _dying_threads
          6) 主窗口 closeEvent 时 wait 所有 _dying_threads
        """
        if room_id not in self.recorders:
            return

        recorder = self.recorders[room_id]
        thread = self.threads.get(room_id)

        # 1) 主线程关 ffmpeg(stdin 'q')
        if recorder.current_ffmpeg is not None:
            try:
                if recorder.current_ffmpeg.stdin and not recorder.current_ffmpeg.stdin.closed:
                    recorder.current_ffmpeg.stdin.write(b"q\n")
                    recorder.current_ffmpeg.stdin.flush()
            except Exception as e:
                logging.debug(f"主线程关 ffmpeg 失败: {e}")

        # 2) 清空触发转码的状态（ffmpeg 已收到 stdin 'q', 不再阻塞等待）
        recorder.current_ffmpeg = None
        recorder.current_save_path = None
        recorder.current_session_id = ""

        # 3) 设标志让 run loop 自然退
        recorder.is_monitoring = False
        recorder.is_running = False

        # 4) 立即从 self.recorders / self.threads 删除(用户视角"已停止")
        #    QThread 移到 self._dying_threads,主窗口持有它避免 Python GC 销毁
        del self.recorders[room_id]
        if room_id in self.threads:
            del self.threads[room_id]

        # 卡片状态更新 — recorder 已停不会再发 status_updated 信号,手动设
        if room_id in self.cards:
            card = self.cards[room_id]
            card.update_status(
                "⚙️ 已暂停", "🌙 未开播", "⏳ 闲置中",
                card.room_info.get("title", ""), "", "", "",
                card.room_info.get("parent_area_name", ""),
                card.room_info.get("area_name", "")
            )

        if thread is not None:
            # 用 list 持有(避免 dict key 冲突)
            if not hasattr(self, '_dying_threads'):
                self._dying_threads = []
            self._dying_threads.append((thread, recorder))

            # 5) thread.finished 信号触发时,从 _dying_threads 移除并 deleteLater
            def _cleanup(t=thread, r=recorder):
                try:
                    self._dying_threads = [
                        (tt, rr) for (tt, rr) in self._dying_threads if tt is not t
                    ]
                except Exception:
                    pass
                t.deleteLater()
                r.deleteLater()

            thread.finished.connect(_cleanup)
        else:
            recorder.deleteLater()

    def load_saved_data(self):
        data = load_app_data()
        self._layout_ready = False
        for ch in data.get("channels", []):
            self.add_card(ch, save=False, rearrange=False)
        self._layout_ready = True
        self.request_rearrange_cards(0)

    def _init_plugins(self):
        """初始化插件系统（懒加载插件页面）"""
        if self.plugins_page is None:
            self.plugins_page = PluginsPage(self.plugin_manager)
            self._plugins_page_index = self.page_stack.addWidget(self.plugins_page)
        # 启动时让已启用的插件在 sidebar 自动出现
        if getattr(self, "_plugin_stack_controller", None) is not None:
            self._plugin_stack_controller.refresh_from_manager()

    # ==================== 插件宿主 API（PluginContext 委托调用） ====================
    def get_save_dir(self) -> str:
        """全局默认录播目录。"""
        return VIDEO_SAVE_DIR

    def get_effective_save_dir_for_room(self, room_id: str, uname: str) -> str:
        """按主程序一致的规则解析某直播间的录播目录。"""
        from core.config import get_effective_save_dir as _gesd
        return _gesd(room_id, uname)

    def get_ffmpeg_cmd(self) -> str:
        """返回当前可用的 ffmpeg 可执行路径。"""
        from core.recorder import FFMPEG_CMD
        return FFMPEG_CMD

    def list_known_rooms(self) -> list:
        """返回已添加的直播间列表 [{room_id, uname, ...}]。"""
        try:
            data = load_app_data() or {}
        except Exception:
            return []
        return list(data.get("channels", []) or [])

    def show_plugins_page(self):
        """显示插件页面"""
        self._init_plugins()  # 确保插件页面已初始化
        self.current_page = "plugins"
        self.page_stack.setCurrentIndex(self._plugins_page_index)
        self._update_sidebar_nav_styles()
        self.plugins_page.refresh()

    def add_card(self, room_info: dict, save=True, rearrange=True):
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
        if room_info.get("enabled", True):
            self._start_recorder(room_id, room_info)
        else:
            # enabled=False 时 recorder 不启动，不会发 status_updated，手动设一次初始状态
            card.update_status(
                "⚙️ 已暂停", "🌙 未开播", "⏳ 闲置中",
                room_info.get("title", ""), "", "", "",
                room_info.get("parent_area_name", ""),
                room_info.get("area_name", "")
            )

        if rearrange:
            self.request_rearrange_cards(0)

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
        search_hit = (
            text in card.room_info.get("uname", "").lower() or
            text in card.room_id or
            text in card.room_info.get("title", "").lower()
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
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #181920;
                color: #E2E8F0;
                border: 1px solid #2D2E3A;
                padding: 8px;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: #252631;
            }
        """)

        group = QActionGroup(self)
        group.setExclusive(True)
        options = [
            ("all", "全部频道"),
            ("monitoring", "仅监控中"),
            ("recording", "仅录制中"),
            ("live", "仅直播中"),
            ("paused", "仅已暂停"),
        ]
        for key, label in options:
            action = menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self.filter_mode)
            action.triggered.connect(lambda checked=False, mode=key: self._set_filter_mode(mode))
            group.addAction(action)

        self.filter_btn.setMenu(menu)
        self._update_filter_button()

    def _setup_sort_menu(self):
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #181920;
                color: #E2E8F0;
                border: 1px solid #2D2E3A;
                padding: 8px;
            }
            QMenu::item {
                padding: 8px 16px;
                border-radius: 6px;
            }
            QMenu::item:selected {
                background-color: #252631;
            }
        """)

        group = QActionGroup(self)
        group.setExclusive(True)
        options = [
            ("default", "默认顺序"),
            ("status", "按状态优先"),
            ("name", "按名称排序"),
            ("room_id", "按房间号排序"),
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
        labels = {
            "all": "过滤: 全部",
            "monitoring": "过滤: 监控中",
            "recording": "过滤: 录制中",
            "live": "过滤: 直播中",
            "paused": "过滤: 已暂停",
        }
        # 关键: 不再 setToolTip — 系统 tooltip 在 Win11 暗色主题下是黑底黑字。
        # 改用 _filter_tip / _sort_tip(由 _wire_hover 创建)动态更新文字。
        if hasattr(self, "_filter_tip"):
            self._filter_tip.setText(labels.get(self.filter_mode, "过滤"))

    def _update_sort_button(self):
        labels = {
            "default": "排序: 默认",
            "status": "排序: 状态优先",
            "name": "排序: 名称",
            "room_id": "排序: 房间号",
        }
        if hasattr(self, "_sort_tip"):
            self._sort_tip.setText(labels.get(self.sort_mode, "排序"))
    
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
        uname = self.cards[room_id].room_info.get("uname", room_id) if room_id in self.cards else room_id
        if enabled:
            # 启监听 — 创建 QThread
            if room_id in self.cards:
                room_info = self.cards[room_id].room_info
                self._start_recorder(room_id, room_info)
            self.show_notification(f"{uname} 已开始监控", "开始监控", "info", merge_key=f"monitor:on:{room_id}")
        else:
            # 关监听 — 销毁 QThread
            self._stop_recorder(room_id)
            self.show_notification(f"{uname} 已关闭监控", "关闭监控", "info", merge_key=f"monitor:off:{room_id}")
        self.save_data()

    def on_cut(self, room_id):
        if room_id in self.recorders and room_id in self.cards:
            if not self.cards[room_id].can_trigger_cut():
                self.show_notification("当前未处于录制中，无法切割", "切割不可用", "error", merge_key=f"cut:disabled:{room_id}")
                return
            self.recorders[room_id].trigger_cut()
            uname = self.cards[room_id].room_info.get("uname", room_id)
            self.show_notification(f"{uname} 正在切割当前录播文件", "切割中", "info", merge_key=f"cut:progress:{room_id}", duration_ms=2200)

    def on_cut_completed(self, room_id, file_name):
        uname = self.cards[room_id].room_info.get("uname", room_id) if room_id in self.cards else room_id
        self.show_notification(f"{uname} 切割完成：{file_name}", "切割成功", "success", merge_key=f"cut:success:{room_id}:{file_name}", duration_ms=4200)

    def on_cut_failed(self, room_id, error):
        uname = self.cards[room_id].room_info.get("uname", room_id) if room_id in self.cards else room_id
        self.show_notification(f"{uname} 切割失败：{error}", "切割失败", "error", merge_key=f"cut:error:{room_id}:{error}", duration_ms=4500)

    def on_delete(self, room_id):
        if room_id not in self.cards:
            return

        uname = self.cards[room_id].room_info.get('uname', '房间')

        # 如果在监听,先停掉(走 _stop_recorder 干净退出,会 wait QThread 退出)
        if room_id in self.recorders:
            self._stop_recorder(room_id)
        # 不在监听时,没 QThread,直接走 UI 清理

        # UI 清理 — 立即把卡片从 UI 拿掉(用户立刻看到反馈)
        self.cards[room_id].setParent(None)
        self.cards[room_id].deleteLater()
        del self.cards[room_id]

        # 重排 + 存盘 + 通知
        self.request_rearrange_cards(0)
        self.save_data()
        self.show_notification(f"已删除 {uname}", "删除成功", "success", merge_key=f"delete:{room_id}")

    def on_settings(self, room_id):
        if room_id in self.cards:
            uname = self.cards[room_id].room_info.get('uname', '房间')
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
            self.show_notification("打开文件夹失败", "错误", "error", merge_key="open-folder:error")

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
        self.page_stack.setCurrentWidget(self.channels_page)
        self._update_sidebar_nav_styles()
        self.request_rearrange_cards(0)

    def show_global_settings_page(self):
        self.current_page = "settings"
        self.global_settings_page.load_settings()
        self.page_stack.setCurrentWidget(self.settings_page)
        self._update_sidebar_nav_styles()

    def show_global_settings(self):
        self.show_global_settings_page()

    def show_about_page(self):
        """切换到关于页面（用 page_stack，和其他页面一致，不弹 dialog）"""
        self.current_page = "about"
        self.page_stack.setCurrentWidget(self.about_page)
        self._update_sidebar_nav_styles()

    def _build_about_page(self):
        """构建关于页面（信息卡片 + 操作按钮）"""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(32, 24, 32, 24)
        layout.setSpacing(20)

        # ==================== 顶部 toolbar ====================
        top_bar = QHBoxLayout()
        title_label = QLabel("关于")
        title_label.setFont(QFont("Microsoft YaHei UI", 24, QFont.Bold))
        title_label.setStyleSheet("color: #F8FAFC;")
        top_bar.addWidget(title_label)
        top_bar.addStretch()
        layout.addLayout(top_bar)

        # ==================== 信息卡片 ====================
        info_card = QFrame()
        info_card.setObjectName("aboutInfoCard")
        info_card.setStyleSheet("""
            QFrame#aboutInfoCard {
                background-color: #181920;
                border: 1px solid #2D2E3A;
                border-radius: 12px;
            }
        """)
        card_layout = QVBoxLayout(info_card)
        card_layout.setContentsMargins(28, 24, 28, 24)
        card_layout.setSpacing(14)

        app_name = QLabel("DD录播机")
        app_name.setFont(QFont("Microsoft YaHei UI", 20, QFont.Bold))
        app_name.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setStyleSheet(
            "color: #2D2E3A; background-color: #2D2E3A; border: none; max-height: 1px;"
        )

        current_ver = get_local_version() or __version__
        mode_str = "Portable" if IS_PORTABLE else "安装版"
        info_label = QLabel(
            f"当前版本:  v{current_ver}\n"
            f"运行模式:  {mode_str}\n"
            f"GitHub:    github.com/ivanhih/dd-rec"
        )
        info_label.setStyleSheet(
            "color: #94A3B8; background: transparent; border: none; "
            "font-size: 14px; line-height: 1.8;"
        )
        info_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_label.setAlignment(Qt.AlignTop | Qt.AlignLeft)

        card_layout.addWidget(app_name)
        card_layout.addWidget(sep)
        card_layout.addWidget(info_label)
        layout.addWidget(info_card)

        # ==================== Mirror 酱设置卡片 ====================
        mirror_card = QFrame()
        mirror_card.setObjectName("mirrorChyanCard")
        mirror_card.setStyleSheet("""
            QFrame#mirrorChyanCard {
                background-color: #181920;
                border: 1px solid #2D2E3A;
                border-radius: 12px;
            }
        """)
        mirror_layout = QVBoxLayout(mirror_card)
        mirror_layout.setContentsMargins(28, 24, 28, 24)
        mirror_layout.setSpacing(12)

        mirror_title = QLabel("Mirror 酱设置")
        mirror_title.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
        mirror_title.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")

        mirror_desc = QLabel(
            "Mirror 酱是国内 CDN 加速服务。当 GitHub 不可达时,\n"
            "DD录播机 可通过 Mirror 酱 API 获取最新版本号（开发者不参与付费流程）。\n"
            "用户自行购买 CDK 后,在下方填入即可启用。"
        )
        mirror_desc.setStyleSheet(
            "color: #94A3B8; font-size: 13px; background: transparent; border: none; "
            "line-height: 1.6;"
        )

        mirror_enable = QCheckBox("启用 Mirror 酱（GitHub 不可达时作为 fallback）")
        mirror_enable.setChecked(bool(get_global_setting("mirror_chyan_enabled")))
        mirror_enable.setStyleSheet("""
            QCheckBox {
                color: #E2E8F0;
                background: transparent;
                spacing: 8px;
                padding: 4px 0;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
        """)

        cdk_input = QLineEdit()
        cdk_input.setEchoMode(QLineEdit.EchoMode.Password)
        cdk_input.setText(str(get_global_setting("mirror_chyan_cdk") or ""))
        cdk_input.setPlaceholderText("留空表示仅用 GitHub 检查（Mirror 酱 API 仍可查版本，无 CDK 时不返回 url）")

        cdk_warning = QLabel("⚠️ CDK 以明文存储于本地 config.json,请勿在公用电脑使用")
        cdk_warning.setStyleSheet(
            "color: #F59E0B; font-size: 12px; background: transparent; border: none;"
        )

        get_cdk_btn = QPushButton("获取 CDK →")
        get_cdk_btn.setCursor(Qt.PointingHandCursor)
        get_cdk_btn.setFixedHeight(34)
        get_cdk_btn.setStyleSheet("""
            QPushButton {
                background-color: #252631;
                color: #CBD5E1;
                border: 1px solid #2D2E3A;
                border-radius: 6px;
                font-size: 12px;
                font-weight: 500;
                padding: 0 14px;
            }
            QPushButton:hover {
                background-color: #2D2E3A;
                color: #F8FAFC;
                border: 1px solid #475569;
            }
        """)
        get_cdk_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl("https://mirrorchyan.com"))
        )

        cdk_row = QHBoxLayout()
        cdk_row.setSpacing(10)
        cdk_label = QLabel("CDK:")
        cdk_label.setStyleSheet("color: #94A3B8; font-size: 13px; background: transparent; border: none;")
        cdk_label.setFixedWidth(40)
        cdk_row.addWidget(cdk_label)
        cdk_row.addWidget(cdk_input, 1)
        cdk_row.addWidget(get_cdk_btn)

        mirror_status = QLabel("")
        mirror_status.setStyleSheet(
            "color: #64748B; font-size: 12px; background: transparent; border: none;"
        )

        def _refresh_mirror_status():
            cdk_text = cdk_input.text().strip()
            if not mirror_enable.isChecked():
                mirror_status.setText("● 未启用")
                mirror_status.setStyleSheet(
                    "color: #64748B; font-size: 12px; background: transparent; border: none;"
                )
            elif not cdk_text:
                mirror_status.setText("● 已启用,但未填 CDK（仍可查版本,下载走 GitHub）")
                mirror_status.setStyleSheet(
                    "color: #F59E0B; font-size: 12px; background: transparent; border: none;"
                )
            else:
                if len(cdk_text) <= 6:
                    masked = "***"
                else:
                    masked = cdk_text[:3] + "***" + cdk_text[-3:]
                mirror_status.setText(f"● 已启用,CDK: {masked}")
                mirror_status.setStyleSheet(
                    "color: #4ADE80; font-size: 12px; background: transparent; border: none;"
                )

        mirror_enable.toggled.connect(_refresh_mirror_status)
        cdk_input.textChanged.connect(_refresh_mirror_status)
        _refresh_mirror_status()

        mirror_save_btn = QPushButton("保存")
        mirror_save_btn.setCursor(Qt.PointingHandCursor)
        mirror_save_btn.setFixedHeight(34)
        mirror_save_btn.setStyleSheet("""
            QPushButton {
                background-color: #3B82F6;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: 600;
                padding: 0 18px;
            }
            QPushButton:hover { background-color: #2563EB; }
        """)

        def _save_mirror():
            try:
                set_global_setting("mirror_chyan_enabled", mirror_enable.isChecked())
                set_global_setting("mirror_chyan_cdk", cdk_input.text().strip())
                self.show_notification(
                    "Mirror 酱设置已保存", "提示", "success", merge_key="mirror:saved"
                )
            except Exception as e:
                logger.error(f"保存 Mirror 酱设置失败: {e}")
                self.show_notification(
                    f"保存失败: {e}", "提示", "error", merge_key="mirror:save-failed"
                )

        mirror_save_btn.clicked.connect(_save_mirror)

        save_row = QHBoxLayout()
        save_row.addStretch()
        save_row.addWidget(mirror_save_btn)

        mirror_layout.addWidget(mirror_title)
        mirror_layout.addWidget(mirror_desc)
        mirror_layout.addSpacing(4)
        mirror_layout.addWidget(mirror_enable)
        mirror_layout.addLayout(cdk_row)
        mirror_layout.addWidget(cdk_warning)
        mirror_layout.addWidget(mirror_status)
        mirror_layout.addLayout(save_row)

        layout.addWidget(mirror_card)

        # ==================== 操作按钮区 ====================
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(12)

        github_btn = QPushButton("🌐 打开 GitHub")
        github_btn.setFixedHeight(40)
        github_btn.setCursor(Qt.PointingHandCursor)
        github_btn.setStyleSheet("""
            QPushButton {
                background-color: #252631;
                color: #CBD5E1;
                border: 1px solid #2D2E3A;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 500;
                padding: 0 18px;
            }
            QPushButton:hover {
                background-color: #2D2E3A;
                color: #F8FAFC;
                border: 1px solid #475569;
            }
        """)

        check_btn = QPushButton("🔄 检查更新")
        check_btn.setFixedHeight(40)
        check_btn.setCursor(Qt.PointingHandCursor)
        check_btn.setStyleSheet("""
            QPushButton {
                background-color: #3B82F6;
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 13px;
                font-weight: 600;
                padding: 0 18px;
            }
            QPushButton:hover {
                background-color: #2563EB;
            }
            QPushButton:disabled {
                background-color: #1F2937;
                color: #64748B;
            }
        """)

        btn_layout.addWidget(github_btn)
        btn_layout.addWidget(check_btn)
        btn_layout.addStretch()
        layout.addLayout(btn_layout)

        layout.addStretch()

        # ==================== 信号 ====================
        def open_github():
            QDesktopServices.openUrl(QUrl("https://github.com/ivanhih/dd-rec"))

        def check_for_update():
            # 用 Signal 跨线程回主线程（比 QTimer.singleShot(0, lambda) 稳得多）
            # Signal.emit 跨线程时自动用 QueuedConnection 派发到主线程的 event loop

            def _on_done(info, err_msg):
                if info is not None:
                    # 有更新：调现有更新 dialog
                    show_update_dialog(info, self)
                else:
                    # 已是最新 / 检查失败
                    if err_msg:
                        self.show_notification(
                            f"检查更新失败: {err_msg[:80]}",
                            "提示", "error", merge_key="about:check-failed"
                        )
                    else:
                        self.show_notification(
                            f"当前已是最新版本 (v{current_ver})",
                            "提示", "success", merge_key="about:up-to-date"
                        )
                # 关键: 无论哪种结果都恢复按钮
                check_btn.setEnabled(True)
                check_btn.setText("🔄 检查更新")

            class _Sigs(QObject):
                done = Signal(object, str)  # (info, err_msg)

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

            check_btn.setEnabled(False)
            check_btn.setText("检查中...")
            # 用 threading.Thread（Signal 自动切回主线程，比 QThread.create 稳）
            import threading
            threading.Thread(target=_do_check, daemon=True).start()

        github_btn.clicked.connect(open_github)
        check_btn.clicked.connect(check_for_update)

        return page

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


if __name__ == "__main__":
    import logging
    logging.info(f"DD录播机 v{__version__} 启动中...")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    window = MainWindow()
    window.show()

    # 窗口显示后异步检查更新（500ms 延迟避免阻塞首帧渲染）
    def check_update_async():
        try:
            logging.info("[auto-update] 开始检查更新...")
            info = _check_update()
            if info:
                logging.info(f"[auto-update] 发现新版本 v{info.version}，弹出更新 dialog")
                show_update_dialog(info, window)
            else:
                logging.info("[auto-update] 已是最新版本或检查失败")
        except Exception as e:
            # 任何异常都不能阻断应用启动
            logging.error(f"[auto-update] 检查更新异常: {e}", exc_info=True)

    QTimer.singleShot(500, check_update_async)

    sys.exit(app.exec())
