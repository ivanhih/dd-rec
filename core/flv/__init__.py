"""原生 FLV 录制管线。

按 tag 解析 HTTP-FLV，缓存 sequence header，在关键帧边界切段，
时间戳归零后写出可独立解码的 capture 文件。
"""

from core.flv.types import FlvHeader, FlvTag, TagType
from core.flv.parser import FlvTagParser
from core.flv.writer import FlvSegmentWriter
from core.flv.session import FlvCaptureSession, CaptureEvent, FlvProgressSnapshot
from core.flv.health import FlvHealthConfig, FlvHealthDecision, FlvMediaHealth

__all__ = [
    "FlvHeader",
    "FlvTag",
    "TagType",
    "FlvTagParser",
    "FlvSegmentWriter",
    "FlvCaptureSession",
    "CaptureEvent",
    "FlvProgressSnapshot",
    "FlvHealthConfig",
    "FlvHealthDecision",
    "FlvMediaHealth",
]
