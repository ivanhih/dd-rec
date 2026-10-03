# core/config.py
import os
import re
import sys
import json
import logging
import uuid

PACK_ID = "dd_rec"
USERDATA_DIR_NAME = "userdata"   # 独立目录名,不跟录播文件混
VIDEOS_DIR_NAME = "录播文件"      # 用户视频目录(独立于 userdata)


def _portable_root() -> str:
    """获取程序根目录。

    新结构中 dd_rec.exe、version.ini、ffmpeg/、userdata/ 均位于同一目录。
    仍兼容旧版主程序位于版本化子目录、version.ini 位于父目录的结构。
    """
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if os.path.exists(os.path.join(exe_dir, "version.ini")):
            return exe_dir
        parent = os.path.dirname(exe_dir)
        if os.path.exists(os.path.join(parent, "version.ini")):
            return parent
        return exe_dir
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _user_data_dir() -> str:
    """用户数据目录 — portable 根/userdata/

    不再用 %AppData%:
      - 录播文件保持独立(在 portable 根/录播文件/)
      - userdata 单独管理(config/data/日志/缓存/db)
      - kachina update.exe 通过 userDataPath + ignoreFolderPath 保护
    """
    d = os.path.join(_portable_root(), USERDATA_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _video_save_dir() -> str:
    """录播文件目录 — portable 根/录播文件/

    跟 userdata 平级:录播文件是用户视频,不应该跟配置文件混在一起。
    kachina ignoreFolderPath 保护。
    """
    d = os.path.join(_portable_root(), VIDEOS_DIR_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def _is_portable_mode() -> bool:
    """检测是否为 Portable 模式（兼容新旧目录结构）。"""
    if not getattr(sys, "frozen", False):
        return False
    exe_dir = os.path.dirname(sys.executable)
    parent_dir = os.path.dirname(exe_dir)
    return any(
        os.path.exists(os.path.join(base, "version.ini"))
        for base in (exe_dir, parent_dir)
    )


def _read_version_ini() -> str:
    """读取新结构同目录或旧结构父目录中的 version.ini。"""
    if not getattr(sys, "frozen", False):
        return ""
    exe_dir = os.path.dirname(sys.executable)
    for base in (exe_dir, os.path.dirname(exe_dir)):
        version_ini = os.path.join(base, "version.ini")
        try:
            if os.path.exists(version_ini):
                with open(version_ini, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("version="):
                            return line.split("=", 1)[1].strip()
        except Exception:
            logging.exception("读取 version.ini 失败: %s", version_ini)
    return ""


def _resource_dir() -> str:
    """代码自带资源目录；打包后资源与唯一主程序位于同一 onedir。"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# 用户数据目录(可读写,跨更新保留) — portable 根/userdata/
APP_DIR = _user_data_dir()
# 录播文件目录(可读写,跨更新保留) — portable 根/录播文件/,跟 userdata 平级
VIDEO_SAVE_DIR = _video_save_dir()

# 只读资源目录（exe 同级）
RESOURCE_DIR = _resource_dir()

CONFIG_FILE = os.path.join(APP_DIR, "config.json")
DATA_FILE = os.path.join(APP_DIR, "data.json")

# 历史默认（改默认前写死在用户 config 里的值）。仅当仍等于这些旧默认时才迁移。
LEGACY_STREAM_DEFAULTS = {
    "stream_codec": "av1",
    "stream_format": "fmp4",
}
# 迁移一次性标记：避免用户升级后主动改回 av1/fmp4 又被再次覆盖
STREAM_DEFAULTS_MIGRATION_FLAG = "stream_defaults_migrated_v2"
DEPRECATED_GLOBAL_SETTINGS_KEYS = frozenset({"mirror_chyan_enabled", "mirror_chyan_cdk"})

# ==================== 外观可选项（设置页下拉 / 后续主题与 i18n 共用） ====================
# 配置里仍存展示字符串，兼容已有 userdata/config.json；内部映射在 Phase 1+ 再做。
LANGUAGE_OPTIONS = ("简体中文", "繁體中文", "English")
THEME_OPTIONS = ("深色", "浅色")

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
    "stream_codec": "h264",
    "stream_format": "flv",
    "stream_url_priority": "",
    # 找不到目标组合时：compatible=尽量同 codec；strict=宁可不录；best_effort=任意最高质量
    "stream_fallback_policy": "compatible",
    # 内部：旧 av1/fmp4 默认是否已迁移（用户勿手改）
    STREAM_DEFAULTS_MIGRATION_FLAG: False,

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
    # webhook 失败日志：false=同 URL 连续失败合并/限频；true=每次失败都 warning
    "webhook_log_verbose": False,
    "webhook_fail_log_interval_sec": 300,

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

    # 转换格式（遗留 key，仍可读；新逻辑优先 artifact_*）
    "convert_enabled": True,
    "convert_delete_source": False,
    "convert_format": "mp4",

    # 关闭文件后处理（无损优先）
    "artifact_profile": "lossless_first",
    "artifact_strict_decode_validation": True,
    "artifact_keep_source": True,
    "webhook_only_verified_artifacts": True,
    "flv_native_capture": True,
    # 原生 FLV CDN 慢流切换（后端默认；首版不暴露 UI）
    "flv_cdn_failover_enabled": True,
    "flv_cdn_failover_startup_grace_sec": 45,
    "flv_cdn_failover_lag_sec": 30,
    "flv_cdn_failover_sustain_sec": 20,
    "flv_cdn_failover_recovery_lag_sec": 15,
    "flv_cdn_failover_cooldown_sec": 60,
    "flv_cdn_endpoint_quarantine_sec": 300,
    "flv_cdn_outage_restart_sec": 300,
    "flv_cdn_speed_window_sec": 20,
    "flv_cdn_slow_speed_ratio": 0.8,
    "flv_cdn_recovery_speed_ratio": 0.95,
    "flv_cdn_recovery_sustain_sec": 20,
    "flv_cdn_media_stall_sec": 15,
    "flv_cdn_refresh_interval_sec": 30,
    "flv_cdn_allow_quality_change": False,

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

    # 磁盘守卫 / 挂机心跳 / 单房间自愈（UI 后续接入；先有默认值保证老配置可 load）
    "disk_min_free_gb": 5.0,
    # History page soft warning; effective value is always max(warn, min_free, min_free*3 floor at 15)
    "disk_warn_free_gb": 15.0,
    "disk_check_interval_sec": 60,
    "heartbeat_interval_sec": 300,
    "recorder_auto_heal": True,
    "recorder_auto_heal_max_restarts": 3,
    "recorder_auto_heal_window_sec": 600,

    # 用户自定义标签库：[{id, name, color}, ...]；房间绑定见 rooms[id].tag_ids
    "tag_library": [],

    # 更新通道
    "update_channel": "github",      # 用户上次选择的下载通道(github / cnb)
}

# ==================== 配置管理 ====================
CONFIG = {
    "global_settings": dict(DEFAULT_GLOBAL_SETTINGS),
    "global": {
        "default_save_dir": VIDEO_SAVE_DIR
    },
    "rooms": {}
}


def apply_config_migrations(saved_gs: dict | None, gs: dict) -> list[str]:
    """对已合并的 global_settings 做一次性兼容迁移。

    规则：
      1. 仅当 stream_codec/stream_format **同时**仍为历史默认 av1/fmp4 时，迁移到 h264/flv；
         用户只改了其中一项的，视为明确选择，不覆盖。
      2. 旧 convert_delete_source=True 且磁盘上还没有 artifact_keep_source 时，
         映射为 artifact_keep_source=False。
      3. 迁移幂等：写 STREAM_DEFAULTS_MIGRATION_FLAG，避免用户后来主动改回旧值被再迁一次。

    返回人类可读的迁移说明列表（空 = 无变化）。
    """
    notes: list[str] = []
    saved_gs = saved_gs or {}

    already = bool(gs.get(STREAM_DEFAULTS_MIGRATION_FLAG) or saved_gs.get(STREAM_DEFAULTS_MIGRATION_FLAG))
    if not already:
        codec = str(gs.get("stream_codec") or "").strip().lower()
        fmt = str(gs.get("stream_format") or "").strip().lower()
        legacy_codec = LEGACY_STREAM_DEFAULTS["stream_codec"].lower()
        legacy_fmt = LEGACY_STREAM_DEFAULTS["stream_format"].lower()
        if codec == legacy_codec and fmt == legacy_fmt:
            gs["stream_codec"] = DEFAULT_GLOBAL_SETTINGS["stream_codec"]
            gs["stream_format"] = DEFAULT_GLOBAL_SETTINGS["stream_format"]
            notes.append(
                f"stream_codec/format: {legacy_codec}/{legacy_fmt} -> "
                f"{gs['stream_codec']}/{gs['stream_format']}（旧默认值一次性迁移）"
            )
        gs[STREAM_DEFAULTS_MIGRATION_FLAG] = True

    # convert_delete_source → artifact_keep_source（仅磁盘缺新 key 时）
    if "artifact_keep_source" not in saved_gs and "convert_delete_source" in saved_gs:
        delete_src = bool(saved_gs.get("convert_delete_source"))
        keep = not delete_src
        if bool(gs.get("artifact_keep_source", True)) != keep:
            gs["artifact_keep_source"] = keep
            notes.append(
                f"artifact_keep_source={keep}（由旧 convert_delete_source={delete_src} 映射）"
            )
        else:
            gs["artifact_keep_source"] = keep

    return notes


def _merge_global_settings(saved_gs: dict | None) -> tuple[dict, list[str], bool]:
    """DEFAULT ∪ saved → 迁移后的 global_settings、迁移日志、是否需要写回磁盘。"""
    saved_gs = saved_gs if isinstance(saved_gs, dict) else {}
    removed_keys = sorted(DEPRECATED_GLOBAL_SETTINGS_KEYS.intersection(saved_gs))
    effective_saved = {
        k: v for k, v in saved_gs.items()
        if k not in DEPRECATED_GLOBAL_SETTINGS_KEYS
    }

    merged = dict(DEFAULT_GLOBAL_SETTINGS)
    for k, v in DEFAULT_GLOBAL_SETTINGS.items():
        if k in effective_saved:
            merged[k] = effective_saved[k]
    # 保留用户自定义额外 key，但明确废弃的更新源配置除外。
    for k, v in effective_saved.items():
        if k not in merged:
            merged[k] = v

    notes = apply_config_migrations(effective_saved, merged)
    if removed_keys:
        notes.append("移除已停用更新源配置: " + ", ".join(removed_keys))

    channel = str(merged.get("update_channel") or "github").strip().lower()
    if channel not in {"github", "cnb"}:
        merged["update_channel"] = "github"
        notes.append(f"update_channel: {channel or '(empty)'} -> github")

    dirty = bool(notes) or bool(removed_keys) or (
        not bool(effective_saved.get(STREAM_DEFAULTS_MIGRATION_FLAG))
        and bool(merged.get(STREAM_DEFAULTS_MIGRATION_FLAG))
    )
    return merged, notes, dirty


def _log_effective_stream_settings(gs: dict, migration_notes: list[str] | None = None) -> None:
    codec = gs.get("stream_codec")
    fmt = gs.get("stream_format")
    policy = gs.get("stream_fallback_policy")
    if migration_notes:
        for n in migration_notes:
            logging.info(f"⚙️ 配置迁移: {n}")
    logging.info(
        f"📺 实际使用直播流: codec={codec}, format={fmt}, fallback={policy}"
    )


if os.path.exists(CONFIG_FILE):
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            loaded = json.load(f)
            if isinstance(loaded, dict):
                CONFIG["global"] = loaded.get("global", CONFIG["global"])
                CONFIG["rooms"] = loaded.get("rooms", {})
                saved_gs = loaded.get("global_settings", {})
                merged, notes, dirty = _merge_global_settings(saved_gs)
                CONFIG["global_settings"] = merged
                if dirty:
                    try:
                        # save_config 在下方定义；启动路径用内联写盘避免前向引用问题
                        with open(CONFIG_FILE, "w", encoding="utf-8") as wf:
                            json.dump(CONFIG, wf, ensure_ascii=False, indent=4)
                    except Exception as e:
                        logging.warning(f"配置迁移后写回失败: {e}")
                _log_effective_stream_settings(merged, notes)
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
        saved_gs = loaded.get("global_settings", {})
        merged, notes, dirty = _merge_global_settings(saved_gs)
        CONFIG["global_settings"] = merged
        if dirty:
            try:
                save_config()
            except Exception as e:
                logging.warning(f"reload 配置迁移后写回失败: {e}")
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


_TAG_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
DEFAULT_TAG_COLOR = "#3B82F6"


def _normalize_tag_name(name) -> str:
    # 单行、去首尾空白，限制长度避免卡片布局被撑爆
    return " ".join(str(name or "").split())[:20]


def _normalize_tag_color(color) -> str:
    value = str(color or "").strip()
    return value.upper() if _TAG_COLOR_RE.fullmatch(value) else DEFAULT_TAG_COLOR


def get_tag_library() -> list[dict]:
    """Return a normalized copy of the user-defined tag library."""
    raw = get_global_setting("tag_library")
    if not isinstance(raw, list):
        return []
    out = []
    seen_ids = set()
    seen_names = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        tag_id = str(item.get("id") or "").strip()
        name = _normalize_tag_name(item.get("name"))
        name_key = name.casefold()
        if not tag_id or not name or tag_id in seen_ids or name_key in seen_names:
            continue
        seen_ids.add(tag_id)
        seen_names.add(name_key)
        out.append({"id": tag_id, "name": name, "color": _normalize_tag_color(item.get("color"))})
    return out


def get_tag(tag_id: str) -> dict | None:
    wanted = str(tag_id or "")
    return next((tag for tag in get_tag_library() if tag["id"] == wanted), None)


def upsert_tag(name: str, color: str = DEFAULT_TAG_COLOR, tag_id: str | None = None) -> dict:
    """Create or update a tag. Names are unique case-insensitively."""
    clean_name = _normalize_tag_name(name)
    if not clean_name:
        raise ValueError("标签名称不能为空")
    clean_color = _normalize_tag_color(color)
    tags = get_tag_library()
    current_id = str(tag_id or "").strip()
    for tag in tags:
        if tag["name"].casefold() == clean_name.casefold() and tag["id"] != current_id:
            raise ValueError("标签名称已存在")
    if current_id:
        for tag in tags:
            if tag["id"] == current_id:
                tag.update(name=clean_name, color=clean_color)
                set_global_setting("tag_library", tags)
                return dict(tag)
        raise KeyError(current_id)
    created = {"id": f"tag_{uuid.uuid4().hex[:12]}", "name": clean_name, "color": clean_color}
    tags.append(created)
    set_global_setting("tag_library", tags)
    return dict(created)


def delete_tag(tag_id: str) -> bool:
    """Delete from library and remove all room bindings."""
    tag_id = str(tag_id or "")
    tags = get_tag_library()
    kept = [tag for tag in tags if tag["id"] != tag_id]
    if len(kept) == len(tags):
        return False
    CONFIG["global_settings"]["tag_library"] = kept
    for room in CONFIG.get("rooms", {}).values():
        if not isinstance(room, dict):
            continue
        room["tag_ids"] = [tid for tid in room.get("tag_ids", []) if str(tid) != tag_id]
    save_config()
    return True


def set_room_tag_ids(room_id, tag_ids) -> list[str]:
    valid = {tag["id"] for tag in get_tag_library()}
    clean = []
    for tag_id in tag_ids or []:
        tag_id = str(tag_id)
        if tag_id in valid and tag_id not in clean:
            clean.append(tag_id)
    room = get_room_config(room_id)
    room["tag_ids"] = clean
    save_config()
    return list(clean)


def get_room_tags(room_id) -> list[dict]:
    room = get_room_config(room_id)
    by_id = {tag["id"]: tag for tag in get_tag_library()}
    return [dict(by_id[tag_id]) for tag_id in room.get("tag_ids", []) if tag_id in by_id]


def get_room_config(room_id):
    room_id = str(room_id)
    if room_id not in CONFIG["rooms"]:
        CONFIG["rooms"][room_id] = {
            "sessdata": "",
            "format": "",
            "quality": 10000,
            "custom_dir": "",
            "tag_ids": [],
        }
        save_config()
        return CONFIG["rooms"][room_id]
    room = CONFIG["rooms"][room_id]
    # 老房间缺 tag_ids 时补默认，不强制立刻写盘（下次 save 会带上）
    if "tag_ids" not in room or not isinstance(room.get("tag_ids"), list):
        room["tag_ids"] = []
    return room


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


def get_headers(room_id=None, monitor=False, *, media=False):
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://live.bilibili.com/"
    }
    credential = get_effective_cookie(room_id)
    if credential:
        headers["Cookie"] = credential
    # 如果为监控请求且专用监控代理已设置，则优先使用之
    if monitor:
        mon = get_global_setting("monitor_proxy") or ""
        if mon:
            headers["_proxy"] = mon
            return headers
    # 根据代理模式决定是否使用代理
    setting = (lambda key: get_room_setting(room_id, key)) if media and room_id is not None else get_global_setting
    proxy_mode = setting("proxy_mode") or "禁用"
    if proxy_mode == "自定义":
        proxy = setting("proxy") or ""
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


def get_effective_cookie(room_id=None, *, chat=False):
    """Resolve one account for API, media and chat, preserving legacy overrides."""
    from core.cookies import cookie_header
    from core.bili_auth import account_service

    room = CONFIG["rooms"].get(str(room_id), {}) if room_id is not None else {}
    mode = room.get("auth_mode", "inherit")
    if mode == "anonymous":
        return ""
    if mode == "account":
        return account_service().store.cookie()
    manual = room.get("sessdata", "")
    chat_override = room.get("overrides", {}).get("chat_credential", "")
    if mode == "manual":
        return cookie_header((chat_override or manual) if chat else manual)
    if chat and "chat_credential" in room.get("overrides", {}):
        return cookie_header(chat_override)
    if manual:
        return cookie_header(manual)
    shared = account_service().store.cookie()
    return shared or (cookie_header(get_global_setting("chat_credential")) if chat else "")


def set_room_auth_mode(room_id, mode):
    if mode not in {"inherit", "account", "anonymous", "manual"}:
        raise ValueError("Invalid recording account mode")
    get_room_config(room_id)["auth_mode"] = mode
    save_config()


def bind_all_rooms_to_account():
    """Use the shared account without deleting any saved manual credentials."""
    for room in CONFIG["rooms"].values():
        if isinstance(room, dict):
            room["auth_mode"] = "account"
    save_config()


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
    if room_id is None:
        return get_global_setting(key)
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


# 注:不再需要 migrate_legacy_data —— 老 NSIS 流程已废弃,用户数据走 portable 根/userdata/。
# kachina update.exe 通过 userDataPath + ignoreFolderPath 自动保护 userdata/ 和 录播文件/。
