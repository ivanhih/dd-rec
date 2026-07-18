# tests/test_i18n.py
"""ui/i18n 的语言映射、t() 回退与 LanguageManager 切换信号。"""
import sys
import unittest

from PySide6.QtWidgets import (
    QApplication,
    QPushButton,
    QStyle,
    QStyleOptionButton,
)

import ui.i18n as i18n
from ui.i18n import (
    DEFAULT_LANG,
    EN,
    ZH_CN,
    ZH_TW,
    LANG_DISPLAY,
    LanguageManager,
    normalize_language,
)


class _TestApp:
    _app = None

    @classmethod
    def get(cls):
        if cls._app is None:
            cls._app = QApplication.instance() or QApplication(sys.argv)
        return cls._app


class TestNormalize(unittest.TestCase):
    def test_display_name_to_code(self):
        self.assertEqual(normalize_language("简体中文"), ZH_CN)
        self.assertEqual(normalize_language("繁體中文"), ZH_TW)
        self.assertEqual(normalize_language("English"), EN)

    def test_code_passthrough(self):
        self.assertEqual(normalize_language(ZH_TW), ZH_TW)

    def test_unknown_falls_back(self):
        for value in (None, "", "日本語", 0, True, [], {}):
            self.assertEqual(normalize_language(value), DEFAULT_LANG, repr(value))

    def test_display_map_roundtrip(self):
        self.assertEqual(len(set(LANG_DISPLAY.values())), 3)


class TestDictionaryCoverage(unittest.TestCase):
    def test_all_languages_have_same_keys(self):
        zh = set(i18n.STRINGS[ZH_CN])
        tw = set(i18n.STRINGS[ZH_TW])
        en = set(i18n.STRINGS[EN])
        self.assertEqual(zh, tw)
        self.assertEqual(zh, en)
        self.assertGreaterEqual(len(zh), 50)

    def test_no_empty_translations(self):
        for code, table in i18n.STRINGS.items():
            for key, value in table.items():
                self.assertTrue(str(value).strip(), f"{code}:{key}")


class _ManagerCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _TestApp.get()

    def setUp(self):
        self._old = i18n._manager
        self.manager = LanguageManager(parent=self.app)
        i18n._manager = self.manager

    def tearDown(self):
        i18n._manager = self._old


class TestTranslate(_ManagerCase):
    def test_t_uses_current_language(self):
        self.manager.set_language("English")
        self.assertEqual(i18n.t("nav.channels"), "Channels")
        self.manager.set_language("繁體中文")
        self.assertEqual(i18n.t("nav.channels"), "頻道")
        self.manager.set_language("简体中文")
        self.assertEqual(i18n.t("nav.channels"), "频道")

    def test_t_missing_key_falls_back_to_zh_cn(self):
        i18n.STRINGS[ZH_CN]["_test.only_zh"] = "仅简体"
        try:
            self.manager.set_language("English")
            self.assertEqual(i18n.t("_test.only_zh"), "仅简体")
        finally:
            del i18n.STRINGS[ZH_CN]["_test.only_zh"]

    def test_t_unknown_key_returns_key(self):
        self.assertEqual(i18n.t("no.such.key"), "no.such.key")

    def test_t_format_kwargs(self):
        i18n.STRINGS[ZH_CN]["_test.fmt"] = "房间 {name}"
        try:
            self.manager.set_language("简体中文")
            self.assertEqual(i18n.t("_test.fmt", name="测试"), "房间 测试")
        finally:
            del i18n.STRINGS[ZH_CN]["_test.fmt"]


class TestManager(_ManagerCase):
    def test_change_emits_signal(self):
        seen = []
        self.manager.changed.connect(seen.append)
        self.manager.set_language("English")
        self.manager.set_language("English")  # 重复设置不重复发
        self.manager.set_language("繁體中文")
        self.assertEqual(seen, [EN, ZH_TW])

    def test_current_code(self):
        self.manager.set_language("繁體中文")
        self.assertEqual(self.manager.current_code, ZH_TW)


class TestUiRetranslate(_ManagerCase):
    def test_main_window_and_card_switch_language(self):
        from ui.room_card import RoomCard
        from main import MainWindow

        self.manager.set_language("简体中文")
        window = MainWindow(show_startup_overlay=False)
        card = RoomCard({"room_id": "1", "uname": "测试", "title": "标题", "enabled": True})
        window.cards["1"] = card

        self.manager.set_language("English")
        window.retranslate_ui()

        self.assertEqual(window.windowTitle(), "DD Rec")
        self.assertEqual(window.channels_title_label.text(), "Channels")
        self.assertEqual(window.search_input.placeholderText(), "🔍 Search")
        self.assertEqual(window.btn_add.text(), "Add")
        self.assertEqual(card.lbl_m.text(), "⏯ Monitoring")
        self.assertEqual(card.lbl_l.text(), "🌙 Offline")
        self.assertEqual(card.lbl_r.text(), "⏳ Idle")

        # 状态 code 过滤不依赖中文
        card.update_status("active", "on", "recording", "live title")
        self.assertTrue(card.is_live())
        self.assertTrue(card.is_recording_active())
        self.assertEqual(card.lbl_l.text(), "🔴 Live")
        self.assertEqual(card.lbl_r.text(), "📹 Recording")

        self.manager.set_language("繁體中文")
        window.retranslate_ui()
        self.assertEqual(window.channels_title_label.text(), "頻道")
        self.assertEqual(card.lbl_r.text(), "📹 錄製中")

        window.close()
        card.deleteLater()



class TestSettingsPageI18n(_ManagerCase):
    def test_settings_combo_translates_without_changing_stored_value(self):
        from unittest import mock
        from ui.settings_dialog import GlobalSettingsOldStyleReplicaPage

        saved = []
        self.manager.set_language("English")
        page = GlobalSettingsOldStyleReplicaPage()
        combo = page._controls["proxy_mode"]
        with mock.patch("ui.settings_dialog.set_global_setting", side_effect=lambda key, value: saved.append((key, value))):
            combo.setCurrentIndex(combo.findData("系统"))
        self.assertEqual(combo.currentText(), "System")
        self.assertEqual(combo.currentData(), "系统")
        self.assertEqual(saved, [("proxy_mode", "系统")])

        saved.clear()
        self.manager.set_language("繁體中文")
        self.assertEqual(combo.currentText(), "系統")
        self.assertEqual(combo.currentData(), "系统")
        self.assertEqual(saved, [])
        page.deleteLater()

    def test_settings_compact_buttons_fit_all_languages_and_themes(self):
        from ui.settings_dialog import GlobalSettingsOldStyleReplicaPage
        from ui.theme import ThemeManager

        theme_manager = ThemeManager()
        for theme in ("深色", "浅色"):
            theme_manager.apply(self.app, theme)
            for language in ("简体中文", "繁體中文", "English"):
                with self.subTest(theme=theme, language=language):
                    self.manager.set_language(language)
                    page = GlobalSettingsOldStyleReplicaPage()
                    page.resize(900, 650)
                    page.show()
                    page._open_size_overlay()
                    page._open_cookie_overlay()
                    page._open_notify_template_overlay("notify_title_template", "编辑")
                    page._open_path_template_overlay("path_template")
                    self.app.processEvents()

                    buttons = [
                        button
                        for button in page.findChildren(QPushButton)
                        if button.property("settingsCompact") is True
                    ]
                    self.assertGreater(len(buttons), 10)
                    for button in buttons:
                        font_metrics = button.fontMetrics()
                        hint = button.sizeHint()
                        self.assertLessEqual(
                            hint.height(),
                            button.maximumHeight(),
                            f"{language}: {button.text()!r} needs {hint.height()}px, has {button.maximumHeight()}px",
                        )
                        if button.text():
                            self.assertGreaterEqual(
                                hint.width(),
                                font_metrics.horizontalAdvance(button.text()) + 16,
                                f"{language}: {button.text()!r} width hint clips text",
                            )
                        if button.isVisibleTo(page):
                            option = QStyleOptionButton()
                            option.initFrom(button)
                            contents = button.style().subElementRect(
                                QStyle.SubElement.SE_PushButtonContents,
                                option,
                                button,
                            )
                            self.assertGreaterEqual(
                                contents.height(),
                                font_metrics.height(),
                                f"{language}: {button.text()!r} vertical contents clipped",
                            )
                            if button.text():
                                self.assertGreaterEqual(
                                    contents.width(),
                                    font_metrics.horizontalAdvance(button.text()),
                                    f"{language}: {button.text()!r} horizontal contents clipped",
                                )

                    page.close()
                    page.deleteLater()
                    self.app.processEvents()

    def test_settings_page_titles_switch_language(self):
        import re
        from PySide6.QtWidgets import QLabel
        from ui.settings_dialog import GlobalSettingsOldStyleReplicaPage

        self.manager.set_language("English")
        page = GlobalSettingsOldStyleReplicaPage()
        titles = [lab.text() for lab in page.findChildren(QLabel) if lab.property("role") == "title"]
        self.assertIn("Appearance", titles)
        self.assertIn("File split", titles)
        self.assertIn("Network", titles)
        self.assertTrue(all(not re.search(r"[一-鿿]", t) for t in titles), titles)

        self.manager.set_language("繁體中文")
        page.retranslate_ui()
        titles_tw = [lab.text() for lab in page.findChildren(QLabel) if lab.property("role") == "title"]
        self.assertTrue(any("檔案" in t or "網路" in t or "外觀" in t for t in titles_tw), titles_tw)
        page.deleteLater()

if __name__ == "__main__":
    unittest.main()
