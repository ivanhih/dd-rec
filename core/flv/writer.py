"""FLV 分段写盘。"""
from __future__ import annotations

import os
import struct
from typing import BinaryIO, Optional

from core.flv.types import FlvHeader, FlvTag, TagType


def _pack_tag(tag: FlvTag, timestamp: Optional[int] = None) -> bytes:
    ts = tag.timestamp if timestamp is None else timestamp
    if ts < 0:
        ts = 0
    ts_low = ts & 0xFFFFFF
    ts_ext = (ts >> 24) & 0xFF
    data_size = len(tag.data)
    header = bytes(
        [
            int(tag.tag_type) & 0xFF,
            (data_size >> 16) & 0xFF,
            (data_size >> 8) & 0xFF,
            data_size & 0xFF,
            (ts_low >> 16) & 0xFF,
            (ts_low >> 8) & 0xFF,
            ts_low & 0xFF,
            ts_ext,
            (tag.stream_id >> 16) & 0xFF,
            (tag.stream_id >> 8) & 0xFF,
            tag.stream_id & 0xFF,
        ]
    )
    body = header + tag.data
    prev = struct.pack(">I", 11 + data_size)
    return body + prev


class FlvSegmentWriter:
    def __init__(self, path: str):
        self.path = path
        self._fp: Optional[BinaryIO] = None
        self.bytes_written = 0
        self.tag_count = 0
        self.closed = False
        self._last_tag_size = 0

    def open(self, header: Optional[FlvHeader] = None):
        if self._fp is not None:
            return
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._fp = open(self.path, "wb")
        h = header or FlvHeader()
        data = h.to_bytes()
        # PreviousTagSize0
        data += struct.pack(">I", 0)
        self._fp.write(data)
        self.bytes_written += len(data)
        self._last_tag_size = 0

    def write_tag(self, tag: FlvTag, timestamp: Optional[int] = None):
        if self._fp is None:
            raise RuntimeError("writer not open")
        if self.closed:
            raise RuntimeError("writer closed")
        blob = _pack_tag(tag, timestamp=timestamp)
        self._fp.write(blob)
        self.bytes_written += len(blob)
        self.tag_count += 1
        self._last_tag_size = 11 + len(tag.data)

    def flush(self):
        if self._fp:
            self._fp.flush()
            try:
                os.fsync(self._fp.fileno())
            except OSError:
                pass

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self._fp:
            try:
                self.flush()
            finally:
                self._fp.close()
                self._fp = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
