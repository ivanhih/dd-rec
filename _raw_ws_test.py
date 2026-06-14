"""用原始 socket 直接发 auth 包，绕过 websocket-client"""
import socket, ssl, struct, json, base64, time, urllib.request

host = 'hw-sg-live-comet-01.chat.bilibili.com'
port = 443
room_id = 23121424
buvid3 = '13E96849-5A98-1DA0-45BB-516A1D3BA8A185241infoc'

# 获取 token（不用 WBI 签名，看看 -352 还是成功）
url = f'https://api.live.bilibili.com/xlive/web-room/v1/index/getDanmuInfo?id={room_id}&type=0'
req = urllib.request.Request(url, headers={
    'User-Agent': 'Mozilla/5.0',
    'Cookie': f'buvid3={buvid3}'
})
d = json.loads(urllib.request.urlopen(req, timeout=10).read())
print(f'danmuinfo code={d["code"]} msg={d.get("message")}')
if d['code'] != 0:
    print('failed to get token'); exit(1)
token = d['data']['token']
h = d['data']['host_list'][0]
ws_host = h['host']
ws_port = h['wss_port']
print(f'token={token[:12]}... ws={ws_host}:{ws_port}')

# 构造 B站 auth 包
payload = json.dumps({
    'uid': 0, 'roomid': room_id, 'protover': 3,
    'buvid': buvid3, 'platform': 'web', 'type': 2, 'key': token,
    'support_ack': 'true', 'queue_uuid': 'abcd1234', 'scene': 'room',
}, ensure_ascii=False).encode()
auth_pkt = struct.pack('>IHHII', 16 + len(payload), 16, 1, 7, 1) + payload
print(f'auth packet size: {len(auth_pkt)}b')

# WebSocket 帧封装（客户端必须 mask）
def ws_frame(data):
    ln = len(data)
    mask = b'\x00\x00\x00\x00'
    if ln <= 125:
        return bytes([0x82, 0x80 | ln]) + mask + data
    elif ln <= 65535:
        return bytes([0x82, 0xFE]) + struct.pack('>H', ln) + mask + data
    else:
        return bytes([0x82, 0xFF]) + struct.pack('>Q', ln) + mask + data

# 连接
ctx = ssl.create_default_context()
raw = socket.create_connection((ws_host, ws_port), timeout=8)
sock = ctx.wrap_socket(raw, server_hostname=ws_host)

# HTTP Upgrade
ws_key = base64.b64encode(b'bilibili12345678').decode()
upgrade = (
    f'GET /sub HTTP/1.1\r\n'
    f'Host: {ws_host}\r\n'
    f'Upgrade: websocket\r\n'
    f'Connection: Upgrade\r\n'
    f'Sec-WebSocket-Key: {ws_key}\r\n'
    f'Sec-WebSocket-Version: 13\r\n'
    f'Origin: https://live.bilibili.com\r\n'
    f'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n'
    f'\r\n'
)
sock.send(upgrade.encode())
resp = sock.recv(4096)
got_101 = b'101' in resp
print(f'HTTP 101: {got_101}')
if not got_101:
    print(f'Response: {resp[:200]}')
    exit(1)

# 发 auth
frame = ws_frame(auth_pkt)
sock.send(frame)
print(f'auth frame sent ({len(frame)}b)')

# 读响应（最多等5秒）
sock.settimeout(5)
try:
    data = sock.recv(8192)
    print(f'response ({len(data)}b) hex: {data[:60].hex()}')
    # 尝试解析 WebSocket 帧
    if len(data) >= 2:
        fin_op = data[0]
        opcode = fin_op & 0x0F
        masked = (data[1] & 0x80) != 0
        plen = data[1] & 0x7F
        print(f'ws frame: opcode={opcode:#04x} masked={masked} payload_len_indicator={plen}')
        # 读完整 payload
        offset = 2
        if plen == 126: plen = struct.unpack('>H', data[2:4])[0]; offset = 4
        elif plen == 127: plen = struct.unpack('>Q', data[2:10])[0]; offset = 10
        body = data[offset:offset+plen]
        print(f'payload ({len(body)}b): {body[:200]}')
        # 尝试解析 B站包头
        if len(body) >= 16:
            total, hlen, proto, op, seq = struct.unpack_from('>IHHII', body)
            print(f'bili header: total={total} proto={proto} op={op}')
            msg_body = body[hlen:total]
            print(f'bili body: {msg_body[:200]}')
except socket.timeout:
    print('timeout - server sent nothing after auth')
except Exception as e:
    print(f'recv error: {type(e).__name__}: {e}')

sock.close()
