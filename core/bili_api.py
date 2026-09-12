# core/bili_api.py
import re
import logging
from urllib.parse import urlparse
from curl_cffi import requests
from .config import get_headers, get_global_setting, get_room_config, get_effective_format, get_room_setting, reload_config



def extract_room_id(url_or_id):
    s = str(url_or_id).strip()
    match = re.search(r"live\.bilibili\.com/(\d+)", s)
    if match:
        return match.group(1)
    match = re.search(r"\d+", s)
    return match.group(0) if match else None


def build_endpoint_identity(host: str, base_url: str) -> str:
    """Stable CDN identity without stream paths or signed fields."""
    host = (host or "").strip()
    raw = host if "://" in host else f"https://{host.lstrip('/')}"
    try:
        parsed = urlparse(raw)
    except Exception:
        parsed = None
    if parsed and (parsed.scheme or parsed.netloc or parsed.path):
        scheme = (parsed.scheme or "https").lower()
        hostname = (parsed.hostname or parsed.netloc or "").lower()
        port = parsed.port
        host_part = hostname
        if port and not (
            (scheme == "https" and port == 443)
            or (scheme == "http" and port == 80)
        ):
            host_part = f"{hostname}:{port}"
        if not host_part:
            host_part = host.lower()
    else:
        scheme = "https"
        host_part = host.lower()
    return f"{scheme}://{host_part}"


def build_cdn_candidate(host: str, base_url: str, extra: str = "") -> dict:
    host = host or ""
    base_url = base_url or ""
    extra = extra or ""
    return {
        "url": f"{host}{base_url}{extra}",
        "host": host,
        "extra": extra,
        "endpoint_id": build_endpoint_identity(host, base_url),
    }


def build_cdn_candidates(base_url: str, url_info: list, *, cfg_url_priority: str = "") -> list[dict]:
    """Expand every url_info entry with its own host/extra pair."""
    candidates: list[dict] = []
    for item in url_info or []:
        if not isinstance(item, dict):
            continue
        host = item.get("host", "") or ""
        extra = item.get("extra", "") or ""
        if not host and not base_url:
            continue
        candidates.append(build_cdn_candidate(host, base_url, extra))
    return prefer_cdn_candidates(candidates, cfg_url_priority)


def prefer_cdn_candidates(candidates: list[dict], cfg_url_priority: str = "") -> list[dict]:
    """Stable reorder only; never drop non-matching candidates."""
    items = [dict(c) for c in (candidates or []) if isinstance(c, dict) and c.get("url")]
    pref = (cfg_url_priority or "").strip()
    if not pref or not items:
        return items
    matched = []
    others = []
    for c in items:
        hay = " ".join(
            str(c.get(k) or "")
            for k in ("url", "host", "endpoint_id")
        )
        if pref in hay:
            matched.append(c)
        else:
            others.append(c)
    return matched + others if matched else items


def build_stream_entry(
    *,
    base_url: str,
    url_info: list,
    codec: str,
    format_name: str,
    qn: int | str = 0,
    fps: str = "",
    bitrate: str = "",
    cfg_url_priority: str = "",
) -> dict | None:
    candidates = build_cdn_candidates(base_url, url_info, cfg_url_priority=cfg_url_priority)
    if not candidates:
        return None
    return {
        "url": candidates[0]["url"],
        "base_url": base_url,
        "codec": codec,
        "format": format_name,
        "qn": qn,
        "fps": str(fps),
        "bitrate": str(bitrate),
        "cdn_candidates": candidates,
    }


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


# UI / 配置 codec → B 站 playurl API codec_name
CODEC_ALIAS = {"h264": "avc", "h265": "hevc", "hevc": "hevc", "avc": "avc", "av1": "av1"}


def normalize_codec_name(codec: str) -> str:
    c = (codec or "").strip().lower()
    return CODEC_ALIAS.get(c, c)


def select_best_stream(
    available_streams: list,
    *,
    cfg_codec: str = "h264",
    cfg_format: str = "flv",
    fallback_policy: str = "compatible",
    priority_param: str = "分辨率",
    cfg_fps: str = "",
    cfg_bitrate: str = "",
    cfg_url_priority: str = "",
    room_id: str = "",
) -> dict | None:
    """从候选流中选择最优项。纯函数，便于单测。

    返回带 match_kind / requested_* 元数据的 stream dict；strict 且无匹配时返回 None。
    不会在无提示情况下静默回到无关编码：非 exact 匹配都会打 warning/error。
    URL 偏好只重排已选规格内部的 CDN 候选，不参与规格排名、不丢弃备选。
    """
    if not available_streams:
        return None

    streams = list(available_streams)
    cfg_codec_api = normalize_codec_name(cfg_codec)
    cfg_format = (cfg_format or "flv").strip().lower()
    fallback_policy = (fallback_policy or "compatible").strip().lower()
    rid = room_id or "?"

    def sort_stream(s):
        score = 0
        if priority_param == "编码" and s.get("codec") == cfg_codec_api:
            score += 10000
        elif priority_param == "格式" and s.get("format") == cfg_format:
            score += 10000
        elif priority_param == "分辨率":
            score += int(s.get("qn") or 0) * 10
        elif priority_param == "帧率" and cfg_fps:
            try:
                target_fps = float(str(cfg_fps).split()[0])
                stream_fps = float(s.get("fps") or 0)
                if abs(stream_fps - target_fps) < 1:
                    score += 10000
            except (ValueError, IndexError, TypeError):
                pass
        elif priority_param == "码率" and cfg_bitrate:
            try:
                parts = str(cfg_bitrate).split()[:2]
                val = float(parts[0])
                unit = (parts[1] if len(parts) > 1 else "").lower()
                if "mb" in unit:
                    target_kbps = val * 1000
                elif "kb" in unit:
                    target_kbps = val
                else:
                    target_kbps = val
                stream_kbps = float(s.get("bitrate") or 0)
                if target_kbps > 0 and abs(stream_kbps - target_kbps) / target_kbps < 0.2:
                    score += 10000
                if target_kbps > 0:
                    score += max(0, 5000 - int(abs(stream_kbps - target_kbps)))
            except (ValueError, IndexError, TypeError):
                pass
        # 目标编码优先级远高于 qn。URL 偏好不参与规格打分。
        if s.get("codec") == cfg_codec_api:
            score += 100000
        if s.get("format") == cfg_format:
            score += 100
        score += int(s.get("qn") or 0)
        return score

    streams.sort(key=sort_stream, reverse=True)

    exact = [s for s in streams if s.get("codec") == cfg_codec_api and s.get("format") == cfg_format]
    same_codec = [s for s in streams if s.get("codec") == cfg_codec_api]
    if exact:
        best = exact[0]
        match_kind = "exact"
    elif fallback_policy == "strict":
        logging.error(
            f"❌ {rid} 无目标流组合 codec={cfg_codec_api} format={cfg_format}（strict），拒绝静默降级"
        )
        return None
    elif same_codec and fallback_policy in ("compatible", "best_effort", ""):
        best = same_codec[0]
        match_kind = f"codec_only:{best.get('format')}"
        logging.warning(
            f"⚠️ {rid} 无 {cfg_codec_api}+{cfg_format}，compatible 回退到 "
            f"{best.get('codec')}+{best.get('format')} qn={best.get('qn')}"
        )
    elif fallback_policy == "best_effort":
        best = streams[0]
        match_kind = f"best_effort:{best.get('codec')}+{best.get('format')}"
        logging.warning(
            f"⚠️ {rid} best_effort 选用 {best.get('codec')}+{best.get('format')} qn={best.get('qn')}"
        )
    else:
        # compatible 但没有同编码：明确降级并告警（非静默）
        best = streams[0]
        match_kind = f"fallback:{best.get('codec')}+{best.get('format')}"
        logging.warning(
            f"⚠️ {rid} 无同编码流，降级 {best.get('codec')}+{best.get('format')} qn={best.get('qn')}"
        )

    best = dict(best)
    candidates = list(best.get("cdn_candidates") or [])
    if not candidates and best.get("url"):
        candidates = [
            {
                "url": best.get("url"),
                "host": "",
                "extra": "",
                "endpoint_id": build_endpoint_identity("", best.get("base_url") or ""),
            }
        ]
    candidates = prefer_cdn_candidates(candidates, cfg_url_priority)
    if candidates:
        best["cdn_candidates"] = candidates
        best["url"] = candidates[0]["url"]
    best["match_kind"] = match_kind
    best["requested_codec"] = cfg_codec_api
    best["requested_format"] = cfg_format
    logging.info(
        f"👉 {rid} 匹配最优[{match_kind}]: 画质qn[{best.get('qn')}], "
        f"编码[{best.get('codec')}], 格式[{best.get('format')}], "
        f"CDN候选[{len(best.get('cdn_candidates') or [])}]"
    )
    return best


def get_stream_info(real_room_id):
    # 关键：每次录制/查询都从磁盘 reload 配置 —— 这样用户在 UI 上修改 stream_codec 后
    # 下次录制立即生效，无需重启 bilirec。
    reload_config()

    cfg_codec = get_room_setting(real_room_id, "stream_codec") or get_global_setting("stream_codec") or "h264"
    cfg_format = get_room_setting(real_room_id, "stream_format") or get_global_setting("stream_format") or "flv"
    fallback_policy = (get_global_setting("stream_fallback_policy") or "compatible").strip().lower()
    cfg_url_priority = get_global_setting("stream_url_priority") or ""
    priority_param = get_global_setting("stream_priority_param") or "分辨率"
    cfg_fps = get_global_setting("stream_fps") or ""
    cfg_bitrate = get_global_setting("stream_bitrate") or ""
    allow_audio_only = get_global_setting("allow_audio_only")

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
        if res.get("code") != 0:
            return None

        streams = res.get("data", {}).get("playurl_info", {}).get("playurl", {}).get("stream", [])
        available_streams = []

        for stream in streams:
            if not allow_audio_only and stream.get("protocol_name") == "audio":
                continue

            for format_obj in stream.get("format", []):
                for codec_obj in format_obj.get("codec", []):
                    base_url = codec_obj.get("base_url", "")
                    url_info = codec_obj.get("url_info", [])
                    entry = build_stream_entry(
                        base_url=base_url,
                        url_info=url_info,
                        codec=codec_obj.get("codec_name", ""),
                        format_name=format_obj.get("format_name", ""),
                        qn=codec_obj.get("current_qn", 0),
                        fps=str(codec_obj.get("frame_rate", "")),
                        bitrate=str(codec_obj.get("bitrate", "")),
                        cfg_url_priority=cfg_url_priority,
                    )
                    if entry is not None:
                        available_streams.append(entry)

        return select_best_stream(
            available_streams,
            cfg_codec=cfg_codec,
            cfg_format=cfg_format,
            fallback_policy=fallback_policy,
            priority_param=priority_param,
            cfg_fps=cfg_fps,
            cfg_bitrate=cfg_bitrate,
            cfg_url_priority=cfg_url_priority,
            room_id=str(real_room_id),
        )

    except Exception as e:
        logging.error(f"⚠️ 获取流地址失败: {e}")
    return None
