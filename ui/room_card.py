# ui/room_card.py
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QToolButton, QSizePolicy,
                              QGraphicsDropShadowEffect, QWidget, QApplication)
from PySide6.QtCore import (Qt, Signal, QTimer, QPoint, QRect, QSize, QPropertyAnimation, QEasingCurve, QObject, QEvent, QRunnable, QThreadPool, Slot)
from PySide6.QtGui import QPixmap, QFont, QColor, QCursor, QPainter, QBrush, QPainterPath
from PySide6.QtWidgets import QGraphicsOpacityEffect
import os
import subprocess
import platform
import threading
import weakref
import requests

from core.config import get_global_setting, get_effective_save_dir, VIDEO_SAVE_DIR, get_room_config


_AVATAR_CACHE = {}
_AVATAR_CACHE_LOCK = threading.Lock()
_AVATAR_POOL = QThreadPool()
_AVATAR_POOL.setMaxThreadCount(4)
_AVATAR_POOL.setExpiryTimeout(30_000)
_ENTRY_ANIMATING_CARDS = weakref.WeakSet()


class _AvatarLoadSignals(QObject):
    loaded = Signal(str, bytes)


class _AvatarLoadTask(QRunnable):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.signals = _AvatarLoadSignals()

    @Slot()
    def run(self):
        with _AVATAR_CACHE_LOCK:
            cached = _AVATAR_CACHE.get(self.url)
        if cached is not None:
            self.signals.loaded.emit(self.url, cached)
            return

        try:
            from curl_cffi import requests as curl_requests
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            }
            response = curl_requests.get(
                self.url, headers=headers, impersonate="chrome110", timeout=15
            )
            response.raise_for_status()
            data = bytes(response.content)
            if not data:
                return
            with _AVATAR_CACHE_LOCK:
                if len(_AVATAR_CACHE) >= 128:
                    _AVATAR_CACHE.pop(next(iter(_AVATAR_CACHE)))
                _AVATAR_CACHE[self.url] = data
            self.signals.loaded.emit(self.url, data)
        except Exception:
            # 头像是非关键资源，失败时保留默认占位图即可。
            return


class _HoverToolButton(QToolButton):
    """自带 hover tip 的 QToolButton — 用 override enterEvent/leaveEvent

    为什么不用 eventFilter: QToolButton 自己处理 enterEvent(autoRaise 等),
    eventFilter 顺序上在 QToolButton.enterEvent 之前/之后不可控,经常丢事件。
    直接 subclass override 是最稳的。
    """
    def __init__(self, text, tip_text, parent=None):
        super().__init__(parent)
        self._hover_tip = None  # 延迟创建(需要 anchor.window() 存在)
        self._hover_text = tip_text
        self.setText(text)
        # 强制 mouse tracking,确保 enter/leave 事件能稳定分发
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)

    def attach_tip(self, tip):
        self._hover_tip = tip

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._hover_tip is not None:
            self._hover_tip.show()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._hover_tip is not None:
            self._hover_tip.hide()


class _HoverPushButton(QPushButton):
    """带 hover tip 的 QPushButton — 给"添加"按钮用。"""
    def __init__(self, text, tip_text, parent=None):
        super().__init__(text, parent)
        self._hover_tip = None
        self._hover_text = tip_text
        self.setMouseTracking(True)
        self.setAttribute(Qt.WA_Hover, True)

    def attach_tip(self, tip):
        self._hover_tip = tip

    def enterEvent(self, event):
        super().enterEvent(event)
        if self._hover_tip is not None:
            self._hover_tip.show()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        if self._hover_tip is not None:
            self._hover_tip.hide()


class HoverLabel(QLabel):
    """自定义 hover 浮动文字 — 鼠标进入目标 widget 时显示,离开时消失。

    关键修复:
      1. WA_TransparentForMouseEvents 会让 widget 在某些情况下被 QSS 颜色覆盖,
         所以 paintEvent 完全自绘背景+文字+边框,绕开 stylesheet。
      2. WA_ShowWithoutActivating 防止 tip 抢焦点(导致按钮 enter 事件丢失)。
      3. parent 必须是顶层 window(anchor.window())而不是 anchor 自己,
         否则会被父 widget 的 z-order 遮挡。
      4. setWindowFlags(Qt.Tool | Qt.FramelessWindowHint) 让它永远是顶层,
         不被 QFrame 卡片遮挡。
    """
    def __init__(self, anchor: QWidget, text: str):
        # parent = 顶层 window,确保 tip 在最上层不被卡片遮挡
        super().__init__(anchor.window())
        self._anchor = anchor
        # Qt.Tool 让 tip 是独立顶层窗口,不被父 widget 的 paint 覆盖
        # FramelessWindowHint 不要标题栏
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint)
        # 不要激活窗口(防止按钮失焦后 enter 事件链断掉)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        # 不抢鼠标事件
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        from PySide6.QtGui import QFont
        f = QFont("Microsoft YaHei UI", 10, QFont.Medium)
        f.setStyleHint(QFont.SansSerif)
        self.setFont(f)
        # 边距用 setContentsMargins(让 _resize_to_text 算对尺寸)
        self.setContentsMargins(10, 4, 10, 4)
        self.setAlignment(Qt.AlignCenter)
        self.setText(text)
        self._resize_to_text()
        self.hide()

        from ui.theme import theme_manager
        theme_manager().changed.connect(self.update)

    def setText(self, text):
        super().setText(text)
        self._resize_to_text()

    def _resize_to_text(self):
        from PySide6.QtCore import QSize
        from PySide6.QtGui import QFontMetrics
        fm = QFontMetrics(self.font())
        text_w = fm.horizontalAdvance(self.text())
        text_h = fm.height()
        m = self.contentsMargins()
        self.resize(QSize(text_w + m.left() + m.right() + 2,
                          text_h + m.top() + m.bottom() + 2))

    def paintEvent(self, event):
        """完全自绘 — 背景 + 边框 + 文字,绕开 stylesheet 色彩坑。"""
        from PySide6.QtGui import QPainter, QColor, QPen, QPainterPath
        from ui.theme import theme_manager

        tokens = theme_manager().tokens
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)
        p.fillPath(path, QColor(tokens["tooltip_background"]))
        pen = QPen(QColor(tokens["tooltip_border"]))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawPath(path)
        p.setPen(QColor(tokens["tooltip_text"]))
        p.drawText(rect, Qt.AlignCenter, self.text())
        p.end()

    def showEvent(self, event):
        super().showEvent(event)
        # 把标签放到 anchor 下方居中,必要时上移防溢出
        rect = self._anchor.rect()
        gp = self._anchor.mapToGlobal(rect.bottomLeft())
        x = gp.x() + (rect.width() - self.width()) // 2
        y = gp.y() + 8
        screen = QApplication.primaryScreen().availableGeometry()
        if y + self.height() > screen.bottom():
            y = self._anchor.mapToGlobal(rect.topLeft()).y() - self.height() - 8
        self.move(x, y)
        self.raise_()


class _HoverFilter(QObject):
    """把鼠标 enter/leave 事件桥接到 HoverLabel 的 show/hide。"""
    def __init__(self, tip: HoverLabel):
        super().__init__()
        self._tip = tip
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Enter:
            self._tip.show()
        elif event.type() == QEvent.Leave:
            self._tip.hide()
        return False


class ToggleSwitch(QFrame):
    """自定义开关按钮，带动画效果"""
    toggled = Signal(bool)
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(64, 32)
        self.setCursor(QCursor(Qt.PointingHandCursor))
        
        self._checked = False
        self._switch_x = 4  # 当前开关位置
        self._target_x = 4  # 目标位置
        self._animation_timer = QTimer()
        self._animation_timer.timeout.connect(self._update_animation)
        self._animation_step = 0

        from ui.theme import theme_manager
        theme_manager().changed.connect(self.update)
    
    def isChecked(self):
        return self._checked
    
    def setChecked(self, checked, *, animate=True):
        if self._checked != checked:
            self._checked = checked
            self._target_x = 32 if checked else 4
            self._animation_step = 0
            if animate:
                self._animation_timer.start(10)  # 10ms 更新一次
            else:
                self._animation_timer.stop()
                self._switch_x = self._target_x
                self.update()
            self.toggled.emit(checked)
    
    def _update_animation(self):
        diff = self._target_x - self._switch_x
        if abs(diff) < 1:
            self._switch_x = self._target_x
            self._animation_timer.stop()
        else:
            step = diff * 0.3
            self._switch_x += step
        self.update()
    
    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)
    
    def paintEvent(self, event):
        from ui.theme import theme_manager

        tokens = theme_manager().tokens
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        track_color = tokens["toggle_on"] if self._checked else tokens["toggle_off"]
        painter.setBrush(QBrush(QColor(track_color)))

        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, 64, 32, 16, 16)

        painter.setBrush(QBrush(QColor(tokens["toggle_thumb"])))
        painter.drawRoundedRect(int(self._switch_x), 4, 24, 24, 12, 12)


class AnimatedLabel(QLabel):
    """带淡入淡出动画的标签（使用 QGraphicsOpacityEffect，更快更可靠）

    关键设计：淡出后**不** setVisible(False)，只把透明度降到 0。
    这样元素永远在布局里占位，下面的按钮行不会因为它消失而上跳。
    """
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setStyleSheet("border: none; background: transparent;")

        # 使用 QGraphicsOpacityEffect 来控制透明度！更快更可靠！
        self._effect = QGraphicsOpacityEffect()
        self._effect.setOpacity(0.0)  # 初始透明但占位
        self.setGraphicsEffect(self._effect)

        # 创建动画对象
        self._anim = QPropertyAnimation(self._effect, b"opacity")
        self._anim.setDuration(200)  # 200ms 快速动画
        self._anim.setEasingCurve(QEasingCurve.InOutQuad)

    def fade_in(self):
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(1.0)
        self._anim.start()

    def fade_out(self):
        self._anim.stop()
        self._anim.setStartValue(self._effect.opacity())
        self._anim.setEndValue(0.0)
        self._anim.start()

    def ensure_visible(self):
        """确保标签可见，直接设置透明度为1.0"""
        self._anim.stop()
        self._effect.setOpacity(1.0)


class RoomCard(QFrame):
    toggle_signal = Signal(str, bool)
    cut_signal = Signal(str)
    delete_signal = Signal(str)
    settings_signal = Signal(str)
    open_folder_signal = Signal(str)
    # 用于跨线程设置头像的信号

    def __init__(self, room_info: dict, parent=None):
        super().__init__(parent)
        self.room_id = str(room_info["room_id"])
        self.room_info = room_info
        self.current_save_path = None
        self._is_recording = False
        self._stats_visible = False  # 录制统计标签是否当前显示中（避免重复 fade）
        self._entry_animation = None
        # 保存监控开关状态
        self._is_monitoring = room_info.get("enabled", True)
        # 稳定状态码：UI 只存 code，展示文案一律 t() 渲染，避免中文子串协议
        self._monitor_code = "active" if self._is_monitoring else "paused"
        self._live_code = "off"
        self._rec_code = "idle"
        
        # 连接头像信号

        # 固定卡片大小，确保所有卡片一致！
        self.setFixedSize(540, 260)
        self.setObjectName("roomCard")
        from ui.theme import enable_surface
        enable_surface(self)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # ==================== 顶部信息 ====================
        top_layout = QHBoxLayout()
        top_layout.setSpacing(16)

        # 头像
        self.avatar = QLabel()
        self.avatar.setObjectName("roomAvatar")
        self.avatar.setFixedSize(64, 64)
        self.avatar.setAlignment(Qt.AlignCenter)
        self.avatar.setText("📺")
        self.load_avatar(room_info.get("face", ""))

        # 名称和标题
        info_layout = QVBoxLayout()
        info_layout.setSpacing(2)

        uname_layout = QHBoxLayout()
        uname_layout.setContentsMargins(0, 6, 0, 0)
        self.lbl_uname = QLabel(room_info.get("uname", f"房间_{self.room_id}"))
        self.lbl_uname.setFont(QFont("Microsoft YaHei UI", 15, QFont.Bold))
        self.lbl_uname.setProperty("role", "title")

        self.lbl_room_id = QLabel(self.room_id)
        self.lbl_room_id.setProperty("role", "link")
        self.lbl_room_id.setStyleSheet("font-size: 13px; font-weight: 500;")
        
        uname_layout.addWidget(self.lbl_uname)
        uname_layout.addStretch()
        uname_layout.addWidget(self.lbl_room_id)

        from ui.i18n import t

        self.lbl_title = QLabel(room_info.get("title") or t("card.no_title"))
        self.lbl_title.setProperty("role", "secondary")
        self.lbl_title.setStyleSheet("font-size: 13px;")
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setFixedHeight(38)   # 固定两行高，防止 1 行/2 行切换时布局跳动
        self.lbl_title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

        info_layout.addLayout(uname_layout)
        info_layout.addWidget(self.lbl_title)

        top_layout.addWidget(self.avatar)
        top_layout.addLayout(info_layout, 1)

        main_layout.addLayout(top_layout)

        # ==================== 标签行 ====================
        tag_layout = QHBoxLayout()
        self.tag_platform = self._create_tag("bilibili", "#3B82F6")
        self._parent_area_raw = room_info.get("parent_area_name", "")
        self._area_raw = room_info.get("area_name", "")
        self.tag_parent = self._create_tag(self._parent_area_raw or t("card.unknown_area"), "#7C3AED")
        self.tag_area = self._create_tag(self._area_raw or t("card.unknown_content"), "#A855F7")

        tag_layout.addWidget(self.tag_platform)
        tag_layout.addWidget(self.tag_parent)
        tag_layout.addWidget(self.tag_area)
        tag_layout.addStretch()
        main_layout.addLayout(tag_layout)

        # ==================== 状态行 ====================
        self.status_container = QWidget()
        self.status_container.setFixedHeight(24)
        status_layout = QHBoxLayout(self.status_container)
        status_layout.setSpacing(24)
        status_layout.setContentsMargins(0, 0, 0, 0)

        self.lbl_m = QLabel()
        self.lbl_l = QLabel()
        self.lbl_r = QLabel()

        for lbl in [self.lbl_m, self.lbl_l, self.lbl_r]:
            lbl.setProperty("statusTone", "neutral")
            lbl.setStyleSheet("font-size: 13px; font-weight: 500;")
            status_layout.addWidget(lbl)
        
        status_layout.addStretch()
        main_layout.addWidget(self.status_container)

        self.status_container.setAttribute(Qt.WA_TranslucentBackground)
        self._status_opacity_effect = QGraphicsOpacityEffect(self.status_container)
        self._status_opacity_effect.setOpacity(1.0)
        self.status_container.setGraphicsEffect(self._status_opacity_effect)
        self._status_fade_animation = QPropertyAnimation(self._status_opacity_effect, b"opacity", self)
        self._status_fade_animation.setDuration(150)
        self._status_fade_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._last_status_signature = self._status_signature()

        # ==================== 录制统计行 (默认隐藏) ====================
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(24)
        
        self.lbl_duration = AnimatedLabel("⏱ 00:00:00")
        self.lbl_duration.setProperty("role", "secondary")
        self.lbl_duration.setStyleSheet("font-size: 13px;")

        self.lbl_speed = AnimatedLabel("⚡ 0 KB/s")
        self.lbl_speed.setProperty("role", "secondary")
        self.lbl_speed.setStyleSheet("font-size: 13px;")

        self.lbl_size = AnimatedLabel("💾 0 B")
        self.lbl_size.setProperty("role", "secondary")
        self.lbl_size.setStyleSheet("font-size: 13px;")

        stats_layout.addWidget(self.lbl_duration)
        stats_layout.addWidget(self.lbl_speed)
        stats_layout.addWidget(self.lbl_size)
        stats_layout.addStretch()
        
        main_layout.addLayout(stats_layout)

        # ==================== 按钮行 ====================
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(8)

        # 开关按钮
        self.switch_btn = ToggleSwitch()
        self.switch_btn.setChecked(room_info.get("enabled", True), animate=False)
        self.switch_btn.toggled.connect(self.on_toggle)

        btn_folder = _HoverToolButton("📁", t("card.tip.folder"))
        btn_folder.setFixedSize(40, 40)
        btn_folder.setProperty("roomAction", "folder")
        btn_folder.setCursor(QCursor(Qt.PointingHandCursor))
        btn_folder.clicked.connect(lambda: self.open_folder_signal.emit(self.room_id))
        self._tip_folder = HoverLabel(btn_folder, t("card.tip.folder"))
        btn_folder.attach_tip(self._tip_folder)

        self.btn_cut = _HoverToolButton("✂️", t("card.tip.cut_disabled"))
        self.btn_cut.setFixedSize(40, 40)
        self.btn_cut.setProperty("roomAction", "cut")
        self.btn_cut.setCursor(QCursor(Qt.PointingHandCursor))
        self.btn_cut.clicked.connect(lambda: self.cut_signal.emit(self.room_id))
        self._tip_cut = HoverLabel(self.btn_cut, t("card.tip.cut_disabled"))
        self.btn_cut.attach_tip(self._tip_cut)

        btn_delete = _HoverToolButton("🗑️", t("card.tip.delete"))
        btn_delete.setFixedSize(40, 40)
        btn_delete.setProperty("roomAction", "delete")
        btn_delete.setCursor(QCursor(Qt.PointingHandCursor))
        btn_delete.clicked.connect(lambda: self.delete_signal.emit(self.room_id))
        self._tip_delete = HoverLabel(btn_delete, t("card.tip.delete"))
        btn_delete.attach_tip(self._tip_delete)

        btn_settings = _HoverToolButton("⚙️", t("card.tip.settings"))
        btn_settings.setFixedSize(40, 40)
        btn_settings.setProperty("roomAction", "settings")
        btn_settings.setCursor(QCursor(Qt.PointingHandCursor))
        btn_settings.clicked.connect(lambda: self.settings_signal.emit(self.room_id))
        self._tip_settings = HoverLabel(btn_settings, t("card.tip.settings"))
        btn_settings.attach_tip(self._tip_settings)

        btn_layout.addWidget(self.switch_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_folder)
        btn_layout.addWidget(self.btn_cut)
        btn_layout.addWidget(btn_delete)
        btn_layout.addWidget(btn_settings)

        main_layout.addLayout(btn_layout)
        self._render_status_labels()
        self._refresh_cut_button_state()

        from ui.i18n import language_manager
        language_manager().changed.connect(self.retranslate_ui)

    def _create_tag(self, text, color):
        label = QLabel(text)
        label.setObjectName("roomTag")
        label.setProperty("accentColor", color)
        return label

    def load_avatar(self, url):
        # 清理 URL，去掉反引号和空格。
        if url:
            url = url.strip().strip('`').strip('"').strip("'")
        self._avatar_url = url or ""
        if not self._avatar_url:
            return

        task = _AvatarLoadTask(self._avatar_url)
        task.signals.loaded.connect(self._set_avatar)
        _AVATAR_POOL.start(task)
    
    @Slot(str, bytes)
    def _set_avatar(self, url, data):
        # 卡片复用或销毁前可能收到迟到结果，只接受当前 URL。
        if url != getattr(self, "_avatar_url", ""):
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(data) or pixmap.isNull():
            return
        self.avatar.clear()  # 清除所有内容（包括占位符）
        
        size = 64
        
        # 先缩放图片，确保填满整个圆形区域
        scaled = pixmap.scaled(
            size, size, 
            Qt.KeepAspectRatioByExpanding, 
            Qt.SmoothTransformation
        )
        
        # 计算居中的位置
        x = (size - scaled.width()) // 2
        y = (size - scaled.height()) // 2
        
        # 创建圆形裁剪的图片
        circular = QPixmap(size, size)
        circular.fill(Qt.transparent)
        
        painter = QPainter(circular)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        
        # 设置圆形裁剪路径
        path = QPainterPath()
        path.addEllipse(0, 0, size, size)
        painter.setClipPath(path)
        
        # 绘制图片
        painter.drawPixmap(x, y, scaled)
        painter.end()
        
        self.avatar.setPixmap(circular)

    def _status_signature(self):
        return (self.lbl_m.text(), self.lbl_l.text(), self.lbl_r.text(), self.lbl_title.text())

    def _animate_status_transition(self, previous_signature, animate=True):
        current_signature = self._status_signature()
        self._last_status_signature = current_signature
        if not animate or current_signature == previous_signature:
            self._status_fade_animation.stop()
            self._status_opacity_effect.setOpacity(1.0)
            return
        self._status_fade_animation.stop()
        self._status_fade_animation.setStartValue(0.55)
        self._status_fade_animation.setEndValue(1.0)
        self._status_fade_animation.start()

    def play_entry_animation(self):
        """对用户新添加的卡片播放一次轻量入场淡入；批量加载由调用方关闭。"""
        if self._entry_animation is not None or len(_ENTRY_ANIMATING_CARDS) >= 6:
            return False
        effect = QGraphicsOpacityEffect(self)
        effect.setOpacity(0.0)
        self.setGraphicsEffect(effect)
        animation = QPropertyAnimation(effect, b"opacity", self)
        animation.setDuration(180)
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        self._entry_animation = animation
        _ENTRY_ANIMATING_CARDS.add(self)

        def _finish():
            _ENTRY_ANIMATING_CARDS.discard(self)
            if self._entry_animation is animation:
                self._entry_animation = None
                self.setGraphicsEffect(None)
                animation.deleteLater()

        animation.finished.connect(_finish)
        animation.start()
        return True

    @staticmethod
    def _set_status(label, text, tone):
        from ui.theme import repolish

        label.setText(text)
        label.setProperty("statusTone", tone)
        repolish(label)

    @staticmethod
    def _normalize_monitor_code(value: str) -> str:
        text = (value or "").strip().lower()
        # 已是 code
        if text in {"active", "paused", "error"}:
            return text
        # 兼容旧中文协议
        if "出错" in (value or "") or "error" in text:
            return "error"
        if "暂停" in (value or "") or "停用" in (value or "") or "paused" in text:
            return "paused"
        return "active"

    @staticmethod
    def _normalize_live_code(value: str) -> str:
        text = (value or "").strip().lower()
        if text in {"on", "off"}:
            return text
        if "直播中" in (value or "") or "live" in text:
            return "on"
        return "off"

    @staticmethod
    def _normalize_rec_code(value: str) -> str:
        text = (value or "").strip().lower()
        if text in {"idle", "recording", "filtered", "waiting", "interrupted", "error", "disabled", "confirming"}:
            return text
        raw = value or ""
        if "条件过滤" in raw or "filtered" in text:
            return "filtered"
        if "录制已停用" in raw or "disabled" in text:
            return "disabled"
        if "录制中断" in raw or "interrupted" in text:
            return "interrupted"
        if "等待防抖" in raw or "debounce" in text or "waiting" in text:
            return "waiting"
        if "确认中" in raw or "confirm" in text:
            return "confirming"
        if "出错" in raw or "error" in text:
            return "error"
        if "录制" in raw or "recording" in text:
            return "recording"
        return "idle"

    def _render_status_labels(self):
        from ui.i18n import t

        monitor_map = {
            "active": ("card.monitor.active", "success"),
            "paused": ("card.monitor.paused", "warning"),
            "error": ("card.monitor.error", "danger"),
        }
        live_map = {
            "on": ("card.live.on", "danger"),
            "off": ("card.live.off", "neutral"),
        }
        rec_map = {
            "idle": ("card.rec.idle", "neutral"),
            "recording": ("card.rec.recording", "info"),
            "filtered": ("card.rec.filtered", "warning"),
            "waiting": ("card.rec.waiting", "warning"),
            "interrupted": ("card.rec.interrupted", "warning"),
            "error": ("card.rec.error", "danger"),
            "disabled": ("card.rec.disabled", "warning"),
            "confirming": ("card.rec.confirming", "warning"),
        }
        m_key, m_tone = monitor_map.get(self._monitor_code, monitor_map["active"])
        l_key, l_tone = live_map.get(self._live_code, live_map["off"])
        r_key, r_tone = rec_map.get(self._rec_code, rec_map["idle"])
        self._set_status(self.lbl_m, t(m_key), m_tone)
        self._set_status(self.lbl_l, t(l_key), l_tone)
        self._set_status(self.lbl_r, t(r_key), r_tone)

    def retranslate_ui(self, *_args):
        """语言切换时按状态码重渲染 tip 与状态文案。"""
        from ui.i18n import t

        if hasattr(self, "_tip_folder"):
            self._tip_folder.setText(t("card.tip.folder"))
        if hasattr(self, "_tip_delete"):
            self._tip_delete.setText(t("card.tip.delete"))
        if hasattr(self, "_tip_settings"):
            self._tip_settings.setText(t("card.tip.settings"))
        self.tag_parent.setText(self._parent_area_raw or t("card.unknown_area"))
        self.tag_area.setText(self._area_raw or t("card.unknown_content"))
        if not (self.room_info.get("title") or "").strip():
            self.lbl_title.setText(t("card.no_title"))
        self._render_status_labels()
        self._refresh_cut_button_state()

    def update_status(self, m: str, l: str, r: str, title: str,
                      duration="00:00:00", speed="0 B/s", size="0 B",
                      parent_area="", area_name="", *, animate=True):
        previous_signature = self._last_status_signature
        self.room_info["title"] = title
        if parent_area:
            self.room_info["parent_area_name"] = parent_area
            self._parent_area_raw = parent_area
            self.tag_parent.setText(parent_area)
        if area_name:
            self.room_info["area_name"] = area_name
            self._area_raw = area_name
            self.tag_area.setText(area_name)

        # 关键守卫: 当卡片监控已关闭时, 无论收到什么信号都显示"已暂停/闲置中"。
        # 防止 _stop_recorder 手动设完状态后, recorder 线程残留在 Qt 事件队列中
        # 的 status_updated 信号又覆盖成"监控中/录制中"。
        if not self._is_monitoring:
            self._monitor_code = "paused"
            self._live_code = "off"
            self._rec_code = "idle"
            self._render_status_labels()
            self.lbl_title.setText(title or self.lbl_title.text())
            # 监控关闭时清零并隐藏录制统计信息
            if self._stats_visible:
                self._stats_visible = False
                self._is_recording = False
                self.lbl_duration.setText("⏱ 00:00:00")
                self.lbl_speed.setText("⚡ 0 B/s")
                self.lbl_size.setText("💾 0 B")
                self.lbl_duration.fade_out()
                self.lbl_speed.fade_out()
                self.lbl_size.fade_out()
            self._refresh_cut_button_state()
            self._animate_status_transition(previous_signature, animate)
            return

        self._monitor_code = self._normalize_monitor_code(m)
        self._live_code = self._normalize_live_code(l)
        self._rec_code = self._normalize_rec_code(r)
        self._render_status_labels()

        self.lbl_title.setText(title)

        is_recording = self._rec_code == "recording"
        is_paused = self._monitor_code == "paused"
        should_show_stats = self._is_monitoring and is_recording and not is_paused
        if should_show_stats:
            # 只要应该显示，就更新文本并确保标签可见
            self.lbl_duration.setText(f"⏱ {duration}")
            self.lbl_speed.setText(f"⚡ {speed}")
            self.lbl_size.setText(f"💾 {size}")

            if not self._stats_visible:
                # 从非录制状态变为录制状态，执行淡入动画
                self._stats_visible = True
                self._is_recording = True
                self.lbl_duration.fade_in()
                self.lbl_speed.fade_in()
                self.lbl_size.fade_in()
            else:
                # 已经在录制中，确保标签可见（防止透明度问题）
                self.lbl_duration.ensure_visible()
                self.lbl_speed.ensure_visible()
                self.lbl_size.ensure_visible()
        else:
            # 下播/闲置：只在从显示变为隐藏时淡出一次，避免重复 fade
            if self._stats_visible:
                self._stats_visible = False
                self._is_recording = False
                # 隐藏前将文字清零，防止残留旧的录制数值
                self.lbl_duration.setText("⏱ 00:00:00")
                self.lbl_speed.setText("⚡ 0 B/s")
                self.lbl_size.setText("💾 0 B")
                self.lbl_duration.fade_out()
                self.lbl_speed.fade_out()
                self.lbl_size.fade_out()
        self._refresh_cut_button_state()
        self._animate_status_transition(previous_signature, animate)

    def on_toggle(self, checked):
        self._is_monitoring = checked
        self.room_info["enabled"] = checked   # 关键：同步 room_info，否则 _start_recorder 拿到 stale False
        if not checked:
            self._monitor_code = "paused"
            self._live_code = "off"
            self._rec_code = "idle"
            self._render_status_labels()
        else:
            self._monitor_code = "active"
            self._render_status_labels()
        # 关闭监控时，不管什么状态，都立即隐藏统计信息！
        if not checked and self._stats_visible:
            self._stats_visible = False
            self._is_recording = False
            self.lbl_duration.setText("⏱ 00:00:00")
            self.lbl_speed.setText("⚡ 0 B/s")
            self.lbl_size.setText("💾 0 B")
            self.lbl_duration.fade_out()
            self.lbl_speed.fade_out()
            self.lbl_size.fade_out()
        self._refresh_cut_button_state()
        self.toggle_signal.emit(self.room_id, checked)

    def _refresh_cut_button_state(self):
        from ui.i18n import t

        can_cut = self.is_recording_active()
        self.btn_cut.setEnabled(can_cut)
        if hasattr(self, "_tip_cut"):
            self._tip_cut.setText(t("card.tip.cut") if can_cut else t("card.tip.cut_disabled"))

    def is_monitoring_enabled(self):
        return self._is_monitoring

    def is_live(self):
        return self._live_code == "on"

    def is_recording_active(self):
        return self._rec_code == "recording"

    def status_priority(self):
        if self.is_recording_active():
            return 0
        if self.is_live():
            return 1
        if self.is_monitoring_enabled():
            return 2
        return 3

    def can_trigger_cut(self):
        return self.is_recording_active()
