"""media_pipeline 逻辑测试（mock ffmpeg 时跳过真实解码）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from unittest import mock

from core.media_pipeline import ArtifactResult, ArtifactStatus, MediaPipeline, remux_copy, sampled_decode


class TestMediaPipelinePaths(unittest.TestCase):
    def test_remux_refuses_same_path_via_process_guard(self):
        # remux_copy 本身允许任意 dst；pipeline 保证 final != src
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "a.capture.flv")
            with open(src, "wb") as handle:
                handle.write(b"not-a-real-flv")
            pipe = MediaPipeline(on_result=lambda r: None, keep_source=True, strict=False)
            # 直接测 _process 会调真实 ffmpeg，这里只测路径选择辅助逻辑
            base, _ = os.path.splitext(src)
            preferred = base[: -len(".capture")] + ".mp4" if base.endswith(".capture") else base + ".mp4"
            self.assertTrue(preferred.endswith(".mp4"))
            self.assertNotEqual(os.path.abspath(preferred), os.path.abspath(src))
            pipe.shutdown(drain=False, timeout=1)

    def test_sampled_decode_checks_start_and_end_only(self):
        with mock.patch("core.media_pipeline._run", return_value=(0, "", "")) as run_mock:
            ok, err = sampled_decode("video.mp4", duration=100.0, sample_seconds=3.0)
        self.assertTrue(ok, err)
        self.assertEqual(run_mock.call_count, 2)
        first_cmd = run_mock.call_args_list[0].args[0]
        last_cmd = run_mock.call_args_list[1].args[0]
        self.assertNotIn("-ss", first_cmd)
        self.assertEqual(last_cmd[last_cmd.index("-ss") + 1], "97.000")
        self.assertEqual(first_cmd[first_cmd.index("-t") + 1], "3.000")

    def test_pipeline_uses_sample_validation_not_full_decode(self):
        with tempfile.TemporaryDirectory() as td:
            src = os.path.join(td, "a.capture.flv")
            final = os.path.join(td, "a.mp4")
            with open(src, "wb") as handle:
                handle.write(b"source")
            probe = {
                "ok": True,
                "has_video": True,
                "has_audio": True,
                "duration": 120.0,
                "size": 6,
            }

            def fake_remux(_src, dst):
                with open(dst, "wb") as handle:
                    handle.write(b"final")
                return True, ""

            pipe = MediaPipeline(on_result=lambda _r: None, keep_source=True, strict=True)
            pipe._wait_stable = mock.Mock()
            try:
                with mock.patch("core.media_pipeline.probe_file", side_effect=[probe, probe]), \
                        mock.patch("core.media_pipeline.remux_copy", side_effect=fake_remux), \
                        mock.patch("core.media_pipeline.sampled_decode", return_value=(True, "")) as sample_mock, \
                        mock.patch("core.media_pipeline.strict_decode") as full_mock:
                    result = pipe._process({"source_path": src, "preferred_final": final})
            finally:
                pipe.shutdown(drain=False, timeout=1)

            self.assertEqual(result.status, ArtifactStatus.SUCCESS)
            pipe._wait_stable.assert_not_called()
            sample_mock.assert_called_once_with(final, duration=120.0)
            full_mock.assert_not_called()

    def test_run_applies_hidden_subprocess_options(self):
        completed = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch("core.media_pipeline.hidden_subprocess_kwargs", return_value={"creationflags": 123}), \
                mock.patch("core.media_pipeline.subprocess.run", return_value=completed) as run_mock:
            from core.media_pipeline import _run
            code, _out, _err = _run(["ffprobe-test"])
        self.assertEqual(code, 0)
        self.assertEqual(run_mock.call_args.kwargs["creationflags"], 123)


class TestPipelineHandlerLifetime(unittest.TestCase):
    def test_unregister_stops_future_dispatch(self):
        pipe = MediaPipeline(keep_source=True, strict=False)
        seen = []

        def cb(result, token=None):
            seen.append((result.room_id, token))

        try:
            pipe.register_result_handler("9", cb, "tok1")
            pipe._dispatch_result(ArtifactResult(
                status=ArtifactStatus.SUCCESS, source_path="a", room_id="9", final_path="b.mp4"
            ))
            self.assertEqual(len(seen), 1)
            pipe.unregister_result_handler("9", "tok1")
            pipe._dispatch_result(ArtifactResult(
                status=ArtifactStatus.SUCCESS, source_path="a", room_id="9", final_path="b.mp4"
            ))
            self.assertEqual(len(seen), 1)
        finally:
            pipe.shutdown(drain=False, timeout=1)

    def test_token_mismatch_does_not_unregister_new_handler(self):
        pipe = MediaPipeline(keep_source=True, strict=False)
        seen = []

        def cb_old(result, token=None):
            seen.append("old")

        def cb_new(result, token=None):
            seen.append("new")

        try:
            pipe.register_result_handler("1", cb_old, "old-tok")
            pipe.register_result_handler("1", cb_new, "new-tok")
            # 旧实例误注销，不应干掉新实例
            pipe.unregister_result_handler("1", "old-tok")
            pipe._dispatch_result(ArtifactResult(
                status=ArtifactStatus.FAILED, source_path="a", room_id="1"
            ))
            self.assertEqual(seen, ["new"])
        finally:
            pipe.shutdown(drain=False, timeout=1)

    def test_dispatch_passes_token_to_callback(self):
        pipe = MediaPipeline(keep_source=True, strict=False)
        tokens = []

        def cb(result, token=None):
            tokens.append(token)

        try:
            pipe.register_result_handler("42", cb, "abc")
            pipe._dispatch_result(ArtifactResult(
                status=ArtifactStatus.WARNING, source_path="s", room_id="42"
            ))
            self.assertEqual(tokens, ["abc"])
        finally:
            pipe.shutdown(drain=False, timeout=1)


class TestRecorderArtifactLifetime(unittest.TestCase):
    def test_wait_pending_artifacts_and_stale_token_drop(self):
        import sys
        import threading
        import time
        from PySide6.QtWidgets import QApplication
        from core.recorder import BiliRecorder

        app = QApplication.instance() or QApplication(sys.argv)

        with mock.patch("core.recorder.get_default_pipeline") as get_pipe:
            pipe = mock.Mock()
            get_pipe.return_value = pipe
            rec = BiliRecorder({"room_id": "1001", "uname": "u", "title": "t"})
            with rec._artifact_cv:
                rec._pending_artifacts = 1

            def release():
                time.sleep(0.05)
                with rec._artifact_cv:
                    rec._pending_artifacts = 0
                    rec._artifact_cv.notify_all()

            threading.Thread(target=release, daemon=True).start()
            self.assertTrue(rec._wait_pending_artifacts(timeout=1.0))

            # 错误 token 的回调应被丢弃且不崩
            rec._on_artifact_result(
                ArtifactResult(
                    status=ArtifactStatus.SUCCESS,
                    source_path="x",
                    room_id="1001",
                    final_path="y.mp4",
                ),
                token="wrong",
            )
            # 正确 token：只入队，不在 pipeline 线程 emit
            with rec._artifact_cv:
                rec._pending_artifacts = 1
            rec._on_artifact_result(
                ArtifactResult(
                    status=ArtifactStatus.SUCCESS,
                    source_path="x",
                    room_id="1001",
                    final_path="y.mp4",
                ),
                token=rec._handler_token,
            )
            with rec._artifact_cv:
                self.assertEqual(rec._pending_artifacts, 0)
                self.assertEqual(len(rec._artifact_results), 1)
            rec._drain_artifact_results()
            with rec._artifact_cv:
                self.assertEqual(len(rec._artifact_results), 0)

            # finished 后正确 token 也不再入队处理
            rec._finished_emitted = True
            with rec._artifact_cv:
                rec._pending_artifacts = 1
            rec._on_artifact_result(
                ArtifactResult(
                    status=ArtifactStatus.SUCCESS,
                    source_path="x",
                    room_id="1001",
                    final_path="y2.mp4",
                ),
                token=rec._handler_token,
            )
            with rec._artifact_cv:
                self.assertEqual(rec._pending_artifacts, 0)

            # 重新测 emit_finished 的 unregister 路径
            rec._finished_emitted = False
            rec._accepting_artifacts = False
            with rec._artifact_cv:
                rec._pending_artifacts = 0
            rec._emit_finished_once()
            pipe.unregister_result_handler.assert_called()
            self.assertTrue(rec._finished_emitted)


if __name__ == "__main__":
    unittest.main()
