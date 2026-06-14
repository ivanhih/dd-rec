from liquid import Template, Environment
from liquid.filter import liquid_filter
import platform
import asyncio
from curl_cffi import requests
import time
import datetime
import threading
import re
import os
import sys
import json
import subprocess
import logging
import flet as ft

# ==========================================
# 路径与目录
# ==========================================
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

LOCAL_FFMPEG = os.path.join(APP_DIR, "ffmpeg.exe")
FFMPEG_CMD = LOCAL_FFMPEG if os.path.exists(LOCAL_FFMPEG) else "ffmpeg"

VIDEO_SAVE_DIR = os.path.join(APP_DIR, "录播文件")
os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)

LOG_FILE = os.path.join(APP_DIR, "app.log")
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
DATA_FILE = os.path.join(APP_DIR, "data.json")

# ==========================================
# 日志
# ==========================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout)
    ]
)


# ==========================================
# 辅助函数：容量格式化
# ==========================================
def format_size(bytes_size):
    if bytes_size < 1024:
        return f"{bytes_size:.0f} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.2f} KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"


# ==========================================
# 自定义 Liquid 环境（支持 local 时区 + %3f 毫秒）
# ==========================================
def _make_liquid_env():
    """
    返回一个自定义的 Liquid Environment。
    date 过滤器签名: {{ ctime | date: '%Y-%m-%d', 'local' }}
      - 第一参数: strftime 格式字符串，支持 %3f（3位毫秒）
      - 第二参数（可选）: 'local' 使用本地时区，否则 UTC（默认）
    """
    env = Environment()

    @liquid_filter
    def date_filter(value, fmt: str = "%Y-%m-%d", tz: str = "utc") -> str:
        # value 可能是 datetime.datetime 或字符串
        if isinstance(value, str):
            try:
                value = datetime.datetime.fromisoformat(value)
            except Exception:
                return value

        if not isinstance(value, datetime.datetime):
            return str(value)

        # 时区处理
        if tz.lower() == "local":
            # 如果是 aware datetime，转本地；如果是 naive，直接当本地时间用
            if value.tzinfo is not None:
                value = value.astimezone()
            # naive 直接使用（视为本地时间）
        else:
            # utc：如果是 aware，转 UTC；naive 直接用
            if value.tzinfo is not None:
                value = value.astimezone(datetime.timezone.utc).replace(tzinfo=None)

        # 处理 %3f → 3位毫秒
        ms = f"{value.microsecond // 1000:03d}"
        fmt_processed = fmt.replace("%3f", ms)

        return value.strftime(fmt_processed)

    env.add_filter("date", date_filter)
    return env


LIQUID_ENV = _make_liquid_env()


def render_path_template(template_str: str, **kwargs) -> str:
    """使用自定义 Liquid 环境渲染路径模板"""
    tpl = LIQUID_ENV.from_string(template_str)
    return tpl.render(**kwargs)


# ==========================================
# 全局设置默认值
# ==========================================
DEFAULT_GLOBAL_SETTINGS = {
    # 外观
    "language": "简体中文",
    "theme": "深色",

    # 文件分割
    "split_by_size": "",
    "split_by_duration": "01:00:00",
    "split_on_codec_change": False,
    "split_on_stream_discontinuity": False,
    "split_on_title_change": False,
    "split_on_category_change": False,

    # 网络
    "proxy": "",
    # 代理模式: 禁用 / 系统 / 自定义
    "proxy_mode": "禁用",
    # 代理绕过（如: localhost,127.0.0.1）逗号分隔
    "proxy_bypass": "",

    # 直播流录制
    "stream_record_enabled": True,
    "allow_audio_only": False,
    "auto_switch_stream": False,
    "stream_priority_param": "分辨率",
    "stream_resolution": "原画",
    "stream_fps": "30 fps",
    "stream_bitrate": "30.0 Mb/s",
    "stream_codec": "av1",
    "stream_format": "fmp4",
    "stream_url_priority": "",

    # 聊天消息录制
    "chat_record_enabled": True,
    "chat_credential": "",
    "chat_format": "jsonl 数据",

    # 录制计划
    "schedule_timezone": "UTC",
    "schedule_start": "",
    "schedule_stop": "",

    # 自动化
    "webhooks": "",

    # 解析
    "advanced_parsing": False,

    # 文件位置
    "save_dir": VIDEO_SAVE_DIR,
    "path_template": """{%- if out_dir != '' -%}
{{ out_dir }}
{%- else -%}
{{ download_dir }}
{%- endif -%}
{%- if user_name != '' and user_name != channel -%}
/{{ channel }}-{{ user_name }}
{%- else -%}
/{{ channel }}
{%- endif -%}
/{{ ctime | date: '%Y-%m-%d', 'local' }}
{%- if platform != 'unknown' -%}
/{{ platform }}-{{ channel }}
{%- else -%}
/{{ channel }}
{%- endif -%}
{%- if user_name != '' and user_name != channel -%}
-{{ user_name }}
{%- endif -%}
{%- if title != '' -%}
-{{ title | truncate: 40 }}
{%- endif -%}
-{{ ctime | date: '%Y-%m-%d-%H%M%S-%3f', 'local' }}.{{ format }}""",

    # 转换格式
    "convert_enabled": True,
    "convert_delete_source": True,
    "convert_format": "mp4",

    # 直播监控
    "monitor_delay": "自动",
    "monitor_interval": "自动",
    "monitor_concurrency": "自动",
    "monitor_debounce": "1 分钟",
    "monitor_proxy": "",

    # 封面下载
    "download_cover": True,

    # 录制条件
    "condition_title": "",
    "condition_category": "",
    "condition_time_range": "",

    # 通知
    "notify_enabled": False,
    "notify_url": "",
    "notify_title_template": "",
    "notify_body_template": "",
    "notify_on_live_end": False,
    "notify_on_error": False,

    # 系统
    "auto_start": True,
    "prevent_sleep": True,
}

# ==========================================
# 配置（全局设置 + 房间设置）
# ==========================================
CONFIG = {
    "global_settings": dict(DEFAULT_GLOBAL_SETTINGS),
    "global": {
        "default_save_dir": VIDEO_SAVE_DIR
    },
    "rooms": {}
}

if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                CONFIG["global"] = loaded.get("global", CONFIG["global"])
                CONFIG["rooms"] = loaded.get("rooms", {})
                saved_gs = loaded.get("global_settings", {})
                for k, v in DEFAULT_GLOBAL_SETTINGS.items():
                    CONFIG["global_settings"][k] = saved_gs.get(k, v)
    except Exception as e:
        logging.error(f"读取配置失败: {e}")


def save_config():
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(CONFIG, f, ensure_ascii=False, indent=4)


def get_global_setting(key):
    return CONFIG["global_settings"].get(key, DEFAULT_GLOBAL_SETTINGS.get(key))


def set_global_setting(key, value):
    CONFIG["global_settings"][key] = value
    save_config()


def get_room_config(room_id):
    room_id = str(room_id)
    if room_id not in CONFIG["rooms"]:
        CONFIG["rooms"][room_id] = {
            "sessdata": "",
            "format": "",
            "quality": 10000,
            "custom_dir": ""
        }
        save_config()
    return CONFIG["rooms"][room_id]


def get_effective_format(room_id):
    room_cfg = get_room_config(room_id)
    fmt = room_cfg.get("format", "").strip()
    if fmt:
        return fmt
    return get_global_setting("convert_format") or "mp4"


def get_effective_save_dir(room_id, uname):
    room_cfg = get_room_config(room_id)
    base_dir = room_cfg.get("custom_dir", "").strip()
    if not base_dir:
        base_dir = get_global_setting("save_dir") or VIDEO_SAVE_DIR
    save_dir = os.path.join(base_dir, uname)
    os.makedirs(save_dir, exist_ok=True)
    return save_dir


def get_headers(room_id=None, monitor=False):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://live.bilibili.com/"
    }
    if room_id:
        room_cfg = CONFIG["rooms"].get(str(room_id), {})
        sessdata = room_cfg.get("sessdata", "").strip()
        if sessdata:
            headers["Cookie"] = f"SESSDATA={sessdata};"
    # 如果为监控请求且专用监控代理已设置，则优先使用之
    if monitor:
        mon = get_global_setting("monitor_proxy") or ""
        if mon:
            headers["_proxy"] = mon
            return headers
    # 根据代理模式决定是否使用代理
    proxy_mode = get_global_setting("proxy_mode") or "禁用"
    if proxy_mode == "自定义":
        proxy = get_global_setting("proxy") or ""
        if proxy:
            headers["_proxy"] = proxy
    elif proxy_mode == "系统":
        # 优先使用环境变量（ALL/HTTPS/HTTP）作为系统代理
        proxy = os.environ.get("ALL_PROXY") or os.environ.get("all_proxy") or \
                os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy") or \
                os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy") or ""
        if proxy:
            headers["_proxy"] = proxy
    return headers


# ==========================================
# 数据
# ==========================================
def load_app_data():
    if not os.path.exists(DATA_FILE):
        return {"channels": []}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if "channels" not in data:
                data["channels"] = []
            return data
    except Exception as e:
        logging.error(f"读取数据失败: {e}")
        return {"channels": []}


def save_app_data(channels):
    data = {"channels": channels}
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=4)


# ==========================================
# B站接口
# ==========================================
def extract_room_id(url_or_id):
    s = str(url_or_id).strip()
    match = re.search(r"live\.bilibili\.com/(\d+)", s)
    if match:
        return match.group(1)
    match = re.search(r"\d+", s)
    if match:
        return match.group(0)
    return None


def get_bili_info(url_or_id, room_id_for_cookie=None, silent=False):
    room_id = extract_room_id(url_or_id)
    if not room_id: return None

    if not silent: logging.info(f"👉 提取到房间号: {room_id}，正在获取数据...")

    try:
        # 如果是后台静默调用（如监控），允许使用监控代理
        headers = get_headers(room_id_for_cookie or room_id, monitor=silent)

        url_init = f"https://api.live.bilibili.com/room/v1/Room/room_init?id={room_id}"
        res_init = requests.get(url_init, headers=headers, impersonate="chrome110", timeout=10).json()
        if res_init.get("code") != 0: return None

        real_room_id = res_init.get("data", {}).get("room_id")
        live_status = res_init.get("data", {}).get("live_status", 0)

        url_info = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={real_room_id}"
        res_info = requests.get(url_info, headers=headers, impersonate="chrome110", timeout=10).json()

        title = "未知标题"
        parent_area_name = "未知分区"
        area_name = "未知内容"

        if res_info.get("code") == 0:
            data = res_info.get("data", {})
            title = data.get("title", "未知标题")
            parent_area_name = data.get("parent_area_name", "未知分区")
            area_name = data.get("area_name", "未知内容")

        url_anchor = f"https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room?roomid={real_room_id}"
        res_anchor = requests.get(url_anchor, headers=headers, impersonate="chrome110", timeout=10).json()

        uname = f"主播_{real_room_id}"
        face = ""

        if res_anchor.get("code") == 0:
            uname = res_anchor.get("data", {}).get("info", {}).get("uname", uname)
            face = res_anchor.get("data", {}).get("info", {}).get("face", "")

        if not silent: logging.info(f"✅ 获取信息: {uname} ({real_room_id}) - [{parent_area_name}/{area_name}]")

        return {
            "room_id": str(real_room_id),
            "uname": uname,
            "title": title,
            "live_status": live_status,
            "parent_area_name": parent_area_name,
            "area_name": area_name,
            "face": face
        }
    except Exception as e:
        if not silent: logging.error(f"❌ 获取信息失败: {e}")
        return None


def get_stream_info(real_room_id):
    cfg_codec = get_global_setting("stream_codec") or "av1"
    cfg_format = get_global_setting("stream_format") or "fmp4"
    cfg_url_priority = get_global_setting("stream_url_priority") or ""
    priority_param = get_global_setting("stream_priority_param") or "分辨率"
    cfg_fps = get_global_setting("stream_fps") or ""
    cfg_bitrate = get_global_setting("stream_bitrate") or ""
    allow_audio_only = get_global_setting("allow_audio_only")

    res_map = {"原画": 10000, "超清": 400, "高清": 250, "流畅": 150}
    cfg_res_str = get_global_setting("stream_resolution") or "原画"
    qn = res_map.get(cfg_res_str, 10000)

    url = f"https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo?room_id={real_room_id}&protocol=0,1&format=0,1,2&codec=0,1,2&qn={qn}&platform=web"

    try:
        res = requests.get(url, headers=get_headers(real_room_id, monitor=True), impersonate="chrome110", timeout=10).json()
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
            if priority_param == "编码" and s["codec"] == cfg_codec:
                score += 10000
            elif priority_param == "格式" and s["format"] == cfg_format:
                score += 10000
            elif priority_param == "分辨率":
                score += s["qn"] * 10
            elif priority_param == "帧率" and cfg_fps and cfg_fps.split()[0] in s["fps"]:
                score += 10000
            elif priority_param == "码率" and cfg_bitrate and cfg_bitrate.split()[0] in s["bitrate"]:
                score += 10000
            elif priority_param == "网址" and cfg_url_priority and cfg_url_priority in s["url"]:
                score += 10000
            if s["codec"] == cfg_codec: score += 100
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


# ==========================================
# 路径模板渲染（统一入口）
# ==========================================
def _build_save_path(room_id, uname, title, now_dt):
    """根据路径模板渲染最终保存路径，含完整扩展名"""
    fmt = get_effective_format(room_id)
    global_dir = get_global_setting("save_dir") or VIDEO_SAVE_DIR
    room_cfg = get_room_config(room_id)
    custom_dir = room_cfg.get("custom_dir", "").strip()

    template_str = get_global_setting("path_template") or (
        "{{ download_dir }}/{{ channel }}_{{ ctime | date: '%Y%m%d_%H%M%S' }}.{{ format }}"
    )

    # 清理文件名中的非法字符（保留盘符冒号不处理）
    invalid_chars = r'[\\/:*?"<>|]' if platform.system() == "Windows" else r'[/]'
    safe_channel = re.sub(invalid_chars, '_', str(room_id))
    safe_uname = re.sub(invalid_chars, '_', str(uname))
    safe_title = re.sub(invalid_chars, '_', str(title))

    try:
        rendered = render_path_template(
            template_str,
            out_dir=custom_dir,
            download_dir=global_dir,
            platform="bilibili",
            channel=safe_channel,
            user_name=safe_uname,
            title=safe_title,
            ctime=now_dt,
            format=fmt
        )
        save_path = os.path.normpath(rendered.strip())
    except Exception as e:
        logging.error(f"模板解析失败，回退到默认路径: {e}")
        now_str = now_dt.strftime("%Y%m%d_%H%M%S")
        fallback_dir = custom_dir if custom_dir else global_dir
        save_path = os.path.join(fallback_dir, f"room_{room_id}_{now_str}.{fmt}")

    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    return save_path


# ==========================================
# 录制器
# ==========================================
class BiliRecorder:
    def __init__(self, room_info, status_callback):
        self.room_info = room_info
        self.room_id = str(room_info["room_id"])
        self.uname = room_info["uname"]
        self.current_title = room_info.get("title", "未知标题")
        self.status_callback = status_callback

        self.is_running = True
        self.is_monitoring = True
        self.current_ffmpeg = None
        self.stream_url = None

        self.current_save_path = None
        self.record_start_time = 0
        self.last_check_time = 0

        self.last_total_size = 0
        self.accumulated_size = 0
        self.last_api_check_time = 0

        self._split_timer = None

    @property
    def is_recording(self):
        return self.current_ffmpeg is not None and self.current_ffmpeg.poll() is None

    def _graceful_stop_ffmpeg(self, process):
        if process and process.poll() is None:
            try:
                process.stdin.write(b"q\n")
                process.stdin.flush()
                process.wait(timeout=5)
            except Exception:
                try:
                    process.terminate()
                    process.wait(timeout=5)
                except Exception:
                    pass

    def stop_recording(self):
        was_recording = self.is_recording

        if self._split_timer:
            self._split_timer.cancel()
            self._split_timer = None

        if self.current_ffmpeg:
            self._graceful_stop_ffmpeg(self.current_ffmpeg)

        self.current_ffmpeg = None
        self.current_save_path = None
        self.stream_url = None

        self.record_start_time = 0
        self.last_check_time = 0
        self.last_total_size = 0
        self.accumulated_size = 0
        self.last_api_check_time = 0

        if was_recording:
            logging.info(f"⏹️ 已停止录制: {self.room_id}")

    def kill_thread(self):
        self.is_running = False
        self.stop_recording()

    def _schedule_duration_split(self):
        duration_str = get_global_setting("split_by_duration") or ""
        if not duration_str:
            return
        try:
            parts = duration_str.split(":")
            seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
            if seconds <= 0:
                return
            if self._split_timer:
                self._split_timer.cancel()
            self._split_timer = threading.Timer(seconds, self._auto_split)
            self._split_timer.daemon = True
            self._split_timer.start()
            logging.info(f"⏱️ {self.room_id} 将在 {duration_str} 后自动切割")
        except Exception as e:
            logging.error(f"设置分割定时器失败: {e}")

    def _auto_split(self):
        if self.is_recording and self.stream_url:
            logging.info(f"⏱️ {self.room_id} 触发时长自动切割")
            self.trigger_cut()

    def _check_size_split(self):
        size_str = get_global_setting("split_by_size") or ""
        if not size_str:
            return False
        try:
            size_str = size_str.strip().upper()
            if size_str.endswith("GB"):
                limit = float(size_str[:-2]) * 1024 * 1024 * 1024
            elif size_str.endswith("MB"):
                limit = float(size_str[:-2]) * 1024 * 1024
            else:
                return False

            if self.current_save_path and os.path.exists(self.current_save_path):
                current_size = self.accumulated_size + os.path.getsize(self.current_save_path)
                if current_size >= limit:
                    logging.info(f"📦 {self.room_id} 文件大小达到 {size_str}，触发切割")
                    return True
        except Exception:
            pass
        return False

    def _start_ffmpeg(self, save_path):
        """启动 ffmpeg 进程，stderr 静默"""
        cmd = [
            FFMPEG_CMD, "-y",
            "-reconnect", "1",
            "-reconnect_streamed", "1",
            "-reconnect_delay_max", "5",
            "-rw_timeout", "15000000",
            "-user_agent", "Mozilla/5.0",
            "-headers", "Referer: https://live.bilibili.com/\r\n",
            "-i", self.stream_url,
            "-c", "copy",
            save_path
        ]
        return subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL  # ← 静默 ffmpeg 后台输出
        )

    def trigger_cut(self):
        if not self.is_recording or not self.stream_url: return

        logging.info(f"✂️ {self.room_id} 正在切割...")
        now_dt = datetime.datetime.now()
        save_path = _build_save_path(self.room_id, self.uname, self.current_title, now_dt)

        new_ffmpeg = self._start_ffmpeg(save_path)
        time.sleep(2)

        if self.current_save_path and os.path.exists(self.current_save_path):
            self.accumulated_size += os.path.getsize(self.current_save_path)

        old_ffmpeg, self.current_ffmpeg = self.current_ffmpeg, new_ffmpeg
        self.current_save_path = save_path

        if old_ffmpeg: self._graceful_stop_ffmpeg(old_ffmpeg)
        logging.info(f"✅ {self.room_id} 切割完成: {os.path.basename(save_path)}")

        self._schedule_duration_split()

    def run(self):


# ==========================================
# 卡片 UI
# ==========================================
class FletChannelCard:
    def __init__(self, page: ft.Page, room_info: dict, app, add_index: int):
        self.page = page
        self.app = app
        self.room_info = room_info
        self.room_id = str(room_info["room_id"])
        self.add_index = add_index

        self.pending_m = "🟢 监控中"
        self.pending_l = "📡 未开播"
        self.pending_r = "⏳ 闲置中"
        self.pending_duration = "00:00:00"
        self.pending_speed = "0 B/s"
        self.pending_size = "0 B"
        self.ui_dirty = True

        uname = room_info.get("uname") or room_info.get("name") or f"房间_{self.room_id}"
        title = room_info.get("title", "")
        self.face_url = room_info.get("face", "")

        self.room_info["uname"] = uname
        self.room_info["title"] = title

        self.lbl_uname = ft.Text(uname, size=17, weight=ft.FontWeight.BOLD, color="#31B7FF")
        self.lbl_room_id = ft.Text(self.room_id, size=13, color="#55AFFF")
        self.lbl_title = ft.Text(title or "暂无标题", size=12, color="#F2C36B", max_lines=1,
                                 overflow=ft.TextOverflow.ELLIPSIS)

        self.lbl_m = ft.Text("🟢 监控中", size=12, color="#6FCF70")
        self.lbl_l = ft.Text("📡 未开播", size=12, color="#EAEAEA")
        self.lbl_r = ft.Text("⏳ 闲置中", size=12, color="#C8C8C8")
        self.pending_title = title or "暂无标题"
        self.pending_parent_area = room_info.get("parent_area_name", "未知分区")
        self.pending_area_name = room_info.get("area_name", "未知内容")

        self.lbl_duration = ft.Text("00:00:00", size=12, color="#EAEAEA")
        self.lbl_speed = ft.Text("0.00 MB/s", size=12, color="#EAEAEA")
        self.lbl_size = ft.Text("0.00 GiB", size=12, color="#EAEAEA")

        self.tag_parent_txt = ft.Text(self.pending_parent_area, size=10, color="#7B61FF")
        self.tag_area_txt = ft.Text(self.pending_area_name, size=10, color="#BB6BD9")

        tag_parent = ft.Container(bgcolor="#111111", border_radius=12, padding=ft.Padding(8, 2, 8, 2),
                                  content=self.tag_parent_txt)
        tag_area = ft.Container(bgcolor="#111111", border_radius=12, padding=ft.Padding(8, 2, 8, 2),
                                content=self.tag_area_txt)

        self.switch = ft.Switch(value=room_info.get("enabled", True), on_change=self.on_toggle)

        if self.face_url:
            avatar_box = ft.Image(src=self.face_url, width=48, height=48, border_radius=24, fit="cover")
        else:
            avatar_box = ft.Container(
                width=48, height=48, border_radius=24, bgcolor="#EAEAEA",
                content=ft.Column(alignment=ft.MainAxisAlignment.CENTER,
                                  horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                  controls=[ft.Text("头像", size=10, color="black")])
            )

        self.stats_row = ft.Row(
            spacing=35, opacity=0, animate_opacity=300,
            controls=[
                self._status_block("🕒", self.lbl_duration),
                self._status_block("🚀", self.lbl_speed),
                self._status_block("💾", self.lbl_size),
            ]
        )

        self.view = ft.Container(
            width=540, height=205, bgcolor="#1E1E1F", border_radius=8, padding=16,
            content=ft.Column(
                spacing=10,
                controls=[
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                        vertical_alignment=ft.CrossAxisAlignment.START,
                        controls=[
                            ft.Row(spacing=12, controls=[
                                avatar_box,
                                ft.Column(spacing=2, controls=[self.lbl_uname, self.lbl_title])
                            ]),
                            self.lbl_room_id
                        ]
                    ),
                    ft.Row(spacing=8, controls=[self._tag("bilibili", "#2F80ED"), tag_parent, tag_area]),
                    ft.Row(spacing=35, controls=[self.lbl_m, self.lbl_l, self.lbl_r]),
                    self.stats_row,
                    ft.Row(
                        alignment=ft.MainAxisAlignment.SPACE_AROUND,
                        controls=[
                            self.switch,
                            ft.IconButton(icon=ft.Icons.FOLDER_OPEN, tooltip="打开文件夹",
                                          on_click=self.on_open_folder),
                            ft.IconButton(icon=ft.Icons.CONTENT_CUT, tooltip="切割", on_click=self.on_cut),
                            ft.IconButton(icon=ft.Icons.SETTINGS, tooltip="房间设置", on_click=self.on_settings),
                            ft.IconButton(icon=ft.Icons.DELETE, icon_color="red", tooltip="删除",
                                          on_click=self.on_delete),
                        ]
                    )
                ]
            )
        )

        self.recorder = BiliRecorder(self.room_info, self.update_status)
        self.recorder.is_monitoring = room_info.get("enabled", True)
        self.thread = threading.Thread(target=self.recorder.run, daemon=True)
        self.thread.start()

    def _tag(self, text, color):
        return ft.Container(bgcolor="#111111", border_radius=12, padding=ft.Padding(8, 2, 8, 2),
                            content=ft.Text(text, size=10, color=color))

    def _status_block(self, icon_text, label_ctrl):
        return ft.Row(spacing=4, controls=[ft.Text(icon_text, size=12), label_ctrl])

    def update_status(self, m, l, r, title=None, duration="00:00:00", speed="0 B/s",
                      file_size="0 B", parent_area=None, area_name=None):
        try:
            self.pending_m = m
            self.pending_l = l
            self.pending_r = r
            self.pending_title = title or self.pending_title
            self.pending_duration = duration
            self.pending_speed = speed
            self.pending_size = file_size
            if parent_area: self.pending_parent_area = parent_area
            if area_name: self.pending_area_name = area_name
            self.ui_dirty = True
        except Exception as e:
            logging.error(f"缓存状态失败: {e}")

    def apply_pending_ui(self):
        if not self.ui_dirty:
            return

        self.lbl_m.value = self.pending_m
        self.lbl_l.value = self.pending_l
        self.lbl_r.value = self.pending_r

        self.lbl_m.color = "#ff4d4f" if "出错" in self.pending_m else (
            "#6FCF70" if "监控" in self.pending_m else "#EAEAEA")
        self.lbl_l.color = "#ff4d4f" if "直播中" in self.pending_l else "#EAEAEA"
        self.lbl_r.color = "#ff4d4f" if "出错" in self.pending_r else (
            "#5faede" if "录制" in self.pending_r else "#C8C8C8")

        self.lbl_title.value = self.pending_title
        self.room_info["title"] = self.pending_title
        self.lbl_uname.value = self.recorder.uname
        self.tag_parent_txt.value = self.pending_parent_area
        self.tag_area_txt.value = self.pending_area_name
        self.room_info["parent_area_name"] = self.pending_parent_area
        self.room_info["area_name"] = self.pending_area_name

        if "录制" in self.pending_r:
            self.stats_row.opacity = 1
            self.lbl_duration.value = self.pending_duration
            self.lbl_speed.value = self.pending_speed
            self.lbl_size.value = self.pending_size
        else:
            self.stats_row.opacity = 0

        self.ui_dirty = False

    def on_toggle(self, e):
        enabled = self.switch.value
        self.room_info["enabled"] = enabled
        self.recorder.is_monitoring = enabled
        self.app.save_all_data()
        status_str = "已开启" if enabled else "已关闭"
        self.app.notify(f"{self.room_info['uname']} 监控{status_str}")

    def on_cut(self, e):
        threading.Thread(target=self.recorder.trigger_cut, daemon=True).start()
        self.app.notify(f"已触发切割：{self.room_info['uname']}")

    def on_open_folder(self, e):
        if self.recorder.current_save_path and os.path.exists(os.path.dirname(self.recorder.current_save_path)):
            save_dir = os.path.dirname(self.recorder.current_save_path)
        else:
            global_dir = get_global_setting("save_dir") or VIDEO_SAVE_DIR
            room_cfg = get_room_config(self.room_id)
            save_dir = room_cfg.get("custom_dir", "").strip() or global_dir
            os.makedirs(save_dir, exist_ok=True)

        try:
            if platform.system() == "Windows":
                os.startfile(save_dir)
            elif platform.system() == "Darwin":
                subprocess.Popen(['open', save_dir])
            else:
                subprocess.Popen(['xdg-open', save_dir])
            self.app.notify(f"已打开文件夹")
        except Exception as ex:
            self.app.notify(f"打开目录失败: {ex}", is_error=True)

    def on_delete(self, e):
        self.recorder.kill_thread()
        self.app.remove_card(self)

    def close_dialog(self, dlg):
        dlg.open = False
        self.page.update()

    def on_settings(self, e):
        cfg = get_room_config(self.room_id)
        global_fmt = get_global_setting("convert_format") or "mp4"
        global_dir = get_global_setting("save_dir") or VIDEO_SAVE_DIR

        sessdata_input = ft.TextField(label="SESSDATA（留空继承全局）", value=cfg.get("sessdata", ""))
        format_dropdown = ft.Dropdown(
            label=f"输出格式（全局: {global_fmt}）",
            value=cfg.get("format", "") or "",
            options=[
                ft.dropdown.Option("", "继承全局"),
                ft.dropdown.Option("mp4"),
                ft.dropdown.Option("ts"),
                ft.dropdown.Option("flv")
            ]
        )
        quality_input = ft.TextField(label="清晰度", value=str(cfg.get("quality", 10000)))
        custom_dir_input = ft.TextField(
            label=f"自定义保存目录（留空继承全局）",
            hint_text=global_dir,
            value=cfg.get("custom_dir", "")
        )

        def save_settings(ev):
            cfg["sessdata"] = sessdata_input.value.strip()
            cfg["format"] = format_dropdown.value or ""
            try:
                cfg["quality"] = int(quality_input.value)
            except Exception:
                cfg["quality"] = 10000
            cfg["custom_dir"] = custom_dir_input.value.strip()
            save_config()
            dlg.open = False
            self.app.notify(f"{self.room_info['uname']} 房间设置已保存")
            self.page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"房间设置 - {self.room_info['uname']}"),
            content=ft.Container(
                width=500,
                content=ft.Column(tight=True,
                                  controls=[sessdata_input, format_dropdown, quality_input, custom_dir_input])
            ),
            actions=[
                ft.TextButton("取消", on_click=lambda ev: self.close_dialog(dlg)),
                ft.Button("保存", on_click=save_settings)
            ],
            actions_alignment=ft.MainAxisAlignment.END
        )
        self.page.dialog = dlg
        dlg.open = True
        self.page.update()


# ==========================================
# 消息提示
# ==========================================
class NotificationManager:
    def __init__(self, page: ft.Page):
        self.page = page
        self.container = ft.Column(
            spacing=10,
            horizontal_alignment=ft.CrossAxisAlignment.END,
            alignment=ft.MainAxisAlignment.END
        )

    def show(self, message: str, title="提示", bgcolor="#1f2937", title_color="white", icon_text="ℹ", duration=3):
        item = ft.Container(
            width=340, bgcolor=bgcolor, border_radius=8, padding=12,
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                vertical_alignment=ft.CrossAxisAlignment.START,
                controls=[
                    ft.Row(expand=True, spacing=10, controls=[
                        ft.Text(icon_text, color=title_color, size=18),
                        ft.Column(spacing=6, controls=[
                            ft.Text(title, color=title_color, weight=ft.FontWeight.BOLD, size=18),
                            ft.Text(message, color="white", size=14),
                        ])
                    ]),
                    ft.IconButton(icon=ft.Icons.CLOSE, icon_color="white", icon_size=16,
                                  tooltip="关闭", on_click=lambda e: self.remove(item))
                ]
            )
        )
        self.container.controls.insert(0, item)
        self.page.update()
        self.page.run_task(self.auto_remove, item, duration)

    def success(self, message: str, title="成功", duration=3):
        self.show(message=message, title=title, bgcolor="#0f2e1d", title_color="#4ade80", icon_text="✔",
                  duration=duration)

    def error(self, message: str, title="错误", duration=5):
        self.show(message=message, title=title, bgcolor="#2b0b0b", title_color="#ff4d4f", icon_text="❗",
                  duration=duration)

    def info(self, message: str, title="提示", duration=3):
        self.show(message=message, title=title, bgcolor="#1f2937", title_color="#60a5fa", icon_text="ℹ",
                  duration=duration)

    async def auto_remove(self, item, duration):
        await asyncio.sleep(duration)
        self.remove(item)

    def remove(self, item):
        try:
            if item in self.container.controls:
                self.container.controls.remove(item)
                self.page.update()
        except Exception as e:
            logging.error(f"移除通知失败: {e}")


# ==========================================
# 全局设置页面
# ==========================================
class GlobalSettingsPage:
    def __init__(self, page: ft.Page, notifier: NotificationManager):
        self.page = page
        self.notifier = notifier
        self._controls = {}

    def _gs(self, key):
        return get_global_setting(key)

    def _save(self, key, value):
        set_global_setting(key, value)

    def _on_switch(self, key):
        def handler(e):
            self._save(key, e.control.value)
            self.notifier.success(f"已保存", title="设置", duration=1)
        return handler

    def _on_dropdown(self, key):
        def handler(e):
            self._save(key, e.control.value)
            self.notifier.success(f"已保存", title="设置", duration=1)
        return handler

    def _on_textfield_submit(self, key):
        def handler(e):
            self._save(key, e.control.value)
            self.notifier.success(f"已保存", title="设置", duration=1)
        return handler

    def _switch(self, key) -> ft.Switch:
        ctrl = ft.Switch(value=bool(self._gs(key)), on_change=self._on_switch(key))
        self._controls[key] = ctrl
        return ctrl

    def _dropdown(self, key, options: list, width=140) -> ft.Dropdown:
        ctrl = ft.Dropdown(
            value=str(self._gs(key) or options[0]),
            width=width, dense=True, text_size=13,
            options=[ft.dropdown.Option(o) for o in options]
        )
        ctrl.on_change = self._on_dropdown(key)
        self._controls[key] = ctrl
        return ctrl

    def _textfield(self, key, hint="", width=200, password=False) -> ft.TextField:
        ctrl = ft.TextField(
            value=str(self._gs(key) or ""),
            hint_text=hint, width=width, dense=True,
            password=password,
            on_submit=self._on_textfield_submit(key),
            on_blur=self._on_textfield_submit(key),
        )
        self._controls[key] = ctrl
        return ctrl

    def _directory_field(self, key, hint="", width=200):
        tf = self._textfield(key, hint=hint, width=width)

        async def open_picker(e):
            folder = await ft.FilePicker().get_directory_path(dialog_title="选择保存目录")
            if folder:
                tf.value = folder
                tf.update()
                self._save(key, folder)
                self.notifier.success("已保存目录", title="设置", duration=1)

        btn = ft.IconButton(
            icon=ft.Icons.FOLDER_OPEN,
            icon_color="#2D9CDB",
            tooltip="选择文件夹",
            on_click=open_picker
        )
        return ft.Row(spacing=4, controls=[tf, btn])

    def _template_editor_field(self, title_text, key, value):
        editor_tb = ft.TextField(
            value=get_global_setting(key) or value,
            multiline=True,
            min_lines=18,
            max_lines=22,
            expand=True,
            text_size=12,
            bgcolor="#1e1e1e",
            color="white",
            border_color="transparent",
            focused_border_color="transparent",
        )
        
        # 对话框容器
        dialog_container = None
        
        def close(ev=None):
            nonlocal dialog_container
            if dialog_container and dialog_container in self.page.overlay:
                self.page.overlay.remove(dialog_container)
            self.page.update()
        
        def save_dialog(ev):
            self._save(key, editor_tb.value)
            self.notifier.success("模板已保存", title="设置", duration=2)
            close()
        
        def open_editor(e):
            nonlocal dialog_container
            logging.info(f"打开模板编辑器: key={key}")
            
            # 创建对话框内容 - 更精致的设计
            dialog_content = ft.Container(
                bgcolor="#1e1e1e",
                border_radius=8,
                shadow=ft.BoxShadow(
                    blur_radius=24,
                    spread_radius=8,
                    offset=ft.Offset(0, 10),
                    color="rgba(0,0,0,0.6)"
                ),
                content=ft.Column(
                    spacing=0,
                    controls=[
                        # 标题栏
                        ft.Container(
                            padding=ft.Padding(20, 20, 20, 16),
                            border=ft.Border(bottom=ft.BorderSide(1, "#2D2D2D")),
                            content=ft.Column(
                                spacing=6,
                                controls=[
                                    ft.Text(
                                        title_text,
                                        size=16,
                                        weight=ft.FontWeight.BOLD,
                                        color="white",
                                    ),
                                    ft.Text(
                                        "编辑 Liquid 模板内容",
                                        size=11,
                                        color="#666666",
                                    ),
                                ],
                            ),
                        ),
                        # 编辑框 - 填满空间，贴合对话框
                        ft.Container(
                            expand=True,
                            padding=ft.Padding(20, 16, 20, 16),
                            bgcolor="#252526",
                            content=editor_tb,
                        ),
                        # 按钮栏
                        ft.Container(
                            height=60,
                            padding=ft.Padding(20, 12, 20, 12),
                            border=ft.Border(top=ft.BorderSide(1, "#2D2D2D")),
                            bgcolor="#1e1e1e",
                            content=ft.Row(
                                alignment=ft.MainAxisAlignment.END,
                                vertical_alignment=ft.CrossAxisAlignment.CENTER,
                                spacing=10,
                                controls=[
                                    ft.TextButton(
                                        "取消",
                                        on_click=close,
                                        # keep only padding to avoid incompatible style kwargs on older flet
                                        style=ft.ButtonStyle(padding=ft.Padding(16, 8, 16, 8)),
                                    ),
                                    ft.FilledButton(
                                        "保存",
                                        icon=ft.Icons.SAVE,
                                        on_click=save_dialog,
                                        # avoid bgcolor/color kwargs which may not be supported in all flet versions
                                        style=ft.ButtonStyle(padding=ft.Padding(20, 8, 20, 8)),
                                    ),
                                ],
                            ),
                        ),
                    ],
                ),
            )
            
            # 创建背景遮罩 - 居中显示
            # Use a Column for vertical centering and a Row for horizontal centering to ensure
            # the dialog is placed in the exact center of the app window.
            dialog_bg = ft.Container(
                expand=True,
                bgcolor="rgba(0,0,0,0.65)",
                content=ft.Column(
                    expand=True,
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Row(
                            expand=True,
                            alignment=ft.MainAxisAlignment.CENTER,
                            controls=[
                                ft.Container(
                                    width=750,
                                    height=600,
                                    content=dialog_content,
                                )
                            ],
                        )
                    ],
                ),
            )
            
            dialog_container = dialog_bg
            
            try:
                self.page.overlay.append(dialog_container)
                self.page.update()
            except Exception as ex:
                logging.error(f"无法打开对话框: {ex}")
        
        click_row = ft.Row(
            [
                ft.Icon(ft.Icons.CODE, color="#F2C94C"),
                ft.Text("点击展开编辑 Liquid 模板", color="#2D9CDB", size=14),
                ft.Icon(ft.Icons.CHEVRON_RIGHT),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        
        return ft.Container(
            content=ft.GestureDetector(content=click_row, on_tap=open_editor),
            padding=16,
            border_radius=8,
            bgcolor="#252526",
            border=ft.Border.all(1, "#3C3C3C"),
        )

    def _open_proxy_dialog(self):
        """打开一个对话框用于配置代理（模式、地址、绕过列表）。

        使用 try/except 包裹对话框构建以避免未捕获异常导致 Flet 会话崩溃。
        """
        try:
            logging.info("尝试打开代理设置对话框")
            # 在 UI 上也给用户一个轻量提示，便于确认事件被触发
            try:
                self.notifier.info("正在打开代理设置...")
            except Exception:
                pass
            current_mode = get_global_setting("proxy_mode") or "禁用"
            current_proxy = get_global_setting("proxy") or ""
            current_bypass = get_global_setting("proxy_bypass") or ""

            mode_dropdown = ft.Dropdown(
                label="模式",
                value=current_mode,
                options=[
                    ft.dropdown.Option("禁用"),
                    ft.dropdown.Option("系统"),
                    ft.dropdown.Option("自定义"),
                ],
                width=360,
                height=50,
                dense=True,
                color="white",
                label_style=ft.TextStyle(color="#999999"),
                bgcolor="#2a2a2a",
                border_color="#444444",
                focused_border_color="#2D9CDB",
            )

            proxy_addr = ft.TextField(
                label="代理地址", 
                hint_text="如: http://127.0.0.1:7890", 
                value=current_proxy, 
                width=360,
                color="white",
                label_style=ft.TextStyle(color="#999999"),
                hint_style=ft.TextStyle(color="#666666"),
                bgcolor="#2a2a2a",
                border_color="#444444",
                focused_border_color="#2D9CDB",
            )
            bypass_tf = ft.TextField(
                label="绕过（逗号分隔）", 
                hint_text="如: localhost,127.0.0.1", 
                value=current_bypass, 
                width=360,
                color="white",
                label_style=ft.TextStyle(color="#999999"),
                hint_style=ft.TextStyle(color="#666666"),
                bgcolor="#2a2a2a",
                border_color="#444444",
                focused_border_color="#2D9CDB",
            )

            # 控制可见性
            def refresh_visibility(ev=None):
                nonlocal mode_dropdown, proxy_addr, bypass_tf

                is_custom = (mode_dropdown.value == "自定义")
                proxy_addr.visible = is_custom
                bypass_tf.visible = is_custom

                # 【修复点】：精准触发这两个控件的独立重绘
                try:
                    proxy_addr.update()
                    bypass_tf.update()
                except Exception:
                    pass

                self.page.update()

            mode_dropdown.on_change = lambda e: refresh_visibility(e)

            # 对话框容器引用（用于 overlay 回退）
            dialog_container = None

            def close_dialog(ev=None):
                nonlocal dialog_container
                try:
                    if dialog_container and dialog_container in self.page.overlay:
                        self.page.overlay.remove(dialog_container)
                except Exception:
                    pass
                self.page.update()

            def on_save(ev):
                set_global_setting("proxy_mode", mode_dropdown.value)
                if mode_dropdown.value == "自定义":
                    set_global_setting("proxy", proxy_addr.value.strip())
                else:
                    # 禁用或使用系统时，不修改 proxy 字段（禁用时清空）
                    if mode_dropdown.value == "禁用":
                        set_global_setting("proxy", "")
                set_global_setting("proxy_bypass", bypass_tf.value.strip())
                self.notifier.success("代理设置已保存", title="设置", duration=1)
                # 更新设置页上显示的摘要字段（如果存在）
                try:
                    if "proxy_summary" in self._controls:
                        self._controls["proxy_summary"].value = str(get_global_setting("proxy") or "")
                        try:
                            self._controls["proxy_summary"].update()
                        except Exception:
                            pass
                except Exception:
                    pass
                # 关闭对话框（无论是 page.dialog 还是 overlay）
                close_dialog()

            def on_cancel(ev):
                close_dialog()

            # 初次根据当前模式设置可见性
            proxy_addr.visible = True if current_mode == "自定义" else False
            bypass_tf.visible = True if current_mode == "自定义" else False

            # 构建自定义对话框内容并使用 overlay 显示（更可靠且可自定义样式）
            logging.info(f"构建对话框内容: mode={current_mode}, proxy={current_proxy}, bypass={current_bypass}")
            dialog_content = ft.Container(
                bgcolor="#1e1e1e",
                border_radius=8,
                padding=ft.Padding(18, 12, 18, 12),
                content=ft.Column(spacing=10, controls=[
                    ft.Text("网络代理设置", weight=ft.FontWeight.BOLD, size=16, color="white"),
                    ft.Container(height=6),
                    ft.Column(spacing=12, controls=[
                        mode_dropdown, 
                        proxy_addr, 
                        bypass_tf
                    ]),
                    ft.Container(height=6),
                    ft.Row(alignment=ft.MainAxisAlignment.END, spacing=10, controls=[
                        ft.TextButton("取消", on_click=on_cancel, style=ft.ButtonStyle(padding=ft.Padding(16, 8, 16, 8))),
                        ft.FilledButton("保存", on_click=on_save, style=ft.ButtonStyle(padding=ft.Padding(20, 8, 20, 8))),
                    ])
                ])
            )
            logging.info("对话框内容构建完毕")

            # 背景遮罩，居中显示对话框
            dialog_bg = ft.Container(
                expand=True,
                bgcolor="rgba(0,0,0,0.6)",
                content=ft.Column(
                    expand=True,
                    alignment=ft.MainAxisAlignment.CENTER,
                    horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                    controls=[
                        ft.Row(
                            expand=True, 
                            alignment=ft.MainAxisAlignment.CENTER, 
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            controls=[
                                ft.Container(width=620, content=dialog_content)
                            ]
                        )
                    ]
                )
            )

            # 将对话框加入 overlay 并显示
            dialog_container = dialog_bg
            try:
                logging.info(f"对话框内容创建完毕，准备加入 overlay。page.overlay 当前长度: {len(self.page.overlay)}")
                self.page.overlay.append(dialog_container)
                logging.info(f"对话框已加入 overlay，overlay 长度现在: {len(self.page.overlay)}")
                self.page.update()
                logging.info("页面已更新，对话框应该可见了")
            except Exception as ex_overlay:
                logging.error(f"使用 overlay 打开代理设置对话框失败: {ex_overlay}", exc_info=True)
                try:
                    self.notifier.error("无法打开代理设置对话框，请查看日志", title="错误")
                except Exception:
                    pass
                return
        except Exception as ex:
            logging.error(f"打开代理设置对话框失败: {ex}")
            try:
                self.notifier.error("无法打开代理设置对话框，请查看日志", title="错误")
            except Exception:
                pass
            return

    def _chevron_field(self, key, hint="", width=140):
        tf = self._textfield(key, hint=hint, width=width)
        return ft.Row(spacing=4, controls=[tf, ft.Icon(ft.Icons.CHEVRON_RIGHT, size=16, color="#888888")])

    def _setting_item(self, title, subtitle, control):
        return ft.Container(
            padding=ft.Padding(0, 10, 0, 10),
            border=ft.Border(bottom=ft.BorderSide(1, "#2a2a2a")),
            content=ft.Row(
                alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                controls=[
                    ft.Column(
                        spacing=3, expand=True,
                        controls=[
                            ft.Text(title, size=13, color="white"),
                            ft.Text(subtitle, size=11, color="#666666"),
                        ]
                    ),
                    control
                ]
            )
        )

    def _setting_card(self, title, items, accent_color="#2D9CDB"):
        return ft.Container(
            bgcolor="#111111", border_radius=10, padding=16,
            margin=ft.Margin(0, 0, 0, 14),
            border=ft.Border(left=ft.BorderSide(3, accent_color)),
            content=ft.Column(
                spacing=0,
                controls=[
                    ft.Text(title, size=14, weight=ft.FontWeight.BOLD, color=accent_color),
                    ft.Container(height=6),
                    *items
                ]
            )
        )

    def _section_divider(self):
        return ft.Container(height=1, bgcolor="#2a2a2a", margin=ft.Margin(0, 4, 0, 4))

    def _build_appearance(self):
        return self._setting_card("🎨 外观", [
            self._setting_item("语言", "界面显示语言",
                               self._dropdown("language", ["简体中文", "English", "繁體中文"])),
            self._setting_item("主题", "界面颜色主题",
                               self._dropdown("theme", ["深色", "浅色", "跟随系统"])),
        ], "#7B61FF")

    def _build_file_split(self):
        return self._setting_card("✂️ 文件分割", [
            self._setting_item("文件大小", "按文件大小分割（如 4GB / 1000MB，留空禁用）",
                               self._textfield("split_by_size", hint="如: 4GB", width=120)),
            self._section_divider(),
            self._setting_item("视频时长", "按时长分割（格式 HH:MM:SS，留空禁用）",
                               self._chevron_field("split_by_duration", hint="01:00:00", width=110)),
            self._section_divider(),
            self._setting_item("编码改变", "在编码改变处自动切割文件", self._switch("split_on_codec_change")),
            self._setting_item("流不连续", "在流不连续处自动切割文件", self._switch("split_on_stream_discontinuity")),
            self._setting_item("标题改变", "直播标题改变时自动切割", self._switch("split_on_title_change")),
            self._setting_item("类别改变", "直播类别改变时自动切割", self._switch("split_on_category_change")),
        ], "#EB5757")

    def _build_network(self):
        # 显示一行摘要并提供箭头打开详细设置对话框
        summary_tf = ft.TextField(value=str(self._gs("proxy") or ""), hint_text="留空禁用",
                                  width=200, dense=True, disabled=True)
        # 保存引用，以便在对话框保存后更新显示
        self._controls["proxy_summary"] = summary_tf
        def _on_chevron_click(e):
            logging.info("代理 chevron 被点击")
            try:
                try:
                    self.notifier.info("打开代理设置...")
                except Exception:
                    pass
                self._open_proxy_dialog()
            except Exception as ex:
                logging.error(f"_open_proxy_dialog 调用失败: {ex}")

        chevron_btn = ft.IconButton(icon=ft.Icons.CHEVRON_RIGHT, icon_color="#888888", on_click=_on_chevron_click)

        control_row = ft.Row(spacing=6, controls=[summary_tf, chevron_btn])

        return self._setting_card("🌐 网络", [
            self._setting_item("全局代理", "HTTP/SOCKS5 代理地址（如 http://127.0.0.1:7890）",
                               control_row),
        ], "#27AE60")

    def _build_stream_record(self):
        return self._setting_card("📡 直播流录制", [
            self._setting_item("启用录制", "全局开关，关闭后所有房间停止录制", self._switch("stream_record_enabled")),
            self._section_divider(),
            self._setting_item("允许仅音频", "允许录制仅音频的直播流", self._switch("allow_audio_only")),
            self._setting_item("自动切换", "有新画质或格式时自动切换流", self._switch("auto_switch_stream")),
            self._section_divider(),
            self._setting_item("流优先参数", "优先级排序依据",
                               self._dropdown("stream_priority_param",
                                              ["分辨率", "帧率", "码率", "编码", "格式", "网址"])),
            self._setting_item("分辨率优先", "优先选择的分辨率",
                               self._dropdown("stream_resolution", ["原画", "超清", "高清", "流畅"], width=110)),
            self._setting_item("帧率优先", "优先选择的帧率",
                               self._dropdown("stream_fps",
                                              ["30 fps", "60 fps", "120 fps", "25 fps", "20 fps", "15 fps"],
                                              width=110)),
            self._setting_item("码率优先", "优先选择的码率",
                               self._chevron_field("stream_bitrate", hint="30.0 Mb/s", width=120)),
            self._setting_item("编码优先", "优先选择的编码",
                               self._dropdown("stream_codec", ["av1", "hevc", "avc"], width=100)),
            self._setting_item("格式优先", "优先选择的封装格式",
                               self._dropdown("stream_format", ["fmp4", "flv", "ts"], width=100)),
        ], "#2D9CDB")

    def _build_chat_record(self):
        return self._setting_card("💬 聊天消息录制", [
            self._setting_item("启用", "录制直播间弹幕和聊天消息", self._switch("chat_record_enabled")),
            self._setting_item("凭据", "下载聊天消息使用的 SESSDATA",
                               self._textfield("chat_credential", hint="留空使用房间设置", width=180, password=True)),
            self._setting_item("输出格式", "聊天记录保存格式",
                               self._dropdown("chat_format", ["jsonl 数据", "xml", "ass 弹幕"])),
        ], "#BB6BD9")

    def _build_schedule(self):
        return self._setting_card("📅 录制计划", [
            self._setting_item("时区", "录制计划使用的时区",
                               self._dropdown("schedule_timezone",
                                              ["UTC", "Asia/Shanghai", "Asia/Tokyo", "America/New_York",
                                               "Europe/London"], width=160)),
            self._setting_item("开始录制", "仅在此时间后开始（HH:MM，留空不限）",
                               self._textfield("schedule_start", hint="如: 08:00", width=100)),
            self._setting_item("停止录制", "到达此时间后停止（HH:MM，留空不限）",
                               self._textfield("schedule_stop", hint="如: 23:00", width=100)),
        ], "#F2994A")

    def _build_automation(self):
        return self._setting_card("⚡ 自动化", [
            self._setting_item("Webhooks", "录制事件通知的 Webhook 地址（每行一个）",
                               self._textfield("webhooks", hint="https://...", width=220)),
        ], "#56CCF2")

    def _build_file_location(self):
        return self._setting_card("📁 文件位置", [
            self._setting_item("保存目录", "所有录制文件的根目录",
                               self._directory_field("save_dir", hint=VIDEO_SAVE_DIR, width=180)),
            self._setting_item("路径模板", "用于生成最终文件路径的 liquid 模板",
                               self._template_editor_field("编辑路径模板", "path_template",
                                                           self._gs("path_template") or ""))
        ], "#F2C94C")

    def _build_convert(self):
        return self._setting_card("🔄 转换格式", [
            self._setting_item("启用转换", "录制完成后自动转换视频格式", self._switch("convert_enabled")),
            self._setting_item("删除原文件", "转换成功后删除原始录制文件", self._switch("convert_delete_source")),
            self._setting_item("目标格式", "转换的目标视频格式",
                               self._dropdown("convert_format", ["mp4", "mkv", "ts", "flv"], width=100)),
        ], "#EB5757")

    def _build_monitor(self):
        return self._setting_card("👁️ 直播监控", [
            self._setting_item("轮询延时", "每次轮询之间的等待时间",
                               self._dropdown("monitor_delay", ["自动", "5 秒", "10 秒", "30 秒", "1 分钟"],
                                              width=110)),
            self._setting_item("轮询间隔", "检查直播状态的时间间隔",
                               self._dropdown("monitor_interval", ["自动", "10 秒", "30 秒", "1 分钟", "5 分钟"],
                                              width=110)),
            self._setting_item("并发数", "同时轮询的房间数量上限",
                               self._dropdown("monitor_concurrency", ["自动", "5", "10", "20", "50"], width=110)),
            self._setting_item("防抖延迟", "下播状态确认延迟，防止误触发",
                               self._dropdown("monitor_debounce", ["禁用", "30 秒", "1 分钟", "3 分钟", "5 分钟"],
                                              width=110)),
            self._setting_item("监控代理", "专用于监控请求的代理地址",
                               self._textfield("monitor_proxy", hint="留空使用全局代理", width=180)),
        ], "#2D9CDB")

    def _build_cover(self):
        return self._setting_card("🖼️ 封面下载", [
            self._setting_item("启用", "开播时自动下载直播封面图片", self._switch("download_cover")),
        ], "#BB6BD9")

    def _build_conditions(self):
        return self._setting_card("🎯 录制条件", [
            self._setting_item("直播标题", "仅录制标题包含以下关键词的直播（多个用英文逗号分隔）",
                               self._textfield("condition_title", hint="留空不过滤", width=200)),
            self._setting_item("直播类别", "仅录制分区包含以下关键词的直播（多个用英文逗号分隔）",
                               self._textfield("condition_category", hint="留空不过滤", width=200)),
            self._setting_item("直播时段", "仅在指定时段录制（格式: 08:00-23:00，留空不限）",
                               self._textfield("condition_time_range", hint="如: 08:00-23:00", width=140)),
        ], "#F2994A")

    def _build_notify(self):
        return self._setting_card("🔔 通知", [
            self._setting_item("启用通知", "开播/下播/错误时发送通知", self._switch("notify_enabled")),
            self._setting_item("通知地址", "通知服务的 Webhook 地址（支持 Bark / PushPlus 等）",
                               self._textfield("notify_url", hint="https://...", width=220)),
            self._setting_item("标题模板", "通知标题的 Liquid 模板",
                               self._textfield("notify_title_template", hint="{{ uname }} 开播了", width=200)),
            self._setting_item("正文模板", "通知正文的 Liquid 模板",
                               self._textfield("notify_body_template", hint="{{ title }}", width=200)),
            self._section_divider(),
            self._setting_item("直播结束通知", "直播结束时发送通知", self._switch("notify_on_live_end")),
            self._setting_item("错误通知", "发生录制错误时发送通知", self._switch("notify_on_error")),
        ], "#56CCF2")

    def _build_system(self):
        return self._setting_card("⚙️ 系统", [
            self._setting_item("开机自启", "系统启动时自动运行本程序", self._switch("auto_start")),
            self._setting_item("阻止休眠", "录制期间阻止系统进入休眠状态", self._switch("prevent_sleep")),
        ], "#6FCF70")

    def build(self) -> ft.Control:
        # Left and right columns (no per-column scrolling) — we'll provide one outer scrollbar
        left_col = ft.Column(
            expand=True, spacing=0,
            controls=[
                self._build_appearance(),
                self._build_file_split(),
                self._build_network(),
                self._build_stream_record(),
                self._build_chat_record(),
                self._build_schedule(),
                self._build_automation(),
            ]
        )

        right_col = ft.Column(
            expand=True, spacing=0,
            controls=[
                self._build_file_location(),
                self._build_convert(),
                self._build_monitor(),
                self._build_cover(),
                self._build_conditions(),
                self._build_notify(),
                self._build_system(),
            ]
        )

        header = ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Row(spacing=10, controls=[
                    ft.Icon(ft.Icons.TUNE, color="#2D9CDB", size=22),
                    ft.Text("全局设置", size=22, weight=ft.FontWeight.BOLD, color="white"),
                ]),
                ft.Container(
                    bgcolor="#1a2a1a", border_radius=6, padding=ft.Padding(10, 5, 10, 5),
                    content=ft.Row(spacing=6, controls=[
                        ft.Icon(ft.Icons.INFO_OUTLINE, color="#4ade80", size=14),
                        ft.Text("所有更改实时保存，立即生效", size=11, color="#4ade80"),
                    ])
                )
            ]
        )

        return ft.Container(
            expand=True, padding=20,
            content=ft.Column(
                expand=True, spacing=0,
                controls=[
                    header,
                    ft.Container(height=14),
                    # single outer scroll container containing two columns side-by-side
                    ft.Column(
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                        controls=[
                            ft.Row(
                                expand=True, spacing=16,
                                vertical_alignment=ft.CrossAxisAlignment.START,
                                controls=[left_col, right_col]
                            )
                        ]
                    )
                ]
            )
        )


# ==========================================
# 主程序
# ==========================================
class RecorderApp:
    def __init__(self, page: ft.Page):
        self.page = page
        self.notifier = NotificationManager(page)
        self.cards = []
        self.search_text = ""
        self.current_view = "home"

        self.cards_grid = ft.Row(
            spacing=15, run_spacing=15, wrap=True,
            scroll=ft.ScrollMode.AUTO, expand=True,
            alignment=ft.MainAxisAlignment.START,
            vertical_alignment=ft.CrossAxisAlignment.START
        )

        self.input_room = ft.TextField(hint_text="输入直播间链接或房间号", width=220, dense=True)
        self.search_input = ft.TextField(hint_text="搜索", width=220, dense=True, on_change=self.on_search_change)

        self.dd_monitor = ft.Dropdown(label="监控状态", width=140, dense=True, value="全部")
        self.dd_monitor.on_change = self.on_search_change
        self.dd_monitor.options = [ft.dropdown.Option("全部"), ft.dropdown.Option("监控中"),
                                   ft.dropdown.Option("已禁用"), ft.dropdown.Option("出错了")]

        self.dd_live = ft.Dropdown(label="直播状态", width=140, dense=True, value="全部")
        self.dd_live.on_change = self.on_search_change
        self.dd_live.options = [ft.dropdown.Option("全部"), ft.dropdown.Option("直播中"), ft.dropdown.Option("未开播")]

        self.dd_record = ft.Dropdown(label="录制状态", width=140, dense=True, value="全部")
        self.dd_record.on_change = self.on_search_change
        self.dd_record.options = [ft.dropdown.Option("全部"), ft.dropdown.Option("录制中"),
                                  ft.dropdown.Option("闲置中"), ft.dropdown.Option("出错了")]

        self.filter_bar = ft.Container(
            visible=False, padding=ft.Padding(0, 10, 0, 10),
            content=ft.Row(spacing=15, controls=[self.dd_monitor, self.dd_live, self.dd_record])
        )

        self.sort_desc = True
        self.dd_sort = ft.Dropdown(
            label="排序方式", width=120, dense=True, value="添加时间",
            options=[
                ft.dropdown.Option("添加时间"), ft.dropdown.Option("主播名称"),
                ft.dropdown.Option("直播标题"), ft.dropdown.Option("监控状态"),
                ft.dropdown.Option("直播状态"), ft.dropdown.Option("录制状态"),
                ft.dropdown.Option("录制用时"), ft.dropdown.Option("录制总计")
            ]
        )
        self.dd_sort.on_change = self.on_sort_change

        self.btn_sort_dir = ft.IconButton(
            icon=ft.Icons.ARROW_DOWNWARD, icon_color="white", icon_size=18,
            tooltip="降序 (点击切换)", on_click=self.toggle_sort_dir
        )

        self.page.title = "B站高级录播机"
        self.page.theme_mode = ft.ThemeMode.DARK
        self.page.bgcolor = "#000000"
        self.page.padding = 0
        self.page.window_width = 1280
        self.page.window_height = 720

        self.build_ui()
        self.load_data()
        self.start_ui_refresher()

    def notify(self, message, is_error=False):
        if is_error:
            self.notifier.error(message)
        else:
            self.notifier.success(message)

    def build_sidebar(self):
        self.nav_home_btn = ft.IconButton(
            icon=ft.Icons.LIVE_TV, icon_color="#2D9CDB", icon_size=24,
            on_click=lambda e: self.switch_page("home")
        )
        self.nav_settings_btn = ft.IconButton(
            icon=ft.Icons.SETTINGS, icon_color="#888888", icon_size=24,
            on_click=lambda e: self.switch_page("settings")
        )

        return ft.Container(
            width=72, bgcolor="#0C0D10",
            content=ft.Column(
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                controls=[
                    ft.Container(height=10),
                    ft.Icon(ft.Icons.CROP_FREE, color="#2D9CDB", size=34),
                    ft.Container(height=8),
                    ft.Container(
                        width=44, height=44, border_radius=10, bgcolor="#1F2C3A",
                        content=ft.Column(alignment=ft.MainAxisAlignment.CENTER,
                                          horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                                          controls=[ft.Icon(ft.Icons.DASHBOARD, color="#7BB6FF", size=22)])
                    ),
                    ft.Container(height=12),
                    self.nav_home_btn,
                    ft.Container(expand=True),
                    self.nav_settings_btn,
                    ft.Container(height=20),
                    ft.Icon(ft.Icons.INFO_OUTLINE, color="white", size=24),
                    ft.Container(height=20),
                ]
            )
        )

    def switch_page(self, target_page):
        if target_page == "home":
            self.nav_home_btn.icon_color = "#2D9CDB"
            self.nav_settings_btn.icon_color = "#888888"
            self.home_view.visible = True
            self.settings_view.visible = False
        else:
            self.nav_home_btn.icon_color = "#888888"
            self.nav_settings_btn.icon_color = "#2D9CDB"
            self.home_view.visible = False
            self.settings_view.visible = True
        self.page.update()

    def toggle_filter_bar(self, e):
        self.filter_bar.visible = not self.filter_bar.visible
        self.page.update()

    def toggle_sort_dir(self, e):
        self.sort_desc = not self.sort_desc
        if self.sort_desc:
            self.btn_sort_dir.icon = ft.Icons.ARROW_DOWNWARD
            self.btn_sort_dir.tooltip = "降序 (点击切换)"
        else:
            self.btn_sort_dir.icon = ft.Icons.ARROW_UPWARD
            self.btn_sort_dir.tooltip = "升序 (点击切换)"
        self.apply_sorting()

    def on_sort_change(self, e=None):
        self.apply_sorting()

    def apply_sorting(self):
        key = self.dd_sort.value

        def get_sort_val(card):
            if key == "添加时间":
                return card.add_index
            elif key == "主播名称":
                return card.recorder.uname
            elif key == "直播标题":
                return card.pending_title
            elif key == "监控状态":
                return card.pending_m
            elif key == "直播状态":
                return card.pending_l
            elif key == "录制状态":
                return card.pending_r
            elif key == "录制用时":
                if card.recorder.is_recording and card.recorder.record_start_time:
                    return time.time() - card.recorder.record_start_time
                return 0
            elif key == "录制总计":
                if card.recorder.is_recording:
                    current = os.path.getsize(card.recorder.current_save_path) \
                        if card.recorder.current_save_path and os.path.exists(card.recorder.current_save_path) else 0
                    return card.recorder.accumulated_size + current
                return 0
            return 0

        self.cards.sort(key=get_sort_val, reverse=self.sort_desc)
        self.cards_grid.controls.clear()
        for card in self.cards:
            self.cards_grid.controls.append(card.view)
        self.page.update()

    def build_topbar(self):
        return ft.Row(
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
            controls=[
                ft.Text("频道", size=26, weight=ft.FontWeight.BOLD, color="white"),
                ft.Row(spacing=12, controls=[
                    ft.Row(spacing=6, vertical_alignment=ft.CrossAxisAlignment.CENTER,
                           controls=[ft.Icon(ft.Icons.SEARCH, color="white", size=20), self.search_input]),
                    ft.IconButton(icon=ft.Icons.FILTER_ALT, icon_color="white", icon_size=18,
                                  tooltip="展开/收起状态筛选", on_click=self.toggle_filter_bar),
                    self.dd_sort,
                    self.btn_sort_dir,
                    self.input_room,
                    ft.Button(
                        content=ft.Row(
                            spacing=6, tight=True,
                            controls=[ft.Icon(ft.Icons.ADD, size=18), ft.Text("添加")]
                        ),
                        on_click=self.add_channel_click
                    )
                ])
            ]
        )

    def build_ui(self):
        self.home_view = ft.Container(
            expand=True, padding=20,
            content=ft.Column(
                expand=True,
                controls=[
                    self.build_topbar(),
                    self.filter_bar,
                    ft.Container(height=5),
                    self.cards_grid
                ]
            )
        )

        gs_page = GlobalSettingsPage(self.page, self.notifier)
        self.settings_view = gs_page.build()
        self.settings_view.visible = False

        main_content = ft.Row(
            expand=True, spacing=0,
            controls=[
                self.build_sidebar(),
                ft.Container(width=1, bgcolor="#232323"),
                ft.Stack(expand=True, controls=[self.home_view, self.settings_view])
            ]
        )

        root = ft.Stack(
            expand=True,
            controls=[
                main_content,
                ft.Container(
                    bottom=20, right=20, width=360,
                    content=self.notifier.container
                )
            ]
        )
        self.page.add(root)

    def load_data(self):
        data = load_app_data()
        for ch in data.get("channels", []):
            try:
                self.add_card(ch, save=False, show_message=False)
            except Exception as e:
                logging.error(f"加载频道失败: {ch}, 错误: {e}")
        self.page.update()

    def save_all_data(self):
        channels = []
        for card in self.cards:
            channels.append({
                "room_id": card.room_info["room_id"],
                "uname": card.recorder.uname,
                "title": card.room_info.get("title", ""),
                "parent_area_name": card.room_info.get("parent_area_name", ""),
                "area_name": card.room_info.get("area_name", ""),
                "face": card.room_info.get("face", ""),
                "enabled": card.switch.value
            })
        save_app_data(channels)

    def refresh_cards_visibility(self):
        keyword = self.search_text.strip().lower()
        fm = self.dd_monitor.value
        fl = self.dd_live.value
        fr = self.dd_record.value

        for card in self.cards:
            uname = card.room_info.get("uname", "").lower()
            title = card.room_info.get("title", "").lower()
            rid = card.room_info.get("room_id", "").lower()
            match_text = (keyword in uname) or (keyword in title) or (keyword in rid) if keyword else True

            match_m = True
            if fm == "监控中":
                match_m = "监控" in card.pending_m
            elif fm == "已禁用":
                match_m = "暂停" in card.pending_m
            elif fm == "出错了":
                match_m = "出错" in card.pending_m

            match_l = True
            if fl == "直播中":
                match_l = "直播" in card.pending_l
            elif fl == "未开播":
                match_l = "未开播" in card.pending_l

            match_r = True
            if fr == "录制中":
                match_r = "录制" in card.pending_r
            elif fr == "闲置中":
                match_r = "闲置" in card.pending_r
            elif fr == "出错了":
                match_r = "出错" in card.pending_r

            card.view.visible = match_text and match_m and match_l and match_r

        self.page.update()

    def on_search_change(self, e=None):
        self.search_text = self.search_input.value or ""
        self.refresh_cards_visibility()

    def add_card(self, room_info, save=True, show_message=True):
        room_id = str(room_info.get("room_id", "")).strip()
        if not room_id: return False

        room_info = {
            "room_id": room_id,
            "uname": room_info.get("uname") or room_info.get("name") or f"房间_{room_id}",
            "title": room_info.get("title", ""),
            "parent_area_name": room_info.get("parent_area_name", "未知分区"),
            "area_name": room_info.get("area_name", "未知内容"),
            "face": room_info.get("face", ""),
            "enabled": room_info.get("enabled", True)
        }

        for c in self.cards:
            if str(c.room_info["room_id"]) == room_id:
                if show_message:
                    self.notifier.error("该直播间已存在", title="添加失败")
                return False

        add_index = len(self.cards)
        card = FletChannelCard(self.page, room_info, self, add_index)
        self.cards.append(card)
        self.cards_grid.controls.append(card.view)

        if save: self.save_all_data()
        self.refresh_cards_visibility()
        self.apply_sorting()
        return True

    def remove_card(self, card_obj):
        if card_obj in self.cards: self.cards.remove(card_obj)
        if card_obj.view in self.cards_grid.controls:
            self.cards_grid.controls.remove(card_obj.view)
        self.save_all_data()
        self.notifier.success("频道已删除", title="删除成功")

    def add_channel_click(self, e):
        value = self.input_room.value.strip()
        if not value:
            self.notifier.error("请输入直播间链接或房间号", title="输入错误")
            return

        self.notifier.info("正在获取直播间信息...", title="请稍候")
        info = get_bili_info(value)

        if not info:
            self.notifier.error("获取直播间信息失败", title="添加失败")
            return

        if self.add_card({
            "room_id": info["room_id"],
            "uname": info["uname"],
            "title": info["title"],
            "parent_area_name": info.get("parent_area_name", "未知分区"),
            "area_name": info.get("area_name", "未知内容"),
            "face": info.get("face", ""),
            "enabled": True
        }, save=True):
            self.input_room.value = ""
            self.notifier.success(f"已添加：{info['uname']}", title="添加成功")
            self.page.update()

    def start_ui_refresher(self):
        self.page.run_task(self.ui_refresh_loop)

    async def ui_refresh_loop(self):
        while True:
            try:
                need_update = False
                for card in self.cards:
                    if getattr(card, "ui_dirty", False):
                        card.apply_pending_ui()
                        need_update = True
                if need_update:
                    self.refresh_cards_visibility()
                    self.apply_sorting()
            except Exception as e:
                # If the underlying Flet session was destroyed (for example user closed the view),
                # further attempts to update the UI will raise errors like
                # "An attempt to fetch destroyed session.". When that happens, stop the loop
                # to avoid noisy repeated logging.
                msg = str(e)
                logging.error(f"UI刷新循环失败: {msg}")
                if "destroyed session" in msg or "fetch destroyed session" in msg:
                    logging.info("检测到已销毁的会话，停止 UI 刷新循环。")
                    break
            await asyncio.sleep(2)


def main(page: ft.Page):
    RecorderApp(page)


if __name__ == "__main__":
    ft.app(target=main)
