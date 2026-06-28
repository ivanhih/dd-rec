"""
高能剪切插件 —— 入口。

- get_widget():  主页面 widget(筛选 + 分析 + 预览 + 剪切)
- get_settings_widget(): 插件专属设置页
- on_enable / on_disable: 宿主回调,做清理

注: plugin id 故意用下划线 `high_energy_cut` 而不是 `high-energy-cut`,
    因为 `plugins/PluginManager._create_instance` 用 `importlib.import_module(id)`
    直接当 Python 模块名 import,带连字符会失败。目录名和 id 保持一致。
"""
from __future__ import annotations
import logging
from typing import Optional

from PySide6.QtWidgets import QWidget

from plugins import PluginInterface

from .constants import PLUGIN_ID
from .ui import HighEnergyCutPage
from .settings_ui import HighEnergyCutSettingsPage

logger = logging.getLogger(__name__)


class Plugin(PluginInterface):
    """高能剪切插件主类 —— 宿主通过 import_module + getattr 找到这个类。"""

    def __init__(self, app_context):
        super().__init__(app_context)
        self._widget: Optional[HighEnergyCutPage] = None
        self._settings_widget: Optional[HighEnergyCutSettingsPage] = None

    # -------- 宿主要求实现的接口 --------
    def get_widget(self) -> QWidget:
        if self._widget is None:
            self._widget = HighEnergyCutPage(self)
        return self._widget

    def get_settings_widget(self) -> Optional[QWidget]:
        if self._settings_widget is None:
            self._settings_widget = HighEnergyCutSettingsPage(self)
        return self._settings_widget

    # -------- 生命周期 --------
    def on_enable(self) -> None:
        logger.info("high-energy-cut 插件已启用")

    def on_disable(self) -> None:
        # 释放 widget,避免下次启用时残留旧状态
        if self._widget is not None:
            try:
                self._widget.deleteLater()
            except Exception:
                pass
            self._widget = None
        if self._settings_widget is not None:
            try:
                self._settings_widget.deleteLater()
            except Exception:
                pass
            self._settings_widget = None
        logger.info("high-energy-cut 插件已禁用")

    def on_install(self) -> None:
        logger.info("high-energy-cut 插件已安装(下次启动生效)")

    def on_uninstall(self) -> None:
        logger.info("high-energy-cut 插件已卸载")
