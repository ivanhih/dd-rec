from __future__ import annotations

import os
import tempfile
import unittest

from ui.log_viewer_dialog import (
    FILTER_ERROR,
    FILTER_INFO,
    FILTER_WARNING,
    filter_log_lines,
    read_log_tail,
)


SAMPLE = """\
2026-07-18 16:03:42 [INFO] root | started
2026-07-18 16:03:43 [WARNING] core.power | battery low
2026-07-18 16:03:44 [ERROR] root | boom
Traceback (most recent call last):
  File "main.py", line 1, in <module>
    raise RuntimeError("x")
RuntimeError: x
2026-07-18 16:03:45 [INFO] root | recovered
2026-07-18 16:03:46 [CRITICAL] root | dead
"""


class TestLogViewer(unittest.TestCase):
    def test_missing_log_is_friendly(self):
        text, truncated = read_log_tail(os.path.join(tempfile.gettempdir(), "ddrec-missing-log.txt"))
        self.assertIn("尚未生成", text)
        self.assertFalse(truncated)

    def test_large_log_reads_tail(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bilirec.log")
            with open(path, "w", encoding="utf-8") as f:
                for i in range(200):
                    f.write(f"line-{i:03d}-" + "x" * 40 + "\n")
            text, truncated = read_log_tail(path, max_bytes=4096)
            self.assertTrue(truncated)
            self.assertIn("line-199", text)
            self.assertNotIn("line-000", text)

    def test_filter_info_only(self):
        out = filter_log_lines(SAMPLE, FILTER_INFO)
        self.assertIn("[INFO] root | started", out)
        self.assertIn("[INFO] root | recovered", out)
        self.assertNotIn("[WARNING]", out)
        self.assertNotIn("[ERROR]", out)
        self.assertNotIn("Traceback", out)

    def test_filter_error_keeps_stack_continuation(self):
        out = filter_log_lines(SAMPLE, FILTER_ERROR)
        self.assertIn("[ERROR] root | boom", out)
        self.assertIn("Traceback (most recent call last):", out)
        self.assertIn("RuntimeError: x", out)
        self.assertIn("[CRITICAL] root | dead", out)
        self.assertNotIn("[INFO]", out)
        self.assertNotIn("[WARNING]", out)

    def test_filter_warning(self):
        out = filter_log_lines(SAMPLE, FILTER_WARNING)
        self.assertEqual(out.strip(), "2026-07-18 16:03:43 [WARNING] core.power | battery low")

    def test_filter_all_is_passthrough(self):
        self.assertEqual(filter_log_lines(SAMPLE, "ALL"), SAMPLE)


if __name__ == "__main__":
    unittest.main()
