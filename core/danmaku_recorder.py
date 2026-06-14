"""
弹幕录制模块
通过 B站直播弹幕 WebSocket 接口抓取弹幕，支持 jsonl / xml / ass 输出格式
使用原始 socket 实现 WebSocket 连接（绕过第三方库与海外弹幕节点的兼容问题）
"""
import base64
import hashlib
import hmac
import json
import logging
import os
import socket
import struct
import threading
import time
import urllib.parse
import zlib
import xml.sax.saxutils as saxutils
from typing import Optional

try:
    import brotli
    HAS_BROTLI = True
except ImportError:
    HAS_BROTLI = False


ASS_HEADER = """\
[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScalingFactor: 100

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,黑体,50,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,2,0,8,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _seconds_to_ass(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


class DanmakuRecorder:
    _UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
           "AppleWebKit/537.36 (KHTML, like Gecko) "
           "Chrome/136.0.0.0 Safari/537.36")

    _HEARTBEAT_INTERVAL = 30

    # WBI 签名重排映射表
    _MIXIN_KEY_ENC_TAB = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35,
        27, 43, 5, 49, 33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13,
        37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1, 60, 51, 30, 4,
        22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52,
    ]

    def __init__(self, room_id: str, save_dir: str, filename_base: str):
        self.room_id = room_id
        self.save_dir = save_dir
        self.filename_base = filename_base

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._sock = None
        self._start_ts = time.time()
        self._fmt = "xml"
        self._sessdata = ""
        self._cookie_str = ""
        self._cookies = {}
        self._uid = 0
        self._buvid3 = ""
        self._buvid4 = ""
        self._file = None
        self._danmaku_count = 0
        self._lock = threading.Lock()

    # ─── 公开接口 ──────────────────────────────

    def start(self, chat_format: str, sessdata: str = ""):
        self._fmt = chat_format
        self._sessdata = sessdata
        # chat_credential 可能是纯 SESSDATA 值，也可能是一整串浏览器 cookie。
        # 统一解析成 dict，并提取真实登录态（DedeUserID / buvid3）。
        self._parse_credential(sessdata)
        self._stop_event.clear()
        self._start_ts = time.time()
        self._thread = threading.Thread(target=self._run_thread, daemon=True,
                                        name=f"danmaku-{self.room_id}")
        self._thread.start()
        logging.info(f"💬 {self.room_id} 弹幕录制已启动，格式: {chat_format}")

    def _parse_credential(self, cred: str):
        """解析凭证：支持纯 SESSDATA 或完整 cookie 串"""
        self._cookies = {}
        cred = (cred or "").strip()
        if not cred:
            return
        if "=" in cred and ";" in cred or "SESSDATA=" in cred:
            # 完整 cookie 串
            for part in cred.split(";"):
                if "=" in part:
                    k, _, v = part.strip().partition("=")
                    self._cookies[k.strip()] = v.strip()
        else:
            # 纯 SESSDATA 值
            self._cookies["SESSDATA"] = cred
        self._uid = int(self._cookies.get("DedeUserID", "0") or "0")
        if self._cookies.get("buvid3"):
            self._buvid3 = self._cookies["buvid3"]
        if self._cookies.get("buvid4"):
            self._buvid4 = self._cookies["buvid4"]
        # 组装统一的 Cookie 头
        self._cookie_str = "; ".join(f"{k}={v}" for k, v in self._cookies.items())

    def stop(self):
        self._stop_event.set()
        # 关闭 socket 唤醒阻塞的 recv
        if self._sock is not None:
            try:
                self._sock.close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=8)
        self._finalize()
        logging.info(f"💬 {self.room_id} 弹幕录制已停止，共 {self._danmaku_count} 条弹幕")

    def reset(self, filename_base: str):
        self._finalize()
        self.filename_base = filename_base
        self._danmaku_count = 0
        self._open_file()

    # ─── 文件操作 ──────────────────────────────

    def _ext(self):
        fmt = self._fmt.lower()
        if "jsonl" in fmt:
            return "jsonl"
        if "ass" in fmt:
            return "ass"
        return "xml"

    def _open_file(self):
        os.makedirs(self.save_dir, exist_ok=True)
        path = os.path.join(self.save_dir, f"{self.filename_base}.{self._ext()}")
        self._file = open(path, "w", encoding="utf-8", buffering=1)
        if self._ext() == "xml":
            self._file.write('<?xml version="1.0" encoding="UTF-8"?>\n<i>\n')
        elif self._ext() == "ass":
            self._file.write(ASS_HEADER)

    def _finalize(self):
        if self._file:
            try:
                if self._ext() == "xml":
                    self._file.write("</i>\n")
                self._file.close()
            except Exception:
                pass
            self._file = None

    def _write_danmaku(self, ts: float, uid: str, uname: str, text: str,
                       color: int = 16777215, size: int = 25):
        with self._lock:
            if not self._file:
                return
            elapsed = ts - self._start_ts
            ext = self._ext()
            if ext == "jsonl":
                self._file.write(json.dumps(
                    {"ts": round(elapsed, 3), "uid": uid, "name": uname,
                     "text": text, "color": color, "size": size},
                    ensure_ascii=False) + "\n")
            elif ext == "xml":
                t = round(elapsed, 3)
                self._file.write(
                    f'  <d p="{t},1,{size},{color},0,{uid},0,0" '
                    f'user="{saxutils.quoteattr(uname)[1:-1]}">'
                    f'{saxutils.escape(text)}</d>\n')
            elif ext == "ass":
                start_s = _seconds_to_ass(elapsed)
                end_s = _seconds_to_ass(elapsed + 8)
                safe = text.replace("\n", " ").replace("{", "｛").replace("}", "｝")
                self._file.write(
                    f"Dialogue: 0,{start_s},{end_s},Default,{saxutils.escape(uname)},"
                    f"0,0,0,,{safe}\n")
            self._danmaku_count += 1

    # ─── 凭证获取 ──────────────────────────────

    @classmethod
    def _ensure_buvid(cls):
        """每次都获取独立的 buvid3：多房间同时录制时若共用同一 buvid3，
        会被 B站当作同一身份的并发连接而踢掉，导致反复断开重连。"""
        import uuid
        b3, b4 = cls._fetch_buvid()
        if not b3:
            b3 = str(uuid.uuid4()).upper() + "infoc"
            b4 = ""
        return b3, b4

    @staticmethod
    def _fetch_buvid() -> tuple:
        try:
            from curl_cffi import requests as _cr
            r = _cr.get(
                "https://api.bilibili.com/x/frontend/finger/spi",
                headers={"User-Agent": DanmakuRecorder._UA,
                         "Referer": "https://www.bilibili.com/"},
                impersonate="chrome110", timeout=8,
            )
            d = r.json()
            if d.get("code") == 0:
                return d["data"]["b_3"], d["data"]["b_4"]
        except Exception as e:
            logging.debug(f"danmaku: fetch buvid failed: {e}")
        return "", ""

    @staticmethod
    def _fetch_bili_ticket(buvid3: str, buvid4: str) -> tuple:
        try:
            from curl_cffi import requests as _cr
            ts = int(time.time())
            hex_sign = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode(), hashlib.sha256).hexdigest()
            params = urllib.parse.urlencode({
                "key_id": "ec02", "hexsign": hex_sign, "context[ts]": ts, "csrf": "",
            })
            url = f"https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket?{params}"
            r = _cr.post(url, data=b"", headers={
                "User-Agent": DanmakuRecorder._UA,
                "Referer": "https://www.bilibili.com/",
                "Cookie": f"buvid3={buvid3}; buvid4={buvid4}; b_nut={ts}",
                "Content-Type": "application/x-www-form-urlencoded",
            }, impersonate="chrome110", timeout=8)
            d = r.json()
            if d.get("code") == 0:
                ticket = d["data"]["ticket"]
                img_url = d["data"].get("nav", {}).get("img", "")
                sub_url = d["data"].get("nav", {}).get("sub", "")
                img_key = img_url.rsplit("/", 1)[-1].replace(".png", "") if img_url else ""
                sub_key = sub_url.rsplit("/", 1)[-1].replace(".png", "") if sub_url else ""
                return ticket, img_key, sub_key
        except Exception as e:
            logging.debug(f"danmaku: fetch bili_ticket failed: {e}")
        return "", "", ""

    @staticmethod
    def _activate_buvid(buvid3: str, buvid4: str, ticket: str):
        """调用 ExClimbWuzhi 激活 buvid3（部分弹幕节点要求）"""
        try:
            from curl_cffi import requests as _cr
            payload = {
                "3064": 1, "5062": str(int(time.time() * 1000)),
                "03bf": "https://www.bilibili.com/", "39c8": "333.1007.fp.risk",
                "34f1": "", "d402": "", "654a": "", "6e7c": "839x959",
                "3c43": {"adca": "Win32", "bfe9": "ab123"},
            }
            body = json.dumps({"payload": json.dumps(payload, ensure_ascii=False)},
                              ensure_ascii=False)
            cookies = [f"buvid3={buvid3}"]
            if buvid4:
                cookies += [f"buvid4={buvid4}", f"b_nut={int(time.time())}"]
            if ticket:
                cookies.append(f"bili_ticket={ticket}")
            _cr.post(
                "https://api.bilibili.com/x/internal/gaia-gateway/ExClimbWuzhi",
                data=body.encode(),
                headers={
                    "User-Agent": DanmakuRecorder._UA,
                    "Referer": "https://www.bilibili.com/",
                    "Origin": "https://www.bilibili.com",
                    "Content-Type": "application/json",
                    "Cookie": "; ".join(cookies),
                }, impersonate="chrome110", timeout=8)
        except Exception as e:
            logging.debug(f"danmaku: activate buvid failed: {e}")

    @classmethod
    def _get_mixin_key(cls, img_key: str, sub_key: str) -> str:
        raw = img_key + sub_key
        return "".join(raw[i] for i in cls._MIXIN_KEY_ENC_TAB if i < len(raw))[:32]

    @classmethod
    def _wbi_sign(cls, params: dict, img_key: str, sub_key: str) -> dict:
        mixin_key = cls._get_mixin_key(img_key, sub_key)
        wts = int(time.time())
        signed_params = dict(params)
        signed_params["wts"] = wts
        sorted_params = dict(sorted(signed_params.items()))
        filtered = {
            k: "".join(c for c in str(v) if c not in "!'()*")
            for k, v in sorted_params.items()
        }
        query = urllib.parse.urlencode(filtered)
        w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
        params["wts"] = wts
        params["w_rid"] = w_rid
        return params

    # ─── getDanmuInfo ──────────────────────────

    def _get_danmaku_info(self):
        if not self._buvid3:
            buvid3, buvid4 = self._ensure_buvid()
            self._buvid3 = buvid3
            self._buvid4 = buvid4
        else:
            buvid3 = self._buvid3
            buvid4 = self._buvid4

        bili_ticket, img_key, sub_key = ("", "", "")
        b_nut = str(int(time.time()))
        if buvid3:
            bili_ticket, img_key, sub_key = self._fetch_bili_ticket(buvid3, buvid4)
            self._activate_buvid(buvid3, buvid4, bili_ticket)

        base_params = {"id": self.room_id, "type": 0}
        if img_key and sub_key:
            base_params = self._wbi_sign(base_params, img_key, sub_key)

        try:
            from curl_cffi import requests as _cr
            query = urllib.parse.urlencode(base_params)
            url = (f"https://api.live.bilibili.com/xlive/web-room/v1/index/"
                   f"getDanmuInfo?{query}")
            hdrs = {
                "User-Agent": self._UA,
                "Referer": f"https://live.bilibili.com/{self.room_id}",
                "Origin": "https://live.bilibili.com",
                "Accept": "application/json, text/plain, */*",
            }
            cookies = []
            if buvid3:
                cookies.append(f"buvid3={buvid3}")
            if buvid4:
                cookies.append(f"buvid4={buvid4}")
                cookies.append(f"b_nut={b_nut}")
            if bili_ticket:
                cookies.append(f"bili_ticket={bili_ticket}")
            # 已登录则带上完整 cookie（含 SESSDATA / DedeUserID 等）
            if self._cookie_str:
                cookies.append(self._cookie_str)
            if cookies:
                hdrs["Cookie"] = "; ".join(cookies)
            resp = _cr.get(url, headers=hdrs, impersonate="chrome110", timeout=10)
            data = resp.json()
            if data["code"] == 0:
                host_info = data["data"]["host_list"][0]
                token = data["data"]["token"]
                ws_url = f"wss://{host_info['host']}:{host_info['wss_port']}/sub"
                logging.info(
                    f"💬 {self.room_id} 弹幕服务器: {host_info['host']}, "
                    f"token={token[:8]}..."
                )
                return ws_url, token
            else:
                logging.warning(
                    f"💬 {self.room_id} getDanmuInfo 失败: "
                    f"code={data.get('code')} {data.get('message')}"
                )
        except Exception as e:
            logging.warning(f"💬 {self.room_id} 获取弹幕服务器失败: {e}")
        return "", ""

    # ─── WebSocket 协议 ────────────────────────

    @staticmethod
    def _pack(data: bytes, protocol: int = 1, op: int = 2) -> bytes:
        header = struct.pack(">IHHII", 16 + len(data), 16, protocol, op, 1)
        return header + data

    def _make_auth_bytes(self, token: str) -> bytes:
        payload = json.dumps({
            "uid": self._uid,
            "roomid": int(self.room_id),
            "protover": 3,
            "buvid": self._buvid3,
            "platform": "web",
            "type": 2,
            "key": token,
        }, ensure_ascii=False).encode("utf-8")
        return self._pack(payload, protocol=1, op=7)

    @staticmethod
    def _make_heartbeat_bytes() -> bytes:
        body = b"[object Object]"
        header = struct.pack(">IHHII", 16 + len(body), 16, 1, 2, 1)
        return header + body

    def _decode_frames(self, raw: bytes):
        offset = 0
        while offset < len(raw):
            if offset + 16 > len(raw):
                break
            total, header_len, protocol, op, _ = struct.unpack_from(">IHHII", raw, offset)
            if total < 16 or offset + total > len(raw):
                break
            body = raw[offset + header_len: offset + total]

            if protocol == 2:
                try:
                    self._decode_frames(zlib.decompress(body))
                except Exception:
                    pass
            elif protocol == 3:
                if HAS_BROTLI:
                    try:
                        self._decode_frames(brotli.decompress(body))
                    except Exception:
                        pass
                else:
                    try:
                        self._decode_frames(zlib.decompress(body))
                    except Exception:
                        pass
            elif op == 5:
                try:
                    msg = json.loads(body.decode("utf-8", errors="ignore"))
                    self._handle_cmd(msg)
                except Exception:
                    pass
            offset += total

    def _handle_cmd(self, msg: dict):
        cmd = msg.get("cmd", "")
        if not cmd.startswith("DANMU_MSG"):
            return
        info = msg.get("info", [])
        try:
            text = info[1]
            uid = str(info[2][0])
            uname = str(info[2][1])
            color = int(info[0][3]) if len(info[0]) > 3 else 16777215
            size = int(info[0][2]) if len(info[0]) > 2 else 25
            self._write_danmaku(time.time(), uid, uname, text, color, size)
        except (IndexError, TypeError, ValueError):
            pass

    # ─── 主循环 (raw socket) ───────────────────

    def _run_thread(self):
        """后台线程：建立 WebSocket 连接并接收弹幕（原始 socket 实现）"""
        self._open_file()
        ws_url, token = self._get_danmaku_info()
        if not token:
            logging.warning(f"💬 {self.room_id} 无法获取弹幕 token，弹幕录制取消。")
            self._finalize()
            return

        retry_delay = 3
        while not self._stop_event.is_set():
            try:
                self._connect_once(ws_url, token)
            except Exception as e:
                logging.warning(f"💬 {self.room_id} ws 异常: {type(e).__name__}: {e}")

            if self._stop_event.is_set():
                break

            logging.info(f"💬 {self.room_id} 弹幕连接断开，{retry_delay}s 后重连...")
            for _ in range(retry_delay):
                if self._stop_event.is_set():
                    break
                time.sleep(1)
            retry_delay = min(retry_delay * 2, 60)

            new_url, new_token = self._get_danmaku_info()
            if new_token:
                ws_url, token = new_url, new_token

    # ─── WebSocket 帧编解码 (RFC6455) ──────────

    @staticmethod
    def _ws_encode(payload: bytes) -> bytes:
        """把业务数据封装成带掩码的 WebSocket 二进制帧"""
        fin_op = 0x82  # FIN + binary opcode
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        n = len(payload)
        if n < 126:
            header = struct.pack(">BB", fin_op, 0x80 | n)
        elif n < 65536:
            header = struct.pack(">BBH", fin_op, 0x80 | 126, n)
        else:
            header = struct.pack(">BBQ", fin_op, 0x80 | 127, n)
        return header + mask + masked

    def _connect_once(self, ws_url: str, token: str):
        """建立一次连接：握手 → 鉴权 → 接收消息（阻塞直到断开）"""
        import ssl as _ssl

        host = ws_url.replace("wss://", "").replace("ws://", "")
        host = host.split("/")[0].split(":")[0]

        ctx = _ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = _ssl.CERT_NONE

        raw = socket.create_connection((host, 443), timeout=10)
        sock = ctx.wrap_socket(raw, server_hostname=host)
        self._sock = sock

        try:
            # 1. WebSocket 握手
            key = base64.b64encode(os.urandom(16)).decode()
            req = (
                f"GET /sub HTTP/1.1\r\n"
                f"Host: {host}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
                f"Origin: https://live.bilibili.com\r\n"
                f"User-Agent: {self._UA}\r\n\r\n"
            )
            sock.send(req.encode())
            resp = sock.recv(4096)
            head, _, rest = resp.partition(b"\r\n\r\n")
            if b"101" not in head.split(b"\r\n", 1)[0]:
                logging.warning(f"💬 {self.room_id} 握手失败: {resp[:80]}")
                return

            # 2. 发送鉴权包
            sock.send(self._ws_encode(self._make_auth_bytes(token)))
            logging.info(f"💬 {self.room_id} 弹幕连接成功")

            # 3. 启动心跳线程
            hb_stop = threading.Event()
            threading.Thread(
                target=self._heartbeat_loop, args=(sock, hb_stop),
                daemon=True, name=f"danmaku-hb-{self.room_id}").start()

            try:
                self._recv_loop(sock, rest)
            finally:
                hb_stop.set()
        finally:
            try:
                sock.close()
            except Exception:
                pass
            self._sock = None

    def _heartbeat_loop(self, sock, hb_stop: threading.Event):
        hb = self._ws_encode(self._make_heartbeat_bytes())
        while not hb_stop.is_set() and not self._stop_event.is_set():
            try:
                sock.send(hb)
            except Exception:
                break
            hb_stop.wait(self._HEARTBEAT_INTERVAL)

    def _recv_loop(self, sock, initial: bytes = b""):
        """读取 WebSocket 帧，拆出业务 payload 交给 _decode_frames"""
        buf = initial
        sock.settimeout(self._HEARTBEAT_INTERVAL * 2)
        while not self._stop_event.is_set():
            while True:
                frame, consumed = self._ws_decode(buf)
                if frame is None:
                    break
                buf = buf[consumed:]
                opcode, payload = frame
                if opcode == 0x8:  # close
                    logging.warning(f"💬 {self.room_id} 服务器发送 close 帧 payload={payload[:16]!r}")
                    return
                if opcode in (0x1, 0x2) and payload:
                    self._decode_frames(payload)
            try:
                chunk = sock.recv(65536)
            except socket.timeout:
                logging.debug(f"💬 {self.room_id} recv 超时，继续等待")
                continue
            except OSError as e:
                logging.warning(f"💬 {self.room_id} recv OSError: {e}")
                break
            if not chunk:
                logging.warning(f"💬 {self.room_id} 服务器关闭连接（recv 返回空）")
                break
            buf += chunk

    @staticmethod
    def _ws_decode(buf: bytes):
        """解析一个服务器帧（无掩码）。返回 ((opcode, payload), consumed) 或 (None, 0)"""
        if len(buf) < 2:
            return None, 0
        b0, b1 = buf[0], buf[1]
        opcode = b0 & 0x0F
        masked = b1 & 0x80
        length = b1 & 0x7F
        idx = 2
        if length == 126:
            if len(buf) < 4:
                return None, 0
            length = struct.unpack_from(">H", buf, 2)[0]
            idx = 4
        elif length == 127:
            if len(buf) < 10:
                return None, 0
            length = struct.unpack_from(">Q", buf, 2)[0]
            idx = 10
        if masked:
            if len(buf) < idx + 4:
                return None, 0
            mask = buf[idx:idx + 4]
            idx += 4
        if len(buf) < idx + length:
            return None, 0
        payload = buf[idx:idx + length]
        if masked:
            payload = bytes(c ^ mask[i % 4] for i, c in enumerate(payload))
        return (opcode, payload), idx + length
