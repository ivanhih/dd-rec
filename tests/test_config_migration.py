"""配置迁移：旧 av1/fmp4 默认 → h264/flv；convert_delete_source 映射。"""
from __future__ import annotations

import unittest

from core.config import (
    DEFAULT_GLOBAL_SETTINGS,
    LEGACY_STREAM_DEFAULTS,
    STREAM_DEFAULTS_MIGRATION_FLAG,
    apply_config_migrations,
    _merge_global_settings,
)


class TestConfigMigration(unittest.TestCase):
    def test_migrate_legacy_av1_fmp4_defaults(self):
        saved = {
            "stream_codec": LEGACY_STREAM_DEFAULTS["stream_codec"],
            "stream_format": LEGACY_STREAM_DEFAULTS["stream_format"],
        }
        merged, notes, dirty = _merge_global_settings(saved)
        self.assertEqual(merged["stream_codec"], "h264")
        self.assertEqual(merged["stream_format"], "flv")
        self.assertTrue(merged[STREAM_DEFAULTS_MIGRATION_FLAG])
        self.assertTrue(dirty)
        self.assertTrue(any("stream_codec/format" in n for n in notes))

    def test_do_not_override_user_explicit_choice(self):
        # 用户只改了 codec 或 format 之一：视为明确选择，不覆盖
        saved = {"stream_codec": "av1", "stream_format": "flv"}
        merged, notes, _dirty = _merge_global_settings(saved)
        self.assertEqual(merged["stream_codec"], "av1")
        self.assertEqual(merged["stream_format"], "flv")
        self.assertTrue(merged[STREAM_DEFAULTS_MIGRATION_FLAG])
        self.assertFalse(any("stream_codec/format" in n for n in notes))

        saved2 = {"stream_codec": "h264", "stream_format": "fmp4"}
        merged2, notes2, _ = _merge_global_settings(saved2)
        self.assertEqual(merged2["stream_codec"], "h264")
        self.assertEqual(merged2["stream_format"], "fmp4")
        self.assertFalse(any("stream_codec/format" in n for n in notes2))

    def test_migration_is_idempotent(self):
        saved = {
            "stream_codec": "av1",
            "stream_format": "fmp4",
        }
        merged, notes, dirty = _merge_global_settings(saved)
        self.assertTrue(dirty)
        self.assertEqual(merged["stream_codec"], "h264")

        # 第二次：磁盘已有迁移标记，即使用户又写成 av1/fmp4 也不再迁
        saved_after = dict(merged)
        saved_after["stream_codec"] = "av1"
        saved_after["stream_format"] = "fmp4"
        merged2, notes2, dirty2 = _merge_global_settings(saved_after)
        self.assertEqual(merged2["stream_codec"], "av1")
        self.assertEqual(merged2["stream_format"], "fmp4")
        self.assertFalse(any("stream_codec/format" in n for n in notes2))
        self.assertFalse(dirty2)

    def test_convert_delete_source_maps_to_artifact_keep_source(self):
        saved = {"convert_delete_source": True}
        # 磁盘没有 artifact_keep_source
        gs = dict(DEFAULT_GLOBAL_SETTINGS)
        gs["convert_delete_source"] = True
        notes = apply_config_migrations(saved, gs)
        self.assertFalse(gs["artifact_keep_source"])
        self.assertTrue(any("artifact_keep_source" in n for n in notes))

    def test_artifact_keep_source_wins_over_legacy(self):
        saved = {"convert_delete_source": True, "artifact_keep_source": True}
        gs = dict(DEFAULT_GLOBAL_SETTINGS)
        gs["convert_delete_source"] = True
        gs["artifact_keep_source"] = True
        notes = apply_config_migrations(saved, gs)
        self.assertTrue(gs["artifact_keep_source"])
        self.assertFalse(any("artifact_keep_source" in n for n in notes))

    def test_remove_deprecated_update_source_settings(self):
        saved = {
            "mirror_chyan_enabled": True,
            "mirror_chyan_cdk": "secret",
            "update_channel": "mirror_chyan",
        }
        merged, notes, dirty = _merge_global_settings(saved)
        self.assertNotIn("mirror_chyan_enabled", merged)
        self.assertNotIn("mirror_chyan_cdk", merged)
        self.assertEqual(merged["update_channel"], "github")
        self.assertTrue(dirty)
        self.assertTrue(any("update_channel" in n for n in notes))


if __name__ == "__main__":
    unittest.main()
