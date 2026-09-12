"""HTTP-FLV 拉流源：支持停止、超时与基础重连信号。"""
from __future__ import annotations

import logging
import socket
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request

from core.http_ssl import urlopen

logger = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)


@dataclass
class StreamSourceStats:
    bytes_read: int = 0
    reconnects: int = 0
    last_error: str = ""
    started_at: float = 0.0
    stopped_at: float = 0.0


class StreamSource:
    """阻塞式读取循环；在线程中运行。

    on_data(bytes) 每次读到块调用。
    on_disconnected(reason) 连接断开时调用（一次连接结束）。
    """

    def __init__(
        self,
        url: str,
        *,
        on_data: Callable[[bytes], None],
        on_disconnected: Optional[Callable[[str], None]] = None,
        headers: Optional[dict] = None,
        read_size: int = 64 * 1024,
        timeout: float = 15.0,
    ):
        self.url = url
        self.on_data = on_data
        self.on_disconnected = on_disconnected or (lambda r: None)
        self.headers = headers or {}
        self.read_size = read_size
        self.timeout = timeout
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.stats = StreamSourceStats()

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.stats = StreamSourceStats(started_at=time.time())
        self._resp = None
        self._resp_lock = getattr(self, "_resp_lock", None) or threading.Lock()
        self._thread = threading.Thread(target=self._run, name="stream-source", daemon=True)
        self._thread.start()

    def stop(self, wait: float = 5.0):
        # 仅置位停止标记。resp.close() 若在另一线程调用，可能与 resp.read 并发
        # 触发 Windows/OpenSSL access violation。read 超时后会自行退出。
        self._stop.set()
        t = self._thread
        if t and t.is_alive():
            t.join(timeout=wait)
        self.stats.stopped_at = time.time()

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run(self):
        reason = "stop"
        resp = None
        if not hasattr(self, "_resp_lock"):
            self._resp_lock = threading.Lock()
        try:
            hdrs = {
                "User-Agent": self.headers.get("User-Agent", DEFAULT_UA),
                "Referer": self.headers.get("Referer", "https://live.bilibili.com/"),
            }
            for k, v in self.headers.items():
                if k.startswith("_"):
                    continue
                hdrs[k] = v
            req = Request(self.url, headers=hdrs)
            resp = urlopen(req, timeout=self.timeout)
            # Keep the network timeout long enough to tolerate brief media stalls.
            try:
                resp.fp.raw._sock.settimeout(float(self.timeout))
            except Exception:
                pass
            with self._resp_lock:
                self._resp = resp
            while not self._stop.is_set():
                try:
                    chunk = resp.read(self.read_size)
                except (TimeoutError, socket.timeout) as e:
                    if self._stop.is_set():
                        reason = "stop"
                        break
                    reason = f"read_timeout:{e}"
                    break
                except Exception as e:
                    if self._stop.is_set():
                        reason = "stop"
                        break
                    reason = f"read_error:{e}"
                    break
                if not chunk:
                    reason = "eof"
                    break
                self.stats.bytes_read += len(chunk)
                try:
                    self.on_data(chunk)
                except Exception as e:
                    reason = f"on_data_error:{e}"
                    logger.exception("stream on_data failed")
                    break
                if self._stop.is_set():
                    reason = "stop"
                    break
        except HTTPError as e:
            reason = f"http_{e.code}"
            self.stats.last_error = reason
        except URLError as e:
            reason = f"url_error:{e.reason}"
            self.stats.last_error = reason
        except Exception as e:
            reason = f"error:{e}"
            self.stats.last_error = reason
            logger.warning(f"StreamSource error: {e}")
        finally:
            with self._resp_lock:
                if self._resp is resp:
                    self._resp = None
            if resp is not None:
                try:
                    resp.close()
                except Exception:
                    pass
            if not self._stop.is_set():
                try:
                    self.on_disconnected(reason)
                except Exception:
                    pass
            else:
                reason = "stop"
