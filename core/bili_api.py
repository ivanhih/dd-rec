# core/bili_api.py
import re
import logging
from curl_cffi import requests
from .config import get_headers, get_global_setting, get_room_config, get_effective_format, get_room_setting, reload_config



def extract_room_id(url_or_id):
    s = str(url_or_id).strip()
    match = re.search(r"live\.bilibili\.com/(\d+)", s)
    if match:
        return match.group(1)
    match = re.search(r"\d+", s)
    return match.group(0) if match else None


def get_bili_info(url_or_id, room_id_for_cookie=None, silent=False):
    room_id = extract_room_id(url_or_id)
    if not room_id:
        return None

    if not silent:
        logging.info(f"👉 提取到房间号: {room_id}，正在获取数据...")

    try:
        headers = get_headers(room_id_for_cookie or room_id, monitor=silent)

        # room_init
        res_init = requests.get(
            f"https://api.live.bilibili.com/room/v1/Room/room_init?id={room_id}",
            headers=headers, impersonate="chrome110", timeout=10
        ).json()
        if res_init.get("code") != 0:
            return None

        real_room_id = res_init.get("data", {}).get("room_id")
        live_status = res_init.get("data", {}).get("live_status", 0)

        # get_info
        res_info = requests.get(
            f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={real_room_id}",
            headers=headers, impersonate="chrome110", timeout=10
        ).json()

        title = "未知标题"
        parent_area_name = "未知分区"
        area_name = "未知内容"
        cover = ""

        if res_info.get("code") == 0:
            data = res_info.get("data", {})
            title = data.get("title", "未知标题")
            parent_area_name = data.get("parent_area_name", "未知分区")
            area_name = data.get("area_name", "未知内容")
            # user_cover 是主播设置的直播间封面，优先使用；keyframe 是实时截帧作备选
            cover = (data.get("user_cover") or data.get("keyframe") or "").strip()

        # anchor info
        res_anchor = requests.get(
            f"https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room?roomid={real_room_id}",
            headers=headers, impersonate="chrome110", timeout=10
        ).json()

        uname = f"主播_{real_room_id}"
        face = ""

        if res_anchor.get("code") == 0:
            uname = res_anchor.get("data", {}).get("info", {}).get("uname", uname)
            face = res_anchor.get("data", {}).get("info", {}).get("face", "")
            if face:
                face = face.strip().strip('`').strip('"').strip("'")

        if not silent:
            logging.info(f"✅ 获取信息: {uname} ({real_room_id}) - [{parent_area_name}/{area_name}]")

        return {
            "room_id": str(real_room_id),
            "uname": uname,
            "title": title,
            "live_status": live_status,
            "parent_area_name": parent_area_name,
            "area_name": area_name,
            "face": face,
            "cover": cover
        }
    except Exception as e:
        if not silent:
            logging.error(f"❌ 获取信息失败: {e}")
        return None


def get_stream_info(real_room_id):
    # 关键：每次录制/查询都从磁盘 reload 配置 —— 这样用户在 UI 上修改 stream_codec 后
    # 下次录制立即生效，无需重启 bilirec。
    reload_config()

    cfg_codec = get_room_setting(real_room_id, "stream_codec") or get_global_setting("stream_codec") or "av1"
    cfg_format = get_room_setting(real_room_id, "stream_format") or get_global_setting("stream_format") or "fmp4"
    cfg_url_priority = get_global_setting("stream_url_priority") or ""
    priority_param = get_global_setting("stream_priority_param") or "分辨率"
    cfg_fps = get_global_setting("stream_fps") or ""
    cfg_bitrate = get_global_setting("stream_bitrate") or ""
    allow_audio_only = get_global_setting("allow_audio_only")

    # 关键：B 站 API 的 codec 名称跟我们 UI 用的不一样
    #   - h264  → "avc"
    #   - h265  → "hevc"
    #   - av1   → "av1"
    # 把 UI 配置的 codec 翻译成 B 站 API 实际用的名字再匹配
    _codec_alias = {"h264": "avc", "h265": "hevc", "hevc": "hevc", "avc": "avc", "av1": "av1"}
    cfg_codec_api = _codec_alias.get(cfg_codec.lower(), cfg_codec.lower())

    res_map = {"原画": 10000, "超清": 400, "高清": 250, "流畅": 150}
    # 关键：先读房间级覆盖，缺失再回退全局；没有覆盖时按全局走（保持向后兼容）
    cfg_res_str = (
        get_room_setting(real_room_id, "stream_resolution")
        or get_global_setting("stream_resolution")
        or "原画"
    )
    qn = res_map.get(cfg_res_str, 10000)

    url = f"https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo?room_id={real_room_id}&protocol=0,1&format=0,1,2&codec=0,1,2&qn={qn}&platform=web"

    try:
        headers = get_headers(real_room_id, monitor=True)
        
        res = requests.get(url, headers=headers, impersonate="chrome110", timeout=10).json()
        if res.get("code") != 0: return None

        streams = res.get("data", {}).get("playurl_info", {}).get("playurl", {}).get("stream", [])
        available_streams = []

        for stream in streams:
            if not allow_audio_only and stream.get("protocol_name") == "audio":
                continue

            for format_obj in stream.get("format", []):
                for codec_obj in format_obj.get("codec", []):
                    base_url = codec_obj.get("base_url", "")
                    url_info = codec_obj.get("url_info", [])
                    if url_info and base_url:
                        full_url = url_info[0].get("host", "") + base_url + url_info[0].get("extra", "")
                        available_streams.append({
                            "url": full_url,
                            "base_url": base_url,
                            "codec": codec_obj.get("codec_name", ""),
                            "format": format_obj.get("format_name", ""),
                            "qn": codec_obj.get("current_qn", 0),
                            "fps": str(codec_obj.get("frame_rate", "")),
                            "bitrate": str(codec_obj.get("bitrate", ""))
                        })

        if not available_streams: return None

        if cfg_url_priority:
            filtered = [s for s in available_streams if cfg_url_priority in s['url']]
            if filtered: available_streams = filtered

        def sort_stream(s):
            score = 0
            if priority_param == "编码" and s["codec"] == cfg_codec_api:
                score += 10000
            elif priority_param == "格式" and s["format"] == cfg_format:
                score += 10000
            elif priority_param == "分辨率":
                score += s["qn"] * 10
            elif priority_param == "帧率" and cfg_fps:
                # cfg_fps 格式如 "60 fps"，取数字部分
                try:
                    target_fps = float(cfg_fps.split()[0])
                    stream_fps = float(s["fps"]) if s["fps"] else 0
                    if abs(stream_fps - target_fps) < 1:
                        score += 10000
                except (ValueError, IndexError):
                    pass
            elif priority_param == "码率" and cfg_bitrate:
                # cfg_bitrate 格式如 "30.0 Mb/s"，转成 kbps 与 API 返回的 kbps 比较
                try:
                    val, unit = cfg_bitrate.split()[:2]
                    val = float(val)
                    unit = unit.lower()
                    if "mb" in unit:
                        target_kbps = val * 1000
                    elif "kb" in unit:
                        target_kbps = val
                    else:
                        target_kbps = val
                    stream_kbps = float(s["bitrate"]) if s["bitrate"] else 0
                    # 误差在 20% 以内就加分
                    if target_kbps > 0 and abs(stream_kbps - target_kbps) / target_kbps < 0.2:
                        score += 10000
                    # 码率越接近目标越高分
                    if target_kbps > 0:
                        score += max(0, 5000 - int(abs(stream_kbps - target_kbps)))
                except (ValueError, IndexError):
                    pass
            elif priority_param == "网址" and cfg_url_priority and cfg_url_priority in s["url"]:
                score += 10000
            # 关键：把 cfg_codec 编码（用户配置的，比如 h264）的优先级**大幅**提高。
            # 否则 B 站 API 默认返回 av1 在前，按 +100 加分也排不赢 av1。
            if s["codec"] == cfg_codec_api:
                score += 100000  # 大幅超过 qn 维度（qn*10 最多 10000）
            if s["format"] == cfg_format: score += 100
            score += s["qn"]
            return score

        available_streams.sort(key=sort_stream, reverse=True)
        best = available_streams[0]
        logging.info(f"👉 {real_room_id} 匹配最优: 画质qn[{best['qn']}], 编码[{best['codec']}], 格式[{best['format']}]")
        return best

    except Exception as e:
        logging.error(f"⚠️ 获取流地址失败: {e}")
    return None
