# ui/settings_dialog.py
import threading
import logging
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, 
    QComboBox, QPushButton, QCheckBox, QScrollArea, QFrame,
    QFormLayout, QTabWidget, QWidget, QTextEdit, QFileDialog
)
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QFont

from core.config import get_room_config, get_global_setting, get_effective_format, VIDEO_SAVE_DIR, save_config
from core.config import set_global_setting
from ui.room_card import ToggleSwitch


class AddChannelDialog(QDialog):
    """添加直播间对话框"""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("添加直播间")
        self.setModal(True)
        self.setFixedSize(400, 200)
        self.result = None  # 保存获取到的房间信息
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # 提示标签
        tip_label = QLabel("请输入直播间号或完整链接")
        tip_label.setStyleSheet("color: #94A3B8; font-size: 13px;")
        layout.addWidget(tip_label)

        # 输入框
        self.input = QLineEdit()
        self.input.setPlaceholderText("例如：23058 或 https://live.bilibili.com/23058")
        self.input.setStyleSheet("""
            QLineEdit {
                background-color: #181920;
                border: 1px solid #2D2E3A;
                border-radius: 10px;
                padding: 12px 16px;
                color: #E2E8F0;
                font-size: 14px;
            }
            QLineEdit:focus {
                border: 1px solid #3B82F6;
            }
        """)
        self.input.returnPressed.connect(self.confirm)
        layout.addWidget(self.input)
        
        # 错误提示标签（初始隐藏）
        self.error_label = QLabel("获取直播间信息失败，请检查输入")
        self.error_label.setStyleSheet("color: #EF4444; font-size: 12px;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        # 按钮
        btn_layout = QHBoxLayout()
        btn_layout.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedWidth(100)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #252631;
                color: #94A3B8;
                border-radius: 10px;
                padding: 10px 24px;
                font-size: 14px;
                border: none;
            }
            QPushButton:hover {
                background-color: #2D2E3A;
                color: white;
            }
        """)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(cancel_btn)

        self.confirm_btn = QPushButton("添加")
        self.confirm_btn.setFixedWidth(100)
        self.confirm_btn.setStyleSheet("""
            QPushButton {
                background-color: #3B82F6;
                color: white;
                border-radius: 10px;
                padding: 10px 24px;
                font-size: 14px;
                font-weight: 600;
                border: none;
            }
            QPushButton:hover {
                background-color: #2563EB;
            }
            QPushButton:disabled {
                background-color: #252631;
                color: #64748B;
            }
        """)
        self.confirm_btn.clicked.connect(self.confirm)
        btn_layout.addWidget(self.confirm_btn)

        layout.addLayout(btn_layout)

    def confirm(self):
        text = self.input.text().strip()
        if not text:
            return
        
        # 禁用按钮避免重复点击
        self.confirm_btn.setEnabled(False)
        self.confirm_btn.setText("添加...")
        self.error_label.setVisible(False)
        
        # 使用 QTimer.singleShot 立即返回，让按钮先更新
        QTimer.singleShot(0, lambda: self._do_add(text))
    
    def _do_add(self, text):
        from core.bili_api import get_bili_info
        try:
            info = get_bili_info(text)
            if info:
                self.result = info
                self.accept()
            else:
                self.error_label.setVisible(True)
                QTimer.singleShot(3000, lambda: self.error_label.setVisible(False))
        except Exception as e:
            self.error_label.setVisible(True)
            QTimer.singleShot(3000, lambda: self.error_label.setVisible(False))
        finally:
            # 恢复按钮
            self.confirm_btn.setEnabled(True)
            self.confirm_btn.setText("添加")


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
        self.format_combo = QComboBox()
        self.format_combo.addItems(["继承全局", "mp4", "ts", "flv"])
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

        cancel_btn = QPushButton("取消")
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

        save_btn = QPushButton("保存")
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
            index = self.format_combo.findText(format_value)
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
            cfg["format"] = self.format_combo.currentText()
        
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

        self.stream_codec_combo = QComboBox()
        self.stream_codec_combo.addItems(["av1", "hevc", "h264"])
        stream_layout.addRow(QLabel("优先编码:"), self.stream_codec_combo)

        self.stream_resolution_combo = QComboBox()
        self.stream_resolution_combo.addItems(["原画", "超清", "高清", "流畅"])
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

        self.convert_format_combo = QComboBox()
        self.convert_format_combo.addItems(["mp4", "ts", "flv"])
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

        codec_index = self.stream_codec_combo.findText(get_global_setting("stream_codec"))
        if codec_index >= 0:
            self.stream_codec_combo.setCurrentIndex(codec_index)

        res_index = self.stream_resolution_combo.findText(get_global_setting("stream_resolution"))
        if res_index >= 0:
            self.stream_resolution_combo.setCurrentIndex(res_index)

        fmt_index = self.convert_format_combo.findText(get_global_setting("convert_format"))
        if fmt_index >= 0:
            self.convert_format_combo.setCurrentIndex(fmt_index)

    def save_settings(self):
        # 保存录制设置
        set_global_setting("split_by_duration", self.split_duration_input.text())
        set_global_setting("split_by_size", self.split_size_input.text())
        set_global_setting("split_on_title_change", self.split_title_check.isChecked())
        set_global_setting("split_on_category_change", self.split_category_check.isChecked())
        set_global_setting("stream_codec", self.stream_codec_combo.currentText())
        set_global_setting("stream_resolution", self.stream_resolution_combo.currentText())
        set_global_setting("auto_switch_stream", self.auto_switch_check.isChecked())
        
        # 保存保存设置
        set_global_setting("save_dir", self.save_dir_input.text())
        set_global_setting("convert_format", self.convert_format_combo.currentText())
        self.saved.emit()


class GlobalSettingsOldStyleReplicaPage(QWidget):
    """参考 oldmain.py 复刻的全局设置页面，暂不接入当前主界面入口。"""
    saved = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._controls = {}
        self._build_ui()
        self._load_values()

    def _build_ui(self):
        self.setStyleSheet("""
            QWidget {
                background-color: transparent;
                color: #E2E8F0;
            }
            QFrame#settingsCard {
                background-color: #1A1B21;
                border: 1px solid #272833;
                border-radius: 14px;
            }
            QFrame#settingDivider {
                background-color: #252631;
                min-height: 1px;
                max-height: 1px;
                border: none;
            }
            QLabel[role="title"] {
                color: #F8FAFC;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel[role="desc"] {
                color: #94A3B8;
                font-size: 12px;
            }
            QLabel[role="itemTitle"] {
                color: #E2E8F0;
                font-size: 13px;
                font-weight: 600;
            }
            QLabel[role="itemDesc"] {
                color: #94A3B8;
                font-size: 12px;
            }
            QLineEdit, QComboBox, QTextEdit {
                background-color: #15161D;
                border: 1px solid #2D2E3A;
                border-radius: 10px;
                padding: 8px 10px;
                color: #E2E8F0;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus, QTextEdit:focus {
                border: 1px solid #3B82F6;
            }
            QCheckBox {
                color: #CBD5E1;
                spacing: 8px;
                font-size: 13px;
            }
            QPushButton {
                background-color: #252631;
                color: #E2E8F0;
                border: none;
                border-radius: 10px;
                padding: 9px 14px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2D2E3A;
            }
            QPushButton#primaryBtn {
                background-color: #3B82F6;
                color: white;
            }
            QPushButton#primaryBtn:hover {
                background-color: #2563EB;
            }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(12)

        header_left = QHBoxLayout()
        header_left.setSpacing(10)
        icon = QLabel("⚙")
        icon.setStyleSheet("color: #2D9CDB; font-size: 22px; font-weight: 700;")
        title = QLabel("全局设置")
        title.setFont(QFont("Microsoft YaHei UI", 22, QFont.Bold))
        title.setStyleSheet("color: #F8FAFC;")
        header_left.addWidget(icon)
        header_left.addWidget(title)
        header_left.addStretch()

        badge = QLabel("所有更改实时保存，立即生效")
        badge.setStyleSheet("""
            color: #4ADE80;
            background-color: #15251A;
            border: 1px solid #1E3A26;
            border-radius: 8px;
            padding: 6px 10px;
            font-size: 12px;
            font-weight: 600;
        """)

        header.addLayout(header_left, 1)
        header.addWidget(badge, 0, Qt.AlignRight | Qt.AlignVCenter)
        root.addLayout(header)
        root.addSpacing(14)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet("""
            QScrollArea {
                border: none;
                background: transparent;
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

        content = QWidget()
        content_layout = QHBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(20)

        left_col = QVBoxLayout()
        left_col.setSpacing(18)
        left_col.addWidget(self._build_appearance_card())
        left_col.addWidget(self._build_file_split_card())
        left_col.addWidget(self._build_network_card())
        left_col.addWidget(self._build_stream_record_card())
        left_col.addWidget(self._build_chat_record_card())
        left_col.addWidget(self._build_schedule_card())
        left_col.addWidget(self._build_automation_card())
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

    # 通知模板默认值
    # Template defaults are now in config.py
    # Template defaults are now in config.py

    def _setting_card(self, title_text, color, items):
        card = QFrame()
        card.setObjectName("settingsCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)

        header = QHBoxLayout()
        header.setSpacing(10)

        accent = QFrame()
        accent.setFixedWidth(4)
        accent.setStyleSheet(f"background-color: {color}; border-radius: 2px; border: none;")

        title = QLabel(title_text)
        title.setProperty("role", "title")

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

    def _setting_item(self, title_text, desc_text, control):
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        title = QLabel(title_text)
        title.setProperty("role", "itemTitle")
        desc = QLabel(desc_text)
        desc.setProperty("role", "itemDesc")
        desc.setWordWrap(True)

        text_col.addWidget(title)
        text_col.addWidget(desc)

        layout.addLayout(text_col, 1)
        layout.addWidget(control, 0, Qt.AlignRight | Qt.AlignVCenter)
        return wrapper

    def _bind_line_edit(self, widget, key):
        widget.editingFinished.connect(lambda k=key, w=widget: self._save_setting(k, w.text()))
        self._controls[key] = widget
        return widget

    def _bind_checkbox(self, widget, key):
        widget.toggled.connect(lambda checked, k=key: self._save_setting(k, checked))
        self._controls[key] = widget
        return widget

    def _bind_combo(self, widget, key):
        widget.currentTextChanged.connect(lambda text, k=key: self._save_setting(k, text))
        self._controls[key] = widget
        return widget

    def _line_edit(self, key, hint="", width=180):
        widget = QLineEdit()
        widget.setPlaceholderText(hint)
        widget.setFixedWidth(width)
        return self._bind_line_edit(widget, key)

    def _combo(self, key, options, width=130):
        widget = QComboBox()
        widget.addItems(options)
        widget.setFixedWidth(width)
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
        button = QPushButton("📁")
        button.setFixedSize(42, 40)
        button.setToolTip("选择文件夹")
        button.clicked.connect(lambda: self._choose_directory(key, line_edit))

        layout.addWidget(line_edit)
        layout.addWidget(button)
        return wrapper

    def _template_editor_button(self, key):
        button = QPushButton("编辑路径模板")
        button.setFixedWidth(140)
        button.clicked.connect(lambda: self._open_template_editor_dialog(key))
        return button

    def _build_appearance_card(self):
        return self._setting_card("外观", "#7B61FF", [
            self._setting_item("语言", "界面显示语言", self._combo("language", ["简体中文"], 120)),
            self._setting_item("主题", "应用主题模式", self._combo("theme", ["深色"], 120)),
        ])

    @staticmethod
    def _format_bytes(val: int) -> str:
        """把字节数格式化成可读字符串"""
        if val <= 0:
            return "禁用"
        if val < 1024:
            return f"{val} B"
        if val < 1024 ** 2:
            return f"{val / 1024:.2f} KB"
        if val < 1024 ** 3:
            return f"{val / 1024 ** 2:.2f} MB"
        return f"{val / 1024 ** 3:.2f} GB"

    def _build_size_input(self):
        """文件大小：一个摘要按钮行，点击后弹出内嵌面板编辑"""
        wrapper = QWidget()
        wrapper.setFixedHeight(36)
        row = QHBoxLayout(wrapper)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        # 摘要标签（显示当前值）
        summary = QLabel("禁用")
        summary.setStyleSheet("color: #64748B; font-size: 13px;")

        # 初始化摘要
        raw = get_global_setting("split_by_size")
        try:
            init_val = int(raw) if raw and str(raw).isdigit() else 0
        except (ValueError, TypeError):
            init_val = 0
        summary.setText(self._format_bytes(init_val))

        edit_btn = QPushButton("编辑")
        edit_btn.setFixedWidth(52)
        edit_btn.setFixedHeight(30)
        edit_btn.setStyleSheet("""
            QPushButton {
                background: #2D2E3A; border: none;
                border-radius: 8px; color: #94A3B8; font-size: 12px;
            }
            QPushButton:hover { background: #3B82F6; color: white; }
        """)

        # 保存摘要引用供 overlay 保存后更新
        self._size_summary_label = summary

        edit_btn.clicked.connect(lambda: self._open_size_overlay())

        row.addWidget(summary)
        row.addStretch()
        row.addWidget(edit_btn)

        # 不注册进 _controls，直接在 overlay 里读写
        return wrapper

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
        mask.setStyleSheet("background: rgba(0,0,0,0.55);")
        mask.resize(root.size())
        mask.move(0, 0)
        mask.show()
        mask.raise_()

        # 面板
        panel = QFrame(root)
        panel.setObjectName("sizePanel")
        panel.setFixedSize(440, 290)
        panel.setStyleSheet("""
            QFrame#sizePanel {
                background: #1A1B21;
                border: 1px solid #2D2E3A;
                border-radius: 14px;
            }
            QLabel {
                background: transparent;
                color: #E2E8F0;
            }
        """)
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
        title = QLabel("按文件大小分割")
        title.setStyleSheet("color: #E2E8F0; font-size: 17px; font-weight: 600; background: transparent;")
        vbox.addWidget(title)

        # 禁用/启用行
        enabled_row = QHBoxLayout()
        enabled_lbl = QLabel("启用")
        enabled_lbl.setStyleSheet("color: #CBD5E1; font-size: 15px; background: transparent;")
        enabled_toggle = ToggleSwitch()
        enabled_row.addWidget(enabled_lbl)
        enabled_row.addStretch()
        enabled_row.addWidget(enabled_toggle)
        vbox.addLayout(enabled_row)

        # 输入行
        input_row = QHBoxLayout()
        inp = QLineEdit()
        inp.setPlaceholderText("输入字节数")
        inp.setFixedWidth(180)
        inp.setStyleSheet("""
            QLineEdit {
                background: #252631; border: 1px solid #2D2E3A;
                border-radius: 8px; padding: 10px 14px;
                color: #E2E8F0; font-size: 16px;
            }
            QLineEdit:focus { border: 1px solid #3B82F6; }
            QLineEdit:disabled { color: #4B5563; background: #1A1B21; }
        """)
        unit_lbl = QLabel("字节")
        unit_lbl.setStyleSheet("color: #64748B; font-size: 13px; background: transparent;")

        preview = QLabel("—")
        preview.setStyleSheet("color: #64748B; font-size: 13px; min-width: 110px; background: transparent;")

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
                preview.setStyleSheet("color: #64748B; font-size: 13px; min-width: 110px; background: transparent;")
            elif val < 10 * 1024 * 1024:
                preview.setText(f"≈ {self._format_bytes(val)}  ⚠️ 建议 ≥ 10 MB")
                preview.setStyleSheet("color: #F59E0B; font-size: 13px; min-width: 110px; background: transparent;")
            else:
                preview.setText(f"≈ {self._format_bytes(val)}")
                preview.setStyleSheet("color: #60A5FA; font-size: 13px; min-width: 110px; background: transparent;")

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
            preview.setStyleSheet("color: #60A5FA; font-size: 13px; min-width: 110px; background: transparent;")

        def _on_toggle(checked):
            inp.setEnabled(checked)
            if not checked:
                inp.setText("")
                preview.setText("—")
                preview.setStyleSheet("color: #64748B; font-size: 13px; min-width: 110px; background: transparent;")
        enabled_toggle.toggled.connect(_on_toggle)

        # 按钮行
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedWidth(90)
        cancel_btn.setFixedHeight(36)
        cancel_btn.setStyleSheet("""
            QPushButton {
                background: #2D2E3A; border: none; border-radius: 8px;
                color: #94A3B8; font-size: 13px;
            }
            QPushButton:hover { background: #374151; color: white; }
        """)
        save_btn = QPushButton("保存")
        save_btn.setFixedWidth(90)
        save_btn.setFixedHeight(36)
        save_btn.setStyleSheet("""
            QPushButton {
                background: #3B82F6; border: none; border-radius: 8px;
                color: white; font-size: 13px; font-weight: 600;
            }
            QPushButton:hover { background: #2563EB; }
        """)

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
            b.setStyleSheet("""
                QLineEdit {
                    background: #181920;
                    border: 1px solid #2D2E3A;
                    border-radius: 8px;
                    padding: 6px 4px;
                    color: #E2E8F0;
                    font-size: 14px;
                }
                QLineEdit:focus { border: 1px solid #3B82F6; }
            """)
            return b

        h_box = _small_box("时", 99)
        m_box = _small_box("分", 59)
        s_box = _small_box("秒", 59)

        def _sep():
            lbl = QLabel(":")
            lbl.setStyleSheet("color: #64748B; font-size: 16px;")
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

        reset_btn = QPushButton("重置")
        reset_btn.setFixedWidth(48)
        reset_btn.setFixedHeight(34)
        reset_btn.setStyleSheet("""
            QPushButton {
                background: #2D2E3A;
                border: none;
                border-radius: 8px;
                color: #94A3B8;
                font-size: 12px;
            }
            QPushButton:hover { background: #3B82F6; color: white; }
        """)

        def _reset():
            h_box.setText("1")
            m_box.setText("00")
            s_box.setText("00")

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
        return self._setting_card("✂️ 文件分割", "#EB5757", [
            self._setting_item("文件大小", "输入字节数，留空或 0 表示不使用", self._build_size_input()),
            self._setting_item("视频时长", "留空或 00:00:00 表示不使用，默认 1 小时", self._build_duration_input()),
            self._setting_item("编码改变", "在编码改变处自动切割文件", self._check("split_on_codec_change")),
            self._setting_item("流不连续", "在流不连续处自动切割文件", self._check("split_on_stream_discontinuity")),
            self._setting_item("标题改变", "直播标题改变时自动切割", self._check("split_on_title_change")),
            self._setting_item("类别改变", "直播类别改变时自动切割", self._check("split_on_category_change")),
        ])

    def _build_network_card(self):
        proxy_box = QWidget()
        row = QHBoxLayout(proxy_box)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        mode_combo = self._combo("proxy_mode", ["禁用", "系统", "自定义"], 110)
        proxy_input = self._line_edit("proxy", "http://127.0.0.1:7890", 180)
        bypass_input = self._line_edit("proxy_bypass", "localhost,127.0.0.1", 180)

        def _update_proxy_state(mode_text):
            enabled = (mode_text == "自定义")
            proxy_input.setEnabled(enabled)
            bypass_input.setEnabled(enabled)
            dim = "color: #4B5563;" if not enabled else ""
            proxy_input.setStyleSheet(proxy_input.styleSheet() + (
                " QLineEdit { color: #4B5563; background: #131419; }" if not enabled else ""
            ))

        # 初始状态
        init_mode = get_global_setting("proxy_mode") or "禁用"
        _update_proxy_state(init_mode)

        # 模式切换时联动
        mode_combo.currentTextChanged.connect(_update_proxy_state)

        row.addWidget(mode_combo)
        row.addWidget(proxy_input)
        return self._setting_card("🌐 网络", "#27AE60", [
            self._setting_item("全局代理", "HTTP/SOCKS5 代理地址与模式", proxy_box),
            self._setting_item("绕过列表", "代理绕过规则，多个用逗号分隔", bypass_input),
        ])

    def _build_stream_record_card(self):
        return self._setting_card("📡 直播流录制", "#2D9CDB", [
            self._setting_item("启用录制", "全局开关，关闭后所有房间停止录制", self._check("stream_record_enabled")),
            self._setting_item("允许仅音频", "允许录制仅音频的直播流", self._check("allow_audio_only")),
            self._setting_item("自动切换", "有新画质或格式时自动切换流", self._check("auto_switch_stream")),
            self._setting_item("流优先参数", "优先级排序依据", self._combo("stream_priority_param", ["分辨率", "帧率", "码率", "编码", "格式", "网址"], 120)),
            self._setting_item("分辨率优先", "优先选择的分辨率", self._combo("stream_resolution", ["原画", "超清", "高清", "流畅"], 110)),
            self._setting_item("帧率优先", "优先选择的帧率", self._combo("stream_fps", ["30 fps", "60 fps", "120 fps", "25 fps", "20 fps", "15 fps"], 110)),
            self._setting_item("码率优先", "优先选择的码率", self._line_edit("stream_bitrate", "30.0 Mb/s", 120)),
            self._setting_item("编码优先", "优先选择的编码", self._combo("stream_codec", ["av1", "hevc", "h264"], 100)),
            self._setting_item("格式优先", "优先选择的封装格式", self._combo("stream_format", ["fmp4", "flv", "ts"], 100)),
        ])

    def _build_chat_record_card(self):
        # 凭据行：显示当前状态 + 编辑按钮
        cred_widget = QWidget()
        cred_row = QHBoxLayout(cred_widget)
        cred_row.setContentsMargins(0, 0, 0, 0)
        cred_row.setSpacing(8)

        cred_summary = QLabel("未设置")
        cred_summary.setStyleSheet("color: #64748B; font-size: 13px; background: transparent;")
        saved = get_global_setting("chat_credential") or ""
        if saved:
            masked = saved[:6] + "..." + saved[-4:] if len(saved) > 10 else "已设置"
            cred_summary.setText(masked)
        self._cred_summary_label = cred_summary

        edit_btn = QPushButton("编辑")
        edit_btn.setFixedWidth(52)
        edit_btn.setFixedHeight(30)
        edit_btn.setStyleSheet("""
            QPushButton { background:#2D2E3A; border:none; border-radius:8px; color:#94A3B8; font-size:12px; }
            QPushButton:hover { background:#3B82F6; color:white; }
        """)
        edit_btn.clicked.connect(self._open_cookie_overlay)

        cred_row.addWidget(cred_summary)
        cred_row.addStretch()
        cred_row.addWidget(edit_btn)

        return self._setting_card("💬 聊天消息录制", "#BB6BD9", [
            self._setting_item("启用", "录制直播间弹幕和聊天消息", self._check("chat_record_enabled")),
            self._setting_item("凭据", "下载聊天消息使用的 SESSDATA（B站 cookie）", cred_widget),
            self._setting_item("输出格式", "聊天记录保存格式", self._combo("chat_format", ["jsonl 数据", "xml", "ass 弹幕"], 120)),
        ])

    def _open_cookie_overlay(self):
        """在设置页内部弹出 cookie 编辑面板"""
        root = self
        while root.parent() and not isinstance(root.parent(), QScrollArea):
            root = root.parent()

        mask = QWidget(root)
        mask.setStyleSheet("background: rgba(0,0,0,0.6);")
        mask.resize(root.size())
        mask.move(0, 0)
        mask.show()
        mask.raise_()

        panel = QFrame(root)
        panel.setObjectName("cookiePanel")
        panel.setFixedSize(520, 360)
        panel.setStyleSheet("""
            QFrame#cookiePanel {
                background: #1A1B21;
                border: 1px solid #2D2E3A;
                border-radius: 14px;
            }
            QLabel { background: transparent; }
        """)
        cx = (root.width() - panel.width()) // 2
        cy = (root.height() - panel.height()) // 2
        panel.move(cx, cy)
        panel.show()
        panel.raise_()

        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(28, 24, 28, 20)
        vbox.setSpacing(14)

        # 标题
        title = QLabel("编辑 B站 Cookie 凭据")
        title.setStyleSheet("color:#E2E8F0; font-size:16px; font-weight:600;")
        vbox.addWidget(title)

        hint = QLabel("输入你的 SESSDATA（在浏览器 B站 cookie 中找到）")
        hint.setStyleSheet("color:#64748B; font-size:12px;")
        vbox.addWidget(hint)

        # 输入框
        inp = QLineEdit()
        inp.setPlaceholderText("粘贴你的 SESSDATA …")
        inp.setEchoMode(QLineEdit.Password)
        inp.setStyleSheet("""
            QLineEdit {
                background:#252631; border:1px solid #2D2E3A;
                border-radius:8px; padding:10px 14px;
                color:#E2E8F0; font-size:14px;
            }
            QLineEdit:focus { border:1px solid #3B82F6; }
        """)
        saved = get_global_setting("chat_credential") or ""
        inp.setText(saved)

        show_btn = QPushButton("显示")
        show_btn.setFixedWidth(52)
        show_btn.setFixedHeight(36)
        show_btn.setStyleSheet("""
            QPushButton { background:#2D2E3A; border:none; border-radius:8px; color:#94A3B8; font-size:12px; }
            QPushButton:hover { background:#374151; color:white; }
        """)
        def _toggle_show():
            if inp.echoMode() == QLineEdit.Password:
                inp.setEchoMode(QLineEdit.Normal)
                show_btn.setText("隐藏")
            else:
                inp.setEchoMode(QLineEdit.Password)
                show_btn.setText("显示")
        show_btn.clicked.connect(_toggle_show)

        inp_row = QHBoxLayout()
        inp_row.setSpacing(6)
        inp_row.addWidget(inp, 1)
        inp_row.addWidget(show_btn)
        vbox.addLayout(inp_row)

        # 验证区域
        verify_btn = QPushButton("验证 Cookie")
        verify_btn.setFixedHeight(36)
        verify_btn.setStyleSheet("""
            QPushButton { background:#374151; border:none; border-radius:8px; color:#CBD5E1; font-size:13px; }
            QPushButton:hover { background:#4B5563; }
        """)
        vbox.addWidget(verify_btn)

        # 账号信息区（左下角）
        account_frame = QFrame()
        account_frame.setStyleSheet("background:#131419; border-radius:8px;")
        account_layout = QVBoxLayout(account_frame)
        account_layout.setContentsMargins(12, 10, 12, 10)
        account_layout.setSpacing(4)

        account_name = QLabel("— 尚未验证 —")
        account_name.setStyleSheet("color:#94A3B8; font-size:13px; background:transparent;")
        account_mid = QLabel("")
        account_mid.setStyleSheet("color:#64748B; font-size:11px; background:transparent;")
        account_layout.addWidget(account_name)
        account_layout.addWidget(account_mid)
        vbox.addWidget(account_frame, 1)

        def _verify():
            sessdata = inp.text().strip()
            if not sessdata:
                account_name.setText("❌ 请先输入 SESSDATA")
                account_mid.setText("")
                return
            verify_btn.setEnabled(False)
            verify_btn.setText("验证中…")
            account_name.setText("⌛ 验证中…")
            account_mid.setText("")

            def _do_verify():
                import urllib.request, json as _json
                try:
                    url = "https://api.bilibili.com/x/web-interface/nav"
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "Mozilla/5.0",
                        "Cookie": f"SESSDATA={sessdata}"
                    })
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        data = _json.loads(resp.read())
                    if data["code"] == 0 and data["data"]["isLogin"]:
                        uname = data["data"]["uname"]
                        mid = data["data"]["mid"]
                        account_name.setText(f"✅  {uname}")
                        account_mid.setText(f"UID: {mid}")
                        account_name.setStyleSheet("color:#4ADE80; font-size:14px; font-weight:600; background:transparent;")
                    else:
                        account_name.setText("❌ Cookie 无效或已过期")
                        account_mid.setText("")
                        account_name.setStyleSheet("color:#F87171; font-size:13px; background:transparent;")
                except Exception as e:
                    account_name.setText(f"❌ 网络错误：{e}")
                    account_mid.setText("")
                    account_name.setStyleSheet("color:#F87171; font-size:13px; background:transparent;")
                finally:
                    verify_btn.setEnabled(True)
                    verify_btn.setText("验证 Cookie")

            threading.Thread(target=_do_verify, daemon=True).start()

        verify_btn.clicked.connect(_verify)

        # 底部按钮行（右下角）
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedWidth(90)
        cancel_btn.setFixedHeight(36)
        cancel_btn.setStyleSheet("""
            QPushButton { background:#2D2E3A; border:none; border-radius:8px; color:#94A3B8; font-size:13px; }
            QPushButton:hover { background:#374151; color:white; }
        """)
        save_btn = QPushButton("保存")
        save_btn.setFixedWidth(90)
        save_btn.setFixedHeight(36)
        save_btn.setStyleSheet("""
            QPushButton { background:#3B82F6; border:none; border-radius:8px; color:white; font-size:13px; font-weight:600; }
            QPushButton:hover { background:#2563EB; }
        """)

        def _close():
            panel.hide(); panel.deleteLater()
            mask.hide(); mask.deleteLater()

        def _save():
            val = inp.text().strip()
            self._save_setting("chat_credential", val)
            if val:
                masked = val[:6] + "..." + val[-4:] if len(val) > 10 else "已设置"
                self._cred_summary_label.setText(masked)
            else:
                self._cred_summary_label.setText("未设置")
            _close()

        cancel_btn.clicked.connect(_close)
        save_btn.clicked.connect(_save)
        btn_row.addStretch()
        btn_row.addWidget(cancel_btn)
        btn_row.addWidget(save_btn)
        vbox.addLayout(btn_row)

    def _build_schedule_card(self):
        return self._setting_card("📅 录制计划", "#F2994A", [
            self._setting_item("时区", "录制计划使用的时区", self._combo("schedule_timezone", ["UTC", "Asia/Shanghai", "Asia/Tokyo", "America/New_York", "Europe/London"], 160)),
            self._setting_item("开始录制", "仅在此时间后开始（HH:MM，留空不限）", self._line_edit("schedule_start", "如: 08:00", 110)),
            self._setting_item("停止录制", "到达此时间后停止（HH:MM，留空不限）", self._line_edit("schedule_stop", "如: 23:00", 110)),
        ])

    def _build_automation_card(self):
        return self._setting_card("⚡ 自动化", "#56CCF2", [
            self._setting_item("Webhooks", "录制事件通知的 Webhook 地址（每行一个）", self._line_edit("webhooks", "https://...", 220)),
        ])

    def _build_file_location_card(self):
        return self._setting_card("📁 文件位置", "#F2C94C", [
            self._setting_item("保存目录", "所有录制文件的根目录", self._directory_field("save_dir", VIDEO_SAVE_DIR, 220)),
            self._setting_item("路径模板", "点击按钮弹出编辑器修改 liquid 模板", self._template_editor_button("path_template")),
        ])

    def _build_convert_card(self):
        return self._setting_card("🔄 转换格式", "#EB5757", [
            self._setting_item("启用转换", "录制完成后自动转换视频格式", self._check("convert_enabled")),
            self._setting_item("删除原文件", "转换成功后删除原始录制文件", self._check("convert_delete_source")),
            self._setting_item("目标格式", "转换的目标视频格式", self._combo("convert_format", ["mp4", "mkv", "ts", "flv"], 100)),
        ])

    def _build_monitor_card(self):
        return self._setting_card("👁️ 直播监控", "#2D9CDB", [
            self._setting_item("轮询延时", "每次轮询之间的等待时间", self._combo("monitor_delay", ["自动", "5 秒", "10 秒", "30 秒", "1 分钟"], 110)),
            self._setting_item("轮询间隔", "检查直播状态的时间间隔", self._combo("monitor_interval", ["自动", "10 秒", "30 秒", "1 分钟", "5 分钟"], 110)),
            self._setting_item("并发数", "同时轮询的房间数量上限", self._combo("monitor_concurrency", ["自动", "5", "10", "20", "50"], 110)),
            self._setting_item("防抖延迟", "下播状态确认延迟，防止误触发", self._combo("monitor_debounce", ["禁用", "30 秒", "1 分钟", "3 分钟", "5 分钟"], 110)),
            self._setting_item("监控代理", "专用于监控请求的代理地址", self._line_edit("monitor_proxy", "留空使用全局代理", 180)),
        ])

    def _build_cover_card(self):
        return self._setting_card("🖼️ 封面下载", "#BB6BD9", [
            self._setting_item("启用", "开播时自动下载直播封面图片", self._check("download_cover")),
        ])

    def _build_conditions_card(self):
        return self._setting_card("🎯 录制条件", "#F2994A", [
            self._setting_item("直播标题", "仅录制标题包含以下关键词的直播（多个用英文逗号分隔）", self._line_edit("condition_title", "留空不过滤", 200)),
            self._setting_item("直播类别", "仅录制分区包含以下关键词的直播（多个用英文逗号分隔）", self._line_edit("condition_category", "留空不过滤", 200)),
            self._setting_item("直播时段", "仅在指定时段录制（格式: 08:00-23:00，留空不限）", self._line_edit("condition_time_range", "如: 08:00-23:00", 140)),
        ])

    def _update_template_summary(self, key):
        if not hasattr(self, "_template_summary_labels"):
            return
        label = self._template_summary_labels.get(key)
        if not label:
            return
        value = (get_global_setting(key) or "").strip()
        if not value:
            label.setText("(未设置)")
            label.setStyleSheet("color: #64748B; font-size: 12px;")
        else:
            first_line = value.split("\n")[0][:60]
            display = first_line + ("..." if len(first_line) >= 60 else "")
            label.setText(display)
            label.setStyleSheet("color: #E2E8F0; font-size: 12px;")

    def _build_notify_template_item(self, key):
        wrapper = QWidget()
        layout = QHBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        summary = QLabel("(未设置)")
        summary.setStyleSheet("color: #64748B; font-size: 12px;")
        edit_btn = QPushButton("\u25b6")
        edit_btn.setFixedSize(32, 32)
        edit_btn.setToolTip("编辑模板")
        edit_btn.setStyleSheet("""QPushButton { background: transparent; border: none; color: #94A3B8; font-size: 14px; } QPushButton:hover { color: #3B82F6; }""")
        if not hasattr(self, "_template_summary_labels"):
            self._template_summary_labels = {}
        self._template_summary_labels[key] = summary
        self._update_template_summary(key)
        edit_btn.clicked.connect(lambda checked=False, k=key: self._open_notify_template_overlay(k, "编辑"))
        layout.addWidget(summary, 1)
        layout.addWidget(edit_btn)
        return wrapper

    def _open_notify_template_overlay(self, key, window_title):
        root = self
        while root.parent() and not isinstance(root.parent(), QScrollArea):
            root = root.parent()
        mask = QWidget(root)
        mask.setStyleSheet("background: rgba(0,0,0,0.6);")
        mask.resize(root.size())
        mask.move(0, 0)
        mask.show()
        mask.raise_()
        panel = QFrame(root)
        panel.setObjectName("notifyTemplatePanel")
        panel.setFixedSize(680, 480)
        panel.setStyleSheet('''QFrame#notifyTemplatePanel { background: #1A1B21; border: 1px solid #2D2E3A; border-radius: 14px; } QLabel { background: transparent; }''')
        cx = (root.width() - panel.width()) // 2
        cy = (root.height() - panel.height()) // 2
        panel.move(cx, cy)
        panel.show()
        panel.raise_()
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(24, 20, 24, 20)
        vbox.setSpacing(14)
        title = QLabel("编辑" + window_title)
        title.setStyleSheet("color: #E2E8F0; font-size: 16px; font-weight: 600;")
        vbox.addWidget(title)
        editor = QTextEdit()
        val = str(get_global_setting(key) or "")
        logging.info(f"TPL_OVERLAY key={key} len={len(val)}")
        editor.setPlainText(val)
        editor.setPlaceholderText("请输入 Liquid 模板...")
        editor.setStyleSheet('''QTextEdit { background-color: #15161D; border: 1px solid #2D2E3A; border-radius: 10px; padding: 12px; color: #E2E8F0; font-size: 12px; } QTextEdit:focus { border: 1px solid #3B82F6; }''')
        vbox.addWidget(editor, 1)
        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedWidth(90)
        cancel_btn.setFixedHeight(36)
        cancel_btn.setStyleSheet("QPushButton { background: #2D2E3A; border: none; border-radius: 8px; color: #94A3B8; font-size: 13px; } QPushButton:hover { background: #374151; color: white; }")
        save_btn = QPushButton("保存")
        save_btn.setFixedWidth(90)
        save_btn.setFixedHeight(36)
        save_btn.setStyleSheet("QPushButton { background: #3B82F6; border: none; border-radius: 8px; color: white; font-size: 13px; font-weight: 600; } QPushButton:hover { background: #2563EB; }")
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
        return self._setting_card("🔔 通知", "#56CCF2", [
            self._setting_item("启用通知", "开播/下播/错误时发送通知", self._check("notify_enabled")),
            self._setting_item("通知地址", "通知服务的 Webhook 地址", self._line_edit("notify_url", "https://...", 220)),
                        self._setting_item("标题模板", "通知标题的模板（{uname}, {room_id}, {title}, {time}）", self._build_notify_template_item("notify_title_template")),
                        self._setting_item("正文模板", "通知正文的模板（{uname}, {room_id}, {title}, {time}）", self._build_notify_template_item("notify_body_template")),
            self._setting_item("直播结束通知", "直播结束时发送通知", self._check("notify_on_live_end")),
            self._setting_item("错误通知", "发生录制错误时发送通知", self._check("notify_on_error")),
        ])

    def _build_system_card(self):
        return self._setting_card("⚙️ 系统", "#6FCF70", [
            self._setting_item("开机自启", "系统启动时自动运行本程序", self._check("auto_start")),
            self._setting_item("阻止休眠", "录制期间阻止系统进入休眠状态", self._check("prevent_sleep")),
        ])

    def _save_setting(self, key, value):
        set_global_setting(key, value)
        self.saved.emit(key)

    def _choose_directory(self, key, line_edit):
        current_dir = line_edit.text().strip() or str(VIDEO_SAVE_DIR)
        selected = QFileDialog.getExistingDirectory(self, "选择保存目录", current_dir)
        if selected:
            line_edit.setText(selected)
            self._save_setting(key, selected)

    def _open_template_editor_dialog(self, key):
        dialog = QDialog(self)
        dialog.setWindowTitle("编辑路径模板")
        dialog.setModal(True)
        dialog.resize(760, 560)
        dialog.setStyleSheet("""
            QDialog {
                background-color: #1A1B21;
            }
            QTextEdit {
                background-color: #15161D;
                border: 1px solid #2D2E3A;
                border-radius: 12px;
                padding: 12px;
                color: #E2E8F0;
                font-size: 12px;
            }
        """)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        title = QLabel("路径模板")
        title.setStyleSheet("color: #F8FAFC; font-size: 18px; font-weight: 700;")
        desc = QLabel("修改 Liquid 路径模板后，点击保存立即生效。")
        desc.setStyleSheet("color: #94A3B8; font-size: 12px;")

        editor = QTextEdit()
        editor.setPlainText(str(get_global_setting(key) or ""))
        editor.setPlaceholderText("请输入路径模板")

        button_row = QHBoxLayout()
        button_row.addStretch()

        cancel_btn = QPushButton("取消")
        cancel_btn.setFixedWidth(100)
        cancel_btn.clicked.connect(dialog.reject)

        save_btn = QPushButton("保存")
        save_btn.setObjectName("primaryBtn")
        save_btn.setFixedWidth(100)
        save_btn.clicked.connect(lambda: self._save_template_and_close(dialog, key, editor.toPlainText()))

        button_row.addWidget(cancel_btn)
        button_row.addWidget(save_btn)

        layout.addWidget(title)
        layout.addWidget(desc)
        layout.addWidget(editor, 1)
        layout.addLayout(button_row)
        dialog.exec()

    def _save_template_and_close(self, dialog, key, value):
        self._save_setting(key, value)
        dialog.accept()

    def _load_values(self):
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
                widget.setChecked(bool(value))
            elif isinstance(widget, QComboBox):
                text = "" if value is None else str(value)
                index = widget.findText(text)
                if index >= 0:
                    widget.setCurrentIndex(index)

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
