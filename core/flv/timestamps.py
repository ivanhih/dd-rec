"""时间戳单调与归零。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TimestampCorrector:
    """将流时间戳平移到段内从 0 开始，并抑制小幅回退。"""

    base: int | None = None
    last_out: int = -1
    max_backward_ms: int = 1000  # 超过则视为 discontinuity

    def reset(self):
        self.base = None
        self.last_out = -1

    def correct(self, ts: int) -> tuple[int, bool]:
        """返回 (out_ts, discontinuity)。

        discontinuity=True 表示检测到大回退，调用方应切段。
        """
        if self.base is None:
            self.base = ts
            out = 0
            self.last_out = 0
            return out, False

        out = ts - self.base
        if out < 0:
            # 小幅回退：夹到 last
            if -out <= self.max_backward_ms:
                out = max(0, self.last_out)
                return out, False
            return out, True

        if out < self.last_out:
            delta = self.last_out - out
            if delta <= self.max_backward_ms:
                out = self.last_out
                return out, False
            return out, True

        self.last_out = out
        return out, False
