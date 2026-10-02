"""Loopback media relay: FFmpeg gets fresh room credentials on every request.

Existing healthy upstream connections keep streaming. HLS playlists, segments,
keys and initialization maps resolve credentials independently of the process.
Only registered URLs can be requested; account cookies never reach FFmpeg.
"""
from __future__ import annotations

import hashlib
import os
import posixpath
import re
import secrets
import threading
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.parse import urljoin, urlparse
from urllib.request import HTTPSHandler, HTTPRedirectHandler, ProxyHandler, Request, build_opener

from core.http_ssl import get_default_context


def loopback_process_env(env=None):
    """The relay owns upstream proxies; FFmpeg must reach localhost directly."""
    result = dict(os.environ if env is None else env)
    for key in list(result):
        if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}:
            result.pop(key)
    return result


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class MediaRelay:
    def __init__(self, upstream_url: str, headers_provider):
        self._headers_provider = headers_provider
        self._origin_host = urlparse(upstream_url).hostname
        self._token = secrets.token_urlsafe(24)
        self._urls = OrderedDict()
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._thread = None
        relay = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                relay._serve(self)

            def do_HEAD(self):
                relay._serve(self, head=True)

            def log_message(self, *_args):
                pass  # No upstream URL, Cookie, or capability token in logs.

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._base = f"http://127.0.0.1:{self._server.server_port}/{self._token}/"
        self.url = self._register(upstream_url)

    def start(self):
        self._thread = threading.Thread(target=lambda: self._server.serve_forever(poll_interval=.1),
                                        name="media-http-relay", daemon=True)
        self._thread.start()

    def stop(self):
        with self._lock:
            if self._stopped.is_set():
                return
            self._stopped.set()
        if self._thread is not None:
            self._server.shutdown()
        self._server.server_close()

    def stop_when(self, process, on_exit=None):
        def watch():
            try:
                process.wait()
            finally:
                self.stop()
                if on_exit is not None:
                    on_exit()
        threading.Thread(target=watch, name="media-relay-cleanup", daemon=True).start()

    def _register(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise ValueError("Invalid media URL")
        key = hashlib.sha256(url.encode()).hexdigest()
        # HLS also checks segment extensions (.ts/.m4s/.mp4) for admission.
        extension = posixpath.splitext(parsed.path)[1]
        suffix = extension if re.fullmatch(r"\.[A-Za-z0-9]{1,8}", extension) else ""
        key += suffix
        with self._lock:
            self._urls[key] = url
            self._urls.move_to_end(key)
            # Bounded cache, enough for delayed segment readers and live playlists.
            while len(self._urls) > 4096:
                oldest = next(iter(self._urls))
                if hasattr(self, "url") and self.url.endswith(oldest):
                    self._urls.move_to_end(oldest)
                else:
                    self._urls.popitem(last=False)
        return self._base + key

    def _trusted_cookie_host(self, url):
        host = (urlparse(url).hostname or "").lower()
        return host == self._origin_host or any(
            host == domain or host.endswith("." + domain)
            for domain in ("bilivideo.com", "bilivideo.cn", "bilibili.com", "hdslb.com")
        )

    def _open(self, url, *, head=False, byte_range=None):
        for _ in range(6):
            headers = dict(self._headers_provider())
            proxy = headers.pop("_proxy", None)
            headers = {k: v for k, v in headers.items() if not k.startswith("_")}
            if not self._trusted_cookie_host(url):
                headers.pop("Cookie", None)
            headers["Accept-Encoding"] = "identity"
            if byte_range:
                headers["Range"] = byte_range
            opener = build_opener(ProxyHandler({"http": proxy, "https": proxy} if proxy else {}),
                                  HTTPSHandler(context=get_default_context()), _NoRedirect())
            request = Request(url, headers=headers, method="HEAD" if head else "GET")
            try:
                return opener.open(request, timeout=15)
            except HTTPError as error:
                if error.code not in {301, 302, 303, 307, 308} or not error.headers.get("Location"):
                    return error
                destination = urljoin(url, error.headers["Location"])
                error.close()
                if urlparse(destination).scheme not in {"http", "https"}:
                    raise ValueError("Invalid media redirect")
                url = destination
        raise ValueError("Too many media redirects")

    def _rewrite_playlist(self, body: bytes, base_url: str) -> bytes:
        lines = []
        for line in body.decode("utf-8-sig").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                line = self._register(urljoin(base_url, stripped))
            elif stripped.startswith("#"):
                line = re.sub(r'URI="([^"]+)"',
                              lambda match: f'URI="{self._register(urljoin(base_url, match.group(1)))}"', line)
            lines.append(line)
        return ("\n".join(lines) + "\n").encode()

    def _serve(self, handler, *, head=False):
        prefix = f"/{self._token}/"
        if self._stopped.is_set() or not handler.path.startswith(prefix):
            handler.send_error(404)
            return
        with self._lock:
            url = self._urls.get(handler.path[len(prefix):])
        if not url:
            handler.send_error(404)
            return
        sent_headers = False
        try:
            with self._open(url, head=head, byte_range=handler.headers.get("Range")) as upstream:
                status = upstream.getcode()
                first = b"" if head else upstream.read(65536)
                playlist = first.lstrip().startswith(b"#EXTM3U")
                if playlist:
                    remainder = upstream.read(1024 * 1024)
                    if len(first) + len(remainder) > 1024 * 1024:
                        raise ValueError("Playlist too large")
                    first = self._rewrite_playlist(first + remainder, upstream.geturl())
                handler.send_response(status)
                for name in ("Content-Type", "Content-Range", "Accept-Ranges"):
                    if upstream.headers.get(name):
                        handler.send_header(name, upstream.headers[name])
                if playlist:
                    handler.send_header("Content-Length", str(len(first)))
                elif upstream.headers.get("Content-Length"):
                    handler.send_header("Content-Length", upstream.headers["Content-Length"])
                handler.end_headers()
                sent_headers = True
                if head:
                    return
                handler.wfile.write(first)
                if not playlist:
                    while not self._stopped.is_set():
                        chunk = upstream.read(65536)
                        if not chunk:
                            break
                        handler.wfile.write(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            # Do not include URLs, remote bodies or account values in errors.
            if sent_headers:
                handler.close_connection = True
                return
            try:
                handler.send_error(502, "Media request failed")
            except OSError:
                pass
