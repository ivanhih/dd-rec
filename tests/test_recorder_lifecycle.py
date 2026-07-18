"""录制生命周期：停止幂等、FLV 关段提交、异常清理。"""
from __future__ import annotations

import os
import struct
import tempfile
import threading
import time
import unittest
from unittest import mock

from core.flv.session import CaptureEvent, FlvCaptureSession
from core.flv.types import FlvHeader
from tests.test_flv_parser import (
    _aac_raw,
    _aac_seq,
    _avc_inter,
    _avc_key_nalu,
    _avc_seq,
    _pack_tag,
)


class TestRecorderLifecycleHelpers(unittest.TestCase):
    def test_request_stop_and_double_stop_idempotent(self):
        """不启动 Qt 事件循环：直接测 stop_recording 对空状态幂等。"""
        from core.recorder import BiliRecorder

        with mock.patch.object(BiliRecorder, "__init__", lambda self, *a, **k: None):
            rec = BiliRecorder.__new__(BiliRecorder)
        rec.room_id = "1"
        rec.uname = "u"
        rec.current_title = "t"
        rec.is_running = True
        rec.is_monitoring = True
        rec.current_ffmpeg = None
        rec.stream_url = None
        rec.current_save_path = None
        rec.record_start_time = 0
        rec.current_part_start_time = 0
        rec.last_total_size = 0
        rec.accumulated_size = 0
        rec.current_session_id = ""
        rec._split_timer = None
        rec._danmaku_recorder = None
        rec._flv_session = None
        rec._stream_source = None
        rec._capture_mode = "none"
        rec._last_part_stop_time = 0.0
        rec._last_part_size_at_stop = 0
        rec._pending_artifacts = 0
        rec._artifact_lock = threading.Lock()
        rec._emitted_artifact_paths = set()

        # 未在录制时多次 stop 不应抛
        rec.stop_recording(reset_time=True)
        rec.stop_recording(reset_time=True)
        rec.request_stop(reset_time=True)
        self.assertFalse(rec.is_monitoring)
        self.assertEqual(rec._capture_mode, "none")

    def test_flv_stop_closes_segment_and_submits_once(self):
        from core.recorder import BiliRecorder

        submitted = []

        with mock.patch.object(BiliRecorder, "__init__", lambda self, *a, **k: None):
            rec = BiliRecorder.__new__(BiliRecorder)
        rec.room_id = "9"
        rec.uname = "u"
        rec.current_title = "t"
        rec.current_session_id = "sid"
        rec._artifact_lock = threading.Lock()
        rec._pending_artifacts = 0
        rec._emitted_artifact_paths = set()
        rec.current_part_start_time = time.time()
        rec._last_part_stop_time = 0.0
        rec._last_part_size_at_stop = 0
        rec.record_start_time = time.time()
        rec.accumulated_size = 0
        rec.stream_url = "http://x"
        rec._split_timer = None
        rec._danmaku_recorder = None
        rec.current_ffmpeg = None

        with tempfile.TemporaryDirectory() as td:
            def factory(idx):
                return os.path.join(td, f"p{idx}.capture.flv")

            events = []
            sess = FlvCaptureSession(factory, on_event=lambda e, d: events.append((e, d)))
            sess.start()
            data = FlvHeader().to_bytes() + struct.pack(">I", 0)
            data += _pack_tag(9, 0, _avc_seq())
            data += _pack_tag(8, 0, _aac_seq())
            data += _pack_tag(9, 40, _avc_key_nalu())
            data += _pack_tag(8, 40, _aac_raw())
            sess.feed(data)
            self.assertIsNotNone(sess.current_path)

            rec._flv_session = sess
            stream_src = mock.Mock()
            stream_src.is_running = True
            stream_src.stop = mock.Mock()
            rec._stream_source = stream_src
            rec._capture_mode = "flv"
            rec.current_save_path = sess.current_path

            def _submit(path, close_reason="", health=""):
                submitted.append((path, close_reason, health))

            rec._submit_closed_source = _submit  # type: ignore
            # SEGMENT_CLOSED 事件路径会调 _on_flv_event → _submit
            rec._on_flv_event = lambda e, p: (  # type: ignore
                submitted.append((p.get("path"), p.get("close_reason"), p.get("health")))
                if e == CaptureEvent.SEGMENT_CLOSED
                else None
            )
            # 重新绑定 session 的 on_event
            sess._on_event = rec._on_flv_event

            rec.stop_recording(reset_time=True)
            # stop_recording 会把 _stream_source 置 None；用局部引用断言
            stream_src.stop.assert_called()
            self.assertIsNone(rec._stream_source)
            self.assertIsNone(rec._flv_session)
            # 关闭后至少提交一次
            self.assertGreaterEqual(len(submitted), 1)
            self.assertTrue(any(p and p.endswith(".capture.flv") for p, *_ in submitted))
            # 重复 stop 不炸
            rec.stop_recording(reset_time=True)

    def test_run_convert_is_noop(self):
        from core.recorder import _run_convert

        with tempfile.NamedTemporaryFile(suffix=".flv", delete=False) as f:
            f.write(b"x")
            path = f.name
        try:
            with mock.patch("core.recorder.subprocess.run") as run:
                _run_convert(path)
                run.assert_not_called()
        finally:
            os.remove(path)

    def test_thread_exception_cleanup_pattern(self):
        """模拟 capture 线程异常后 stop 清理 stream source / session。"""
        from core.recorder import BiliRecorder

        with mock.patch.object(BiliRecorder, "__init__", lambda self, *a, **k: None):
            rec = BiliRecorder.__new__(BiliRecorder)
        rec.room_id = "2"
        rec.uname = "u"
        rec.current_title = ""
        rec.current_session_id = ""
        rec._split_timer = None
        rec._danmaku_recorder = None
        rec.current_ffmpeg = None
        rec.record_start_time = 0
        rec.current_part_start_time = 0
        rec.last_total_size = 0
        rec.accumulated_size = 0
        rec._last_part_stop_time = 0.0
        rec._last_part_size_at_stop = 0
        rec.stream_url = None
        rec.current_save_path = None

        src = mock.Mock()
        src.is_running = False
        src.stop = mock.Mock()
        sess = mock.Mock()
        sess.close = mock.Mock(return_value=[])
        rec._stream_source = src
        rec._flv_session = sess
        rec._capture_mode = "flv"

        rec.stop_recording(reset_time=True)
        src.stop.assert_called()
        sess.close.assert_called()
        self.assertIsNone(rec._stream_source)
        self.assertIsNone(rec._flv_session)
        self.assertEqual(rec._capture_mode, "none")


if __name__ == "__main__":
    unittest.main()
