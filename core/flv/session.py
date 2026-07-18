"""FLV 捕获会话：缓存 sequence header、关键帧起段、时间戳归零、强制切段。"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Callable, List, Optional

from core.flv.parser import FlvParseError, FlvTagParser, feed_filter
from core.flv.timestamps import TimestampCorrector
from core.flv.types import CaptureIssue, FlvHeader, FlvTag, StreamHeaders, TagType
from core.flv.writer import FlvSegmentWriter

logger = logging.getLogger(__name__)


class CaptureEvent(str, Enum):
    SEGMENT_OPENED = "segment_opened"
    SEGMENT_CLOSED = "segment_closed"
    ISSUE = "issue"
    WAITING_KEYFRAME = "waiting_keyframe"
    RECOVERED = "recovered"


@dataclass
class SegmentInfo:
    path: str
    index: int
    opened_at: float
    closed_at: float = 0.0
    bytes_written: int = 0
    tag_count: int = 0
    health: str = "healthy"  # healthy | degraded | failed
    issues: List[dict] = field(default_factory=list)
    close_reason: str = ""
    first_media_ts_out: int = 0
    last_media_ts_out: int = 0
    duration_ms: int = 0
    started_with_keyframe: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class FlvCaptureSession:
    """消费网络字节流，写出一个或多个 .capture.flv 分段。"""

    def __init__(
        self,
        path_factory: Callable[[int], str],
        *,
        on_event: Optional[Callable[[CaptureEvent, dict], None]] = None,
        max_segment_duration_ms: int = 0,
        max_segment_bytes: int = 0,
        require_audio: bool = True,
        require_video: bool = True,
    ):
        self._path_factory = path_factory
        self._on_event = on_event or (lambda e, d: None)
        self.max_segment_duration_ms = max_segment_duration_ms
        self.max_segment_bytes = max_segment_bytes
        self.require_audio = require_audio
        self.require_video = require_video

        self._parser = FlvTagParser()
        self._headers = StreamHeaders()
        self._writer: Optional[FlvSegmentWriter] = None
        self._ts = TimestampCorrector()
        self._lock = threading.RLock()

        self._segment_index = 0
        self._waiting_keyframe = True
        self._current: Optional[SegmentInfo] = None
        self._closed_segments: List[SegmentInfo] = []
        self._stream_header: Optional[FlvHeader] = None
        self._active = False
        self._total_bytes_in = 0
        self._last_video_ts = -1
        self._last_audio_ts = -1
        # Cuts are committed only at the next video keyframe. The active writer
        # stays open until then so no GOP data is dropped or recorded twice.
        self._pending_cut_reason: Optional[str] = None
        self._pending_next_path: Optional[str] = None

    # ---- public API ----
    def start(self):
        with self._lock:
            self._active = True
            self._reset_stream_state(close_reason="")

    def reset_for_reconnect(self, reason: str = "reconnect"):
        """HTTP 重连后完整重置：关当前段、清 parser/ts/headers，重新等待 AVC/AAC 与 IDR。"""
        with self._lock:
            self._close_writer(reason or "reconnect")
            self._reset_stream_state(close_reason=reason)
            self._active = True

    def _reset_stream_state(self, close_reason: str = ""):
        self._parser.reset()
        self._headers = StreamHeaders()
        self._waiting_keyframe = True
        self._ts.reset()
        self._last_video_ts = -1
        self._last_audio_ts = -1
        self._stream_header = None
        self._pending_cut_reason = None
        self._pending_next_path = None
        if close_reason:
            logger.debug(f"flv session state reset: {close_reason}")

    def feed(self, data: bytes):
        if not data:
            return
        with self._lock:
            if not self._active:
                return
            self._total_bytes_in += len(data)
            try:
                header, tags = feed_filter(self._parser, data)
            except FlvParseError as e:
                self._note_issue("parse_error", 0, str(e))
                self._force_cut("parse_error")
                self._parser.reset()
                return
            if header is not None:
                self._stream_header = header
                # 新 FLV header 出现：视为新流，等待关键帧
                self._waiting_keyframe = True
                self._ts.reset()
            for tag in tags:
                self._handle_tag(tag)

    def request_cut(self, reason: str = "manual", next_path: Optional[str] = None) -> bool:
        """Request a lossless rotation at the next AVC keyframe.

        The current segment remains writable until the boundary keyframe. That
        keyframe is written only to the new segment. ``False`` means there is no
        open segment yet or another rotation is already pending.
        """
        with self._lock:
            if not self._active or self._writer is None:
                return False
            if self._pending_cut_reason is not None:
                return False
            self._pending_cut_reason = reason or "manual"
            self._pending_next_path = next_path or None
            self._emit(
                CaptureEvent.WAITING_KEYFRAME,
                {"reason": self._pending_cut_reason, "path": self.current_path or ""},
            )
            return True

    def close(self, reason: str = "stop") -> List[SegmentInfo]:
        with self._lock:
            self._active = False
            self._pending_cut_reason = None
            self._pending_next_path = None
            self._close_writer(reason)
            return list(self._closed_segments)

    @property
    def current_path(self) -> Optional[str]:
        return self._current.path if self._current else None

    @property
    def closed_segments(self) -> List[SegmentInfo]:
        return list(self._closed_segments)

    @property
    def is_writing(self) -> bool:
        return self._writer is not None and not self._writer.closed

    @property
    def has_pending_cut(self) -> bool:
        with self._lock:
            return self._pending_cut_reason is not None

    # ---- internal ----
    def _emit(self, event: CaptureEvent, payload: dict):
        try:
            self._on_event(event, payload)
        except Exception as e:
            logger.debug(f"capture on_event error: {e}")

    def _note_issue(self, kind: str, ts: int, detail: str = ""):
        issue = CaptureIssue(kind=kind, timestamp_ms=ts, detail=detail)
        if self._current is not None:
            self._current.issues.append(asdict(issue))
            if self._current.health == "healthy":
                self._current.health = "degraded"
        self._emit(CaptureEvent.ISSUE, asdict(issue))

    def _handle_tag(self, tag: FlvTag):
        # 更新 sequence headers
        if tag.is_script():
            self._headers.script = tag
            return
        if tag.is_avc_sequence_header():
            if self._headers.video_seq is not None and self._headers.video_seq.data != tag.data:
                # SPS/PPS 变化 → 强制切段
                self._note_issue("video_config_change", tag.timestamp)
                self._force_cut("video_config_change")
                self._waiting_keyframe = True
                self._ts.reset()
            self._headers.video_seq = tag
            return
        if tag.is_aac_sequence_header():
            if self._headers.audio_seq is not None and self._headers.audio_seq.data != tag.data:
                self._note_issue("audio_config_change", tag.timestamp)
                self._force_cut("audio_config_change")
                self._waiting_keyframe = True
                self._ts.reset()
            self._headers.audio_seq = tag
            return

        # 简单完整性：空 data
        if not tag.data:
            self._note_issue("empty_tag", tag.timestamp, str(tag.tag_type))
            return

        # 时间戳大回退检测（在归零前，用流原始 ts）
        if tag.is_video():
            if self._last_video_ts >= 0 and tag.timestamp + 1000 < self._last_video_ts:
                self._note_issue("video_ts_backward", tag.timestamp)
                self._force_cut("timestamp_discontinuity")
                self._waiting_keyframe = True
                self._ts.reset()
            self._last_video_ts = tag.timestamp
        elif tag.is_audio():
            if self._last_audio_ts >= 0 and tag.timestamp + 1000 < self._last_audio_ts:
                self._note_issue("audio_ts_backward", tag.timestamp)
                self._force_cut("timestamp_discontinuity")
                self._waiting_keyframe = True
                self._ts.reset()
            self._last_audio_ts = tag.timestamp

        # 等关键帧开新段
        # Rotate before the boundary keyframe is written. Every media tag is
        # therefore owned by exactly one segment.
        if tag.is_video_keyframe_nalu() and self._writer is not None:
            rotate_reason = self._pending_cut_reason or self._limit_reason_at_keyframe(tag)
            if rotate_reason:
                self._rotate_at_keyframe(rotate_reason)

        if self._waiting_keyframe:
            if not tag.is_video_keyframe_nalu():
                return
            if not self._headers.has_required(self.require_video, self.require_audio):
                # 缺 sequence header 时继续等（但仍需 keyframe 再检查）
                self._emit(
                    CaptureEvent.WAITING_KEYFRAME,
                    {"has_video_seq": self._headers.video_seq is not None,
                     "has_audio_seq": self._headers.audio_seq is not None},
                )
                # 若只要视频且已有 video_seq，可开
                if self.require_video and self._headers.video_seq is None:
                    return
                if self.require_audio and self._headers.audio_seq is None:
                    # 允许先开视频，音频 header 稍后写入（部分流 header 晚到）
                    pass
            self._open_writer()
            self._write_bootstrap()
            self._waiting_keyframe = False
            self._emit(CaptureEvent.RECOVERED, {"path": self._current.path if self._current else ""})
            # fall through 写该 keyframe

        if self._writer is None:
            return

        out_ts, disc = self._ts.correct(tag.timestamp)
        if disc:
            self._note_issue("timestamp_discontinuity", tag.timestamp)
            self._force_cut("timestamp_discontinuity")
            self._waiting_keyframe = True
            self._ts.reset()
            # 若当前是关键帧，立即作为新段起点
            if tag.is_video_keyframe_nalu():
                self._handle_tag(tag)
            return

        self._writer.write_tag(tag, timestamp=out_ts)
        if self._current:
            if self._current.tag_count == 0:
                self._current.first_media_ts_out = out_ts
                if tag.is_video_keyframe_nalu():
                    self._current.started_with_keyframe = True
            self._current.last_media_ts_out = out_ts
            self._current.tag_count += 1
            self._current.bytes_written = self._writer.bytes_written
            self._current.duration_ms = max(0, out_ts - self._current.first_media_ts_out)

        # 时长/大小限制：在关键帧处切
    def _limit_reason_at_keyframe(self, tag: FlvTag) -> Optional[str]:
        if self._current is None or self._writer is None or self._current.tag_count <= 10:
            return None
        if self.max_segment_duration_ms > 0:
            prospective_duration = self._current.duration_ms
            if self._ts.base is not None:
                prospective_duration = max(prospective_duration, max(0, tag.timestamp - self._ts.base))
            if prospective_duration >= self.max_segment_duration_ms:
                return "duration_limit"
        if self.max_segment_bytes > 0 and self._writer.bytes_written >= self.max_segment_bytes:
            return "size_limit"
        return None

    def _rotate_at_keyframe(self, reason: str):
        """Close the old writer immediately before the boundary keyframe."""
        self._close_writer(reason)
        self._pending_cut_reason = None
        self._waiting_keyframe = True
        self._ts.reset()

    def _open_writer(self):
        if self._writer is not None:
            self._close_writer("rotate")
        self._segment_index += 1
        path = self._pending_next_path or self._path_factory(self._segment_index)
        self._pending_next_path = None
        self._writer = FlvSegmentWriter(path)
        hdr = self._stream_header or FlvHeader(
            has_audio=self._headers.audio_seq is not None or not self.require_audio,
            has_video=self._headers.video_seq is not None or not self.require_video,
        )
        self._writer.open(hdr)
        self._current = SegmentInfo(
            path=path,
            index=self._segment_index,
            opened_at=time.time(),
        )
        self._ts.reset()
        self._emit(CaptureEvent.SEGMENT_OPENED, {"path": path, "index": self._segment_index})

    def _write_bootstrap(self):
        """新段：script + video/audio sequence header，时间戳 0。"""
        assert self._writer is not None
        if self._headers.script is not None:
            self._writer.write_tag(self._headers.script, timestamp=0)
        if self._headers.video_seq is not None:
            self._writer.write_tag(self._headers.video_seq, timestamp=0)
        if self._headers.audio_seq is not None:
            self._writer.write_tag(self._headers.audio_seq, timestamp=0)

    def _force_cut(self, reason: str):
        self._pending_cut_reason = None
        self._pending_next_path = None
        self._close_writer(reason)

    def _close_writer(self, reason: str):
        if self._writer is None:
            return
        try:
            self._writer.close()
        except Exception as e:
            logger.warning(f"close flv writer failed: {e}")
        if self._current is not None:
            self._current.closed_at = time.time()
            self._current.close_reason = reason
            self._current.bytes_written = self._writer.bytes_written if self._writer else self._current.bytes_written
            if self._current.tag_count == 0 or not self._current.started_with_keyframe:
                self._current.health = "failed"
            # sidecar manifest
            try:
                side = self._current.path + ".media.json"
                with open(side, "w", encoding="utf-8") as f:
                    json.dump(self._current.to_dict(), f, ensure_ascii=False, indent=2)
            except Exception as e:
                logger.debug(f"write media.json failed: {e}")
            self._closed_segments.append(self._current)
            self._emit(
                CaptureEvent.SEGMENT_CLOSED,
                self._current.to_dict(),
            )
        self._writer = None
        self._current = None
