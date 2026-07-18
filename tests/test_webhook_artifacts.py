"""Webhook 交付门禁：仅验证成功的最终 MP4 才发 FileClosed。"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from core.media_pipeline import ArtifactResult, ArtifactStatus


class _FakeRecorder:
    """最小替身，直接调用 BiliRecorder._on_artifact_result 逻辑。"""

    def __init__(self):
        import threading
        from PySide6.QtCore import QObject, Signal

        # 避免完整 BiliRecorder 初始化；绑定方法到真实类实现
        from core.recorder import BiliRecorder

        self.room_id = "12345"
        self.uname = "tester"
        self.current_title = "t"
        self.current_session_id = "sess-1"
        self._pending_artifacts = 1
        self._artifact_lock = threading.Lock()
        self._emitted_artifact_paths = set()
        self.cut_completed_calls = []
        self.cut_failed_calls = []

        class _Sig:
            def __init__(self, bag, key):
                self.bag = bag
                self.key = key

            def emit(self, *args):
                self.bag.append(args)

        self.cut_completed = _Sig(self.cut_completed_calls, "ok")
        self.cut_failed = _Sig(self.cut_failed_calls, "fail")
        self._on_artifact_result = BiliRecorder._on_artifact_result.__get__(self, BiliRecorder)


class TestWebhookArtifacts(unittest.TestCase):
    def test_success_sends_final_mp4_not_capture(self):
        rec = _FakeRecorder()
        with mock.patch("core.recorder._send_webhooks") as send, mock.patch(
            "core.recorder.get_global_setting", return_value=True
        ):
            final = os.path.join(tempfile.gettempdir(), "seg.mp4")
            result = ArtifactResult(
                status=ArtifactStatus.SUCCESS,
                source_path=final.replace(".mp4", ".capture.flv"),
                final_path=final,
                size=100,
                duration=12.5,
                session_id="sess-1",
                room_id="12345",
                health="healthy",
            )
            rec._on_artifact_result(result)
            self.assertEqual(send.call_count, 1)
            args, kwargs = send.call_args
            self.assertEqual(args[0], "recording_split")
            extra = args[4]
            self.assertEqual(extra["file"], final)
            self.assertFalse(str(extra["file"]).endswith(".capture.flv"))
            self.assertEqual(len(rec.cut_completed_calls), 1)

    def test_failed_remux_does_not_send(self):
        rec = _FakeRecorder()
        with mock.patch("core.recorder._send_webhooks") as send, mock.patch(
            "core.recorder.get_global_setting", return_value=True
        ):
            result = ArtifactResult(
                status=ArtifactStatus.FAILED,
                source_path="a.capture.flv",
                final_path="",
                error="remux_failed:boom",
                health="failed",
            )
            rec._on_artifact_result(result)
            send.assert_not_called()
            self.assertEqual(len(rec.cut_failed_calls), 1)

    def test_red_or_warning_not_success_webhook(self):
        rec = _FakeRecorder()
        with mock.patch("core.recorder._send_webhooks") as send, mock.patch(
            "core.recorder.get_global_setting", return_value=True
        ):
            result = ArtifactResult(
                status=ArtifactStatus.WARNING,
                source_path="a.capture.flv",
                final_path="a.mp4",
                warnings=["final_no_audio"],
                health="degraded",
            )
            rec._on_artifact_result(result)
            send.assert_not_called()

    def test_duplicate_finalize_does_not_resend(self):
        rec = _FakeRecorder()
        with mock.patch("core.recorder._send_webhooks") as send, mock.patch(
            "core.recorder.get_global_setting", return_value=True
        ):
            final = os.path.join(tempfile.gettempdir(), "dup.mp4")
            result = ArtifactResult(
                status=ArtifactStatus.SUCCESS,
                source_path="dup.capture.flv",
                final_path=final,
                size=1,
                duration=1.0,
                health="healthy",
            )
            rec._pending_artifacts = 2
            rec._on_artifact_result(result)
            rec._on_artifact_result(result)
            self.assertEqual(send.call_count, 1)

    def test_emit_last_part_skips_when_only_verified(self):
        from core.recorder import _emit_last_part_file_closed_webhook

        class R:
            current_save_path = None
            _last_part_size_at_stop = 0
            _last_part_stop_time = 0
            current_part_start_time = 0
            record_start_time = 0
            room_id = "1"
            uname = "u"
            current_title = "t"
            current_session_id = "s"

        with tempfile.NamedTemporaryFile(suffix=".capture.flv", delete=False) as f:
            f.write(b"x" * 10)
            path = f.name
        try:
            r = R()
            r.current_save_path = path
            with mock.patch("core.recorder._send_webhooks") as send, mock.patch(
                "core.recorder.get_global_setting", return_value=True
            ):
                _emit_last_part_file_closed_webhook(r)
                send.assert_not_called()
        finally:
            try:
                os.remove(path)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
