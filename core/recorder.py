# core/recorder.py
import sys
import time
import datetime
import threading
import logging
import subprocess
import os
import platform
import re
import json
import urllib.request
import uuid
from typing import Optional
from PySide6.QtCore import QObject, Signal, QThread

from core.config import (
    get_global_setting, get_room_setting, get_room_config, get_effective_format,
    get_effective_save_dir, VIDEO_SAVE_DIR, RESOURCE_DIR
)
from core.bili_api import get_bili_info, get_stream_info
from core.utils import format_size, render_path_template
from core.danmaku_recorder import DanmakuRecorder
from core.ffmpeg_tools import FFMPEG_CMD as _FFMPEG_FROM_TOOLS, hidden_subprocess_kwargs
from core.media_pipeline import ArtifactResult, ArtifactStatus, get_default_pipeline
from core.flv.session import FlvCaptureSession, CaptureEvent
from core.stream_source import StreamSource
from core.ffmpeg_flv_source import FfmpegFlvSource
from core.http_ssl import urlopen as _ssl_urlopen


def _get_ffmpeg_path():
    """获取 ffmpeg 路径；新结构优先使用 dd_rec.exe 同级的 ffmpeg/。"""
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        candidates = (
            os.path.join(exe_dir, "ffmpeg", "ffmpeg.exe"),
            os.path.join(RESOURCE_DIR, "ffmpeg", "ffmpeg.exe"),
            # 兼容旧版主程序位于版本化子目录的结构。
            os.path.join(os.path.dirname(exe_dir), "ffmpeg", "ffmpeg.exe"),
        )
        for path in candidates:
            if os.path.exists(path):
                return path
        return "ffmpeg"
    return "ffmpeg"


def _extract_ffmpeg():
    """从打包资源中提取 ffmpeg（如果尚未提取）

    注意：Portable 模式下 ffmpeg 在根目录，不需要提取
    """
    if not getattr(sys, 'frozen', False):
        return  # 开发环境不需要

    # 新结构优先检查主程序同级；父目录检查只用于兼容旧版结构。
    exe_dir = os.path.dirname(sys.executable)
    for base in (exe_dir, os.path.dirname(exe_dir)):
        if os.path.exists(os.path.join(base, "ffmpeg", "ffmpeg.exe")):
            return

    # 旧模式：从打包资源提取
    base_dir = RESOURCE_DIR
    ffmpeg_dir = os.path.join(base_dir, "ffmpeg")
    ffmpeg_path = os.path.join(ffmpeg_dir, "ffmpeg.exe")
    ffprobe_path = os.path.join(ffmpeg_dir, "ffprobe.exe")

    # 如果 ffmpeg 已存在，直接返回
    if os.path.exists(ffmpeg_path):
        return

    # 确保目录存在
    os.makedirs(ffmpeg_dir, exist_ok=True)

    # 确定 ffmpeg 源目录（datas 打包到 ffmpeg/ 子目录）
    if hasattr(sys, '_MEIPASS'):
        src_dir = os.path.join(sys._MEIPASS, 'ffmpeg')
    else:
        src_dir = base_dir

    src_ffmpeg = os.path.join(src_dir, "ffmpeg.exe")
    src_ffprobe = os.path.join(src_dir, "ffprobe.exe")

    # 复制到 ffmpeg/ 子目录
    import shutil
    if os.path.exists(src_ffmpeg):
        shutil.copy2(src_ffmpeg, ffmpeg_path)
    if os.path.exists(src_ffprobe):
        shutil.copy2(src_ffprobe, ffprobe_path)


# 启动时尝试提取 ffmpeg
_extract_ffmpeg()

if platform.system() == "Windows":
    LOCAL_FFMPEG = _get_ffmpeg_path()
else:
    LOCAL_FFMPEG = _get_ffmpeg_path()

# 优先使用 core.ffmpeg_tools（含项目内 ffmpeg/ 目录）；旧逻辑作回退
_legacy = LOCAL_FFMPEG if os.path.exists(LOCAL_FFMPEG) else "ffmpeg"
FFMPEG_CMD = _FFMPEG_FROM_TOOLS if os.path.exists(_FFMPEG_FROM_TOOLS) or _FFMPEG_FROM_TOOLS != "ffmpeg" else _legacy


def _build_save_path(room_id, uname, title, now_dt):
    """根据路径模板渲染最终保存路径，含完整扩展名"""
    fmt = get_effective_format(room_id)
    global_dir = get_room_setting(room_id, "save_dir") or VIDEO_SAVE_DIR
    room_cfg = get_room_config(room_id)
    custom_dir = room_cfg.get("custom_dir", "").strip()

    template_str = get_room_setting(room_id, "path_template") or (
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


def _send_webhooks(event: str, room_id: str, uname: str, title: str, extra: dict = None):
    """把录播事件推送到 biliupforjava 的 /recordWebHook 端点。

    payload 严格按 FQrabbit/biliupforjava 的 RecordEventDTO 字段命名：
      - 顶层 PascalCase（EventType / EventTimestamp / EventId / EventData）
      - EventData 内也是 PascalCase（RoomId / Name / Title / RelativePath / FileSize / Duration ...）
      - 时间字段为 ISO8601 带时区
    """
    raw = (get_room_setting(room_id, "webhooks") or "").strip()
    if not raw:
        return
    urls = [u.strip() for u in raw.splitlines() if u.strip()]
    if not urls:
        return

    fmt = (get_room_setting(room_id, "webhook_format") or "blrec").strip()

    # biliupforjava 会用 work-path + relativePath 拼绝对路径找 part record。
    # relativePath 必须是**相对 work-path 的相对路径**。
    _work_path = (get_room_setting(room_id, "webhook_work_path") or "").strip() \
                 or (get_room_setting(room_id, "save_dir") or "").strip()
    _work_path = os.path.normpath(_work_path) if _work_path else ""

    def _relpath(p: str) -> str:
        if not p:
            return ""
        if _work_path:
            try:
                rp = os.path.relpath(p, _work_path)
                if not rp.startswith(".."):
                    return rp.replace("\\", "/")
            except (ValueError, OSError):
                pass
        return p.replace("\\", "/")

    # 把 extra["file"] 从绝对路径转换为相对 _work_path 的相对路径
    if extra and extra.get("file"):
        extra = dict(extra)  # 复制一份避免污染调用方
        extra["file"] = _relpath(extra["file"])

    # —— 内部事件名 → biliupforjava EventType 枚举 ——
    evt_map_bililive = {
        "session_started":     "SessionStarted",
        "live_began":          "LiveBeganEvent",
        "recording_started":   "RecordingStartedEvent",
        "recording_stopped":   "RecordingFinishedEvent",
        "live_ended":          "LiveEndedEvent",
        "recording_split":     "FileClosed",
        "file_completed":      "VideoFileCompletedEvent",
        "space_change":        "RoomChangeEvent",
    }
    # 关键语义：bilirec 的 "recording_started" = ffmpeg 已经开写，文件已落地。
    # 一定要发 RecordingStartedEvent（不是 StreamStarted），这样 biliupforjava 才会
    # 在 Room 上挂一个活跃 RecordHistory；之后 FileClosed 才能找到 historyId 入队上传。

    def _build_bililive_payload() -> dict | None:
        evt_str = evt_map_bililive.get(event)
        if not evt_str:
            return None
        # SessionId 跨事件保持一致：recording_started 时由调用方通过 extra.session_id 注入，
        # 后续 recording_split / recording_stopped 复用同一个值。
        _session_id = (extra or {}).get("session_id") or str(uuid.uuid4())
        ed: dict = {
            "SessionId":       _session_id,
            "RoomId":          str(room_id),
            "ShortId":         0,
            "Name":            uname or "",
            "Title":           title or "",
            "AreaNameParent":  "",
            "AreaNameChild":   "",
            "Recording":       event in ("recording_started", "recording_split"),
            "Streaming":       event == "recording_started",
            "DanmakuConnected": True,
        }
        # 落盘类事件需要携带文件信息，否则 biliupforjava 没法上传
        if extra and extra.get("file"):
            ed["RelativePath"]  = str(extra["file"])
            ed["FileSize"]      = int(extra.get("size") or 0)
            ed["Duration"]      = float(extra.get("duration") or 0.0)
            open_t = extra.get("file_open_time")
            close_t = extra.get("file_close_time")
            ed["FileOpenTime"]  = open_t  if open_t  else datetime.datetime.now().astimezone().isoformat(timespec="microseconds")
            ed["FileCloseTime"] = close_t if close_t else datetime.datetime.now().astimezone().isoformat(timespec="microseconds")
        return {
            "EventType":      evt_str,
            "EventTimestamp": datetime.datetime.now().astimezone().isoformat(timespec="microseconds"),
            "EventId":        str(uuid.uuid4()),
            "EventData":      ed,
        }

    # —— 默认格式：biliupforjava 旧版 / 通用 JSON ——
    def _build_default_payload() -> dict:
        payload = {
            "event": event,
            "room_id": room_id,
            "uname": uname,
            "title": title,
            "time": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        if extra:
            payload.update(extra)
        return payload

    if fmt == "blrec":
        payload = _build_bililive_payload()
        if payload is None:
            payload = _build_default_payload()
    else:
        payload = _build_default_payload()

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _fire():
        for url in urls:
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers={"Content-Type": "application/json; charset=utf-8"},
                    method="POST",
                )
                with _ssl_urlopen(req, timeout=10) as resp:
                    body_preview = resp.read(200).decode("utf-8", errors="replace")
                    logging.info(f"Webhook OK {url} -> {resp.status} | EventType={payload.get('EventType') or payload.get('event')} | resp={body_preview!r}")
            except Exception as e:
                logging.warning(f"Webhook fail {url}: {e} | payload_keys={list(payload.keys())}")

    threading.Thread(target=_fire, daemon=True).start()


def _emit_recording_stopped_webhook(recorder) -> None:
    """下播/用户停止监听时统一调用：把"录制结束"事件按 RecordEventDTO 规范发出。

    必须在 recorder.stop_recording() 之前调用 —— 因为 stop_recording 会清零
    record_start_time / accumulated_size，duration/size 就拿不到了。
    """
    _stop_path = recorder.current_save_path
    _stop_size = 0
    _stop_duration = 0.0
    _file_close_time = datetime.datetime.now().astimezone().isoformat(timespec="microseconds")
    try:
        if _stop_path and os.path.exists(_stop_path):
            _stop_size = os.path.getsize(_stop_path)
        # 加上本次会话里已切片的累计大小
        _stop_size = int((recorder.accumulated_size or 0) + _stop_size)
        if recorder.record_start_time:
            _stop_duration = float(max(0, time.time() - recorder.record_start_time))
    except Exception:
        pass
    _send_webhooks("recording_stopped", recorder.room_id, recorder.uname, recorder.current_title or "",
                   {"file": _stop_path or "",
                    "size": _stop_size,
                    "duration": _stop_duration,
                    "file_open_time": (datetime.datetime.fromtimestamp(recorder.record_start_time).astimezone().isoformat(timespec="microseconds")
                                       if recorder.record_start_time else _file_close_time),
                    "file_close_time": _file_close_time,
                    "session_id": recorder.current_session_id})


def _emit_last_part_file_closed_webhook(recorder) -> None:
    """下播时如果当前还在写一个分P（ffmpeg 还没被 stop），
    给该 part 补发一个 FileClosed 事件 —— 不然最后一个分P 不会入队上传。

    biliupforjava 的 FileClosed 是 part 入库的**唯一**入口，
    RecordingFinishedEvent 只修 part 的结束态，**不**主动建 part。
    """
    _stop_path = recorder.current_save_path
    if not _stop_path or not os.path.exists(_stop_path):
        return
    _stop_size = 0
    _stop_duration = 0.0
    _file_close_time = datetime.datetime.now().astimezone().isoformat(timespec="microseconds")
    try:
        # 关键：只算**当前 part**的实际大小（不要加 accumulated_size）
        # 优先用 stop_recording 时记下的"停止瞬间大小"（防抖期内不会被虚涨）
        if recorder._last_part_size_at_stop and recorder._last_part_stop_time:
            _stop_size = recorder._last_part_size_at_stop
        else:
            _stop_size = os.path.getsize(_stop_path)
        # 关键：duration 用 current_part_start_time（不是 record_start_time），
        # 但**截止时间**用 stop_recording 时记下的"停止瞬间时间戳"，
        # 避免防抖 5 分钟内 duration 被虚涨。
        _open_time = recorder.current_part_start_time or recorder.record_start_time
        _close_time = recorder._last_part_stop_time or time.time()
        if _open_time:
            _stop_duration = float(max(0, _close_time - _open_time))
    except Exception:
        pass
    # FileClosed 仅在 media_pipeline 验证成功后发送（见 BiliRecorder._on_artifact_result）。
    # 这里保留函数供兼容调用，但默认不再盲发未验证源路径。
    if not get_room_setting(recorder.room_id, "webhook_only_verified_artifacts"):
        _send_webhooks("recording_split", recorder.room_id, recorder.uname, recorder.current_title or "",
                       {"file": _stop_path,
                        "size": _stop_size,
                        "duration": _stop_duration,
                        "file_open_time": (datetime.datetime.fromtimestamp(recorder.current_part_start_time or recorder.record_start_time).astimezone().isoformat(timespec="microseconds")
                                           if (recorder.current_part_start_time or recorder.record_start_time) else _file_close_time),
                        "file_close_time": _file_close_time,
                        "session_id": recorder.current_session_id})
    else:
        logging.info(f"⏭️ 跳过未验证 FileClosed（等待 pipeline）: {os.path.basename(_stop_path)}")


def _send_notify(event: str, room_id: str, uname: str, title: str, extra: dict = None):
    """使用 apprise 发送推送通知。event: recording_started / recording_stopped / error"""
    if not get_room_setting(room_id, "notify_enabled"):
        return
    if event == "recording_stopped" and not get_room_setting(room_id, "notify_on_live_end"):
        return
    if event == "error" and not get_room_setting(room_id, "notify_on_error"):
        return

    notify_url = (get_room_setting(room_id, "notify_url") or "").strip()
    if not notify_url:
        notify_url = (get_room_setting(room_id, "webhooks") or "").strip()
    if not notify_url:
        return

    # 映射 apprise 模板变量
    event_map = {"recording_started": "live_start", "recording_stopped": "live_end", "error": "error"}
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    liquid_ctx = {
        "event": event_map.get(event, event),
        "platform": "bilibili",
        "user_name": uname,
        "channel": room_id,
        "user_id": room_id,
        "title": title,
        "live_id": room_id,
        "categories": [],
        "url": f"https://live.bilibili.com/{room_id}",
        "service_url": notify_url,
        "start_time": now_str,
        "avatar": extra.get("face", "") if extra else "",
        "cover": extra.get("cover", "") if extra else "",
        "error": extra.get("error", "") if extra else "",
    }

    logging.info(f"debug cover_url={repr(liquid_ctx.get("cover",""))}")
    default_titles = {"recording_started": "\U0001f534 \u5f00\u59cb\u5f55\u5236", "recording_stopped": "\u23f9\ufe0f \u5f55\u5236\u7ed3\u675f", "error": "\u26a0\ufe0f \u5f55\u5236\u51fa\u9519"}
    default_bodies = {"recording_started": "{uname} \u5f00\u59cb\u76f4\u64ad\uff1a{title}", "recording_stopped": "{uname} \u5df2\u4e0b\u64ad\uff0c\u5f55\u5236\u7ed3\u675f", "error": "{uname}\uff08\u623f\u95f4 {room_id}\uff09\u5f55\u5236\u51fa\u9519"}

    title_tpl_raw = (get_room_setting(room_id, "notify_title_template") or "").strip()
    body_tpl_raw = (get_room_setting(room_id, "notify_body_template") or "").strip()

    from liquid import Template as LqTpl
    try:
        if title_tpl_raw:
            notify_title = LqTpl(title_tpl_raw).render(**liquid_ctx)
        else:
            notify_title = default_titles.get(event, event)
    except Exception:
        notify_title = default_titles.get(event, event)

    try:
        if body_tpl_raw:
            notify_body = LqTpl(body_tpl_raw).render(**liquid_ctx)
        else:
            ctx = {"uname": uname, "room_id": room_id, "title": title, "time": now_str}
            notify_body = default_bodies.get(event, event).format(**ctx)
    except Exception:
        notify_body = default_bodies.get(event, event)

    def _fire():
        try:
            import apprise
            ap = apprise.Apprise()
            for url in [u.strip() for u in notify_url.splitlines() if u.strip()]:
                ap.add(url)
            ap.notify(title=notify_title, body=notify_body)
            logging.info(f"\U0001f4e3 \u901a\u77e5\u5df2\u53d1\u9001 [{event}] {uname}")
        except ImportError:
            logging.info(f"apprise \u672a\u5b89\u88c5\uff0c\u5df2\u964d\u7ea7\u4f7f\u7528 urllib \u53d1\u9001\u901a\u77e5 [{event}] {uname}")
            payload = {"event": event, "room_id": room_id, "uname": uname, "title": notify_title, "body": notify_body, "time": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            for url in [u.strip() for u in notify_url.splitlines() if u.strip()]:
                try:
                    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
                    with _ssl_urlopen(req, timeout=10) as resp:
                        logging.debug(f"Notify {url} -> {resp.status}")
                except Exception as e2:
                    logging.warning(f"\u901a\u77e5\u53d1\u9001\u5931\u8d25 {url}: {e2}")
        except Exception as e:
            logging.warning(f"\u901a\u77e5\u53d1\u9001\u5931\u8d25: {e}")
    threading.Thread(target=_fire, daemon=True).start()
def _run_convert(src_path: str):
    """【已废弃】旧同步重编码转换入口。

    所有后处理统一走 media_pipeline（验证 → 无损 remux → FileClosed）。
    保留空壳仅为兼容可能的外部调用/热加载；**不再执行任何转换**，
    避免与 pipeline 双轨：重复转换、提前删源、提前 webhook。
    """
    logging.warning(
        f"⚠️ _run_convert 已废弃，忽略调用: {os.path.basename(src_path or '') or src_path!r}；"
        f"请使用 media_pipeline / BiliRecorder._submit_closed_source"
    )
    return


def _parse_monitor_seconds(key: str, default: float, room_id=None) -> float:
    """将 monitor_* 设置的文字值解析为秒数，无法解析则返回 default。"""
    raw_val = get_room_setting(room_id, key) if room_id else get_global_setting(key)
    raw = (raw_val or "").strip()
    if not raw or raw == "自动":
        return default
    # 匹配 "30 秒" / "1 分钟" / "5 分钟" 等格式
    import re as _re
    m = _re.match(r"^(\d+(?:\.\d+)?)\s*(秒|分钟|s|m)?$", raw)
    if not m:
        return default
    val = float(m.group(1))
    unit = m.group(2) or "秒"
    if unit in ("分钟", "m"):
        val *= 60
    return max(1.0, val)


def _download_cover(cover_url: str, save_dir: str, filename_base: str):
    """后台下载直播封面，保存为 <filename_base>.jpg 到 save_dir。"""
    if not cover_url or not save_dir:
        return

    def _do():
        try:
            import urllib.request as _req
            ext = cover_url.rsplit(".", 1)[-1].split("?")[0].lower()
            if ext not in ("jpg", "jpeg", "png", "webp"):
                ext = "jpg"
            dest = os.path.join(save_dir, f"{filename_base}.{ext}")
            req = _req.Request(cover_url, headers={"User-Agent": "Mozilla/5.0"})
            with _ssl_urlopen(req, timeout=15) as resp:
                with open(dest, "wb") as f:
                    f.write(resp.read())
            logging.info(f"🖼️ 封面已保存: {os.path.basename(dest)}")
        except Exception as e:
            logging.warning(f"封面下载失败: {e}")

    threading.Thread(target=_do, daemon=True).start()


class BiliRecorder(QObject):
    status_updated = Signal(str, str, str, str, str, str, str, str, str)  # m,l,r,title,duration,speed,size,parent,area
    cut_completed = Signal(str, str)  # room_id, file_name
    cut_failed = Signal(str, str)  # room_id, error
    finished = Signal()

    def __init__(self, room_info, parent=None):
        super().__init__(parent)
        self.room_info = room_info
        self.room_id = str(room_info.get("room_id") or "")
        if not self.room_id:
            raise ValueError("BiliRecorder 缺少 room_id，无法启动录制")
        self.uname = room_info.get("uname", f"房间_{self.room_id}")
        self.current_title = room_info.get("title", "未知标题")
        self.current_parent_area = room_info.get("parent_area_name", "未知分区")
        self.current_area = room_info.get("area_name", "未知内容")

        self.is_running = True
        self.is_monitoring = room_info.get("enabled", True)
        self._async_stop_requested = False
        self._async_stop_reset_time = True
        self._finished_emitted = False
        self.current_ffmpeg = None
        self.stream_url = None
        self.current_save_path = None
        self.record_start_time = 0
        # 当前 part 开始时间（每次开新 part 时更新）。用于算单个 part 的 duration，
        # 而不是整个录制会话的 duration。
        self.current_part_start_time = 0
        self.last_check_time = 0
        self.last_total_size = 0
        self.accumulated_size = 0
        self.last_api_check_time = 0
        # 本次"录制会话"使用的 SessionId，recording_started 时生成、recording_stopped 时清空。
        # 必须与 RecordingStartedEvent / RecordingFinishedEvent 保持一致，否则 biliupforjava
        # 会判定 session 错位（RecordEnd.IgnoreStaleSession）→ 不会触发上传。
        self.current_session_id = ""
        self._split_timer = None
        self.current_stream_base_url = None
        self._last_codec = None
        self._last_format = None
        self._ffmpeg_exit_count = 0
        self._danmaku_recorder: Optional[DanmakuRecorder] = None
        # 防抖期内关停录播后，记下"停止瞬间"的时间戳和 part 大小，
        # 让防抖到期发 FileClosed 时能算出"停止瞬间那一刻的 duration/size"，
        # 而不是"防抖结束后 5 分钟 + 实际 part 时长"的虚高值。
        self._last_part_stop_time = 0.0
        self._last_part_size_at_stop = 0
        self._live_first_seen = 0.0
        self.current_cover = room_info.get("cover", "")
        self.current_face = room_info.get("face", "")
        # 原生 FLV 捕获
        self._flv_session: Optional[FlvCaptureSession] = None
        self._stream_source: Optional[object] = None
        self._capture_mode = "none"  # none | flv | flv_bridge | ffmpeg
        self._cut_lock = threading.Lock()
        self._cutting = False
        # Old ffmpeg processes that stopped reading input but are still flushing trailers.
        # current_ffmpeg always points only to the active recorder.
        self._retiring_ffmpegs = {}
        self._retiring_ffmpeg_lock = threading.Lock()
        self._ffmpeg_stderr_path = None
        self._pending_artifacts = 0
        self._artifact_lock = threading.Lock()
        self._artifact_cv = threading.Condition(self._artifact_lock)
        self._emitted_artifact_paths = set()
        self._handler_token = uuid.uuid4().hex
        self._accepting_artifacts = True
        # pipeline 线程只入队；recorder 线程 drain 后才 emit，避免跨线程碰 QObject
        self._artifact_results = []
        self._source_gen = 0
        self._pipeline = get_default_pipeline()
        # 按 room_id 注册产物回调，避免共享 on_result 被新 recorder 覆盖后打到已销毁对象
        self._pipeline.register_result_handler(
            self.room_id, self._on_artifact_result, self._handler_token
        )

    @property
    def is_recording(self):
        if self._capture_mode in ("flv", "flv_bridge"):
            return self._stream_source is not None and self._stream_source.is_running
        return self.current_ffmpeg is not None and self.current_ffmpeg.poll() is None

    def request_stop(self, reset_time: bool = True):
        """同步停止入口；仅应在后台线程或 recorder 自身线程中调用。"""
        self.is_monitoring = False
        self.stop_recording(reset_time=reset_time)

    def request_stop_async(self, reset_time: bool = True):
        """只设置停止标记，让 run() 在 recorder 线程内完成可能阻塞的收尾。"""
        self._async_stop_requested = True
        self._async_stop_reset_time = reset_time
        self.is_monitoring = False
        self.is_running = False

    def _wait_pending_artifacts(self, timeout: float = 30.0) -> bool:
        """等待 pipeline 产物回调完成。返回是否全部收完。"""
        deadline = time.time() + max(0.0, timeout)
        with self._artifact_cv:
            while self._pending_artifacts > 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    logging.warning(
                        f"[recorder {self.room_id}] 等待产物超时: pending={self._pending_artifacts}"
                    )
                    return False
                self._artifact_cv.wait(timeout=remaining)
            return True

    def _emit_finished_once(self):
        if self._finished_emitted:
            return
        # 先拒绝新回调，再等已提交 job 收尾，最后才注销并 finished
        self._accepting_artifacts = False
        try:
            self._wait_pending_artifacts(timeout=30.0)
        except Exception:
            logging.exception(f"[recorder {self.room_id}] 等待产物失败")
        try:
            self._drain_artifact_results()
        except Exception:
            logging.exception(f"[recorder {self.room_id}] drain artifact results 失败")
        self._finished_emitted = True
        try:
            self._pipeline.unregister_result_handler(self.room_id, self._handler_token)
        except Exception:
            pass
        try:
            self.finished.emit()
        except RuntimeError:
            logging.warning(f"[recorder {self.room_id}] finished emit 时对象已销毁")

    def _on_artifact_result(self, result: ArtifactResult, token: Optional[str] = None):
        """media-pipeline 线程入口：只入队，绝不 emit。"""
        if token is not None and token != getattr(self, "_handler_token", None):
            logging.info(f"⏭️ 丢弃过期产物回调 room={self.room_id}")
            return

        with self._artifact_cv:
            self._pending_artifacts = max(0, self._pending_artifacts - 1)
            if getattr(self, "_finished_emitted", False):
                logging.info(
                    f"📦 finished 后产物丢弃: status={result.status} "
                    f"src={os.path.basename(result.source_path or '')}"
                )
            else:
                # 收尾等待期也要入队，由 _emit_finished_once 在 recorder 线程 drain
                self._artifact_results.append(result)
            self._artifact_cv.notify_all()

    def _drain_artifact_results(self):
        """recorder 线程调用：处理已完成产物并安全 emit。"""
        with self._artifact_cv:
            pending = list(self._artifact_results)
            self._artifact_results.clear()
        for result in pending:
            self._handle_artifact_result(result)

    def _handle_artifact_result(self, result: ArtifactResult):
        if getattr(self, "_finished_emitted", False):
            return
        if not hasattr(self, "_emitted_artifact_paths"):
            self._emitted_artifact_paths = set()
        emitted = self._emitted_artifact_paths

        only_verified = True
        try:
            only_verified = bool(get_room_setting(self.room_id, "webhook_only_verified_artifacts"))
        except Exception:
            pass

        ok_for_webhook = (
            result.status == ArtifactStatus.SUCCESS
            and bool(result.final_path)
            and not str(result.final_path).endswith(".capture.flv")
            and (result.health or "").lower() not in ("failed", "red")
        )
        if result.status == ArtifactStatus.WARNING and not only_verified:
            ok_for_webhook = bool(result.final_path)

        if ok_for_webhook:
            path_key = os.path.normcase(os.path.abspath(result.final_path))
            if path_key in emitted:
                logging.info(f"⏭️ 跳过重复 FileClosed: {os.path.basename(result.final_path)}")
                return
            emitted.add(path_key)
            logging.info(f"✅ 产物就绪: {os.path.basename(result.final_path)}")
            try:
                _send_webhooks(
                    "recording_split",
                    result.room_id or self.room_id,
                    self.uname,
                    self.current_title or "",
                    {
                        "file": result.final_path,
                        "size": result.size,
                        "duration": result.duration,
                        "session_id": result.session_id or self.current_session_id,
                    },
                )
            except Exception:
                logging.exception("recording_split webhook 失败")
            try:
                self.cut_completed.emit(self.room_id, os.path.basename(result.final_path))
            except RuntimeError:
                pass
        else:
            logging.error(
                f"🟥 产物失败/警告: status={result.status} src={os.path.basename(result.source_path)} "
                f"err={result.error or result.warnings}"
            )
            try:
                self.cut_failed.emit(
                    self.room_id,
                    result.error or ",".join(result.warnings) or "artifact_failed",
                )
            except RuntimeError:
                pass

    def _submit_closed_source(
        self,
        path: str,
        close_reason: str = "",
        health: str = "",
        session_id: Optional[str] = None,
    ):
        if not path:
            return
        if not getattr(self, "_accepting_artifacts", True):
            logging.warning(f"跳过收尾后提交: {os.path.basename(path)}")
            return
        preferred = ""
        base, ext = os.path.splitext(path)
        if base.endswith(".capture"):
            preferred = base[: -len(".capture")] + ".mp4"
        elif ext.lower() != ".mp4":
            preferred = base + ".mp4"
        with self._artifact_cv:
            self._pending_artifacts += 1
        try:
            self._pipeline.submit_closed_part(
                path,
                # Finalization is asynchronous, so use the session captured at handoff.
                # current_session_id may already be cleared or belong to a newer session.
                session_id=self.current_session_id if session_id is None else session_id,
                room_id=self.room_id,
                close_reason=close_reason,
                health_hint=health,
                preferred_final=preferred,
            )
        except Exception:
            with self._artifact_cv:
                self._pending_artifacts = max(0, self._pending_artifacts - 1)
                self._artifact_cv.notify_all()
            raise

    def _use_native_flv(self, stream_info: dict) -> bool:
        if not get_room_setting(getattr(self, "room_id", None), "flv_native_capture"):
            return False
        fmt = (stream_info.get("format") or "").lower()
        codec = (stream_info.get("codec") or "").lower()
        return fmt == "flv" and codec in ("avc", "h264")

    def _use_ffmpeg_flv_bridge(self, stream_info: dict) -> bool:
        """Use one persistent FFmpeg reader for non-FLV H264 sources."""
        if not get_room_setting(getattr(self, "room_id", None), "flv_native_capture"):
            return False
        fmt = (stream_info.get("format") or "").lower()
        codec = (stream_info.get("codec") or "").lower()
        return fmt != "flv" and codec in ("avc", "h264")

    @staticmethod
    def _capture_path_for_save(save_path: str) -> str:
        base, _ = os.path.splitext(save_path)
        return f"{base}.capture.flv"

    def _capture_path_factory(self, base_save_path: str):
        base, _ = os.path.splitext(base_save_path)

        def factory(index: int) -> str:
            if index <= 1:
                return self._capture_path_for_save(base_save_path)
            return f"{base}.p{index:03d}.capture.flv"

        return factory

    def _start_flv_capture(self, save_path: str, stream_url: str, *, bridge: bool = False):
        """启动原生 FLV 管线。"""
        # 重连/重启：先关闭旧源与旧段（submit=True 让上一段进入 pipeline）
        old_mode = self._capture_mode
        if old_mode in ("flv", "flv_bridge") and self._flv_session is not None:
            self._stop_flv_capture(submit=True)
        else:
            self._stop_flv_capture(submit=False)
        session = FlvCaptureSession(
            self._capture_path_factory(save_path),
            on_event=self._on_flv_event,
            require_audio=True,
            require_video=True,
        )
        session.start()
        self._flv_session = session
        self._capture_mode = "flv_bridge" if bridge else "flv"
        self._source_gen = getattr(self, "_source_gen", 0) + 1
        source_gen = self._source_gen

        def on_data(chunk: bytes):
            # 旧 source 线程的迟到数据直接丢弃，避免写到已切换的 session
            if source_gen != getattr(self, "_source_gen", -1):
                return
            if self._flv_session is not session:
                return
            session.feed(chunk)

        def on_disc(reason: str):
            if source_gen != getattr(self, "_source_gen", -1):
                return
            source_name = "FFmpeg-to-FLV bridge" if bridge else "FLV"
            logging.warning(f"Room {self.room_id} {source_name} source disconnected: {reason}")
            # 断开时关当前段并重置会话状态，避免重连后追加到旧文件
            try:
                if self._flv_session is session:
                    session.reset_for_reconnect(reason or "disconnect")
            except Exception as e:
                logging.debug(f"reset_for_reconnect failed: {e}")
            # 由 run loop 检测 is_recording 变化后处理重启

        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://live.bilibili.com/"}
        if bridge:
            src = FfmpegFlvSource(
                stream_url,
                on_data=on_data,
                on_disconnected=on_disc,
                headers=headers,
                env=self._get_proxy_env(),
                log_path=save_path + ".bridge.ffmpeg.log",
                ffmpeg_cmd=FFMPEG_CMD,
            )
        else:
            src = StreamSource(stream_url, on_data=on_data, on_disconnected=on_disc, headers=headers)
        try:
            src.start()
            if bridge:
                # Catch unsupported codecs/muxer failures before publishing this source.
                time.sleep(0.05)
                proc = src.process
                if proc is not None and proc.poll() is not None:
                    raise RuntimeError(f"FFmpeg FLV bridge exited during startup: {proc.returncode}")
        except Exception:
            try:
                src.stop(wait=1.0)
            except Exception:
                pass
            session.close(reason="source_start_failed")
            self._flv_session = None
            self._capture_mode = "none"
            raise
        self._stream_source = src
        logging.info(
            f"Single-input capture started for room {self.room_id}: "
            f"mode={'ffmpeg_flv_bridge' if bridge else 'native_flv'}"
        )
        # current_save_path 先用逻辑路径；实际文件可能是 .capture.flv
        self.current_save_path = save_path
        return True

    def _on_flv_event(self, event: CaptureEvent, payload: dict):
        if event == CaptureEvent.SEGMENT_OPENED:
            path = payload.get("path") or ""
            if path:
                self.current_save_path = path
                self.current_part_start_time = time.time()
                logging.info(f"🎬 FLV 分段打开: {os.path.basename(path)}")
        elif event == CaptureEvent.SEGMENT_CLOSED:
            path = payload.get("path") or ""
            health = payload.get("health") or ""
            reason = payload.get("close_reason") or ""
            logging.info(f"💾 FLV 分段关闭: {os.path.basename(path)} health={health} reason={reason}")
            if path and os.path.exists(path):
                self._submit_closed_source(path, close_reason=reason, health=health)
        elif event == CaptureEvent.ISSUE:
            logging.warning(f"⚠️ FLV issue: {payload}")

    def _stop_flv_capture(self, submit: bool = True):
        # 递增 generation，让旧 source 线程回调失效
        self._source_gen = getattr(self, "_source_gen", 0) + 1
        src = self._stream_source
        self._stream_source = None
        if src:
            src.stop(wait=5.0)
        sess = self._flv_session
        self._flv_session = None
        if sess:
            segs = sess.close(reason="stop")
            if not submit:
                return
            # SEGMENT_CLOSED 事件里已 submit；避免重复
            # 若 close 时未触发事件（极端），补交
            for s in segs:
                # 事件路径已 submit；这里仅兜底未写 sidecar 的情况
                pass
        if self._capture_mode in ("flv", "flv_bridge"):
            self._capture_mode = "none"

    def _request_ffmpeg_stop(self, process) -> bool:
        """Tell ffmpeg to stop reading input and start writing its trailer immediately.

        This only writes stdin ``q`` and does not wait for exit. During a cut the new
        recorder can take over while the old process finishes flush/moov in background.
        """
        if not process or process.poll() is not None:
            return True
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.write(b"q\n")
                process.stdin.flush()
                logging.debug(f"ffmpeg pid={getattr(process, 'pid', '?')} received stdin q")
                return True
        except Exception as e:
            logging.debug(f"ffmpeg stdin q failed; falling back to signal immediately: {e}")
        return False

    def _wait_for_ffmpeg_exit(
        self,
        process,
        wait_trailer: float = 10.0,
        stop_requested: bool = True,
    ):
        """Wait for trailer completion, then escalate through signal/terminate/kill."""
        if not process or process.poll() is not None:
            return
        import signal as _sig

        # A successful q gets time to flush in-flight packets and write the trailer.
        if stop_requested:
            try:
                process.wait(timeout=wait_trailer)
                logging.debug(f"ffmpeg exited within {wait_trailer}s after stdin q")
                return
            except subprocess.TimeoutExpired:
                pass
            except Exception as e:
                logging.debug(f"ffmpeg wait after stdin q failed: {e}")

        # If q failed or timed out, use SIGINT immediately.
        try:
            process.send_signal(_sig.CTRL_BREAK_EVENT if platform.system() == "Windows" else _sig.SIGINT)
        except Exception:
            pass
        try:
            process.wait(timeout=wait_trailer)
            logging.debug(f"ffmpeg exited within {wait_trailer}s after SIGINT")
            return
        except subprocess.TimeoutExpired:
            pass
        except Exception as e:
            logging.debug(f"ffmpeg wait after SIGINT failed: {e}")

        try:
            process.terminate()
            process.wait(timeout=5)
            return
        except Exception:
            pass

        try:
            process.kill()
            process.wait(timeout=2)
        except Exception:
            pass

    def _graceful_stop_ffmpeg(self, process, wait_trailer: float = 10.0):
        """Synchronously stop ffmpeg and wait until its trailer is complete."""
        stop_requested = self._request_ffmpeg_stop(process)
        self._wait_for_ffmpeg_exit(process, wait_trailer, stop_requested)

    def _retire_ffmpeg_async(
        self,
        process,
        save_path: str,
        session_id: str,
        close_reason: str = "cut",
    ):
        """Stop an old ffmpeg input now and submit its file after background finalization."""
        if not process:
            return

        started_at = time.monotonic()
        key = id(process)
        stop_requested = self._request_ffmpeg_stop(process)
        record = {
            "process": process,
            "path": save_path,
            "session_id": session_id,
            "thread": None,
        }
        with self._retiring_ffmpeg_lock:
            self._retiring_ffmpegs[key] = record

        def finalize():
            try:
                self._wait_for_ffmpeg_exit(process, stop_requested=stop_requested)
                elapsed_ms = (time.monotonic() - started_at) * 1000
                logging.info(
                    f"Old ffmpeg finalized for room {self.room_id}: "
                    f"pid={getattr(process, 'pid', '?')} elapsed={elapsed_ms:.0f}ms "
                    f"file={os.path.basename(save_path) if save_path else ''}"
                )
                if save_path and os.path.exists(save_path):
                    self._submit_closed_source(
                        save_path,
                        close_reason=close_reason,
                        session_id=session_id,
                    )
            except Exception as e:
                logging.error(f"Old ffmpeg finalization failed for room {self.room_id}: {e}")
                self.cut_failed.emit(self.room_id, str(e))
            finally:
                with self._retiring_ffmpeg_lock:
                    self._retiring_ffmpegs.pop(key, None)

        thread = threading.Thread(
            target=finalize,
            name=f"ffmpeg-finalize-{self.room_id}-{getattr(process, 'pid', key)}",
            daemon=True,
        )
        record["thread"] = thread
        try:
            thread.start()
        except Exception:
            # Thread creation failure is rare, but the old process has already received q.
            # Finalize synchronously rather than leaking the process or losing the artifact.
            finalize()

    def _drain_retiring_ffmpegs(self, timeout: float = 30.0):
        """Wait for old ffmpeg finalizers before exit; force-reap them after timeout."""
        lock = getattr(self, "_retiring_ffmpeg_lock", None)
        records_map = getattr(self, "_retiring_ffmpegs", None)
        if lock is None or records_map is None:
            return

        deadline = time.monotonic() + timeout
        with lock:
            records = list(records_map.values())
        for record in records:
            thread = record.get("thread")
            if not thread or thread is threading.current_thread():
                continue
            remaining = max(0.0, deadline - time.monotonic())
            thread.join(remaining)

        with lock:
            unfinished = list(records_map.values())
        if not unfinished:
            return

        logging.warning(
            f"Room {self.room_id} has {len(unfinished)} old ffmpeg finalizer(s) timed out; force reaping"
        )
        for record in unfinished:
            process = record.get("process")
            try:
                if process and process.poll() is None:
                    process.kill()
            except Exception:
                pass
        for record in unfinished:
            thread = record.get("thread")
            if thread and thread is not threading.current_thread():
                thread.join(2.0)

    def stop_recording(self, reset_time=False):
        was_recording = self.is_recording
        path_to_convert = self.current_save_path if was_recording else None
        capture_mode = self._capture_mode

        logging.info(
            f"🛑 stop_recording begin room={self.room_id} mode={capture_mode} "
            f"recording={was_recording} reset={reset_time}"
        )

        if self._split_timer:
            self._split_timer.cancel()
            self._split_timer = None

        # 先停弹幕，再停拉流，降低 SSL/socket 并发 close 窗口
        if self._danmaku_recorder:
            try:
                self._danmaku_recorder.stop()
            except Exception:
                logging.exception(f"停止弹幕失败: {self.room_id}")
            self._danmaku_recorder = None

        if capture_mode in ("flv", "flv_bridge"):
            self._stop_flv_capture(submit=True)
        elif self.current_ffmpeg:
            self._graceful_stop_ffmpeg(self.current_ffmpeg)
            # ffmpeg 路径：关闭后提交验证
            if path_to_convert and os.path.exists(path_to_convert):
                self._submit_closed_source(path_to_convert, close_reason="stop")

        self.current_ffmpeg = None
        self._capture_mode = "none"
        # 关键：记下"停止瞬间"的时间戳和 part 大小（如果还在写 part），让防抖到期
        # 发 FileClosed 时能算出"停止那一刻"的 duration/size，而不是防抖期内虚涨的值。
        if was_recording and self.current_save_path:
            self._last_part_stop_time = time.time()
            try:
                p = self.current_save_path
                if not os.path.exists(p) and os.path.exists(os.path.splitext(p)[0] + ".capture.flv"):
                    p = os.path.splitext(self.current_save_path)[0] + ".capture.flv"
                self._last_part_size_at_stop = os.path.getsize(p) if os.path.exists(p) else 0
            except OSError:
                self._last_part_size_at_stop = 0
        # 关键：不立即清 current_save_path / current_session_id —— 防抖到期还需要
        # 拿这两个值发 FileClosed + RecordingFinishedEvent；只有 reset_time=True
        # 时（即"真正下播 / 用户主动关停监控"）才彻底清空。
        if reset_time:
            self.current_save_path = None
            self.stream_url = None
            self.record_start_time = 0
            self.current_part_start_time = 0
            self.last_total_size = 0
            self.accumulated_size = 0
            # 会话结束，清空 SessionId 让下次重新生成
            self.current_session_id = ""
            self._last_part_stop_time = 0.0
            self._last_part_size_at_stop = 0

        if was_recording:
            logging.info(f"⏹️ 已停止录制: {self.room_id}")
            # 旧的同步 _run_convert 已由 media_pipeline 替代，不再阻塞 stop 路径
    def kill(self):
        # 关停前如果有未完结的录制，把"录制结束"事件发出去
        # （对端不依赖这个事件投稿；但少了它状态机会少一次"会话收尾"）
        if self.is_recording:
            try:
                # 先补最后一分P 的 FileClosed（part 入库入口），再发 RecordingFinishedEvent（收尾）
                _emit_last_part_file_closed_webhook(self)
                _emit_recording_stopped_webhook(self)
            except Exception as e:
                logging.debug(f"kill: 发下播 webhook 失败（不致命）: {e}")
        self.is_running = False
        self.stop_recording()
        # Finalizer threads are daemons; drain them before process exit.
        self._drain_retiring_ffmpegs()
        self._emit_finished_once()

    def _schedule_duration_split(self):
        duration_str = get_room_setting(self.room_id, "split_by_duration") or ""
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
        size_str = get_room_setting(self.room_id, "split_by_size") or ""
        if not size_str:
            return False
        try:
            size_str = size_str.strip()
            # 支持纯字节数字（新格式）和带单位字符串（旧格式兼容）
            upper = size_str.upper()
            if size_str.isdigit():
                limit = int(size_str)
            elif upper.endswith("GB"):
                limit = float(upper[:-2]) * 1024 ** 3
            elif upper.endswith("MB"):
                limit = float(upper[:-2]) * 1024 ** 2
            elif upper.endswith("KB"):
                limit = float(upper[:-2]) * 1024
            else:
                return False

            if limit <= 0:
                return False

            if self.current_save_path and os.path.exists(self.current_save_path):
                current_size = self.accumulated_size + os.path.getsize(self.current_save_path)
                if current_size >= limit:
                    logging.info(f"📦 {self.room_id} 文件大小达到 {size_str} 字节，触发切割")
                    return True
        except Exception:
            pass
        return False

    def _get_proxy_env(self):
        """根据代理设置返回环境变量，供 ffmpeg 使用"""
        mode = (get_room_setting(self.room_id, "proxy_mode") or "禁用").strip()
        if mode == "禁用":
            # 明确禁用代理，清除环境变量继承
            env = os.environ.copy()
            for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
                env.pop(k, None)
            return env
        if mode == "系统":
            # 继承系统环境变量，不做修改
            return None
        if mode == "自定义":
            proxy_url = (get_room_setting(self.room_id, "proxy") or "").strip()
            if proxy_url:
                env = os.environ.copy()
                env["http_proxy"] = proxy_url
                env["https_proxy"] = proxy_url
                env["HTTP_PROXY"] = proxy_url
                env["HTTPS_PROXY"] = proxy_url
                return env
        return None

    def _start_ffmpeg(self, save_path):
        """启动 ffmpeg 进程，stderr 写入 sidecar。"""
        log_path = save_path + ".ffmpeg.log"
        self._ffmpeg_stderr_path = log_path
        try:
            stderr_f = open(log_path, "w", encoding="utf-8", errors="replace")
        except OSError:
            stderr_f = subprocess.DEVNULL
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
            # 关键：+empty_moov 让 ffmpeg 写一个**空** moov atom 在文件开头
            "-movflags", "+empty_moov",
            save_path
        ]  # noqa
        proxy_env = self._get_proxy_env()
        _popen_kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=stderr_f,
            env=proxy_env,
        )
        _popen_kwargs.update(hidden_subprocess_kwargs(new_process_group=True))
        proc = subprocess.Popen(cmd, **_popen_kwargs)
        self._capture_mode = "ffmpeg"
        return proc

    def trigger_cut(self):
        if not self.is_recording or not self.stream_url:
            return
        if self._cutting:
            logging.info(f"✂️ {self.room_id} 已有切割进行中，忽略重复请求")
            return
        import threading
        cut_thread = threading.Thread(target=self._do_cut, daemon=True)
        cut_thread.start()

    def _do_cut(self):
        if not self._cut_lock.acquire(blocking=False):
            logging.info(f"✂️ {self.room_id} 切割锁占用，跳过")
            return
        self._cutting = True
        try:
            logging.info(f"✂️ {self.room_id} 正在切割...")
            now_dt = datetime.datetime.now()
            save_path = _build_save_path(self.room_id, self.uname, self.current_title, now_dt)

            if self._capture_mode in ("flv", "flv_bridge") and self._flv_session:
                # Single reader: rotate the writer at the next IDR; never reopen the network input.
                next_capture_path = self._capture_path_for_save(save_path)
                accepted = self._flv_session.request_cut(
                    reason="manual_or_timer",
                    next_path=next_capture_path,
                )
                if not accepted:
                    logging.info(f"Room {self.room_id} already has a pending cut or is waiting for the first keyframe")
                    return
                # 切段完成后 session 会 open 新文件；更新弹幕
                if self._danmaku_recorder:
                    fname_base = os.path.splitext(os.path.basename(save_path))[0]
                    self._danmaku_recorder.reset(fname_base)
                self._schedule_duration_split()
                logging.info(f"✅ {self.room_id} FLV 切割请求已提交")
                # FileClosed 由 pipeline 在验证后发送
                return

            # Snapshot the old part before opening the new connection. This leaves only
            # the milliseconds between Popen returning and stdin q as a dual-record window.
            old_ffmpeg = self.current_ffmpeg
            old_save_path = self.current_save_path
            old_session_id = self.current_session_id
            if old_save_path and os.path.exists(old_save_path):
                self.accumulated_size += os.path.getsize(old_save_path)

            new_ffmpeg = self._start_ffmpeg(save_path)
            if new_ffmpeg.poll() is not None:
                raise RuntimeError("ffmpeg failed to start")

            # Publish the new active recorder first, then immediately stop old input.
            # Trailer/moov completion happens in a background finalizer.
            self.current_ffmpeg = new_ffmpeg
            self.current_save_path = save_path
            self.current_part_start_time = time.time()
            if old_ffmpeg:
                self._retire_ffmpeg_async(
                    old_ffmpeg,
                    old_save_path,
                    old_session_id,
                    close_reason="cut",
                )

            if self._danmaku_recorder:
                fname_base = os.path.splitext(os.path.basename(save_path))[0]
                self._danmaku_recorder.reset(fname_base)

            logging.info(f"Cut completed for room {self.room_id}: {os.path.basename(save_path)}")

            self._schedule_duration_split()
            # FileClosed is emitted only after old ffmpeg exits and pipeline verification succeeds.
        except Exception as e:
            logging.error(f"❌ {self.room_id} 切割失败: {e}")
            self.cut_failed.emit(self.room_id, str(e))
        finally:
            self._cutting = False
            self._cut_lock.release()

    def _check_record_conditions(self, info):
        title_cond = get_room_setting(self.room_id, "condition_title") or ""
        if title_cond.strip():
            keywords = [k.strip() for k in title_cond.split(",") if k.strip()]
            if keywords and not any(k in info.get("title", "") for k in keywords):
                return False

        cat_cond = get_room_setting(self.room_id, "condition_category") or ""
        if cat_cond.strip():
            keywords = [k.strip() for k in cat_cond.split(",") if k.strip()]
            area = info.get("area_name", "") + info.get("parent_area_name", "")
            if keywords and not any(k in area for k in keywords):
                return False


        # ---- Recording schedule: time-window check ----
        tz_name = (get_room_setting(self.room_id, "schedule_timezone") or "UTC").strip()
        start_str = (get_room_setting(self.room_id, "schedule_start") or "").strip()
        stop_str = (get_room_setting(self.room_id, "schedule_stop") or "").strip()
        if start_str or stop_str:
            try:
                import zoneinfo
                tz = zoneinfo.ZoneInfo(tz_name)
            except Exception:
                try:
                    import pytz
                    tz = pytz.timezone(tz_name)
                except Exception:
                    tz = datetime.timezone.utc
            now_local = datetime.datetime.now(tz)
            now_t = now_local.time().replace(second=0, microsecond=0)

            def _parse_t(s):
                p = s.split(':')
                return datetime.time(int(p[0]), int(p[1]))

            try:
                if start_str and stop_str:
                    t_start = _parse_t(start_str)
                    t_stop = _parse_t(stop_str)
                    if t_start <= t_stop:
                        # Same-day window e.g. 08:00-23:00
                        if not (t_start <= now_t < t_stop):
                            return False
                    else:
                        # Cross-midnight window e.g. 22:00-06:00
                        if not (now_t >= t_start or now_t < t_stop):
                            return False
                elif start_str:
                    if now_t < _parse_t(start_str):
                        return False
                elif stop_str:
                    if now_t >= _parse_t(stop_str):
                        return False
            except Exception as e:
                logging.warning(f"录制计划时间解析失败: {e}")
        return True

    def run(self):
        try:
            if not get_room_setting(self.room_id, "stream_record_enabled"):
                self.status_updated.emit("⚙️ 已停用", "📡 未开播", "⏳ 闲置中",
                                        self.current_title, "", "", "",
                                        self.current_parent_area, self.current_area)
                return

            try:
                self._run_loop()
            except Exception as e:
                logging.exception(f"❌ {self.room_id} run() 主循环异常: {e}")
                self.status_updated.emit("❌ 出错了", "💥 主循环崩溃", str(e)[:30],
                                        self.current_title, "", "", "",
                                        self.current_parent_area, self.current_area)
        finally:
            if self._async_stop_requested:
                try:
                    self.request_stop(reset_time=self._async_stop_reset_time)
                except Exception:
                    logging.exception(f"异步停止录播收尾失败: {self.room_id}")
            try:
                # 先停 capture 后可能还有 retiring ffmpeg / pipeline job
                self._drain_retiring_ffmpegs()
            except Exception:
                logging.exception(f"[recorder {self.room_id}] drain retiring ffmpeg 失败")
            try:
                self._emit_finished_once()
            except Exception:
                logging.exception(f"[recorder {self.room_id}] finished 信号发送失败")

    def _run_loop(self):
        while self.is_running:
            # 先处理 pipeline 完成事件，保证 emit 始终发生在 recorder 线程
            try:
                self._drain_artifact_results()
            except Exception:
                logging.exception(f"[recorder {self.room_id}] drain artifact results 失败")

            now_time = time.time()

            if not self.is_monitoring:
                if self.is_recording or (self.current_save_path and self.current_session_id):
                    # 用户关停监控时，把当前录制当成一次完整会话上报。
                    # 必须在 stop_recording 之前发，否则 record_start_time 被清零、duration 算不出。
                    # 同时补发最后一分P 的 FileClosed，否则最后一个 part 不会上传。
                    # 注意：也要覆盖"防抖期内"的情况（is_recording=False 但 current_save_path 还在）——
                    # 用户关停监控 = 主动结束录制，立刻发结束事件 + stop_recording(True) 清空。
                    _emit_last_part_file_closed_webhook(self)
                    _emit_recording_stopped_webhook(self)
                    _send_notify("recording_stopped", self.room_id, self.uname, self.current_title, {"cover": self.current_cover, "face": self.current_face})
                    # 无论在录 / 防抖期内，都 reset_time=True 清空所有状态
                    self.stop_recording(reset_time=True)
                # 注意：未录制时不再发 live_ended —— 主循环每 2 秒轮询一次会产生风暴，
                # 而 biliupforjava 自己有 RoomStatusSyncJob 兜底同步房间状态，不需要我们推。
                self.status_updated.emit("⚙️ 已暂停", "🌙 未开播", "⏳ 闲置中",
                                        self.current_title, "", "", "",
                                        self.current_parent_area, self.current_area)
                time.sleep(2)
                continue

            if self.is_recording:
                duration_str = "00:00:00"
                size_str = "0 B"
                speed_str = "0 B/s"

                if self.record_start_time and self.current_save_path and os.path.exists(self.current_save_path):
                    delta_sec = int(now_time - self.record_start_time)
                    duration_str = str(datetime.timedelta(seconds=delta_sec))

                    current_file_size = os.path.getsize(self.current_save_path)
                    total_size = self.accumulated_size + current_file_size
                    size_str = format_size(total_size)

                    if self.last_check_time:
                        time_diff = now_time - self.last_check_time
                        if time_diff > 0:
                            speed_bytes = (total_size - self.last_total_size) / time_diff
                            speed_bytes = max(0, speed_bytes)
                            speed_str = f"{format_size(speed_bytes)}/s"

                    self.last_total_size = total_size
                    self.last_check_time = now_time

                    if self._check_size_split():
                        threading.Thread(target=self.trigger_cut, daemon=True).start()

                    if getattr(self, "last_auto_switch_check", 0) == 0:
                        self.last_auto_switch_check = now_time

                    if get_room_setting(self.room_id, "auto_switch_stream") and (now_time - self.last_auto_switch_check > 180):
                        new_info = get_stream_info(self.room_id)
                        if new_info and getattr(self, "current_stream_base_url", None):
                            if new_info["base_url"] != self.current_stream_base_url:
                                logging.info(f"🔄 {self.room_id} 发现更优的流线路或编码，正在无缝切换...")
                                self.stream_url = new_info["url"]
                                self.current_stream_base_url = new_info["base_url"]
                                threading.Thread(target=self.trigger_cut, daemon=True).start()
                        self.last_auto_switch_check = now_time

                    if now_time - self.last_api_check_time > 30:
                        info = get_bili_info(self.room_id, room_id_for_cookie=self.room_id, silent=True)
                        if info:
                            new_title = info["title"]
                            new_parent = info["parent_area_name"]
                            new_area = info["area_name"]

                            if get_room_setting(self.room_id, "split_on_title_change") and new_title != self.current_title:
                                logging.info(f"📝 {self.room_id} 标题改变，触发切割")
                                threading.Thread(target=self.trigger_cut, daemon=True).start()

                            if get_room_setting(self.room_id, "split_on_category_change") and new_area != self.current_area:
                                logging.info(f"🏷️ {self.room_id} 分区改变，触发切割")
                                threading.Thread(target=self.trigger_cut, daemon=True).start()

                            self.current_title = new_title
                            self.current_parent_area = new_parent
                            self.current_area = new_area

                            # 关键：检测到主播下播（live_status != 1）→ 主动 stop_recording(False)
                            # 杀 ffmpeg、停弹幕，**不**清 part 信息。continue 跳出 874 块
                            # 让主循环下次轮询进 else 分支走防抖流程。
                            # 否则主循环会一直在 is_recording 块跑（只检查标题/分区/编码），
                            # 永远走不到"下播"路径。
                            if info.get("live_status") != 1:
                                logging.info(f"🔍 {self.room_id} API 返回下播（live_status={info.get('live_status')}），主动停录进入防抖")
                                if self.is_recording:
                                    self.stop_recording(reset_time=False)
                                self.last_api_check_time = now_time
                                self.status_updated.emit("⚠️ 监控中", "🌙 未开播", "⏳ 等待防抖",
                                                        self.current_title, "", "", "",
                                                        self.current_parent_area, self.current_area)
                                time.sleep(2)
                                continue

                        # 检测编码改变（需要置 stream_info）
                        if get_room_setting(self.room_id, "split_on_codec_change"):
                            new_info = get_stream_info(self.room_id)
                            if new_info:
                                new_codec = new_info.get("codec", "")
                                if self._last_codec and new_codec and new_codec != self._last_codec:
                                    logging.info(f"🎞️ {self.room_id} 编码从 {self._last_codec} 变为 {new_codec}，触发切割")
                                    self._last_codec = new_codec
                                    threading.Thread(target=self.trigger_cut, daemon=True).start()

                        self.last_api_check_time = now_time

                # 检测流不连续：ffmpeg / FLV 源意外退出。
                # 调 stop_recording(reset_time=False)：保留 current_save_path / current_session_id
                # 给后续防抖路径（或 API 翻回 1 重启路径）使用。
                capture_dead = False
                if self._capture_mode == "ffmpeg" and self.current_ffmpeg and self.current_ffmpeg.poll() is not None:
                    capture_dead = True
                    logging.warning(f"⚠️ {self.room_id} ffmpeg 进程已退出（可能 B 站流断开）")
                elif self._capture_mode in ("flv", "flv_bridge") and self._stream_source and not self._stream_source.is_running:
                    capture_dead = True
                    logging.warning(f"⚠️ {self.room_id} FLV 源已断开")
                if capture_dead:
                    # 关键：用 reset_time=False 保留 part 信息；始终关闭当前 artifact 边界
                    self.stop_recording(reset_time=False)
                    if get_room_setting(self.room_id, "split_on_stream_discontinuity"):
                        self.status_updated.emit("⚠️ 监控中", "📡 直播中", "⏸️ 录制中断（流不连续）",
                                                self.current_title, "", "", "",
                                                self.current_parent_area, self.current_area)
                    else:
                        self.status_updated.emit("⚠️ 监控中", "📡 直播中", "⏸️ 录制中断",
                                                self.current_title, "", "", "",
                                                self.current_parent_area, self.current_area)
                    time.sleep(2)
                    continue

                self.status_updated.emit("🟢 监控中", "📡 直播中", "🔴 录制中",
                                        self.current_title, duration_str, speed_str, size_str,
                                        self.current_parent_area, self.current_area)
                time.sleep(2)
                continue

            info = get_bili_info(self.room_id, room_id_for_cookie=self.room_id, silent=True)
            if not info:
                self.status_updated.emit("❌ 出错了", "⚠️ 获取失败", "⏳ 闲置中",
                                        self.current_title, "", "", "",
                                        self.current_parent_area, self.current_area)
                time.sleep(5)
                continue

            self.uname = info["uname"]
            self.current_title = info["title"]
            self.current_parent_area = info["parent_area_name"]
            self.current_area = info["area_name"]

            self.current_cover = info.get("cover", "")
            self.current_face = info.get("face", "")
            if not self._check_record_conditions(info):
                self.status_updated.emit("🟢 监控中", "📡 直播中", "⏸️ 条件过滤",
                                        self.current_title, "", "", "",
                                        self.current_parent_area, self.current_area)
                time.sleep(_parse_monitor_seconds("monitor_interval", 20, self.room_id))
                continue

            if info["live_status"] == 1:
                # 主播在播 → 重置下播防抖（避免下播事件残留）
                self._live_first_seen = 0.0
                # 检查全局录制开关
                if get_room_setting(self.room_id, "stream_record_enabled") is False:
                    self.status_updated.emit("🟢 监控中", "📡 直播中", "⏸️ 录制已停用",
                                            self.current_title, "", "", "",
                                            self.current_parent_area, self.current_area)
                    time.sleep(10)
                    continue

                # 已在录制则不要重复进入启动流程，否则会反复重建 ffmpeg 与弹幕连接，
                # 同一房间出现两个弹幕连接会被 B站互相踢掉（recv 返回空）
                if self.is_recording:
                    time.sleep(2)
                    continue

                # 关键：防抖期内 API 翻回 1（短暂断流后重新开播）→ 主循环进到这里。
                # 1. 先补发旧 part 的 FileClosed（让 biliupforjava 把旧 part 入队）
                # 2. 清 current_ffmpeg / 弹幕引用
                # 3. 然后正常启 ffmpeg 写新 part，session_id 延续
                # 关键：判定条件 _last_part_stop_time != 0 而不是 _live_first_seen == 0，
                # 避免与 trigger_cut 路径冲突（trigger_cut 走 _do_cut 内部发 FileClosed）。
                if self.current_save_path and self.current_session_id and self._last_part_stop_time:
                    logging.info(f"🔄 {self.room_id} 防抖期内 API 翻回 1，补发旧 part FileClosed 后重启录制")
                    _emit_last_part_file_closed_webhook(self)
                    # 不调 stop_recording —— _last_part_stop_time 已经是首检下播时记下的，
                    # _emit_last_part_file_closed_webhook 会用它算旧 part 的 duration/size。
                    # 只需要清 current_ffmpeg / 弹幕引用，让下面启新 ffmpeg 走通。
                    if self._danmaku_recorder:
                        self._danmaku_recorder.stop()
                        self._danmaku_recorder = None
                    self.current_ffmpeg = None
                    # 清掉 _last_part_* 让下一次防抖首检能正确触发
                    self._last_part_stop_time = 0.0
                    self._last_part_size_at_stop = 0

                stream_info = get_stream_info(self.room_id)
                if stream_info:
                    self.stream_url = stream_info["url"]
                    self.current_stream_base_url = stream_info["base_url"]
                    self._last_codec = stream_info.get("codec", "")  # 记录当前编码
                    self._last_format = stream_info.get("format", "")

                    now_dt = datetime.datetime.now()
                    save_path = _build_save_path(self.room_id, self.uname, self.current_title, now_dt)

                    logging.info(
                        f"🎥 开始录制: {os.path.basename(save_path)} "
                        f"codec={stream_info.get('codec')} format={stream_info.get('format')} "
                        f"match={stream_info.get('match_kind')}"
                    )
                    if self._use_native_flv(stream_info):
                        self._start_flv_capture(save_path, self.stream_url, bridge=False)
                        self.current_ffmpeg = None
                    elif self._use_ffmpeg_flv_bridge(stream_info):
                        try:
                            self._start_flv_capture(save_path, self.stream_url, bridge=True)
                            self.current_ffmpeg = None
                        except Exception as bridge_error:
                            logging.error(
                                f"Single-input FFmpeg FLV bridge failed for room {self.room_id}; "
                                f"falling back to direct FFmpeg recording: {bridge_error}"
                            )
                            self.current_ffmpeg = self._start_ffmpeg(save_path)
                            self.current_save_path = save_path
                    else:
                        self.current_ffmpeg = self._start_ffmpeg(save_path)
                        self.current_save_path = save_path

                    if self.record_start_time == 0:
                        self.record_start_time = time.time()
                        self.last_total_size = 0
                        self.accumulated_size = 0
                        self.current_part_start_time = time.time()
                        # 为本次录制会话生成一个固定 SessionId
                        # —— 后续 split / stopped 都用同一个，保证 biliupforjava 端 session 一致
                        self.current_session_id = str(uuid.uuid4())
                    else:
                        # 防抖重连路径：主播曾短暂断流，现在重新开播。
                        # 必须重置计时器和计数，让新录制 duration 从 0 开始；
                        # 生成新 session_id（旧 part 的 FileClosed 已由上面发出）。
                        self.record_start_time = time.time()
                        self.last_total_size = 0
                        self.accumulated_size = 0
                        self.current_part_start_time = time.time()
                        self.current_session_id = str(uuid.uuid4())

                    # 启动弹幕录制
                    if get_room_setting(self.room_id, "chat_record_enabled"):
                        fname_base = os.path.splitext(os.path.basename(save_path))[0]
                        sessdata = get_room_setting(self.room_id, "chat_credential") or ""
                        chat_fmt = get_room_setting(self.room_id, "chat_format") or "xml"
                        save_dir = os.path.dirname(save_path)
                        if self._danmaku_recorder:
                            self._danmaku_recorder.stop()
                        self._danmaku_recorder = DanmakuRecorder(self.room_id, save_dir, fname_base)
                        self._danmaku_recorder.start(chat_fmt, sessdata)

                    self.last_check_time = time.time()
                    self.last_api_check_time = time.time()

                    self._schedule_duration_split()

                    _send_webhooks("recording_started", self.room_id, self.uname, self.current_title,
                                   {"file": save_path,
                                    "file_open_time": datetime.datetime.now().astimezone().isoformat(timespec="microseconds"),
                                    "session_id": self.current_session_id})

                    _send_notify("recording_started", self.room_id, self.uname, self.current_title, {"cover": info.get("cover", ""), "face": info.get("face", "")})

                    # 下载直播封面
                    if get_room_setting(self.room_id, "download_cover"):
                        _cover_url = info.get("cover", "")
                        _cover_dir = os.path.dirname(save_path)
                        _cover_base = os.path.splitext(os.path.basename(save_path))[0]
                        _download_cover(_cover_url, _cover_dir, _cover_base)
                    self.status_updated.emit("🟢 监控中", "📡 直播中", "🔴 录制中",
                                            self.current_title, "00:00:00", "0 B/s", "0 B",
                                            self.current_parent_area, self.current_area)
                    time.sleep(_parse_monitor_seconds("monitor_delay", 3, self.room_id))
                else:
                    self.status_updated.emit("🟢 监控中", "📡 直播中", "❌ 出错了",
                                            self.current_title, "", "", "",
                                            self.current_parent_area, self.current_area)
                    _send_notify("error", self.room_id, self.uname, self.current_title, {"cover": self.current_cover, "face": self.current_face})
                    time.sleep(5)
            else:
                self.status_updated.emit("🟢 监控中", "🌙 未开播", "⏳ 闲置中",
                                        self.current_title, "", "", "",
                                        self.current_parent_area, self.current_area)
                # 主播下播流程：先 stop_recording（杀 ffmpeg / 停弹幕 / 转码），
                # 然后进入防抖期，5 分钟内 API 翻回 1 算"短暂断流"，重置防抖重启 ffmpeg；
                # 5 分钟到才发 FileClosed + RecordingFinishedEvent + 通知。
                if self.current_save_path and self.current_session_id:
                    _debounce_sec = _parse_monitor_seconds("monitor_debounce", 300, self.room_id)
                    now_ts = time.time()
                    if not self._live_first_seen:
                        # 首检：先关停录播，再开始防抖
                        self._live_first_seen = now_ts
                        logging.info(f"⏳ {self.room_id} 检测到主播下播，停止录制后等待 {int(_debounce_sec)}s 确认...")
                        if self.is_recording:
                            # reset_time=False：保留 current_save_path / current_session_id
                            # 给防抖到期发 FileClosed 用
                            self.stop_recording(reset_time=False)
                        # 立即发信号让卡片 fade_out 统计信息
                        self.status_updated.emit("🟢 监控中", "🌙 未开播", "⏳ 确认中",
                                                self.current_title, "", "", "",
                                                self.current_parent_area, self.current_area)
                        time.sleep(_parse_monitor_seconds("monitor_interval", 20, self.room_id))
                        continue
                    elif now_ts - self._live_first_seen >= _debounce_sec:
                        # 防抖窗口过了 → 真正下播
                        self._live_first_seen = 0.0
                        # 重要：必须先发最后一个分P的 FileClosed 事件，再发 RecordingFinishedEvent。
                        # 否则 biliupforjava 的 FileClosed 是 part 入队上传的唯一入口，
                        # 缺它最后一分P 不会入库、不会上传。
                        _emit_last_part_file_closed_webhook(self)
                        _emit_recording_stopped_webhook(self)
                        _send_notify("recording_stopped", self.room_id, self.uname, self.current_title, {"cover": self.current_cover, "face": self.current_face})
                        # reset_time=True：彻底清空状态（current_save_path / session_id 等）
                        self.stop_recording(reset_time=True)
                        time.sleep(_parse_monitor_seconds("monitor_interval", 20, self.room_id))
                        continue
                    else:
                        # 还在防抖窗口内，等下一轮轮询
                        self.status_updated.emit("🟢 监控中", "🌙 未开播", f"⏳ 确认中({int((_debounce_sec - (now_ts - self._live_first_seen)))}s)",
                                                self.current_title, "", "", "",
                                                self.current_parent_area, self.current_area)
                        time.sleep(_parse_monitor_seconds("monitor_interval", 20, self.room_id))
                        continue
                # 兜底：current_save_path 为空 + API 下播（用户主动关停监控后会到这里，
                # 或主播下播前还没开始录）。直接清理状态，不发任何事件。
                if self.is_recording:
                    self.stop_recording(reset_time=True)
                    # 发信号让卡片 fade_out 统计信息
                    self.status_updated.emit("🟢 监控中", "🌙 未开播", "⏳ 闲置中",
                                            self.current_title, "", "", "",
                                            self.current_parent_area, self.current_area)
                self._live_first_seen = 0.0   # 下播，重置 debounce
                time.sleep(_parse_monitor_seconds("monitor_interval", 20, self.room_id))

        self.current_cover = self.room_info.get("cover", "")
        self.current_face = self.room_info.get("face", "")