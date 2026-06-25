"""
插件商店 - 从 GitHub 获取插件列表并提供安装界面
"""
import json
import logging
import tempfile
import urllib.request
import urllib.error
from typing import List, Optional, Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QScrollArea, QFrame, QProgressBar, QMessageBox,
    QSizePolicy
)
from PySide6.QtCore import Qt, QThread, Signal, QTimer
from PySide6.QtGui import QFont

logger = logging.getLogger(__name__)

# 插件市场 URL
GITHUB_REPO = "ivanhih/ddrec-plugins"
MARKET_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/plugins.json"


class PluginDownloader(QThread):
    """后台下载线程"""
    progress = Signal(int)
    finished = Signal(str, bool)  # file_path, success
    error = Signal(str)

    def __init__(self, url: str, filename: str):
        super().__init__()
        self.url = url
        self.filename = filename

    def run(self):
        try:
            temp_dir = Path(tempfile.gettempdir()) / "bilirec_plugins"
            temp_dir.mkdir(parents=True, exist_ok=True)
            output_path = temp_dir / self.filename

            req = urllib.request.Request(
                self.url,
                headers={"User-Agent": "bilirec-plugin-store/1.0"}
            )
            with urllib.request.urlopen(req, timeout=60) as response:
                total_size = int(response.headers.get("Content-Length", 0)) or 0
                downloaded = 0
                chunk_size = 8192

                with open(output_path, "wb") as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        if total_size > 0:
                            progress = int(downloaded * 100 / total_size)
                            self.progress.emit(progress)

            self.finished.emit(str(output_path), True)
        except Exception as e:
            self.error.emit(str(e))
            self.finished.emit("", False)


class PluginMarketItem(QFrame):
    """插件市场项"""

    def __init__(
        self,
        plugin_info: dict,
        on_install: Callable[["PluginMarketItem"], None],
        on_uninstall: Callable[["PluginMarketItem"], None],
        is_installed: bool = False,
        parent=None
    ):
        super().__init__(parent)
        self.plugin_info = plugin_info
        self.on_install = on_install
        self.on_uninstall = on_uninstall
        self.is_installed = is_installed

        self._setup_ui()
        self._update_install_button()

    def _setup_ui(self):
        self.setFrameStyle(QFrame.StyledPanel)
        self.setMinimumHeight(100)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.setStyleSheet("""
            QFrame {
                background-color: #252631;
                border: 1px solid #3D3D4A;
                border-radius: 8px;
                padding: 12px;
            }
            QFrame:hover {
                border: 1px solid #3B82F6;
            }
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # 标题行
        header_layout = QHBoxLayout()

        name_label = QLabel(self.plugin_info.get("name", "未知插件"))
        name_label.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        name_label.setStyleSheet("color: #F8FAFC; background: transparent; border: none;")
        header_layout.addWidget(name_label)

        version_label = QLabel(f"v{self.plugin_info.get('version', '1.0.0')}")
        version_label.setFont(QFont("Consolas", 10))
        version_label.setStyleSheet("color: #94A3B8; background: transparent; border: none;")
        header_layout.addWidget(version_label)

        header_layout.addStretch()

        self.install_btn = QPushButton()
        self.install_btn.setFixedWidth(80)
        self.install_btn.setCursor(Qt.PointingHandCursor)
        self.install_btn.clicked.connect(self._on_install_clicked)
        header_layout.addWidget(self.install_btn)

        layout.addLayout(header_layout)

        # 描述
        desc_label = QLabel(self.plugin_info.get("description", ""))
        desc_label.setWordWrap(True)
        desc_label.setStyleSheet("color: #CBD5E1; background: transparent; border: none;")
        layout.addWidget(desc_label)

        # 作者
        author_layout = QHBoxLayout()
        author_label = QLabel(f"作者: {self.plugin_info.get('author', '未知')}")
        author_label.setStyleSheet("color: #64748B; font-size: 11px; background: transparent; border: none;")
        author_layout.addWidget(author_label)
        author_layout.addStretch()

        if self.plugin_info.get("homepage"):
            homepage_label = QLabel(f'<a href="{self.plugin_info["homepage"]}" style="color: #60A5FA;">主页</a>')
            homepage_label.setOpenExternalLinks(True)
            homepage_label.setStyleSheet("background: transparent; border: none;")
            author_layout.addWidget(homepage_label)

        layout.addLayout(author_layout)

    def _update_install_button(self):
        if self.is_installed:
            self.install_btn.setText("已安装")
            self.install_btn.setStyleSheet("""
                QPushButton {
                    background-color: #22C55E;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 16px;
                    font-size: 12px;
                }
            """)
            self.install_btn.setEnabled(False)
        else:
            self.install_btn.setText("安装")
            self.install_btn.setStyleSheet("""
                QPushButton {
                    background-color: #3B82F6;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    padding: 6px 16px;
                    font-size: 12px;
                }
                QPushButton:hover {
                    background-color: #2563EB;
                }
            """)
            self.install_btn.setEnabled(True)

    def _on_install_clicked(self):
        self.install_btn.setEnabled(False)
        self.install_btn.setText("安装中...")
        self.on_install(self)


class PluginStoreWidget(QWidget):
    """插件商店界面"""

    def __init__(self, plugin_manager, parent=None):
        super().__init__(parent)
        self.plugin_manager = plugin_manager
        self.plugins_data: List[dict] = []
        self.downloader: Optional[PluginDownloader] = None

        self._setup_ui()
        self._load_plugins()

    def _setup_ui(self):
        self.setStyleSheet("background-color: #0F0F13;")

        layout = QVBoxLayout(self)
        layout.setSpacing(16)

        # 标题栏
        header_layout = QHBoxLayout()

        title_label = QLabel("插件商店")
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
        self.refresh_btn.clicked.connect(self._load_plugins)
        header_layout.addWidget(self.refresh_btn)

        layout.addLayout(header_layout)

        # 加载状态
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #CBD5E1; background: transparent; border: none; font-size: 13px;")
        layout.addWidget(self.status_label)

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
        self.plugins_layout.addStretch()

        self.scroll.setWidget(self.plugins_container)
        layout.addWidget(self.scroll)

        # 下载进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: none;
                border-radius: 4px;
                background-color: #252631;
                text-align: center;
                color: #F8FAFC;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #3B82F6;
                border-radius: 4px;
            }
        """)
        layout.addWidget(self.progress_bar)

    def _load_plugins(self):
        """从 GitHub 加载插件列表"""
        self.status_label.setText("正在加载插件列表...")
        self.refresh_btn.setEnabled(False)

        # 使用 QTimer 延迟执行，避免阻塞 UI
        QTimer.singleShot(100, self._do_load_plugins)

    def _do_load_plugins(self):
        try:
            req = urllib.request.Request(
                MARKET_URL,
                headers={"User-Agent": "bilirec-plugin-store/1.0"}
            )

            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
                self.plugins_data = data.get("plugins", [])

            self._display_plugins()
            self.status_label.setText(f"共 {len(self.plugins_data)} 个插件")
            self.status_label.setStyleSheet("color: #CBD5E1; background: transparent; border: none; font-size: 13px;")
        except urllib.error.URLError as e:
            self.status_label.setText(f"网络错误: {e}")
            self.status_label.setStyleSheet("color: #F87171; background: transparent; border: none; font-size: 13px;")
            logger.error(f"加载插件列表失败: {e}")
        except json.JSONDecodeError as e:
            self.status_label.setText(f"数据解析错误: {e}")
            self.status_label.setStyleSheet("color: #F87171; background: transparent; border: none; font-size: 13px;")
            logger.error(f"JSON 解析失败: {e}")
        except Exception as e:
            self.status_label.setText(f"加载失败: {e}")
            logger.error(f"加载插件列表失败: {e}")
        finally:
            self.refresh_btn.setEnabled(True)

    def _display_plugins(self):
        """显示插件列表"""
        # 清除现有内容
        while self.plugins_layout.count():
            item = self.plugins_layout.takeAt(0)
            if item.widget() and item.widget() != self.plugins_layout.itemAt(self.plugins_layout.count() - 1):
                item.widget().deleteLater()

        # 获取已安装的插件 ID
        installed_ids = {p.info.id for p in self.plugin_manager.get_all_plugins()}

        # 添加插件项
        for plugin_info in self.plugins_data:
            is_installed = plugin_info.get("id") in installed_ids
            item = PluginMarketItem(
                plugin_info,
                on_install=self._on_install,
                on_uninstall=self._on_uninstall,
                is_installed=is_installed
            )
            self.plugins_layout.insertWidget(self.plugins_layout.count() - 1, item)

    def _on_install(self, item: PluginMarketItem):
        """处理安装按钮点击"""
        plugin_info = item.plugin_info
        download_url = plugin_info.get("download_url")

        if not download_url:
            QMessageBox.warning(self, "安装失败", "插件下载链接无效")
            item.install_btn.setEnabled(True)
            item.install_btn.setText("安装")
            return

        self.status_label.setText(f"正在下载 {plugin_info['name']}...")
        self.progress_bar.setVisible(True)
        self.progress_bar.setValue(0)

        # 提取文件名
        filename = download_url.split("/")[-1]

        # 下载插件（Release 文件需要 token）
        self.downloader = PluginDownloader(download_url, filename)
        self.downloader.progress.connect(self._on_download_progress)
        self.downloader.finished.connect(lambda path, ok: self._on_download_finished(item, path, ok))
        self.downloader.error.connect(lambda err: self._on_download_error(item, err))
        self.downloader.start()

    def _on_download_progress(self, progress: int):
        """下载进度更新"""
        self.progress_bar.setValue(progress)

    def _on_download_finished(self, item: PluginMarketItem, zip_path: str, success: bool):
        """下载完成"""
        self.progress_bar.setVisible(False)

        if not success or not zip_path:
            QMessageBox.warning(self, "下载失败", "插件下载失败，请稍后重试")
            item.install_btn.setEnabled(True)
            item.install_btn.setText("安装")
            self.status_label.setText("下载失败")
            return

        # 安装插件
        plugin_id = item.plugin_info.get("id")
        if self.plugin_manager.install_plugin(zip_path, plugin_id):
            item.is_installed = True
            item._update_install_button()
            self.status_label.setText(f"{item.plugin_info['name']} 安装成功，重启后生效")
            self.status_label.setStyleSheet("color: #4ADE80; background: transparent; border: none; font-size: 13px;")

            QMessageBox.information(
                self, "安装成功",
                f"{item.plugin_info['name']} 已安装成功！\n\n"
                "请重启软件以启用插件。"
            )
        else:
            QMessageBox.warning(self, "安装失败", "插件安装失败，请稍后重试")
            item.install_btn.setEnabled(True)
            item.install_btn.setText("安装")
            self.status_label.setText("安装失败")
            self.status_label.setStyleSheet("color: #F87171; background: transparent; border: none; font-size: 13px;")

    def _on_download_error(self, item: PluginMarketItem, error: str):
        """下载错误"""
        self.progress_bar.setVisible(False)
        QMessageBox.warning(self, "下载错误", f"下载失败: {error}")
        item.install_btn.setEnabled(True)
        item.install_btn.setText("安装")
        self.status_label.setText("下载出错")
        self.status_label.setStyleSheet("color: #F87171; background: transparent; border: none; font-size: 13px;")

    def _on_uninstall(self, item: PluginMarketItem):
        """处理卸载按钮点击"""
        reply = QMessageBox.question(
            self, "确认卸载",
            f"确定要卸载插件 {item.plugin_info.get('name')} 吗？",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            plugin_id = item.plugin_info.get("id")
            if self.plugin_manager.uninstall_plugin(plugin_id):
                item.is_installed = False
                item._update_install_button()
                self.status_label.setText(f"{item.plugin_info['name']} 已卸载")
