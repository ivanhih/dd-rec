"""Tests for the single-reader FFmpeg-to-FLV bridge."""
from __future__ import annotations

import unittest

from core.ffmpeg_flv_source import FfmpegFlvSource


class TestFfmpegFlvSource(unittest.TestCase):
    def test_command_copies_one_input_to_flv_stdout(self):
        source = FfmpegFlvSource(
            "https://example.invalid/index.m3u8",
            on_data=lambda _data: None,
            headers={"User-Agent": "ua", "Referer": "https://ref/"},
            ffmpeg_cmd="ffmpeg-test",
        )
        cmd = source.build_command()
        self.assertEqual(cmd[0], "ffmpeg-test")
        self.assertEqual(cmd.count("-i"), 1)
        self.assertEqual(cmd[cmd.index("-i") + 1], "https://example.invalid/index.m3u8")
        self.assertIn("copy", cmd)
        self.assertEqual(cmd[-1], "pipe:1")
        self.assertEqual(cmd[cmd.index("-f") + 1], "flv")
        self.assertNotIn("mp4", cmd)


class _FakeStdout:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.closed = False

    def read(self, _size):
        return self._chunks.pop(0) if self._chunks else b""

    def close(self):
        self.closed = True


class _ExitedProcess:
    def __init__(self, chunks):
        self.stdout = _FakeStdout(chunks)
        self.returncode = 0

    def poll(self):
        return self.returncode


class TestFfmpegFlvSourceShutdown(unittest.TestCase):
    def test_requested_stop_still_drains_stdout_tail(self):
        received = []
        source = FfmpegFlvSource("unused", on_data=received.append)
        source._process = _ExitedProcess([b"media", b"tail"])
        source._stop.set()

        source._read_loop()

        self.assertEqual(received, [b"media", b"tail"])
        self.assertEqual(source.stats.bytes_read, 9)
        self.assertTrue(source._process.stdout.closed)


if __name__ == "__main__":
    unittest.main()
