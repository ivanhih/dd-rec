"""启动与主窗口 UI 测试：验证单窗口、覆盖层生命周期、延迟加载、分批启动监听。"""
from __future__ import annotations

import sys
import unittest
from unittest import mock

from PySide6.QtCore import qInstallMessageHandler
from PySide6.QtWidgets import QApplication, QPushButton, QWidget
from PySide6.QtTest import QTest


class _TestApp:
    """模块级 QApplication 单例，避免 Qt 测试重复创建/销毁。"""
    _app = None

    @classmethod
    def get(cls):
        if cls._app is None:
            cls._app = QApplication.instance() or QApplication(sys.argv)
        return cls._app


class TestStartupUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _TestApp.get()

    def setUp(self):
        # 在 import main 前 patch 模块级依赖，避免真实网络/文件/注册表操作
        patcher = mock.patch.dict(
            "sys.modules",
            {
                "core.config": mock.MagicMock(
                    load_app_data=mock.MagicMock(return_value={"channels": []}),
                    save_app_data=mock.MagicMock(),
                    ensure_default_config=mock.MagicMock(),
                    get_global_setting=mock.MagicMock(return_value=False),
                    get_room_config=mock.MagicMock(return_value={}),
                    get_room_setting=mock.MagicMock(return_value=None),
                    get_effective_format=mock.MagicMock(return_value="flv"),
                    APP_DIR=".",
                    VIDEO_SAVE_DIR=".",
                    DATA_FILE="./data.json",
                ),
                "core.updater": mock.MagicMock(
                    get_local_version=mock.MagicMock(return_value="1.0.0"),
                    check_update=mock.MagicMock(return_value=None),
                    IS_PORTABLE=False,
                ),
                "core.simple_updater": mock.MagicMock(show_update_dialog=mock.MagicMock()),
                "core.power": mock.MagicMock(
                    PowerKeepAlive=mock.MagicMock(),
                    set_auto_start=mock.MagicMock(),
                ),
                "core.bili_api": mock.MagicMock(get_bili_info=mock.MagicMock()),
                "core.recorder": mock.MagicMock(),
                "core.utils": mock.MagicMock(render_path_template=mock.MagicMock(return_value="")),
            },
        )
        self._module_patch = patcher.start()
        self.addCleanup(patcher.stop)

        import main
        self.main_module = main
        self.MainWindow = main.MainWindow
        self.window = None

    def tearDown(self):
        if self.window is not None:
            try:
                self.window.close()
                self.window.deleteLater()
            except Exception:
                pass
            self.window = None
        QTest.qWait(50)

    def _pump(self, ms: int = 200):
        """推进 Qt 事件循环 ms 毫秒。"""
        QTest.qWait(ms)

    def _make_window(self, **kwargs):
        """创建窗口并自动 patch 实例上的方法，避免真实系统调用。"""
        w = self.MainWindow(**kwargs)
        w._setup_tray = mock.MagicMock()
        w.start_refresh_timer = mock.MagicMock()
        return w

    def test_single_toplevel_window_during_startup(self):
        """启动过程中只应存在一个可见的顶层窗口，且是 MainWindow。"""
        self.window = self._make_window(show_startup_overlay=True)
        self.window.show()
        self._pump(50)

        visible_toplevels = [
            w for w in self.app.topLevelWidgets()
            if w.isVisible() and isinstance(w, QWidget)
        ]
        self.assertEqual(len(visible_toplevels), 1, "启动时应只有一个可见顶层窗口")
        self.assertIs(visible_toplevels[0], self.window)

    def test_startup_overlay_is_child_of_main_window(self):
        """开屏覆盖层应是主窗口 central widget 的子控件。"""
        self.window = self._make_window(show_startup_overlay=True)
        self.window.show()
        self._pump(50)

        overlay = self.window._startup_overlay
        self.assertIsNotNone(overlay)
        parent = overlay.parentWidget()
        self.assertIs(parent, self.window.centralWidget())

    def test_progress_never_regresses(self):
        """手动推进覆盖层进度，断言单调不减。"""
        self.window = self._make_window(show_startup_overlay=True)
        overlay = self.window._startup_overlay
        values = [0, 20, 40, 40, 80, 100]
        last = -1
        for v in values:
            overlay.set_progress(v)
            self.assertGreaterEqual(overlay._progress, last)
            last = overlay._progress

    def test_overlay_deleted_after_reveal(self):
        """bootstrap 完成后覆盖层应淡出并被删除（考虑新的最小停留时长）。"""
        self.window = self._make_window(show_startup_overlay=True)
        overlay = self.window._startup_overlay
        self.window.show()
        self.window.bootstrap()

        # 在最小 reveal 时间之前，覆盖层应仍然可见
        self._pump(800)
        self.assertTrue(overlay.isVisible(), "最小 reveal 时间之前覆盖层应仍可见")

        # 等待足够长时间让 reveal 完成
        self._pump(3500)

        overlay_deleted = False
        try:
            visible = self.window._startup_overlay.isVisible()
        except RuntimeError:
            overlay_deleted = True
            visible = False

        self.assertTrue(
            overlay_deleted or not visible,
            "reveal 后覆盖层应隐藏或已被清理",
        )
        self.assertTrue(self.window.main_content.isVisible())

    def test_no_channels_still_reveals(self):
        """没有频道时也能正常完成启动并揭示主界面。"""
        self.window = self._make_window(show_startup_overlay=True)
        self.window.show()
        self.window.bootstrap()
        self._pump(3500)

        self.assertTrue(self.window.main_content.isVisible())

    def test_overlay_minimum_visible_duration(self):
        """即使初始化瞬间完成，覆盖层也要保持最小可见时长。"""
        self.window = self._make_window(show_startup_overlay=True)
        overlay = self.window._startup_overlay
        shown_at = self.window._startup_overlay_shown_at_ms
        self.window.show()
        self.window.bootstrap()

        # 推进到进入动画刚完成但 hold 时间内
        self._pump(overlay.intro_duration_ms + 200)
        self.assertTrue(overlay.isVisible(), "进入动画完成后 hold 期间仍应可见")

        # 推进到最小 reveal 时间之前一点点，仍应可见
        self._pump(overlay.HOLD_BEFORE_REVEAL_MS - 300)
        self.assertTrue(overlay.isVisible(), "最小停留时间内覆盖层不应提前消失")

        # 等待 reveal 完成
        self._pump(overlay.FADE_OUT_DURATION_MS + 300)
        overlay_deleted = False
        try:
            visible = overlay.isVisible()
        except RuntimeError:
            overlay_deleted = True
            visible = False
        self.assertTrue(overlay_deleted or not visible, "最终应淡出消失")

    def test_intro_animation_duration_matches_constants(self):
        """intro_duration_ms 应等于各阶段动画常量之和。"""
        self.window = self._make_window(show_startup_overlay=True)
        overlay = self.window._startup_overlay
        expected = (
            overlay.FADE_IN_DURATION_MS
            + overlay.TEXT_FADE_DELAY_MS
            + overlay.TEXT_FADE_DURATION_MS
        )
        self.assertEqual(overlay.intro_duration_ms, expected)

    def test_logo_visible_from_start(self):
        """logo 从一开始就完整显示，不依赖 opacity effect。"""
        self.window = self._make_window(show_startup_overlay=True)
        overlay = self.window._startup_overlay
        self.assertIsNone(overlay._logo.graphicsEffect())
        self.assertFalse(overlay._logo.pixmap().isNull())
        # logo 在布局中即可，不依赖窗口已显示
        self.assertIsNotNone(overlay._logo.parentWidget())

    def test_text_container_has_single_opacity_effect(self):
        """文字容器使用统一的 opacity effect，而不是每个标签单独挂 effect。"""
        self.window = self._make_window(show_startup_overlay=True)
        overlay = self.window._startup_overlay
        self.assertIsNotNone(overlay._text_container.graphicsEffect())
        self.assertIsNone(overlay._title.graphicsEffect())
        self.assertIsNone(overlay._ver.graphicsEffect())
        self.assertIsNone(overlay._status.graphicsEffect())

    def test_overlay_animations_emit_no_qpainter_warnings(self):
        """进入与退出动画不应产生嵌套 graphics effect 的绘制警告。"""
        messages = []

        def _capture(_mode, _context, message):
            if "QPainter::" in message or "QWidgetEffectSourcePrivate::" in message:
                messages.append(message)

        previous_handler = qInstallMessageHandler(_capture)
        host = QWidget()
        overlay = None
        try:
            from ui.splash import StartupOverlay

            host.resize(900, 600)
            overlay = StartupOverlay(version="test", parent=host)
            overlay.setGeometry(host.rect())
            host.show()
            overlay.show()
            self._pump(overlay.intro_duration_ms + 100)

            self.assertTrue(overlay.is_intro_finished())
            self.assertIsNone(overlay._text_container.graphicsEffect())
            self.assertIsNone(overlay.graphicsEffect())

            overlay.fade_out_and_close()
            self._pump(overlay.FADE_OUT_DURATION_MS + 150)
        finally:
            qInstallMessageHandler(previous_handler)
            host.close()
            host.deleteLater()
            self._pump(20)

        self.assertEqual(messages, [])

    def test_source_mode_update_button_skips_portable_checker(self):
        """源码模式点击检查更新时应直接提示，不调用 Portable 更新器。"""
        self.window = self._make_window(show_startup_overlay=False)
        self.window.show_notification = mock.MagicMock()
        self.main_module._check_update.reset_mock()

        page = self.window._build_about_page()
        check_button = next(
            button for button in page.findChildren(QPushButton)
            if "检查更新" in button.text()
        )
        check_button.click()

        self.main_module._check_update.assert_not_called()
        self.window.show_notification.assert_called_once()
        self.assertIn("源码运行模式", self.window.show_notification.call_args.args[0])
        page.deleteLater()

    def test_settings_page_lazy_created(self):
        """设置页应延迟到第一次点击导航时才创建。"""
        self.window = self._make_window(show_startup_overlay=True)
        self.window.show()
        self.assertIsNone(self.window.global_settings_page)

        # 用真实 QWidget 作为 fake settings page 实例
        fake_page = QWidget(self.window.settings_page)
        fake_page.load_settings = mock.MagicMock()
        fake_page.saved = mock.MagicMock()

        with mock.patch.object(self.main_module, "GlobalSettingsOldStyleReplicaPage") as MockPage:
            MockPage.return_value = fake_page
            self.window.show_global_settings_page()
            MockPage.assert_called_once()
            self.assertIs(self.window.global_settings_page, fake_page)
            self.assertTrue(fake_page.load_settings.called)

            self.window.show_global_settings_page()
            MockPage.assert_called_once()

    def test_about_page_lazy_created(self):
        """关于页面应延迟到第一次点击导航时才创建。"""
        self.window = self._make_window(show_startup_overlay=True)
        self.window.show()
        self.assertIsNone(self.window.about_page)

        fake_page = QWidget()
        with mock.patch.object(self.window, "_build_about_page") as mock_build:
            mock_build.return_value = fake_page
            self.window.show_about_page()
            mock_build.assert_called_once()
            self.assertIs(self.window.about_page, fake_page)

            self.window.show_about_page()
            mock_build.assert_called_once()

    def test_recorders_started_lazily_after_reveal(self):
        """enabled 频道在启动阶段只创建卡片，揭示后才分批启动监听。"""
        channels = [
            {"room_id": "1", "uname": "a", "enabled": True},
            {"room_id": "2", "uname": "b", "enabled": True},
            {"room_id": "3", "uname": "c", "enabled": False},
        ]
        cfg_module = sys.modules["core.config"]
        cfg_module.load_app_data.return_value = {"channels": channels}

        self.window = self._make_window(show_startup_overlay=True)
        self.window.show()
        self.window.bootstrap()
        self._pump(200)

        self.assertEqual(len(self.window.cards), 3)

        self._pump(3500)
        # 由于 BiliRecorder 已被整体 mock，recorders 字典由 _start_recorder 逻辑决定：
        # mock 的 BiliRecorder 可被实例化并 moveToThread，thread.start 为空操作。
        self.assertIn("1", self.window.recorders)
        self.assertIn("2", self.window.recorders)
        self.assertNotIn("3", self.window.recorders)


class TestStartupTheme(unittest.TestCase):
    """启动主题应用：读 mock 的配置值，在首帧前生效，非法值回退深色。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _TestApp.get()

    def setUp(self):
        patcher = mock.patch.dict(
            "sys.modules",
            {
                "core.config": mock.MagicMock(
                    get_global_setting=mock.MagicMock(return_value=False),
                ),
            },
        )
        self._module_patch = patcher.start()
        self.addCleanup(patcher.stop)

        import ui.theme as theme_mod
        self.theme_mod = theme_mod
        # 重置全局 manager，模拟进程刚启动
        theme_mod._manager = None
        self.addCleanup(self._reset_theme)

    def _reset_theme(self):
        self.theme_mod._manager = None
        self.app.setStyleSheet("")

    def _import_main(self):
        # main 已在其它测试 import 过；直接取模块对象即可（入口逻辑已抽成函数）
        import main
        return main

    def test_light_theme_applied_before_window(self):
        sys.modules["core.config"].get_global_setting = mock.MagicMock(
            side_effect=lambda key: "浅色" if key == "theme" else False
        )
        main = self._import_main()

        effective = main.apply_startup_theme(self.app)

        self.assertEqual(effective, "浅色")
        self.assertIn("#F5F7FB", self.app.styleSheet())
        # 此时 MainWindow 尚未构造，主题已生效 → 首帧不会闪深色

    def test_invalid_value_falls_back_to_dark(self):
        sys.modules["core.config"].get_global_setting = mock.MagicMock(
            side_effect=lambda key: "不存在的主题" if key == "theme" else False
        )
        main = self._import_main()

        effective = main.apply_startup_theme(self.app)

        self.assertEqual(effective, "深色")
        self.assertIn("#0F0F13", self.app.styleSheet())

    def test_missing_theme_defaults_to_dark(self):
        sys.modules["core.config"].get_global_setting = mock.MagicMock(return_value=False)
        main = self._import_main()

        effective = main.apply_startup_theme(self.app)

        self.assertEqual(effective, "深色")

    def test_config_exception_falls_back_to_dark(self):
        sys.modules["core.config"].get_global_setting = mock.MagicMock(
            side_effect=RuntimeError("config broken")
        )
        main = self._import_main()

        effective = main.apply_startup_theme(self.app)

        self.assertEqual(effective, "深色")


if __name__ == "__main__":
    unittest.main()

