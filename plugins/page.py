"""
插件页面 - 整合插件商店和插件管理界面
"""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QTabWidget, QLabel
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from plugins.store import PluginStoreWidget
from plugins.manager import PluginManagerWidget


class PluginsPage(QWidget):
    """插件页面 - 整合商店和管理界面"""

    def __init__(self, plugin_manager, parent=None):
        super().__init__(parent)
        self.plugin_manager = plugin_manager

        self._setup_ui()

    def _setup_ui(self):
        self.setStyleSheet("background-color: #0F0F13;")

        layout = QVBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(32, 24, 32, 24)

        # Tab 切换
        self.tabs = QTabWidget()
        self.tabs.setStyleSheet("""
            QTabWidget::pane {
                border: none;
                background-color: #0F0F13;
            }
            QTabBar::tab {
                background-color: transparent;
                color: #94A3B8;
                padding: 12px 24px;
                font-size: 14px;
                border: none;
                font-family: "Microsoft YaHei UI";
            }
            QTabBar::tab:selected {
                color: #F8FAFC;
                background-color: transparent;
                border-bottom: 2px solid #3B82F6;
            }
            QTabBar::tab:hover:!selected {
                color: #CBD5E1;
            }
        """)

        # 插件商店 Tab
        self.store_widget = PluginStoreWidget(self.plugin_manager)
        self.tabs.addTab(self.store_widget, "插件商店")

        # 插件管理 Tab
        self.manager_widget = PluginManagerWidget(self.plugin_manager)
        self.tabs.addTab(self.manager_widget, "已安装")

        layout.addWidget(self.tabs)

    def refresh(self):
        """刷新所有内容"""
        self.manager_widget.refresh()
