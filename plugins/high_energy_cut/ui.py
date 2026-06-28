"""
高能剪切 —— 主页面 UI。

布局:
+--------------------------------------------------------------+
| 顶部:主播筛选 (combo) | 日期 (date) | 刷新 | 计数             |
+----------------------+---------------------------------------+
| 左:Clip 列表          |  右:预览 (QMediaPlayer + 进度条)      |
| - 时间段 / 密度 / 摘要 |  ▶ 播放 / ⏸ 暂停 / 仅播此片段         |
| - 多选                |  当前时间 / 总时长                    |
| - 右键 +/-5s          |                                       |
+----------------------+---------------------------------------+
| 底部:状态 + 进度 +  ✂️ 剪切选中片段                          |
+--------------------------------------------------------------+

线程:
- ClipAnalysisWorker (QThread):后台解析弹幕 + 算 clip
- CutBatchWorker (QThread):后台按顺序切多个 clip
- 同一时间只跑一种(切换录播时取消 analysis)
"""
from __future__ import annotations
import os
import logging
from datetime import date
from typing import List, Optional

from PySide6.QtCore import Qt, QUrl, QDate, Signal
from PySide6.QtGui import QFont, QAction
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QComboBox, QDateEdit, QPushButton, QListWidget, QListWidgetItem,
    QSlider, QFrame, QSplitter, QMenu, QMessageBox, QProgressBar,
    QAbstractItemView
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from .analyzer import Clip, ClipAnalysisWorker
from .cutter import CutConfig, CutTask, CutBatchWorker, build_output_path
from .recording_index import RecordingIndex, RecordingSession
from .settings_ui import PluginSettings, HighEnergyCutSettingsPage
from .constants import PLUGIN_ID

logger = logging.getLogger(__name__)


class HighEnergyCutPage(QWidget):
    """插件主页面 widget。"""

    def __init__(self, plugin, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._plugin = plugin
        self._app = plugin.app
        self._index = RecordingIndex(self._app)
        self._clips: List[Clip] = []
        self._current_session: Optional[RecordingSession] = None
        self._analysis_worker: Optional[ClipAnalysisWorker] = None
        self._cut_worker: Optional[CutBatchWorker] = None
        self._clip_end_timer = None
        self._settings: PluginSettings = PluginSettings.from_dict(
            self._app.get_plugin_config(PLUGIN_ID)
        )

        self._build_ui()
        self._refresh_index()

    # ==================== UI ====================
    def _build_ui(self) -> None:
        self.setStyleSheet("background-color: #0F0F13; color: #E2E8F0;")
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 16, 24, 16)
        root.setSpacing(12)

        # ----- 顶部 -----
        top = QHBoxLayout()
        top.setSpacing(8)
        title = QLabel("高能剪切")
        title.setFont(QFont("Microsoft YaHei UI", 18, QFont.Bold))
        title.setStyleSheet("color: #F8FAFC;")
        top.addWidget(title)
        top.addSpacing(20)

        top.addWidget(QLabel("主播:"))
        self.cmb_uname = QComboBox()
        self.cmb_uname.setMinimumWidth(140)
        self.cmb_uname.setStyleSheet(COMBO_QSS)
        self.cmb_uname.currentTextChanged.connect(self._on_uname_changed)
        top.addWidget(self.cmb_uname)

        top.addWidget(QLabel("日期:"))
        self.date_picker = QDateEdit()
        self.date_picker.setCalendarPopup(True)
        self.date_picker.setDisplayFormat("yyyy-MM-dd")
        self.date_picker.setDate(QDate.currentDate())
        self.date_picker.dateChanged.connect(self._on_date_changed)
        self.date_picker.setStyleSheet(DATE_QSS)
        top.addWidget(self.date_picker)

        self.btn_refresh = QPushButton("重新扫描")
        self.btn_refresh.setStyleSheet(BTN_GHOST_QSS)
        self.btn_refresh.setFixedWidth(100)
        self.btn_refresh.setCursor(Qt.PointingHandCursor)
        self.btn_refresh.clicked.connect(self._refresh_index)
        top.addWidget(self.btn_refresh)

        top.addStretch()
        self.lbl_count = QLabel("未选择录播")
        self.lbl_count.setStyleSheet("color: #94A3B8;")
        top.addWidget(self.lbl_count)
        root.addLayout(top)

        # ----- 主体:左 clip 列表 / 右 预览 -----
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet(SPLITTER_QSS)
        root.addWidget(splitter, 1)

        # 左侧
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(8)

        list_head = QHBoxLayout()
        ll.addLayout(list_head)
        lbl_clips = QLabel("高能片段")
        lbl_clips.setFont(QFont("Microsoft YaHei UI", 12, QFont.Bold))
        lbl_clips.setStyleSheet("color: #F8FAFC;")
        list_head.addWidget(lbl_clips)
        list_head.addStretch()
        self.btn_analyze = QPushButton("🔍 分析")
        self.btn_analyze.setStyleSheet(BTN_PRIMARY_QSS)
        self.btn_analyze.setFixedWidth(100)
        self.btn_analyze.setCursor(Qt.PointingHandCursor)
        self.btn_analyze.clicked.connect(self._start_analysis)
        self.btn_analyze.setEnabled(False)
        list_head.addWidget(self.btn_analyze)

        self.lst_clips = QListWidget()
        self.lst_clips.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.lst_clips.setContextMenuPolicy(Qt.CustomContextMenu)
        self.lst_clips.customContextMenuRequested.connect(self._on_clip_context_menu)
        self.lst_clips.itemSelectionChanged.connect(self._on_clip_selection_changed)
        self.lst_clips.setStyleSheet(LIST_QSS)
        ll.addWidget(self.lst_clips, 1)

        self.lst_clips_note = QLabel("选择主播 + 日期后点『分析』")
        self.lst_clips_note.setAlignment(Qt.AlignCenter)
        self.lst_clips_note.setStyleSheet("color: #64748B; padding: 12px;")
        ll.addWidget(self.lst_clips_note)

        # 右侧:预览
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(8)

        rl.addWidget(self._build_preview_panel())

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 2)

        # ----- 底部 -----
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.lbl_status = QLabel("就绪")
        self.lbl_status.setStyleSheet("color: #94A3B8;")
        bottom.addWidget(self.lbl_status, 1)

        self.progress = QProgressBar()
        self.progress.setFixedWidth(260)
        self.progress.setVisible(False)
        self.progress.setStyleSheet(PROGRESS_QSS)
        bottom.addWidget(self.progress)

        self.btn_cut = QPushButton("✂️  剪切选中片段")
        self.btn_cut.setStyleSheet(BTN_PRIMARY_QSS)
        self.btn_cut.setFixedWidth(180)
        self.btn_cut.setCursor(Qt.PointingHandCursor)
        self.btn_cut.clicked.connect(self._on_cut)
        self.btn_cut.setEnabled(False)
        bottom.addWidget(self.btn_cut)
        root.addLayout(bottom)

    def _build_preview_panel(self) -> QWidget:
        """右侧预览:VideoWidget + 播放控制 + 仅播此片段。"""
        panel = QFrame()
        panel.setStyleSheet("background-color: #15161D; border: 1px solid #2D2E3A; border-radius: 8px;")
        v = QVBoxLayout(panel)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)

        self.video = QVideoWidget()
        self.video.setStyleSheet("background-color: black; border-radius: 4px;")
        v.addWidget(self.video, 1)

        # 控制行
        ctrl = QHBoxLayout()
        ctrl.setSpacing(8)
        self.btn_play = QPushButton("▶  播放")
        self.btn_play.setFixedWidth(80)
        self.btn_play.setStyleSheet(BTN_GHOST_QSS)
        self.btn_play.setCursor(Qt.PointingHandCursor)
        self.btn_play.clicked.connect(self._toggle_play)
        ctrl.addWidget(self.btn_play)

        self.btn_play_clip = QPushButton("仅播此片段")
        self.btn_play_clip.setFixedWidth(110)
        self.btn_play_clip.setStyleSheet(BTN_GHOST_QSS)
        self.btn_play_clip.setCursor(Qt.PointingHandCursor)
        self.btn_play_clip.clicked.connect(self._play_selected_clip)
        self.btn_play_clip.setEnabled(False)
        ctrl.addWidget(self.btn_play_clip)

        self.lbl_time = QLabel("00:00 / 00:00")
        self.lbl_time.setStyleSheet("color: #94A3B8; font-family: Consolas;")
        ctrl.addWidget(self.lbl_time)

        ctrl.addStretch()
        v.addLayout(ctrl)

        self.sld_pos = QSlider(Qt.Horizontal)
        self.sld_pos.setRange(0, 0)
        self.sld_pos.setStyleSheet(SLIDER_QSS)
        self.sld_pos.sliderMoved.connect(self._on_seek)
        v.addWidget(self.sld_pos)

        # 播放器
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.player.setVideoOutput(self.video)
        self.player.setAudioOutput(self.audio)
        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        return panel

    # ==================== 数据流 ====================
    def _refresh_index(self) -> None:
        """重扫磁盘。"""
        try:
            self._index.refresh()
        except Exception as e:
            logger.exception("扫描失败")
            QMessageBox.warning(self, "扫描失败", f"扫描录播目录失败: {e}")
            return
        self._populate_uname_combo()

    def _populate_uname_combo(self) -> None:
        prev = self.cmb_uname.currentText()
        self.cmb_uname.blockSignals(True)
        self.cmb_uname.clear()
        unames = self._index.list_unames()
        self.cmb_uname.addItem("全部", "")
        for u in unames:
            self.cmb_uname.addItem(u, u)
        idx = self.cmb_uname.findText(prev)
        if idx >= 0:
            self.cmb_uname.setCurrentIndex(idx)
        self.cmb_uname.blockSignals(False)
        self._on_uname_changed(self.cmb_uname.currentText())

    def _on_uname_changed(self, _text: str) -> None:
        self._refresh_dates()

    def _refresh_dates(self) -> None:
        uname = self.cmb_uname.currentData() or ""
        dates: List[date] = []
        if uname:
            dates = self._index.list_dates_for(uname)
        else:
            seen = set()
            for u in self._index.list_unames():
                for d in self._index.list_dates_for(u):
                    if d not in seen:
                        seen.add(d)
                        dates.append(d)
            dates.sort(reverse=True)
        # 仅展示有弹幕的日期
        valid_dates: List[date] = []
        for d in dates:
            if uname:
                sess = self._index.get_session(uname, d)
                if sess and sess.has_danmaku:
                    valid_dates.append(d)
            else:
                for u in self._index.list_unames():
                    sess = self._index.get_session(u, d)
                    if sess and sess.has_danmaku:
                        valid_dates.append(d)
                        break
        # 同步日期下拉可选范围
        if valid_dates:
            min_d = min(valid_dates)
            max_d = max(valid_dates)
            self.date_picker.setMinimumDate(QDate(min_d.year, min_d.month, min_d.day))
            self.date_picker.setMaximumDate(QDate(max_d.year, max_d.month, max_d.day))
            cur = self.date_picker.date()
            if cur < self.date_picker.minimumDate():
                self.date_picker.setDate(self.date_picker.minimumDate())
            elif cur > self.date_picker.maximumDate():
                self.date_picker.setDate(self.date_picker.maximumDate())
        self._update_session()

    def _on_date_changed(self, _qdate) -> None:
        self._update_session()

    def _update_session(self) -> None:
        uname = self.cmb_uname.currentData() or ""
        qd = self.date_picker.date()
        d = date(qd.year(), qd.month(), qd.day())
        sess = None
        if uname:
            sess = self._index.get_session(uname, d)
        else:
            for u in self._index.list_unames():
                s = self._index.get_session(u, d)
                if s and s.has_danmaku:
                    sess = s
                    uname = u
                    break
        self._current_session = sess
        if sess is None:
            self.lbl_count.setText("该日期下没有带弹幕的录播")
            self.btn_analyze.setEnabled(False)
            self._clips = []
            self._refresh_clip_list()
            self.player.stop()
            self.player.setSource(QUrl())
            return
        has_dm = sess.has_danmaku
        n_parts = len(sess.parts)
        self.lbl_count.setText(
            f"主播:{sess.uname}  |  {n_parts} 个 part  |  弹幕:{'有' if has_dm else '无'}"
        )
        self.btn_analyze.setEnabled(has_dm)
        target = next((p for p in sess.parts if p.has_danmaku), sess.parts[0])
        self._load_part_to_player(target)

    def _load_part_to_player(self, part) -> None:
        if not part or not os.path.exists(part.mp4_path):
            return
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(part.mp4_path))

    # ==================== 分析 ====================
    def _start_analysis(self) -> None:
        sess = self._current_session
        if sess is None or not sess.has_danmaku:
            return
        if self._analysis_worker and self._analysis_worker.isRunning():
            self._analysis_worker.cancel()
            self._analysis_worker.wait(2000)
        self._settings = PluginSettings.from_dict(self._app.get_plugin_config(PLUGIN_ID))
        part = next((p for p in sess.parts if p.has_danmaku), None)
        if part is None:
            return
        cfg = self._settings.to_analysis_config()
        self.btn_analyze.setEnabled(False)
        self.lbl_status.setText("分析中...")
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)

        self._analysis_worker = ClipAnalysisWorker(part, cfg, parent=self)
        self._analysis_worker.progress.connect(self._on_analysis_progress)
        self._analysis_worker.finished.connect(self._on_analysis_done)
        self._analysis_worker.failed.connect(self._on_analysis_failed)
        self._analysis_worker.start()

    def _on_analysis_progress(self, p: int) -> None:
        if p >= 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(p)

    def _on_analysis_done(self, clips: List[Clip]) -> None:
        self._clips = list(clips or [])
        self._refresh_clip_list()
        self.progress.setVisible(False)
        self.btn_analyze.setEnabled(self._current_session is not None and (self._current_session.has_danmaku))
        self.lbl_status.setText(f"分析完成,共 {len(self._clips)} 个高能片段")
        if not self._clips:
            QMessageBox.information(
                self, "分析完成",
                "没有发现明显的高能片段。\n\n可以试着调低『重复弹幕阈值』或『最短片段时长』。"
            )

    def _on_analysis_failed(self, msg: str) -> None:
        self.progress.setVisible(False)
        self.btn_analyze.setEnabled(True)
        self.lbl_status.setText("分析失败")
        QMessageBox.warning(self, "分析失败", msg)

    def _refresh_clip_list(self) -> None:
        self.lst_clips.clear()
        if not self._clips:
            self.lst_clips_note.setVisible(True)
            self.lst_clips.setVisible(False)
            return
        self.lst_clips_note.setVisible(False)
        self.lst_clips.setVisible(True)
        for i, c in enumerate(self._clips, 1):
            sample = c.sample_texts[0] if c.sample_texts else ""
            text = f"#{i:02d}  {c.display_range()}   时长 {c.duration:.1f}s   密度 {c.peak_density:.2f}   评分 {c.score:.2f}   \"{sample}\""
            item = QListWidgetItem(text)
            item.setData(Qt.UserRole, i - 1)
            self.lst_clips.addItem(item)

    def _on_clip_selection_changed(self) -> None:
        has = bool(self.lst_clips.selectedItems())
        self.btn_play_clip.setEnabled(has)
        self.btn_cut.setEnabled(has)

    def _on_clip_context_menu(self, pos) -> None:
        item = self.lst_clips.itemAt(pos)
        if item is None:
            return
        idx = item.data(Qt.UserRole)
        menu = QMenu(self)
        a_shift_back = QAction("整体提前 5 秒", self)
        a_shift_fwd = QAction("整体延后 5 秒", self)
        a_extend5 = QAction("前后各扩 5 秒", self)
        menu.addAction(a_shift_back)
        menu.addAction(a_shift_fwd)
        menu.addAction(a_extend5)
        chosen = menu.exec(self.lst_clips.mapToGlobal(pos))
        if chosen is None:
            return
        c = self._clips[idx]
        if chosen is a_shift_back:
            c.start = max(0.0, c.start - 5.0)
            c.end = max(c.start + 0.5, c.end - 5.0)
        elif chosen is a_shift_fwd:
            c.end = c.end + 5.0
            c.start = c.start + 5.0
        elif chosen is a_extend5:
            c.start = max(0.0, c.start - 5.0)
            c.end = c.end + 5.0
        c.duration = c.end - c.start
        self._refresh_clip_list()

    # ==================== 预览 ====================
    def _toggle_play(self) -> None:
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _play_selected_clip(self) -> None:
        items = self.lst_clips.selectedItems()
        if not items:
            return
        idx = items[0].data(Qt.UserRole)
        c = self._clips[idx]
        self.player.setPosition(int(c.start * 1000))
        self.player.play()
        from PySide6.QtCore import QTimer
        if self._clip_end_timer is not None:
            self._clip_end_timer.stop()
        self._clip_end_timer = QTimer(self)
        self._clip_end_timer.setSingleShot(True)
        self._clip_end_timer.timeout.connect(self.player.pause)
        self._clip_end_timer.start(int(c.duration * 1000) + 200)

    def _on_seek(self, ms: int) -> None:
        self.player.setPosition(ms)

    def _on_position(self, ms: int) -> None:
        self.sld_pos.setValue(ms)
        self._update_time_label(ms, self.player.duration())

    def _on_duration(self, ms: int) -> None:
        self.sld_pos.setRange(0, ms)
        self._update_time_label(self.player.position(), ms)

    def _on_playback_state(self, state) -> None:
        if state == QMediaPlayer.PlayingState:
            self.btn_play.setText("⏸  暂停")
        else:
            self.btn_play.setText("▶  播放")

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.InvalidMedia:
            self.lbl_status.setText("无法播放该 MP4(可能文件还在被录制)")

    def _update_time_label(self, pos_ms: int, dur_ms: int) -> None:
        self.lbl_time.setText(f"{_fmt_ms(pos_ms)} / {_fmt_ms(dur_ms)}")

    # ==================== 剪切 ====================
    def _on_cut(self) -> None:
        items = self.lst_clips.selectedItems()
        if not items:
            return
        sess = self._current_session
        if sess is None:
            return
        part = next((p for p in sess.parts if p.has_danmaku), None)
        if part is None or not os.path.exists(part.mp4_path):
            QMessageBox.warning(self, "无法剪切", "找不到有效的 MP4 文件")
            return
        clips = [self._clips[i.data(Qt.UserRole)] for i in items]
        clips.sort(key=lambda c: c.start)

        ffmpeg = self._app.get_ffmpeg_cmd() or "ffmpeg"
        cfg = CutConfig(
            ffmpeg_cmd=ffmpeg,
            name_template=self._settings.name_template,
            crf=self._settings.crf,
            use_copy_first=self._settings.fallback_to_reencode,
        )
        tasks = []
        for i, c in enumerate(clips, start=1):
            out = build_output_path(part.mp4_path, c, i, cfg)
            tasks.append(CutTask(clip=c, src_mp4=part.mp4_path, output_path=out))

        if self._cut_worker and self._cut_worker.isRunning():
            QMessageBox.information(self, "请稍候", "已有剪切任务在进行中")
            return

        ret = QMessageBox.question(
            self, "确认剪切",
            f"将剪切 {len(tasks)} 个片段到:\n{os.path.dirname(tasks[0].output_path)}\n\n是否继续?",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        if ret != QMessageBox.Yes:
            return

        self.btn_cut.setEnabled(False)
        self.btn_analyze.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.lbl_status.setText(f"准备剪切 {len(tasks)} 个片段...")

        self._cut_worker = CutBatchWorker(tasks, cfg, parent=self)
        self._cut_worker.started_clip.connect(self._on_cut_started)
        self._cut_worker.clip_done.connect(self._on_cut_clip_done)
        self._cut_worker.finished.connect(self._on_cut_finished)
        self._cut_worker.failed.connect(self._on_cut_failed)
        self._cut_worker.start()

    def _on_cut_started(self, i: int, total: int) -> None:
        self.lbl_status.setText(f"正在剪切 {i}/{total}...")

    def _on_cut_clip_done(self, i: int, total: int, out_path: str, ok: bool) -> None:
        pct = int(i / total * 100)
        self.progress.setValue(pct)
        if ok:
            self.lbl_status.setText(f"已完成 {i}/{total}  →  {os.path.basename(out_path)}")
        else:
            self.lbl_status.setText(f"失败 {i}/{total}  →  {os.path.basename(out_path)}")

    def _on_cut_finished(self, success: int, total: int) -> None:
        self.progress.setVisible(False)
        self.btn_cut.setEnabled(bool(self.lst_clips.selectedItems()))
        self.btn_analyze.setEnabled(True)
        if success == total:
            self.lbl_status.setText(f"✅ 全部完成 ({success}/{total})")
            QMessageBox.information(self, "剪切完成", f"成功 {success}/{total} 个片段。\n输出目录请看底部状态栏。")
        else:
            self.lbl_status.setText(f"⚠️ 部分失败 ({success}/{total})")
            QMessageBox.warning(self, "部分失败", f"成功 {success}/{total} 个片段,其余失败。\n请看日志。")

    def _on_cut_failed(self, msg: str) -> None:
        self.progress.setVisible(False)
        self.btn_cut.setEnabled(bool(self.lst_clips.selectedItems()))
        self.btn_analyze.setEnabled(True)
        self.lbl_status.setText("剪切失败")
        QMessageBox.critical(self, "剪切失败", msg)


def _fmt_ms(ms: int) -> str:
    s = max(0, int(ms // 1000))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{ss:02d}"
    return f"{m:02d}:{ss:02d}"


# ==================== 样式 ====================
COMBO_QSS = """
QComboBox {
    background-color: #181920;
    border: 1px solid #2D2E3A;
    border-radius: 6px;
    color: #E2E8F0;
    padding: 6px 10px;
    font-size: 13px;
}
QComboBox:hover { border: 1px solid #3B82F6; }
QComboBox::drop-down { width: 18px; border: none; }
"""

DATE_QSS = """
QDateEdit {
    background-color: #181920;
    border: 1px solid #2D2E3A;
    border-radius: 6px;
    color: #E2E8F0;
    padding: 6px 10px;
    font-size: 13px;
}
QDateEdit:hover { border: 1px solid #3B82F6; }
"""

LIST_QSS = """
QListWidget {
    background-color: #15161D;
    border: 1px solid #2D2E3A;
    border-radius: 8px;
    color: #E2E8F0;
    font-size: 12px;
    font-family: Consolas;
}
QListWidget::item {
    padding: 8px 10px;
    border-bottom: 1px solid #252631;
}
QListWidget::item:selected {
    background-color: #252631;
    color: #F8FAFC;
}
QListWidget::item:hover { background-color: #1F2029; }
"""

SPLITTER_QSS = """
QSplitter::handle {
    background-color: #1A1B21;
    width: 4px;
}
QSplitter::handle:hover { background-color: #3B82F6; }
"""

PROGRESS_QSS = """
QProgressBar {
    border: none;
    border-radius: 4px;
    background-color: #252631;
    text-align: center;
    color: #F8FAFC;
    height: 18px;
    font-size: 11px;
}
QProgressBar::chunk { background-color: #3B82F6; border-radius: 4px; }
"""

SLIDER_QSS = """
QSlider::groove:horizontal {
    height: 6px;
    background: #252631;
    border-radius: 3px;
}
QSlider::handle:horizontal {
    width: 14px;
    margin: -4px 0;
    background: #3B82F6;
    border-radius: 7px;
}
QSlider::sub-page:horizontal { background: #3B82F6; border-radius: 3px; }
"""

BTN_PRIMARY_QSS = """
QPushButton {
    background-color: #3B82F6;
    color: white;
    border: none;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 13px;
    font-weight: 600;
}
QPushButton:hover { background-color: #2563EB; }
QPushButton:pressed { background-color: #1D4ED8; }
QPushButton:disabled { background-color: #2D2E3A; color: #64748B; }
"""

BTN_GHOST_QSS = """
QPushButton {
    background-color: #181920;
    color: #E2E8F0;
    border: 1px solid #2D2E3A;
    border-radius: 6px;
    padding: 8px 14px;
    font-size: 13px;
}
QPushButton:hover { background-color: #252631; border: 1px solid #3B82F6; }
QPushButton:disabled { color: #64748B; }
"""
