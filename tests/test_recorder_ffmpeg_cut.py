"""FFmpeg cut handoff and asynchronous trailer finalization tests."""
from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock


class _FakeStdin:
    def __init__(self, on_write=None, fail=False):
        self.closed = False
        self.writes = []
        self._on_write = on_write
        self._fail = fail

    def write(self, data):
        if self._fail:
            raise OSError("stdin unavailable")
        self.writes.append(data)
        if self._on_write:
            self._on_write(data)

    def flush(self):
        return None


class _FakeProcess:
    def __init__(self, pid, exit_event=None, stdin=None):
        self.pid = pid
        self.stdin = stdin or _FakeStdin()
        self.exit_event = exit_event or threading.Event()
        self.alive = True
        self.signals = []
        self.terminated = False
        self.killed = False

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        if self.exit_event.wait(timeout):
            self.alive = False
            return 0
        raise subprocess.TimeoutExpired(["ffmpeg"], timeout)

    def send_signal(self, sig):
        self.signals.append(sig)
        self.exit_event.set()

    def terminate(self):
        self.terminated = True
        self.exit_event.set()

    def kill(self):
        self.killed = True
        self.exit_event.set()


class TestFfmpegCutHandoff(unittest.TestCase):
    @staticmethod
    def _make_recorder(old_process, old_path):
        from core.recorder import BiliRecorder

        with mock.patch.object(BiliRecorder, "__init__", lambda self, *a, **k: None):
            rec = BiliRecorder.__new__(BiliRecorder)
        rec.room_id = "100"
        rec.uname = "tester"
        rec.current_title = "title"
        rec.current_session_id = "old-session"
        rec.current_ffmpeg = old_process
        rec.current_save_path = old_path
        rec.current_part_start_time = time.time() - 60
        rec.record_start_time = time.time() - 120
        rec.accumulated_size = 0
        rec.stream_url = "https://example.invalid/live.flv"
        rec._capture_mode = "ffmpeg"
        rec._flv_session = None
        rec._danmaku_recorder = None
        rec._cut_lock = threading.Lock()
        rec._cutting = False
        rec._retiring_ffmpegs = {}
        rec._retiring_ffmpeg_lock = threading.Lock()
        rec._schedule_duration_split = mock.Mock()
        return rec

    def test_cut_stops_old_input_immediately_and_finalizes_in_background(self):
        from core import recorder as recorder_module

        events = []
        old_exit = threading.Event()
        submitted = threading.Event()

        with tempfile.TemporaryDirectory() as td:
            old_path = os.path.join(td, "old.mp4")
            new_path = os.path.join(td, "new.mp4")
            with open(old_path, "wb") as f:
                f.write(b"old-data")

            old_process = _FakeProcess(11, exit_event=old_exit)
            rec = self._make_recorder(old_process, old_path)
            new_process = _FakeProcess(22)

            def start_new(path):
                events.append("start-new")
                self.assertEqual(path, new_path)
                with open(path, "wb") as f:
                    f.write(b"new-data")
                return new_process

            def old_q_written(data):
                events.append("old-q")
                self.assertEqual(data, b"q\n")
                # The active recorder must already be the new process when q is sent.
                self.assertIs(rec.current_ffmpeg, new_process)

            old_process.stdin = _FakeStdin(on_write=old_q_written)
            rec._start_ffmpeg = start_new
            submissions = []

            def submit(path, close_reason="", health="", session_id=None):
                submissions.append((path, close_reason, session_id))
                submitted.set()

            rec._submit_closed_source = submit

            with mock.patch.object(recorder_module, "_build_save_path", return_value=new_path), \
                    mock.patch.object(recorder_module.time, "sleep") as sleep_mock:
                started = time.monotonic()
                rec._do_cut()
                elapsed = time.monotonic() - started

            self.assertLess(elapsed, 1.0)
            sleep_mock.assert_not_called()
            self.assertEqual(events[:2], ["start-new", "old-q"])
            self.assertIs(rec.current_ffmpeg, new_process)
            self.assertEqual(rec.current_save_path, new_path)
            self.assertEqual(old_process.stdin.writes, [b"q\n"])
            self.assertFalse(submitted.is_set(), "old file must not be submitted before ffmpeg exits")
            self.assertEqual(len(rec._retiring_ffmpegs), 1)

            # Simulate the live session changing while the old trailer is still being written.
            rec.current_session_id = "new-session"
            old_exit.set()
            self.assertTrue(submitted.wait(2.0))
            rec._drain_retiring_ffmpegs(timeout=2.0)

            self.assertEqual(submissions, [(old_path, "cut", "old-session")])
            self.assertEqual(rec._retiring_ffmpegs, {})

    def test_submit_closed_source_uses_explicit_session_snapshot(self):
        from core.recorder import BiliRecorder

        with mock.patch.object(BiliRecorder, "__init__", lambda self, *a, **k: None):
            rec = BiliRecorder.__new__(BiliRecorder)
        rec.current_session_id = "new-session"
        rec.room_id = "100"
        rec._artifact_lock = threading.Lock()
        rec._pending_artifacts = 0
        rec._pipeline = mock.Mock()

        rec._submit_closed_source("old.mp4", close_reason="cut", session_id="old-session")

        rec._pipeline.submit_closed_part.assert_called_once_with(
            "old.mp4",
            session_id="old-session",
            room_id="100",
            close_reason="cut",
            health_hint="",
            preferred_final="",
        )

    def test_failed_stdin_q_escalates_without_waiting_first(self):
        from core.recorder import BiliRecorder

        with mock.patch.object(BiliRecorder, "__init__", lambda self, *a, **k: None):
            rec = BiliRecorder.__new__(BiliRecorder)
        process = _FakeProcess(33, stdin=_FakeStdin(fail=True))

        requested = rec._request_ffmpeg_stop(process)
        self.assertFalse(requested)
        rec._wait_for_ffmpeg_exit(process, wait_trailer=0.1, stop_requested=requested)

        self.assertTrue(process.signals)
        self.assertFalse(process.terminated)
        self.assertFalse(process.killed)


class TestSegmentedBridgeSelection(unittest.TestCase):
    def _make_recorder(self):
        from core.recorder import BiliRecorder

        with mock.patch.object(BiliRecorder, "__init__", lambda self, *a, **k: None):
            return BiliRecorder.__new__(BiliRecorder)

    def test_h264_fmp4_uses_single_reader_bridge(self):
        rec = self._make_recorder()
        with mock.patch("core.recorder.get_global_setting", return_value=True):
            self.assertTrue(rec._use_ffmpeg_flv_bridge({"codec": "avc", "format": "fmp4"}))
            self.assertFalse(rec._use_ffmpeg_flv_bridge({"codec": "avc", "format": "flv"}))
            self.assertFalse(rec._use_ffmpeg_flv_bridge({"codec": "hevc", "format": "fmp4"}))

    def test_bridge_cut_only_requests_writer_rotation(self):
        from core import recorder as recorder_module

        rec = self._make_recorder()
        rec.room_id = "100"
        rec.uname = "u"
        rec.current_title = "title"
        rec._capture_mode = "flv_bridge"
        rec._flv_session = mock.Mock()
        rec._flv_session.request_cut.return_value = True
        rec._danmaku_recorder = None
        rec._cut_lock = threading.Lock()
        rec._cutting = False
        rec._schedule_duration_split = mock.Mock()
        rec._start_ffmpeg = mock.Mock(side_effect=AssertionError("must not open another reader"))

        with tempfile.TemporaryDirectory() as td:
            save_path = os.path.join(td, "new.mp4")
            with mock.patch.object(recorder_module, "_build_save_path", return_value=save_path):
                rec._do_cut()

        rec._flv_session.request_cut.assert_called_once_with(
            reason="manual_or_timer",
            next_path=os.path.splitext(save_path)[0] + ".capture.flv",
        )
        rec._start_ffmpeg.assert_not_called()
        rec._schedule_duration_split.assert_called_once()


if __name__ == "__main__":
    unittest.main()
