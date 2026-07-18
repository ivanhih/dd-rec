"""FLV 基础类型与常量。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
from typing import Optional


class TagType(IntEnum):
    AUDIO = 8
    VIDEO = 9
    SCRIPT = 18


class SoundFormat(IntEnum):
    AAC = 10


class AACPacketType(IntEnum):
    SEQUENCE_HEADER = 0
    RAW = 1


class AVCPacketType(IntEnum):
    SEQUENCE_HEADER = 0
    NALU = 1
    END_OF_SEQUENCE = 2


class FrameType(IntEnum):
    KEY = 1
    INTER = 2
    DISPOSABLE = 3
    GENERATED = 4
    INFO = 5


class CodecID(IntEnum):
    AVC = 7
    HEVC = 12  # 部分实现使用 12；B 站也可能用增强头


FLV_HEADER_SIZE = 9
FLV_PREV_TAG_SIZE_SIZE = 4
FLV_TAG_HEADER_SIZE = 11


@dataclass
class FlvHeader:
    has_audio: bool = True
    has_video: bool = True
    version: int = 1

    def to_bytes(self) -> bytes:
        flags = 0
        if self.has_audio:
            flags |= 0x04
        if self.has_video:
            flags |= 0x01
        # signature FLV + version + flags + dataOffset(9)
        return b"FLV" + bytes([self.version, flags, 0, 0, 0, 9])


@dataclass
class FlvTag:
    tag_type: TagType
    timestamp: int  # ms, 32-bit extended
    stream_id: int
    data: bytes
    raw_size: int = 0  # 含 tag header + data + prevTagSize 的字节数（用于统计）

    @property
    def data_size(self) -> int:
        return len(self.data)

    def is_script(self) -> bool:
        return self.tag_type == TagType.SCRIPT

    def is_audio(self) -> bool:
        return self.tag_type == TagType.AUDIO

    def is_video(self) -> bool:
        return self.tag_type == TagType.VIDEO

    # ---- video helpers ----
    def video_frame_type(self) -> Optional[int]:
        if not self.is_video() or not self.data:
            return None
        return (self.data[0] >> 4) & 0x0F

    def video_codec_id(self) -> Optional[int]:
        if not self.is_video() or not self.data:
            return None
        return self.data[0] & 0x0F

    def avc_packet_type(self) -> Optional[int]:
        if not self.is_video() or len(self.data) < 2:
            return None
        # enhanced HEVC 等不按此解析；AVC 标准布局
        return self.data[1]

    def is_avc_sequence_header(self) -> bool:
        if not self.is_video() or len(self.data) < 5:
            return False
        if self.video_codec_id() != CodecID.AVC:
            return False
        return self.avc_packet_type() == AVCPacketType.SEQUENCE_HEADER

    def is_video_keyframe_nalu(self) -> bool:
        """AVC keyframe NALU（非 sequence header）。"""
        if not self.is_video() or len(self.data) < 5:
            return False
        if self.video_frame_type() != FrameType.KEY:
            return False
        if self.video_codec_id() != CodecID.AVC:
            # 非 AVC：把 keyframe 当边界（尽力）
            return self.avc_packet_type() != AVCPacketType.SEQUENCE_HEADER
        return self.avc_packet_type() == AVCPacketType.NALU

    # ---- audio helpers ----
    def sound_format(self) -> Optional[int]:
        if not self.is_audio() or not self.data:
            return None
        return (self.data[0] >> 4) & 0x0F

    def aac_packet_type(self) -> Optional[int]:
        if not self.is_audio() or len(self.data) < 2:
            return None
        if self.sound_format() != SoundFormat.AAC:
            return None
        return self.data[1]

    def is_aac_sequence_header(self) -> bool:
        return self.aac_packet_type() == AACPacketType.SEQUENCE_HEADER


@dataclass
class StreamHeaders:
    """缓存的初始化 tags（原始 tag，时间戳稍后重写）。"""
    script: Optional[FlvTag] = None
    video_seq: Optional[FlvTag] = None
    audio_seq: Optional[FlvTag] = None

    def has_required(self, need_video: bool = True, need_audio: bool = True) -> bool:
        if need_video and self.video_seq is None:
            return False
        if need_audio and self.audio_seq is None:
            return False
        return True

    def copy(self) -> "StreamHeaders":
        return StreamHeaders(
            script=self.script,
            video_seq=self.video_seq,
            audio_seq=self.audio_seq,
        )


@dataclass
class CaptureIssue:
    kind: str
    timestamp_ms: int = 0
    detail: str = ""
    extras: dict = field(default_factory=dict)
