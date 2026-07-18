"""Process-wide SSL contexts for Windows-safe concurrent HTTPS.

Python's ssl.create_default_context() loads the system trust store.
On Windows that path is not safe when many threads call it at once
(multiple rooms starting native FLV + cover download + webhooks).
Reuse one warmed context per process instead.
"""
from __future__ import annotations

import ssl
import threading
from typing import Any, Optional
from urllib.request import urlopen as _stdlib_urlopen

_lock = threading.RLock()
_default_ctx: Optional[ssl.SSLContext] = None
_insecure_ctx: Optional[ssl.SSLContext] = None
_warmed = False


def get_default_context() -> ssl.SSLContext:
    global _default_ctx
    ctx = _default_ctx
    if ctx is not None:
        return ctx
    with _lock:
        if _default_ctx is None:
            _default_ctx = ssl.create_default_context()
        return _default_ctx


def get_insecure_context() -> ssl.SSLContext:
    """Shared context that skips cert verification (danmaku WS etc.)."""
    global _insecure_ctx
    ctx = _insecure_ctx
    if ctx is not None:
        return ctx
    with _lock:
        if _insecure_ctx is None:
            c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            c.check_hostname = False
            c.verify_mode = ssl.CERT_NONE
            _insecure_ctx = c
        return _insecure_ctx


def urlopen(url, data=None, timeout=..., *, context: Optional[ssl.SSLContext] = None, **kwargs: Any):
    """urllib.request.urlopen that always reuses a process-level SSL context."""
    if context is None:
        context = get_default_context()
    if timeout is ...:
        return _stdlib_urlopen(url, data=data, context=context, **kwargs)
    return _stdlib_urlopen(url, data=data, timeout=timeout, context=context, **kwargs)


def warm_up() -> None:
    """Create contexts once on the main thread before worker traffic starts."""
    global _warmed
    if _warmed:
        return
    with _lock:
        if _warmed:
            return
        get_default_context()
        get_insecure_context()
        _warmed = True
