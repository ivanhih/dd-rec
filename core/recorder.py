# core/recorder.py
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
    get_global_setting, get_room_config, get_effective_format,
    get_effective_save_dir, VIDEO_SAVE_DIR
)
from core.bili_api import get_bili_info, get_stream_info
from core.utils import format_size, render_path_template
from core.danmaku_recorder import DanmakuRecorder

if platform.system() == "Windows":
    LOCAL_FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ffmpeg.exe")
else:
    LOCAL_FFMPEG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "ffmpeg")

FFMPEG_CMD = LOCAL_FFMPEG if os.path.exists(LOCAL_FFMPEG) else "ffmpeg"


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


def _send_webhooks(event: str, room_id: str, uname: str, title: str, extra: dict = None):
    """把录播事件推送到 biliupforjava 的 /recordWebHook 端点。

    payload 严格按 FQrabbit/biliupforjava 的 RecordEventDTO 字段命名：
      - 顶层 PascalCase（EventType / EventTimestamp / EventId / EventData）
      - EventData 内也是 PascalCase（RoomId / Name / Title / RelativePath / FileSize / Duration ...）
      - 时间字段为 ISO8601 带时区
    """
    raw = (get_global_setting("webhooks") or "").strip()
    if not raw:
        return
    urls = [u.strip() for u in raw.splitlines() if u.strip()]
    if not urls:
        return

    fmt = (get_global_setting("webhook_format") or "blrec").strip()

    # biliupforjava 会用 work-path + relativePath 拼绝对路径找 part record。
    # relativePath 必须是**相对 work-path 的相对路径**。
    _work_path = (get_global_setting("webhook_work_path") or "").strip() \
                 or (get_global_setting("save_dir") or "").strip()
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
                with urllib.request.urlopen(req, timeout=10) as resp:
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
    # 关键：同步重编码修 A/V 同步（-vsync cfr 让视频从 0 开始），让 webhook 用**重编码后**路径
    _upload_path = _stop_path
    if False:  # 取消重编码 —— mp4 容器已由 SIGINT + 10s 等 trailer 写完整
        pass  # 占位
    _send_webhooks("recording_split", recorder.room_id, recorder.uname, recorder.current_title or "",
                   {"file": _upload_path,
                    "size": _stop_size,
                    "duration": _stop_duration,
                    "file_open_time": (datetime.datetime.fromtimestamp(recorder.current_part_start_time or recorder.record_start_time).astimezone().isoformat(timespec="microseconds")
                                       if (recorder.current_part_start_time or recorder.record_start_time) else _file_close_time),
                    "file_close_time": _file_close_time,
                    "session_id": recorder.current_session_id})


def _send_notify(event: str, room_id: str, uname: str, title: str, extra: dict = None):
    """使用 apprise 发送推送通知。event: recording_started / recording_stopped / error"""
    if not get_global_setting("notify_enabled"):
        return
    if event == "recording_stopped" and not get_global_setting("notify_on_live_end"):
        return
    if event == "error" and not get_global_setting("notify_on_error"):
        return

    notify_url = (get_global_setting("notify_url") or "").strip()
    if not notify_url:
        notify_url = (get_global_setting("webhooks") or "").strip()
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

    title_tpl_raw = (get_global_setting("notify_title_template") or "").strip()
    body_tpl_raw = (get_global_setting("notify_body_template") or "").strip()

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
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        logging.debug(f"Notify {url} -> {resp.status}")
                except Exception as e2:
                    logging.warning(f"\u901a\u77e5\u53d1\u9001\u5931\u8d25 {url}: {e2}")
        except Exception as e:
            logging.warning(f"\u901a\u77e5\u53d1\u9001\u5931\u8d25: {e}")
    threading.Thread(target=_fire, daemon=True).start()
def _run_convert(src_path: str):
    """录制结束后将源文件转换为目标格式，可选删除原文件。在后台线程中运行。

    注意：如果要用 webhook 投稿到 biliupforjava，**必须**录制 h264 编码的流（设置
    config.json 的 stream_codec = "h264"），不要让这个函数转码 —— B 站 web 投稿 bvc_check
    对 av1 编码不稳定。
    """
    if not get_global_setting("convert_enabled"):
        return
    if not src_path or not os.path.exists(src_path):
        return

    target_fmt = (get_global_setting("convert_format") or "mp4").strip()
    base, src_ext = os.path.splitext(src_path)
    # 注意：不要"同格式跳过"——因为 B 站直播流（h264/av1）的 mp4 容器 PTS 不规范，
    # 必须**重编码**才能修。不重编码 bvc_check 会拒收（code=21588）。
    dst_path = f"{base}.{target_fmt}"
    # B 站 web 投稿 bvc_check 会拒收 A/V PTS 错位的 mp4（B 站直播流视频 start=0.269s
    # 但音频 start=0.022s，-c copy 写出的文件保流错位）。必须**重编码视频流**才能修。
    # 用 libx264 veryfast 模式 + crf 23 质量够用且速度快。音频直通。
    cmd = [
        FFMPEG_CMD, "-y", "-i", src_path,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-vsync", "cfr", "-async", "1",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        dst_path,
    ]
    logging.info(f"🔄 开始转换（重编码修 A/V 同步）: {os.path.basename(src_path)} -> {os.path.basename(dst_path)}")

    # 关键：**同步**执行 ffmpeg 转码，不开后台线程。
    # 因为 webhook 必须等转码完成、发的是**重编码后**的路径（B 站 bvc_check 才能通过）。
    try:
        result = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=600,  # 10 分钟超时
        )
        if result.returncode == 0:
            logging.info(f"✅ 转换完成: {os.path.basename(dst_path)}")
            if get_global_setting("convert_delete_source"):
                try:
                    os.remove(src_path)
                    logging.info(f"🗑️ 已删除原文件: {os.path.basename(src_path)}")
                except Exception as e:
                    logging.warning(f"删除原文件失败: {e}")
        else:
            logging.error(f"❌ 转换失败 (returncode={result.returncode}): {os.path.basename(src_path)}")
    except Exception as e:
        logging.error(f"❌ 转换异常: {e}")


def _parse_monitor_seconds(key: str, default: float) -> float:
    """将 monitor_* 设置的文字值解析为秒数，无法解析则返回 default。"""
    raw = (get_global_setting(key) or "").strip()
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
            with _req.urlopen(req, timeout=15) as resp:
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
        self.room_id = str(room_info["room_id"])
        self.uname = room_info.get("uname", f"房间_{self.room_id}")
        self.current_title = room_info.get("title", "未知标题")
        self.current_parent_area = room_info.get("parent_area_name", "未知分区")
        self.current_area = room_info.get("area_name", "未知内容")

        self.is_running = True
        self.is_monitoring = room_info.get("enabled", True)
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
        self._ffmpeg_exit_count = 0
        self._danmaku_recorder: Optional[DanmakuRecorder] = None
        # 防抖期内关停录播后，记下"停止瞬间"的时间戳和 part 大小，
        # 让防抖到期发 FileClosed 时能算出"停止瞬间那一刻的 duration/size"，
        # 而不是"防抖结束后 5 分钟 + 实际 part 时长"的虚高值。
        self._last_part_stop_time = 0.0
        self._last_part_size_at_stop = 0

    @property
    def is_recording(self):
        return self.current_ffmpeg is not None and self.current_ffmpeg.poll() is None

    def _graceful_stop_ffmpeg(self, process, wait_trailer: float = 10.0):
        """优雅关闭 ffmpeg 进程，**确保 trailer (moov) 写入完成**。

        关键问题：ffmpeg 用 -c copy 录实时流时，moov atom 默认在文件**末尾**。
        关闭时如果立即杀进程，moov 写一半 → "moov atom not found"。
        这里用 stdin 'q'（不是 signal）触发 ffmpeg 内部 flush + trailer write，
        等待 wait_trailer 秒让 ffmpeg 完整写完 mp4 trailer。

        修复：之前直接 send_signal(CTRL_BREAK_EVENT) 在部分 Windows 11 build 上
        会触发 PySide6 主进程收到 SIGINT 退出，导致 GUI "神秘消失"。
        现在改成 stdin 'q' 优先（ffmpeg 软关闭 + flush），失败再用 SIGINT 兜底。

        流程:
          1. stdin 'q' → ffmpeg 内部 flush（处理完所有 in-flight packet）
          2. 等 wait_trailer 秒（默认 10s，足够 4K HDR 也写完）
          3. 如果还没退出 → SIGINT（兜底，不影响主进程，因为 ffmpeg 走的是
             CREATE_NEW_PROCESS_GROUP 自己的 process group）
          4. 如果还没退出 → SIGTERM
          5. 如果还没退出 → SIGKILL（兜底）
        """
        if not process or process.poll() is not None:
            return
        import signal as _sig

        # Step 1: stdin 'q' 触发 ffmpeg 内部 flush（最安全，不动 signal）
        try:
            if process.stdin and not process.stdin.closed:
                process.stdin.write(b"q\n")
                process.stdin.flush()
            try:
                process.wait(timeout=wait_trailer)
                logging.debug(f"ffmpeg 收到 stdin q 后 {wait_trailer}s 内自然退出")
                return
            except subprocess.TimeoutExpired:
                pass
        except Exception as e:
            logging.debug(f"ffmpeg stdin q 关闭失败，降级到 signal: {e}")

        # Step 2: SIGINT 兜底（CREATE_NEW_PROCESS_GROUP 隔离，不会误伤主进程）
        try:
            process.send_signal(_sig.CTRL_BREAK_EVENT if platform.system() == "Windows" else _sig.SIGINT)
        except Exception:
            pass
        try:
            process.wait(timeout=wait_trailer)
            logging.debug(f"ffmpeg 收到 SIGINT 后 {wait_trailer}s 内自然退出")
            return
        except subprocess.TimeoutExpired:
            pass

        # Step 3: SIGTERM 兜底
        try:
            process.terminate()
            process.wait(timeout=5)
            return
        except Exception:
            pass

        # Step 4: SIGKILL 兜底（不应该到这里）
        try:
            process.kill()
            process.wait(timeout=2)
        except Exception:
            pass

    def stop_recording(self, reset_time=False):
        was_recording = self.is_recording
        path_to_convert = self.current_save_path if was_recording else None

        if self._split_timer:
            self._split_timer.cancel()
            self._split_timer = None

        if self.current_ffmpeg:
            self._graceful_stop_ffmpeg(self.current_ffmpeg)

        # 停止弹幕录制
        if self._danmaku_recorder:
            self._danmaku_recorder.stop()
            self._danmaku_recorder = None

        self.current_ffmpeg = None
        # 关键：记下"停止瞬间"的时间戳和 part 大小（如果还在写 part），让防抖到期
        # 发 FileClosed 时能算出"停止那一刻"的 duration/size，而不是防抖期内虚涨的值。
        if was_recording and self.current_save_path:
            self._last_part_stop_time = time.time()
            try:
                self._last_part_size_at_stop = os.path.getsize(self.current_save_path)
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

            if path_to_convert:
                _run_convert(path_to_convert)
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
        self.finished.emit()

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
        mode = (get_global_setting("proxy_mode") or "禁用").strip()
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
            proxy_url = (get_global_setting("proxy") or "").strip()
            if proxy_url:
                env = os.environ.copy()
                env["http_proxy"] = proxy_url
                env["https_proxy"] = proxy_url
                env["HTTP_PROXY"] = proxy_url
                env["HTTPS_PROXY"] = proxy_url
                return env
        return None

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
            # 关键：+empty_moov 让 ffmpeg 写一个**空** moov atom 在文件开头
            # （而不是末尾）。这样 `q\n` 关闭时即使 ffmpeg 立即退出，
            # moov 也已经在文件里（虽然不完整，但 mp4 容器**结构有效**）。
            # 关闭后用 -movflags +faststart 重写 moov 完成结构。
            "-movflags", "+empty_moov",
            save_path
        ]  # noqa
        proxy_env = self._get_proxy_env()
        # Windows 下要 CREATE_NEW_PROCESS_GROUP 才能收到 CTRL_BREAK_EVENT / SIGINT
        _popen_kwargs = dict(
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=proxy_env,
        )
        if platform.system() == "Windows":
            _popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        return subprocess.Popen(cmd, **_popen_kwargs)

    def trigger_cut(self):
        if not self.is_recording or not self.stream_url:
            return

        # 异步执行切割，避免阻塞主线程
        import threading
        cut_thread = threading.Thread(target=self._do_cut, daemon=True)
        cut_thread.start()

    def _do_cut(self):
        try:
            logging.info(f"✂️ {self.room_id} 正在切割...")
            now_dt = datetime.datetime.now()
            save_path = _build_save_path(self.room_id, self.uname, self.current_title, now_dt)

            new_ffmpeg = self._start_ffmpeg(save_path)
            time.sleep(2)

            if new_ffmpeg.poll() is not None:
                raise RuntimeError("ffmpeg 启动失败")

            if self.current_save_path and os.path.exists(self.current_save_path):
                self.accumulated_size += os.path.getsize(self.current_save_path)

            old_save_path = self.current_save_path
            old_ffmpeg, self.current_ffmpeg = self.current_ffmpeg, new_ffmpeg
            self.current_save_path = save_path

            if old_ffmpeg:
                self._graceful_stop_ffmpeg(old_ffmpeg)

            # 切割时同步重置弹幕文件
            # 关键：webhook 用**原文件**（不重编码）。mp4 moov 由 +empty_moov + SIGINT 等 10s
            # 写完整；B 站 bvc_check 接受 h264/avc 编码 mp4。
            _upload_path = old_save_path  # 默认用原文件路径
            if self._danmaku_recorder:
                fname_base = os.path.splitext(os.path.basename(save_path))[0]
                self._danmaku_recorder.reset(fname_base)

            logging.info(f"✅ {self.room_id} 切割完成: {os.path.basename(save_path)}")

            self._schedule_duration_split()
            self.cut_completed.emit(self.room_id, os.path.basename(save_path))
            # 计算被切割文件的大小和时长，给对端做上传/统计用
            # 注意：duration 必须是**当前 part**的时长，不是整个录制会话的时长
            _old_size = 0
            _old_duration = 0.0
            _old_part_open_time = self.current_part_start_time or self.record_start_time
            _file_close_time = datetime.datetime.now().astimezone().isoformat(timespec="microseconds")
            try:
                if _upload_path and os.path.exists(_upload_path):
                    _old_size = os.path.getsize(_upload_path)
                if _old_part_open_time:
                    _old_duration = float(max(0, time.time() - _old_part_open_time))
            except Exception:
                pass
            # 切换到新 part
            self.current_part_start_time = time.time()
            _send_webhooks("recording_split", self.room_id, self.uname, self.current_title,
                           {"file": _upload_path,
                            "size": _old_size,
                            "duration": _old_duration,
                            "file_open_time": (datetime.datetime.fromtimestamp(_old_part_open_time).astimezone().isoformat(timespec="microseconds")
                                               if _old_part_open_time else _file_close_time),
                            "file_close_time": _file_close_time,
                            "session_id": self.current_session_id})
        except Exception as e:
            logging.error(f"❌ {self.room_id} 切割失败: {e}")
            self.cut_failed.emit(self.room_id, str(e))

    def _check_record_conditions(self, info):
        title_cond = get_global_setting("condition_title") or ""
        if title_cond.strip():
            keywords = [k.strip() for k in title_cond.split(",") if k.strip()]
            if keywords and not any(k in info.get("title", "") for k in keywords):
                return False

        cat_cond = get_global_setting("condition_category") or ""
        if cat_cond.strip():
            keywords = [k.strip() for k in cat_cond.split(",") if k.strip()]
            area = info.get("area_name", "") + info.get("parent_area_name", "")
            if keywords and not any(k in area for k in keywords):
                return False


        # ---- Recording schedule: time-window check ----
        tz_name = (get_global_setting("schedule_timezone") or "UTC").strip()
        start_str = (get_global_setting("schedule_start") or "").strip()
        stop_str = (get_global_setting("schedule_stop") or "").strip()
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
        if not get_global_setting("stream_record_enabled"):
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

    def _run_loop(self):
        while self.is_running:
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

                    if get_global_setting("auto_switch_stream") and (now_time - self.last_auto_switch_check > 180):
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

                            if get_global_setting("split_on_title_change") and new_title != self.current_title:
                                logging.info(f"📝 {self.room_id} 标题改变，触发切割")
                                threading.Thread(target=self.trigger_cut, daemon=True).start()

                            if get_global_setting("split_on_category_change") and new_area != self.current_area:
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
                        if get_global_setting("split_on_codec_change"):
                            new_info = get_stream_info(self.room_id)
                            if new_info:
                                new_codec = new_info.get("codec", "")
                                if self._last_codec and new_codec and new_codec != self._last_codec:
                                    logging.info(f"🎞️ {self.room_id} 编码从 {self._last_codec} 变为 {new_codec}，触发切割")
                                    self._last_codec = new_codec
                                    threading.Thread(target=self.trigger_cut, daemon=True).start()

                        self.last_api_check_time = now_time

                # 检测流不连续：ffmpeg 意外退出。
                # 调 stop_recording(reset_time=False)：保留 current_save_path / current_session_id
                # 给后续防抖路径（或 API 翻回 1 重启路径）使用。
                if self.current_ffmpeg and self.current_ffmpeg.poll() is not None:
                    logging.warning(f"⚠️ {self.room_id} ffmpeg 进程已退出（可能 B 站流断开）")
                    # 关键：用 reset_time=False 保留 part 信息
                    self.stop_recording(reset_time=False)
                    if get_global_setting("split_on_stream_discontinuity"):
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
                time.sleep(_parse_monitor_seconds("monitor_interval", 20))
                continue

            if info["live_status"] == 1:
                # 主播在播 → 重置下播防抖（避免下播事件残留）
                self._live_first_seen = 0.0
                # 检查全局录制开关
                if get_global_setting("stream_record_enabled") is False:
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

                    now_dt = datetime.datetime.now()
                    save_path = _build_save_path(self.room_id, self.uname, self.current_title, now_dt)

                    logging.info(f"🎥 开始录制: {os.path.basename(save_path)}")
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
                        # 关键：本次是"防抖期内 API 翻回 1 重启 ffmpeg"或"split 切 part"路径。
                        # 重置 current_part_start_time 让新 part 的 duration 从 0 开始算；
                        # session_id 延续（biliupforjava 端同一会话续命）。
                        self.current_part_start_time = time.time()

                    # 启动弹幕录制
                    if get_global_setting("chat_record_enabled"):
                        fname_base = os.path.splitext(os.path.basename(save_path))[0]
                        sessdata = get_global_setting("chat_credential") or ""
                        chat_fmt = get_global_setting("chat_format") or "xml"
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
                    if get_global_setting("download_cover"):
                        _cover_url = info.get("cover", "")
                        _cover_dir = os.path.dirname(save_path)
                        _cover_base = os.path.splitext(os.path.basename(save_path))[0]
                        _download_cover(_cover_url, _cover_dir, _cover_base)
                    self.status_updated.emit("🟢 监控中", "📡 直播中", "🔴 录制中",
                                            self.current_title, "00:00:00", "0 B/s", "0 B",
                                            self.current_parent_area, self.current_area)
                    time.sleep(_parse_monitor_seconds("monitor_delay", 3))
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
                    _debounce_sec = _parse_monitor_seconds("monitor_debounce", 300)
                    now_ts = time.time()
                    if not self._live_first_seen:
                        # 首检：先关停录播，再开始防抖
                        self._live_first_seen = now_ts
                        logging.info(f"⏳ {self.room_id} 检测到主播下播，停止录制后等待 {int(_debounce_sec)}s 确认...")
                        if self.is_recording:
                            # reset_time=False：保留 current_save_path / current_session_id
                            # 给防抖到期发 FileClosed 用
                            self.stop_recording(reset_time=False)
                        time.sleep(_parse_monitor_seconds("monitor_interval", 20))
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
                        time.sleep(_parse_monitor_seconds("monitor_interval", 20))
                        continue
                    else:
                        # 还在防抖窗口内，等下一轮轮询
                        self.status_updated.emit("🟢 监控中", "🌙 未开播", f"⏳ 确认中({int((_debounce_sec - (now_ts - self._live_first_seen)))}s)",
                                                self.current_title, "", "", "",
                                                self.current_parent_area, self.current_area)
                        time.sleep(_parse_monitor_seconds("monitor_interval", 20))
                        continue
                # 兜底：current_save_path 为空 + API 下播（用户主动关停监控后会到这里，
                # 或主播下播前还没开始录）。直接清理状态，不发任何事件。
                if self.is_recording:
                    self.stop_recording(reset_time=True)
                self._live_first_seen = 0.0   # 下播，重置 debounce
                time.sleep(_parse_monitor_seconds("monitor_interval", 20))

        self.current_cover = self.room_info.get("cover", "")
        self.current_face = self.room_info.get("face", "")