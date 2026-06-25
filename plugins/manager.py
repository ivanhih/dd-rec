"""
插件管理界面 - 管理已安装的插件（启用/禁用/卸载）
"""
import logging
from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QMessageBox,
    QSizePolicy, QSpacerItem
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from plugins import Plugin, PluginManager, PluginState

logger = logging.getLogger(__name__)


class PluginItem(QFrame):
    """插件项"""

    enabled_changed = Signal(str, bool)  # plugin_id, enabled
    uninstall_requested = Signal(str)   # plugin_id

    def __init__(self, plugin: Plugin, parent=None):
        super().__init__(parent)
        self.plugin = plugin

        self._setup_ui()
        self._update_state()

    def _setup_ui(self):
        self.setFrameStyle(QFrame.StyledPanel)
        self.setMinimumHeight(80)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setStyleSheet("""
            QFrame {
                background-color: #252631;
                border: 1px solid #3D3D4A;
                border-radius: 8px;
                padding: 12px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setSpacing(16)

        # 插件信息
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)

        # 名称和版本
        name_layout = QHBoxLayout()
        name_layout.setSpacing(8)

        self.name_label = QLabel(self.plugin.info.name)
        name_label_font = QFont("Microsoft YaHei UI", 12, QFont.Bold)
        self.name_label.setFont(name_label_font)
        self.name_label.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")
        name_layout.addWidget(self.name_label)

        self.version_label = QLabel(f"v{self.plugin.info.version}")
        self.version_label.setFont(QFont("Consolas", 10))
        self.version_label.setStyleSheet("color: #94A3B8; background: transparent; border: none;")
        name_layout.addWidget(self.version_label)

        name_layout.addStretch()

        info_layout.addLayout(name_layout)

        # 描述
        self.desc_label = QLabel(self.plugin.info.description or "无描述")
        self.desc_label.setWordWrap(True)
        self.desc_label.setStyleSheet("color: #CBD5E1; background: transparent; border: none;")
        info_layout.addWidget(self.desc_label)

        # 作者
        if self.plugin.info.author:
            author_label = QLabel(f"作者: {self.plugin.info.author}")
            author_label.setStyleSheet("color: #64748B; font-size: 11px; background: transparent; border: none;")
            info_layout.addWidget(author_label)

        layout.addLayout(info_layout, 1)

        # 开关按钮
        self.enable_switch = QPushButton()
        self.enable_switch.setFixedWidth(60)
        self.enable_switch.setCursor(Qt.PointingHandCursor)
        self.enable_switch.clicked.connect(self._on_toggle)
        layout.addWidget(self.enable_switch)

        # 卸载按钮
        self.uninstall_btn = QPushButton("卸载")
        self.uninstall_btn.setFixedWidth(60)
        self.uninstall_btn.setCursor(Qt.PointingHandCursor)
        self.uninstall_btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #EF4444;
                border: 1px solid #EF4444;
                border-radius: 6px;
                padding: 6px 12px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #EF4444;
                color: white;
            }
        """)
        self.uninstall_btn.clicked.connect(self._on_uninstall)
        layout.addWidget(self.uninstall_btn)

    def _update_state(self):
        """更新状态显示"""
        is_enabled = self.plugin.state == PluginState.ENABLED

        if is_enabled:
            self.enable_switch.setText("禁用")
            self.enable_switch.setStyleSheet("""
                QPushButton {
                    background-color: #22C55E;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #16A34A;
                }
            """)
        else:
            self.enable_switch.setText("启用")
            self.enable_switch.setStyleSheet("""
                QPushButton {
                    background-color: #252631;
                    color: #E2E8F0;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 12px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #3B82F6;
                }
            """)

    def _on_toggle(self):
        """切换启用状态"""
        self.enabled_changed.emit(self.plugin.info.id, self.plugin.state != PluginState.ENABLED)

    def _on_uninstall(self):
        """卸载请求"""
        reply = QMessageBox.question(
            self.window(),
            "确认卸载",
            f"确定要卸载插件「{self.plugin.info.name}」吗？",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply == QMessageBox.Yes:
            self.uninstall_requested.emit(self.plugin.info.id)


class PluginManagerWidget(QWidget):
    """插件管理界面"""

    def __init__(self, plugin_manager: PluginManager, parent=None):
        super().__init__(parent)
        self.plugin_manager = plugin_manager

        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        self.setStyleSheet("background-color: #0F0F13;")

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # 标题栏
        header_layout = QHBoxLayout()

        title_label = QLabel("插件管理")
        title_label.setFont(QFont("Microsoft YaHei UI", 20, QFont.Bold))
        title_label.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")
        header_layout.addWidget(title_label)

        header_layout.addStretch()

        self.refresh_btn = QPushButton("刷新")
        self.refresh_btn.setFixedWidth(80)
        self.refresh_btn.setCursor(Qt.PointingHandCursor)
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #252631;
                color: #E2E8F0;
                border: none;
                border-radius: 6px;
                padding: 8px 16px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #2D2E3A;
            }
        """)
        self.refresh_btn.clicked.connect(self.refresh)
        header_layout.addWidget(self.refresh_btn)

        layout.addLayout(header_layout)

        # 插件列表滚动区域
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
        """)

        self.plugins_container = QWidget()
        self.plugins_layout = QVBoxLayout(self.plugins_container)
        self.plugins_layout.setSpacing(12)
        self.plugins_layout.setAlignment(Qt.AlignTop)

        self.scroll.setWidget(self.plugins_container)
        layout.addWidget(self.scroll)

        # 空状态提示
        self.empty_label = QLabel("暂无已安装的插件\n\n前往「插件商店」下载更多插件")
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.empty_label.setFont(QFont("Microsoft YaHei UI", 12))
        self.empty_label.setStyleSheet("color: #64748B; background: transparent; border: none;")
        self.empty_label.setVisible(False)
        layout.addWidget(self.empty_label)

    def refresh(self):
        """刷新插件列表"""
        plugins = self.plugin_manager.get_all_plugins()

        # 清除现有内容
        while self.plugins_layout.count():
            item = self.plugins_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not plugins:
            self.empty_label.setVisible(True)
            self.scroll.setVisible(False)
            return

        self.empty_label.setVisible(False)
        self.scroll.setVisible(True)

        for plugin in plugins:
            item = PluginItem(plugin)
            item.enabled_changed.connect(self._on_enabled_changed)
            item.uninstall_requested.connect(self._on_uninstall_requested)
            self.plugins_layout.addWidget(item)

    def _on_enabled_changed(self, plugin_id: str, enable: bool):
        """处理启用状态改变"""
        if enable:
            success = self.plugin_manager.enable_plugin(plugin_id)
        else:
            success = self.plugin_manager.disable_plugin(plugin_id)

        if success:
            self.refresh()
        else:
            QMessageBox.warning(
                self, "操作失败",
                "插件操作失败，请查看日志获取更多信息"
            )

    def _on_uninstall_requested(self, plugin_id: str):
        """处理卸载请求"""
        if self.plugin_manager.uninstall_plugin(plugin_id):
            self.refresh()
            QMessageBox.information(
                self, "卸载成功",
                "插件已卸载"
            )
