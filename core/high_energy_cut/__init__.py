"""
高能剪切 —— 内置功能模块。

提供:
- HighEnergyCutPage: 主页面 widget(筛选 + 分析 + 预览 + 剪切)
- HighEnergyCutSettingsPage: 设置页 widget

原为插件,后移入 core/ 作为内置功能。
"""
from __future__ import annotations
import logging

from .constants import PLUGIN_ID
from .ui import HighEnergyCutPage
from .settings_ui import HighEnergyCutSettingsPage

logger = logging.getLogger(__name__)

__all__ = ["PLUGIN_ID", "HighEnergyCutPage", "HighEnergyCutSettingsPage"]
