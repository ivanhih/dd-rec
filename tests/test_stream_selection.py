"""直播流选择：优先 H.264+FLV，fallback 可观察。"""
from __future__ import annotations

import unittest

from core.bili_api import normalize_codec_name, select_best_stream


def _s(codec, fmt, qn=10000, url="https://example/live"):
    return {
        "url": url,
        "base_url": "/live",
        "codec": codec,
        "format": fmt,
        "qn": qn,
        "fps": "30",
        "bitrate": "3000",
    }


class TestStreamSelection(unittest.TestCase):
    def test_normalize_codec(self):
        self.assertEqual(normalize_codec_name("h264"), "avc")
        self.assertEqual(normalize_codec_name("AVC"), "avc")
        self.assertEqual(normalize_codec_name("av1"), "av1")

    def test_prefer_h264_flv_exact(self):
        streams = [
            _s("av1", "fmp4", qn=10000),
            _s("avc", "flv", qn=10000),
            _s("hevc", "fmp4", qn=10000),
        ]
        best = select_best_stream(streams, cfg_codec="h264", cfg_format="flv")
        self.assertIsNotNone(best)
        self.assertEqual(best["codec"], "avc")
        self.assertEqual(best["format"], "flv")
        self.assertEqual(best["match_kind"], "exact")
        self.assertEqual(best["requested_codec"], "avc")
        self.assertEqual(best["requested_format"], "flv")

    def test_compatible_fallback_same_codec_other_format(self):
        streams = [
            _s("av1", "fmp4", qn=10000),
            _s("avc", "fmp4", qn=10000),
        ]
        best = select_best_stream(
            streams, cfg_codec="h264", cfg_format="flv", fallback_policy="compatible"
        )
        self.assertIsNotNone(best)
        self.assertEqual(best["codec"], "avc")
        self.assertEqual(best["format"], "fmp4")
        self.assertTrue(best["match_kind"].startswith("codec_only:"))

    def test_strict_refuses_silent_av1(self):
        streams = [
            _s("av1", "fmp4", qn=10000),
            _s("hevc", "ts", qn=10000),
        ]
        best = select_best_stream(
            streams, cfg_codec="h264", cfg_format="flv", fallback_policy="strict"
        )
        self.assertIsNone(best)

    def test_best_effort_allows_other_codec_with_kind(self):
        streams = [
            _s("av1", "fmp4", qn=10000),
            _s("hevc", "ts", qn=400),
        ]
        best = select_best_stream(
            streams, cfg_codec="h264", cfg_format="flv", fallback_policy="best_effort"
        )
        self.assertIsNotNone(best)
        self.assertTrue(best["match_kind"].startswith("best_effort:"))
        # av1 qn 更高应优先
        self.assertEqual(best["codec"], "av1")

    def test_does_not_prefer_av1_when_h264_available(self):
        # API 常把 av1 排在前面；选择逻辑仍应选 h264
        streams = [
            _s("av1", "fmp4", qn=10000),
            _s("avc", "flv", qn=400),
        ]
        best = select_best_stream(streams, cfg_codec="h264", cfg_format="flv")
        self.assertEqual(best["codec"], "avc")
        self.assertEqual(best["format"], "flv")


if __name__ == "__main__":
    unittest.main()
