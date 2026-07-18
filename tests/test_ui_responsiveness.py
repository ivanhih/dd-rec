"""UI 响应性回归测试：确保慢操作不占用 GUI 线程，初始化不触发保存副作用。"""
from __future__ import annotations

import sys
import time
import unittest
from unittest import mock

from PySide6.QtCore import QAbstractAnimation, qInstallMessageHandler
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QLineEdit

from core.recorder import BiliRecorder
from ui.room_card import RoomCard, ToggleSwitch
from ui.settings_dialog import (
    AddChannelDialog,
    GlobalSettingsOldStyleReplicaPage,
    RoomSettingsPage,
)


class TestUiResponsiveness(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def tearDown(self):
        QTest.qWait(20)

    def test_add_channel_network_request_keeps_event_loop_responsive(self):
        dialog = AddChannelDialog()
        dialog.input.setText("23058")
        event_loop_tick = []

        def slow_lookup(_text):
            time.sleep(0.25)
            return {"room_id": "23058", "uname": "测试主播"}

        with mock.patch("core.bili_api.get_bili_info", side_effect=slow_lookup):
            from PySide6.QtCore import QTimer

            QTimer.singleShot(20, lambda: event_loop_tick.append(True))
            started = time.perf_counter()
            dialog.confirm()
            confirm_elapsed = time.perf_counter() - started

            self.assertLess(confirm_elapsed, 0.1, "confirm 不应同步等待网络请求")
            self.assertFalse(dialog.confirm_btn.isEnabled())
            self.assertFalse(dialog.input.isEnabled())

            QTest.qWait(80)
            self.assertTrue(event_loop_tick, "慢请求期间 Qt 事件循环仍应继续运行")

            for _ in range(20):
                if dialog.result is not None:
                    break
                QTest.qWait(100)
            self.assertIsNotNone(dialog.result, "后台请求应在超时前回到 GUI 线程")
            self.assertEqual(dialog.result["room_id"], "23058")
            self.assertTrue(dialog.confirm_btn.isEnabled())

        dialog.deleteLater()

    def test_toggle_switch_can_snap_without_starting_animation(self):
        toggle = ToggleSwitch()
        toggle.setChecked(True, animate=False)

        self.assertTrue(toggle.isChecked())
        self.assertEqual(toggle._switch_x, 32)
        self.assertFalse(toggle._animation_timer.isActive())

        from ui.theme import theme_manager
        theme_manager().apply(self.app, "浅色")
        self.assertTrue(toggle.isChecked())
        self.assertEqual(toggle._switch_x, 32)
        self.assertFalse(toggle._animation_timer.isActive())
        theme_manager().apply(self.app, "深色")
        toggle.deleteLater()

    def test_global_settings_load_blocks_widget_signals(self):
        line = QLineEdit()
        combo = QComboBox()
        combo.addItems(["a", "b"])
        toggle = ToggleSwitch()
        fake_page = mock.Mock()
        fake_page._controls = {"line": line, "combo": combo, "toggle": toggle}
        emitted = []
        line.textChanged.connect(lambda _value: emitted.append("line"))
        combo.currentTextChanged.connect(lambda _value: emitted.append("combo"))
        toggle.toggled.connect(lambda _value: emitted.append("toggle"))
        values = {"line": "loaded", "combo": "b", "toggle": True}

        with mock.patch("ui.settings_dialog.get_global_setting", side_effect=values.get):
            GlobalSettingsOldStyleReplicaPage._load_values(fake_page)

        self.assertEqual(emitted, [])
        self.assertEqual(line.text(), "loaded")
        self.assertEqual(combo.currentText(), "b")
        self.assertTrue(toggle.isChecked())
        self.assertFalse(toggle._animation_timer.isActive())

        line.deleteLater()
        combo.deleteLater()
        toggle.deleteLater()

    def test_room_settings_load_blocks_widget_signals(self):
        line = QLineEdit()
        toggle = ToggleSwitch()
        fake_page = mock.Mock()
        fake_page.room_id = "42"
        fake_page._controls = {"line": line, "toggle": toggle}
        emitted = []
        line.textChanged.connect(lambda _value: emitted.append("line"))
        toggle.toggled.connect(lambda _value: emitted.append("toggle"))
        values = {"line": "room-value", "toggle": True}

        with mock.patch("ui.settings_dialog.get_room_setting", side_effect=lambda _rid, key: values[key]):
            RoomSettingsPage._load_values(fake_page)

        self.assertEqual(emitted, [])
        self.assertEqual(line.text(), "room-value")
        self.assertTrue(toggle.isChecked())
        self.assertFalse(toggle._animation_timer.isActive())

        line.deleteLater()
        toggle.deleteLater()

    def test_add_channel_loading_animation_stops_on_result(self):
        dialog = AddChannelDialog()
        dialog.input.setText("23058")

        with mock.patch.object(dialog, "_do_add") as do_add:
            dialog.confirm()
            do_add.assert_called_once_with("23058")
            self.assertTrue(dialog._loading_timer.isActive())
            self.assertTrue(dialog.confirm_btn.text().startswith("获取中"))
            first_text = dialog.confirm_btn.text()
            dialog._advance_loading_animation()
            self.assertNotEqual(dialog.confirm_btn.text(), first_text)

            dialog._on_add_finished(None, "请求失败")
            self.assertFalse(dialog._loading_timer.isActive())
            self.assertTrue(dialog.confirm_btn.isEnabled())
            self.assertTrue(dialog.input.isEnabled())
            self.assertEqual(dialog.confirm_btn.text(), "添加")
            self.assertFalse(dialog.error_label.isHidden())

        dialog.deleteLater()

    def test_room_card_status_fade_only_runs_for_real_status_change(self):
        card = RoomCard({"room_id": "42", "uname": "测试主播", "enabled": True})
        card.update_status("⚙️ 已暂停", "🌙 未开播", "⏳ 闲置中", "", animate=False)
        self.assertEqual(card._status_fade_animation.state(), QAbstractAnimation.Stopped)

        card.update_status("监控中", "直播中", "录制中", "直播标题", animate=True)
        self.assertEqual(card.lbl_m.property("statusTone"), "success")
        self.assertEqual(card.lbl_l.property("statusTone"), "danger")
        self.assertEqual(card.lbl_r.property("statusTone"), "info")
        self.assertEqual(card._status_fade_animation.state(), QAbstractAnimation.Running)
        QTest.qWait(220)
        self.assertEqual(card._status_fade_animation.state(), QAbstractAnimation.Stopped)
        self.assertAlmostEqual(card._status_opacity_effect.opacity(), 1.0, places=2)

        card.update_status("监控中", "直播中", "录制中", "直播标题", animate=True)
        self.assertEqual(card._status_fade_animation.state(), QAbstractAnimation.Stopped)
        card.deleteLater()

    def test_room_card_entry_animation_is_single_shot(self):
        card = RoomCard({"room_id": "43", "uname": "新主播", "enabled": False})
        self.assertTrue(card.play_entry_animation())
        self.assertFalse(card.play_entry_animation())
        self.assertIsNotNone(card._entry_animation)
        QTest.qWait(240)
        self.assertIsNone(card._entry_animation)
        self.assertIsNone(card.graphicsEffect())
        card.deleteLater()

    def test_card_entry_and_status_fade_emit_no_qpainter_warnings(self):
        """新卡片入场与状态淡入同时发生时不应触发 Qt 绘制冲突。"""
        messages = []

        def _capture(_mode, _context, message):
            if "QPainter::" in message or "QWidgetEffectSourcePrivate::" in message:
                messages.append(message)

        card = RoomCard({"room_id": "44", "uname": "动画测试", "enabled": True})
        card.resize(650, 280)
        previous_handler = qInstallMessageHandler(_capture)
        try:
            card.show()
            self.assertTrue(card.play_entry_animation())
            card.update_status("监控中", "直播中", "录制中", "动画测试直播", animate=True)
            QTest.qWait(280)
        finally:
            qInstallMessageHandler(previous_handler)
            card.close()
            card.deleteLater()

        self.assertEqual(messages, [])

    def test_request_stop_async_only_sets_flags(self):
        with mock.patch.object(BiliRecorder, "__init__", lambda self, *args, **kwargs: None):
            recorder = BiliRecorder.__new__(BiliRecorder)
        recorder.is_monitoring = True
        recorder.is_running = True
        recorder._async_stop_requested = False
        recorder._async_stop_reset_time = True
        recorder.stop_recording = mock.Mock()

        recorder.request_stop_async(reset_time=False)

        self.assertFalse(recorder.is_monitoring)
        self.assertFalse(recorder.is_running)
        self.assertTrue(recorder._async_stop_requested)
        self.assertFalse(recorder._async_stop_reset_time)
        recorder.stop_recording.assert_not_called()

    def test_theme_save_persists_then_applies_then_signals(self):
        """主题保存顺序：持久化 → 应用主题 → emit saved；非主题 key 不触发 apply。"""
        import ui.settings_dialog as sd

        calls = []

        def fake_set(key, value):
            calls.append(("persist", key, value))

        class FakeManager:
            def apply(self, app, value):
                calls.append(("apply", value))
                return value

        fake_page = mock.Mock()
        fake_page.window = mock.Mock(return_value=None)
        fake_page.saved = mock.Mock()
        fake_page.saved.emit = lambda key: calls.append(("saved", key))

        with mock.patch.object(sd, "set_global_setting", side_effect=fake_set), \
             mock.patch("ui.theme.theme_manager", return_value=FakeManager()):
            GlobalSettingsOldStyleReplicaPage._save_setting(fake_page, "theme", "浅色")
            GlobalSettingsOldStyleReplicaPage._save_setting(fake_page, "proxy", "127.0.0.1")

        self.assertEqual(
            calls,
            [
                ("persist", "theme", "浅色"),
                ("apply", "浅色"),
                ("saved", "theme"),
                ("persist", "proxy", "127.0.0.1"),
                ("saved", "proxy"),
            ],
        )

    def test_theme_combo_load_normalizes_unknown_value_without_saving(self):
        """未知主题值加载时 UI 显示深色回退，且不触发保存。"""
        import ui.settings_dialog as sd

        combo = QComboBox()
        combo.addItems(["深色", "浅色"])
        fake_page = mock.Mock()
        fake_page._controls = {"theme": combo}
        saved = []

        with mock.patch.object(sd, "get_global_setting", return_value="不存在的主题"), \
             mock.patch.object(sd, "set_global_setting", side_effect=lambda k, v: saved.append((k, v))):
            GlobalSettingsOldStyleReplicaPage._load_values(fake_page)

        self.assertEqual(combo.currentText(), "深色")
        self.assertEqual(saved, [])
        combo.deleteLater()

    def test_run_performs_async_cleanup_and_finishes_once(self):
        with mock.patch.object(BiliRecorder, "__init__", lambda self, *args, **kwargs: None):
            recorder = BiliRecorder.__new__(BiliRecorder)
        recorder.room_id = "42"
        recorder._async_stop_requested = True
        recorder._async_stop_reset_time = False
        recorder._run_loop = mock.Mock()
        recorder.request_stop = mock.Mock()
        recorder._emit_finished_once = mock.Mock()

        with mock.patch("core.recorder.get_global_setting", return_value=True):
            recorder.run()

        recorder._run_loop.assert_called_once_with()
        recorder.request_stop.assert_called_once_with(reset_time=False)
        recorder._emit_finished_once.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
