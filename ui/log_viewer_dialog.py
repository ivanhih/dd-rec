from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui.theme import enable_surface, repolish


DEFAULT_MAX_LOG_BYTES = 2 * 1024 * 1024
AUTO_REFRESH_MS = 30_000

# 标准 logging 行头：2026-07-18 16:03:42 [INFO] ...
_LOG_LINE_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\s+\[(?P<level>[A-Z]+)\]"
)

FILTER_ALL = "ALL"
FILTER_INFO = "INFO"
FILTER_WARNING = "WARNING"
FILTER_ERROR = "ERROR"
FILTER_CRASH = "CRASH"

_LEVEL_MATCHERS = {
    FILTER_INFO: frozenset({"INFO"}),
    FILTER_WARNING: frozenset({"WARNING", "WARN"}),
    FILTER_ERROR: frozenset({"ERROR", "CRITICAL", "FATAL"}),
}


def read_log_tail(log_path: str, max_bytes: int = DEFAULT_MAX_LOG_BYTES) -> tuple[str, bool]:
    """读取日志尾部，返回 (文本, 是否截断)，避免大日志阻塞界面。"""
    path = Path(log_path)
    if not path.is_file():
        return "日志文件尚未生成。", False

    max_bytes = max(4096, int(max_bytes))
    size = path.stat().st_size
    truncated = size > max_bytes
    with path.open("rb") as f:
        if truncated:
            f.seek(-max_bytes, os.SEEK_END)
        data = f.read()

    if truncated:
        # 跳过从文件中间开始时读到的第一段残缺行。
        newline = data.find(b"\n")
        if newline >= 0:
            data = data[newline + 1:]
    return data.decode("utf-8", errors="replace"), truncated


def filter_log_lines(text: str, level: str = FILTER_ALL) -> str:
    """按日志级别过滤。非标准行（堆栈等）跟在匹配行后一并保留。"""
    if not text:
        return text
    wanted = _LEVEL_MATCHERS.get(level)
    if wanted is None:
        return text

    out: list[str] = []
    keep_continuation = False
    for line in text.splitlines():
        m = _LOG_LINE_RE.match(line)
        if m:
            keep_continuation = m.group("level") in wanted
            if keep_continuation:
                out.append(line)
            continue
        if keep_continuation:
            out.append(line)
    return "\n".join(out)


class LogViewerPanel(QWidget):
    """可嵌入的日志查看面板：级别筛选 + 搜索 + 尾部读取。"""

    def __init__(
        self,
        app_log_path: str,
        crash_log_path: Optional[str] = None,
        parent=None,
        *,
        on_close=None,
    ):
        super().__init__(parent)
        from ui.i18n import t

        self.app_log_path = os.path.abspath(app_log_path)
        self.crash_log_path = os.path.abspath(crash_log_path) if crash_log_path else ""
        self._on_close = on_close
        self._filter = FILTER_ALL
        self._raw_text = ""
        self._truncated = False
        self._filter_buttons: dict[str, QPushButton] = {}
        self._auto_refresh = QTimer(self)
        self._auto_refresh.setInterval(AUTO_REFRESH_MS)
        self._auto_refresh.timeout.connect(self.refresh)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        path_row = QHBoxLayout()
        path_row.setSpacing(8)
        self.path_label = QLabel(self.app_log_path)
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.path_label.setWordWrap(True)
        self.path_label.setProperty("role", "secondary")
        self.path_label.setStyleSheet("font-size: 12px;")
        path_row.addWidget(self.path_label, 1)
        root.addLayout(path_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self._filter_group = QButtonGroup(self)
        self._filter_group.setExclusive(True)
        # filterTone 驱动 theme QSS 着色（info 蓝 / warning 黄 / error 红 / crash 紫）
        filter_specs = (
            (FILTER_ALL, "log.filter_all", "all"),
            (FILTER_INFO, "log.filter_info", "info"),
            (FILTER_WARNING, "log.filter_warning", "warning"),
            (FILTER_ERROR, "log.filter_error", "error"),
            (FILTER_CRASH, "log.filter_crash", "crash"),
        )
        for key, label_key, tone in filter_specs:
            if key == FILTER_CRASH and not self.crash_log_path:
                continue
            btn = QPushButton(t(label_key))
            btn.setCheckable(True)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(32)
            btn.setProperty("variant", "channelChoice")
            btn.setProperty("filterKey", key)
            btn.setProperty("filterTone", tone)
            # 让文案完整显示：WARNING / 查找下一个 等不能被挤扁
            btn.setMinimumWidth(max(72, btn.fontMetrics().horizontalAdvance(btn.text()) + 28))
            repolish(btn)
            self._filter_group.addButton(btn)
            self._filter_buttons[key] = btn
            filter_row.addWidget(btn)
        filter_row.addStretch()
        root.addLayout(filter_row)

        if FILTER_ALL in self._filter_buttons:
            self._filter_buttons[FILTER_ALL].setChecked(True)
        self._filter_group.buttonClicked.connect(self._on_filter_clicked)

        search_row = QHBoxLayout()
        search_row.setSpacing(8)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText(t("log.search_placeholder"))
        self.search_input.setFixedHeight(36)
        self.search_input.returnPressed.connect(self.find_next)
        find_btn = QPushButton(t("log.find_next"))
        find_btn.setCursor(Qt.PointingHandCursor)
        find_btn.setProperty("variant", "secondary")
        find_btn.setProperty("settingsCompact", True)
        find_btn.setFixedHeight(36)
        find_btn.setMinimumWidth(max(96, find_btn.fontMetrics().horizontalAdvance(find_btn.text()) + 28))
        repolish(find_btn)
        find_btn.clicked.connect(self.find_next)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(find_btn)
        root.addLayout(search_row)

        self.viewer = QPlainTextEdit()
        self.viewer.setObjectName("logTextView")
        self.viewer.setReadOnly(True)
        self.viewer.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.viewer.setFont(QFont("Consolas", 10))
        root.addWidget(self.viewer, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.status_label = QLabel("")
        self.status_label.setProperty("role", "muted")
        self.status_label.setStyleSheet("font-size: 11px;")
        refresh_btn = QPushButton(t("log.refresh"))
        copy_btn = QPushButton(t("log.copy_all"))
        close_btn = QPushButton(t("log.close"))
        for btn in (refresh_btn, copy_btn, close_btn):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFixedHeight(36)
            btn.setProperty("variant", "secondary")
            btn.setProperty("settingsCompact", True)
            btn.setMinimumWidth(max(80, btn.fontMetrics().horizontalAdvance(btn.text()) + 28))
            repolish(btn)
        refresh_btn.clicked.connect(self.refresh)
        copy_btn.clicked.connect(self.copy_all)
        close_btn.clicked.connect(self._close)
        bottom.addWidget(self.status_label, 1)
        bottom.addWidget(refresh_btn)
        bottom.addWidget(copy_btn)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        self.refresh()
        self._auto_refresh.start()

    def current_log_path(self) -> str:
        if self._filter == FILTER_CRASH and self.crash_log_path:
            return self.crash_log_path
        return self.app_log_path

    def _on_filter_clicked(self, button: QPushButton) -> None:
        key = button.property("filterKey") or FILTER_ALL
        if key == self._filter:
            self._apply_filter()
            return
        prev = self._filter
        self._filter = key
        # 崩溃日志是另一文件；级别筛选共用 bilirec.log 缓存
        if key == FILTER_CRASH or prev == FILTER_CRASH:
            self.refresh()
        else:
            self._apply_filter()

    def refresh(self) -> None:
        from ui.i18n import t

        path = self.current_log_path()
        self.path_label.setText(path)
        try:
            text, truncated = read_log_tail(path)
            # 内容没变就别重设文本，避免自动刷新把滚动条/选区打乱
            if text == self._raw_text and truncated == self._truncated:
                return
            self._raw_text = text
            self._truncated = truncated
            self._apply_filter()
        except OSError as exc:
            self._raw_text = ""
            self._truncated = False
            self.viewer.setPlainText(f"{t('log.status_failed')}: {exc}")
            self.status_label.setText(t("log.status_failed"))

    def _apply_filter(self) -> None:
        from ui.i18n import t

        if self._filter == FILTER_CRASH or self._filter == FILTER_ALL:
            shown = self._raw_text
            level_label = t("log.filter_crash") if self._filter == FILTER_CRASH else t("log.filter_all")
        else:
            shown = filter_log_lines(self._raw_text, self._filter)
            level_label = {
                FILTER_INFO: t("log.filter_info"),
                FILTER_WARNING: t("log.filter_warning"),
                FILTER_ERROR: t("log.filter_error"),
            }.get(self._filter, self._filter)

        # 靠近底部时刷新后继续贴底；用户往上翻时尽量保住当前位置
        bar = self.viewer.verticalScrollBar()
        stick_bottom = bar.value() >= max(0, bar.maximum() - 24)
        old_value = bar.value()
        self.viewer.setPlainText(shown)
        if stick_bottom:
            self.viewer.moveCursor(QTextCursor.End)
        else:
            bar.setValue(min(old_value, bar.maximum()))
        count = len(shown.splitlines()) if shown else 0
        if self._truncated:
            self.status_label.setText(
                t("log.status_filtered_truncated", level=level_label, count=count)
            )
        else:
            self.status_label.setText(
                t("log.status_filtered", level=level_label, count=count)
            )

    def find_next(self) -> None:
        keyword = self.search_input.text().strip()
        if not keyword:
            return
        if self.viewer.find(keyword):
            return
        cursor = self.viewer.textCursor()
        cursor.movePosition(QTextCursor.Start)
        self.viewer.setTextCursor(cursor)
        self.viewer.find(keyword)

    def copy_all(self) -> None:
        from ui.i18n import t

        QApplication.clipboard().setText(self.viewer.toPlainText())
        self.status_label.setText(t("common.success"))

    def stop_auto_refresh(self) -> None:
        self._auto_refresh.stop()

    def _close(self) -> None:
        self.stop_auto_refresh()
        if callable(self._on_close):
            self._on_close()


def open_log_viewer_overlay(
    main_window,
    app_log_path: str,
    crash_log_path: Optional[str] = None,
):
    """主窗口内日志查看 overlay（半透明遮罩 + 居中面板）。"""
    from ui.i18n import t

    overlay = QWidget(main_window)
    overlay.setObjectName("modalOverlay")
    overlay.resize(main_window.size())
    overlay.move(0, 0)
    overlay.show()
    overlay.raise_()

    panel_w = min(main_window.width() - 80, 1100)
    panel_h = min(main_window.height() - 60, 720)
    panel = QFrame(main_window)
    panel.setObjectName("logViewerPanel")
    enable_surface(panel)
    panel.setFixedSize(panel_w, panel_h)
    panel.move(
        (main_window.width() - panel_w) // 2,
        (main_window.height() - panel_h) // 2,
    )
    panel.show()
    panel.raise_()

    outer = QVBoxLayout(panel)
    outer.setContentsMargins(0, 0, 0, 0)
    outer.setSpacing(0)

    top_bar = QWidget()
    top_bar.setObjectName("logViewerTopBar")
    top_bar.setFixedHeight(56)
    top_layout = QHBoxLayout(top_bar)
    top_layout.setContentsMargins(24, 0, 16, 0)
    top_layout.setSpacing(10)

    title_lbl = QLabel(t("log.title"))
    title_lbl.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    title_lbl.setProperty("role", "title")

    close_btn = QPushButton("×")
    close_btn.setFixedSize(34, 34)
    close_btn.setCursor(Qt.PointingHandCursor)
    close_btn.setToolTip(t("common.close"))
    close_btn.setProperty("variant", "close")
    close_btn.setFlat(False)
    repolish(close_btn)

    top_layout.addWidget(title_lbl)
    top_layout.addStretch()
    top_layout.addWidget(close_btn)
    outer.addWidget(top_bar)

    def _close():
        panel_view.stop_auto_refresh()
        overlay.hide()
        overlay.deleteLater()
        panel.hide()
        panel.deleteLater()

    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(24, 16, 24, 20)
    body_layout.setSpacing(0)
    panel_view = LogViewerPanel(
        app_log_path,
        crash_log_path=crash_log_path,
        parent=body,
        on_close=_close,
    )
    body_layout.addWidget(panel_view)
    outer.addWidget(body, 1)

    close_btn.clicked.connect(_close)
    overlay.mousePressEvent = lambda e: _close()
    return panel_view
