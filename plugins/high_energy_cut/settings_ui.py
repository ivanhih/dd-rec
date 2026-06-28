"""
高能剪切插件 —— 专属设置页。

Plugin.get_settings_widget() 返回这里实现的 widget。设置项:
- 滑动窗口秒数
- 重复弹幕阈值
- 最短片段时长
- 屏蔽词列表(一行一个)
- 输出命名模板
- 转码 CRF
- 『-c copy 失败时回退到 libx264』开关

持久化走 PluginContext.save_plugin_config('high_energy_cut', dict),
不污染主程序 config.py。
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel,
    QSpinBox, QDoubleSpinBox, QPlainTextEdit, QLineEdit,
    QPushButton, QCheckBox, QGroupBox, QMessageBox
)
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont

from .analyzer import AnalysisConfig
from .constants import PLUGIN_ID

logger = logging.getLogger(__name__)


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
    """设置页 widget。"""
    settings_saved = Signal()

    def __init__(self, plugin, parent=None):
        super().__init__(parent)
        self._plugin = plugin
        self._settings: PluginSettings = PluginSettings.from_dict(
            plugin.app.get_plugin_config(PLUGIN_ID)
        )
        self._build_ui()
        self._populate()

    def _build_ui(self) -> None:
        self.setStyleSheet("background-color: #0F0F13; color: #E2E8F0;")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(16)

        title = QLabel("高能剪切 - 插件设置")
        title.setFont(QFont("Microsoft YaHei UI", 16, QFont.Bold))
        title.setStyleSheet("color: #F8FAFC;")
        root.addWidget(title)

        # ----- 分析参数 -----
        gb_analysis = QGroupBox("分析参数")
        gb_analysis.setStyleSheet(GROUPBOX_QSS)
        form = QFormLayout(gb_analysis)
        form.setSpacing(8)
        form.setContentsMargins(16, 16, 16, 16)

        self.spn_window = QSpinBox()
        self.spn_window.setRange(3, 60)
        self.spn_window.setSuffix(" 秒")
        form.addRow("滑动窗口大小:", self.spn_window)

        self.spn_threshold = QSpinBox()
        self.spn_threshold.setRange(2, 100)
        form.addRow("重复弹幕阈值:", self.spn_threshold)

        self.spn_min_dur = QSpinBox()
        self.spn_min_dur.setRange(1, 300)
        self.spn_min_dur.setSuffix(" 秒")
        form.addRow("最短片段时长:", self.spn_min_dur)

        self.spn_min_density = QDoubleSpinBox()
        self.spn_min_density.setRange(0.05, 10.0)
        self.spn_min_density.setSingleStep(0.05)
        self.spn_min_density.setDecimals(2)
        self.spn_min_density.setSuffix(" 条/秒")
        form.addRow("最低密度阈值:", self.spn_min_density)

        self.spn_merge_gap = QSpinBox()
        self.spn_merge_gap.setRange(0, 30)
        self.spn_merge_gap.setSuffix(" 秒")
        form.addRow("相邻片段合并间隔:", self.spn_merge_gap)

        self.txt_draw_words = QPlainTextEdit()
        self.txt_draw_words.setPlaceholderText("一行一个屏蔽词,例如:抽奖 / 中奖 / 礼物 / 投喂")
        self.txt_draw_words.setFixedHeight(110)
        self.txt_draw_words.setStyleSheet(TEXT_QSS)
        form.addRow("屏蔽词列表:", self.txt_draw_words)

        root.addWidget(gb_analysis)

        # ----- 剪切输出 -----
        gb_cut = QGroupBox("剪切输出")
        gb_cut.setStyleSheet(GROUPBOX_QSS)
        form2 = QFormLayout(gb_cut)
        form2.setSpacing(8)
        form2.setContentsMargins(16, 16, 16, 16)

        self.txt_template = QLineEdit()
        self.txt_template.setPlaceholderText("{stem}__片段{idx}_{start}_{end}.{ext}")
        self.txt_template.setStyleSheet(LINEEDIT_QSS)
        form2.addRow("输出文件命名模板:", self.txt_template)

        self.spn_crf = QSpinBox()
        self.spn_crf.setRange(16, 32)
        self.spn_crf.setToolTip("18-22 高质量,23-26 中等,27+ 体积小但画质差")
        form2.addRow("转码质量(CRF):", self.spn_crf)

        self.chk_fallback = QCheckBox("优先 -c copy;失败时自动回退到 libx264 重编码")
        self.chk_fallback.setChecked(True)
        form2.addRow(self.chk_fallback)

        root.addWidget(gb_cut)

        root.addStretch()

        # ----- 按钮 -----
        btn_bar = QHBoxLayout()
        btn_bar.addStretch()
        self.btn_reset = QPushButton("恢复默认")
        self.btn_reset.setStyleSheet(BTN_GHOST_QSS)
        self.btn_reset.setFixedWidth(100)
        self.btn_reset.setCursor(Qt.PointingHandCursor)
        self.btn_reset.clicked.connect(self._on_reset)
        btn_bar.addWidget(self.btn_reset)

        self.btn_save = QPushButton("保存")
        self.btn_save.setStyleSheet(BTN_PRIMARY_QSS)
        self.btn_save.setFixedWidth(100)
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.clicked.connect(self._on_save)
        btn_bar.addWidget(self.btn_save)

        root.addLayout(btn_bar)

    def _populate(self) -> None:
        s = self._settings
        self.spn_window.setValue(s.window_seconds)
        self.spn_threshold.setValue(s.repeat_threshold)
        self.spn_min_dur.setValue(s.min_clip_duration)
        self.spn_min_density.setValue(s.min_density_per_sec)
        self.spn_merge_gap.setValue(s.merge_gap)
        self.txt_draw_words.setPlainText("\n".join(s.draw_words))
        self.txt_template.setText(s.name_template)
        self.spn_crf.setValue(s.crf)
        self.chk_fallback.setChecked(s.fallback_to_reencode)

    def _collect(self) -> PluginSettings:
        words = [
            w.strip() for w in self.txt_draw_words.toPlainText().splitlines() if w.strip()
        ]
        return PluginSettings(
            window_seconds=self.spn_window.value(),
            repeat_threshold=self.spn_threshold.value(),
            min_clip_duration=self.spn_min_dur.value(),
            min_density_per_sec=self.spn_min_density.value(),
            merge_gap=self.spn_merge_gap.value(),
            draw_words=words,
            name_template=self.txt_template.text().strip() or PluginSettings.name_template,
            crf=self.spn_crf.value(),
            fallback_to_reencode=self.chk_fallback.isChecked(),
        )

    def _on_save(self) -> None:
        try:
            new = self._collect()
            self._plugin.app.save_plugin_config(PLUGIN_ID, new.to_dict())
            self._settings = new
            self.settings_saved.emit()
            QMessageBox.information(self, "已保存", "高能剪切插件设置已保存。")
        except Exception as e:
            logger.exception("保存设置失败")
            QMessageBox.critical(self, "保存失败", f"保存失败: {e}")

    def _on_reset(self) -> None:
        self._settings = PluginSettings()
        self._populate()

    def current_settings(self) -> PluginSettings:
        return self._collect()


# ---------------- 样式 ----------------
GROUPBOX_QSS = """
QGroupBox {
    color: #F8FAFC;
    border: 1px solid #2D2E3A;
    border-radius: 8px;
    margin-top: 12px;
    font-size: 13px;
    font-weight: bold;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 6px;
}
"""

TEXT_QSS = """
QPlainTextEdit {
    background-color: #181920;
    border: 1px solid #2D2E3A;
    border-radius: 6px;
    color: #E2E8F0;
    padding: 6px;
    font-family: Consolas;
    font-size: 12px;
}
QPlainTextEdit:focus { border: 1px solid #3B82F6; }
"""

LINEEDIT_QSS = """
QLineEdit {
    background-color: #181920;
    border: 1px solid #2D2E3A;
    border-radius: 6px;
    color: #E2E8F0;
    padding: 6px 8px;
    font-size: 12px;
}
QLineEdit:focus { border: 1px solid #3B82F6; }
"""

BTN_PRIMARY_QSS = """
QPushButton {
    background-color: #3B82F6;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton:hover { background-color: #2563EB; }
QPushButton:pressed { background-color: #1D4ED8; }
"""

BTN_GHOST_QSS = """
QPushButton {
    background-color: transparent;
    color: #94A3B8;
    border: 1px solid #2D2E3A;
    border-radius: 6px;
    padding: 8px 16px;
    font-size: 13px;
}
QPushButton:hover { background-color: #252631; color: #E2E8F0; }
"""
