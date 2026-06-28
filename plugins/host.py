"""
插件宿主挂载层 —— 把已启用的插件 widget 挂到主窗口的 sidebar + QStackedWidget。

设计目标:
- 不修改 PluginManager / PluginInterface 既有 API
- 启用插件自动在 sidebar 出现一个图标按钮
- 点击切换 page_stack 到插件 widget(懒加载)
- 与现有『📋 频道 / 🧩 插件商店 / ⚙️ 全局设置 / ℹ️ 关于』4 个按钮并存,样式一致
"""
from __future__ import annotations
import logging
from typing import Dict, Optional

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QToolButton
from PySide6.QtGui import QFont
from PySide6.QtCore import Qt

# 复用主程序里 sidebar 按钮的样式表
SIDEBAR_ACTIVE = """
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

SIDEBAR_INACTIVE = """
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

logger = logging.getLogger(__name__)

# 默认 emoji 图标(v1: 全部插件统一用 🎬,后续 plugin.json 加 icon 字段再做差异化)
DEFAULT_PLUGIN_ICON = "🎬"


class PluginHostBar(QWidget):
    """Sidebar 上显示已启用插件图标的容器。

    外部把 PluginHostBar 放进 sidebar_layout,在『🧩 插件』按钮之后。
    PluginStackController 负责调 refresh_from_manager 来同步按钮列表。
    """
    plugin_selected = Signal(str)  # plugin_id

    def __init__(self, plugin_manager, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._plugin_manager = plugin_manager
        # plugin_id -> QToolButton
        self._buttons: Dict[str, QToolButton] = {}

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(16)
        # 顶部用一根细线视觉上与原生导航区隔开
        self.setStyleSheet("background-color: transparent;")
        # 容器高度自适应(只包按钮,不留空白)
        self.setSizePolicy(self.sizePolicy().horizontalPolicy(), self.sizePolicy().verticalPolicy())

    def set_plugin_manager(self, plugin_manager) -> None:
        """支持延后回填 —— 构造时 manager 可能还没建好。"""
        self._plugin_manager = plugin_manager

    def refresh(self, enabled_plugin_infos) -> None:
        """按传入的已启用插件列表重建按钮。

        enabled_plugin_infos: list[PluginInfo] —— 已经是 ENABLED 状态的
        """
        # 1) 删已不存在的按钮
        current_ids = {p.id for p in enabled_plugin_infos}
        for pid in list(self._buttons.keys()):
            if pid not in current_ids:
                btn = self._buttons.pop(pid)
                self._layout.removeWidget(btn)
                btn.deleteLater()

        # 2) 新增按钮
        for info in enabled_plugin_infos:
            if info.id in self._buttons:
                continue
            btn = self._create_button(info)
            self._buttons[info.id] = btn
            self._layout.addWidget(btn, 0, Qt.AlignHCenter)

    def _create_button(self, info) -> QToolButton:
        # 用 QToolButton 走与原生 sidebar 按钮一致的样式 hook。
        # tooltip 在暗色主题下看不到字,这里只设 accessibleDescription(供 a11y)。
        btn = QToolButton(self)
        icon = getattr(info, "icon", None) or DEFAULT_PLUGIN_ICON
        btn.setText(icon)
        btn.setFixedSize(56, 56)
        btn.setCursor(Qt.PointingHandCursor)
        btn.setAccessibleDescription(info.name)
        btn.setToolTip(info.name)  # 保险起见也设,某些主题能看到
        btn.setStyleSheet(SIDEBAR_INACTIVE)
        btn.clicked.connect(lambda _checked=False, pid=info.id: self.plugin_selected.emit(pid))
        return btn

    def set_active(self, active_plugin_id: Optional[str]) -> None:
        """高亮当前选中的插件按钮;传 None 全 inactive。"""
        for pid, btn in self._buttons.items():
            btn.setStyleSheet(SIDEBAR_ACTIVE if pid == active_plugin_id else SIDEBAR_INACTIVE)


class PluginStackController(QObject):
    """把 plugin_id 映射到 QStackedWidget 的 page index,懒加载 widget。"""

    def __init__(self, plugin_manager, page_stack: "QStackedWidget", host_bar: PluginHostBar, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._plugin_manager = plugin_manager
        self._page_stack = page_stack
        self._host_bar = host_bar
        # plugin_id -> page_stack index
        self._index: Dict[str, int] = {}
        # 缓存 page_stack 当前位置(避免和外部 current_page 同步时被覆盖)
        self._active_plugin_id: Optional[str] = None

        self._host_bar.plugin_selected.connect(self.show_plugin)
        # 监听 page_stack 切换:如果不是切到插件页(比如用户点了『频道』),
        # 自动清掉 host_bar 上的高亮。
        self._page_stack.currentChanged.connect(self._on_page_stack_changed)

    def _on_page_stack_changed(self, index: int) -> None:
        # 当前 index 对应哪个 plugin_id?
        active = None
        for pid, idx in self._index.items():
            if idx == index:
                active = pid
                break
        if active != self._active_plugin_id:
            self._active_plugin_id = active
            self._host_bar.set_active(active)

    def refresh_from_manager(self) -> None:
        """从 plugin_manager 同步按钮 + page_stack。

        启动时调一次,以及『启用/禁用/卸载』后也可以再调(目前 v1 只在启动时调)。

        注意:状态持久化在 `plugins_config.json` 里,instance 是内存对象,
        跨进程不保留。所以『state==ENABLED 但 instance==None』是正常情况,
        这里按需补一次 enable。
        """
        from plugins import PluginState  # 避免循环
        enabled = [p.info for p in self._plugin_manager.get_all_plugins()
                   if p.state == PluginState.ENABLED]

        valid = []
        for info in enabled:
            try:
                widget = self._plugin_manager.get_plugin_widget(info.id)
                if widget is None:
                    # state==ENABLED 但 instance 还没建(冷启动场景) —— 再 enable 一次
                    self._plugin_manager.enable_plugin(info.id)
                    widget = self._plugin_manager.get_plugin_widget(info.id)
                if widget is None:
                    logger.warning(f"插件 {info.id} 加载失败,sidebar 不挂载")
                    continue
                valid.append(info)
            except Exception as e:
                logger.error(f"挂载插件 {info.id} 时异常: {e}")

        self._host_bar.refresh(valid)

        # 把每个插件 widget 加进 page_stack(若还没加)
        for info in valid:
            if info.id in self._index:
                continue
            widget = self._plugin_manager.get_plugin_widget(info.id)
            if widget is None:
                continue
            self._index[info.id] = self._page_stack.addWidget(widget)

    def show_plugin(self, plugin_id: str) -> None:
        """切换到指定插件页面。"""
        if plugin_id not in self._index:
            # 还没挂载,触发一次同步
            self.refresh_from_manager()
        if plugin_id not in self._index:
            logger.warning(f"插件 {plugin_id} 不存在或加载失败")
            return
        self._active_plugin_id = plugin_id
        self._host_bar.set_active(plugin_id)
        self._page_stack.setCurrentIndex(self._index[plugin_id])

    def clear_active(self) -> None:
        """切到非插件页(频道/设置/关于)时清掉高亮。"""
        self._active_plugin_id = None
        self._host_bar.set_active(None)

    @property
    def active_plugin_id(self) -> Optional[str]:
        return self._active_plugin_id

    def has_plugin(self, plugin_id: str) -> bool:
        return plugin_id in self._index
