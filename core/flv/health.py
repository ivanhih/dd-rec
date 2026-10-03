"""Deterministic media-speed and lag decisions; callers supply a monotonic clock."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
from typing import Optional

from core.flv.session import FlvProgressSnapshot


@dataclass(frozen=True)
class FlvHealthConfig:
    enabled: bool = True
    startup_grace_s: float = 45.0
    lag_threshold_s: float = 30.0
    sustain_s: float = 20.0
    recovery_lag_s: float = 15.0
    cooldown_s: float = 60.0
    speed_window_s: float = 20.0
    slow_speed_ratio: float = 0.8
    recovery_speed_ratio: float = 0.95
    recovery_sustain_s: float = 20.0
    stall_s: float = 15.0

    @classmethod
    def from_mapping(cls, raw: Optional[dict] = None) -> "FlvHealthConfig":
        data = dict(raw or {})

        def number(key, default, lo, hi):
            try:
                value = float(data.get(key, default))
            except (TypeError, ValueError):
                value = default
            if not math.isfinite(value):
                value = default
            return max(lo, min(hi, value))

        enabled = data.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("0", "false", "no", "off", "")
        lag = number("lag_threshold_s", 30.0, 5.0, 3600.0)
        slow = number("slow_speed_ratio", 0.8, 0.1, 0.95)
        return cls(
            enabled=bool(enabled),
            startup_grace_s=number("startup_grace_s", 45.0, 0.0, 3600.0),
            lag_threshold_s=lag,
            sustain_s=number("sustain_s", 20.0, 1.0, 3600.0),
            recovery_lag_s=number("recovery_lag_s", 15.0, 1.0, lag),
            cooldown_s=number("cooldown_s", 60.0, 0.0, 3600.0),
            speed_window_s=number("speed_window_s", 20.0, 5.0, 120.0),
            slow_speed_ratio=slow,
            recovery_speed_ratio=number("recovery_speed_ratio", 0.95, slow, 1.0),
            recovery_sustain_s=number("recovery_sustain_s", 20.0, 5.0, 120.0),
            stall_s=number("stall_s", 15.0, 5.0, 120.0),
        )


@dataclass(frozen=True)
class FlvHealthDecision:
    state: str
    lag_ms: int
    media_progress_ms: int
    transport_generation: int
    should_failover: bool
    detail: str
    state_changed: bool = False
    speed_ratio: Optional[float] = None
    speed_ready: bool = False
    recovery_ready: bool = False
    media_idle_s: float = 0.0
    bytes_in: int = 0
    data_idle_s: float = 0.0


class FlvMediaHealth:
    def __init__(self, config: Optional[FlvHealthConfig] = None):
        self.config = config or FlvHealthConfig()
        self.reset()

    def reset(self) -> None:
        self._state = "startup" if self.config.enabled else "disabled"
        self._generation = None
        self._baseline_now = None
        self._baseline_media_ms = 0
        self._max_media_ms = 0
        self._warning_since = None
        self._cooldown_until = 0.0
        self._pending = False
        self._last_lag_ms = 0
        self._samples = deque()
        self._last_progress_at = None
        self._last_bytes_at = None
        self._last_bytes = 0
        self._seen_progress = False

    def complete_failover(self, now: float, succeeded: bool) -> None:
        self._pending = False
        self._warning_since = None
        self._cooldown_until = float(now) + self.config.cooldown_s if succeeded else 0.0
        # Keep the baseline: successful speed validation must not erase old lag.
        self._state = "healthy" if succeeded else "degraded"

    def _media_at(self, at: float) -> float:
        start_now, start_media = self._samples[0]
        for end_now, end_media in list(self._samples)[1:]:
            if at <= end_now:
                fraction = max(0.0, (at - start_now) / (end_now - start_now))
                return start_media + (end_media - start_media) * fraction
            start_now, start_media = end_now, end_media
        return float(start_media)

    def _window(self, now: float, seconds: float):
        start = max(self._samples[0][0], now - seconds)
        elapsed = now - start
        ready = elapsed >= seconds - 1e-6 and len(self._samples) >= 2
        ratio = (self._media_at(now) - self._media_at(start)) / (elapsed * 1000.0) if elapsed > 0 else None
        return ratio, ready

    def _steady_recovery(self, now: float) -> bool:
        # Every subwindow must carry media. Two cache bursts cannot hide a freeze
        # inside an otherwise convincing full-window average.
        remaining = self.config.recovery_sustain_s
        while remaining > 1e-6:
            seconds = min(5.0, remaining)
            ratio, ready = self._window(now, seconds)
            if not ready or not self.config.slow_speed_ratio <= ratio <= 2.0:
                return False
            now -= seconds
            remaining -= seconds
        return True

    def observe(self, now: float, progress: FlvProgressSnapshot) -> FlvHealthDecision:
        now = float(now)
        gen = int(progress.transport_generation)
        media_in = max(0, int(progress.media_progress_ms))
        byte_count = max(0, int(getattr(progress, "bytes_in", 0)))
        prev = self._state
        if self._generation != gen:
            self._generation = gen
            self._baseline_now = now
            self._baseline_media_ms = media_in
            self._max_media_ms = media_in
            self._warning_since = None
            self._pending = False
            self._samples.clear()
            self._last_progress_at = now
            self._last_bytes_at = now
            self._last_bytes = byte_count
            self._seen_progress = bool(progress.has_media)
        if self._samples:
            now = max(now, self._samples[-1][0])
        media = max(self._max_media_ms, media_in)
        if media > self._max_media_ms:
            self._last_progress_at = now
            self._seen_progress = True
        if byte_count > self._last_bytes:
            self._last_bytes_at = now
        self._last_bytes = byte_count
        self._max_media_ms = media
        if self._samples and self._samples[-1][0] == now:
            self._samples[-1] = (now, media)
        else:
            self._samples.append((now, media))
        keep = max(self.config.speed_window_s, self.config.recovery_sustain_s)
        while len(self._samples) > 2 and self._samples[1][0] <= now - keep:
            self._samples.popleft()
        ratio, ready = self._window(now, self.config.speed_window_s)
        recovery_ratio, recovery_window = self._window(now, self.config.recovery_sustain_s)
        media_idle = max(0.0, now - self._last_progress_at)
        data_idle = max(0.0, now - self._last_bytes_at)
        elapsed = max(0.0, now - self._baseline_now)
        lag = max(0, round(elapsed * 1000.0) - (media - self._baseline_media_ms))
        self._last_lag_ms = lag
        recovery_ready = bool(
            recovery_window and len(self._samples) >= 3 and progress.has_media
            and self.config.recovery_speed_ratio <= recovery_ratio <= 2.0
            and media_idle < min(5.0, self.config.stall_s)
            and self._steady_recovery(now)
        )
        stalled = self._seen_progress and media_idle >= self.config.stall_s
        slow = ready and ratio < self.config.slow_speed_ratio and lag >= self.config.lag_threshold_s * 1000
        caught_up = lag <= self.config.recovery_lag_s * 1000
        realtime = ready and ratio >= self.config.recovery_speed_ratio and not stalled
        cooldown = now < self._cooldown_until
        startup = elapsed < self.config.startup_grace_s
        should_failover = False
        speed_text = "warming" if ratio is None else f"{ratio:.2f}x"
        detail = f"lag={lag / 1000:.1f}s speed={speed_text} media_idle={media_idle:.1f}s"
        if not self.config.enabled:
            self._state = "disabled"
        elif self._pending:
            self._state = "failover_pending"
        elif stalled or (slow and (not startup or cooldown)):
            self._state = "degraded"
            if self._warning_since is None:
                self._warning_since = now
            sustained = now - self._warning_since
            detail += f" sustained={sustained:.0f}/{self.config.sustain_s:.0f}s"
            if not cooldown and (stalled or sustained >= self.config.sustain_s):
                self._pending = True
                self._state = "failover_pending"
                should_failover = True
        elif startup and not cooldown:
            self._state = "startup"
            self._warning_since = None
        elif caught_up or realtime:
            self._state = "cooldown" if cooldown else "healthy"
            self._warning_since = None
        else:
            # Hysteresis band is observable, but old lag alone cannot trigger a switch.
            self._state = "degraded"
            self._warning_since = None
        if cooldown:
            detail += f" cooldown={self._cooldown_until - now:.0f}s"
        return FlvHealthDecision(
            state=self._state, lag_ms=lag, media_progress_ms=media,
            transport_generation=gen, should_failover=should_failover,
            detail=detail, state_changed=prev != self._state,
            speed_ratio=ratio, speed_ready=ready, recovery_ready=recovery_ready,
            media_idle_s=media_idle, bytes_in=byte_count, data_idle_s=data_idle,
        )
