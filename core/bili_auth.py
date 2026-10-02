"""Optional Web QR login and local account lifecycle, independent of Qt.

Protocol reference: bilibili-API-collect docs/login/login_action/QR.md and
docs/login/cookie_refresh.md. Network failures never invalidate an account.
"""
from __future__ import annotations

import copy
import json
import logging
import os
import re
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from curl_cffi import requests

from core.cookies import cookie_header, parse_cookie

PASSPORT = "https://passport.bilibili.com/x/passport-login/web"
PUBLIC_KEY = b"""-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDLgd2OAkcGVtoE3ThUREbio0Eg
Uc/prcajMKXvkCKFCWhJYJcLkcM2DKKcSeFpD/j6Boy538YXnR6VhcuUJOhH2x71
nzPjfdTcqMz7djHum0qSZA0AyCBDABUqCrfNgCiJ00Ra7GmRj+YCK1NJEuewlb40
JNrRuoEUXpabUzGB8QIDAQAB
-----END PUBLIC KEY-----"""


class AuthError(Exception):
    """A safe, non-secret error category for presentation and logs."""


class AccountStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._account = {}
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(saved, dict) and saved.get("cookies"):
                saved["cookies"] = parse_cookie(saved["cookies"])
                self._account = saved
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            logging.warning("B站账号文件无法读取，将使用匿名录制")

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._account)

    def replace(self, account: dict, *, expected_revision: str | None = None) -> str | bool:
        """Atomic persistence; stale verification cannot resurrect a logged-out user."""
        with self._lock:
            if expected_revision is not None and self._account.get("revision", "") != expected_revision:
                return False
            saved = copy.deepcopy(account)
            if saved:
                saved["cookies"] = parse_cookie(saved.get("cookies"))
                saved["revision"] = uuid.uuid4().hex
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.tmp")
            try:
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    json.dump(saved, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                temporary.unlink(missing_ok=True)
            self._account = saved
            return saved.get("revision") or True

    def cookie(self) -> str:
        account = self.snapshot()
        if account.get("status") != "active":
            return ""
        return cookie_header(account.get("cookies"))

    def invalidate(self, used_cookie: str) -> bool:
        account = self.snapshot()
        if not used_cookie or used_cookie != self.cookie():
            return False
        account["status"] = "expired"
        return bool(self.replace(account, expected_revision=account.get("revision", "")))


class BiliAuthClient:
    def __init__(self, transport=None):
        self.transport = transport or requests

    def _request(self, method: str, url: str, cookies=None, **kwargs):
        from core.config import get_headers

        headers = get_headers()
        headers.pop("Cookie", None)
        proxy = headers.pop("_proxy", None)
        headers["Referer"] = "https://www.bilibili.com/"
        if cookies:
            headers["Cookie"] = cookie_header(cookies)
        try:
            response = getattr(self.transport, method)(
                url, headers=headers, proxy=proxy, timeout=10,
                impersonate="chrome110", allow_redirects=False, **kwargs,
            )
            response.raise_for_status()
            return response
        except Exception:
            # curl errors can include a secret QR key or correspond URL.
            raise AuthError("network") from None

    @staticmethod
    def _json(response) -> dict:
        try:
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError
            return payload
        except (ValueError, TypeError):
            raise AuthError("response") from None

    def generate(self) -> dict:
        payload = self._json(self._request("get", f"{PASSPORT}/qrcode/generate"))
        data = payload.get("data") or {}
        if payload.get("code") != 0 or not data.get("qrcode_key") or not data.get("url"):
            raise AuthError("response")
        return {"key": data["qrcode_key"], "url": data["url"]}

    def poll(self, key: str) -> dict:
        response = self._request("get", f"{PASSPORT}/qrcode/poll", params={"qrcode_key": key})
        payload = self._json(response)
        if payload.get("code") != 0:
            raise AuthError("response")
        data = payload.get("data") or {}
        status = {86101: "pending", 86090: "scanned", 86038: "expired", 0: "success"}.get(data.get("code"))
        if status is None:
            raise AuthError("response")
        if status != "success":
            return {"status": status}
        cookies = dict(response.cookies.items())
        # Some clients do not expose Set-Cookie; the success URL also carries it.
        query = parse_qs(urlparse(data.get("url", "")).query)
        for name in ("SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "sid"):
            if name not in cookies and query.get(name):
                value = query[name][0]
                cookies[name] = quote(value, safe="%") if name == "SESSDATA" else value
        cookies = parse_cookie(cookies)
        if not cookies.get("SESSDATA"):
            raise AuthError("response")
        try:
            profile = self.profile(cookies)
        except AuthError as exc:
            if str(exc) == "expired":
                raise
            # A completed scan must not be lost to a separate profile outage.
            profile = {"uid": cookies.get("DedeUserID", ""), "uname": "", "face": ""}
        account = {
            "cookies": cookies, "refresh_token": data.get("refresh_token", ""),
            "status": "active", "checked_at": time.time() if profile["uname"] else 0, **profile,
        }
        return {"status": "success", "account": account}

    def profile(self, cookies: dict) -> dict:
        payload = self._json(self._request("get", "https://api.bilibili.com/x/web-interface/nav", cookies))
        data = payload.get("data") or {}
        if payload.get("code") == -101 or (payload.get("code") == 0 and data.get("isLogin") is False):
            raise AuthError("expired")
        if payload.get("code") != 0 or not data.get("isLogin"):
            raise AuthError("response")
        return {"uid": str(data.get("mid", "")), "uname": str(data.get("uname", "")), "face": str(data.get("face", ""))}

    def avatar(self, url: str) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not (parsed.hostname or "").endswith(".hdslb.com"):
            raise AuthError("response")
        response = self._request("get", url)
        if len(response.content) > 1024 * 1024:
            raise AuthError("response")
        return response.content

    def refresh(self, account: dict) -> dict:
        """Rotate only when B站 requests it; save before confirming the old token."""
        cookies = account["cookies"]
        if not account.get("refresh_token") or not cookies.get("bili_jct"):
            return account
        payload = self._json(self._request("get", f"{PASSPORT}/cookie/info", cookies, params={"csrf": cookies["bili_jct"]}))
        if payload.get("code") == -101:
            raise AuthError("expired")
        if payload.get("code") != 0:
            raise AuthError("response")
        if not (payload.get("data") or {}).get("refresh"):
            return account
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp = int((payload.get("data") or {}).get("timestamp") or time.time() * 1000)
        key = serialization.load_pem_public_key(PUBLIC_KEY)
        path = key.encrypt(f"refresh_{timestamp}".encode(), padding.OAEP(
            mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None,
        )).hex()
        html = self._request("get", f"https://www.bilibili.com/correspond/1/{path}", cookies).text
        match = re.search(r'<div\s+id=["\']1-name["\']\s*>([^<]+)</div>', html)
        if not match:
            raise AuthError("response")
        response = self._request("post", f"{PASSPORT}/cookie/refresh", cookies, data={
            "csrf": cookies["bili_jct"], "refresh_csrf": match.group(1).strip(),
            "source": "main_web", "refresh_token": account["refresh_token"],
        })
        result = self._json(response)
        token = (result.get("data") or {}).get("refresh_token")
        fresh_cookies = parse_cookie(dict(response.cookies.items()))
        if result.get("code") != 0 or not token or not fresh_cookies.get("SESSDATA") or not fresh_cookies.get("bili_jct"):
            raise AuthError("response")
        return {**account, "cookies": {**cookies, **fresh_cookies},
                "refresh_token": token, "pending_confirm": account["refresh_token"]}

    def confirm_refresh(self, account: dict):
        payload = self._json(self._request("post", f"{PASSPORT}/confirm/refresh", account["cookies"], data={
            "csrf": account["cookies"]["bili_jct"], "refresh_token": account["pending_confirm"],
        }))
        if payload.get("code") != 0:
            raise AuthError("response")


class AccountService:
    def __init__(self, store: AccountStore, client: BiliAuthClient | None = None):
        self.store = store
        self.client = client or BiliAuthClient()
        self._maintenance_lock = threading.Lock()

    def maintain(self, *, force=False) -> dict:
        if not self._maintenance_lock.acquire(blocking=False):
            return self.store.snapshot()
        try:
            account = self.store.snapshot()
            if not account or account.get("status") != "active":
                return account
            if not force and time.time() - account.get("checked_at", 0) < 6 * 3600:
                return account
            revision = account.get("revision", "")
            try:
                # A failed confirmation is retried before starting another rotation.
                if account.get("pending_confirm"):
                    self.client.confirm_refresh(account)
                    account.pop("pending_confirm")
                    revision = self.store.replace(account, expected_revision=revision)
                    if not revision:
                        return self.store.snapshot()
                account = self.client.refresh(account)
                revision = self.store.replace(account, expected_revision=revision)
                if not revision:
                    return self.store.snapshot()
                if account.get("pending_confirm"):
                    self.client.confirm_refresh(account)
                    account.pop("pending_confirm")
                    revision = self.store.replace(account, expected_revision=revision)
                    if not revision:
                        return self.store.snapshot()
                account.update(self.client.profile(account["cookies"]))
                account["checked_at"] = time.time()
                self.store.replace(account, expected_revision=revision)
            except AuthError as exc:
                if str(exc) == "expired":
                    account["status"] = "expired"
                    self.store.replace(account, expected_revision=revision)
                    logging.warning("B站登录已失效，默认账号将回退为匿名录制，请重新扫码")
                else:
                    raise
            return self.store.snapshot()
        finally:
            self._maintenance_lock.release()


_service = None
_service_lock = threading.Lock()


def account_service() -> AccountService:
    global _service
    with _service_lock:
        if _service is None:
            from core.config import APP_DIR
            _service = AccountService(AccountStore(Path(APP_DIR) / "bili_account.json"))
        return _service
