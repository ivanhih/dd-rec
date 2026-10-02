# ui/settings_dialog.py
import os
import threading
import logging
from PySide6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QComboBox, QPushButton, QCheckBox, QScrollArea, QFrame,
    QFormLayout, QTabWidget, QWidget, QTextEdit, QFileDialog,
    QColorDialog, QInputDialog, QMessageBox
)
from PySide6.QtCore import QObject, QRunnable, QSignalBlocker, QThreadPool, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont, QColor

from core.config import (
    DEFAULT_GLOBAL_SETTINGS,
    VIDEO_SAVE_DIR,
    get_effective_format,
    get_global_setting,
    get_room_config,
    get_room_setting,
    get_tag_library,
    get_room_tags,
    upsert_tag,
    delete_tag,
    set_room_tag_ids,
    has_room_override,
    save_config,
    set_global_setting,
    set_room_setting,
)
from ui.room_card import ToggleSwitch
from ui.theme import enable_surface, repolish


# setting key -> local help section id (core.help_docs.HELP_TOC)
SETTING_HELP_LINKS = {
    # paths
    "save_dir": "paths",
    "path_template": "paths",
    "custom_dir": "paths",
    # webhooks / notify
    "webhooks": "webhook",
    "webhook_format": "webhook",
    "webhook_log_verbose": "webhook",
    "webhook_fail_log_interval_sec": "webhook",
    "notify_enabled": "webhook",
    "notify_url": "webhook",
    "notify_title_template": "webhook",
    "notify_body_template": "webhook",
    # recording / convert / split / stream
    "stream_record_enabled": "record",
    "stream_codec": "record",
    "stream_format": "record",
    "stream_resolution": "record",
    "stream_fps": "record",
    "stream_bitrate": "record",
    "stream_fallback_policy": "record",
    "auto_switch_stream": "record",
    "allow_audio_only": "record",
    "flv_native_capture": "record",
    "convert_enabled": "record",
    "convert_delete_source": "record",
    "convert_format": "record",
    "artifact_keep_source": "record",
    "split_by_duration": "record",
    "split_by_size": "record",
    "split_on_codec_change": "record",
    "split_on_stream_discontinuity": "record",
    "split_on_title_change": "record",
    "split_on_category_change": "record",
    "chat_record_enabled": "config",
    "chat_credential": "config",
    "chat_format": "config",
    "sessdata": "config",
    # hang / diagnostics
    "disk_min_free_gb": "troubleshoot",
    "disk_check_interval_sec": "troubleshoot",
    "heartbeat_interval_sec": "troubleshoot",
    "recorder_auto_heal": "troubleshoot",
    "recorder_auto_heal_max_restarts": "troubleshoot",
    "recorder_auto_heal_window_sec": "troubleshoot",
    # tags / ui
    "tag_library": "ui",
}


SETTING_COMBO_OPTIONS = {
    "auth_mode": (
        ("inherit", "account.mode.inherit"),
        ("account", "account.mode.account"),
        ("anonymous", "account.mode.anonymous"),
        ("manual", "account.mode.manual"),
    ),
    "language": (
        ("简体中文", "settings.option.language.zh_cn"),
        ("繁體中文", "settings.option.language.zh_tw"),
        ("English", "settings.option.language.en"),
    ),
    "theme": (
        ("深色", "settings.option.theme.dark"),
        ("浅色", "settings.option.theme.light"),
    ),
    "proxy_mode": (
        ("禁用", "settings.option.proxy.disabled"),
        ("系统", "settings.option.proxy.system"),
        ("自定义", "settings.option.proxy.custom"),
    ),
    "stream_priority_param": (
        ("分辨率", "settings.option.priority.resolution"),
        ("帧率", "settings.option.priority.fps"),
        ("码率", "settings.option.priority.bitrate"),
        ("编码", "settings.option.priority.codec"),
        ("格式", "settings.option.priority.format"),
        ("网址", "settings.option.priority.url"),
    ),
    "stream_resolution": (
        ("原画", "settings.option.resolution.original"),
        ("超清", "settings.option.resolution.uhd"),
        ("高清", "settings.option.resolution.hd"),
        ("流畅", "settings.option.resolution.smooth"),
    ),
    "stream_fps": tuple(
        (value, f"settings.option.fps.{value.split()[0]}")
        for value in ("30 fps", "60 fps", "120 fps", "25 fps", "20 fps", "15 fps")
    ),
    "stream_codec": tuple(
        (value, f"settings.option.codec.{value}")
        for value in ("h264", "hevc", "av1")
    ),
    "stream_format": tuple(
        (value, f"settings.option.format.{value}")
        for value in ("flv", "fmp4", "ts")
    ),
    "stream_fallback_policy": (
        ("compatible", "settings.option.fallback.compatible"),
        ("strict", "settings.option.fallback.strict"),
        ("best_effort", "settings.option.fallback.best_effort"),
    ),
    "chat_format": (
        ("jsonl 数据", "settings.option.chat.jsonl"),
        ("xml", "settings.option.chat.xml"),
        ("ass 弹幕", "settings.option.chat.ass"),
    ),
    "schedule_timezone": (
        ("UTC", "settings.option.timezone.utc"),
        ("Asia/Shanghai", "settings.option.timezone.shanghai"),
        ("Asia/Tokyo", "settings.option.timezone.tokyo"),
        ("America/New_York", "settings.option.timezone.new_york"),
        ("Europe/London", "settings.option.timezone.london"),
    ),
    "convert_format": tuple(
        (value, f"settings.option.format.{value}")
        for value in ("mp4", "mkv", "ts", "flv")
    ),
    "monitor_delay": (
        ("自动", "settings.option.duration.auto"),
        ("5 秒", "settings.option.duration.5_seconds"),
        ("10 秒", "settings.option.duration.10_seconds"),
        ("30 秒", "settings.option.duration.30_seconds"),
        ("1 分钟", "settings.option.duration.1_minute"),
    ),
    "monitor_interval": (
        ("自动", "settings.option.duration.auto"),
        ("10 秒", "settings.option.duration.10_seconds"),
        ("30 秒", "settings.option.duration.30_seconds"),
        ("1 分钟", "settings.option.duration.1_minute"),
        ("5 分钟", "settings.option.duration.5_minutes"),
    ),
    "monitor_concurrency": (
        ("自动", "settings.option.duration.auto"),
        ("5", "settings.option.number.5"),
        ("10", "settings.option.number.10"),
        ("20", "settings.option.number.20"),
        ("50", "settings.option.number.50"),
    ),
    "monitor_debounce": (
        ("禁用", "settings.option.disabled"),
        ("30 秒", "settings.option.duration.30_seconds"),
        ("1 分钟", "settings.option.duration.1_minute"),
        ("3 分钟", "settings.option.duration.3_minutes"),
        ("5 分钟", "settings.option.duration.5_minutes"),
    ),
    "room_format": (
        ("", "settings.option.inherit_global"),
        ("mp4", "settings.option.format.mp4"),
        ("ts", "settings.option.format.ts"),
        ("flv", "settings.option.format.flv"),
    ),
}


def _populate_combo(combo, key, values=None):
    from ui.i18n import t
    registered = SETTING_COMBO_OPTIONS.get(key)
    if registered is None:
        specs = tuple((value, value) for value in (values or ()))
    elif values is None:
        specs = registered
    else:
        labels = dict(registered)
        specs = tuple((value, labels[value]) for value in values)
    combo.clear()
    for value, label_key in specs:
        combo.addItem(t(label_key), value)
    return specs


def _set_combo_value(combo, value, fallback_index=0):
    index = combo.findData(value, Qt.UserRole)
    if index < 0 and combo.itemData(0, Qt.UserRole) is None:
        index = combo.findText(str(value))
    combo.setCurrentIndex(index if index >= 0 else fallback_index)


class _AddChannelWorkerSignals(QObject):
    finished = Signal(object, str)


class _AddChannelWorker(QRunnable):
    """在共享线程池中获取直播间信息，避免网络请求阻塞 Qt 主线程。"""

    def __init__(self, text):
        super().__init__()
        self.text = text
        self.signals = _AddChannelWorkerSignals()

    @Slot()
    def run(self):
        from core.bili_api import get_bili_info

        try:
            info = get_bili_info(self.text)
            error = "" if info else "获取直播间信息失败，请检查输入"
            self.signals.finished.emit(info, error)
        except Exception as exc:
            logging.exception("获取直播间信息失败")
            self.signals.finished.emit(None, str(exc) or "获取直播间信息失败，请检查输入")


class NoWheelComboBox(QComboBox):
    """滚轮不切换选项:忽略事件并向上冒泡,交给滚动区域滚动页面。"""
    def wheelEvent(self, event):
        event.ignore()


class AddChannelDialog(QDialog):
    """添加直播间对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        from ui.i18n import t
        self.setWindowTitle(t("add_room.title"))
        self.setModal(True)
        self.setFixedSize(400, 200)
        self.result = None  # 保存获取到的房间信息
        self._add_worker = None
        self._loading_frame = 0
        self._loading_timer = QTimer(self)
        self._loading_timer.setInterval(360)
        self._loading_timer.timeout.connect(self._advance_loading_animation)
        self.setup_ui()

    def setup_ui(self):
        from ui.i18n import t
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 提示标签
        tip_label = QLabel(t("add_room.tip"))
        tip_label.setProperty("role", "secondary")
        tip_label.setStyleSheet("font-size: 13px;")
        layout.addWidget(tip_label)

        # 输入框
        self.input = QLineEdit()
        self.input.setPlaceholderText(t("add_room.placeholder"))
        self.input.setStyleSheet("padding: 12px 16px; font-size: 14px;")
        self.input.returnPressed.connect(self.confirm)
        layout.addWidget(self.input)

        # 错误提示标签（初始隐藏）
        self.error_label = QLabel(t("add_room.error"))
        self.error_label.setProperty("statusTone", "danger")
        self.error_label.setStyleSheet("font-size: 12px;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton(t("common.cancel"))
        cancel_btn.setFixedWidth(100)
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setStyleSheet("border: none;")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self.confirm_btn = QPushButton(t("add_room.confirm"))
        self.confirm_btn.setFixedWidth(100)
        self.confirm_btn.setProperty("variant", "primary")
        self.confirm_btn.clicked.connect(self.confirm)
        btn_layout.addWidget(self.confirm_btn)

        layout.addLayout(btn_layout)

    def _advance_loading_animation(self):
        from ui.i18n import t
        self._loading_frame = (self._loading_frame + 1) % 4
        self.confirm_btn.setText(t("add_room.loading") + "." * self._loading_frame)

    def _start_loading_animation(self):
        from ui.i18n import t
        self._loading_frame = 0
        self.confirm_btn.setText(t("add_room.loading"))
        self._loading_timer.start()

    def _stop_loading_animation(self):
        from ui.i18n import t
        self._loading_timer.stop()
        self._loading_frame = 0
        self.confirm_btn.setText(t("add_room.confirm"))

    def confirm(self):
        text = self.input.text().strip()
        if not text:
            return

        # 禁用按钮避免重复点击
        self.confirm_btn.setEnabled(False)
        self._start_loading_animation()
        self.error_label.setVisible(False)

        self.input.setEnabled(False)
        self._do_add(text)

    def _do_add(self, text):
        worker = _AddChannelWorker(text)
        self._add_worker = worker
        worker.signals.finished.connect(self._on_add_finished)
        QThreadPool.globalInstance().start(worker)

    @Slot(object, str)
    def _on_add_finished(self, info, error):
        from ui.i18n import t
        self._add_worker = None
        self._stop_loading_animation()
        self.confirm_btn.setEnabled(True)
        self.confirm_btn.setText(t("add_room.confirm"))
        self.input.setEnabled(True)

        if info:
            self.result = info
            self.accept()
            return

        self.error_label.setText(error or t("add_room.error"))
        self.error_label.setVisible(True)
        self.input.setFocus()
        QTimer.singleShot(3000, lambda: self.error_label.setVisible(False))


class RoomSettingsDialog(QDialog):
    def __init__(self, room_id, uname, parent=None):
        super().__init__(parent)
        self.room_id = room_id
        self.uname = uname
        self.setWindowTitle(f"房间设置 - {uname}")
        self.setModal(True)
        self.resize(500, 400)
        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 表单布局
        form_layout = QFormLayout()
        form_layout.setSpacing(12)

        # SESSDATA
        self.sessdata_input = QLineEdit()
        self.sessdata_input.setPlaceholderText("留空继承全局")
        form_layout.addRow(QLabel("SESSDATA:"), self.sessdata_input)

        # 输出格式
        self.format_combo = NoWheelComboBox()
        _populate_combo(self.format_combo, "room_format")
        form_layout.addRow(QLabel("输出格式:"), self.format_combo)

        # 清晰度
        self.quality_input = QLineEdit()
        self.quality_input.setPlaceholderText("10000 (原画)")
        form_layout.addRow(QLabel("清晰度:"), self.quality_input)

        # 自定义保存目录
        self.custom_dir_input = QLineEdit()
        self.custom_dir_input.setPlaceholderText(f"留空继承全局 ({VIDEO_SAVE_DIR})")
        form_layout.addRow(QLabel("自定义保存目录:"), self.custom_dir_input)

        layout.addLayout(form_layout)
        layout.addStretch()

        # 按钮
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton(__import__("ui.i18n", fromlist=["t"]).t("common.cancel"))
        cancel_btn.setFixedWidth(100)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #2d2d2d;
                color: #ffffff;
                border-radius: 8px;
                padding: 10px 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #3d3d3d;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)

        save_btn = QPushButton(__import__("ui.i18n", fromlist=["t"]).t("common.save"))
        save_btn.setFixedWidth(100)
        save_btn.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: #ffffff;
                border-radius: 8px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
        """)
        save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

    def load_settings(self):
        cfg = get_room_config(self.room_id)
        self.sessdata_input.setText(cfg.get("sessdata", ""))
        
        format_value = cfg.get("format", "")
        if format_value == "":
            self.format_combo.setCurrentIndex(0)
        else:
            index = self.format_combo.findData(format_value, Qt.UserRole)
            if index >= 0:
                self.format_combo.setCurrentIndex(index)
        
        self.quality_input.setText(str(cfg.get("quality", 10000)))
        self.custom_dir_input.setText(cfg.get("custom_dir", ""))

    def save_settings(self):
        cfg = get_room_config(self.room_id)
        cfg["sessdata"] = self.sessdata_input.text().strip()
        
        if self.format_combo.currentIndex() == 0:
            cfg["format"] = ""
        else:
            cfg["format"] = self.format_combo.currentData(Qt.UserRole)
        
        try:
            cfg["quality"] = int(self.quality_input.text())
        except:
            cfg["quality"] = 10000
        
        cfg["custom_dir"] = self.custom_dir_input.text().strip()
        save_config()
        self.accept()


class GlobalSettingsPage(QWidget):
    saved = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
        self.load_settings()

    def setup_ui(self):
        self.setStyleSheet("""
            QWidget {
                background-color: transparent;
                color: #E2E8F0;
            }
            QLabel {
                color: #E2E8F0;
            }
            QLineEdit, QComboBox {
                background-color: #181920;
                border: 1px solid #2D2E3A;
                border-radius: 10px;
                padding: 10px 12px;
                color: #E2E8F0;
                min-height: 20px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #3B82F6;
            }
            QCheckBox {
                color: #CBD5E1;
                spacing: 8px;
            }
            QTabWidget::pane {
                border: none;
                background-color: #1A1B21;
            }
            QTabBar::tab {
                background-color: #252631;
                color: #94A3B8;
                padding: 10px 20px;
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                margin-right: 4px;
            }
            QTabBar::tab:selected {
                background-color: #3B82F6;
                color: white;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(20)

        header_layout = QVBoxLayout()
        header_layout.setSpacing(6)

        title = QLabel("全局设置")
        title.setFont(QFont("Microsoft YaHei UI", 24, QFont.Bold))
        title.setStyleSheet("color: #F8FAFC;")

        desc = QLabel("统一配置录制策略、流优先级和保存设置")
        desc.setStyleSheet("color: #94A3B8; font-size: 13px;")

        header_layout.addWidget(title)
        header_layout.addWidget(desc)
        main_layout.addLayout(header_layout)

        self.tab_widget = QTabWidget()

        self.record_tab = QWidget()
        self.setup_record_tab()
        self.tab_widget.addTab(self.record_tab, "录制设置")

        self.save_tab = QWidget()
        self.setup_save_tab()
        self.tab_widget.addTab(self.save_tab, "保存设置")

        main_layout.addWidget(self.tab_widget, 1)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.save_btn = QPushButton("保存设置")
        self.save_btn.setFixedWidth(120)
        self.save_btn.setStyleSheet("""
            QPushButton {
                background-color: #3B82F6;
                color: #FFFFFF;
                border-radius: 10px;
                padding: 10px 20px;
                font-size: 14px;
                font-weight: 600;
                border: none;
            }
            QPushButton:hover {
                background-color: #2563EB;
            }
        """)
        self.save_btn.clicked.connect(self.save_settings)
        button_layout.addWidget(self.save_btn)

        main_layout.addLayout(button_layout)

    def setup_record_tab(self):
        layout = QVBoxLayout(self.record_tab)
        layout.setSpacing(15)

        form_layout = QFormLayout()
        form_layout.setSpacing(12)

        # 分割设置
        self.split_duration_input = QLineEdit()
        self.split_duration_input.setPlaceholderText("01:00:00")
        form_layout.addRow(QLabel("按时长分割:"), self.split_duration_input)

        self.split_size_input = QLineEdit()
        self.split_size_input.setPlaceholderText("例如: 500MB 或 2GB")
        form_layout.addRow(QLabel("按大小分割:"), self.split_size_input)

        self.split_title_check = QCheckBox("标题改变时分割")
        form_layout.addRow("", self.split_title_check)

        self.split_category_check = QCheckBox("分区改变时分割")
        form_layout.addRow("", self.split_category_check)

        layout.addLayout(form_layout)

        # 分隔线
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setStyleSheet("background-color: #2d2e3a;")
        layout.addWidget(line)

        # 流设置
        stream_layout = QFormLayout()
        stream_layout.setSpacing(12)

        self.stream_codec_combo = NoWheelComboBox()
        _populate_combo(self.stream_codec_combo, "stream_codec", ("av1", "hevc", "h264"))
        stream_layout.addRow(QLabel("优先编码:"), self.stream_codec_combo)

        self.stream_resolution_combo = NoWheelComboBox()
        _populate_combo(self.stream_resolution_combo, "stream_resolution")
        stream_layout.addRow(QLabel("清晰度:"), self.stream_resolution_combo)

        self.auto_switch_check = QCheckBox("自动切换更好的流")
        stream_layout.addRow("", self.auto_switch_check)

        layout.addLayout(stream_layout)
        layout.addStretch()

    def setup_save_tab(self):
        layout = QVBoxLayout(self.save_tab)
        layout.setSpacing(15)

        form_layout = QFormLayout()
        form_layout.setSpacing(12)

        self.save_dir_input = QLineEdit()
        self.save_dir_input.setPlaceholderText(str(VIDEO_SAVE_DIR))
        form_layout.addRow(QLabel("默认保存目录:"), self.save_dir_input)

        self.convert_format_combo = NoWheelComboBox()
        _populate_combo(self.convert_format_combo, "convert_format", ("mp4", "ts", "flv"))
        form_layout.addRow(QLabel("输出格式:"), self.convert_format_combo)

        layout.addLayout(form_layout)
        layout.addStretch()

    def load_settings(self):
        self.split_duration_input.setText(get_global_setting("split_by_duration"))
        self.split_size_input.setText(get_global_setting("split_by_size"))
        self.split_title_check.setChecked(get_global_setting("split_on_title_change"))
        self.split_category_check.setChecked(get_global_setting("split_on_category_change"))
        self.auto_switch_check.setChecked(get_global_setting("auto_switch_stream"))
        self.save_dir_input.setText(get_global_setting("save_dir"))

        codec_index = self.stream_codec_combo.findData(get_global_setting("stream_codec"), Qt.UserRole)
        if codec_index >= 0:
            self.stream_codec_combo.setCurrentIndex(codec_index)

        res_index = self.stream_resolution_combo.findData(get_global_setting("stream_resolution"), Qt.UserRole)
        if res_index >= 0:
            self.stream_resolution_combo.setCurrentIndex(res_index)

        fmt_index = self.convert_format_combo.findData(get_global_setting("convert_format"), Qt.UserRole)
        if fmt_index >= 0:
            self.convert_format_combo.setCurrentIndex(fmt_index)

    def save_settings(self):
        # 保存录制设置
        set_global_setting("split_by_duration", self.split_duration_input.text())
        set_global_setting("split_by_size", self.split_size_input.text())
        set_global_setting("split_on_title_change", self.split_title_check.isChecked())
        set_global_setting("split_on_category_change", self.split_category_check.isChecked())
        set_global_setting("stream_codec", self.stream_codec_combo.currentData(Qt.UserRole))
        set_global_setting("stream_resolution", self.stream_resolution_combo.currentData(Qt.UserRole))
        set_global_setting("auto_switch_stream", self.auto_switch_check.isChecked())
        
        # 保存保存设置
        set_global_setting("save_dir", self.save_dir_input.text())
        set_global_setting("convert_format", self.convert_format_combo.currentData(Qt.UserRole))
        self.saved.emit()


class GlobalSettingsOldStyleReplicaPage(QWidget):
    """参考 oldmain.py 复刻的全局设置页面，暂不接入当前主界面入口。"""
    saved = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controls = {}
        # (widget, key, kind, prefix)：语言切换时批量刷新
        # kind: text | placeholder | button
        self._i18n_bindings = []
        self._combo_bindings = []
        from ui.i18n import language_manager
        language_manager().changed.connect(self.retranslate_ui)
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        # 颜色由应用级主题 QSS 控制（ui/theme.py）；这里不设局部样式。
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)

        header_left = QHBoxLayout()
        header_left.setSpacing(10)
        icon = QLabel("⚙")
        icon.setProperty("role", "link")
        icon.setStyleSheet("font-size: 22px; font-weight: 700;")
        title = QLabel()
        title.setFont(QFont("Microsoft YaHei UI", 22, QFont.Bold))
        title.setProperty("role", "title")
        self._page_title = title
        self._bind_i18n(title, "settings.window_title")
        header_left.addWidget(icon)
        header_left.addWidget(title)
        header_left.addStretch()

        badge = QLabel()
        badge.setObjectName("saveBadge")
        self._page_badge = badge
        self._bind_i18n(badge, "settings.badge")

        help_btn = self._compact_button(QPushButton(), height=34, min_width=96)
        help_btn.setProperty("variant", "secondary")
        self._bind_i18n(help_btn, "help.open")
        help_btn.clicked.connect(self._open_help_overlay)
        self._page_help_btn = help_btn

        header.addLayout(header_left, 1)
        header.addWidget(help_btn, 0, Qt.AlignRight | Qt.AlignVCenter)
        header.addWidget(badge, 0, Qt.AlignRight | Qt.AlignVCenter)
        root.addLayout(header)
        root.addSpacing(14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(20)

        left_col = QVBoxLayout()
        left_col.setSpacing(18)
        left_col.addWidget(self._build_appearance_card())
        left_col.addWidget(self._build_account_card())
        left_col.addWidget(self._build_file_split_card())
        left_col.addWidget(self._build_network_card())
        left_col.addWidget(self._build_stream_record_card())
        left_col.addWidget(self._build_chat_record_card())
        left_col.addWidget(self._build_schedule_card())
        left_col.addWidget(self._build_automation_card())
        left_col.addWidget(self._build_tag_manager_card())
        left_col.addStretch()

        right_col = QVBoxLayout()
        right_col.setSpacing(18)
        right_col.addWidget(self._build_file_location_card())
        right_col.addWidget(self._build_convert_card())
        right_col.addWidget(self._build_monitor_card())
        right_col.addWidget(self._build_cover_card())
        right_col.addWidget(self._build_conditions_card())
        right_col.addWidget(self._build_notify_card())
        right_col.addWidget(self._build_system_card())
        right_col.addStretch()

        left_wrap = QWidget()
        left_wrap.setLayout(left_col)
        right_wrap = QWidget()
        right_wrap.setLayout(right_col)

        content_layout.addWidget(left_wrap, 1)
        content_layout.addWidget(right_wrap, 1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)
        # 非默认语言启动时，外观卡/页头按当前语言显示
        self.retranslate_ui()

    def _build_account_card(self):
        from ui.account_panel import AccountPanel
        panel = AccountPanel(self)
        panel.saved.connect(self.saved.emit)
        self._account_panel = panel
        return self._setting_card("account.title", "#00A1D6", [panel])

    def _build_tag_manager_card(self):
        from ui.i18n import t

        card = QFrame()
        card.setObjectName("settingsCard")
        enable_surface(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header = QHBoxLayout()
        accent = QFrame()
        accent.setFixedWidth(4)
        accent.setStyleSheet("background-color:#8B5CF6; border-radius:2px; border:none;")
        title = QLabel(t("settings.tags.title"))
        title.setProperty("role", "title")
        add_btn = self._compact_button(QPushButton(t("settings.tags.add")), height=30, min_width=72)
        add_btn.setProperty("variant", "primary")
        add_btn.clicked.connect(self._add_tag)
        header.addWidget(accent)
        header.addWidget(title)
        header.addStretch()
        header.addWidget(add_btn)
        layout.addLayout(header)

        desc = QLabel(t("settings.tags.desc"))
        desc.setProperty("role", "itemDesc")
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._tag_manager_rows = QVBoxLayout()
        self._tag_manager_rows.setSpacing(8)
        layout.addLayout(self._tag_manager_rows)
        self._refresh_tag_manager_rows()
        return card

    @staticmethod
    def _clear_layout(layout):
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()
            child = item.layout()
            if child is not None:
                GlobalSettingsOldStyleReplicaPage._clear_layout(child)

    def _refresh_tag_manager_rows(self):
        if not hasattr(self, "_tag_manager_rows"):
            return
        from ui.i18n import t
        self._clear_layout(self._tag_manager_rows)
        tags = get_tag_library()
        if not tags:
            empty = QLabel(t("settings.tags.empty"))
            empty.setProperty("role", "muted")
            self._tag_manager_rows.addWidget(empty)
            return
        for tag in tags:
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(8)
            dot = QLabel("●")
            dot.setStyleSheet(f"color:{tag['color']}; font-size:18px;")
            name = QLabel(tag["name"])
            name.setProperty("role", "itemTitle")
            color = QLabel(tag["color"])
            color.setProperty("role", "muted")
            color.setStyleSheet("font-size:11px;")
            edit_btn = self._compact_button(QPushButton(t("settings.action.edit")), height=28, min_width=54)
            edit_btn.setProperty("variant", "secondary")
            delete_btn = self._compact_button(QPushButton(t("settings.tags.delete")), height=28, min_width=54)
            delete_btn.setProperty("variant", "danger")
            edit_btn.clicked.connect(lambda checked=False, item=dict(tag): self._edit_tag(item))
            delete_btn.clicked.connect(lambda checked=False, item=dict(tag): self._delete_tag(item))
            h.addWidget(dot)
            h.addWidget(name)
            h.addWidget(color)
            h.addStretch()
            h.addWidget(edit_btn)
            h.addWidget(delete_btn)
            self._tag_manager_rows.addWidget(row)

    def _prompt_tag(self, existing=None):
        from ui.i18n import t
        existing = existing or {}
        name, ok = QInputDialog.getText(
            self,
            t("settings.tags.edit_title") if existing else t("settings.tags.add_title"),
            t("settings.tags.name"),
            text=existing.get("name", ""),
        )
        if not ok:
            return None
        name = name.strip()
        if not name:
            QMessageBox.warning(self, t("common.error"), t("settings.tags.name_required"))
            return None
        initial = QColor(existing.get("color", "#3B82F6"))
        color = QColorDialog.getColor(initial, self, t("settings.tags.color"))
        if not color.isValid():
            return None
        return name, color.name().upper()

    def _add_tag(self):
        values = self._prompt_tag()
        if not values:
            return
        try:
            upsert_tag(values[0], values[1])
        except ValueError as exc:
            QMessageBox.warning(self, __import__("ui.i18n", fromlist=["t"]).t("common.error"), str(exc))
            return
        self._refresh_tag_manager_rows()
        self.saved.emit("tag_library")

    def _edit_tag(self, tag):
        values = self._prompt_tag(tag)
        if not values:
            return
        try:
            upsert_tag(values[0], values[1], tag["id"])
        except (ValueError, KeyError) as exc:
            QMessageBox.warning(self, __import__("ui.i18n", fromlist=["t"]).t("common.error"), str(exc))
            return
        self._refresh_tag_manager_rows()
        self.saved.emit("tag_library")

    def _delete_tag(self, tag):
        from ui.i18n import t
        answer = QMessageBox.question(
            self,
            t("settings.tags.delete_title"),
            t("settings.tags.delete_confirm", name=tag["name"]),
        )
        if answer != QMessageBox.Yes:
            return
        delete_tag(tag["id"])
        self._refresh_tag_manager_rows()
        self.saved.emit("tag_library")

    # 通知模板默认值
    # Template defaults are now in config.py
    # Template defaults are now in config.py

    def _open_help_overlay(self, section_id=None):
        """Open in-app local help; section_id selects a wiki chapter."""
        win = self.window()
        try:
            from ui.help_dialog import open_help_overlay
            open_help_overlay(win if win is not None else self, section_id=section_id)
        except Exception as exc:
            logging.exception("打开使用说明失败: %s", exc)

    def _bind_i18n(self, widget, key, kind="text", prefix=""):
        """登记可热更文案；立即按当前语言填充。"""
        self._i18n_bindings.append((widget, key, kind, prefix))
        self._apply_i18n_binding(widget, key, kind, prefix)
        return widget

    def _apply_i18n_binding(self, widget, key, kind="text", prefix=""):
        from ui.i18n import t
        text = f"{prefix}{t(key)}" if prefix else t(key)
        if kind == "placeholder":
            widget.setPlaceholderText(text)
        else:
            widget.setText(text)
            if isinstance(widget, QPushButton) and widget.property("settingsCompact"):
                widget.ensurePolished()
                widget.setMinimumWidth(max(widget.minimumWidth(), widget.fontMetrics().horizontalAdvance(text) + 20))

    @staticmethod
    def _compact_button(button, *, height, min_width=0):
        button.setProperty("settingsCompact", True)
        button.setFixedHeight(height)
        button.ensurePolished()
        button.setMinimumWidth(max(min_width, button.fontMetrics().horizontalAdvance(button.text()) + 20))
        return button

    def _setting_card(self, title_key, color, items, title_prefix=""):
        card = QFrame()
        card.setObjectName("settingsCard")
        enable_surface(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)

        accent = QFrame()
        accent.setFixedWidth(4)
        accent.setStyleSheet(f"background-color: {color}; border-radius: 2px; border: none;")

        title = QLabel()
        title.setProperty("role", "title")
        self._bind_i18n(title, title_key, prefix=title_prefix)

        header.addWidget(accent)
        header.addWidget(title)
        header.addStretch()
        layout.addLayout(header)

        for index, item in enumerate(items):
            layout.addWidget(item)
            if index != len(items) - 1:
                layout.addWidget(self._section_divider())
        return card

    def _section_divider(self):
        line = QFrame()
        line.setObjectName("settingDivider")
        return line

    def _setting_item(self, title_key, desc_key, control, reset_widget=None, *, help_key=None, help_section=None):
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        title = QLabel()
        title.setProperty("role", "itemTitle")
        self._bind_i18n(title, title_key)
        desc = QLabel()
        desc.setProperty("role", "itemDesc")
        desc.setWordWrap(True)
        self._bind_i18n(desc, desc_key)

        text_col.addWidget(title)
        text_col.addWidget(desc)

        layout.addLayout(text_col, 1)
        layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
        if reset_widget:
            layout.addWidget(reset_widget, 0, Qt.AlignRight | Qt.AlignVCenter)

        section = help_section or SETTING_HELP_LINKS.get(help_key or "")
        if section:
            layout.addWidget(self._help_button(section), 0, Qt.AlignRight | Qt.AlignVCenter)
        return wrapper

    def _help_button(self, section_id: str):
        from ui.i18n import t
        btn = self._compact_button(QPushButton("?"), height=28, min_width=28)
        btn.setProperty("variant", "secondary")
        btn.setProperty("settingsCompact", True)
        btn.setToolTip(t("help.setting_tip"))
        btn.setCursor(Qt.PointingHandCursor)
        repolish(btn)
        btn.clicked.connect(lambda checked=False, sid=section_id: self._open_help_overlay(sid))
        return btn

    def _reset_button(self, key, default_value):
        """生成两阶段确认重置按钮（点重置 -> 显示确认重置 -> 点确认才执行）"""
        btn = self._compact_button(QPushButton(), height=28, min_width=60)
        btn.setProperty("variant", "reset")
        self._bind_i18n(btn, "settings.action.reset")
        confirm = self._compact_button(QPushButton(), height=28, min_width=60)
        confirm.setProperty("variant", "danger")
        self._bind_i18n(confirm, "settings.action.confirm_reset")
        confirm.hide()

        def _on_reset():
            btn.hide()
            confirm.show()

        def _do_reset():
            self._save_setting(key, default_value)
            self._refresh_widget(key, default_value)
            confirm.hide()
            btn.show()
            btn.setFocus()

        btn.clicked.connect(_on_reset)
        confirm.clicked.connect(_do_reset)

        wrapper = QWidget()
        w_layout = QHBoxLayout(wrapper)
        w_layout.setContentsMargins(0, 0, 0, 0)
        w_layout.setSpacing(4)
        w_layout.addWidget(btn)
        w_layout.addWidget(confirm)
        return wrapper

    def _refresh_widget(self, key, default_value):
        """重置后刷新控件 UI 到默认值"""
        widget = self._controls.get(key)
        if widget is None:
            return
        if isinstance(widget, tuple):       # split_by_duration 三元组
            h, m, s = widget
            parts = str(default_value).split(":")
            h.setText(parts[0] if len(parts) > 0 else "1")
            m.setText(parts[1] if len(parts) > 1 else "00")
            s.setText(parts[2] if len(parts) > 2 else "00")
            return
        widget.blockSignals(True)
        try:
            if isinstance(widget, ToggleSwitch):
                widget.setChecked(bool(default_value), animate=False)
            elif isinstance(widget, QLineEdit):
                widget.setText(str(default_value) if default_value else "")
            elif isinstance(widget, QTextEdit):
                widget.setPlainText(str(default_value) if default_value else "")
            elif isinstance(widget, QComboBox):
                _set_combo_value(widget, default_value)
        finally:
            widget.blockSignals(False)

    def _bind_line_edit(self, widget, key):
        widget.editingFinished.connect(lambda k=key, w=widget: self._save_setting(k, self._coerce_setting_value(k, w.text())))
        self._controls[key] = widget
        return widget

    def _bind_checkbox(self, widget, key):
        widget.toggled.connect(lambda checked, k=key: self._save_setting(k, checked))
        self._controls[key] = widget
        return widget

    def _bind_combo(self, widget, key):
        widget.currentIndexChanged.connect(
            lambda _index, k=key, w=widget: self._save_setting(k, w.currentData(Qt.UserRole))
        )
        self._controls[key] = widget
        return widget

    @staticmethod
    def _coerce_setting_value(key, raw):
        """Normalize numeric hang/diagnostics settings typed in line edits."""
        text = "" if raw is None else str(raw).strip()
        defaults = DEFAULT_GLOBAL_SETTINGS
        float_keys = {
            "disk_min_free_gb": (0.0, 100000.0),
            "disk_check_interval_sec": (15.0, 86400.0),
            "heartbeat_interval_sec": (60.0, 86400.0),
            "webhook_fail_log_interval_sec": (30.0, 86400.0),
            "recorder_auto_heal_window_sec": (60.0, 86400.0),
        }
        int_keys = {
            "recorder_auto_heal_max_restarts": (1, 100),
        }
        if key in float_keys:
            lo, hi = float_keys[key]
            try:
                value = float(text)
            except (TypeError, ValueError):
                return defaults.get(key)
            return max(lo, min(hi, value))
        if key in int_keys:
            lo, hi = int_keys[key]
            try:
                value = int(float(text))
            except (TypeError, ValueError):
                return defaults.get(key)
            return max(lo, min(hi, value))
        return text

    def _line_edit(self, key, hint="", width=180):
        widget = QLineEdit()
        widget.setPlaceholderText(hint)
        widget.setFixedWidth(width)
        return self._bind_line_edit(widget, key)

    def _combo(self, key, options=None, width=130):
        widget = NoWheelComboBox()
        _populate_combo(widget, key, options)
        widget.setFixedWidth(width)
        self._combo_bindings.append((widget, key, tuple(options) if options else None))
        return self._bind_combo(widget, key)

    def _check(self, key):
        widget = ToggleSwitch()
        return self._bind_checkbox(widget, key)

    def _directory_field(self, key, hint="", width=220):
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        line_edit = self._line_edit(key, hint, width)
        button = self._compact_button(QPushButton("📁"), height=40)
        button.setFixedWidth(42)
        button.setToolTip("选择文件夹")
        button.clicked.connect(lambda: self._choose_directory(key, line_edit))

        layout.addWidget(line_edit)
        layout.addWidget(button)
        return wrapper

    def _template_editor_button(self, key):
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QLabel()
        summary.setProperty("role", "muted")
        summary.setStyleSheet("font-size: 12px;")
        if not hasattr(self, "_template_summary_labels"):
            self._template_summary_labels = {}
        self._template_summary_labels[key] = summary
        self._update_template_summary(key)
        button = QPushButton()
        self._bind_i18n(button, "settings.action.edit_template")
        button.setFixedWidth(140)
        button.clicked.connect(lambda: self._open_path_template_overlay(key))
        layout.addStretch()
        layout.addWidget(summary)
        layout.addWidget(button)
        return wrapper

    def _build_appearance_card(self):
        return self._setting_card("settings.appearance", "#7B61FF", [
            self._setting_item("settings.appearance.language", "settings.appearance.language_desc",
                self._combo("language", width=140),
                self._reset_button("language", DEFAULT_GLOBAL_SETTINGS["language"])),
            self._setting_item("settings.appearance.theme", "settings.appearance.theme_desc",
                self._combo("theme", width=120),
                self._reset_button("theme", DEFAULT_GLOBAL_SETTINGS["theme"])),
        ])

    def retranslate_ui(self):
        """语言切换时重译设置页所有已绑定文案。"""
        for widget, key, kind, prefix in list(getattr(self, "_i18n_bindings", [])):
            try:
                self._apply_i18n_binding(widget, key, kind, prefix)
            except RuntimeError:
                continue
        for widget, key, options in list(getattr(self, "_combo_bindings", [])):
            try:
                selected = widget.currentData(Qt.UserRole)
                blocker = QSignalBlocker(widget)
                _populate_combo(widget, key, options)
                _set_combo_value(widget, selected)
                del blocker
            except RuntimeError:
                continue

    @staticmethod
    def _format_bytes(val: int) -> str:
        """把字节数格式化成可读字符串"""
        if val <= 0:
            from ui.i18n import t as _t_dis; return _t_dis("settings.common.disabled")
        if val < 1024:
            return f"{val} B"
        if val < 1024 ** 2:
            return f"{val / 1024:.2f} KB"
        if val < 1024 ** 3:
            return f"{val / 1024 ** 2:.2f} MB"
        return f"{val / 1024 ** 3:.2f} GB"

    def _open_size_overlay(self):
        """在设置页内部弹出内嵌面板（非系统窗口）"""
        # 找到最顶层的 QWidget 作为遮罩父级
        overlay_parent = self
        while overlay_parent.parent() and not isinstance(overlay_parent.parent(), QScrollArea):
            overlay_parent = overlay_parent.parent()
        # 如果找不到合适的父级，就用 self 本身
        root = overlay_parent if isinstance(overlay_parent, QWidget) else self

        # 半透明遮罩
        mask = QWidget(root)
        mask.setObjectName("modalOverlay")
        mask.resize(root.size())
        mask.move(0, 0)
        mask.show()
        mask.raise_()

        # 面板
        panel = QFrame(root)
        panel.setObjectName("settingsOverlayPanel")
        enable_surface(panel)
        panel.setFixedSize(440, 290)
        # 居中定位
        cx = (root.width() - panel.width()) // 2
        cy = (root.height() - panel.height()) // 2
        panel.move(cx, cy)
        panel.show()
        panel.raise_()

        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(24, 20, 24, 20)
        vbox.setSpacing(16)

        # 标题
        from ui.i18n import t as _t_ov; title = QLabel(_t_ov("settings.overlay.size_title"))
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 17px; font-weight: 600;")
        vbox.addWidget(title)

        # 禁用/启用行
        enabled_row = QHBoxLayout()
        enabled_lbl = QLabel(_t_ov("settings.overlay.enable"))
        enabled_lbl.setProperty("role", "itemTitle")
        enabled_lbl.setStyleSheet("font-size: 15px;")
        enabled_toggle = ToggleSwitch()
        enabled_row.addWidget(enabled_lbl)
        enabled_row.addStretch()
        enabled_row.addWidget(enabled_toggle)
        vbox.addLayout(enabled_row)

        # 输入行
        input_row = QHBoxLayout()
        inp = QLineEdit()
        inp.setPlaceholderText(_t_ov("settings.overlay.bytes_placeholder"))
        inp.setFixedWidth(180)
        inp.setStyleSheet("font-size: 16px;")
        unit_lbl = QLabel(_t_ov("settings.overlay.bytes_unit"))
        unit_lbl.setProperty("role", "muted")

        preview = QLabel("—")
        preview.setProperty("statusTone", "neutral")
        preview.setStyleSheet("font-size: 13px; min-width: 110px;")

        def _set_preview_tone(tone):
            from ui.theme import repolish
            preview.setProperty("statusTone", tone)
            repolish(preview)

        def _update_preview(text):
            # 合并过滤 + 显示，避免双重 textChanged 触发
            clean = "".join(c for c in text if c.isdigit())
            if clean != text:
                inp.blockSignals(True)
                inp.setText(clean)
                inp.blockSignals(False)
                text = clean
            val = int(text) if text.strip() else 0
            if val == 0:
                preview.setText("—")
                _set_preview_tone("neutral")
            elif val < 10 * 1024 * 1024:
                preview.setText(f"≈ {self._format_bytes(val)}  ⚠️ 建议 ≥ 10 MB")
                _set_preview_tone("warning")
            else:
                preview.setText(f"≈ {self._format_bytes(val)}")
                _set_preview_tone("info")

        inp.textChanged.connect(_update_preview)

        input_row.addWidget(inp)
        input_row.addWidget(unit_lbl)
        input_row.addWidget(preview)
        vbox.addLayout(input_row)

        # 读取当前值
        raw = get_global_setting("split_by_size")
        try:
            cur_val = int(raw) if raw and str(raw).isdigit() else 0
        except (ValueError, TypeError):
            cur_val = 0

        is_enabled = cur_val > 0
        enabled_toggle.setChecked(is_enabled)
        inp.setText(str(cur_val) if cur_val > 0 else "")
        inp.setEnabled(is_enabled)
        if cur_val > 0:
            preview.setText(f"≈ {self._format_bytes(cur_val)}")
            _set_preview_tone("info")

        def _on_toggle(checked):
            inp.setEnabled(checked)
            if not checked:
                inp.setText("")
                preview.setText("—")
                _set_preview_tone("neutral")
        enabled_toggle.toggled.connect(_on_toggle)

        # 按钮行
        btn_row = QHBoxLayout()
        cancel_btn = self._compact_button(
            QPushButton(__import__("ui.i18n", fromlist=["t"]).t("common.cancel")),
            height=36,
            min_width=90,
        )
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setStyleSheet("border: none;")
        save_btn = self._compact_button(
            QPushButton(__import__("ui.i18n", fromlist=["t"]).t("common.save")),
            height=36,
            min_width=90,
        )
        save_btn.setProperty("variant", "primary")

        def _close():
            panel.hide(); panel.deleteLater()
            mask.hide(); mask.deleteLater()

        def _save():
            enabled = enabled_toggle.isChecked()
            val = 0
            if enabled:
                text = inp.text().strip()
                val = int(text) if text and text != "0" else 0
            self._save_setting("split_by_size", str(val) if val > 0 else "")
            if hasattr(self, "_size_summary_label"):
                self._size_summary_label.setText(self._format_bytes(val))
            _close()

        cancel_btn.clicked.connect(_close)
        save_btn.clicked.connect(_save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        vbox.addLayout(btn_row)

    def _build_duration_input(self):
        """视频时长输入：小时/分钟/秒三个独立小框 + 重置按钮"""
        wrapper = QWidget()
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)

        from PySide6.QtGui import QIntValidator

        def _small_box(placeholder, max_val):
            b = QLineEdit()
            b.setPlaceholderText(placeholder)
            b.setFixedWidth(52)
            b.setAlignment(Qt.AlignCenter)
            b.setValidator(QIntValidator(0, max_val))
            b.setStyleSheet("padding: 6px 4px; font-size: 14px;")
            return b

        h_box = _small_box("时", 99)
        m_box = _small_box("分", 59)
        s_box = _small_box("秒", 59)

        def _sep():
            lbl = QLabel(":")
            lbl.setProperty("role", "muted")
            lbl.setStyleSheet("font-size: 16px;")
            return lbl

        def _on_change(_=None):
            hh = int(h_box.text()) if h_box.text().strip() else 0
            mm = int(m_box.text()) if m_box.text().strip() else 0
            ss = int(s_box.text()) if s_box.text().strip() else 0
            total = hh * 3600 + mm * 60 + ss
            if total == 0:
                self._save_setting("split_by_duration", "")
            else:
                self._save_setting("split_by_duration", f"{hh:02d}:{mm:02d}:{ss:02d}")

        h_box.textChanged.connect(_on_change)
        m_box.textChanged.connect(_on_change)
        s_box.textChanged.connect(_on_change)

        reset_btn = self._compact_button(QPushButton(), height=34, min_width=48)
        self._bind_i18n(reset_btn, "settings.action.reset")
        reset_btn.setProperty("variant", "secondary")
        reset_btn.setStyleSheet("border: none;")

        def _reset():
            default = DEFAULT_GLOBAL_SETTINGS["split_by_duration"]
            parts = default.split(":")
            h_box.setText(parts[0] if len(parts) > 0 else "1")
            m_box.setText(parts[1] if len(parts) > 1 else "00")
            s_box.setText(parts[2] if len(parts) > 2 else "00")

        reset_btn.clicked.connect(_reset)

        # 把三个输入框当一个整体注册进 _controls，load 时统一解析
        self._controls["split_by_duration"] = (h_box, m_box, s_box)

        row.addWidget(h_box)
        row.addWidget(_sep())
        row.addWidget(m_box)
        row.addWidget(_sep())
        row.addWidget(s_box)
        row.addWidget(reset_btn)
        return wrapper

    def _build_file_split_card(self):
        # split_by_size 是自定义控件，不在 _controls 里，需要单独处理重置
        size_wrapper = QWidget()
        size_row = QHBoxLayout(size_wrapper)
        size_row.setContentsMargins(0, 0, 0, 0)
        size_row.setSpacing(8)

        # 摘要标签
        summary = QLabel()
        summary.setProperty("role", "muted")
        summary.setStyleSheet("font-size: 13px;")
        self._bind_i18n(summary, "settings.common.disabled")
        raw = get_global_setting("split_by_size")
        try:
            init_val = int(raw) if raw and str(raw).isdigit() else 0
        except (ValueError, TypeError):
            init_val = 0
        summary.setText(self._format_bytes(init_val))
        self._size_summary_label = summary

        edit_btn = self._compact_button(QPushButton(), height=30, min_width=52)
        self._bind_i18n(edit_btn, "settings.action.edit")
        edit_btn.setProperty("variant", "secondary")
        edit_btn.setStyleSheet("border: none;")
        edit_btn.clicked.connect(lambda: self._open_size_overlay())

        reset_btn = self._compact_button(QPushButton(), height=30, min_width=52)
        self._bind_i18n(reset_btn, "settings.action.reset")
        reset_btn.setProperty("variant", "reset")
        confirm_btn = self._compact_button(QPushButton(), height=30, min_width=60)
        self._bind_i18n(confirm_btn, "settings.action.confirm_reset")
        confirm_btn.setProperty("variant", "danger")
        confirm_btn.hide()

        def _on_size_reset():
            reset_btn.hide()
            confirm_btn.show()

        def _do_size_reset():
            self._save_setting("split_by_size", "")
            from ui.i18n import t as _t_dis; summary.setText(_t_dis("settings.common.disabled"))
            confirm_btn.hide()
            reset_btn.show()

        reset_btn.clicked.connect(_on_size_reset)
        confirm_btn.clicked.connect(_do_size_reset)

        size_row.addWidget(summary)
        size_row.addStretch()
        size_row.addWidget(edit_btn)
        size_row.addWidget(reset_btn)
        size_row.addWidget(confirm_btn)

        return self._setting_card("settings.group.file_split", "#EB5757", [
            self._setting_item("settings.split.size", "settings.split.size_desc", size_wrapper, help_key="split_by_size"),
            self._setting_item("settings.split.duration", "settings.split.duration_desc",
                self._build_duration_input(),
                self._reset_button("split_by_duration", DEFAULT_GLOBAL_SETTINGS["split_by_duration"]),
                help_key="split_by_duration"),
            self._setting_item("settings.split.codec", "settings.split.codec_desc",
                self._check("split_on_codec_change"),
                self._reset_button("split_on_codec_change", DEFAULT_GLOBAL_SETTINGS["split_on_codec_change"]),
                help_key="split_on_codec_change"),
            self._setting_item("settings.split.discontinuity", "settings.split.discontinuity_desc",
                self._check("split_on_stream_discontinuity"),
                self._reset_button("split_on_stream_discontinuity", DEFAULT_GLOBAL_SETTINGS["split_on_stream_discontinuity"]),
                help_key="split_on_stream_discontinuity"),
            self._setting_item("settings.split.title", "settings.split.title_desc",
                self._check("split_on_title_change"),
                self._reset_button("split_on_title_change", DEFAULT_GLOBAL_SETTINGS["split_on_title_change"]),
                help_key="split_on_title_change"),
            self._setting_item("settings.split.category", "settings.split.category_desc",
                self._check("split_on_category_change"),
                self._reset_button("split_on_category_change", DEFAULT_GLOBAL_SETTINGS["split_on_category_change"]),
                help_key="split_on_category_change"),
        ])

    def _build_network_card(self):
        proxy_box = QWidget()
        row = QHBoxLayout(proxy_box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        mode_combo = self._combo("proxy_mode", width=110)
        proxy_input = self._line_edit("proxy", "http://127.0.0.1:7890", 180)
        bypass_input = self._line_edit("proxy_bypass", "localhost,127.0.0.1", 180)

        def _update_proxy_state(mode_value):
            enabled = (mode_value == "自定义")
            proxy_input.setEnabled(enabled)
            bypass_input.setEnabled(enabled)

        init_mode = get_global_setting("proxy_mode") or "禁用"
        _update_proxy_state(init_mode)
        mode_combo.currentIndexChanged.connect(
            lambda _index: _update_proxy_state(mode_combo.currentData(Qt.UserRole))
        )

        row.addWidget(mode_combo)
        row.addWidget(proxy_input)
        return self._setting_card("settings.group.network", "#27AE60", [
            self._setting_item("settings.network.proxy", "settings.network.proxy_desc", proxy_box,
                self._reset_button("proxy_mode", DEFAULT_GLOBAL_SETTINGS["proxy_mode"])),
            self._setting_item("settings.network.bypass", "settings.network.bypass_desc", bypass_input,
                self._reset_button("proxy_bypass", DEFAULT_GLOBAL_SETTINGS["proxy_bypass"])),
        ])

    def _build_stream_record_card(self):
        return self._setting_card("settings.group.stream", "#2D9CDB", [
            self._setting_item("settings.stream.enabled", "settings.stream.enabled_desc",
                self._check("stream_record_enabled"),
                self._reset_button("stream_record_enabled", DEFAULT_GLOBAL_SETTINGS["stream_record_enabled"]),
                help_key="stream_record_enabled"),
            self._setting_item("settings.stream.audio_only", "settings.stream.audio_only_desc",
                self._check("allow_audio_only"),
                self._reset_button("allow_audio_only", DEFAULT_GLOBAL_SETTINGS["allow_audio_only"]),
                help_key="allow_audio_only"),
            self._setting_item("settings.stream.auto_switch", "settings.stream.auto_switch_desc",
                self._check("auto_switch_stream"),
                self._reset_button("auto_switch_stream", DEFAULT_GLOBAL_SETTINGS["auto_switch_stream"]),
                help_key="auto_switch_stream"),
            self._setting_item("settings.stream.priority", "settings.stream.priority_desc",
                self._combo("stream_priority_param", width=120),
                self._reset_button("stream_priority_param", DEFAULT_GLOBAL_SETTINGS["stream_priority_param"]),
                help_section="record"),
            self._setting_item("settings.stream.resolution", "settings.stream.resolution_desc",
                self._combo("stream_resolution", width=110),
                self._reset_button("stream_resolution", DEFAULT_GLOBAL_SETTINGS["stream_resolution"]),
                help_key="stream_resolution"),
            self._setting_item("settings.stream.fps", "settings.stream.fps_desc",
                self._combo("stream_fps", width=110),
                self._reset_button("stream_fps", DEFAULT_GLOBAL_SETTINGS["stream_fps"]),
                help_key="stream_fps"),
            self._setting_item("settings.stream.bitrate", "settings.stream.bitrate_desc",
                self._line_edit("stream_bitrate", "30.0 Mb/s", 120),
                self._reset_button("stream_bitrate", DEFAULT_GLOBAL_SETTINGS["stream_bitrate"]),
                help_key="stream_bitrate"),
            self._setting_item("settings.stream.codec", "settings.stream.codec_desc",
                self._combo("stream_codec", width=100),
                self._reset_button("stream_codec", DEFAULT_GLOBAL_SETTINGS["stream_codec"]),
                help_key="stream_codec"),
            self._setting_item("settings.stream.format", "settings.stream.format_desc",
                self._combo("stream_format", width=100),
                self._reset_button("stream_format", DEFAULT_GLOBAL_SETTINGS["stream_format"]),
                help_key="stream_format"),
            self._setting_item("settings.stream.fallback", "settings.stream.fallback_desc",
                self._combo("stream_fallback_policy", width=140),
                self._reset_button("stream_fallback_policy", DEFAULT_GLOBAL_SETTINGS.get("stream_fallback_policy", "compatible")),
                help_key="stream_fallback_policy"),
            self._setting_item("settings.stream.native_flv", "settings.stream.native_flv_desc",
                self._check("flv_native_capture"),
                self._reset_button("flv_native_capture", DEFAULT_GLOBAL_SETTINGS.get("flv_native_capture", True)),
                help_key="flv_native_capture"),
            self._setting_item("settings.stream.keep_source", "settings.stream.keep_source_desc",
                self._check("artifact_keep_source"),
                self._reset_button("artifact_keep_source", DEFAULT_GLOBAL_SETTINGS.get("artifact_keep_source", True)),
                help_key="artifact_keep_source"),
            self._setting_item("settings.stream.webhook_verified", "settings.stream.webhook_verified_desc",
                self._check("webhook_only_verified_artifacts"),
                self._reset_button("webhook_only_verified_artifacts", DEFAULT_GLOBAL_SETTINGS.get("webhook_only_verified_artifacts", True)),
                help_section="webhook"),
        ])

    def _build_chat_record_card(self):
        # 凭据行：显示当前状态 + 编辑按钮
        cred_widget = QWidget()
        cred_row = QHBoxLayout(cred_widget)
        cred_row.setContentsMargins(0, 0, 0, 0)
        cred_row.setSpacing(8)

        cred_summary = QLabel()
        cred_summary.setProperty("role", "muted")
        cred_summary.setStyleSheet("font-size: 13px;")
        room_id = getattr(self, "room_id", None)
        cred_summary.setText(self._format_credential_summary(get_room_setting(room_id, "chat_credential") or ""))
        self._chat_cred_summary_label = cred_summary

        edit_btn = self._compact_button(QPushButton(), height=30, min_width=52)
        self._bind_i18n(edit_btn, "settings.action.edit")
        edit_btn.setProperty("variant", "secondary")
        edit_btn.setStyleSheet("border: none;")
        edit_btn.clicked.connect(self._open_chat_cookie_overlay)

        cred_row.addWidget(cred_summary)
        cred_row.addStretch()
        cred_row.addWidget(edit_btn)

        # 凭据重置按钮（清除 cookie）
        def _do_cred_reset():
            self._save_setting("chat_credential", "")
            self._chat_cred_summary_label.setText(self._format_credential_summary(""))
        cred_reset = self._reset_button("chat_credential", "")

        return self._setting_card("settings.group.chat", "#BB6BD9", [
            self._setting_item("settings.chat.enabled", "settings.chat.enabled_desc",
                self._check("chat_record_enabled"),
                self._reset_button("chat_record_enabled", DEFAULT_GLOBAL_SETTINGS["chat_record_enabled"]),
                help_key="chat_record_enabled"),
            self._setting_item("settings.chat.credential", "settings.chat.credential_desc",
                cred_widget,
                cred_reset,
                help_key="chat_credential"),
            self._setting_item("settings.chat.format", "settings.chat.format_desc",
                self._combo("chat_format", width=120),
                self._reset_button("chat_format", DEFAULT_GLOBAL_SETTINGS["chat_format"]),
                help_key="chat_format"),
        ])

    @staticmethod
    def _format_credential_summary(value):
        from ui.i18n import t
        text = (value or "").strip()
        if not text:
            return t("settings.common.unset")
        if len(text) > 10:
            return text[:6] + "..." + text[-4:]
        return t("settings.common.set")

    def _open_cookie_overlay(self):
        """全局聊天凭据 cookie 编辑面板。"""
        self._open_chat_cookie_overlay()

    def _open_chat_cookie_overlay(self):
        room_id = getattr(self, "room_id", None)
        self._open_cookie_overlay_impl(
            load_value=lambda: get_room_setting(room_id, "chat_credential") or "",
            save_value=lambda val: self._save_setting("chat_credential", val),
            summary_label=getattr(self, "_chat_cred_summary_label", None),
        )

    def _open_cookie_overlay_impl(self, load_value, save_value, summary_label=None):
        """共享 cookie 编辑 overlay：load/save 由调用方决定作用域。"""
        from ui.i18n import t

        root = self
        while root.parent() and not isinstance(root.parent(), QScrollArea):
            root = root.parent()

        mask = QWidget(root)
        mask.setObjectName("modalOverlay")
        mask.resize(root.size())
        mask.move(0, 0)
        mask.show()
        mask.raise_()

        panel = QFrame(root)
        panel.setObjectName("settingsOverlayPanel")
        enable_surface(panel)
        panel.setFixedSize(520, 360)
        cx = (root.width() - panel.width()) // 2
        cy = (root.height() - panel.height()) // 2
        panel.move(cx, cy)
        panel.show()
        panel.raise_()

        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(28, 24, 28, 20)
        vbox.setSpacing(14)

        title = QLabel(t("settings.overlay.cookie_title"))
        title.setProperty("role", "title")
        title.setStyleSheet("font-size:16px; font-weight:600;")
        vbox.addWidget(title)

        hint = QLabel(t("settings.overlay.cookie_hint"))
        hint.setProperty("role", "muted")
        vbox.addWidget(hint)

        inp = QLineEdit()
        inp.setPlaceholderText(t("settings.overlay.cookie_placeholder"))
        inp.setEchoMode(QLineEdit.Password)
        inp.setText(load_value() or "")

        show_btn = self._compact_button(
            QPushButton(t("settings.overlay.show")),
            height=36,
            min_width=72,
        )
        show_btn.setProperty("variant", "secondary")
        show_btn.setStyleSheet("border: none;")

        def _toggle_show():
            if inp.echoMode() == QLineEdit.Password:
                inp.setEchoMode(QLineEdit.Normal)
                show_btn.setText(t("settings.overlay.hide"))
            else:
                inp.setEchoMode(QLineEdit.Password)
                show_btn.setText(t("settings.overlay.show"))

        show_btn.clicked.connect(_toggle_show)

        inp_row = QHBoxLayout()
        inp_row.setSpacing(6)
        inp_row.addWidget(inp, 1)
        inp_row.addWidget(show_btn, 0)
        vbox.addLayout(inp_row)

        verify_btn = self._compact_button(
            QPushButton(t("settings.overlay.verify_cookie")),
            height=36,
            min_width=110,
        )
        verify_btn.setProperty("variant", "secondary")
        verify_btn.setStyleSheet("border: none;")
        vbox.addWidget(verify_btn)

        account_frame = QFrame()
        account_frame.setObjectName("accountFrame")
        account_layout = QVBoxLayout(account_frame)
        account_layout.setContentsMargins(12, 10, 12, 10)
        account_layout.setSpacing(4)

        account_name = QLabel(t("settings.overlay.not_verified"))
        account_name.setProperty("statusTone", "neutral")
        account_name.setStyleSheet("font-size:13px;")
        account_mid = QLabel("")
        account_mid.setProperty("role", "muted")
        account_mid.setStyleSheet("font-size:11px;")
        account_layout.addWidget(account_name)
        account_layout.addWidget(account_mid)
        vbox.addWidget(account_frame, 1)

        from ui.account_panel import AsyncRunner
        from core.bili_auth import BiliAuthClient
        from core.cookies import parse_cookie
        runner = AsyncRunner(panel)

        def _set_account_tone(tone):
            from ui.theme import repolish
            account_name.setProperty("statusTone", tone)
            repolish(account_name)

        def _verify():
            sessdata = inp.text().strip()
            if not sessdata:
                account_name.setText(t("settings.overlay.cookie_empty"))
                account_mid.setText("")
                _set_account_tone("danger")
                return
            verify_btn.setEnabled(False)
            verify_btn.setText(t("settings.overlay.verifying"))
            account_name.setText(t("settings.overlay.verifying"))
            account_mid.setText("")
            _set_account_tone("neutral")

            def _verified(profile, error):
                verify_btn.setEnabled(True)
                verify_btn.setText(t("settings.overlay.verify_cookie"))
                if error:
                    account_name.setText(t("settings.overlay.cookie_invalid") if error == "expired" else t("account.network" if error == "network" else "account.response"))
                    _set_account_tone("danger")
                else:
                    account_name.setText(f"✅  {profile['uname']}")
                    account_mid.setText(f"UID: {profile['uid']}")
                    _set_account_tone("success")

            runner.submit(lambda: BiliAuthClient().profile(parse_cookie(sessdata)), _verified)

        verify_btn.clicked.connect(_verify)

        btn_row = QHBoxLayout()
        cancel_btn = self._compact_button(
            QPushButton(t("common.cancel")),
            height=36,
            min_width=90,
        )
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setStyleSheet("border: none;")
        save_btn = self._compact_button(
            QPushButton(t("common.save")),
            height=36,
            min_width=90,
        )
        save_btn.setProperty("variant", "primary")

        def _close():
            runner.cancel()
            panel.hide(); panel.deleteLater()
            mask.hide(); mask.deleteLater()

        def _save():
            val = inp.text().strip()
            try:
                parse_cookie(val)
            except ValueError:
                account_name.setText(t("account.response"))
                _set_account_tone("danger")
                return
            save_value(val)
            if summary_label is not None:
                summary_label.setText(self._format_credential_summary(val))
            _close()

        cancel_btn.clicked.connect(_close)
        save_btn.clicked.connect(_save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        vbox.addLayout(btn_row)

    def _build_schedule_card(self):
        return self._setting_card("settings.group.schedule", "#F2994A", [
            self._setting_item("settings.schedule.timezone", "settings.schedule.timezone_desc",
                self._combo("schedule_timezone", width=160),
                self._reset_button("schedule_timezone", DEFAULT_GLOBAL_SETTINGS["schedule_timezone"])),
            self._setting_item("settings.schedule.start", "settings.schedule.start_desc",
                self._line_edit("schedule_start", "如: 08:00", 110),
                self._reset_button("schedule_start", DEFAULT_GLOBAL_SETTINGS["schedule_start"])),
            self._setting_item("settings.schedule.stop", "settings.schedule.stop_desc",
                self._line_edit("schedule_stop", "如: 23:00", 110),
                self._reset_button("schedule_stop", DEFAULT_GLOBAL_SETTINGS["schedule_stop"])),
        ])

    def _build_automation_card(self):
        return self._setting_card("settings.group.automation", "#56CCF2", [
            self._setting_item("settings.automation.webhooks", "settings.automation.webhooks_desc",
                self._line_edit("webhooks", "https://...", 220),
                self._reset_button("webhooks", DEFAULT_GLOBAL_SETTINGS["webhooks"]),
                help_key="webhooks"),
            self._setting_item("settings.automation.webhook_log_verbose", "settings.automation.webhook_log_verbose_desc",
                self._check("webhook_log_verbose"),
                self._reset_button("webhook_log_verbose", DEFAULT_GLOBAL_SETTINGS["webhook_log_verbose"]),
                help_key="webhook_log_verbose"),
            self._setting_item("settings.automation.webhook_fail_log_interval", "settings.automation.webhook_fail_log_interval_desc",
                self._line_edit("webhook_fail_log_interval_sec", "300", 100),
                self._reset_button("webhook_fail_log_interval_sec", DEFAULT_GLOBAL_SETTINGS["webhook_fail_log_interval_sec"]),
                help_key="webhook_fail_log_interval_sec"),
        ])

    def _build_file_location_card(self):
        # path_template 摘要需要刷新
        return self._setting_card("settings.group.location", "#F2C94C", [
            self._setting_item("settings.location.dir", "settings.location.dir_desc",
                self._directory_field("save_dir", VIDEO_SAVE_DIR, 220),
                self._reset_button("save_dir", VIDEO_SAVE_DIR),
                help_key="save_dir"),
            self._setting_item("settings.location.template", "settings.location.template_desc",
                self._template_editor_button("path_template"),
                self._reset_button("path_template", DEFAULT_GLOBAL_SETTINGS["path_template"]),
                help_key="path_template"),
        ])

    def _build_convert_card(self):
        return self._setting_card("settings.group.convert", "#EB5757", [
            self._setting_item("settings.convert.enabled", "settings.convert.enabled_desc",
                self._check("convert_enabled"),
                self._reset_button("convert_enabled", DEFAULT_GLOBAL_SETTINGS["convert_enabled"]),
                help_key="convert_enabled"),
            self._setting_item("settings.convert.delete_source", "settings.convert.delete_source_desc",
                self._check("convert_delete_source"),
                self._reset_button("convert_delete_source", DEFAULT_GLOBAL_SETTINGS["convert_delete_source"]),
                help_key="convert_delete_source"),
            self._setting_item("settings.convert.format", "settings.convert.format_desc",
                self._combo("convert_format", width=100),
                self._reset_button("convert_format", DEFAULT_GLOBAL_SETTINGS["convert_format"]),
                help_key="convert_format"),
        ])

    def _build_monitor_card(self):
        return self._setting_card("settings.group.monitor", "#2D9CDB", [
            self._setting_item("settings.monitor.delay", "settings.monitor.delay_desc",
                self._combo("monitor_delay", width=110),
                self._reset_button("monitor_delay", DEFAULT_GLOBAL_SETTINGS["monitor_delay"])),
            self._setting_item("settings.monitor.interval", "settings.monitor.interval_desc",
                self._combo("monitor_interval", width=110),
                self._reset_button("monitor_interval", DEFAULT_GLOBAL_SETTINGS["monitor_interval"])),
            self._setting_item("settings.monitor.concurrency", "settings.monitor.concurrency_desc",
                self._combo("monitor_concurrency", width=110),
                self._reset_button("monitor_concurrency", DEFAULT_GLOBAL_SETTINGS["monitor_concurrency"])),
            self._setting_item("settings.monitor.debounce", "settings.monitor.debounce_desc",
                self._combo("monitor_debounce", width=110),
                self._reset_button("monitor_debounce", DEFAULT_GLOBAL_SETTINGS["monitor_debounce"])),
            self._setting_item("settings.monitor.proxy", "settings.monitor.proxy_desc",
                self._line_edit("monitor_proxy", "留空使用全局代理", 180),
                self._reset_button("monitor_proxy", DEFAULT_GLOBAL_SETTINGS["monitor_proxy"])),
        ])

    def _build_cover_card(self):
        return self._setting_card("settings.group.cover", "#BB6BD9", [
            self._setting_item("settings.cover.enabled", "settings.cover.enabled_desc",
                self._check("download_cover"),
                self._reset_button("download_cover", DEFAULT_GLOBAL_SETTINGS["download_cover"])),
        ])

    def _build_conditions_card(self):
        return self._setting_card("settings.group.conditions", "#F2994A", [
            self._setting_item("settings.conditions.title", "settings.conditions.title_desc",
                self._line_edit("condition_title", "留空不过滤", 200),
                self._reset_button("condition_title", DEFAULT_GLOBAL_SETTINGS["condition_title"])),
            self._setting_item("settings.conditions.category", "settings.conditions.category_desc",
                self._line_edit("condition_category", "留空不过滤", 200),
                self._reset_button("condition_category", DEFAULT_GLOBAL_SETTINGS["condition_category"])),
            self._setting_item("settings.conditions.hours", "settings.conditions.hours_desc",
                self._line_edit("condition_time_range", "如: 08:00-23:00", 140),
                self._reset_button("condition_time_range", DEFAULT_GLOBAL_SETTINGS["condition_time_range"])),
        ])

    def _update_template_summary(self, key):
        if not hasattr(self, "_template_summary_labels"):
            return
        label = self._template_summary_labels.get(key)
        if not label:
            return
        from ui.theme import repolish
        value = (get_global_setting(key) or "").strip()
        if not value:
            label.setText("(未设置)")
            label.setProperty("role", "muted")
        else:
            first_line = value.split("\n")[0][:60]
            display = first_line + ("..." if len(first_line) >= 60 else "")
            label.setText(display)
            label.setProperty("role", "itemDesc")
        label.setStyleSheet("font-size: 12px;")
        repolish(label)

    def _build_notify_template_item(self, key):
        reset_btn = self._compact_button(QPushButton(), height=28, min_width=60)
        self._bind_i18n(reset_btn, "settings.action.reset")
        reset_btn.setToolTip("重置为默认模板")
        reset_btn.setProperty("variant", "reset")
        confirm_btn = self._compact_button(QPushButton(), height=28, min_width=60)
        self._bind_i18n(confirm_btn, "settings.action.confirm_reset")
        confirm_btn.setProperty("variant", "danger")
        confirm_btn.hide()
        def on_reset():
            reset_btn.hide()
            confirm_btn.show()
        def do_confirm():
            default_value = DEFAULT_GLOBAL_SETTINGS.get(key, "")
            self._save_setting(key, default_value)
            self._update_template_summary(key)
            confirm_btn.hide()
            reset_btn.show()
        reset_btn.clicked.connect(on_reset)
        confirm_btn.clicked.connect(do_confirm)
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        if not hasattr(self, "_template_summary_labels"):
            self._template_summary_labels = {}
        # No summary label preview - user didn't want template text shown
        from ui.i18n import t as _t_edit
        edit_btn = self._compact_button(QPushButton("▶"), height=32)
        edit_btn.setFixedWidth(32)
        edit_btn.setToolTip(_t_edit("settings.action.edit_template"))
        edit_btn.setProperty("variant", "ghost")
        edit_btn.setStyleSheet("font-size: 14px;")
        edit_btn.clicked.connect(lambda checked=False, k=key: self._open_notify_template_overlay(k, ""))
        layout.addStretch()
        layout.addWidget(reset_btn)
        layout.addWidget(edit_btn)
        layout.addWidget(confirm_btn)
        return wrapper

    def _open_notify_template_overlay(self, key, window_title=""):
        from ui.i18n import t

        root = self
        while root.parent() and not isinstance(root.parent(), QScrollArea):
            root = root.parent()
        mask = QWidget(root)
        mask.setObjectName("modalOverlay")
        mask.resize(root.size())
        mask.move(0, 0)
        mask.show()
        mask.raise_()
        panel = QFrame(root)
        panel.setObjectName("settingsOverlayPanel")
        enable_surface(panel)
        panel.setFixedSize(680, 480)
        cx = (root.width() - panel.width()) // 2
        cy = (root.height() - panel.height()) // 2
        panel.move(cx, cy)
        panel.show()
        panel.raise_()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(24, 20, 24, 20)
        vbox.setSpacing(14)
        title = QLabel((t("settings.action.edit") + window_title) if window_title else t("settings.action.edit_template"))
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        vbox.addWidget(title)
        editor = QTextEdit()
        val = str(get_global_setting(key) or "")
        logging.info(f"TPL_OVERLAY key={key} len={len(val)}")
        editor.setPlainText(val)
        editor.setPlaceholderText(t("settings.overlay.path_placeholder"))
        editor.setStyleSheet("font-size: 12px; padding: 12px;")
        vbox.addWidget(editor, 1)
        btn_row = QHBoxLayout()
        cancel_btn = self._compact_button(
            QPushButton(t("common.cancel")),
            height=36,
            min_width=90,
        )
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setStyleSheet("border: none;")
        save_btn = self._compact_button(
            QPushButton(t("common.save")),
            height=36,
            min_width=90,
        )
        save_btn.setProperty("variant", "primary")
        def _close():
            panel.hide(); panel.deleteLater()
            mask.hide(); mask.deleteLater()
        def _save():
            self._save_setting(key, editor.toPlainText())
            self._update_template_summary(key)
            _close()
        cancel_btn.clicked.connect(_close)
        save_btn.clicked.connect(_save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        vbox.addLayout(btn_row)

    def _build_notify_card(self):
        return self._setting_card("settings.group.notify", "#56CCF2", [
            self._setting_item("settings.notify.enabled", "settings.notify.enabled_desc",
                self._check("notify_enabled"),
                self._reset_button("notify_enabled", DEFAULT_GLOBAL_SETTINGS["notify_enabled"])),
            self._setting_item("settings.notify.url", "settings.notify.url_desc",
                self._line_edit("notify_url", "https://...", 220),
                self._reset_button("notify_url", DEFAULT_GLOBAL_SETTINGS["notify_url"])),
            self._setting_item("settings.notify.title_tpl", "settings.notify.title_tpl_desc",
                self._build_notify_template_item("notify_title_template")),
            self._setting_item("settings.notify.body_tpl", "settings.notify.body_tpl_desc",
                self._build_notify_template_item("notify_body_template")),
            self._setting_item("settings.notify.end", "settings.notify.end_desc",
                self._check("notify_on_live_end"),
                self._reset_button("notify_on_live_end", DEFAULT_GLOBAL_SETTINGS["notify_on_live_end"])),
            self._setting_item("settings.notify.error", "settings.notify.error_desc",
                self._check("notify_on_error"),
                self._reset_button("notify_on_error", DEFAULT_GLOBAL_SETTINGS["notify_on_error"])),
        ])

    def _build_system_card(self):
        return self._setting_card("settings.group.system", "#6FCF70", [
            self._setting_item("settings.system.auto_start", "settings.system.auto_start_desc",
                self._check("auto_start"),
                self._reset_button("auto_start", DEFAULT_GLOBAL_SETTINGS["auto_start"])),
            self._setting_item("settings.system.prevent_sleep", "settings.system.prevent_sleep_desc",
                self._check("prevent_sleep"),
                self._reset_button("prevent_sleep", DEFAULT_GLOBAL_SETTINGS["prevent_sleep"])),
            self._setting_item("settings.system.disk_min_free", "settings.system.disk_min_free_desc",
                self._line_edit("disk_min_free_gb", "5.0", 100),
                self._reset_button("disk_min_free_gb", DEFAULT_GLOBAL_SETTINGS["disk_min_free_gb"]),
                help_key="disk_min_free_gb"),
            self._setting_item("settings.system.disk_check_interval", "settings.system.disk_check_interval_desc",
                self._line_edit("disk_check_interval_sec", "60", 100),
                self._reset_button("disk_check_interval_sec", DEFAULT_GLOBAL_SETTINGS["disk_check_interval_sec"]),
                help_key="disk_check_interval_sec"),
            self._setting_item("settings.system.heartbeat_interval", "settings.system.heartbeat_interval_desc",
                self._line_edit("heartbeat_interval_sec", "300", 100),
                self._reset_button("heartbeat_interval_sec", DEFAULT_GLOBAL_SETTINGS["heartbeat_interval_sec"]),
                help_key="heartbeat_interval_sec"),
            self._setting_item("settings.system.auto_heal", "settings.system.auto_heal_desc",
                self._check("recorder_auto_heal"),
                self._reset_button("recorder_auto_heal", DEFAULT_GLOBAL_SETTINGS["recorder_auto_heal"]),
                help_key="recorder_auto_heal"),
            self._setting_item("settings.system.auto_heal_max", "settings.system.auto_heal_max_desc",
                self._line_edit("recorder_auto_heal_max_restarts", "3", 100),
                self._reset_button("recorder_auto_heal_max_restarts", DEFAULT_GLOBAL_SETTINGS["recorder_auto_heal_max_restarts"]),
                help_key="recorder_auto_heal_max_restarts"),
            self._setting_item("settings.system.auto_heal_window", "settings.system.auto_heal_window_desc",
                self._line_edit("recorder_auto_heal_window_sec", "600", 100),
                self._reset_button("recorder_auto_heal_window_sec", DEFAULT_GLOBAL_SETTINGS["recorder_auto_heal_window_sec"]),
                help_key="recorder_auto_heal_window_sec"),
        ])

    def _save_setting(self, key, value):
        # 同步特殊 key 到系统层（注册表 / 电源）
        if key == "auto_start":
            try:
                from core.power import set_auto_start
                set_auto_start(bool(value))
            except Exception as e:
                logging.error(f"同步 auto_start 到注册表失败: {e}")
        elif key == "prevent_sleep":
            # 防止休眠: 通知主窗口启停 PowerKeepAlive
            win = self.window()
            keepalive = getattr(win, "_power_keepalive", None)
            if keepalive is not None:
                try:
                    if value:
                        keepalive.start()
                    else:
                        keepalive.stop()
                except Exception as e:
                    logging.error(f"启停 PowerKeepAlive 失败: {e}")

        set_global_setting(key, value)
        if key == "theme":
            app = QApplication.instance()
            if app is not None:
                from ui.theme import theme_manager
                theme_manager().apply(app, value)
        elif key == "language":
            from ui.i18n import set_language
            set_language(value)
        self.saved.emit(key)

    def _choose_directory(self, key, line_edit):
        current_dir = line_edit.text().strip() or str(VIDEO_SAVE_DIR)
        selected = QFileDialog.getExistingDirectory(self, "选择保存目录", current_dir)
        if selected:
            line_edit.setText(selected)
            self._save_setting(key, selected)

    def _open_path_template_overlay(self, key):
        root = self
        while root.parent() and not isinstance(root.parent(), QScrollArea):
            root = root.parent()
        mask = QWidget(root)
        mask.setObjectName("modalOverlay")
        mask.resize(root.size())
        mask.move(0, 0)
        mask.show()
        mask.raise_()
        panel = QFrame(root)
        panel.setObjectName("settingsOverlayPanel")
        enable_surface(panel)
        panel.setFixedSize(680, 480)
        cx = (root.width() - panel.width()) // 2
        cy = (root.height() - panel.height()) // 2
        panel.move(cx, cy)
        panel.show()
        panel.raise_()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(24, 20, 24, 20)
        vbox.setSpacing(14)
        from ui.i18n import t as _t_path; title = QLabel(_t_path("settings.overlay.path_title"))
        title.setProperty("role", "title")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        desc = QLabel(_t_path("settings.overlay.path_desc"))
        desc.setProperty("role", "muted")
        vbox.addWidget(title)
        vbox.addWidget(desc)
        editor = QTextEdit()
        editor.setPlainText(str(get_global_setting(key) or ""))
        editor.setPlaceholderText(_t_path("settings.overlay.path_placeholder"))
        editor.setStyleSheet("font-size: 12px; padding: 12px;")
        vbox.addWidget(editor, 1)
        btn_row = QHBoxLayout()
        cancel_btn = self._compact_button(
            QPushButton(__import__("ui.i18n", fromlist=["t"]).t("common.cancel")),
            height=36,
            min_width=90,
        )
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setStyleSheet("border: none;")
        save_btn = self._compact_button(
            QPushButton(__import__("ui.i18n", fromlist=["t"]).t("common.save")),
            height=36,
            min_width=90,
        )
        save_btn.setProperty("variant", "primary")
        def _close():
            panel.hide(); panel.deleteLater()
            mask.hide(); mask.deleteLater()
        def _save():
            self._save_setting(key, editor.toPlainText())
            self._update_template_summary(key)
            _close()
        cancel_btn.clicked.connect(_close)
        save_btn.clicked.connect(_save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        vbox.addLayout(btn_row)

    def _save_template_and_close(self, dialog, key, value):
        self._save_setting(key, value)
        dialog.accept()

    def _load_values(self):
        blockers = []
        for widget in self._controls.values():
            widgets = widget if isinstance(widget, tuple) else (widget,)
            blockers.extend(QSignalBlocker(item) for item in widgets)

        try:
            for key, widget in self._controls.items():
                value = get_global_setting(key)
                # 视频时长：三元组 (h_box, m_box, s_box)
                if isinstance(widget, tuple):
                    h_box, m_box, s_box = widget
                    raw = "" if value is None else str(value)
                    parts = raw.split(":") if raw else []
                    try:
                        hh = int(parts[0]) if len(parts) > 0 else 1
                        mm = int(parts[1]) if len(parts) > 1 else 0
                        ss = int(parts[2]) if len(parts) > 2 else 0
                    except (ValueError, IndexError):
                        hh, mm, ss = 1, 0, 0
                    h_box.setText(str(hh))
                    m_box.setText(f"{mm:02d}")
                    s_box.setText(f"{ss:02d}")
                # 文件大小：字节数字输入框
                elif key == "split_by_size" and isinstance(widget, QLineEdit):
                    raw = "" if value is None else str(value)
                    widget.setText(raw if raw.isdigit() and int(raw) > 0 else "")
                elif isinstance(widget, QLineEdit):
                    widget.setText("" if value is None else str(value))
                elif isinstance(widget, QTextEdit):
                    widget.setPlainText("" if value is None else str(value))
                elif isinstance(widget, ToggleSwitch):
                    widget.setChecked(bool(value), animate=False)
                elif isinstance(widget, QComboBox):
                    data = value
                    if key == "theme":
                        # 未知/缺失主题值：UI 显示深色回退，但不改写用户配置
                        from ui.theme import normalize_theme
                        data = normalize_theme(value)
                    index = widget.findData(data, Qt.UserRole)
                    if index < 0 and widget.itemData(0, Qt.UserRole) is None:
                        index = widget.findText(str(data))
                    if index >= 0:
                        widget.setCurrentIndex(index)
        finally:
            blockers.clear()

    def load_settings(self):
        self._load_values()


class GlobalSettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("全局设置")
        self.setModal(True)
        self.resize(700, 600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        self.page = GlobalSettingsPage(self)
        self.page.saved.connect(self.accept)
        layout.addWidget(self.page)


# ==================== Per-Room Settings ====================

class RoomSettingsPage(GlobalSettingsOldStyleReplicaPage):
    """Per-room settings page.
    
    Inherits all card-builder helpers from GlobalSettingsOldStyleReplicaPage but:
    - Shows only the cards relevant to per-room config (no Appearance/Network/Automation/System).
    - Reads values via get_room_setting (falls back to global).
    - Writes values via set_room_setting (stores only overrides).
    - Adds a Cookie card for per-room SESSDATA.
    - Hides the "????" page header (the overlay has its own top bar).
    """

    def __init__(self, room_id, uname, parent=None):
        self.room_id = str(room_id)
        self.uname = uname
        self._template_summary_labels = {}   # must exist before super().__init__ calls _build_ui
        super().__init__(parent)

    # ------------------------------------------------------------------
    # Override _build_ui: show only per-room relevant cards, no header
    # ------------------------------------------------------------------
    def _build_ui(self):
        # 颜色由应用级主题 QSS 控制（ui/theme.py）；与全局设置页共用选择器。
        self._controls = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(20)

        left_col = QVBoxLayout()
        left_col.setSpacing(18)
        left_col.addWidget(self._build_file_split_card())
        left_col.addWidget(self._build_stream_record_card())
        left_col.addWidget(self._build_chat_record_card())
        left_col.addWidget(self._build_schedule_card())
        left_col.addStretch()

        right_col = QVBoxLayout()
        right_col.setSpacing(18)
        right_col.addWidget(self._build_cookie_card())
        right_col.addWidget(self._build_room_tags_card())
        right_col.addWidget(self._build_file_location_card())
        right_col.addWidget(self._build_convert_card())
        right_col.addWidget(self._build_monitor_card())
        right_col.addWidget(self._build_cover_card())
        right_col.addWidget(self._build_conditions_card())
        right_col.addWidget(self._build_notify_card())
        right_col.addStretch()

        left_wrap = QWidget()
        left_wrap.setLayout(left_col)
        right_wrap = QWidget()
        right_wrap.setLayout(right_col)

        content_layout.addWidget(left_wrap, 1)
        content_layout.addWidget(right_wrap, 1)
        scroll.setWidget(content)
        root.addWidget(scroll, 1)

    # ------------------------------------------------------------------
    # Route all reads/writes to per-room config
    # ------------------------------------------------------------------
    def _save_setting(self, key, value):
        set_room_setting(self.room_id, key, value)
        self.saved.emit(key)

    def _load_values(self):
        blockers = []
        for widget in self._controls.values():
            widgets = widget if isinstance(widget, tuple) else (widget,)
            blockers.extend(QSignalBlocker(item) for item in widgets)

        try:
            for key, widget in self._controls.items():
                value = get_room_setting(self.room_id, key)
                if isinstance(widget, tuple):          # split_by_duration triple
                    h_box, m_box, s_box = widget
                    raw = "" if value is None else str(value)
                    parts = raw.split(":") if raw else []
                    try:
                        hh = int(parts[0]) if len(parts) > 0 else 1
                        mm = int(parts[1]) if len(parts) > 1 else 0
                        ss = int(parts[2]) if len(parts) > 2 else 0
                    except (ValueError, IndexError):
                        hh, mm, ss = 1, 0, 0
                    h_box.setText(str(hh))
                    m_box.setText(f"{mm:02d}")
                    s_box.setText(f"{ss:02d}")
                elif key == "split_by_size" and isinstance(widget, QLineEdit):
                    raw = "" if value is None else str(value)
                    widget.setText(raw if raw.isdigit() and int(raw) > 0 else "")
                elif isinstance(widget, QLineEdit):
                    widget.setText("" if value is None else str(value))
                elif isinstance(widget, QTextEdit):
                    widget.setPlainText("" if value is None else str(value))
                elif isinstance(widget, ToggleSwitch):
                    widget.setChecked(bool(value), animate=False)
                elif isinstance(widget, QComboBox):
                    data = value
                    idx = widget.findData(data, Qt.UserRole)
                    if idx < 0 and widget.itemData(0, Qt.UserRole) is None:
                        idx = widget.findText(str(value))
                    if idx >= 0:
                        widget.setCurrentIndex(idx)
        finally:
            blockers.clear()

    # ------------------------------------------------------------------
    # Override template / overlay helpers to use room-scoped values
    # ------------------------------------------------------------------
    def _update_template_summary(self, key):
        if not hasattr(self, "_template_summary_labels"):
            return
        label = self._template_summary_labels.get(key)
        if not label:
            return
        from ui.i18n import t
        from ui.theme import repolish
        value = (get_room_setting(self.room_id, key) or "").strip()
        if not value:
            label.setText(f"({t('settings.common.unset')})")
            label.setProperty("role", "muted")
        else:
            first_line = value.split("\n")[0][:60]
            label.setText(first_line + ("..." if len(first_line) >= 60 else ""))
            label.setProperty("role", "itemDesc")
        label.setStyleSheet("font-size: 12px;")
        repolish(label)

    def _open_notify_template_overlay(self, key, window_title=""):
        from ui.i18n import t
        title = (t("settings.action.edit") + window_title) if window_title else t("settings.action.edit_template")
        self._open_text_overlay(
            key,
            title,
            t("settings.overlay.path_placeholder"),
            "notifyTemplatePanel",
        )

    def _open_path_template_overlay(self, key):
        from ui.i18n import t
        self._open_text_overlay(
            key,
            t("settings.overlay.path_title"),
            t("settings.overlay.path_placeholder"),
            "pathTemplatePanel",
            extra_desc=t("settings.overlay.path_desc"),
        )

    def _open_text_overlay(self, key, title_text, placeholder, obj_name, extra_desc=None):
        """Generic text-editor overlay for template editing."""
        from ui.i18n import t

        root = self
        while root.parent() and not isinstance(root.parent(), QScrollArea):
            root = root.parent()
        mask = QWidget(root)
        mask.setObjectName("modalOverlay")
        mask.resize(root.size())
        mask.move(0, 0)
        mask.show(); mask.raise_()

        panel = QFrame(root)
        panel.setObjectName("settingsOverlayPanel")
        enable_surface(panel)
        panel.setFixedSize(680, 480)
        cx = (root.width() - panel.width()) // 2
        cy = (root.height() - panel.height()) // 2
        panel.move(cx, cy)
        panel.show(); panel.raise_()

        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(24, 20, 24, 20)
        vbox.setSpacing(14)

        title_lbl = QLabel(title_text)
        title_lbl.setProperty("role", "title")
        title_lbl.setStyleSheet("font-size: 16px; font-weight: 600;")
        vbox.addWidget(title_lbl)
        if extra_desc:
            desc_lbl = QLabel(extra_desc)
            desc_lbl.setProperty("role", "muted")
            vbox.addWidget(desc_lbl)

        editor = QTextEdit()
        editor.setPlainText(str(get_room_setting(self.room_id, key) or ""))
        editor.setPlaceholderText(placeholder)
        editor.setStyleSheet("font-size: 12px; padding: 12px;")
        vbox.addWidget(editor, 1)

        btn_row = QHBoxLayout()
        cancel_btn = self._compact_button(QPushButton(t("common.cancel")), height=36, min_width=90)
        cancel_btn.setProperty("variant", "secondary")
        cancel_btn.setStyleSheet("border: none;")
        save_btn = self._compact_button(QPushButton(t("common.save")), height=36, min_width=90)
        save_btn.setProperty("variant", "primary")

        def _close():
            panel.hide(); panel.deleteLater()
            mask.hide(); mask.deleteLater()

        def _save():
            self._save_setting(key, editor.toPlainText())
            self._update_template_summary(key)
            _close()

        cancel_btn.clicked.connect(_close)
        save_btn.clicked.connect(_save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        vbox.addLayout(btn_row)

    def _open_cookie_overlay(self):
        """Cookie editor writing to room config sessdata field."""
        from core.config import CONFIG as _CFG, get_room_config as _grc, save_config as _sc

        def _load():
            return _grc(self.room_id).get("sessdata", "")

        def _save(val):
            if self.room_id not in _CFG["rooms"]:
                _CFG["rooms"][self.room_id] = {
                    "sessdata": "", "format": "", "quality": 10000,
                    "custom_dir": "", "overrides": {}
                }
            _CFG["rooms"][self.room_id]["sessdata"] = val
            _sc()

        self._open_cookie_overlay_impl(
            load_value=_load,
            save_value=_save,
            summary_label=getattr(self, "_cred_summary_label", None),
        )

    # ------------------------------------------------------------------
    # Room tag binding (room-only)
    # ------------------------------------------------------------------
    def _build_room_tags_card(self):
        from ui.i18n import t
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._room_tag_checks = {}
        self._room_tag_list_layout = QVBoxLayout()
        self._room_tag_list_layout.setSpacing(8)
        layout.addLayout(self._room_tag_list_layout)
        self._populate_room_tag_checks()

        add_btn = self._compact_button(QPushButton(t("settings.tags.create_bind")), height=30, min_width=110)
        add_btn.setProperty("variant", "secondary")
        add_btn.clicked.connect(self._create_and_bind_tag)
        layout.addWidget(add_btn, 0, Qt.AlignLeft)
        return self._setting_card("settings.tags.room_title", "#8B5CF6", [
            self._setting_item("settings.tags.room_item", "settings.tags.room_desc", wrapper, help_section="ui")
        ])

    def _populate_room_tag_checks(self):
        from ui.i18n import t
        self._clear_layout(self._room_tag_list_layout)
        self._room_tag_checks = {}
        bound = {tag["id"] for tag in get_room_tags(self.room_id)}
        tags = get_tag_library()
        if not tags:
            empty = QLabel(t("settings.tags.empty_room"))
            empty.setProperty("role", "muted")
            empty.setWordWrap(True)
            self._room_tag_list_layout.addWidget(empty)
            return
        for tag in tags:
            cb = QCheckBox(tag["name"])
            cb.setChecked(tag["id"] in bound)
            cb.setStyleSheet(f"color:{tag['color']}; font-weight:600;")
            cb.toggled.connect(self._save_room_tags)
            self._room_tag_checks[tag["id"]] = cb
            self._room_tag_list_layout.addWidget(cb)

    def _create_and_bind_tag(self):
        values = self._prompt_tag()
        if not values:
            return
        try:
            tag = upsert_tag(values[0], values[1])
            current = [item["id"] for item in get_room_tags(self.room_id)]
            set_room_tag_ids(self.room_id, current + [tag["id"]])
        except ValueError as exc:
            QMessageBox.warning(self, __import__("ui.i18n", fromlist=["t"]).t("common.error"), str(exc))
            return
        self._populate_room_tag_checks()
        self.saved.emit("tag_library")

    def _save_room_tags(self, *_args):
        selected = [tag_id for tag_id, cb in getattr(self, "_room_tag_checks", {}).items() if cb.isChecked()]
        set_room_tag_ids(self.room_id, selected)
        self.saved.emit("tag_ids")

    # ------------------------------------------------------------------
    # Cookie card (new, room-only)
    # ------------------------------------------------------------------
    def _build_cookie_card(self):
        from core.config import get_room_config as _grc
        from core.config import set_room_auth_mode

        mode = NoWheelComboBox()
        mode.setObjectName("roomAuthMode")
        _populate_combo(mode, "auth_mode")
        _set_combo_value(mode, _grc(self.room_id).get("auth_mode", "inherit"))
        self._combo_bindings.append((mode, "auth_mode", None))
        self._auth_mode_combo = mode
        mode.currentIndexChanged.connect(
            lambda _index: (set_room_auth_mode(self.room_id, mode.currentData(Qt.UserRole)), self.saved.emit("auth_mode"))
        )

        cred_widget = QWidget()
        cred_row = QHBoxLayout(cred_widget)
        cred_row.setContentsMargins(0, 0, 0, 0)
        cred_row.setSpacing(8)

        saved_val = _grc(self.room_id).get("sessdata", "")
        cred_summary = QLabel(self._format_credential_summary(saved_val))
        cred_summary.setProperty("role", "muted")
        cred_summary.setStyleSheet("font-size: 13px;")
        self._cred_summary_label = cred_summary

        edit_btn = self._compact_button(QPushButton(), height=30, min_width=52)
        self._bind_i18n(edit_btn, "settings.action.edit")
        edit_btn.setProperty("variant", "secondary")
        edit_btn.setStyleSheet("border: none;")
        edit_btn.clicked.connect(self._open_cookie_overlay)

        cred_row.addWidget(cred_summary)
        cred_row.addStretch()
        cred_row.addWidget(edit_btn)
        return self._setting_card("settings.group.cookie", "#F59E0B", [
            self._setting_item("account.room_mode", "account.room_mode_hint", mode),
            self._setting_item(
                "SESSDATA",
                "settings.room.cookie_desc",
                cred_widget,
                help_key="sessdata",
            ),
        ])


# ------------------------------------------------------------------
# Overlay entry point called from MainWindow.on_settings
# ------------------------------------------------------------------
def open_room_settings_overlay(room_id, uname, main_window):
    """Open a full-screen overlay on main_window with per-room settings."""
    from PySide6.QtGui import QFont as _QFont
    from ui.i18n import t

    overlay = QWidget(main_window)
    overlay.setObjectName("modalOverlay")
    overlay.resize(main_window.size())
    overlay.move(0, 0)
    overlay.show(); overlay.raise_()

    panel_w = min(main_window.width() - 80, 1340)
    panel_h = min(main_window.height() - 60, 920)
    panel = QFrame(main_window)
    panel.setObjectName("roomSettingsPanel")
    enable_surface(panel)
    panel.setFixedSize(panel_w, panel_h)
    panel.move(
        (main_window.width() - panel_w) // 2,
        (main_window.height() - panel_h) // 2,
    )
    panel.show(); panel.raise_()

    outer = QVBoxLayout(panel)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    # ---- top bar ----
    top_bar = QWidget()
    top_bar.setObjectName("roomSettingsTopBar")
    top_bar.setFixedHeight(56)
    top_layout = QHBoxLayout(top_bar)
    top_layout.setContentsMargins(24, 0, 16, 0)
    top_layout.setSpacing(10)

    icon_lbl = QLabel("\U0001f4f9")
    icon_lbl.setProperty("role", "link")
    icon_lbl.setStyleSheet("font-size: 18px;")

    room_title_lbl = QLabel(t("settings.room.title", name=uname))
    room_title_lbl.setFont(_QFont("Microsoft YaHei UI", 14, _QFont.Bold))
    room_title_lbl.setProperty("role", "title")

    badge_lbl = QLabel(f"Room {room_id}")
    badge_lbl.setObjectName("roomBadge")

    note_lbl = QLabel(t("settings.room.inherit_note"))
    note_lbl.setObjectName("inheritBadge")

    close_btn = QPushButton("×")
    close_btn.setFixedSize(34, 34)
    close_btn.setCursor(Qt.PointingHandCursor)
    close_btn.setToolTip(t("common.close"))
    close_btn.setProperty("variant", "close")
    from ui.theme import repolish
    repolish(close_btn)

    top_layout.addWidget(icon_lbl)
    top_layout.addWidget(room_title_lbl)
    top_layout.addWidget(badge_lbl)
    top_layout.addStretch()
    top_layout.addWidget(note_lbl)
    top_layout.addSpacing(8)
    top_layout.addWidget(close_btn)
    outer.addWidget(top_bar)

    # ---- content: RoomSettingsPage (has its own internal scroll) ----
    page = RoomSettingsPage(room_id, uname, panel)
    page.setContentsMargins(24, 18, 24, 18)
    outer.addWidget(page, 1)

    def _close():
        # Room tag choices save immediately; refresh the card before destroying overlay.
        card = getattr(main_window, "cards", {}).get(str(room_id))
        if card is not None and hasattr(card, "refresh_custom_tags"):
            card.refresh_custom_tags()
        if hasattr(main_window, "_setup_filter_menu"):
            main_window._setup_filter_menu()
        if hasattr(main_window, "request_rearrange_cards"):
            main_window.request_rearrange_cards(0)
        overlay.hide(); overlay.deleteLater()
        panel.hide(); panel.deleteLater()

    close_btn.clicked.connect(_close)
    overlay.mousePressEvent = lambda e: _close()


# ------------------------------------------------------------------
# Overlay entry point called from MainWindow.add_channel
# ------------------------------------------------------------------
def open_add_channel_overlay(main_window):
    """添加直播间 — overlay 风格(全屏半透明遮罩 + 居中输入框)。

    复用 AddChannelDialog 已有的输入 + 错误提示 + 异步加载直播间信息逻辑,
    但不用 QDialog 模态弹窗,而是作为普通 widget 嵌到全屏遮罩中央。
    加载完成后调 main_window.add_card 添加卡片,再关掉 overlay。
    """
    from ui.i18n import t

    # 1) 全屏半透明遮罩
    overlay = QWidget(main_window)
    overlay.setObjectName("modalOverlay")
    overlay.resize(main_window.size())
    overlay.move(0, 0)
    overlay.show(); overlay.raise_()

    # 2) 嵌入 AddChannelDialog,去掉 QDialog 模态边框
    dialog = AddChannelDialog(main_window)
    dialog.setWindowFlags(Qt.Widget)
    dialog.setModal(False)
    dialog.setFixedSize(420, 260)

    # 顶部加一个小标题
    title = QLabel(t("add_room.title"))
    title.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    title.setProperty("role", "title")
    dialog.layout().insertWidget(0, title)

    # 居中
    dialog.move(
        (main_window.width() - dialog.width()) // 2,
        (main_window.height() - dialog.height()) // 2,
    )
    dialog.show(); dialog.raise_()

    # 3) 关闭 hook
    def _close():
        overlay.hide(); overlay.deleteLater()
        dialog.hide(); dialog.deleteLater()

    # 4) accepted/rejected 信号: dialog.confirm() 完成后会调 self.accept()
    #    (因为我们 setWindowFlags(Qt.Widget) 不再真正关闭,只触发信号)
    def _on_accepted():
        info = dialog.result
        if info:
            main_window.add_card(info, animate=True)
            main_window.show_notification(
                t("notify.add_ok_body", name=info["uname"]),
                t("notify.add_ok_title"),
                "success",
                merge_key=f"add:{info['room_id']}",
            )
        _close()

    def _on_rejected():
        _close()

    dialog.accepted.connect(_on_accepted)
    dialog.rejected.connect(_on_rejected)

    # 5) 点击 overlay 空白处也关
    def _on_overlay_click(event):
        if not dialog.geometry().contains(event.pos()):
            _close()
    overlay.mousePressEvent = _on_overlay_click
