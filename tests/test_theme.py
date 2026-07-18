"""ui/theme.py 单元测试：规范化、apply 行为、信号、无配置依赖。"""
import sys
import unittest

from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest


class _TestApp:
    _app = None

    @classmethod
    def get(cls):
        if cls._app is None:
            cls._app = QApplication.instance() or QApplication(sys.argv)
        return cls._app


class TestNormalizeTheme(unittest.TestCase):
    def test_dark_and_light_pass_through(self):
        from ui.theme import normalize_theme, DARK_THEME, LIGHT_THEME

        self.assertEqual(normalize_theme("深色"), DARK_THEME)
        self.assertEqual(normalize_theme("浅色"), LIGHT_THEME)

    def test_unknown_and_empty_fall_back_to_dark(self):
        from ui.theme import normalize_theme, DARK_THEME

        for value in (None, "", "dark", "light", "蓝色", 0, True, [], {}):
            self.assertEqual(normalize_theme(value), DARK_THEME, repr(value))


class TestThemeTokens(unittest.TestCase):
    REQUIRED_SURFACE_KEYS = (
        "window",
        "sidebar",
        "surface",
        "panel",
        "toolbar",
        "card",
        "card_hover",
        "surface_raised",
        "surface_input",
        "surface_code",
        "surface_hover",
        "surface_pressed",
        "control",
        "control_hover",
        "control_pressed",
        "control_border",
        "border",
        "border_subtle",
        "border_strong",
        "card_border",
        "card_border_hover",
        "focus",
        "action_folder_hover",
        "action_folder_pressed",
        "action_cut_hover",
        "action_cut_pressed",
        "action_delete_hover",
        "action_delete_pressed",
        "action_settings_hover",
        "action_settings_pressed",
    )

    def test_both_themes_have_surface_and_action_tokens(self):
        from ui.theme import DARK_TOKENS, LIGHT_TOKENS

        for name, tokens in (("dark", DARK_TOKENS), ("light", LIGHT_TOKENS)):
            for key in self.REQUIRED_SURFACE_KEYS:
                self.assertIn(key, tokens, f"{name} missing {key}")
                self.assertTrue(str(tokens[key]).strip(), f"{name}.{key} empty")

    def test_surface_layers_are_distinct(self):
        from ui.theme import DARK_TOKENS, LIGHT_TOKENS

        for name, tokens in (("dark", DARK_TOKENS), ("light", LIGHT_TOKENS)):
            # 三档色度：页面底 / 中间层 / 卡片
            self.assertNotEqual(tokens["window"], tokens["panel"], name)
            self.assertNotEqual(tokens["window"], tokens["toolbar"], name)
            self.assertNotEqual(tokens["window"], tokens["card"], name)
            self.assertNotEqual(tokens["panel"], tokens["card"], name)
            self.assertNotEqual(tokens["toolbar"], tokens["card"], name)
            # 控件相对中间层 / 卡片可辨
            self.assertNotEqual(tokens["toolbar"], tokens["surface_input"], name)
            self.assertNotEqual(tokens["toolbar"], tokens["control"], name)
            self.assertNotEqual(tokens["card"], tokens["surface_input"], name)
            self.assertNotEqual(tokens["card"], tokens["card_hover"], name)
            self.assertNotEqual(tokens["card_border"], tokens["card_border_hover"], name)
            self.assertNotEqual(tokens["panel"], tokens["surface_raised"], name)


class TestThemeManager(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _TestApp.get()

    def setUp(self):
        import ui.theme as theme_mod

        self.theme_mod = theme_mod
        # 每个用例用独立 manager，避免全局单例状态泄漏
        self.manager = theme_mod.ThemeManager()

    def _apply(self, value):
        return self.manager.apply(self.app, value)

    def test_first_dark_apply_installs_stylesheet(self):
        self.app.setStyleSheet("")
        self._apply("深色")
        self.assertTrue(self.app.styleSheet().strip())
        self.assertEqual(self.manager.current_name, "深色")

    def test_apply_returns_effective_name(self):
        self.assertEqual(self._apply("浅色"), "浅色")
        self.assertEqual(self._apply("胡闹"), "深色")

    def test_switch_replaces_stylesheet_and_tokens(self):
        self._apply("深色")
        dark_qss = self.app.styleSheet()
        self.assertIn("#0F0F13", dark_qss)

        self._apply("浅色")
        light_qss = self.app.styleSheet()
        self.assertNotEqual(dark_qss, light_qss)
        self.assertIn("#C2CAD9", light_qss)
        self.assertNotIn("#0F0F13", light_qss)
        self.assertEqual(self.manager.tokens["window"], "#C2CAD9")
        # 三档色度与控件对比
        self.assertNotEqual(self.manager.tokens["window"], self.manager.tokens["card"])
        self.assertNotEqual(self.manager.tokens["panel"], self.manager.tokens["card"])
        self.assertNotEqual(self.manager.tokens["toolbar"], self.manager.tokens["control"])
        self.assertNotEqual(self.manager.tokens["toolbar"], self.manager.tokens["surface_input"])

    def test_stylesheet_splits_cards_and_states(self):
        from ui.theme import build_stylesheet, DARK_TOKENS, LIGHT_TOKENS

        for tokens in (DARK_TOKENS, LIGHT_TOKENS):
            qss = build_stylesheet(tokens)
            self.assertIn("QFrame#roomCard {", qss)
            self.assertIn("QFrame#roomCard:hover {", qss)
            self.assertIn("QLabel#roomTag {\n    background-color: transparent;", qss)
            self.assertIn('QPushButton[settingsCompact="true"] {', qss)
            self.assertIn("QFrame#settingsCard {", qss)
            self.assertIn("QFrame#aboutCard {", qss)
            self.assertIn("QFrame#channelsToolbar {", qss)
            self.assertIn("QFrame#channelsToolbar QLineEdit", qss)
            self.assertIn("QToolButton#toolbarButton {", qss)
            self.assertIn(tokens["control"], qss)
            self.assertIn('QFrame#roomCard QToolButton[roomAction="folder"]:hover', qss)
            self.assertIn('QFrame#roomCard QToolButton[roomAction="cut"]:hover', qss)
            self.assertIn('QFrame#roomCard QToolButton[roomAction="delete"]:hover', qss)
            self.assertIn('QFrame#roomCard QToolButton[roomAction="settings"]:hover', qss)
            self.assertIn("QPushButton:pressed {", qss)
            self.assertIn("QToolButton#toolbarButton:pressed {", qss)
            self.assertIn("QTabBar::tab:hover {", qss)
            self.assertIn("QLineEdit:hover, QComboBox:hover", qss)
            self.assertIn("QFrame#roomSettingsPanel {", qss)
            self.assertIn(tokens["surface_raised"], qss)
            self.assertNotIn(
                "QFrame#settingsCard, QFrame#roomCard, QFrame#aboutCard",
                qss,
            )

    def test_roundtrip_theme_switch_keeps_dynamic_rules(self):
        self._apply("深色")
        self._apply("浅色")
        self._apply("深色")
        qss = self.app.styleSheet()
        self.assertIn("QFrame#roomCard:hover {", qss)
        self.assertIn('QFrame#roomCard QToolButton[roomAction="delete"]:hover', qss)
        self.assertEqual(self.manager.tokens["card"], "#262836")
        self.assertEqual(self.manager.tokens["card_border"], "#3F4156")
        self.assertEqual(self.manager.tokens["panel"], "#1A1B24")
        self.assertEqual(self.manager.tokens["surface_input"], "#323447")

    def test_changed_emitted_once_per_real_switch(self):
        seen = []
        self.manager.changed.connect(seen.append)

        self._apply("深色")
        self.assertEqual(seen, ["深色"])

        self._apply("深色")  # 重复应用当前主题：不重复发信号
        self.assertEqual(seen, ["深色"])

        self._apply("浅色")
        self.assertEqual(seen, ["深色", "浅色"])

        self._apply("未知值")  # 规范化为深色，与当前浅色不同：是真实切换
        self.assertEqual(seen, ["深色", "浅色", "深色"])

        self._apply(None)  # 规范化后等于当前主题：不发信号
        self.assertEqual(seen, ["深色", "浅色", "深色"])

    def test_palette_roles_follow_theme(self):
        from PySide6.QtGui import QPalette

        self._apply("浅色")
        palette = self.app.palette()
        self.assertEqual(palette.color(QPalette.Window).name(), "#c2cad9")

        self._apply("深色")
        palette = self.app.palette()
        self.assertEqual(palette.color(QPalette.Window).name(), "#0f0f13")

    def test_theme_module_does_not_import_config(self):
        # ui.theme 不应依赖 core.config（防止呈现层写配置）
        self.assertNotIn("core.config", sys.modules.get("ui.theme").__dict__)

    def tearDown(self):
        # 恢复应用级样式，避免污染其它测试模块
        self.app.setStyleSheet("")
        QTest.qWait(10)


class TestRepolish(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _TestApp.get()

    def test_repolish_keeps_widget_state(self):
        from PySide6.QtWidgets import QPushButton

        from ui.theme import repolish

        button = QPushButton("x")
        button.setProperty("variant", "primary")
        repolish(button)
        self.assertEqual(button.text(), "x")
        self.assertEqual(button.property("variant"), "primary")
        button.deleteLater()

    def tearDown(self):
        self.app.setStyleSheet("")


class TestRenderedSurfaces(unittest.TestCase):
    """真实控件抓图：防止只有 token/QSS 字符串正确、界面却仍是窗口色。"""

    @classmethod
    def setUpClass(cls):
        cls.app = _TestApp.get()

    def setUp(self):
        from ui.theme import ThemeManager

        self.manager = ThemeManager()

    def _dominant_colors(self, image, x0, y0, x1, y1, step=6):
        counts = {}
        for y in range(y0, min(y1, image.height()), step):
            for x in range(x0, min(x1, image.width()), step):
                name = image.pixelColor(x, y).name()
                counts[name] = counts.get(name, 0) + 1
        return sorted(counts.items(), key=lambda item: -item[1])

    def _assert_surfaces_render(self, theme_name):
        from PySide6.QtCore import QPoint, Qt
        from PySide6.QtWidgets import QFrame, QHBoxLayout, QLineEdit, QToolButton, QVBoxLayout, QWidget

        from ui.room_card import RoomCard
        from ui.theme import enable_surface

        self.manager.apply(self.app, theme_name)
        tokens = self.manager.tokens
        window = tokens["window"].lower()
        card = tokens["card"].lower()
        toolbar = tokens["toolbar"].lower()
        control = tokens["control"].lower()
        surface_input = tokens["surface_input"].lower()

        host = QWidget()
        host.setObjectName("themePaintHost")
        host.resize(720, 420)
        host.setStyleSheet(
            f"QWidget#themePaintHost {{ background-color: {tokens['window']}; }}"
        )
        root = QVBoxLayout(host)
        root.setContentsMargins(16, 16, 16, 16)
        root.setSpacing(16)

        bar = QFrame()
        bar.setObjectName("channelsToolbar")
        enable_surface(bar)
        bar.setFixedHeight(72)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 12, 12, 12)

        search = QLineEdit()
        search.setObjectName("channelSearch")
        search.setFixedSize(240, 40)
        filter_btn = QToolButton()
        filter_btn.setObjectName("toolbarButton")
        filter_btn.setFixedSize(44, 44)
        filter_btn.setText("F")
        bar_layout.addWidget(search)
        bar_layout.addWidget(filter_btn)
        bar_layout.addStretch()
        root.addWidget(bar)

        room_card = RoomCard({"room_id": "99", "uname": "测试", "title": "标题", "enabled": True})
        root.addWidget(room_card, 0, Qt.AlignLeft)

        host.show()
        self.app.processEvents()
        QTest.qWait(40)

        self.assertTrue(room_card.testAttribute(Qt.WA_StyledBackground))
        self.assertTrue(bar.testAttribute(Qt.WA_StyledBackground))

        # 直接抓控件本体，避免 host 空白区域干扰采样
        card_image = room_card.grab().toImage()
        bar_image = bar.grab().toImage()
        search_image = search.grab().toImage()
        button_image = filter_btn.grab().toImage()

        card_colors = self._dominant_colors(card_image, 12, 12, card_image.width() - 12, card_image.height() - 12)
        bar_colors = self._dominant_colors(bar_image, 8, 8, bar_image.width() - 8, bar_image.height() - 8)
        search_colors = self._dominant_colors(search_image, 4, 4, search_image.width() - 4, search_image.height() - 4, step=3)
        button_colors = self._dominant_colors(button_image, 4, 4, button_image.width() - 4, button_image.height() - 4, step=2)
        tag_top_left = room_card.tag_platform.mapTo(room_card, QPoint(0, 0))
        tag_rect = room_card.tag_platform.rect()
        tag_corner_colors = {
            card_image.pixelColor(tag_top_left + QPoint(1, 1)).name(),
            card_image.pixelColor(tag_top_left + QPoint(tag_rect.width() - 2, 1)).name(),
            card_image.pixelColor(tag_top_left + QPoint(1, tag_rect.height() - 2)).name(),
            card_image.pixelColor(
                tag_top_left + QPoint(tag_rect.width() - 2, tag_rect.height() - 2)
            ).name(),
        }

        self.assertTrue(card_colors, "room card region empty")
        self.assertTrue(bar_colors, "toolbar region empty")
        self.assertNotEqual(card_colors[0][0], window, f"{theme_name} room card still window-colored: {card_colors[:3]}")
        self.assertEqual(card_colors[0][0], card, f"{theme_name} room card dominant {card_colors[:3]}")
        self.assertEqual(tag_corner_colors, {card}, f"{theme_name} room tag does not inherit card: {tag_corner_colors}")
        self.assertNotEqual(bar_colors[0][0], window, f"{theme_name} toolbar still window-colored: {bar_colors[:3]}")
        self.assertEqual(bar_colors[0][0], toolbar, f"{theme_name} toolbar dominant {bar_colors[:3]}")

        search_top = {color for color, _ in search_colors[:3]}
        button_top = {color for color, _ in button_colors[:3]}
        self.assertIn(surface_input, search_top, f"{theme_name} search not distinct: {search_colors[:4]}")
        self.assertIn(control, button_top, f"{theme_name} toolbar button not distinct: {button_colors[:4]}")
        self.assertNotEqual(surface_input, window)
        self.assertNotEqual(control, window)
        self.assertNotEqual(surface_input, toolbar)
        self.assertNotEqual(control, toolbar)

        host.close()
        room_card.deleteLater()
        bar.deleteLater()
        host.deleteLater()
        QTest.qWait(10)

    def test_dark_surfaces_actually_paint(self):
        self._assert_surfaces_render("深色")

    def test_light_surfaces_actually_paint(self):
        self._assert_surfaces_render("浅色")

    def test_theme_roundtrip_keeps_painted_card(self):
        self._assert_surfaces_render("深色")
        self._assert_surfaces_render("浅色")
        self._assert_surfaces_render("深色")

    def tearDown(self):
        self.app.setStyleSheet("")
        QTest.qWait(10)


if __name__ == "__main__":
    unittest.main()
