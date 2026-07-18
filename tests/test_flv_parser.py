"""FLV parser / session unit tests (no network)."""
from __future__ import annotations

import os
import struct
import tempfile
import unittest
from pathlib import Path

from core.flv.parser import FlvTagParser, feed_filter
from core.flv.session import FlvCaptureSession
from core.flv.types import FlvHeader, FlvTag, TagType
from core.flv.writer import FlvSegmentWriter
from core.flv.timestamps import TimestampCorrector


def _pack_tag(tag_type: int, ts: int, data: bytes) -> bytes:
    data_size = len(data)
    ts_low = ts & 0xFFFFFF
    ts_ext = (ts >> 24) & 0xFF
    header = bytes(
        [
            tag_type,
            (data_size >> 16) & 0xFF,
            (data_size >> 8) & 0xFF,
            data_size & 0xFF,
            (ts_low >> 16) & 0xFF,
            (ts_low >> 8) & 0xFF,
            ts_low & 0xFF,
            ts_ext,
            0,
            0,
            0,
        ]
    )
    return header + data + struct.pack(">I", 11 + data_size)


def _avc_seq() -> bytes:
    # frameType=1 key, codecId=7 AVC, packetType=0 sequence header
    return bytes([0x17, 0x00, 0x00, 0x00, 0x00, 0x01, 0x64, 0x00, 0x1F])


def _avc_key_nalu() -> bytes:
    return bytes([0x17, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x05, 0x65, 0x88])


def _avc_inter() -> bytes:
    return bytes([0x27, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x41, 0x9A])


def _aac_seq() -> bytes:
    # soundFormat=10 AAC, packetType=0
    return bytes([0xAF, 0x00, 0x12, 0x10])


def _aac_raw() -> bytes:
    return bytes([0xAF, 0x01, 0x21, 0x10, 0x04])


def _flv_stream() -> bytes:
    hdr = FlvHeader().to_bytes() + struct.pack(">I", 0)
    body = b""
    body += _pack_tag(18, 0, b"\x02\x00\x0aonMetaData\x08\x00\x00\x00\x00\x00\x00\x09")
    body += _pack_tag(9, 0, _avc_seq())
    body += _pack_tag(8, 0, _aac_seq())
    body += _pack_tag(9, 40, _avc_key_nalu())
    body += _pack_tag(8, 40, _aac_raw())
    body += _pack_tag(9, 80, _avc_inter())
    body += _pack_tag(8, 80, _aac_raw())
    body += _pack_tag(9, 1000, _avc_key_nalu())
    body += _pack_tag(8, 1000, _aac_raw())
    return hdr + body


class TestFlvParser(unittest.TestCase):
    def test_chunked_parse(self):
        data = _flv_stream()
        parser = FlvTagParser()
        tags = []
        # 分块喂
        for i in range(0, len(data), 17):
            h, t = feed_filter(parser, data[i : i + 17])
            tags.extend(t)
        self.assertGreaterEqual(len(tags), 6)
        self.assertTrue(any(t.is_avc_sequence_header() for t in tags))
        self.assertTrue(any(t.is_aac_sequence_header() for t in tags))
        self.assertTrue(any(t.is_video_keyframe_nalu() for t in tags))


class TestTimestampCorrector(unittest.TestCase):
    def test_zero_base(self):
        c = TimestampCorrector()
        out, disc = c.correct(5000)
        self.assertEqual(out, 0)
        self.assertFalse(disc)
        out2, disc2 = c.correct(5120)
        self.assertEqual(out2, 120)
        self.assertFalse(disc2)


class TestFlvSession(unittest.TestCase):
    def test_keyframe_start_and_cut(self):
        with tempfile.TemporaryDirectory() as td:
            paths = []

            def factory(idx: int) -> str:
                p = os.path.join(td, f"seg{idx}.capture.flv")
                paths.append(p)
                return p

            events = []
            sess = FlvCaptureSession(
                factory,
                on_event=lambda e, d: events.append((e.value, d)),
                require_audio=True,
                require_video=True,
            )
            sess.start()
            # 先喂 inter frame — 不应开段
            pre = FlvHeader().to_bytes() + struct.pack(">I", 0)
            pre += _pack_tag(9, 0, _avc_seq())
            pre += _pack_tag(8, 0, _aac_seq())
            pre += _pack_tag(9, 10, _avc_inter())
            sess.feed(pre)
            self.assertIsNone(sess.current_path)

            # keyframe 后开段
            sess.feed(_pack_tag(9, 40, _avc_key_nalu()) + _pack_tag(8, 40, _aac_raw()))
            self.assertIsNotNone(sess.current_path)
            self.assertTrue(os.path.exists(sess.current_path))

            segs = sess.close("stop")
            self.assertEqual(len(segs), 1)
            self.assertTrue(segs[0].started_with_keyframe)
            self.assertGreater(os.path.getsize(segs[0].path), 0)

    def test_pending_cut_rotates_before_boundary_keyframe_without_overlap_or_gap(self):
        with tempfile.TemporaryDirectory() as td:
            def factory(idx: int) -> str:
                return os.path.join(td, f"pending{idx}.capture.flv")

            sess = FlvCaptureSession(factory, require_audio=True, require_video=True)
            sess.start()
            initial = FlvHeader().to_bytes() + struct.pack(">I", 0)
            initial += _pack_tag(9, 0, _avc_seq())
            initial += _pack_tag(8, 0, _aac_seq())
            initial += _pack_tag(9, 0, _avc_key_nalu())
            initial += _pack_tag(8, 0, _aac_raw())
            initial += _pack_tag(9, 40, _avc_inter())
            sess.feed(initial)
            old_path = sess.current_path
            self.assertIsNotNone(old_path)

            requested_path = os.path.join(td, "manual.capture.flv")
            self.assertTrue(sess.request_cut("manual", next_path=requested_path))
            self.assertTrue(sess.has_pending_cut)
            self.assertFalse(sess.request_cut("duplicate"))

            # Data before the next IDR must continue to the old segment.
            sess.feed(_pack_tag(8, 60, _aac_raw()) + _pack_tag(9, 80, _avc_inter()))
            self.assertEqual(sess.current_path, old_path)

            # The boundary IDR itself starts the new segment.
            sess.feed(_pack_tag(9, 100, _avc_key_nalu()) + _pack_tag(8, 100, _aac_raw()))
            self.assertEqual(sess.current_path, requested_path)
            self.assertFalse(sess.has_pending_cut)
            sess.close("stop")

            def media_tags(path: str):
                parser = FlvTagParser()
                _header, tags = feed_filter(parser, Path(path).read_bytes())
                return [t for t in tags if not t.is_script() and not t.is_avc_sequence_header()
                        and not t.is_aac_sequence_header()]

            old_media = media_tags(old_path)
            new_media = media_tags(requested_path)
            old_keys = [t for t in old_media if t.is_video_keyframe_nalu()]
            new_keys = [t for t in new_media if t.is_video_keyframe_nalu()]
            self.assertEqual(len(old_keys), 1)
            self.assertEqual(len(new_keys), 1)
            self.assertEqual(new_keys[0].timestamp, 0)
            self.assertTrue(any(t.is_video() and t.data == _avc_inter() for t in old_media))
            self.assertFalse(any(t.is_video() and t.data == _avc_inter() for t in new_media))

    def test_reconnect_resets_and_starts_new_segment(self):
        """重连：关旧段、清状态、等新 sequence header + IDR 后开新段。"""
        with tempfile.TemporaryDirectory() as td:
            paths = []

            def factory(idx: int) -> str:
                p = os.path.join(td, f"rec{idx}.capture.flv")
                paths.append(p)
                return p

            events = []
            sess = FlvCaptureSession(
                factory,
                on_event=lambda e, d: events.append((e.value, d)),
                require_audio=True,
                require_video=True,
            )
            sess.start()
            data = FlvHeader().to_bytes() + struct.pack(">I", 0)
            data += _pack_tag(9, 0, _avc_seq())
            data += _pack_tag(8, 0, _aac_seq())
            data += _pack_tag(9, 40, _avc_key_nalu())
            data += _pack_tag(8, 40, _aac_raw())
            sess.feed(data)
            first_path = sess.current_path
            self.assertIsNotNone(first_path)

            # 模拟 HTTP 重连
            sess.reset_for_reconnect("http_disconnect")
            self.assertIsNone(sess.current_path)
            closed = [e for e in events if e[0] == "segment_closed"]
            self.assertGreaterEqual(len(closed), 1)
            self.assertEqual(closed[-1][1].get("close_reason"), "http_disconnect")

            # 重连后只有 inter 帧：不应开段（缺 header/keyframe）
            sess.feed(_pack_tag(9, 100, _avc_inter()))
            self.assertIsNone(sess.current_path)

            # 新流完整 bootstrap + IDR → 新段
            data2 = FlvHeader().to_bytes() + struct.pack(">I", 0)
            data2 += _pack_tag(9, 0, _avc_seq())
            data2 += _pack_tag(8, 0, _aac_seq())
            data2 += _pack_tag(9, 40, _avc_key_nalu())
            sess.feed(data2)
            second_path = sess.current_path
            self.assertIsNotNone(second_path)
            self.assertNotEqual(first_path, second_path)
            self.assertTrue(os.path.exists(first_path))
            self.assertTrue(os.path.exists(second_path))
            segs = sess.close("stop")
            self.assertGreaterEqual(len(segs), 2)

    def test_aac_config_change_forces_new_segment(self):
        """AAC sequence header 变化强制切段，新段等 IDR。"""
        with tempfile.TemporaryDirectory() as td:
            def factory(idx: int) -> str:
                return os.path.join(td, f"aac{idx}.capture.flv")

            events = []
            sess = FlvCaptureSession(
                factory,
                on_event=lambda e, d: events.append((e.value, d)),
                require_audio=True,
                require_video=True,
            )
            sess.start()
            data = FlvHeader().to_bytes() + struct.pack(">I", 0)
            data += _pack_tag(9, 0, _avc_seq())
            data += _pack_tag(8, 0, _aac_seq())  # 0x12 0x10
            data += _pack_tag(9, 40, _avc_key_nalu())
            data += _pack_tag(8, 40, _aac_raw())
            sess.feed(data)
            first = sess.current_path
            self.assertIsNotNone(first)

            # 不同的 AudioSpecificConfig
            new_aac_seq = bytes([0xAF, 0x00, 0x11, 0x90])
            sess.feed(_pack_tag(8, 80, new_aac_seq))
            # 切段后等待 keyframe
            self.assertIsNone(sess.current_path)
            closed = [e for e in events if e[0] == "segment_closed"]
            self.assertTrue(any(c[1].get("close_reason") == "audio_config_change" for c in closed))

            # 新 IDR 开新段
            sess.feed(_pack_tag(9, 120, _avc_key_nalu()) + _pack_tag(8, 120, _aac_raw()))
            second = sess.current_path
            self.assertIsNotNone(second)
            self.assertNotEqual(first, second)
            sess.close("stop")

    def test_duration_limit_cuts_on_keyframe(self):
        """达到时长阈值后，在关键帧处切段。"""
        with tempfile.TemporaryDirectory() as td:
            def factory(idx: int) -> str:
                return os.path.join(td, f"dur{idx}.capture.flv")

            events = []
            sess = FlvCaptureSession(
                factory,
                on_event=lambda e, d: events.append((e.value, d)),
                max_segment_duration_ms=200,
                require_audio=True,
                require_video=True,
            )
            sess.start()
            data = FlvHeader().to_bytes() + struct.pack(">I", 0)
            data += _pack_tag(9, 0, _avc_seq())
            data += _pack_tag(8, 0, _aac_seq())
            data += _pack_tag(9, 0, _avc_key_nalu())
            # 多写一些非关键帧，tag_count 超过阈值检查门槛
            for i in range(1, 15):
                data += _pack_tag(9, i * 20, _avc_inter())
                data += _pack_tag(8, i * 20, _aac_raw())
            sess.feed(data)
            first = sess.current_path
            self.assertIsNotNone(first)
            # 下一个关键帧且 duration >= 200 → 切段
            sess.feed(_pack_tag(9, 300, _avc_key_nalu()) + _pack_tag(8, 300, _aac_raw()))
            closed = [e for e in events if e[0] == "segment_closed"]
            self.assertTrue(
                any(c[1].get("close_reason") == "duration_limit" for c in closed),
                f"events={events}",
            )
            # 切段后等新 keyframe；再喂一个 keyframe 开新段
            sess.feed(_pack_tag(9, 340, _avc_key_nalu()))
            # 可能立刻开新段（当前 tag 是 keyframe）
            segs = sess.close("stop")
            self.assertGreaterEqual(len(segs), 1)
            self.assertTrue(any(s.close_reason == "duration_limit" for s in segs))

    def test_video_config_change_forces_cut(self):
        with tempfile.TemporaryDirectory() as td:
            def factory(idx: int) -> str:
                return os.path.join(td, f"vcc{idx}.capture.flv")

            events = []
            sess = FlvCaptureSession(
                factory,
                on_event=lambda e, d: events.append((e.value, d)),
            )
            sess.start()
            data = FlvHeader().to_bytes() + struct.pack(">I", 0)
            data += _pack_tag(9, 0, _avc_seq())
            data += _pack_tag(8, 0, _aac_seq())
            data += _pack_tag(9, 40, _avc_key_nalu())
            sess.feed(data)
            self.assertIsNotNone(sess.current_path)
            # 不同 SPS/PPS
            new_seq = bytes([0x17, 0x00, 0x00, 0x00, 0x00, 0x01, 0x64, 0x00, 0x28])
            sess.feed(_pack_tag(9, 80, new_seq))
            closed = [e for e in events if e[0] == "segment_closed"]
            self.assertTrue(any(c[1].get("close_reason") == "video_config_change" for c in closed))
            sess.close("stop")


if __name__ == "__main__":
    unittest.main()
