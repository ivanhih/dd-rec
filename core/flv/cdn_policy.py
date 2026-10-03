"""CDN failure memory and ranking, independent of networking and Qt."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
from urllib.parse import urlparse


def candidate_host(candidate: dict) -> str:
    return urlparse(candidate.get("url") or candidate.get("host") or "").hostname or ""


class HostExperience:
    """Short-lived ranking hints; one room cannot globally quarantine a host."""

    def __init__(self):
        self._failures = deque(maxlen=256)
        self._lock = threading.Lock()

    def failed(self, room: str, host: str, now: float):
        if host:
            with self._lock:
                self._failures.append((room, host, now))

    def penalty(self, host: str, now: float) -> int:
        with self._lock:
            rooms = {room for room, seen_host, at in self._failures
                     if seen_host == host and 0 <= now - at < 300.0}
        return int(len(rooms) >= 2)


HOST_EXPERIENCE = HostExperience()
# API lookups run on recorder threads, with a bounded process-wide concurrency.
REFRESH_SLOTS = threading.BoundedSemaphore(2)


@dataclass
class EndpointHealth:
    failures: int = 0
    until: float = 0.0
    failed_at: float = 0.0
    stable_at: float | None = None


class CdnPolicy:
    def __init__(self, room: str, host_experience: HostExperience | None = None):
        self.room = str(room)
        self._endpoints: dict[str, EndpointHealth] = {}
        self._slow_hosts: dict[str, float] = {}
        self._hosts = host_experience or HOST_EXPERIENCE

    def note_failure(self, candidate: dict, now: float, base_seconds: float,
                     reason: str) -> float:
        endpoint = str(candidate.get("endpoint_id") or "")
        record = self._endpoints.setdefault(endpoint, EndpointHealth())
        if now - record.failed_at > 1800:
            record.failures = 0
        record.failures += 1
        record.failed_at = now
        record.stable_at = None
        record.until = now + min(1800.0, max(0.0, base_seconds) * 2 ** min(record.failures - 1, 3))
        host = candidate_host(candidate)
        if reason == "media_stall" and host:
            self._slow_hosts[host] = record.until
            self._hosts.failed(self.room, host, now)
        return record.until

    def note_stable(self, candidate: dict, now: float):
        endpoint = str(candidate.get("endpoint_id") or "")
        self._endpoints[endpoint] = EndpointHealth(stable_at=now)
        self._slow_hosts.pop(candidate_host(candidate), None)

    def blocked_until(self, candidate: dict) -> float:
        record = self._endpoints.get(str(candidate.get("endpoint_id") or ""))
        return max(record.until if record else 0.0,
                   self._slow_hosts.get(candidate_host(candidate), 0.0))

    def ranked(self, candidates: list[dict], now: float) -> list[dict]:
        # Expire memory, including identities of old stream paths.
        self._endpoints = {key: record for key, record in self._endpoints.items()
                           if now - max(record.failed_at, record.stable_at or 0) <= 1800}
        self._slow_hosts = {host: until for host, until in self._slow_hosts.items() if until > now}

        def score(item):
            record = self._endpoints.get(str(item.get("endpoint_id") or ""))
            stable = bool(record and record.stable_at is not None and now - record.stable_at <= 600)
            failures = record.failures if record else 0
            return (self._hosts.penalty(candidate_host(item), now),
                    0 if stable else (2 if failures else 1), failures,
                    record.failed_at if record else 0)

        return sorted(candidates, key=score)
