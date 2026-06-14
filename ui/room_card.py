# ui/room_card.py
from PySide6.QtWidgets import (QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
                              QPushButton, QToolButton, QSizePolicy, 
                              QGraphicsDropShadowEffect, QWidget)
from PySide6.QtCore import (Qt, Signal, QTimer, QPoint, QRect, QSize, QPropertyAnimation, QEasingCurve)
from PySide6.QtGui import QPixmap, QFont, QColor, QCursor, QPainter, QBrush, QPainterPath
from PySide6.QtWidgets import QGraphicsOpacityEffect
import os
import subprocess
import platform
import threading
import requests

from core.config import get_global_setting, get_effective_save_dir, VIDEO_SAVE_DIR, get_room_config


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

        btn_folder = QToolButton()
        btn_folder.setText("📁")
        btn_folder.setFixedSize(40, 40)
        btn_folder.setToolTip("打开文件夹")
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

        self.btn_cut = QToolButton()
        self.btn_cut.setText("✂️")
        self.btn_cut.setFixedSize(40, 40)
        self.btn_cut.setToolTip("立即切割")
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

        btn_delete = QToolButton()
        btn_delete.setText("🗑️")
        btn_delete.setFixedSize(40, 40)
        btn_delete.setToolTip("删除")
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

        btn_settings = QToolButton()
        btn_settings.setText("⚙️")
        btn_settings.setFixedSize(40, 40)
        btn_settings.setToolTip("房间设置")
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
            self.btn_cut.setToolTip("立即切割")
        else:
            self.btn_cut.setToolTip("仅在录制中时可切割")

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
