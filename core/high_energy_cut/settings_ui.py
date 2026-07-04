"""
高能剪切 —— 专属设置页 (overlay 卡片风格,视觉对齐全局/房间设置)。

设置项:
- 分析参数:滑动窗口 / 重复阈值 / 最短片段 / 最低密度 / 合并间隔 / 屏蔽词
- 剪切输出:命名模板 / CRF / 失败回退开关 / 输出目录

交互对齐房间设置 overlay:逐字段即时保存(关掉即生效),每项配两阶段重置按钮。
持久化走 core.config 的 set_global_setting('high_energy_cut_settings', dict)。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QFrame, QScrollArea,
    QSpinBox, QDoubleSpinBox, QPlainTextEdit, QLineEdit,
    QPushButton, QFileDialog
)
from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QFont

from core.config import get_global_setting, set_global_setting
from ui.room_card import ToggleSwitch
from .analyzer import AnalysisConfig
from .constants import PLUGIN_ID

logger = logging.getLogger(__name__)


class NoWheelSpinBox(QSpinBox):
    """滚轮不改变数值:忽略事件并向上冒泡,交给滚动区域滚动页面。"""

    def wheelEvent(self, event):
        event.ignore()


class NoWheelDoubleSpinBox(QDoubleSpinBox):
    """滚轮不改变数值:忽略事件并向上冒泡,交给滚动区域滚动页面。"""

    def wheelEvent(self, event):
        event.ignore()


@dataclass
class PluginSettings:
    # 分析
    window_seconds: int = 10
    repeat_threshold: int = 5
    min_clip_duration: int = 5
    min_density_per_sec: float = 0.5
    merge_gap: int = 3
    draw_words: List[str] = field(default_factory=lambda: [
        "抽奖", "中奖", "中奖啦", "礼物", "感谢", "投喂", "上船"
    ])
    # 剪切
    name_template: str = "{stem}__片段{idx}_{start}_{end}.{ext}"
    crf: int = 23
    fallback_to_reencode: bool = True
    output_dir: str = ""  # 空 = 默认(录播所在目录/高能剪切)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "PluginSettings":
        defaults = cls()
        if not d:
            return defaults
        return cls(
            window_seconds=int(d.get("window_seconds", defaults.window_seconds)),
            repeat_threshold=int(d.get("repeat_threshold", defaults.repeat_threshold)),
            min_clip_duration=int(d.get("min_clip_duration", defaults.min_clip_duration)),
            min_density_per_sec=float(d.get("min_density_per_sec", defaults.min_density_per_sec)),
            merge_gap=int(d.get("merge_gap", defaults.merge_gap)),
            draw_words=list(d.get("draw_words", defaults.draw_words) or []),
            name_template=str(d.get("name_template", defaults.name_template)),
            crf=int(d.get("crf", defaults.crf)),
            fallback_to_reencode=bool(d.get("fallback_to_reencode", defaults.fallback_to_reencode)),
            output_dir=str(d.get("output_dir", defaults.output_dir)),
        )

    def to_analysis_config(self) -> AnalysisConfig:
        return AnalysisConfig(
            window_seconds=self.window_seconds,
            repeat_threshold=self.repeat_threshold,
            min_clip_duration=self.min_clip_duration,
            min_density_per_sec=self.min_density_per_sec,
            merge_gap=self.merge_gap,
            draw_words=self.draw_words,
        )


class HighEnergyCutSettingsPage(QWidget):
    """高能剪切设置页(卡片风格,逐字段即时保存)。"""
    settings_saved = Signal()

    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self._main_window = main_window
        self._defaults = PluginSettings()
        self._settings: PluginSettings = PluginSettings.from_dict(
            get_global_setting("high_energy_cut_settings") or {}
        )
        self._controls = {}  # field -> widget
        # 屏蔽词多行框逐键保存太频繁,用 300ms 防抖
        self._dm_debounce = QTimer(self)
        self._dm_debounce.setSingleShot(True)
        self._dm_debounce.setInterval(300)
        self._dm_debounce.timeout.connect(self._save_draw_words)
        self._build_ui()
        self._load_values()

    # ==================== 卡片 UI helper ====================
    def _setting_card(self, title_text: str, color: str, items) -> QFrame:
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

    def _section_divider(self) -> QFrame:
        line = QFrame()
        line.setObjectName("settingDivider")
        return line

    def _setting_item(self, title_text: str, desc_text: str, control, reset_widget=None) -> QWidget:
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
        if reset_widget:
            layout.addWidget(reset_widget, 0, Qt.AlignRight | Qt.AlignVCenter)
        return wrapper

    def _reset_button(self, field: str, default_value):
        """两阶段确认重置按钮:重置该字段到默认值并即时保存。"""
        btn = QPushButton("重置")
        btn.setFixedSize(60, 28)
        btn.setStyleSheet(RESET_BTN_QSS)
        confirm = QPushButton("确认重置")
        confirm.setFixedSize(64, 28)
        confirm.setStyleSheet(CONFIRM_BTN_QSS)
        confirm.hide()

        def _on_reset():
            btn.hide()
            confirm.show()

        def _do_reset():
            self._reset_field(field, default_value)
            confirm.hide()
            btn.show()
            btn.setFocus()

        btn.clicked.connect(_on_reset)
        confirm.clicked.connect(_do_reset)

        wrapper = QWidget()
        wl = QHBoxLayout(wrapper)
        wl.setContentsMargins(0, 0, 0, 0)
        wl.setSpacing(4)
        wl.addWidget(btn)
        wl.addWidget(confirm)
        return wrapper

    # ==================== 行构建器 ====================
    def _row_spin_int(self, field, title, desc, lo, hi, suffix="") -> QWidget:
        spn = NoWheelSpinBox()
        spn.setRange(lo, hi)
        if suffix:
            spn.setSuffix(f" {suffix}")
        spn.setFixedWidth(150)
        spn.setAlignment(Qt.AlignCenter)
        self._controls[field] = spn
        spn.editingFinished.connect(lambda f=field, w=spn: self._save_field(f, w.value()))
        return self._setting_item(title, desc, spn, self._reset_button(field, getattr(self._defaults, field)))

    def _row_spin_double(self, field, title, desc, lo, hi, step, decimals, suffix="") -> QWidget:
        spn = NoWheelDoubleSpinBox()
        spn.setRange(lo, hi)
        spn.setSingleStep(step)
        spn.setDecimals(decimals)
        if suffix:
            spn.setSuffix(f" {suffix}")
        spn.setFixedWidth(150)
        spn.setAlignment(Qt.AlignCenter)
        self._controls[field] = spn
        spn.editingFinished.connect(lambda f=field, w=spn: self._save_field(f, w.value()))
        return self._setting_item(title, desc, spn, self._reset_button(field, getattr(self._defaults, field)))

    def _row_check(self, field, title, desc) -> QWidget:
        sw = ToggleSwitch()
        self._controls[field] = sw
        sw.toggled.connect(lambda v, f=field: self._save_field(f, v))
        return self._setting_item(title, desc, sw, self._reset_button(field, getattr(self._defaults, field)))

    def _row_line(self, field, title, desc, placeholder="", width=320) -> QWidget:
        le = QLineEdit()
        if placeholder:
            le.setPlaceholderText(placeholder)
        le.setFixedWidth(width)
        self._controls[field] = le
        le.editingFinished.connect(lambda f=field, w=le: self._save_field(f, w.text().strip()))
        return self._setting_item(title, desc, le, self._reset_button(field, getattr(self._defaults, field)))

    def _row_output_dir(self, field, title, desc, placeholder="") -> QWidget:
        ctrl = QWidget()
        cl = QHBoxLayout(ctrl)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        le = QLineEdit()
        if placeholder:
            le.setPlaceholderText(placeholder)
        le.setFixedWidth(300)
        self._controls[field] = le
        le.editingFinished.connect(lambda f=field, w=le: self._save_field(f, w.text().strip()))
        browse = QPushButton("选择…")
        browse.setFixedWidth(60)
        browse.setCursor(Qt.PointingHandCursor)
        browse.setStyleSheet(BROWSE_BTN_QSS)
        browse.clicked.connect(lambda: self._pick_output_dir(le))
        cl.addWidget(le)
        cl.addWidget(browse)
        return self._setting_item(title, desc, ctrl, self._reset_button(field, getattr(self._defaults, field)))

    def _row_text(self, field, title, desc, placeholder="") -> QWidget:
        """多行文本(屏蔽词):标题+描述+重置在上,文本框整行在下。"""
        wrap = QWidget()
        lay = QVBoxLayout(wrap)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        head = QHBoxLayout()
        head.setSpacing(12)
        txt = QVBoxLayout()
        txt.setSpacing(4)
        t = QLabel(title)
        t.setProperty("role", "itemTitle")
        d = QLabel(desc)
        d.setProperty("role", "itemDesc")
        d.setWordWrap(True)
        txt.addWidget(t)
        txt.addWidget(d)
        head.addLayout(txt, 1)
        head.addWidget(self._reset_button(field, list(getattr(self._defaults, field))),
                       0, Qt.AlignTop | Qt.AlignRight)
        lay.addLayout(head)
        te = QPlainTextEdit()
        if placeholder:
            te.setPlaceholderText(placeholder)
        te.setFixedHeight(110)
        te.setStyleSheet(TE_QSS)
        self._controls[field] = te
        te.textChanged.connect(lambda: self._dm_debounce.start())
        lay.addWidget(te)
        return wrap

    # ==================== 主 UI ====================
    def _build_ui(self) -> None:
        self.setStyleSheet(PAGE_QSS)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setStyleSheet(
            "QScrollArea { background: transparent; border: none; }"
            "QScrollBar:vertical { background: #15161D; width: 10px; border-radius: 5px; }"
            "QScrollBar::handle:vertical { background: #2D2E3A; border-radius: 5px; min-height: 30px; }"
            "QScrollBar::handle:vertical:hover { background: #3B82F6; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }"
        )
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        il = QVBoxLayout(inner)
        il.setContentsMargins(28, 22, 28, 28)
        il.setSpacing(16)

        analysis_items = [
            self._row_spin_int("window_seconds", "滑动窗口大小", "统计重复弹幕的时间窗口长度", 3, 60, "秒"),
            self._row_spin_int("repeat_threshold", "重复弹幕阈值", "窗口内相同弹幕达到此条数才算命中", 2, 100, "条"),
            self._row_spin_int("min_clip_duration", "最短片段时长", "短于此长度的片段会被剔除", 1, 300, "秒"),
            self._row_spin_double("min_density_per_sec", "最低密度阈值", "低于此密度(条/秒)的窗口忽略", 0.05, 10.0, 0.05, 2, "条/秒"),
            self._row_spin_int("merge_gap", "相邻片段合并间隔", "相邻命中在此间隔内合并为一段", 0, 30, "秒"),
            self._row_text("draw_words", "屏蔽词列表", "含这些词的弹幕不参与分析(抽奖 / 礼物 / 投喂 等),一行一个"),
        ]
        il.addWidget(self._setting_card("分析参数", "#3B82F6", analysis_items))

        cut_items = [
            self._row_line("name_template", "输出文件命名模板",
                           "可用占位符:{stem} 原名 / {idx} 序号 / {start} {end} 起止 / {ext} 扩展名",
                           "{stem}__片段{idx}_{start}_{end}.{ext}"),
            self._row_spin_int("crf", "转码质量 (CRF)", "18-22 高质量 / 23-26 中等 / 27+ 体积小画质差", 16, 32),
            self._row_check("fallback_to_reencode", "失败回退重编码",
                            "优先用 -c copy 秒切;失败时自动用 libx264 重编码(关闭则失败即放弃)"),
            self._row_output_dir("output_dir", "输出目录", "留空 = 录播所在目录下的「高能剪切」子文件夹", ""),
        ]
        il.addWidget(self._setting_card("剪切输出", "#22C55E", cut_items))

        il.addStretch()
        scroll.setWidget(inner)
        root.addWidget(scroll)

    # ==================== 数据层 ====================
    def _load_values(self) -> None:
        for field, widget in self._controls.items():
            self._set_widget_value(widget, getattr(self._settings, field))

    def _set_widget_value(self, widget, value) -> None:
        widget.blockSignals(True)
        try:
            if isinstance(widget, QPlainTextEdit):
                if isinstance(value, list):
                    widget.setPlainText("\n".join(value))
                else:
                    widget.setPlainText(str(value) if value else "")
            elif isinstance(widget, QSpinBox):
                widget.setValue(int(value))
            elif isinstance(widget, QDoubleSpinBox):
                widget.setValue(float(value))
            elif isinstance(widget, QLineEdit):
                widget.setText(str(value) if value is not None else "")
            elif isinstance(widget, ToggleSwitch):
                widget.setChecked(bool(value))
        finally:
            widget.blockSignals(False)

    def _save_field(self, field, value) -> None:
        """更新某字段并即时写配置 + 通知外部刷新。"""
        setattr(self._settings, field, value)
        try:
            set_global_setting("high_energy_cut_settings", self._settings.to_dict())
            self.settings_saved.emit()
        except Exception:
            logger.exception("保存高能剪切设置失败")

    def _save_draw_words(self) -> None:
        te = self._controls.get("draw_words")
        if te is None:
            return
        words = [w.strip() for w in te.toPlainText().splitlines() if w.strip()]
        self._save_field("draw_words", words)

    def _reset_field(self, field, default_value) -> None:
        widget = self._controls.get(field)
        if widget is not None:
            self._set_widget_value(widget, default_value)
        # draw_words 默认值是 list,_save_field 直接存即可;其他类型同样
        if field == "draw_words":
            self._save_field(field, list(default_value))
        else:
            self._save_field(field, default_value)

    def _pick_output_dir(self, le: QLineEdit) -> None:
        cur = le.text().strip()
        path = QFileDialog.getExistingDirectory(self, "选择高能剪切输出目录", cur or "")
        if path:
            le.setText(path)
            self._save_field("output_dir", path)

    def current_settings(self) -> PluginSettings:
        return self._settings


# ==================== 样式 ====================
PAGE_QSS = """
QWidget { background-color: transparent; color: #E2E8F0; }
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
QLabel[role="title"] { color: #F8FAFC; font-size: 15px; font-weight: 700; }
QLabel[role="itemTitle"] { color: #E2E8F0; font-size: 13px; font-weight: 600; }
QLabel[role="itemDesc"] { color: #94A3B8; font-size: 12px; }
QLineEdit, QSpinBox, QDoubleSpinBox {
    background-color: #15161D;
    border: 1px solid #2D2E3A;
    border-radius: 10px;
    padding: 8px 10px;
    color: #E2E8F0;
    font-size: 13px;
}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border: 1px solid #3B82F6;
}
QSpinBox::up-button, QDoubleSpinBox::up-button {
    subcontrol-origin: border; subcontrol-position: top right;
    width: 18px; border-left: 1px solid #2D2E3A; border-bottom: 1px solid #2D2E3A;
    border-top-right-radius: 10px; background: #1A1B21;
}
QSpinBox::down-button, QDoubleSpinBox::down-button {
    subcontrol-origin: border; subcontrol-position: bottom right;
    width: 18px; border-left: 1px solid #2D2E3A; border-top: 1px solid #2D2E3A;
    border-bottom-right-radius: 10px; background: #1A1B21;
}
QSpinBox::up-button:hover, QDoubleSpinBox::up-button:hover,
QSpinBox::down-button:hover, QDoubleSpinBox::down-button:hover { background: #2D2E3A; }
QSpinBox::up-arrow { width: 0; height: 0; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-bottom: 5px solid #94A3B8; }
QSpinBox::down-arrow { width: 0; height: 0; border-left: 4px solid transparent;
    border-right: 4px solid transparent; border-top: 5px solid #94A3B8; }
"""

TE_QSS = """
QPlainTextEdit {
    background-color: #15161D;
    border: 1px solid #2D2E3A;
    border-radius: 10px;
    color: #E2E8F0;
    padding: 8px;
    font-family: Consolas;
    font-size: 12px;
}
QPlainTextEdit:focus { border: 1px solid #3B82F6; }
"""

RESET_BTN_QSS = """
QPushButton {
    background: transparent;
    border: 1px solid #3D3E4A;
    border-radius: 6px;
    color: #94A3B8;
    font-size: 11px;
    padding: 0 8px;
}
QPushButton:hover {
    background: #2D2E3A;
    color: #F87171;
    border-color: #F87171;
}
"""

CONFIRM_BTN_QSS = """
QPushButton {
    background: #EF4444;
    border: none;
    border-radius: 6px;
    color: white;
    font-size: 11px;
    padding: 0 8px;
}
QPushButton:hover { background: #DC2626; }
"""

BROWSE_BTN_QSS = """
QPushButton {
    background: #252631;
    border: 1px solid #2D2E3A;
    border-radius: 10px;
    color: #E2E8F0;
    font-size: 12px;
    padding: 8px 6px;
}
QPushButton:hover { background: #2D2E3A; border: 1px solid #3B82F6; }
"""
