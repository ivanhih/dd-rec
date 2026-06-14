"""用 curl_cffi WebSocket 连接测试"""
import hashlib, hmac, json, struct, sys, time, zlib, urllib.parse

ROOM_ID = int(sys.argv[1]) if len(sys.argv) > 1 else 23121424
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

from curl_cffi import requests as cr

def fetch_buvid():
    r = cr.get("https://api.bilibili.com/x/frontend/finger/spi",
               headers={"User-Agent": UA, "Referer": "https://www.bilibili.com/"},
               impersonate="chrome110", timeout=8)
    d = r.json()
    if d.get("code") == 0:
        return d["data"]["b_3"], d["data"]["b_4"]
    return "", ""

def fetch_ticket(buvid3, buvid4):
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
    if d.get("code") == 0:
        img = d["data"].get("nav",{}).get("img","")
        sub = d["data"].get("nav",{}).get("sub","")
        return (d["data"]["ticket"],
                img.rsplit("/",1)[-1].replace(".png",""),
                sub.rsplit("/",1)[-1].replace(".png",""))
    return "", "", ""

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
    import hashlib as _h
    p["w_rid"] = _h.md5((query + mk).encode()).hexdigest()
    return p

def get_danmu_info(room_id, buvid3, buvid4, ticket, img_key, sub_key):
    params = wbi_sign({"id": room_id, "type": 0}, img_key, sub_key)
    query = urllib.parse.urlencode(params)
    url = f"https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?{query}"
    b_nut = str(int(time.time()))
    cookies = [f"buvid3={buvid3}"]
    if buvid4: cookies += [f"buvid4={buvid4}", f"b_nut={b_nut}"]
    if ticket: cookies.append(f"bili_ticket={ticket}")
    r = cr.get(url, headers={
        "User-Agent": UA,
        "Referer": f"https://live.bilibili.com/{room_id}",
        "Origin": "https://live.bilibili.com",
        "Cookie": "; ".join(cookies),
    }, impersonate="chrome110", timeout=10)
    d = r.json()
    print(f"[danmuinfo] code={d.get('code')}")
    if d.get("code") == 0:
        h = d["data"]["host_list"][0]
        return f"wss://{h['host']}:{h['wss_port']}/sub", d["data"]["token"]
    return "", ""

def pack(data: bytes, protocol=1, op=2) -> bytes:
    return struct.pack(">IHHII", 16 + len(data), 16, protocol, op, 1) + data

def decode_frames(raw: bytes, depth=0):
    offset = 0
    while offset < len(raw):
        if offset + 16 > len(raw): break
        total, hlen, proto, op, _ = struct.unpack_from(">IHHII", raw, offset)
        if total < 16 or offset + total > len(raw): break
        body = raw[offset+hlen: offset+total]
        print(f"{'  '*depth}frame proto={proto} op={op} body={len(body)}b")
        if proto == 2:
            try: decode_frames(zlib.decompress(body), depth+1)
            except Exception as e: print(f"{'  '*depth}  zlib err: {e}")
        elif proto == 3:
            try:
                import brotli
                decode_frames(brotli.decompress(body), depth+1)
            except Exception as e: print(f"{'  '*depth}  brotli err: {e}")
        elif op == 5:
            try:
                msg = json.loads(body.decode("utf-8","ignore"))
                print(f"{'  '*depth}  CMD: {msg.get('cmd','?')}")
            except: pass
        elif op == 8:
            print(f"{'  '*depth}  AUTH REPLY: {body}")
        offset += total

# ── main ──────────────────────────────────────────────────────────────
print(f"=== curl_cffi WebSocket 测试 room={ROOM_ID} ===")
buvid3, buvid4 = fetch_buvid()
print(f"buvid3={buvid3[:20]}")
ticket, img_key, sub_key = fetch_ticket(buvid3, buvid4)
print(f"ticket={ticket[:12]}...")
ws_url, token = get_danmu_info(ROOM_ID, buvid3, buvid4, ticket, img_key, sub_key)
print(f"ws_url={ws_url}")
if not token:
    print("no token"); sys.exit(1)

import uuid
auth_body = json.dumps({
    "uid": 0, "roomid": ROOM_ID, "protover": 3,
    "buvid": buvid3, "platform": "web", "type": 2, "key": token,
    "support_ack": "true", "queue_uuid": uuid.uuid4().hex[:8], "scene": "room",
}, ensure_ascii=False).encode()
auth_pkt = pack(auth_body, protocol=1, op=7)
hb_pkt = pack(b"[object Object]", protocol=1, op=2)

print(f"\n[ws] connecting via curl_cffi...")
ws = cr.WebSocket()
ws.connect(ws_url, headers={
    "Origin": "https://live.bilibili.com",
    "User-Agent": UA,
}, impersonate="chrome110")
print("[ws] connected, sending auth...")
ws.send_bytes(auth_pkt)

count = 0
start = time.time()
while time.time() - start < 15:
    try:
        data, flags = ws.recv()
        if not data:
            print("[ws] empty recv, breaking")
            break
        print(f"[ws] recv {len(data)}b flags={flags}:")
        decode_frames(data)
        count += 1
        if count == 1:
            ws.send_bytes(hb_pkt)
        if count >= 5:
            break
    except Exception as e:
        print(f"[ws] recv error: {e}")
        break

print(f"\ntotal messages: {count}")
ws.close()
