"""启动覆盖层 —— 主窗口内部的全屏开屏动画。

启动流程中只存在一个主窗口，本控件作为其内部覆盖层显示：
淡入 → 进度条随启动阶段推进 → 主窗口就绪后淡出，露出已准备好的主画面。
风格跟随当前应用主题，动画使用 Qt 原生
QPropertyAnimation / QSequentialAnimationGroup。

设计要点：
- logo 和暗色背景从第一帧完整显示，不做位移动画。
- 进入阶段只给文字容器挂 QGraphicsOpacityEffect；退出前先移除它，
  再给整个覆盖层挂淡出 effect。父子 effect 永不同时存在，避免 Qt 离屏绘制冲突。
"""
import os
import sys

from PySide6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QSequentialAnimationGroup
from PySide6.QtGui import QPixmap, QPainter, QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QLabel, QVBoxLayout, QHBoxLayout, QGraphicsOpacityEffect,
)


def _resource_path(relative_path: str) -> str:
    base_dir = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    # ui/ 在 onedir 里和 assets/ 平级；回退到上一级再拼 assets。
    candidates = [
        os.path.join(base_dir, relative_path),
        os.path.join(os.path.dirname(base_dir), relative_path),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


def _load_logo_pixmap(size: int) -> QPixmap:
    """优先 assets/icon.png，失败回退到与 _load_app_icon 一致的手绘 DD 圆形。"""
    for name in ("icon.png", "icon.svg"):
        path = _resource_path(os.path.join("assets", name))
        pix = QPixmap(path)
        if not pix.isNull():
            return pix.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    from ui.theme import theme_manager
    tokens = theme_manager().tokens
    pix = QPixmap(size, size)
    pix.fill(Qt.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setBrush(QColor(tokens["primary"]))
    painter.setPen(Qt.NoPen)
    painter.drawEllipse(4, 4, size - 8, size - 8)
    painter.setPen(QColor(tokens["text_on_accent"]))
    painter.setFont(QFont("Microsoft YaHei UI", int(size * 0.4), QFont.Bold))
    painter.drawText(pix.rect(), Qt.AlignCenter, "DD")
    painter.end()
    return pix


class StartupOverlay(QWidget):
    """主窗口内部的启动覆盖层。

    用法：
        overlay = StartupOverlay(parent=main_window, version="1.0.x")
        overlay.show()
        ... 期间调用 overlay.set_progress(v) / overlay.set_status_text(...) ...
        overlay.fade_out_and_close(on_finished)  # 主内容已就绪后调用
    """

    BAR_H = 8

    # 动画时长（可在此集中调整）
    # 保留原进入节奏：背景/logo 立即显示，这段时间作为文字出现前的视觉停留。
    FADE_IN_DURATION_MS = 500
    TEXT_FADE_DURATION_MS = 600    # 文字容器淡入
    TEXT_FADE_DELAY_MS = 250       # 文字容器延迟开始淡入
    PROGRESS_ANIM_DURATION_MS = 400

    # 覆盖层最小停留时间：即使用户没有频道、初始化瞬间完成，
    # 也要让用户看清 logo、标题、版本和状态文字。
    HOLD_BEFORE_REVEAL_MS = 600

    # 揭示主界面时的淡出时长
    FADE_OUT_DURATION_MS = 700

    def __init__(self, version: str = "", parent=None):
        super().__init__(parent)
        self._version = version
        self._closing = False
        self._progress = 0
        self._intro_finished = False

        self._build_ui()
        self._setup_animation()

    # ---------- UI ----------
    def _build_ui(self):
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setObjectName("startupOverlay")

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 48, 40, 48)
        root.setSpacing(0)

        root.addStretch(1)

        # logo：从一开始就完整显示，不挂 opacity effect，不做位移动画
        self._logo = QLabel()
        self._logo.setFixedSize(96, 96)
        self._logo.setAlignment(Qt.AlignCenter)
        self._logo.setPixmap(_load_logo_pixmap(96))
        self._logo.setObjectName("startupLogo")
        self._logo.setStyleSheet("QLabel#startupLogo { border: none; background: transparent; }")
        root.addWidget(self._logo, 0, Qt.AlignHCenter)

        root.addSpacing(22)

        # 文字容器：标题 + 版本 + 状态，统一淡入
        self._text_container = QWidget()
        self._text_container.setObjectName("startupText")
        self._text_container.setStyleSheet(
            "QWidget#startupText { background: transparent; border: none; }"
        )
        text_layout = QVBoxLayout(self._text_container)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(0)

        from ui.i18n import t
        self._title = QLabel(t("app.title"))
        self._title.setAlignment(Qt.AlignCenter)
        self._title.setFont(QFont("Microsoft YaHei UI", 22, QFont.Bold))
        self._title.setProperty("role", "title")
        text_layout.addWidget(self._title)

        text_layout.addSpacing(6)

        self._ver = QLabel(f"v{self._version}" if self._version else "")
        self._ver.setAlignment(Qt.AlignCenter)
        self._ver.setFont(QFont("Microsoft YaHei UI", 11))
        self._ver.setProperty("role", "muted")
        text_layout.addWidget(self._ver)

        text_layout.addSpacing(28)

        self._status = QLabel(t("startup.starting"))
        self._status.setAlignment(Qt.AlignCenter)
        self._status.setFont(QFont("Microsoft YaHei UI", 11))
        self._status.setProperty("role", "secondary")
        text_layout.addWidget(self._status)

        root.addWidget(self._text_container, 0, Qt.AlignHCenter)

        root.addSpacing(12)

        # 进度条
        self._bar_track = QWidget()
        self._bar_track.setObjectName("startupProgressTrack")
        self._bar_track.setFixedHeight(self.BAR_H)
        self._bar_track.setMinimumWidth(240)
        self._bar_track.setMaximumWidth(400)

        self._bar_fill = QWidget(self._bar_track)
        self._bar_fill.setObjectName("startupProgressFill")
        self._bar_fill.setGeometry(0, 0, 0, self.BAR_H)

        bar_row = QHBoxLayout()
        bar_row.setContentsMargins(0, 0, 0, 0)
        bar_row.addStretch()
        bar_row.addWidget(self._bar_track, 1)
        bar_row.addStretch()
        root.addLayout(bar_row)

        root.addStretch(1)

    def _setup_animation(self):
        # 进入阶段只使用文字容器 effect。不要同时给父覆盖层挂 effect，
        # 否则 Qt 会在父子离屏缓存之间递归绘制并刷出 QPainter 警告。
        self._opacity_effect = None
        self._fade_out = None
        self._on_faded_out = None

        self._text_opacity_effect = QGraphicsOpacityEffect(self._text_container)
        self._text_opacity_effect.setOpacity(0.0)
        self._text_container.setGraphicsEffect(self._text_opacity_effect)

        self._text_fade = QPropertyAnimation(self._text_opacity_effect, b"opacity", self)
        self._text_fade.setDuration(self.TEXT_FADE_DURATION_MS)
        self._text_fade.setStartValue(0.0)
        self._text_fade.setEndValue(1.0)
        self._text_fade.setEasingCurve(QEasingCurve.OutCubic)

        # 背景/logo 第一帧即稳定显示；保持原总时长后再淡入文字。
        self._intro_group = QSequentialAnimationGroup(self)
        self._intro_group.addPause(self.FADE_IN_DURATION_MS + self.TEXT_FADE_DELAY_MS)
        self._intro_group.addAnimation(self._text_fade)
        self._intro_group.finished.connect(self._on_intro_finished)

        # 进度条宽度动画
        self._bar_anim = QPropertyAnimation(self._bar_fill, b"geometry", self)
        self._bar_anim.setDuration(self.PROGRESS_ANIM_DURATION_MS)
        self._bar_anim.setEasingCurve(QEasingCurve.OutCubic)

    @property
    def intro_duration_ms(self) -> int:
        """进入动画总时长（毫秒），用于主窗口决定最早何时可以 reveal。"""
        return (
            self.FADE_IN_DURATION_MS
            + self.TEXT_FADE_DELAY_MS
            + self.TEXT_FADE_DURATION_MS
        )

    def is_intro_finished(self) -> bool:
        """进入动画是否已经完成。"""
        return self._intro_finished

    def _on_intro_finished(self):
        self._intro_finished = True
        # 文字已经完全可见，及时移除 effect，避免空闲期持续离屏绘制。
        self._detach_text_effect()

    # ---------- 公开 API ----------
    def showEvent(self, event):
        super().showEvent(event)
        if self._closing:
            return
        self._intro_group.stop()
        self._reset_animation_states()
        self._intro_group.start()

    def set_progress(self, value: int):
        """value: 0..100，进度条平滑推进到目标宽度。"""
        v = max(0, min(100, int(value)))
        self._progress = v
        if self._bar_track.width() <= 0:
            QTimer.singleShot(0, lambda: self.set_progress(v))
            return
        target_w = int(self._bar_track.width() * v / 100)
        end_geom = self._bar_fill.geometry()
        end_geom.setWidth(target_w)
        self._bar_anim.stop()
        self._bar_anim.setStartValue(self._bar_fill.geometry())
        self._bar_anim.setEndValue(end_geom)
        self._bar_anim.start()

    def set_status_text(self, text: str):
        self._status.setText(text)

    def fade_out_and_close(self, on_finished=None):
        if self._closing:
            return
        self._on_faded_out = on_finished
        self._closing = True
        self._intro_group.stop()
        self._text_fade.stop()
        self._bar_anim.stop()

        # 先彻底移除子控件 effect，再创建父覆盖层 effect；二者绝不重叠。
        self._detach_text_effect()
        self._start_overlay_fade_out()

    # ---------- 内部 ----------
    def _reset_animation_states(self):
        self._intro_finished = False
        if self._text_opacity_effect is not None:
            self._text_opacity_effect.setOpacity(0.0)

    def _detach_text_effect(self):
        effect = self._text_opacity_effect
        if effect is None:
            return
        self._text_fade.stop()
        self._text_fade.setTargetObject(None)
        effect.setOpacity(1.0)
        self._text_container.setGraphicsEffect(None)
        self._text_opacity_effect = None

    def _start_overlay_fade_out(self):
        self._opacity_effect = QGraphicsOpacityEffect(self)
        self._opacity_effect.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity_effect)

        self._fade_out = QPropertyAnimation(self._opacity_effect, b"opacity", self)
        self._fade_out.setDuration(self.FADE_OUT_DURATION_MS)
        self._fade_out.setStartValue(1.0)
        self._fade_out.setEndValue(0.0)
        self._fade_out.setEasingCurve(QEasingCurve.InCubic)
        self._fade_out.finished.connect(self._on_fade_out_finished)
        self._fade_out.start()

    def _on_fade_out_finished(self):
        self.hide()
        self.deleteLater()
        if self._on_faded_out is not None:
            cb = self._on_faded_out
            self._on_faded_out = None
            cb()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # 保证进度条填充宽度与 track 一致
        self._bar_fill.setFixedHeight(self.BAR_H)
        target_w = int(self._bar_track.width() * self._progress / 100)
        geom = self._bar_fill.geometry()
        geom.setWidth(target_w)
        self._bar_fill.setGeometry(geom)
