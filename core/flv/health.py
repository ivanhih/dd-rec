"""Pure media-lag health detector for native FLV capture.

Caller supplies monotonic time and progress snapshots. This module never
touches network, filesystem, Qt, or wall clocks.
"""
from __future__ import annotations

from dataclasses import dataclass
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

    @classmethod
    def from_mapping(cls, raw: Optional[dict] = None) -> "FlvHealthConfig":
        data = dict(raw or {})

        def _num(key: str, default: float, lo: float, hi: float) -> float:
            try:
                val = float(data.get(key, default))
            except (TypeError, ValueError):
                val = default
            if val != val:  # NaN
                val = default
            return max(lo, min(hi, val))

        enabled = data.get("enabled", True)
        if isinstance(enabled, str):
            enabled = enabled.strip().lower() not in ("0", "false", "no", "off", "")
        else:
            enabled = bool(enabled)

        lag = _num("lag_threshold_s", 30.0, 5.0, 3600.0)
        recovery = _num("recovery_lag_s", 15.0, 1.0, lag)
        return cls(
            enabled=enabled,
            startup_grace_s=_num("startup_grace_s", 45.0, 0.0, 3600.0),
            lag_threshold_s=lag,
            sustain_s=_num("sustain_s", 20.0, 1.0, 3600.0),
            recovery_lag_s=recovery,
            cooldown_s=_num("cooldown_s", 60.0, 0.0, 3600.0),
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


class FlvMediaHealth:
    """Deterministic lag state machine for transport-generation media progress."""

    def __init__(self, config: Optional[FlvHealthConfig] = None):
        self.config = config or FlvHealthConfig()
        self._state = "disabled" if not self.config.enabled else "startup"
        self._baseline_now: Optional[float] = None
        self._baseline_media_ms = 0
        self._max_media_ms = 0
        self._generation: Optional[int] = None
        self._warning_since: Optional[float] = None
        self._edge_armed = True
        self._cooldown_until = 0.0
        self._pending = False
        self._last_lag_ms = 0
        self._startup_since: Optional[float] = None

    def reset(self) -> None:
        self._state = "disabled" if not self.config.enabled else "startup"
        self._baseline_now = None
        self._baseline_media_ms = 0
        self._max_media_ms = 0
        self._generation = None
        self._warning_since = None
        self._edge_armed = True
        self._cooldown_until = 0.0
        self._pending = False
        self._last_lag_ms = 0
        self._startup_since = None

    def complete_failover(self, now: float, succeeded: bool) -> None:
        now = float(now)
        self._pending = False
        self._edge_armed = False
        self._warning_since = None
        if succeeded:
            self._cooldown_until = now + max(0.0, float(self.config.cooldown_s))
            self._state = "cooldown" if self.config.enabled else "disabled"
            # New generation will rebuild baseline on next observe.
            self._baseline_now = None
        else:
            # Require a fresh sustain window before another edge.
            self._cooldown_until = 0.0
            self._state = "degraded" if self.config.enabled else "disabled"
            self._warning_since = now
            self._edge_armed = True

    def observe(self, now: float, progress: FlvProgressSnapshot) -> FlvHealthDecision:
        now = float(now)
        gen = int(getattr(progress, "transport_generation", 0) or 0)
        media_in = max(0, int(getattr(progress, "media_progress_ms", 0) or 0))
        prev_state = self._state

        if not self.config.enabled:
            self._state = "disabled"
            self._last_lag_ms = 0
            return FlvHealthDecision(
                state="disabled",
                lag_ms=0,
                media_progress_ms=media_in,
                transport_generation=gen,
                should_failover=False,
                detail="disabled",
                state_changed=prev_state != "disabled",
            )

        if self._generation is None or gen != self._generation:
            self._generation = gen
            self._baseline_now = now
            self._baseline_media_ms = media_in
            self._max_media_ms = media_in
            self._warning_since = None
            self._edge_armed = True
            self._pending = False
            self._startup_since = now
            if now < self._cooldown_until:
                self._state = "cooldown"
            else:
                self._state = "startup"
                self._cooldown_until = 0.0

        # Defensive clamp: media progress must never go backwards within a generation.
        media = max(self._max_media_ms, media_in)
        self._max_media_ms = media

        if self._baseline_now is None:
            self._baseline_now = now
            self._baseline_media_ms = media

        wall_elapsed_ms = max(0, int(round((now - self._baseline_now) * 1000.0)))
        media_elapsed_ms = max(0, media - self._baseline_media_ms)
        lag_ms = max(0, wall_elapsed_ms - media_elapsed_ms)
        self._last_lag_ms = lag_ms
        lag_s = lag_ms / 1000.0

        in_cooldown = now < self._cooldown_until
        startup_elapsed = 0.0 if self._startup_since is None else max(0.0, now - self._startup_since)
        in_startup = startup_elapsed < float(self.config.startup_grace_s)

        should_failover = False
        detail = f"lag={lag_s:.1f}s"

        if in_cooldown:
            self._state = "cooldown"
            remain = max(0.0, self._cooldown_until - now)
            detail = f"cooldown {remain:.0f}s remaining; lag={lag_s:.1f}s"
            # Still track degraded signal but never edge-trigger during cooldown.
            if lag_s >= float(self.config.lag_threshold_s):
                detail = f"media lag {lag_s:.0f}s during cooldown ({remain:.0f}s left)"
        elif in_startup:
            self._state = "startup"
            remain = max(0.0, float(self.config.startup_grace_s) - startup_elapsed)
            detail = f"startup grace {remain:.0f}s; lag={lag_s:.1f}s"
        else:
            # Post-startup hysteresis.
            if self._state in ("startup", "healthy", "cooldown") and lag_s >= float(self.config.lag_threshold_s):
                self._state = "degraded"
                self._warning_since = now
            elif self._state == "degraded":
                if lag_s <= float(self.config.recovery_lag_s):
                    self._state = "healthy"
                    self._warning_since = None
                    self._edge_armed = True
                # 90-120s band keeps degraded.
            elif self._state == "failover_pending":
                # Stay pending until complete_failover is called.
                pass
            else:
                if lag_s >= float(self.config.lag_threshold_s):
                    self._state = "degraded"
                    if self._warning_since is None:
                        self._warning_since = now
                else:
                    self._state = "healthy"
                    self._warning_since = None

            if self._state == "degraded" and self._warning_since is not None:
                sustained = max(0.0, now - self._warning_since)
                detail = (
                    f"media lag {lag_s:.0f}s, sustained {sustained:.0f}/"
                    f"{float(self.config.sustain_s):.0f}s"
                )
                if (
                    sustained >= float(self.config.sustain_s)
                    and self._edge_armed
                    and not self._pending
                ):
                    should_failover = True
                    self._pending = True
                    self._edge_armed = False
                    self._state = "failover_pending"
                    detail = f"failover edge; media lag {lag_s:.0f}s sustained {sustained:.0f}s"
            elif self._state == "failover_pending":
                sustained = 0.0 if self._warning_since is None else max(0.0, now - self._warning_since)
                detail = f"failover pending; media lag {lag_s:.0f}s sustained {sustained:.0f}s"
            elif self._state == "healthy":
                detail = f"healthy lag={lag_s:.1f}s"

        return FlvHealthDecision(
            state=self._state,
            lag_ms=lag_ms,
            media_progress_ms=media,
            transport_generation=gen,
            should_failover=should_failover,
            detail=detail,
            state_changed=prev_state != self._state,
        )
