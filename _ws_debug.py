"""
WebSocket 弹幕连接调试脚本
直接连接 B站弹幕服务器，打印服务器返回的每一帧原始数据
用法: python _ws_debug.py <room_id>
"""
import hashlib, hmac, json, struct, sys, time, zlib, urllib.parse

ROOM_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 23121424
SESSDATA = sys.argv[3] if len(sys.argv) > 3 else ""
# 如果命令行传入了固定 buvid3，就用它而不是重新获取
FIXED_BUVID3 = sys.argv[4] if len(sys.argv) > 4 else ""
FIXED_BUVID4 = sys.argv[5] if len(sys.argv) > 5 else ""
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# ── 1. 获取 buvid3 ────────────────────────────────────────────────────
def fetch_buvid():
    from curl_cffi import requests as cr
    r = cr.get("https://api.bilibili.com/x/frontend/finger/spi",
               headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
               impersonate="chrome110", timeout=8)
    d = r.json()
    print(f"[buvid] code={d.get('code')}")
    if d.get("code") == 0:
        return d["data"]["b_3"], d["data"]["b_4"]
    return "", ""

# ── 1.5 激活 buvid3 (ExClimbWuzhi) ───────────────────────────────────
def activate_buvid(buvid3, buvid4, ticket):
    from curl_cffi import requests as cr
    payload = {
        "3064": 1,
        "5062": str(int(time.time() * 1000)),
        "03bf": "https://www.bilibili.com/",
        "39c8": "333.1007.fp.risk",
        "34f1": "",
        "d402": "",
        "654a": "",
        "6e7c": "839x959",
        "3c43": {"adca": "Win32", "bfe9": "ab123"},
    }
    body = json.dumps({"payload": json.dumps(payload, ensure_ascii=False)},
                      ensure_ascii=False)
    b_nut = str(int(time.time()))
    cookies = [f"buvid3={buvid3}"]
    if buvid4: cookies += [f"buvid4={buvid4}", f"b_nut={b_nut}"]
    if ticket: cookies.append(f"bili_ticket={ticket}")
    try:
        r = cr.post(
            "https://api.bilibili.com/x/internal/gaia-gateway/ExClimbWuzhi",
            data=body.encode(),
            headers={
                "User-Agent": UA,
                "Referer": "https://www.bilibili.com/",
                "Origin": "https://www.bilibili.com",
                "Content-Type": "application/json",
                "Cookie": "; ".join(cookies),
            }, impersonate="chrome110", timeout=8)
        d = r.json()
        print(f"[activate] code={d.get('code')} msg={d.get('message')}")
    except Exception as e:
        print(f"[activate] error: {e}")

# ── 2. 获取 bili_ticket + WBI key ─────────────────────────────────────
def fetch_ticket(buvid3, buvid4):
    from curl_cffi import requests as cr
    ts = int(time.time())
    sig = hmac.new(b"XgwSnGZ1p", f"ts{ts}".encode(), hashlib.sha256).hexdigest()
    params = urllib.parse.urlencode({"key_id":"ec02","hexsign":sig,"context[ts]":ts,"csrf":""})
    url = f"https://api.bilibili.com/bapis/bilibili.api.ticket.v1.Ticket/GenWebTicket?{params}"
    r = cr.post(url, data=b"", headers={
        "User-Agent": UA, "Referer": "https://www.bilibili.com/",
        "Cookie": f"buvid3={buvid3}; buvid4={buvid4}; b_nut={ts}",
        "Content-Type": "application/x-www-form-urlencoded",
    }, impersonate="chrome110", timeout=8)
    d = r.json()
    print(f"[ticket] code={d.get('code')}")
    if d.get("code") == 0:
        img = d["data"].get("nav",{}).get("img","")
        sub = d["data"].get("nav",{}).get("sub","")
        return (d["data"]["ticket"],
                img.rsplit("/",1)[-1].replace(".png",""),
                sub.rsplit("/",1)[-1].replace(".png",""))
    return "", "", ""

# ── 3. WBI 签名 ────────────────────────────────────────────────────────
TAB = [46,47,18,2,53,8,23,32,15,50,10,31,58,3,45,35,
       27,43,5,49,33,9,42,19,29,28,14,39,12,38,41,13,
       37,48,7,16,24,55,40,61,26,17,0,1,60,51,30,4,
       22,25,54,21,56,59,6,63,57,62,11,36,20,34,44,52]

def wbi_sign(params, img_key, sub_key):
    raw = img_key + sub_key
    mk = "".join(raw[i] for i in TAB if i < len(raw))[:32]
    wts = int(time.time())
    p = dict(params); p["wts"] = wts
    p = dict(sorted(p.items()))
    filtered = {k: "".join(c for c in str(v) if c not in "!'()*") for k,v in p.items()}
    query = urllib.parse.urlencode(filtered)
    p["w_rid"] = hashlib.md5((query + mk).encode()).hexdigest()
    return p

# ── 4. getDanmuInfo ────────────────────────────────────────────────────
def get_danmu_info(room_id, buvid3, buvid4, ticket, img_key, sub_key):
    from curl_cffi import requests as cr
    params = wbi_sign({"id": room_id, "type": 0}, img_key, sub_key)
    query = urllib.parse.urlencode(params)
    url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?{query}"
    b_nut = str(int(time.time()))
    cookies = [f"buvid3={buvid3}"]
    if buvid4: cookies += [f"buvid4={buvid4}", f"b_nut={b_nut}"]
    if ticket: cookies.append(f"bili_ticket={ticket}")
    if SESSDATA: cookies.append(f"SESSDATA={SESSDATA}")
    r = cr.get(url, headers={
        "User-Agent": UA,
        "Referer": f"https://live.bilibili.com/{room_id}",
        "Origin": "https://live.bilibili.com",
        "Cookie": "; ".join(cookies),
    }, impersonate="chrome110", timeout=10)
    d = r.json()
    print(f"[danmuinfo] code={d.get('code')} msg={d.get('message')}")
    if d.get("code") == 0:
        h = d["data"]["host_list"][0]
        return f"wss://{h['host']}:{h['wss_port']}/sub", d["data"]["token"]
    return "", ""

# ── 5. WebSocket 连接 ──────────────────────────────────────────────────
def pack(data: bytes, protocol=1, op=2) -> bytes:
    return struct.pack(">IHHII", 16 + len(data), 16, protocol, op, 1) + data

def auth_packet(room_id, token, buvid3, protover):
    import uuid
    body = {
        "uid": 0, "roomid": room_id, "protover": protover,
        "buvid": buvid3, "platform": "web", "type": 2, "key": token,
        "support_ack": "true", "queue_uuid": uuid.uuid4().hex[:8], "scene": "room",
    }
    print(f"[auth] payload: {json.dumps(body, ensure_ascii=False)}")
    payload = json.dumps(body, ensure_ascii=False).encode()
    return pack(payload, protocol=1, op=7)

def decode_frame(raw: bytes, depth=0):
    offset = 0
    while offset < len(raw):
        if offset + 16 > len(raw): break
        total, hlen, proto, op, _ = struct.unpack_from(">IHHII", raw, offset)
        if total < 16 or offset + total > len(raw): break
        body = raw[offset+hlen: offset+total]
        print(f"  {'  '*depth}frame: total={total} proto={proto} op={op} bodylen={len(body)}")
        if proto == 2:
            try: decode_frame(zlib.decompress(body), depth+1)
            except Exception as e: print(f"  {'  '*depth}  zlib err: {e}")
        elif proto == 3:
            try:
                import brotli
                decode_frame(brotli.decompress(body), depth+1)
            except Exception as e: print(f"  {'  '*depth}  brotli err: {e}")
        elif op == 5:
            try:
                msg = json.loads(body.decode("utf-8","ignore"))
                cmd = msg.get("cmd","?")
                print(f"  {'  '*depth}  CMD: {cmd}")
            except: pass
        elif op == 8:
            print(f"  {'  '*depth}  AUTH REPLY: {body[:80]}")
        offset += total

def run_ws(ws_url, token, buvid3, protover):
    import websocket
    print(f"\n[ws] connecting protover={protover} url={ws_url}")
    received = []

    def on_open(ws):
        import os as _os
        if _os.environ.get("NO_AUTH") == "1":
            print("[ws] NO_AUTH=1, 不发送 auth，仅观察连接是否保持")
        else:
            pkt = auth_packet(ROOM_ID, token, buvid3, protover)
            print(f"[ws] sending auth ({len(pkt)}b), buvid3={buvid3[:16]}")
            ws.send(pkt, opcode=0x2)
        # 10秒后主动关闭
        import threading
        def _close():
            time.sleep(10)
            ws.close()
        threading.Thread(target=_close, daemon=True).start()

    def on_message(ws, msg):
        data = msg if isinstance(msg, bytes) else msg.encode()
        received.append(data)
        print(f"[ws] message {len(data)}b:")
        decode_frame(data)

    def on_error(ws, err):
        print(f"[ws] ERROR: {err}")

    def on_close(ws, code, reason):
        print(f"[ws] CLOSED code={code} reason={reason}")
        print(f"[ws] total messages received: {len(received)}")

    host = ws_url.replace("wss://","").replace("ws://","").split("/")[0].split(":")[0]
    headers = {
        "Origin": "https://live.bilibili.com",
        "User-Agent": UA,
        "Referer": f"https://live.bilibili.com/{ROOM_ID}",
    }
    _ck = []
    if buvid3: _ck.append(f"buvid3={buvid3}")
    if _DBG_BUVID4: _ck.append(f"buvid4={_DBG_BUVID4}")
    if _DBG_TICKET: _ck.append(f"bili_ticket={_DBG_TICKET}")
    if _ck:
        headers["Cookie"] = "; ".join(_ck)
        print(f"[ws] handshake Cookie: {headers['Cookie'][:60]}...")
    ws = websocket.WebSocketApp(ws_url, on_open=on_open, on_message=on_message,
                                 on_error=on_error, on_close=on_close,
                                 header=headers)
    ws.run_forever(ping_interval=0)

# ── main ───────────────────────────────────────────────────────────────
print(f"=== 弹幕调试 room={ROOM_ID} ===")
if FIXED_BUVID3:
    buvid3, buvid4 = FIXED_BUVID3, FIXED_BUVID4
    print(f"[buvid] 使用固定 buvid3")
else:
    buvid3, buvid4 = fetch_buvid()
print(f"buvid3={buvid3[:20]} buvid4={buvid4[:20]}")

ticket, img_key, sub_key = fetch_ticket(buvid3, buvid4)
print(f"ticket={ticket[:12]}... img_key={img_key[:8]}...")
_DBG_BUVID4 = buvid4
_DBG_TICKET = ticket
activate_buvid(buvid3, buvid4, ticket)

ws_url, token = get_danmu_info(ROOM_ID, buvid3, buvid4, ticket, img_key, sub_key)
print(f"ws_url={ws_url} token={token[:12]}...")

if not token:
    print("ERROR: 无法获取 token，退出")
    sys.exit(1)

import sys as _sys
protover = int(_sys.argv[2]) if len(_sys.argv) > 2 else 3
print(f"\n--- 测试 protover={protover} ---")
run_ws(ws_url, token, buvid3, protover=protover)
