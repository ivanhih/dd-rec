"""Pure structural and timing checks for captured media streams."""
from __future__ import annotations

from dataclasses import dataclass
import math
import statistics
from typing import Iterable, Mapping, Optional


@dataclass(frozen=True)
class AudioIntegrityReport:
    fatal_reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    declared_sample_rate: int = 0
    observed_sample_rate: Optional[float] = None
    frame_count: int = 0
    extradata_size: int = 0
    profile: str = ""

    @property
    def ok(self) -> bool:
        return not self.fatal_reasons


def _positive_int(value) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def assess_aac_integrity(
    stream: Mapping,
    frames: Iterable[Mapping],
    *,
    min_timing_intervals: int = 30,
    max_clock_drift: float = 0.03,
) -> AudioIntegrityReport:
    """Assess AAC metadata independently from FFmpeg's decode-success signal."""
    if str(stream.get("codec_name") or "").lower() != "aac":
        return AudioIntegrityReport()

    fatal = []
    warnings = []
    extradata_size = _positive_int(stream.get("extradata_size"))
    if extradata_size == 0:
        fatal.append("missing_aac_extradata")

    declared_rate = _positive_int(stream.get("sample_rate"))
    if declared_rate == 0:
        fatal.append("invalid_audio_sample_rate")

    parsed_frames = []
    for frame in frames:
        samples = _positive_int(frame.get("nb_samples"))
        pts = None
        for raw_pts in (
            frame.get("pts_time"),
            frame.get("best_effort_timestamp_time"),
        ):
            try:
                candidate = float(raw_pts)
            except (TypeError, ValueError):
                continue
            if math.isfinite(candidate):
                pts = candidate
                break
        if samples > 0 and pts is not None:
            parsed_frames.append((pts, samples))

    intervals = []
    for index in range(len(parsed_frames) - 1):
        pts, samples = parsed_frames[index]
        next_pts = parsed_frames[index + 1][0]
        delta = next_pts - pts
        if 0 < delta <= 0.25:
            intervals.append((samples, delta))

    observed_rate = None
    if declared_rate and len(intervals) >= min_timing_intervals:
        median_delta = statistics.median(delta for _samples, delta in intervals)
        stable = [
            (samples, delta)
            for samples, delta in intervals
            if median_delta * 0.5 <= delta <= median_delta * 1.5
        ]
        if len(stable) >= min_timing_intervals:
            elapsed = sum(delta for _samples, delta in stable)
            if elapsed > 0:
                observed_rate = sum(samples for samples, _delta in stable) / elapsed
                drift = abs(observed_rate / declared_rate - 1.0)
                if drift > max_clock_drift:
                    fatal.append("audio_sample_clock_mismatch")
        else:
            warnings.append("insufficient_audio_timing_samples")
    elif declared_rate:
        warnings.append("insufficient_audio_timing_samples")

    return AudioIntegrityReport(
        fatal_reasons=tuple(fatal),
        warnings=tuple(warnings),
        declared_sample_rate=declared_rate,
        observed_sample_rate=observed_rate,
        frame_count=len(parsed_frames),
        extradata_size=extradata_size,
        profile=str(stream.get("profile") or ""),
    )
