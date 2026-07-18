# ui/slide_stack.py
"""带上下滑动过渡的 QStackedWidget。

正确时序（避免“内容先出来、动画在后面”）：
  1. grab 当前页
  2. 临时 setCurrentWidget(目标) → grab 新页 → 立刻切回旧页
  3. 盖不透明 cover，放两张截图做上下滑动
  4. 动画结束后再 setCurrentWidget(目标)，删 cover

动画过程中用户只看到截图，真实页面不会提前露出来。
"""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QParallelAnimationGroup, QPoint, QPropertyAnimation, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication, QLabel, QStackedWidget, QWidget


class SlideStackedWidget(QStackedWidget):
    """支持上下滑动切换的页面栈。"""

    def __init__(self, parent=None, *, duration: int = 280):
        super().__init__(parent)
        self._duration = duration
        self._anim_group: QParallelAnimationGroup | None = None
        self._cover: QWidget | None = None
        self._busy = False
        self._pending_widget: QWidget | None = None

    def slide_to(self, widget: QWidget, *, direction: str | None = None) -> bool:
        """滑动切换到 widget。

        direction:
          - "down":  上→下（前进：频道→设置/关于）
          - "up":    下→上（后退：设置/关于→频道）
          - None:    按 index 大小自动判断
        """
        if widget is None:
            return False
        target_index = self.indexOf(widget)
        if target_index < 0:
            return False

        current = self.currentWidget()
        current_index = self.currentIndex()
        if current is widget or current_index == target_index:
            # 若动画还在跑且目标就是当前 pending，无需重复
            if self._busy and self._pending_widget is widget:
                return False
            if not self._busy:
                return False

        # 动画进行中再切：先落到上一次目标，再开新动画
        if self._busy:
            self._force_finish()
            current = self.currentWidget()
            current_index = self.currentIndex()
            if current is widget:
                return False

        if direction is None:
            # 频道→设置/关于：下→上
            # 设置/关于→频道：上→下
            direction = "up" if target_index > current_index else "down"

        # 首次 / 不可见 / 尺寸无效时直接硬切
        if current is None or not current.isVisible() or self.width() <= 0 or self.height() <= 0:
            self.setCurrentWidget(widget)
            return True

        w = self.width()
        h = self.height()
        self._pending_widget = widget

        # 1) 截旧页
        old_pix = current.grab()

        # 2) 临时切到新页截图，再立刻切回旧页（同一事件循环内，用户看不见）
        self.setUpdatesEnabled(False)
        try:
            self.setCurrentWidget(widget)
            widget.resize(w, h)
            widget.repaint()
            new_pix = widget.grab()
            self.setCurrentWidget(current)
            current.resize(w, h)
        finally:
            self.setUpdatesEnabled(True)

        # 3) 不透明 cover，挡住真实页面
        bg = self._cover_background()
        cover = QWidget(self)
        cover.setGeometry(0, 0, w, h)
        cover.setAttribute(Qt.WA_StyledBackground, True)
        cover.setStyleSheet(f"background-color: {bg};")
        cover.raise_()
        cover.show()
        self._cover = cover

        old_label = QLabel(cover)
        old_label.setPixmap(old_pix)
        old_label.setGeometry(0, 0, w, h)
        old_label.show()

        new_label = QLabel(cover)
        new_label.setPixmap(new_pix)
        new_label.setGeometry(0, 0, w, h)
        new_label.show()

        if direction == "down":
            # 上→下：新页从上方进来，旧页往下走
            new_start = QPoint(0, -h)
            new_end = QPoint(0, 0)
            old_end = QPoint(0, h)
        else:
            # 下→上：新页从下方进来，旧页往上走
            new_start = QPoint(0, h)
            new_end = QPoint(0, 0)
            old_end = QPoint(0, -h)

        new_label.move(new_start)

        anim_new = QPropertyAnimation(new_label, b"pos", self)
        anim_new.setDuration(self._duration)
        anim_new.setStartValue(new_start)
        anim_new.setEndValue(new_end)
        anim_new.setEasingCurve(QEasingCurve.OutCubic)

        anim_old = QPropertyAnimation(old_label, b"pos", self)
        anim_old.setDuration(self._duration)
        anim_old.setStartValue(QPoint(0, 0))
        anim_old.setEndValue(old_end)
        anim_old.setEasingCurve(QEasingCurve.OutCubic)

        group = QParallelAnimationGroup(self)
        group.addAnimation(anim_new)
        group.addAnimation(anim_old)
        group.finished.connect(self._on_anim_finished)
        self._anim_group = group
        self._busy = True
        group.start()
        return True

    def _cover_background(self) -> str:
        """cover 底色：优先主题 window，兜底用 palette。"""
        try:
            from ui.theme import theme_manager
            tokens = theme_manager().tokens
            color = tokens.get("window")
            if color:
                return color
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            return app.palette().window().color().name()
        return "#0F0F13"

    def _on_anim_finished(self):
        # 动画结束：真正切到目标页，再删 cover
        target = self._pending_widget
        if target is not None and self.indexOf(target) >= 0:
            self.setCurrentWidget(target)
        self._cleanup_cover()
        self._anim_group = None
        self._pending_widget = None
        self._busy = False

    def _force_finish(self):
        """中断当前动画并立刻落到 pending 目标页。"""
        group = self._anim_group
        if group is not None:
            try:
                group.finished.disconnect(self._on_anim_finished)
            except Exception:
                pass
            group.stop()
            group.deleteLater()
            self._anim_group = None

        target = self._pending_widget
        if target is not None and self.indexOf(target) >= 0:
            self.setCurrentWidget(target)
        self._cleanup_cover()
        self._pending_widget = None
        self._busy = False

    def _cleanup_cover(self):
        if self._cover is not None:
            self._cover.hide()
            self._cover.deleteLater()
            self._cover = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._busy:
            self._force_finish()
