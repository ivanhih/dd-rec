from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QFont, QTextCursor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from core.help_docs import (
    docs_wiki_dir,
    list_sections,
    missing_docs_html,
    render_section_html,
    resolve_section,
    section_id_from_href,
)
from ui.theme import enable_surface, repolish, theme_manager


GITHUB_WIKI_URL = "https://github.com/ivanhih/dd-rec/wiki"


def _fit_button(button: QPushButton, *, height: int = 34, min_width: int = 88) -> QPushButton:
    button.setCursor(Qt.PointingHandCursor)
    button.setFixedHeight(height)
    button.setProperty("settingsCompact", True)
    text_w = button.fontMetrics().horizontalAdvance(button.text())
    button.setMinimumWidth(max(min_width, text_w + 28))
    repolish(button)
    return button


class HelpViewerPanel(QWidget):
    """Left TOC + right Markdown HTML browser."""

    def __init__(self, parent=None, *, section_id: Optional[str] = None, on_close=None):
        super().__init__(parent)
        from ui.i18n import t

        self._on_close = on_close
        self._sections = list_sections()
        self._current_id = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(12)

        body = QHBoxLayout()
        body.setSpacing(14)

        self.toc = QListWidget()
        self.toc.setObjectName("helpTocList")
        self.toc.setFixedWidth(200)
        self.toc.setSpacing(2)
        for sec in self._sections:
            item = QListWidgetItem(t(sec.i18n_key))
            item.setData(Qt.UserRole, sec.id)
            self.toc.addItem(item)
        if not self._sections:
            placeholder = QListWidgetItem(t("help.missing_toc"))
            placeholder.setFlags(Qt.NoItemFlags)
            self.toc.addItem(placeholder)
        self.toc.currentItemChanged.connect(self._on_toc_changed)
        body.addWidget(self.toc)

        self.browser = QTextBrowser()
        self.browser.setObjectName("helpTextBrowser")
        self.browser.setOpenExternalLinks(False)
        self.browser.setOpenLinks(False)
        self.browser.anchorClicked.connect(self._on_anchor_clicked)
        body.addWidget(self.browser, 1)
        root.addLayout(body, 1)

        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.status = QLabel("")
        self.status.setProperty("role", "muted")
        self.status.setWordWrap(True)

        open_dir_btn = QPushButton(t("help.open_docs_dir"))
        open_dir_btn.setProperty("variant", "secondary")
        _fit_button(open_dir_btn, min_width=110)
        open_dir_btn.clicked.connect(self._open_docs_dir)

        wiki_btn = QPushButton(t("help.open_wiki"))
        wiki_btn.setProperty("variant", "secondary")
        _fit_button(wiki_btn, min_width=110)
        wiki_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(GITHUB_WIKI_URL)))

        close_btn = QPushButton(t("help.close"))
        close_btn.setProperty("variant", "secondary")
        _fit_button(close_btn, min_width=80)
        close_btn.clicked.connect(self._close)

        bottom.addWidget(self.status, 1)
        bottom.addWidget(open_dir_btn)
        bottom.addWidget(wiki_btn)
        bottom.addWidget(close_btn)
        root.addLayout(bottom)

        # theme changes: re-render current page
        try:
            theme_manager().changed.connect(self._rerender_current)
        except Exception:
            pass

        self.show_section(section_id)

    def show_section(self, section_id: Optional[str] = None):
        from ui.i18n import t

        if not self._sections:
            dark = theme_manager().current_name != "light"
            self.browser.setHtml(missing_docs_html(dark=dark))
            self.status.setText(t("help.missing"))
            return

        sec = resolve_section(section_id)
        if sec is None:
            sec = self._sections[0]
        # select TOC row
        for i in range(self.toc.count()):
            item = self.toc.item(i)
            if item and item.data(Qt.UserRole) == sec.id:
                self.toc.blockSignals(True)
                self.toc.setCurrentItem(item)
                self.toc.blockSignals(False)
                break
        self._load_section(sec)

    def _load_section(self, sec):
        from ui.i18n import t

        dark = theme_manager().current_name != "light"
        try:
            html = render_section_html(sec, dark=dark)
            self.browser.setHtml(html)
            self._current_id = sec.id
            self.status.setText(t("help.status_loaded", name=t(sec.i18n_key)))
            cursor = self.browser.textCursor()
            cursor.movePosition(QTextCursor.Start)
            self.browser.setTextCursor(cursor)
        except OSError as exc:
            self.browser.setHtml(missing_docs_html(dark=dark))
            self.status.setText(t("help.missing_detail", error=exc))

    def _on_toc_changed(self, current: QListWidgetItem, _previous: QListWidgetItem):
        if current is None:
            return
        sid = current.data(Qt.UserRole)
        if not sid or sid == self._current_id:
            return
        sec = resolve_section(sid)
        if sec:
            self._load_section(sec)

    def _on_anchor_clicked(self, url: QUrl):
        href = url.toString()
        # Internal section jump: #section:quickstart or section:quickstart
        if href.startswith("#section:"):
            self.show_section(href.split(":", 1)[1])
            return
        if href.startswith("section:"):
            self.show_section(href.split(":", 1)[1])
            return
        sid = section_id_from_href(href)
        if sid:
            self.show_section(sid)
            return
        # External / absolute
        if href.startswith(("http://", "https://", "mailto:")):
            QDesktopServices.openUrl(url)

    def _rerender_current(self, *_args):
        if self._current_id:
            self.show_section(self._current_id)

    def _open_docs_dir(self):
        path = docs_wiki_dir()
        if not os.path.isdir(path):
            from ui.i18n import t
            self.status.setText(t("help.missing"))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _close(self):
        if callable(self._on_close):
            self._on_close()


def open_help_overlay(main_window, section_id: Optional[str] = None):
    """Open local help as a main-window overlay."""
    from ui.i18n import t

    overlay = QWidget(main_window)
    overlay.setObjectName("modalOverlay")
    overlay.resize(main_window.size())
    overlay.move(0, 0)
    overlay.show()
    overlay.raise_()

    panel_w = min(main_window.width() - 64, 1180)
    panel_h = min(main_window.height() - 48, 780)
    panel = QFrame(main_window)
    panel.setObjectName("helpViewerPanel")
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
    top_bar.setObjectName("helpViewerTopBar")
    top_bar.setFixedHeight(56)
    top_layout = QHBoxLayout(top_bar)
    top_layout.setContentsMargins(24, 0, 16, 0)
    top_layout.setSpacing(10)

    title_lbl = QLabel(t("help.title"))
    title_lbl.setFont(QFont("Microsoft YaHei UI", 14, QFont.Bold))
    title_lbl.setProperty("role", "title")

    close_btn = QPushButton("×")
    close_btn.setFixedSize(34, 34)
    close_btn.setCursor(Qt.PointingHandCursor)
    close_btn.setToolTip(t("common.close"))
    close_btn.setProperty("variant", "close")
    repolish(close_btn)

    top_layout.addWidget(title_lbl)
    top_layout.addStretch()
    top_layout.addWidget(close_btn)
    outer.addWidget(top_bar)

    def _close():
        overlay.hide()
        overlay.deleteLater()
        panel.hide()
        panel.deleteLater()

    body = QWidget()
    body_layout = QVBoxLayout(body)
    body_layout.setContentsMargins(20, 14, 20, 16)
    body_layout.setSpacing(0)
    panel_view = HelpViewerPanel(body, section_id=section_id, on_close=_close)
    body_layout.addWidget(panel_view)
    outer.addWidget(body, 1)

    close_btn.clicked.connect(_close)
    # Click scrim to close, but not the panel itself
    overlay.mousePressEvent = lambda e: _close()
    return panel_view
