from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.config import VIDEO_SAVE_DIR, get_global_setting
from core.disk_guard import check_disk, disk_level, format_gb
from core.recording_history import group_by_date, read_recent
from core.utils import format_size
from ui.theme import enable_surface, repolish


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds or 0))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h:d}:{m:02d}:{s:02d}"
    return f"{m:d}:{s:02d}"


def _fit_button(button: QPushButton, *, height: int = 36, min_width: int = 80) -> QPushButton:
    """Avoid global QPushButton padding clipping text at fixed heights."""
    button.setCursor(Qt.PointingHandCursor)
    button.setFixedHeight(height)
    button.setProperty("settingsCompact", True)
    text_w = button.fontMetrics().horizontalAdvance(button.text())
    button.setMinimumWidth(max(min_width, text_w + 28))
    repolish(button)
    return button


def history_row_matches(row: dict, text: str) -> bool:
    """Pure search helper for room/title/path matching."""
    q = (text or "").strip().lower()
    if not q:
        return True
    haystacks = (
        str(row.get("uname") or ""),
        str(row.get("title") or ""),
        str(row.get("room_id") or ""),
        str(row.get("path") or ""),
        str(row.get("source_path") or ""),
        str(row.get("close_reason") or ""),
        str(row.get("health") or ""),
    )
    return any(q in value.lower() for value in haystacks)


class RecentRecordingsPage(QWidget):
    """Standalone page: newest closed recordings grouped by day."""

    def __init__(self, parent=None):
        super().__init__(parent)
        from ui.i18n import t

        root = QVBoxLayout(self)
        root.setContentsMargins(32, 24, 32, 24)
        root.setSpacing(16)

        # Top title row
        title_row = QHBoxLayout()
        self.title = QLabel(t("history.title"))
        self.title.setFont(QFont("Microsoft YaHei UI", 24, QFont.Bold))
        self.title.setProperty("role", "title")
        title_row.addWidget(self.title)
        title_row.addStretch()
        root.addLayout(title_row)

        self.desc = QLabel(t("history.desc"))
        self.desc.setProperty("role", "secondary")
        self.desc.setWordWrap(True)
        root.addWidget(self.desc)

        # Disk free-space banner: global save_dir volume only
        self.disk_banner = QFrame()
        self.disk_banner.setObjectName("aboutCard")
        enable_surface(self.disk_banner)
        disk_layout = QVBoxLayout(self.disk_banner)
        disk_layout.setContentsMargins(14, 12, 14, 12)
        disk_layout.setSpacing(8)
        self.disk_label = QLabel("")
        self.disk_label.setWordWrap(True)
        self.disk_label.setProperty("statusTone", "neutral")
        self.disk_bar = QProgressBar()
        self.disk_bar.setObjectName("historyDiskBar")
        self.disk_bar.setRange(0, 100)
        self.disk_bar.setValue(0)
        self.disk_bar.setTextVisible(True)
        self.disk_bar.setFixedHeight(18)
        self.disk_bar.setProperty("diskLevel", "ok")
        disk_layout.addWidget(self.disk_label)
        disk_layout.addWidget(self.disk_bar)
        root.addWidget(self.disk_banner)

        # Toolbar
        toolbar = QFrame()
        toolbar.setObjectName("channelsToolbar")
        enable_surface(toolbar)
        bar = QHBoxLayout(toolbar)
        bar.setContentsMargins(16, 12, 16, 12)
        bar.setSpacing(10)

        self.search_input = QLineEdit()
        self.search_input.setObjectName("channelSearch")
        self.search_input.setPlaceholderText(t("history.search_placeholder"))
        self.search_input.setFixedHeight(36)
        self.search_input.setMinimumWidth(280)
        self.search_input.textChanged.connect(self.refresh)

        self.room_filter = QComboBox()
        self.room_filter.setMinimumWidth(180)
        self.room_filter.setFixedHeight(36)
        self.room_filter.currentIndexChanged.connect(self.refresh)

        self.status_filter = QComboBox()
        self.status_filter.setMinimumWidth(120)
        self.status_filter.setFixedHeight(36)
        self.status_filter.addItem(t("history.filter_status_all"), "all")
        self.status_filter.addItem(t("history.filter_status_ok"), "ok")
        self.status_filter.addItem(t("history.filter_status_failed"), "failed")
        self.status_filter.currentIndexChanged.connect(self.refresh)

        refresh_btn = QPushButton(t("history.refresh"))
        refresh_btn.setProperty("variant", "secondary")
        _fit_button(refresh_btn, height=36, min_width=72)
        refresh_btn.clicked.connect(self.refresh)
        self.refresh_btn = refresh_btn

        bar.addWidget(self.search_input, 1)
        bar.addWidget(QLabel(t("history.filter_room")))
        bar.addWidget(self.room_filter)
        bar.addWidget(QLabel(t("history.filter_status")))
        bar.addWidget(self.status_filter)
        bar.addWidget(refresh_btn)
        root.addWidget(toolbar)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.body = QWidget()
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(0, 0, 0, 0)
        self.body_layout.setSpacing(12)
        self.scroll.setWidget(self.body)
        root.addWidget(self.scroll, 1)

        self.status = QLabel("")
        self.status.setProperty("role", "muted")
        root.addWidget(self.status)
        self.refresh()

    def retranslate_ui(self):
        from ui.i18n import t
        self.title.setText(t("history.title"))
        self.desc.setText(t("history.desc"))
        self.search_input.setPlaceholderText(t("history.search_placeholder"))
        # rebuild filters with current language labels, keep selection
        room_id = self._selected_room_id()
        status = self.status_filter.currentData() or "all"
        self.status_filter.blockSignals(True)
        self.status_filter.clear()
        self.status_filter.addItem(t("history.filter_status_all"), "all")
        self.status_filter.addItem(t("history.filter_status_ok"), "ok")
        self.status_filter.addItem(t("history.filter_status_failed"), "failed")
        idx = self.status_filter.findData(status)
        self.status_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.status_filter.blockSignals(False)
        self.refresh()
        # restore room selection after refresh rebuilds room list
        if room_id:
            ridx = self.room_filter.findData(room_id)
            if ridx >= 0:
                self.room_filter.setCurrentIndex(ridx)

    def _disk_thresholds(self) -> tuple[float, float]:
        try:
            min_g = max(0.0, float(get_global_setting("disk_min_free_gb") or 5.0))
        except (TypeError, ValueError):
            min_g = 5.0
        try:
            warn_g = float(get_global_setting("disk_warn_free_gb") or 15.0)
        except (TypeError, ValueError):
            warn_g = 15.0
        return min_g, warn_g

    def _global_save_dir(self) -> str:
        return str(
            get_global_setting("save_dir")
            or VIDEO_SAVE_DIR
            or ""
        ).strip() or str(VIDEO_SAVE_DIR)

    def _update_disk_banner(self):
        """Show free space for the volume that holds the global save_dir only."""
        from ui.i18n import t

        min_g, warn_g = self._disk_thresholds()
        save_dir = self._global_save_dir()
        try:
            status = check_disk(save_dir, min_free_gb=min_g)
            level = disk_level(
                status.free_bytes,
                min_free_gb=min_g,
                warn_free_gb=warn_g,
            )
            free_bytes = status.free_bytes
            total_bytes = status.total_bytes
            path_s = status.path
        except OSError:
            level = "unknown"
            free_bytes = 0
            total_bytes = 0
            path_s = save_dir

        tone = {
            "ok": "success",
            "warning": "warning",
            "critical": "danger",
            "unknown": "neutral",
        }.get(level, "neutral")
        free_s = format_gb(free_bytes)
        total_s = format_gb(total_bytes) if total_bytes else "?"
        used_bytes = max(0, total_bytes - free_bytes) if total_bytes else 0
        used_s = format_gb(used_bytes) if total_bytes else "?"
        min_s = f"{min_g:.1f}GB"
        warn_s = f"{warn_g:.1f}GB"
        if total_bytes > 0:
            pct = int(round(100.0 * used_bytes / total_bytes))
            pct = max(0, min(100, pct))
        else:
            pct = 0

        if level == "critical":
            text = t(
                "history.disk.critical",
                path=path_s,
                free=free_s,
                used=used_s,
                total=total_s,
                min=min_s,
            )
        elif level == "warning":
            text = t(
                "history.disk.warn",
                path=path_s,
                free=free_s,
                used=used_s,
                total=total_s,
                warn=warn_s,
            )
        elif level == "unknown":
            text = t("history.disk.unknown", path=path_s)
        else:
            text = t(
                "history.disk.ok",
                path=path_s,
                free=free_s,
                used=used_s,
                total=total_s,
            )

        self.disk_label.setText(text)
        self.disk_label.setProperty("statusTone", tone)
        repolish(self.disk_label)

        self.disk_bar.setValue(pct)
        self.disk_bar.setFormat(t("history.disk.used_pct", pct=pct))
        self.disk_bar.setProperty("diskLevel", level if level != "unknown" else "ok")
        self.disk_bar.setVisible(level != "unknown" and total_bytes > 0)
        repolish(self.disk_bar)
        self.disk_banner.show()

    def _selected_room_id(self):
        data = self.room_filter.currentData()
        return None if not data else str(data)

    def _selected_status(self) -> str:
        return str(self.status_filter.currentData() or "all")

    def _rebuild_room_filter(self, rows: list[dict]):
        current = self._selected_room_id()
        rooms = {}
        for row in rows:
            rid = str(row.get("room_id") or "")
            if not rid:
                continue
            rooms.setdefault(rid, row.get("uname") or rid)
        self.room_filter.blockSignals(True)
        self.room_filter.clear()
        from ui.i18n import t
        self.room_filter.addItem(t("history.all_rooms"), "")
        for rid, uname in sorted(rooms.items(), key=lambda x: str(x[1]).lower()):
            self.room_filter.addItem(f"{uname} ({rid})", rid)
        idx = self.room_filter.findData(current or "")
        self.room_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.room_filter.blockSignals(False)

    def refresh(self):
        from ui.i18n import t

        all_rows = read_recent(limit=300)
        self._rebuild_room_filter(all_rows)
        # Only the global save_dir volume; not per-history paths.
        self._update_disk_banner()
        room_id = self._selected_room_id()
        status = self._selected_status()
        text = self.search_input.text()
        rows = read_recent(limit=200, room_id=room_id)
        filtered = []
        for row in rows:
            if not history_row_matches(row, text):
                continue
            health = str(row.get("health") or "").lower()
            st = str(row.get("status") or "").lower()
            failed = health in ("failed", "red") or "fail" in st or "error" in st
            if status == "failed" and not failed:
                continue
            if status == "ok" and failed:
                continue
            filtered.append(row)

        while self.body_layout.count():
            item = self.body_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        if not filtered:
            empty = QFrame()
            empty.setObjectName("aboutCard")
            enable_surface(empty)
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(28, 36, 28, 36)
            empty_title = QLabel(t("history.empty"))
            empty_title.setProperty("role", "title")
            empty_title.setAlignment(Qt.AlignCenter)
            empty_title.setWordWrap(True)
            empty_layout.addWidget(empty_title)
            self.body_layout.addWidget(empty)
            self.body_layout.addStretch(1)
            self.status.setText(t("history.count", count=0))
            return

        for day, items in group_by_date(filtered):
            day_lbl = QLabel(day)
            day_lbl.setProperty("role", "title")
            day_lbl.setStyleSheet("font-size:15px; font-weight:600;")
            self.body_layout.addWidget(day_lbl)
            for row in items:
                self.body_layout.addWidget(self._make_row(row))
        self.body_layout.addStretch(1)
        self.status.setText(t("history.count", count=len(filtered)))

    def _make_row(self, row: dict) -> QFrame:
        from ui.i18n import t

        card = QFrame()
        card.setObjectName("aboutCard")
        enable_surface(card)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(8)

        top = QHBoxLayout()
        uname = row.get("uname") or row.get("room_id") or "?"
        title = row.get("title") or ""
        name = QLabel(f"{uname}  ·  {title}" if title else str(uname))
        name.setProperty("role", "itemTitle")
        name.setWordWrap(True)
        top.addWidget(name, 1)
        health = str(row.get("health") or "")
        if health:
            badge = QLabel(health)
            badge.setProperty("statusTone", {
                "healthy": "success",
                "degraded": "warning",
                "failed": "danger",
            }.get(health, "neutral"))
            top.addWidget(badge)
        layout.addLayout(top)

        path = str(row.get("path") or "")
        missing = bool(path) and not os.path.exists(path)
        meta = QLabel(
            f"{row.get('time', '')}  ·  "
            f"{_format_duration(row.get('duration') or 0)}  ·  "
            f"{format_size(int(row.get('size') or 0))}  ·  "
            f"room {row.get('room_id', '')}"
            + (f"  ·  {t('history.missing_file')}" if missing else "")
        )
        meta.setProperty("role", "muted")
        meta.setWordWrap(True)
        layout.addWidget(meta)

        path_lbl = QLabel(path or t("history.no_path"))
        path_lbl.setProperty("role", "secondary")
        path_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        path_lbl.setWordWrap(True)
        layout.addWidget(path_lbl)

        reason = str(row.get("close_reason") or "")
        if reason:
            reason_lbl = QLabel(t("history.reason", reason=reason))
            reason_lbl.setProperty("role", "muted")
            reason_lbl.setWordWrap(True)
            layout.addWidget(reason_lbl)

        btns = QHBoxLayout()
        btns.setSpacing(8)
        open_btn = QPushButton(t("history.open_folder"))
        copy_btn = QPushButton(t("history.copy_path"))
        for b in (open_btn, copy_btn):
            b.setProperty("variant", "secondary")
            _fit_button(b, height=34, min_width=96)
        open_btn.clicked.connect(lambda checked=False, p=path: self._open_folder(p))
        copy_btn.clicked.connect(lambda checked=False, p=path: self._copy_path(p))
        open_btn.setEnabled(bool(path))
        copy_btn.setEnabled(bool(path))
        btns.addWidget(open_btn)
        btns.addWidget(copy_btn)
        btns.addStretch()
        layout.addLayout(btns)
        return card

    def _open_folder(self, path: str):
        if not path:
            return
        folder = path if os.path.isdir(path) else os.path.dirname(path)
        if not folder:
            return
        if sys.platform == "win32" and os.path.exists(path) and not os.path.isdir(path):
            try:
                import subprocess
                subprocess.Popen(["explorer", f"/select,{os.path.normpath(path)}"])
                return
            except Exception:
                pass
        if not os.path.isdir(folder):
            from ui.i18n import t
            self.status.setText(t("history.missing_file"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def _copy_path(self, path: str):
        if not path:
            return
        QGuiApplication.clipboard().setText(path)
        from ui.i18n import t
        self.status.setText(t("history.copied"))


# Backward-compatible alias used by earlier about-page embedding.
RecentRecordingsPanel = RecentRecordingsPage
