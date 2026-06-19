# core/config.py
import os
import re
import sys
import json
import logging

if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

VIDEO_SAVE_DIR = os.path.join(APP_DIR, "录播文件")
os.makedirs(VIDEO_SAVE_DIR, exist_ok=True)

CONFIG_FILE = os.path.join(APP_DIR, "config.json")
DATA_FILE = os.path.join(APP_DIR, "data.json")

# ==================== 默认全局设置 ====================
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
    "proxy_mode": "禁用",
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
    "webhook_format": "blrec",

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
    "notify_title_template": "{%- case event -%}\n  {%- when 'live_start', 'live_end' -%}\n    [{{ platform }}]\n    {{- ' ' -}}\n    {%- if user_name != '' -%}\n      {{ user_name }}\n    {%- else -%}\n      {{ channel }}\n    {%- endif -%}\n    {{- ' ' -}}\n    {%- if event == 'live_start' -%}\n      is live\n    {%- else -%}\n      is offline\n    {%- endif -%}\n    {%- if title != '' -%}\n      : {{ title }}\n    {%- endif -%}\n  {%- when 'error' -%}\n    something went wrong\n{%- endcase -%}",
    "notify_body_template": '{%- assign scheme = service_url | split: \'://\' | first -%}\n\n{%- case event -%}\n  {%- when \'live_start\', \'live_end\' -%}\n    {%- case scheme -%}\n      {%- when \'mailto\', \'mailtos\' -%}\n<p><span><b>URL</b>: </span><span><a href="{{ url }}" target="_blank">{{ url }}</a></span></p>\n<p><span><b>Platform</b>: </span><span>{{ platform }}</span></p>\n<p><span><b>Channel</b>: </span><span>{{ channel }}</span></p>\n<p><span><b>User ID</b>: </span><span>{{ user_id }}</span></p>\n<p><span><b>User Name</b>: </span><span>{{ user_name }}</span></p>\n<p><span><b>Avatar</b>: </span><span><a href="{{ avatar }}" target="_blank">{{ avatar }}</a></span></p>\n<p><span><b>Title</b>: </span><span>{{ title }}</span></p>\n<p><span><b>Categories</b>: </span><span>{{ categories | join: \', \' }}</span></p>\n<p><span><b>Live ID</b>: </span><span>{{ live_id }}</span></p>\n<p><span><b>Start Time</b>: </span><span>{{ start_time }}</span></p>\n<div><a href="{{ cover }}" target="_blank"><img src="{{ cover }}" alt="Cover"/></a></div>\n      {%- when \'discord\' -%}\n**URL**: {{ url }}\n**Platform**: {{ platform }}\n**Channel**: {{ channel }}\n**User ID**: {{ user_id }}\n**User Name**: {{ user_name }}\n**Avatar**: [{{ avatar }}]({{ avatar }})\n**Title**: {{ title }}\n**Categories**: {{ categories | join: \', \' }}\n**Live ID**: {{ live_id }}\n**Start Time**: {{ start_time }}\n![Cover]({{ cover }})\n      {%- else -%}\nURL: {{ url }}\nPlatform: {{ platform }}\nChannel: {{ channel }}\nUser ID: {{ user_id }}\nUser Name: {{ user_name }}\nAvatar: {{ avatar }}\nTitle: {{ title }}\nCategories: {{ categories | join: \', \' }}\nLive ID: {{ live_id }}\nStart Time: {{ start_time }}\nCover: {{ cover }}\n    {%- endcase -%}\n  {%- when \'error\' -%}\n    {%- case scheme -%}\n      {%- when \'mailto\', \'mailtos\' -%}\n<pre style="color: red; font-weight: 800;">\n{{ error }}\n</pre>\n      {%- when \'discord\' -%}\n```\n{{ error }}\n```\n      {%- else -%}\n{{ error }}\n    {%- endcase -%}\n{%- endcase -%}',
    "notify_on_live_end": True,
    "notify_on_error": True,

    # 系统
    "auto_start": True,
    "prevent_sleep": True,
}

# ==================== 配置管理 ====================
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


def reload_config() -> bool:
    """从磁盘重新加载 config.json，覆盖内存中的 CONFIG。

    用途：用户修改 config.json 后不重启 bilirec 也能立即生效（在开始录制/开始监控时调用）。
    返回 True 表示 reload 成功，False 表示失败。
    """
    if not os.path.exists(CONFIG_FILE):
        return False
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if not isinstance(loaded, dict):
            return False
        # 整体替换 rooms（per-room 删了的也跟着删）
        CONFIG["rooms"] = loaded.get("rooms", {})
        # 全局变量
        CONFIG["global"] = loaded.get("global", CONFIG["global"])
        # 全局设置：保留 DEFAULT_GLOBAL_SETTINGS 的所有 key，但用磁盘值覆盖
        saved_gs = loaded.get("global_settings", {})
        for k, v in DEFAULT_GLOBAL_SETTINGS.items():
            CONFIG["global_settings"][k] = saved_gs.get(k, v)
        # 用户新增的 key 也带上
        for k, v in saved_gs.items():
            if k not in CONFIG["global_settings"]:
                CONFIG["global_settings"][k] = v
        return True
    except Exception as e:
        logging.error(f"reload_config 失败: {e}")
        return False


def ensure_default_config():
    """如果 config.json 不存在，生成默认配置。"""
    if not os.path.exists(CONFIG_FILE):
        save_config()


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
    return fmt if fmt else (get_global_setting("convert_format") or "mp4")


def get_effective_save_dir(room_id, uname):
    room_cfg = get_room_config(room_id)
    # custom_dir wins; then per-room override; then global; then built-in default
    base_dir = (
        room_cfg.get("custom_dir", "").strip()
        or get_room_setting(room_id, "save_dir")
        or get_global_setting("save_dir")
        or VIDEO_SAVE_DIR
    )
    save_dir = os.path.join(base_dir, uname)
    os.makedirs(save_dir, exist_ok=True)
    return save_dir


def _extract_sessdata(raw):
    """从用户粘贴的 SESSDATA 字符串中提取真正的 SESSDATA 值。

    支持两种粘贴方式：
      1) 裸 SESSDATA 值：5011cd87%2C1797158105%2C4ce7a%2A61CjAtEsoMxbrV9iE1oGgJZLn7VY1U73wPUn-...
      2) 完整 cookie 串：buvid_fp=...; SESSDATA=5011cd87%2C...; bili_jct=...; sid=...
    """
    if not raw:
        return ""
    s = str(raw).strip().strip(';').strip()
    if not s:
        return ""
    # 情况 2：粘了完整 cookie，抠出 SESSDATA= 后面到分号/结尾之间的值
    m = re.search(r"(?:^|[\s;,])SESSDATA=([^;,\s]+)", s)
    if m:
        return m.group(1).strip()
    # 情况 1：裸值，原样返回
    return s


def get_headers(room_id=None, monitor=False):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://live.bilibili.com/"
    }
    if room_id:
        room_cfg = CONFIG["rooms"].get(str(room_id), {})
        sessdata = _extract_sessdata(room_cfg.get("sessdata", ""))
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


# ==================== 应用数据存储 (channels) ====================
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
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        logging.error(f"保存数据失败: {e}")


# ==================== room per-settings API ====================
def get_room_setting(room_id, key):
    room_id = str(room_id)
    if room_id not in CONFIG["rooms"]:
        return get_global_setting(key)
    overrides = CONFIG["rooms"][room_id].get("overrides", {})
    if key in overrides:
        return overrides[key]
    return get_global_setting(key)

def set_room_setting(room_id, key, value):
    room_id = str(room_id)
    if room_id not in CONFIG["rooms"]:
        CONFIG["rooms"][room_id] = {
            "sessdata": "", "format": "", "quality": 10000,
            "custom_dir": "", "overrides": {}
        }
    defaults = DEFAULT_GLOBAL_SETTINGS.get(key)
    if value == defaults:
        if "overrides" in CONFIG["rooms"][room_id] and key in CONFIG["rooms"][room_id]["overrides"]:
            del CONFIG["rooms"][room_id]["overrides"][key]
    else:
        if "overrides" not in CONFIG["rooms"][room_id]:
            CONFIG["rooms"][room_id]["overrides"] = {}
        CONFIG["rooms"][room_id]["overrides"][key] = value
    save_config()

def has_room_override(room_id, key):
    room_id = str(room_id)
    if room_id not in CONFIG["rooms"]:
        return False
    return key in CONFIG["rooms"][room_id].get("overrides", {})