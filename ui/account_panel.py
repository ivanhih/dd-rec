"""Optional B站 account controls. All widget updates run on the Qt thread."""
from __future__ import annotations

import time

import qrcode
from PySide6.QtCore import QEvent, QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot, QSize, QRectF
from PySide6.QtGui import QColor, QCloseEvent, QIcon, QImage, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QToolButton, QVBoxLayout, QWidget

from core.bili_auth import AuthError, account_service
from ui.i18n import language_manager, t


class _Task(QRunnable):
    def __init__(self, runner, token, work):
        super().__init__()
        self.runner, self.token, self.work = runner, token, work

    def run(self):
        result, error = None, ""
        try:
            result = self.work()
        except AuthError as exc:
            error = str(exc)
        except Exception:
            error = "response"
        try:
            self.runner.completed.emit(self.token, result, error)
        except RuntimeError:
            pass  # The owner was closed while a bounded request was in flight.


class AsyncRunner(QObject):
    completed = Signal(int, object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._callbacks = {}
        self._token = 0
        self.completed.connect(self._complete, Qt.QueuedConnection)

    def submit(self, work, callback):
        self._token += 1
        self._callbacks[self._token] = callback
        QThreadPool.globalInstance().start(_Task(self, self._token, work))

    def cancel(self):
        self._callbacks.clear()

    @Slot(int, object, str)
    def _complete(self, token, result, error):
        callback = self._callbacks.pop(token, None)
        if callback is not None:
            callback(result, error)


class AccountMonitor(QObject):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.runner = AsyncRunner(self)
        self._busy = False
        self._revision = None
        self.timer = QTimer(self)
        self.timer.setInterval(60 * 1000)
        self.timer.timeout.connect(self.check)

    def start(self):
        self.timer.start()
        self.check()

    def check(self):
        snapshot = account_service().store.snapshot()
        revision = snapshot.get("revision")
        if revision != self._revision:
            self._revision = revision
            self.changed.emit()
        if self._busy or not snapshot:
            return
        self._busy = True
        self.runner.submit(account_service().maintain, self._checked)

    def _checked(self, _result, _error):
        self._busy = False
        self.changed.emit()


class SidebarAccountButton(QToolButton):
    """Account shortcut with a nonblocking avatar and stale-result protection."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("sidebarAccount")
        self.setFixedSize(56, 56)
        self.setCursor(Qt.PointingHandCursor)
        self.runner = AsyncRunner(self)
        self._tip = None
        self._avatar_key = None
        self._avatar_pixmap = None
        self._avatar_pending = False
        self._retry_at = 0
        self.pressed.connect(lambda: self._tip.hide() if self._tip is not None else None)
        language_manager().changed.connect(self.refresh)
        self.refresh()

    def attach_tip(self, tip):
        self._tip = tip
        self.refresh()

    def enterEvent(self, event):
        if self._tip is not None:
            self._tip.show()
        super().enterEvent(event)

    def leaveEvent(self, event):
        if self._tip is not None:
            self._tip.hide()
        super().leaveEvent(event)

    @staticmethod
    def _default_icon():
        image = QPixmap(48, 48)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#00A1D6"))
        painter.drawEllipse(QRectF(16, 5, 16, 16))
        painter.drawRoundedRect(QRectF(7, 25, 34, 19), 10, 10)
        painter.end()
        return QIcon(image)

    @staticmethod
    def _avatar_icon(pixmap):
        image = QPixmap(88, 88)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, 88, 88), 18, 18)
        painter.setClipPath(path)
        scaled = pixmap.scaled(88, 88, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        painter.drawPixmap((88 - scaled.width()) // 2, (88 - scaled.height()) // 2, scaled)
        painter.end()
        return QIcon(image)

    def refresh(self, *_args):
        account = account_service().store.snapshot()
        active = account.get("status") == "active"
        key = (account.get("uid"), account.get("face", "")) if active else None
        if key != self._avatar_key:
            self.runner.cancel()
            self._avatar_key = key
            self._avatar_pixmap = None
            self._avatar_pending = False
            self._retry_at = 0
        self.setText("" if active else t("account.sidebar_login"))
        self.setToolButtonStyle(Qt.ToolButtonIconOnly if active else Qt.ToolButtonTextUnderIcon)
        self.setIconSize(QSize(44, 44) if active else QSize(28, 28))
        self.setIcon(self._avatar_icon(self._avatar_pixmap) if self._avatar_pixmap is not None else self._default_icon())
        label = t("account.sidebar_profile", name=account.get("uname") or f"UID: {account.get('uid', '')}") if active else t("account.relogin" if account else "account.login")
        self.setAccessibleName(label)
        if self._tip is not None:
            self._tip.setText(label)
        if key and key[1] and self._avatar_pixmap is None and not self._avatar_pending and time.monotonic() >= self._retry_at:
            self._avatar_pending = True
            self.runner.submit(lambda: account_service().client.avatar(key[1]),
                               lambda result, error: self._avatar_loaded(key, result, error))

    def _avatar_loaded(self, key, result, error):
        if key != self._avatar_key:
            return
        self._avatar_pending = False
        pixmap = QPixmap()
        if not error and pixmap.loadFromData(result):
            self._avatar_pixmap = pixmap
        else:
            self._retry_at = time.monotonic() + 60
        self.refresh()


def qr_pixmap(url: str) -> QPixmap:
    """Render QR modules with Qt, including a quiet zone, without Pillow."""
    qr = qrcode.QRCode(border=4, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()
    size = len(matrix)
    image = QImage(size, size, QImage.Format_RGB32)
    image.fill(Qt.white)
    for row, cells in enumerate(matrix):
        for column, black in enumerate(cells):
            if black:
                image.setPixelColor(column, row, Qt.black)
    # Integer scaling keeps every module sharp and equally sized.
    scale = max(1, 256 // size)
    return QPixmap.fromImage(image).scaled(size * scale, size * scale, Qt.KeepAspectRatio, Qt.FastTransformation)


class QRLoginOverlay(QWidget):
    finished = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("modalOverlay")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.StrongFocus)
        self._root = parent
        self.runner = AsyncRunner(self)
        self._generation = 0
        self._key = ""
        self._deadline = 0
        self._busy = False
        self._status = "generating"
        self.account = None
        self._finished = False
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(24, 24, 24, 24)
        root_layout.addStretch(1)
        center = QHBoxLayout()
        center.addStretch(1)
        self.panel = QFrame(self)
        self.panel.setObjectName("settingsOverlayPanel")
        panel_layout = QVBoxLayout(self.panel)
        panel_layout.setContentsMargins(24, 20, 24, 24)
        panel_layout.setSpacing(14)
        title_row = QHBoxLayout()
        self.title = QLabel()
        self.title.setProperty("role", "title")
        self.title.setStyleSheet("font-size: 17px; font-weight: 600;")
        self.close_button = QPushButton("×")
        self.close_button.setFixedSize(34, 34)
        self.close_button.setProperty("variant", "close")
        self.close_button.clicked.connect(self.reject)
        title_row.addWidget(self.title, 1)
        title_row.addWidget(self.close_button)
        panel_layout.addLayout(title_row)
        self.purpose = QLabel()
        self.purpose.setWordWrap(True)
        self.purpose.setTextFormat(Qt.PlainText)
        panel_layout.addWidget(self.purpose)
        privacy_card = QFrame()
        privacy_card.setObjectName("settingsCard")
        privacy_layout = QVBoxLayout(privacy_card)
        privacy_layout.setContentsMargins(12, 12, 12, 12)
        privacy_layout.setSpacing(6)
        self.privacy_title = QLabel()
        self.privacy_title.setProperty("role", "itemTitle")
        self.privacy = QLabel()
        self.privacy.setWordWrap(True)
        self.privacy.setTextFormat(Qt.PlainText)
        self.privacy.setProperty("role", "itemDesc")
        privacy_layout.addWidget(self.privacy_title)
        privacy_layout.addWidget(self.privacy)
        panel_layout.addWidget(privacy_card)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        panel_layout.addWidget(self.hint)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(280, 280)
        self.image.setStyleSheet("background: white; border-radius: 8px;")
        panel_layout.addWidget(self.image)
        self.status = QLabel()
        self.status.setAlignment(Qt.AlignCenter)
        self.status.setWordWrap(True)
        panel_layout.addWidget(self.status)
        row = QHBoxLayout()
        self.refresh_button = QPushButton()
        self.refresh_button.setProperty("variant", "secondary")
        self.refresh_button.clicked.connect(self.generate)
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self.reject)
        row.addWidget(self.refresh_button)
        row.addWidget(self.cancel_button)
        panel_layout.addLayout(row)
        self.scroll = QScrollArea(self)
        self.scroll.setObjectName("qrLoginOverlayScroll")
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setMinimumSize(0, 0)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.panel)
        self.scroll.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
        self.scroll.viewport().setStyleSheet("background: transparent;")
        center.addWidget(self.scroll)
        center.addStretch(1)
        root_layout.addLayout(center)
        root_layout.addStretch(1)
        self.panel.setFixedWidth(420)
        self.panel.adjustSize()
        self.panel.setFixedHeight(self.panel.sizeHint().height())
        self.adjustSize()
        if self._root is not None:
            self.setGeometry(self._root.rect())
            self._root.installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.setInterval(2000)
        self.timer.timeout.connect(self.poll)
        self.finished.connect(self._closed)
        language_manager().changed.connect(self.retranslate)
        self.retranslate()
        self.generate()

    def retranslate(self, *_args):
        self.title.setText(t("account.login"))
        self.purpose.setText(t("account.login_purpose"))
        self.privacy_title.setText(t("account.local_storage_title"))
        self.privacy.setText(t("account.local_storage_notice"))
        self.hint.setText(t("account.scan_hint"))
        self.refresh_button.setText(t("account.refresh_qr"))
        self.cancel_button.setText(t("common.cancel"))
        self.status.setText(t(f"account.{self._status}"))
        self.panel.adjustSize()
        self.panel.setMinimumHeight(0)
        self.panel.setMaximumHeight(16777215)
        self.panel.adjustSize()
        self.panel.setFixedHeight(self.panel.sizeHint().height())
        self._center_panel()

    def _center_panel(self):
        if self._root is None:
            return
        self.setGeometry(self._root.rect())
        available_width = max(1, self.width() - 48)
        available_height = max(1, self.height() - 48)
        panel_width = min(420, max(328, available_width))
        if panel_width != self.panel.width():
            self.panel.setFixedWidth(panel_width)
            self.panel.setMinimumHeight(0)
            self.panel.setMaximumHeight(16777215)
            self.panel.adjustSize()
            self.panel.setFixedHeight(self.panel.sizeHint().height())
        vertical_bar_width = self.scroll.verticalScrollBar().sizeHint().width()
        scroll_width = min(available_width, self.panel.width() + vertical_bar_width)
        self.scroll.setMinimumSize(scroll_width,
                                   min(self.panel.height(), available_height))
        self.scroll.setMaximumSize(available_width, available_height)
        self.scroll.updateGeometry()
        self.layout().activate()

    def eventFilter(self, watched, event):
        if watched is self._root and event.type() == QEvent.Resize:
            self._center_panel()
        return super().eventFilter(watched, event)

    def open(self):
        self._center_panel()
        self.show()
        self.raise_()
        self.setFocus(Qt.ActiveWindowFocusReason)

    def mousePressEvent(self, event):
        if not self.scroll.geometry().contains(event.position().toPoint()):
            self.reject()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent):
        self.reject()
        event.accept()

    def accept(self):
        self._finish(QDialog.Accepted)

    def reject(self):
        self._finish(QDialog.Rejected)

    def _finish(self, result):
        if self._finished:
            return
        self._finished = True
        self.hide()
        self.finished.emit(result)

    def _closed(self, _result):
        self._generation += 1
        self._key = ""
        self.timer.stop()
        self.runner.cancel()


    def generate(self):
        self.timer.stop()
        self._generation += 1
        generation = self._generation
        self._key = ""
        self._busy = True
        self.image.clear()
        self._status = "generating"
        self.refresh_button.setEnabled(False)
        self.retranslate()
        self.runner.submit(account_service().client.generate,
                           lambda result, error: self._generated(generation, result, error))

    def _generated(self, generation, result, error):
        if generation != self._generation:
            return
        self._busy = False
        self.refresh_button.setEnabled(True)
        if error:
            self._status = "network" if error == "network" else "response"
        else:
            self._key = result["key"]
            self._deadline = time.monotonic() + 180
            self.image.setPixmap(qr_pixmap(result["url"]))
            self._status = "pending"
            self.timer.start()
        self.retranslate()

    def poll(self):
        if time.monotonic() >= self._deadline:
            self.timer.stop()
            self._status = "expired_qr"
            self.image.clear()
            self.retranslate()
            return
        if self._busy or not self._key:
            return
        self._busy = True
        key, generation = self._key, self._generation
        self.runner.submit(lambda: account_service().client.poll(key),
                           lambda result, error: self._polled(generation, result, error))

    def _polled(self, generation, result, error):
        if generation != self._generation:
            return
        self._busy = False
        if error:
            self._status = "network" if error == "network" else "response"
        elif result["status"] == "success":
            # Only a live dialog can return an account for the owner to save.
            self.account = result["account"]
            self.accept()
            return
        elif result["status"] == "expired":
            self.timer.stop()
            self.image.clear()
            self._status = "expired_qr"
        else:
            self._status = result["status"]
        self.retranslate()


# Compatibility alias for code that imported the original popup class.
QRLoginDialog = QRLoginOverlay


class AccountPanel(QWidget):
    saved = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.runner = AsyncRunner(self)
        self._busy = False
        self._dialog = None
        self._message = ""
        self._avatar_url = ""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        profile = QHBoxLayout()
        self.avatar = QLabel("👤")
        self.avatar.setAlignment(Qt.AlignCenter)
        self.avatar.setFixedSize(48, 48)
        profile.addWidget(self.avatar)
        text = QVBoxLayout()
        self.name = QLabel()
        self.name.setProperty("role", "itemTitle")
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.status.setProperty("role", "muted")
        text.addWidget(self.name)
        text.addWidget(self.status)
        profile.addLayout(text, 1)
        layout.addLayout(profile)
        self.description = QLabel()
        self.description.setWordWrap(True)
        self.description.setProperty("role", "itemDesc")
        layout.addWidget(self.description)
        buttons = QHBoxLayout()
        self.login_button = QPushButton()
        self.login_button.setProperty("variant", "primary")
        self.login_button.clicked.connect(self.login)
        self.check_button = QPushButton()
        self.check_button.clicked.connect(self.check)
        self.logout_button = QPushButton()
        self.logout_button.clicked.connect(self.logout)
        for button in (self.login_button, self.check_button, self.logout_button):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.apply_button = QPushButton()
        self.apply_button.clicked.connect(self.apply_all)
        layout.addWidget(self.apply_button)
        language_manager().changed.connect(self.refresh)
        monitor = getattr(self.window(), "_account_monitor", None)
        if monitor is not None:
            monitor.changed.connect(self.refresh)
        self.refresh()

    def refresh(self, *_args):
        account = account_service().store.snapshot()
        active = account.get("status") == "active"
        self.name.setText(account.get("uname") or (f"UID: {account.get('uid', '')}" if account else t("account.anonymous")))
        key = "active" if active else "expired_account" if account else "anonymous_hint"
        message = t(f"account.{self._message}") if self._message else t(f"account.{key}")
        uid = account.get("uid")
        self.status.setText(f"UID: {uid}\n{message}" if uid else message)
        self.description.setText(t("account.description"))
        self.login_button.setText(t("account.relogin" if account else "account.login"))
        self.check_button.setText(t("account.checking" if self._busy else "account.check"))
        self.logout_button.setText(t("account.logout"))
        self.apply_button.setText(t("account.apply_all"))
        self.login_button.setEnabled(not self._busy)
        self.check_button.setEnabled(active and not self._busy)
        self.logout_button.setEnabled(bool(account))
        self.apply_button.setEnabled(active and not self._busy)
        url = account.get("face", "")
        if url != self._avatar_url:
            self._avatar_url = url
            self.avatar.clear()
            self.avatar.setText("👤")
            if url:
                self.runner.submit(lambda: account_service().client.avatar(url),
                                   lambda result, error: self._avatar_loaded(url, result, error))

    def _avatar_loaded(self, url, result, error):
        if error or url != self._avatar_url:
            return
        pixmap = QPixmap()
        if pixmap.loadFromData(result):
            self.avatar.setPixmap(pixmap.scaled(48, 48, Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def login(self):
        if self._dialog is not None:
            self._dialog.raise_()
            return
        host = self.window()
        root = host.centralWidget() if hasattr(host, "centralWidget") and host.centralWidget() else self
        self._dialog = QRLoginOverlay(root)
        self._dialog.finished.connect(self._login_finished)
        self._dialog.open()

    def _login_finished(self, result):
        dialog = self._dialog
        self._dialog = None
        if result == QDialog.Accepted and dialog.account:
            try:
                account_service().store.replace(dialog.account)
                self._message = ""
                self.saved.emit("bili_account")
            except OSError:
                self._message = "storage_error"
        dialog.hide()
        dialog.deleteLater()
        self.refresh()

    def check(self):
        if self._busy:
            return
        self._busy = True
        self._message = ""
        self.refresh()
        self.runner.submit(lambda: account_service().maintain(force=True), self._checked)

    def _checked(self, _result, error):
        self._busy = False
        self._message = "network" if error == "network" else "response" if error else ""
        self.refresh()

    def logout(self):
        try:
            account_service().store.replace({})
            self._message = ""
            self.saved.emit("bili_account")
        except OSError:
            self._message = "storage_error"
        self.refresh()

    def apply_all(self):
        from core.config import bind_all_rooms_to_account
        bind_all_rooms_to_account()
        self._message = "applied"
        self.saved.emit("bili_account")
        self.refresh()
