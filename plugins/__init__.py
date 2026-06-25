"""
插件系统核心模块
提供插件加载、卸载、启用、禁用等功能
"""
import os
import sys
import json
import shutil
import zipfile
import importlib
import logging
from pathlib import Path
from typing import Optional, Dict, List, Any
from dataclasses import dataclass
from abc import ABC, abstractmethod
from enum import Enum

from PySide6.QtWidgets import QWidget

logger = logging.getLogger(__name__)


class PluginState(Enum):
    DISABLED = "disabled"
    ENABLED = "enabled"
    INSTALLED = "installed"


@dataclass
class PluginInfo:
    id: str
    name: str
    version: str
    description: str = ""
    author: str = ""
    homepage: str = ""
    min_app_version: str = ""
    download_url: str = ""

    @classmethod
    def from_json(cls, data: dict) -> "PluginInfo":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            version=data.get("version", ""),
            description=data.get("description", ""),
            author=data.get("author", ""),
            homepage=data.get("homepage", ""),
            min_app_version=data.get("min_app_version", ""),
            download_url=data.get("download_url", ""),
        )


@dataclass
class Plugin:
    info: PluginInfo
    state: PluginState
    instance: Optional["PluginInterface"] = None
    module = None
    error: str = ""


class PluginInterface(ABC):
    def __init__(self, app_context: "PluginContext"):
        self.app = app_context

    @abstractmethod
    def get_widget(self) -> QWidget:
        pass

    def get_settings_widget(self) -> Optional[QWidget]:
        return None

    def on_install(self) -> None:
        pass

    def on_uninstall(self) -> None:
        pass

    def on_enable(self) -> None:
        pass

    def on_disable(self) -> None:
        pass


class PluginContext:
    def __init__(self, main_window: QWidget):
        self.main_window = main_window
        self.app_data: Dict[str, Any] = {}
        self.config: Dict[str, Any] = {}
        self._listeners: Dict[str, List[callable]] = {}

    def get_main_window(self) -> QWidget:
        return self.main_window

    def show_notification(self, message: str, title: str = "提示", level: str = "info"):
        if hasattr(self.main_window, "show_notification"):
            self.main_window.show_notification(message, title, level)

    def register_event(self, event_name: str, callback: callable) -> None:
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append(callback)

    def emit_event(self, event_name: str, *args, **kwargs) -> None:
        for callback in self._listeners.get(event_name, []):
            try:
                callback(*args, **kwargs)
            except Exception as e:
                logger.error(f"事件 {event_name} 处理失败: {e}")

    def get_plugin_config(self, plugin_id: str) -> Dict[str, Any]:
        return self.config.get(plugin_id, {})

    def save_plugin_config(self, plugin_id: str, config: Dict[str, Any]) -> None:
        self.config[plugin_id] = config
        self._save_config()

    def _save_config(self) -> None:
        config_dir = Path(__file__).parent / "config"
        config_dir.mkdir(exist_ok=True)
        config_file = config_dir / "plugins_config.json"
        with open(config_file, "w", encoding="utf-8") as f:
            json.dump(self.config, f, ensure_ascii=False, indent=2)

    def load_config(self) -> None:
        config_file = Path(__file__).parent / "config" / "plugins_config.json"
        if config_file.exists():
            with open(config_file, "r", encoding="utf-8") as f:
                self.config = json.load(f)


class PluginManager:
    def __init__(self, plugins_dir: str = None):
        if plugins_dir is None:
            self.plugins_dir = Path(__file__).parent
        else:
            self.plugins_dir = Path(plugins_dir)
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self._plugins: Dict[str, Plugin] = {}
        self._context: Optional[PluginContext] = None
        self._loaded_widgets: Dict[str, QWidget] = {}

    def initialize(self, main_window: QWidget) -> None:
        self._context = PluginContext(main_window)
        self._context.load_config()
        self._scan_plugins()

    @property
    def context(self) -> PluginContext:
        if self._context is None:
            raise RuntimeError("PluginManager 未初始化")
        return self._context

    def _scan_plugins(self) -> None:
        if not self.plugins_dir.exists():
            return
        for item in self.plugins_dir.iterdir():
            if item.is_dir() and not item.name.startswith("_") and item.name not in ("config", "store", "manager", "page"):
                plugin_json = item / "plugin.json"
                if plugin_json.exists():
                    try:
                        with open(plugin_json, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        info = PluginInfo.from_json(data)
                        is_enabled = self._context.config.get(f"{info.id}_enabled", False)
                        plugin = Plugin(
                            info=info,
                            state=PluginState.ENABLED if is_enabled else PluginState.DISABLED,
                        )
                        self._plugins[info.id] = plugin
                        logger.info(f"发现插件: {info.name} v{info.version}")
                    except Exception as e:
                        logger.error(f"加载插件信息失败 {item.name}: {e}")

    def install_plugin(self, zip_path: str, plugin_id: str) -> bool:
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                names = zf.namelist()
                if not any("plugin.json" in n for n in names):
                    raise ValueError("无效的插件包")

                # 检查 zip 结构：文件是否在插件子目录中
                has_prefix = any(n.startswith(plugin_id + "/") or n.startswith(plugin_id + "\\") for n in names)

                extract_dir = self.plugins_dir / plugin_id
                if extract_dir.exists():
                    shutil.rmtree(extract_dir)
                extract_dir.mkdir(parents=True)

                if has_prefix:
                    # 正常结构：文件在 plugin_id/ 子目录下
                    zf.extractall(self.plugins_dir)
                else:
                    # 兼容：文件直接在根目录，需要手动创建子目录
                    for name in names:
                        if name.endswith("/"):
                            continue
                        target = extract_dir / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with zf.open(name) as src, open(target, "wb") as dst:
                            dst.write(src.read())

            logger.info(f"插件安装成功: {plugin_id}")
            return True
        except Exception as e:
            logger.error(f"插件安装失败: {e}")
            return False

    def uninstall_plugin(self, plugin_id: str) -> bool:
        if plugin_id not in self._plugins:
            return False
        plugin = self._plugins[plugin_id]
        if plugin.state == PluginState.ENABLED:
            self.disable_plugin(plugin_id)
        plugin_dir = self.plugins_dir / plugin_id
        if plugin_dir.exists():
            shutil.rmtree(plugin_dir)
        del self._plugins[plugin_id]
        logger.info(f"插件已卸载: {plugin_id}")
        return True

    def enable_plugin(self, plugin_id: str) -> bool:
        if plugin_id not in self._plugins:
            return False
        plugin = self._plugins[plugin_id]
        if plugin.state == PluginState.ENABLED:
            return True
        try:
            plugin.instance = self._create_instance(plugin)
            if plugin.instance is None:
                return False
            plugin.state = PluginState.ENABLED
            self._context.config[f"{plugin_id}_enabled"] = True
            self._context._save_config()
            plugin.instance.on_enable()
            logger.info(f"插件已启用: {plugin.info.name}")
            return True
        except Exception as e:
            logger.error(f"启用插件失败 {plugin_id}: {e}")
            plugin.error = str(e)
            return False

    def disable_plugin(self, plugin_id: str) -> bool:
        if plugin_id not in self._plugins:
            return False
        plugin = self._plugins[plugin_id]
        if plugin.state == PluginState.DISABLED:
            return True
        try:
            if plugin.instance:
                plugin.instance.on_disable()
                plugin.instance = None
            plugin.state = PluginState.DISABLED
            self._context.config[f"{plugin_id}_enabled"] = False
            self._context._save_config()
            if plugin_id in self._loaded_widgets:
                widget = self._loaded_widgets.pop(plugin_id)
                widget.deleteLater()
            logger.info(f"插件已禁用: {plugin.info.name}")
            return True
        except Exception as e:
            logger.error(f"禁用插件失败 {plugin_id}: {e}")
            return False

    def _create_instance(self, plugin: Plugin) -> Optional[PluginInterface]:
        try:
            plugin_dir = self.plugins_dir / plugin.info.id
            if not plugin_dir.exists():
                return None
            if str(plugin_dir.parent) not in sys.path:
                sys.path.insert(0, str(plugin_dir.parent))
            module = importlib.import_module(plugin.info.id)
            plugin_class = getattr(module, "Plugin", None)
            if plugin_class is None:
                for item in dir(module):
                    obj = getattr(module, item)
                    if isinstance(obj, type) and issubclass(obj, PluginInterface) and obj is not PluginInterface:
                        plugin_class = obj
                        break
            if plugin_class is None:
                logger.error(f"插件 {plugin.info.id} 未找到 Plugin 类")
                return None
            return plugin_class(self._context)
        except Exception as e:
            logger.error(f"创建插件实例失败 {plugin.info.id}: {e}")
            return None

    def get_plugin_widget(self, plugin_id: str) -> Optional[QWidget]:
        if plugin_id not in self._plugins:
            return None
        plugin = self._plugins[plugin_id]
        if plugin.state != PluginState.ENABLED:
            return None
        if plugin_id in self._loaded_widgets:
            return self._loaded_widgets[plugin_id]
        if plugin.instance:
            try:
                widget = plugin.instance.get_widget()
                self._loaded_widgets[plugin_id] = widget
                return widget
            except Exception as e:
                logger.error(f"获取插件 widget 失败 {plugin_id}: {e}")
                return None
        return None

    def get_all_plugins(self) -> List[Plugin]:
        return list(self._plugins.values())

    def get_enabled_plugins(self) -> List[Plugin]:
        return [p for p in self._plugins.values() if p.state == PluginState.ENABLED]
