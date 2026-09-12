"""Disk free-space checks for recording safety."""
from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from typing import Iterable, Optional


@dataclass
class DiskStatus:
    path: str
    free_bytes: int
    total_bytes: int
    min_free_bytes: int

    @property
    def free_gb(self) -> float:
        return self.free_bytes / (1024 ** 3)

    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024 ** 3)

    @property
    def ok(self) -> bool:
        return self.free_bytes >= self.min_free_bytes


@dataclass(frozen=True)
class DiskVolumeSummary:
    """One volume's free-space snapshot for UI banners."""

    path: str
    free_bytes: int
    total_bytes: int
    level: str  # ok | warning | critical | unknown
    min_free_gb: float
    warn_free_gb: float

    @property
    def free_gb(self) -> float:
        return self.free_bytes / (1024 ** 3)

    @property
    def total_gb(self) -> float:
        return self.total_bytes / (1024 ** 3)


def _resolve_existing_dir(path: str) -> str:
    """Walk up until an existing directory is found; fall back to cwd."""
    p = os.path.abspath(path or ".")
    if os.path.isfile(p):
        p = os.path.dirname(p)
    while p and not os.path.isdir(p):
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return p if os.path.isdir(p) else os.path.abspath(".")


def check_disk(path: str, min_free_gb: float = 5.0) -> DiskStatus:
    """Return free/total space for the volume that holds ``path``."""
    target = _resolve_existing_dir(path)
    usage = shutil.disk_usage(target)
    min_free_bytes = max(0, int(float(min_free_gb) * (1024 ** 3)))
    return DiskStatus(
        path=target,
        free_bytes=int(usage.free),
        total_bytes=int(usage.total),
        min_free_bytes=min_free_bytes,
    )


def format_gb(num_bytes: int) -> str:
    return f"{num_bytes / (1024 ** 3):.1f}GB"


def should_block_recording(path: str, min_free_gb: float = 5.0) -> tuple[bool, Optional[DiskStatus]]:
    """True if free space is below threshold (recording should not start/continue)."""
    try:
        status = check_disk(path, min_free_gb=min_free_gb)
    except OSError:
        return False, None
    return (not status.ok), status


def effective_warn_free_gb(min_free_gb: float = 5.0, warn_free_gb: Optional[float] = None) -> float:
    """Warn threshold must be at least the critical threshold."""
    try:
        min_g = max(0.0, float(min_free_gb))
    except (TypeError, ValueError):
        min_g = 5.0
    if warn_free_gb is None:
        return max(15.0, min_g * 3.0)
    try:
        warn_g = float(warn_free_gb)
    except (TypeError, ValueError):
        warn_g = max(15.0, min_g * 3.0)
    if warn_g != warn_g:  # NaN
        warn_g = max(15.0, min_g * 3.0)
    return max(min_g, warn_g)


def disk_level(
    free_bytes: int,
    *,
    min_free_gb: float = 5.0,
    warn_free_gb: Optional[float] = None,
) -> str:
    """Return ok | warning | critical for a free-space value."""
    try:
        free = max(0, int(free_bytes))
    except (TypeError, ValueError):
        return "unknown"
    try:
        min_g = max(0.0, float(min_free_gb))
    except (TypeError, ValueError):
        min_g = 5.0
    warn_g = effective_warn_free_gb(min_g, warn_free_gb)
    free_g = free / (1024 ** 3)
    if free_g < min_g:
        return "critical"
    if free_g < warn_g:
        return "warning"
    return "ok"


def _volume_key(path: str) -> str:
    """Stable volume identity for de-duplication (drive root on Windows)."""
    abs_path = os.path.abspath(path or ".")
    drive, _ = os.path.splitdrive(abs_path)
    if drive:
        return os.path.normcase(drive + os.sep)
    return os.path.normcase(os.path.abspath(os.sep))


def collect_storage_paths(
    rows: Iterable[dict],
    *,
    fallback_dir: str = "",
) -> list[str]:
    """Extract unique existing parent dirs from history rows, with fallback."""
    seen: set[str] = set()
    paths: list[str] = []
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        for key in ("path", "source_path"):
            raw = str(row.get(key) or "").strip()
            if not raw:
                continue
            parent = (
                os.path.dirname(os.path.abspath(raw))
                if not os.path.isdir(raw)
                else os.path.abspath(raw)
            )
            if not parent:
                continue
            norm = os.path.normcase(os.path.normpath(parent))
            if norm in seen:
                continue
            seen.add(norm)
            paths.append(parent)
    if paths:
        return paths
    fb = str(fallback_dir or "").strip()
    if fb:
        return [os.path.abspath(fb)]
    return [os.path.abspath(".")]


def summarize_storage_paths(
    paths: Iterable[str],
    *,
    min_free_gb: float = 5.0,
    warn_free_gb: Optional[float] = None,
) -> list[DiskVolumeSummary]:
    """Check each path's volume once; worst level first."""
    warn_g = effective_warn_free_gb(min_free_gb, warn_free_gb)
    try:
        min_g = max(0.0, float(min_free_gb))
    except (TypeError, ValueError):
        min_g = 5.0

    by_volume: dict[str, DiskVolumeSummary] = {}
    for raw in paths or []:
        path = str(raw or "").strip()
        if not path:
            continue
        try:
            status = check_disk(path, min_free_gb=min_g)
            level = disk_level(
                status.free_bytes,
                min_free_gb=min_g,
                warn_free_gb=warn_g,
            )
            summary = DiskVolumeSummary(
                path=status.path,
                free_bytes=status.free_bytes,
                total_bytes=status.total_bytes,
                level=level,
                min_free_gb=min_g,
                warn_free_gb=warn_g,
            )
        except OSError:
            summary = DiskVolumeSummary(
                path=_resolve_existing_dir(path),
                free_bytes=0,
                total_bytes=0,
                level="unknown",
                min_free_gb=min_g,
                warn_free_gb=warn_g,
            )
        key = _volume_key(summary.path)
        prev = by_volume.get(key)
        if prev is None or _level_rank(summary.level) > _level_rank(prev.level):
            by_volume[key] = summary
        elif prev is not None and summary.free_bytes < prev.free_bytes:
            by_volume[key] = summary

    order = {"critical": 0, "warning": 1, "unknown": 2, "ok": 3}
    return sorted(
        by_volume.values(),
        key=lambda item: (order.get(item.level, 9), item.free_bytes, item.path.lower()),
    )


def _level_rank(level: str) -> int:
    return {"ok": 0, "unknown": 1, "warning": 2, "critical": 3}.get(str(level or ""), 0)
