"""Single-reader FFmpeg bridge: HLS/fMP4 input -> copy-mode FLV byte stream."""
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from core.ffmpeg_tools import FFMPEG_CMD, hidden_subprocess_kwargs

logger = logging.getLogger(__name__)


@dataclass
class FfmpegFlvSourceStats:
    bytes_read: int = 0
    last_error: str = ""
    started_at: float = 0.0
    stopped_at: float = 0.0
    returncode: Optional[int] = None


class FfmpegFlvSource:
    """Keep one network reader alive and expose its copy-mode FLV stdout."""

    def __init__(
        self,
        url: str,
        *,
        on_data: Callable[[bytes], None],
        on_disconnected: Optional[Callable[[str], None]] = None,
        headers: Optional[dict] = None,
        env: Optional[dict] = None,
        log_path: str = "",
        read_size: int = 64 * 1024,
        ffmpeg_cmd: str = FFMPEG_CMD,
        headers_provider=None,
    ):
        self.url = url
        self.on_data = on_data
        self.on_disconnected = on_disconnected or (lambda _reason: None)
        self.headers = headers or {}
        self.env = env
        self.log_path = log_path
        self.read_size = read_size
        self.ffmpeg_cmd = ffmpeg_cmd
        self.headers_provider = headers_provider
        self._relay = None
        self.stats = FfmpegFlvSourceStats()

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._stderr_file = None

    def build_command(self) -> list[str]:
        from core.cookies import ffmpeg_headers
        active_headers = {} if self._relay is not None else self.headers
        user_agent = active_headers.get("User-Agent", "Mozilla/5.0")
        referer = self.headers.get("Referer", "https://live.bilibili.com/")
        return [
            self.ffmpeg_cmd,
            "-hide_banner",
            "-nostats",
            "-loglevel",
            "warning",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-rw_timeout",
            "15000000",
            "-user_agent",
            user_agent,
            "-headers",
            ffmpeg_headers({"Referer": referer, **active_headers}),
            "-i",
            self._relay.url if self._relay is not None else self.url,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-c",
            "copy",
            "-f",
            "flv",
            "-flvflags",
            "no_duration_filesize",
            "pipe:1",
        ]

    def start(self):
        if self.is_running:
            return
        self._stop.clear()
        self.stats = FfmpegFlvSourceStats(started_at=time.time())
        if self.headers_provider is not None:
            from core.media_http import MediaRelay
            self._relay = MediaRelay(self.url, self.headers_provider)
            self._relay.start()
        stderr_target = subprocess.DEVNULL
        if self.log_path:
            try:
                os.makedirs(os.path.dirname(self.log_path) or ".", exist_ok=True)
                self._stderr_file = open(self.log_path, "w", encoding="utf-8", errors="replace")
                stderr_target = self._stderr_file
            except OSError as exc:
                logger.warning("open FFmpeg bridge log failed: %s", exc)

        kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr_target,
            env=self.env,
            bufsize=0,
        )
        kwargs.update(hidden_subprocess_kwargs(new_process_group=True))
        if self._relay is not None:
            from core.media_http import loopback_process_env
            kwargs["env"] = loopback_process_env(self.env)
        try:
            self._process = subprocess.Popen(self.build_command(), **kwargs)
        except Exception:
            if self._relay is not None:
                self._relay.stop()
                self._relay = None
            self._close_stderr()
            raise
        self._thread = threading.Thread(
            target=self._read_loop,
            name="ffmpeg-flv-source",
            daemon=True,
        )
        self._thread.start()

    def stop(self, wait: float = 5.0):
        self._stop.set()
        proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.write(b"q\n")
                    proc.stdin.flush()
            except Exception:
                pass

        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=wait)
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        if thread and thread.is_alive():
            thread.join(timeout=2.0)
        self.stats.stopped_at = time.time()
        self._close_stderr()
        if self._relay is not None:
            self._relay.stop()
            self._relay = None

    @property
    def is_running(self) -> bool:
        proc = self._process
        return bool(
            proc is not None
            and proc.poll() is None
            and self._thread is not None
            and self._thread.is_alive()
        )

    @property
    def process(self) -> Optional[subprocess.Popen]:
        return self._process

    def _read_loop(self):
        reason = "stop"
        proc = self._process
        try:
            if proc is None or proc.stdout is None:
                raise RuntimeError("FFmpeg bridge stdout is unavailable")
            # Always drain stdout through EOF. During a normal stop() we first
            # send ``q`` to FFmpeg; FFmpeg may still flush muxer-buffered media
            # before closing the pipe. Exiting merely because _stop is set can
            # discard that tail and leave the final capture segment truncated.
            while True:
                chunk = proc.stdout.read(self.read_size)
                if not chunk:
                    break
                self.stats.bytes_read += len(chunk)
                self.on_data(chunk)
            if not self._stop.is_set():
                code = proc.wait(timeout=5.0) if proc.poll() is None else proc.returncode
                self.stats.returncode = code
                reason = f"ffmpeg_exit:{code}"
                if code:
                    self.stats.last_error = reason
        except Exception as exc:
            reason = f"bridge_error:{exc}"
            self.stats.last_error = reason
            logger.exception("FFmpeg FLV bridge failed")
        finally:
            if proc is not None:
                try:
                    if proc.stdout:
                        proc.stdout.close()
                except Exception:
                    pass
                if proc.poll() is not None:
                    self.stats.returncode = proc.returncode
            self.stats.stopped_at = time.time()
            self._close_stderr()
            if self._relay is not None:
                self._relay.stop()
            if not self._stop.is_set():
                try:
                    self.on_disconnected(reason)
                except Exception:
                    logger.debug("bridge disconnect callback failed", exc_info=True)

    def _close_stderr(self):
        handle = self._stderr_file
        self._stderr_file = None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass
