"""从字节流增量解析 FLV header 与 tags。"""
from __future__ import annotations

import struct
from typing import List, Optional, Tuple

from core.flv.types import (
    FLV_HEADER_SIZE,
    FLV_PREV_TAG_SIZE_SIZE,
    FLV_TAG_HEADER_SIZE,
    FlvHeader,
    FlvTag,
    TagType,
)


class FlvParseError(Exception):
    pass


class FlvTagParser:
    """状态机解析器：feed(bytes) -> list[FlvHeader|FlvTag]。"""

    def __init__(self, max_tag_size: int = 16 * 1024 * 1024):
        self._buf = bytearray()
        self._header_done = False
        self._expect_prev_tag_size = True  # header 后紧跟 PreviousTagSize0
        self._max_tag_size = max_tag_size
        self.header: Optional[FlvHeader] = None
        self.bytes_consumed = 0
        self.tags_parsed = 0

    def reset(self):
        self._buf.clear()
        self._header_done = False
        self._expect_prev_tag_size = True
        self.header = None
        self.bytes_consumed = 0
        self.tags_parsed = 0

    def feed(self, data: bytes) -> List[object]:
        if not data:
            return []
        self._buf.extend(data)
        out: List[object] = []
        while True:
            item = self._try_parse_one()
            if item is None:
                break
            out.append(item)
        return out

    def _try_parse_one(self) -> Optional[object]:
        if not self._header_done:
            return self._try_header()
        if self._expect_prev_tag_size:
            return self._try_prev_tag_size()
        return self._try_tag()

    def _try_header(self) -> Optional[FlvHeader]:
        if len(self._buf) < FLV_HEADER_SIZE:
            return None
        if self._buf[0:3] != b"FLV":
            # 可能是粘连垃圾：寻找 FLV 签名
            idx = self._buf.find(b"FLV")
            if idx < 0:
                # 保留最后 2 字节以防跨块
                if len(self._buf) > 2:
                    del self._buf[:-2]
                return None
            del self._buf[:idx]
            if len(self._buf) < FLV_HEADER_SIZE:
                return None
        version = self._buf[3]
        flags = self._buf[4]
        data_offset = struct.unpack(">I", self._buf[5:9])[0]
        if data_offset < FLV_HEADER_SIZE:
            raise FlvParseError(f"invalid data_offset={data_offset}")
        # 跳过 header 与可选扩展
        skip = data_offset
        if len(self._buf) < skip:
            return None
        del self._buf[:skip]
        self.bytes_consumed += skip
        self._header_done = True
        self._expect_prev_tag_size = True
        self.header = FlvHeader(
            has_audio=bool(flags & 0x04),
            has_video=bool(flags & 0x01),
            version=version,
        )
        return self.header

    def _try_prev_tag_size(self) -> Optional[object]:
        if len(self._buf) < FLV_PREV_TAG_SIZE_SIZE:
            return None
        # PreviousTagSize 校验宽松：损坏时仍继续
        del self._buf[:FLV_PREV_TAG_SIZE_SIZE]
        self.bytes_consumed += FLV_PREV_TAG_SIZE_SIZE
        self._expect_prev_tag_size = False
        # 不产出对象，继续循环解析 tag
        return self._try_tag() or _Continue()

    def _try_tag(self) -> Optional[FlvTag]:
        if len(self._buf) < FLV_TAG_HEADER_SIZE:
            return None
        tag_type_i = self._buf[0]
        data_size = (self._buf[1] << 16) | (self._buf[2] << 8) | self._buf[3]
        if data_size > self._max_tag_size:
            raise FlvParseError(f"tag data_size too large: {data_size}")
        ts_low = (self._buf[4] << 16) | (self._buf[5] << 8) | self._buf[6]
        ts_ext = self._buf[7]
        timestamp = (ts_ext << 24) | ts_low
        stream_id = (self._buf[8] << 16) | (self._buf[9] << 8) | self._buf[10]
        total = FLV_TAG_HEADER_SIZE + data_size
        # tag 后还有 PreviousTagSize
        need = total + FLV_PREV_TAG_SIZE_SIZE
        if len(self._buf) < need:
            return None
        data = bytes(self._buf[FLV_TAG_HEADER_SIZE:total])
        # 消费 tag + prevTagSize
        del self._buf[:need]
        self.bytes_consumed += need
        self.tags_parsed += 1
        if tag_type_i not in (int(TagType.AUDIO), int(TagType.VIDEO), int(TagType.SCRIPT)):
            # 跳过未知 tag，继续
            return _Continue()
        return FlvTag(
            tag_type=TagType(tag_type_i),
            timestamp=timestamp,
            stream_id=stream_id,
            data=data,
            raw_size=need,
        )


class _Continue:
    """内部哨兵：表示已消费字节但无对外对象。"""


def feed_filter(parser: FlvTagParser, data: bytes) -> Tuple[Optional[FlvHeader], List[FlvTag]]:
    """feed 并过滤内部哨兵。"""
    items = parser.feed(data)
    header = None
    tags: List[FlvTag] = []
    for it in items:
        if isinstance(it, _Continue):
            continue
        if isinstance(it, FlvHeader):
            header = it
        elif isinstance(it, FlvTag):
            tags.append(it)
    return header, tags
