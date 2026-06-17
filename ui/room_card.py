# ui/room_card.py
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel,
                              QPushButton, QToolButton, QSizePolicy,
                              QGraphicsDropShadowEffect, QWidget, QApplication)
from PySide6.QtCore import (Qt, Signal, QTimer, QPoint, QRect, QSize, QPropertyAnimation, QEasingCurve, QObject, QEvent)
from PySide6.QtGui import QPixmap, QFont, QColor, QCursor, QPainter, QBrush, QPainterPath
from PySide6.QtWidgets import QGraphicsOpacityEffect
import os
import subprocess
import platform
import threading
import requests

from core.config import get_global_setting, get_effective_save_dir, VIDEO_SAVE_DIR, get_room_config


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
        # 关键: paintEvent 完全自绘,这里 setStyleSheet 是兜底(以防 paintEvent 出 bug)
        self.setStyleSheet("""
            QLabel {
                background-color: #1F2030;
                color: #FFFFFF;
                border: 1px solid #3D3E4F;
                border-radius: 6px;
                font-weight: 600;
            }
        """)
        self.setAlignment(Qt.AlignCenter)
        self.setText(text)
        self._resize_to_text()
        self.hide()

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
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        rect = self.rect()
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(1, 1, -1, -1), 5, 5)
        p.fillPath(path, QColor("#1F2030"))
        pen = QPen(QColor("#3D3E4F"))
        pen.setWidth(1)
        p.setPen(pen)
        p.drawPath(path)
        p.setPen(QColor("#FFFFFF"))
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
    
    def isChecked(self):
        return self._checked
    
    def setChecked(self, checked):
        if self._checked != checked:
            self._checked = checked
            if checked:
                self._target_x = 32
            else:
                self._target_x = 4
            self._animation_step = 0
            self._animation_timer.start(10)  # 10ms 更新一次
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
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        
        if self._checked:
            painter.setBrush(QBrush(QColor(74, 222, 128)))
        else:
            painter.setBrush(QBrush(QColor(55, 58, 72)))
        
        painter.setPen(Qt.NoPen)
        painter.drawRoundedRect(0, 0, 64, 32, 16, 16)
        
        painter.setBrush(QBrush(QColor(255, 255, 255)))
        painter.drawRoundedRect(int(self._switch_x), 4, 24, 24, 12, 12)


class AnimatedLabel(QLabel):
    """带淡入淡出动画的标签（使用 QGraphicsOpacityEffect，更快更可靠）"""
    def __init__(self, text="", parent=None):
        super().__init__(text, parent)
        self.setStyleSheet("border: none; background: transparent;")
        
        # 使用 QGraphicsOpacityEffect 来控制透明度！更快更可靠！
        self._effect = QGraphicsOpacityEffect()
        self._effect.setOpacity(0.0)  # 初始完全透明
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
    _avatar_ready = Signal(QPixmap)

    def __init__(self, room_info: dict, parent=None):
        super().__init__(parent)
        self.room_id = str(room_info["room_id"])
        self.room_info = room_info
        self.current_save_path = None
        self._is_recording = False
        # 保存监控开关状态
        self._is_monitoring = room_info.get("enabled", True)
        
        # 连接头像信号
        self._avatar_ready.connect(self._set_avatar)

        # 固定卡片大小，确保所有卡片一致！
        self.setFixedSize(540, 260)
        self.setStyleSheet("""
            QFrame {
                background-color: #1A1B21;
                border-radius: 14px;
                border: 1px solid #272833;
            }
        """)

        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(16)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # ==================== 顶部信息 ====================
        top_layout = QHBoxLayout()

        # 头像
        self.avatar = QLabel()
        self.avatar.setFixedSize(64, 64)
        self.avatar.setStyleSheet("border-radius: 32px; background-color: #252631; border: none;")
        self.avatar.setAlignment(Qt.AlignCenter)
        self.avatar.setText("📺")
        self.load_avatar(room_info.get("face", ""))

        # 名称和标题
        info_layout = QVBoxLayout()
        info_layout.setSpacing(4)
        
        uname_layout = QHBoxLayout()
        self.lbl_uname = QLabel(room_info.get("uname", f"房间_{self.room_id}"))
        self.lbl_uname.setFont(QFont("Microsoft YaHei UI", 15, QFont.Bold))
        self.lbl_uname.setStyleSheet("color: #F8FAFC; border: none; background: transparent;")
        
        self.lbl_room_id = QLabel(self.room_id)
        self.lbl_room_id.setStyleSheet("color: #3B82F6; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        
        uname_layout.addWidget(self.lbl_uname)
        uname_layout.addStretch()
        uname_layout.addWidget(self.lbl_room_id)

        self.lbl_title = QLabel(room_info.get("title", "暂无标题"))
        self.lbl_title.setStyleSheet("color: #94A3B8; font-size: 13px; border: none; background: transparent;")
        self.lbl_title.setWordWrap(True)

        info_layout.addLayout(uname_layout)
        info_layout.addWidget(self.lbl_title)

        top_layout.addWidget(self.avatar)
        top_layout.addLayout(info_layout, 1)

        main_layout.addLayout(top_layout)

        # ==================== 标签行 ====================
        tag_layout = QHBoxLayout()
        self.tag_platform = self._create_tag("bilibili", "#3B82F6")
        self.tag_parent = self._create_tag(room_info.get("parent_area_name", "未知分区"), "#7C3AED")
        self.tag_area = self._create_tag(room_info.get("area_name", "未知内容"), "#A855F7")

        tag_layout.addWidget(self.tag_platform)
        tag_layout.addWidget(self.tag_parent)
        tag_layout.addWidget(self.tag_area)
        tag_layout.addStretch()
        main_layout.addLayout(tag_layout)

        # ==================== 状态行 ====================
        status_layout = QHBoxLayout()
        status_layout.setSpacing(24)
        
        self.lbl_m = QLabel("⏯ 监控中")
        self.lbl_l = QLabel("🌙 未开播")
        self.lbl_r = QLabel("⏳ 闲置中")

        for lbl in [self.lbl_m, self.lbl_l, self.lbl_r]:
            lbl.setStyleSheet("color: #64748B; font-size: 13px; font-weight: 500; border: none; background: transparent;")
            status_layout.addWidget(lbl)
        
        status_layout.addStretch()
        main_layout.addLayout(status_layout)

        # ==================== 录制统计行 (默认隐藏) ====================
        stats_layout = QHBoxLayout()
        stats_layout.setSpacing(24)
        
        self.lbl_duration = AnimatedLabel("⏱ 00:00:00")
        self.lbl_duration.setStyleSheet("color: #94A3B8; font-size: 13px; border: none; background: transparent;")
        
        self.lbl_speed = AnimatedLabel("⚡ 0 KB/s")
        self.lbl_speed.setStyleSheet("color: #94A3B8; font-size: 13px; border: none; background: transparent;")
        
        self.lbl_size = AnimatedLabel("💾 0 B")
        self.lbl_size.setStyleSheet("color: #94A3B8; font-size: 13px; border: none; background: transparent;")

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
        self.switch_btn.setChecked(room_info.get("enabled", True))
        self.switch_btn.toggled.connect(self.on_toggle)

        btn_folder = _HoverToolButton("📁", "打开文件夹")
        btn_folder.setFixedSize(40, 40)
        # 关键: 不再 setToolTip — 系统 tooltip 在 Win11 暗色主题下是黑底黑字,
        # 会盖在自定义 HoverLabel 上面,看起来像"全黑"。完全用 HoverLabel 替代。
        btn_folder.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                color: #F59E0B;
                border-radius: 10px;
                font-size: 18px;
                border: none;
            }
            QToolButton:hover {
                background-color: #2E2A1A;
            }
            QToolButton:pressed {
                background-color: #4A3A2A;
            }
        """)
        btn_folder.setCursor(QCursor(Qt.PointingHandCursor))
        btn_folder.clicked.connect(lambda: self.open_folder_signal.emit(self.room_id))
        self._tip_folder = HoverLabel(btn_folder, "打开文件夹")
        btn_folder.attach_tip(self._tip_folder)

        self.btn_cut = _HoverToolButton("✂️", "未在录制")
        self.btn_cut.setFixedSize(40, 40)
        self.btn_cut.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                color: #8B5CF6;
                border-radius: 10px;
                font-size: 18px;
                border: none;
            }
            QToolButton:hover {
                background-color: #2A1A2E;
            }
            QToolButton:pressed {
                background-color: #3A2A3E;
            }
            QToolButton:disabled {
                color: #475569;
                background-color: transparent;
            }
        """)
        self.btn_cut.setCursor(QCursor(Qt.PointingHandCursor))
        self.btn_cut.clicked.connect(lambda: self.cut_signal.emit(self.room_id))
        self._tip_cut = HoverLabel(self.btn_cut, "未在录制")
        self.btn_cut.attach_tip(self._tip_cut)

        btn_delete = _HoverToolButton("🗑️", "删除该频道")
        btn_delete.setFixedSize(40, 40)
        btn_delete.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                color: #EF4444;
                border-radius: 10px;
                font-size: 18px;
                border: none;
            }
            QToolButton:hover {
                background-color: #2E1A1E;
            }
            QToolButton:pressed {
                background-color: #3E1A2E;
            }
        """)
        btn_delete.setCursor(QCursor(Qt.PointingHandCursor))
        btn_delete.clicked.connect(lambda: self.delete_signal.emit(self.room_id))
        self._tip_delete = HoverLabel(btn_delete, "删除该频道")
        btn_delete.attach_tip(self._tip_delete)

        btn_settings = _HoverToolButton("⚙️", "频道设置")
        btn_settings.setFixedSize(40, 40)
        btn_settings.setStyleSheet("""
            QToolButton {
                background-color: transparent;
                color: #4ADE80;
                border-radius: 10px;
                font-size: 18px;
                border: none;
            }
            QToolButton:hover {
                background-color: #1A2E20;
            }
            QToolButton:pressed {
                background-color: #2A3E30;
            }
        """)
        btn_settings.setCursor(QCursor(Qt.PointingHandCursor))
        btn_settings.clicked.connect(lambda: self.settings_signal.emit(self.room_id))
        self._tip_settings = HoverLabel(btn_settings, "频道设置")
        btn_settings.attach_tip(self._tip_settings)

        btn_layout.addWidget(self.switch_btn)
        btn_layout.addStretch()
        btn_layout.addWidget(btn_folder)
        btn_layout.addWidget(self.btn_cut)
        btn_layout.addWidget(btn_delete)
        btn_layout.addWidget(btn_settings)
        
        main_layout.addLayout(btn_layout)
        self._refresh_cut_button_state()

    def _create_tag(self, text, color):
        label = QLabel(text)
        label.setStyleSheet(f"""
            background-color: #15161D;
            color: {color};
            padding: 4px 12px;
            border-radius: 14px;
            font-size: 12px;
            font-weight: 600;
            border: none;
        """)
        return label

    def load_avatar(self, url):
        # 清理URL，去掉反引号和空格
        if url:
            url = url.strip().strip('`').strip('"').strip("'")
        if not url:
            return
        
        def _load():
            try:
                from curl_cffi import requests
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                }
                response = requests.get(url, headers=headers, impersonate="chrome110", timeout=30)
                data = response.content
                
                pix = QPixmap()
                pix.loadFromData(data)
                if not pix.isNull():
                    # 发送信号，主线程会处理
                    self._avatar_ready.emit(pix)
            except Exception as e:
                pass
        
        thread = threading.Thread(target=_load, daemon=True)
        thread.start()
    
    def _set_avatar(self, pixmap):
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

    def update_status(self, m: str, l: str, r: str, title: str,
                      duration="00:00:00", speed="0 B/s", size="0 B",
                      parent_area="", area_name=""):
        self.room_info["title"] = title
        if parent_area:
            self.room_info["parent_area_name"] = parent_area
        if area_name:
            self.room_info["area_name"] = area_name

        # 关键守卫: 当卡片监控已关闭时, 无论收到什么信号都显示"已暂停/闲置中"。
        # 防止 _stop_recorder 手动设完状态后, recorder 线程残留在 Qt 事件队列中
        # 的 status_updated 信号又覆盖成"监控中/录制中"。
        if not self._is_monitoring:
            self.lbl_m.setText("⏸ 已暂停")
            self.lbl_m.setStyleSheet("color: #F59E0B; font-size: 13px; font-weight: 500; border: none; background: transparent;")
            self.lbl_l.setText("🌙 未开播")
            self.lbl_l.setStyleSheet("color: #64748B; font-size: 13px; font-weight: 500; border: none; background: transparent;")
            self.lbl_r.setText("⏳ 闲置中")
            self.lbl_r.setStyleSheet("color: #64748B; font-size: 13px; font-weight: 500; border: none; background: transparent;")
            self.lbl_title.setText(title)
            self.lbl_duration.fade_out()
            self.lbl_speed.fade_out()
            self.lbl_size.fade_out()
            self._is_recording = False
            self._refresh_cut_button_state()
            return

        if "出错" in m:
            self.lbl_m.setText("❌ 出错")
            self.lbl_m.setStyleSheet("color: #EF4444; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        elif "暂停" in m or "已暂停" in m:
            self.lbl_m.setText("⏸ 已暂停")
            self.lbl_m.setStyleSheet("color: #F59E0B; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        else:
            self.lbl_m.setText("⏯ 监控中")
            self.lbl_m.setStyleSheet("color: #4ADE80; font-size: 13px; font-weight: 500; border: none; background: transparent;")

        if "直播中" in l:
            self.lbl_l.setText("🔴 直播中")
            self.lbl_l.setStyleSheet("color: #EF4444; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        else:
            self.lbl_l.setText("🌙 未开播")
            self.lbl_l.setStyleSheet("color: #64748B; font-size: 13px; font-weight: 500; border: none; background: transparent;")

        is_recording = "录制" in r
        is_paused = "暂停" in m or "已暂停" in m
        if is_recording:
            self.lbl_r.setText("📹 录制中")
            self.lbl_r.setStyleSheet("color: #3B82F6; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        elif "条件过滤" in r:
            self.lbl_r.setText("⏸ 条件过滤")
            self.lbl_r.setStyleSheet("color: #F59E0B; font-size: 13px; font-weight: 500; border: none; background: transparent;")
        else:
            self.lbl_r.setText("⏳ 闲置中")
            self.lbl_r.setStyleSheet("color: #64748B; font-size: 13px; font-weight: 500; border: none; background: transparent;")

        self.lbl_title.setText(title)

        should_show_stats = self._is_monitoring and is_recording and not is_paused
        if should_show_stats:
            # 只要应该显示，就更新文本并确保标签可见
            self.lbl_duration.setText(f"⏱ {duration}")
            self.lbl_speed.setText(f"⚡ {speed}")
            self.lbl_size.setText(f"💾 {size}")
            
            if not self._is_recording:
                # 从非录制状态变为录制状态，执行淡入动画
                self._is_recording = True
                self.lbl_duration.fade_in()
                self.lbl_speed.fade_in()
                self.lbl_size.fade_in()
            else:
                # 已经在录制中，确保标签可见（防止透明度问题）
                self.lbl_duration.ensure_visible()
                self.lbl_speed.ensure_visible()
                self.lbl_size.ensure_visible()
        elif not should_show_stats:
            # 不管当前 _is_recording 是什么状态，只要不应该显示，就淡出
            self._is_recording = False
            self.lbl_duration.fade_out()
            self.lbl_speed.fade_out()
            self.lbl_size.fade_out()
        self._refresh_cut_button_state()

    def on_toggle(self, checked):
        self._is_monitoring = checked
        self.room_info["enabled"] = checked   # 关键：同步 room_info，否则 _start_recorder 拿到 stale False
        # 关闭监控时，不管什么状态，都立即隐藏统计信息！
        if not checked:
            self._is_recording = False
            self.lbl_duration.fade_out()
            self.lbl_speed.fade_out()
            self.lbl_size.fade_out()
        self._refresh_cut_button_state()
        self.toggle_signal.emit(self.room_id, checked)

    def _refresh_cut_button_state(self):
        can_cut = self.is_recording_active()
        self.btn_cut.setEnabled(can_cut)
        if can_cut:
            if hasattr(self, "_tip_cut"):
                self._tip_cut.setText("立即切割")
        else:
            if hasattr(self, "_tip_cut"):
                self._tip_cut.setText("未在录制")

    def is_monitoring_enabled(self):
        return self._is_monitoring

    def is_live(self):
        return "直播中" in self.lbl_l.text()

    def is_recording_active(self):
        return "录制中" in self.lbl_r.text()

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
