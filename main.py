# main.py
import sys
import os
import re
import platform
import subprocess
import datetime
import logging
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QScrollArea, QLineEdit, QPushButton, QLabel, QGridLayout,
    QFrame, QToolButton, QGraphicsDropShadowEffect, QSizePolicy,
    QGraphicsOpacityEffect, QMenu, QStackedWidget
)
from PySide6.QtCore import QTimer, Qt, QThread, QPropertyAnimation, QEasingCurve, QPoint, QEvent, Signal
from PySide6.QtGui import QIcon, QFont, QColor, QActionGroup, QPixmap, QPainter, QPen, QPainterPath

from core.config import load_app_data, save_app_data, VIDEO_SAVE_DIR, get_room_config, get_global_setting, get_room_setting, get_effective_format
from core.bili_api import get_bili_info
from core.recorder import BiliRecorder
from core.utils import render_path_template
from ui.room_card import RoomCard
from ui.settings_dialog import RoomSettingsDialog, GlobalSettingsOldStyleReplicaPage, AddChannelDialog, open_room_settings_overlay, open_room_settings_overlay


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


# 日志配置
_stdout_handler = logging.StreamHandler(stream=sys.stdout)
if hasattr(_stdout_handler.stream, 'reconfigure'):
    _stdout_handler.stream.reconfigure(encoding='utf-8')
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[_stdout_handler]
)


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
        self.setWindowTitle("B站高级录播机")
        self.resize(1360, 780)
        self.setStyleSheet("background-color: #0F0F13;")

        self.cards = {}           # room_id -> RoomCard
        self.threads = {}         # room_id -> QThread
        self.recorders = {}       # room_id -> BiliRecorder
        
        # 通知队列
        self.notifications = []
        self.notification_lookup = {}
        self.filter_mode = "all"
        self.sort_mode = "default"
        self._last_grid_signature = None
        self._layout_ready = False
        self.current_page = "channels"
        
        self.setup_ui()
        self.load_saved_data()
        self.start_refresh_timer()

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
        button = QToolButton()
        button.setText(text)
        button.setToolTip(tooltip)
        button.setFixedSize(56, 56)
        button.setCursor(Qt.PointingHandCursor)
        return button

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
        self.nav_settings_btn.setStyleSheet(active_style if self.current_page == "settings" else inactive_style)

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
        self.nav_channels_btn.clicked.connect(self.show_channels_page)
        self.nav_settings_btn = self._create_sidebar_nav_button("⚙️", "全局设置")
        self.nav_settings_btn.clicked.connect(self.show_global_settings_page)

        sidebar_layout.addWidget(logo_btn)
        sidebar_layout.addWidget(self.nav_channels_btn)
        sidebar_layout.addWidget(self.nav_settings_btn)
        sidebar_layout.addStretch()
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
        self.filter_btn = QToolButton()
        self.filter_btn.setFixedSize(44, 44)
        self.filter_btn.setIcon(self._create_toolbar_icon("filter"))
        self.filter_btn.setIconSize(QPixmap(20, 20).size())
        self.filter_btn.setPopupMode(QToolButton.InstantPopup)
        self.filter_btn.setToolTip("过滤")
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
        
        self.sort_btn = QToolButton()
        self.sort_btn.setFixedSize(44, 44)
        self.sort_btn.setIcon(self._create_toolbar_icon("sort"))
        self.sort_btn.setIconSize(QPixmap(20, 20).size())
        self.sort_btn.setPopupMode(QToolButton.InstantPopup)
        self.sort_btn.setToolTip("排序")
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
        self.btn_add = QPushButton("添加")
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
        self._layout_update_timer = QTimer(self)
        self._layout_update_timer.setSingleShot(True)
        self._layout_update_timer.timeout.connect(self._rearrange_cards)
        self._layout_ready = True
        
        # 初始定位
        self._position_notification_container()

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

    def load_saved_data(self):
        data = load_app_data()
        self._layout_ready = False
        for ch in data.get("channels", []):
            self.add_card(ch, save=False, rearrange=False)
        self._layout_ready = True
        self.request_rearrange_cards(0)

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

        # 创建 Recorder 和线程
        recorder = BiliRecorder(room_info)
        thread = QThread()
        recorder.moveToThread(thread)

        recorder.status_updated.connect(
            lambda m, l, r, t, d, sp, sz, p, a:
            card.update_status(m, l, r, t, d, sp, sz, p, a)
        )
        recorder.cut_completed.connect(self.on_cut_completed)
        recorder.cut_failed.connect(self.on_cut_failed)

        thread.started.connect(recorder.run)
        thread.start()

        self.cards[room_id] = card
        self.recorders[room_id] = recorder
        self.threads[room_id] = thread

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
        self.filter_btn.setToolTip(labels.get(self.filter_mode, "过滤"))

    def _update_sort_button(self):
        labels = {
            "default": "排序: 默认",
            "status": "排序: 状态优先",
            "name": "排序: 名称",
            "room_id": "排序: 房间号",
        }
        self.sort_btn.setToolTip(labels.get(self.sort_mode, "排序"))
    
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
        dialog = AddChannelDialog(self)
        if dialog.exec() and dialog.result:
            info = dialog.result
            self.add_card(info)
            self.show_notification(f"已添加 {info['uname']}", "添加成功", "success", merge_key=f"add:{info['room_id']}")

    # ==================== 信号槽 ====================
    def on_card_toggle(self, room_id, enabled):
        if room_id in self.recorders:
            self.recorders[room_id].is_monitoring = enabled
        uname = self.cards[room_id].room_info.get("uname", room_id) if room_id in self.cards else room_id
        if enabled:
            self.show_notification(f"{uname} 已开始监控", "开始监控", "info", merge_key=f"monitor:on:{room_id}")
        else:
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
        if room_id in self.cards:
            self.cards[room_id].setParent(None)
            self.recorders[room_id].kill()
            self.threads[room_id].quit()
            self.threads[room_id].wait()
            
            # 获取主播名用于通知
            uname = self.cards[room_id].room_info.get('uname', '房间')
            
            del self.cards[room_id]
            del self.recorders[room_id]
            del self.threads[room_id]
            
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

    def start_refresh_timer(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.refresh_all)
        self.timer.start(2000)

    def refresh_all(self):
        pass

    def closeEvent(self, event):
        for recorder in self.recorders.values():
            recorder.kill()
        for thread in self.threads.values():
            thread.quit()
            thread.wait()
        event.accept()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
