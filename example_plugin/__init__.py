"""
示例插件 - 展示插件系统的使用方法
"""
import logging
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QPushButton, QTextEdit
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont

from plugins import PluginInterface

logger = logging.getLogger(__name__)


class Plugin(PluginInterface):
    """示例插件"""

    def __init__(self, app_context):
        super().__init__(app_context)
        self._widget = None

    def get_widget(self) -> QWidget:
        """返回插件主界面"""
        if self._widget is None:
            self._widget = self._create_widget()
        return self._widget

    def _create_widget(self) -> QWidget:
        """创建插件界面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setSpacing(16)

        # 标题
        title = QLabel("示例插件")
        title.setFont(QFont("Microsoft YaHei UI", 18, QFont.Bold))
        title.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")
        layout.addWidget(title)

        # 描述
        desc = QLabel("这是一个示例插件，展示了如何开发 bilirec 插件。\n\n"
                      "插件功能：\n"
                      "• 显示欢迎信息\n"
                      "• 访问主应用上下文\n"
                      "• 响应按钮点击")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #CBD5E1; background: transparent; border: none;")
        layout.addWidget(desc)

        # 测试按钮
        self.test_btn = QPushButton("测试通知")
        self.test_btn.setFixedWidth(120)
        self.test_btn.setCursor(Qt.PointingHandCursor)
        self.test_btn.setStyleSheet("""
            QPushButton {
                background-color: #3B82F6;
                color: white;
                border: none;
                border-radius: 8px;
                padding: 10px 24px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #2563EB;
            }
        """)
        self.test_btn.clicked.connect(self._on_test_clicked)
        layout.addWidget(self.test_btn)

        # 日志显示
        self.log_area = QTextEdit()
        self.log_area.setReadOnly(True)
        self.log_area.setMaximumHeight(150)
        self.log_area.setStyleSheet("""
            QTextEdit {
                background-color: #252631;
                border: 1px solid #3D3D4A;
                border-radius: 8px;
                color: #CBD5E1;
                padding: 8px;
                font-family: Consolas;
                font-size: 12px;
            }
        """)
        self.log_area.append(">>> 插件已加载")
        layout.addWidget(self.log_area)

        layout.addStretch()

        return widget

    def _on_test_clicked(self):
        """测试按钮点击"""
        self.log_area.append(">>> 按钮被点击!")
        self.app.show_notification(
            "这是来自插件的通知！",
            "插件消息",
            "success"
        )

    def on_enable(self) -> None:
        """插件启用时调用"""
        logger.info("示例插件已启用")

    def on_disable(self) -> None:
        """插件禁用时调用"""
        logger.info("示例插件已禁用")

    def on_install(self) -> None:
        """插件安装时调用"""
        logger.info("示例插件已安装")

    def on_uninstall(self) -> None:
        """插件卸载时调用"""
        logger.info("示例插件已卸载")
