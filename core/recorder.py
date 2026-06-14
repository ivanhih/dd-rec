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
    """Fire-and-forget POST to each configured webhook URL.

    Payload JSON keys: event, room_id, uname, title, time (UTC ISO-8601), plus any extra fields.
    """
    raw = (get_global_setting("webhooks") or "").strip()
    if not raw:
        return
    urls = [u.strip() for u in raw.splitlines() if u.strip()]
    if not urls:
        return

    payload = {
        "event": event,
        "room_id": room_id,
        "uname": uname,
        "title": title,
        "time": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if extra:
        payload.update(extra)

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
                    logging.debug(f"Webhook {url} -> {resp.status}")
            except Exception as e:
                logging.warning(f"Webhook 发送失败 {url}: {e}")

    threading.Thread(target=_fire, daemon=True).start()


def _run_convert(src_path: str):
    """录制结束后将源文件转换为目标格式，可选删除原文件。在后台线程中运行。"""
    if not get_global_setting("convert_enabled"):
        return
    if not src_path or not os.path.exists(src_path):
        return

    target_fmt = (get_global_setting("convert_format") or "mp4").strip()
    base, src_ext = os.path.splitext(src_path)
    # 源文件已经是目标格式则跳过
    if src_ext.lstrip(".").lower() == target_fmt.lower():
        return

    dst_path = f"{base}.{target_fmt}"
    cmd = [FFMPEG_CMD, "-y", "-i", src_path, "-c", "copy", dst_path]
    logging.info(f"🔄 开始转换: {os.path.basename(src_path)} -> {os.path.basename(dst_path)}")

    def _do():
        try:
            result = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
        self.last_check_time = 0
        self.last_total_size = 0
        self.accumulated_size = 0
        self.last_api_check_time = 0
        self._split_timer = None
        self.current_stream_base_url = None
        self._last_codec = None
        self._ffmpeg_exit_count = 0
        self._danmaku_recorder: Optional[DanmakuRecorder] = None

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
        self.current_save_path = None
        self.stream_url = None

        if reset_time:
            # 只有真正停止（主播下播）时才重置时间
            self.record_start_time = 0
            self.last_total_size = 0
            self.accumulated_size = 0

        if was_recording:
            logging.info(f"⏹️ 已停止录制: {self.room_id}")

            if path_to_convert:
                _run_convert(path_to_convert)
    def kill(self):
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
            save_path
        ]
        proxy_env = self._get_proxy_env()
        return subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=proxy_env
        )

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
            if old_save_path:
                _run_convert(old_save_path)
            if self._danmaku_recorder:
                fname_base = os.path.splitext(os.path.basename(save_path))[0]
                self._danmaku_recorder.reset(fname_base)

            logging.info(f"✅ {self.room_id} 切割完成: {os.path.basename(save_path)}")

            self._schedule_duration_split()
            self.cut_completed.emit(self.room_id, os.path.basename(save_path))
            _send_webhooks("recording_split", self.room_id, self.uname, self.current_title,
                           {"file": os.path.basename(save_path)})
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

        while self.is_running:
            now_time = time.time()

            if not self.is_monitoring:
                if self.is_recording:
                    self.stop_recording(reset_time=True)  # 关闭监控时重置录制时间
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

                # 检测流不连续：ffmpeg 意外退出但主播仍在直播
                if get_global_setting("split_on_stream_discontinuity"):
                    if self.current_ffmpeg and self.current_ffmpeg.poll() is not None:
                        logging.info(f"⚡ {self.room_id} ffmpeg 意外退出，检测到流不连续，触发重新录制")
                        self._ffmpeg_exit_count += 1
                        self.current_ffmpeg = None
                        # 不 stop_recording，直接让外层循环重新拉流并重启录制

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

            if not self._check_record_conditions(info):
                self.status_updated.emit("🟢 监控中", "📡 直播中", "⏸️ 条件过滤",
                                        self.current_title, "", "", "",
                                        self.current_parent_area, self.current_area)
                time.sleep(20)
                continue

            if info["live_status"] == 1:
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
                                   {"file": os.path.basename(save_path)})

                    self.status_updated.emit("🟢 监控中", "📡 直播中", "🔴 录制中",
                                            self.current_title, "00:00:00", "0 B/s", "0 B",
                                            self.current_parent_area, self.current_area)
                    time.sleep(3)
                else:
                    self.status_updated.emit("🟢 监控中", "📡 直播中", "❌ 出错了",
                                            self.current_title, "", "", "",
                                            self.current_parent_area, self.current_area)
                    time.sleep(5)
            else:
                self.status_updated.emit("🟢 监控中", "🌙 未开播", "⏳ 闲置中",
                                        self.current_title, "", "", "",
                                        self.current_parent_area, self.current_area)
                if self.is_recording:
                    _send_webhooks("recording_stopped", self.room_id, self.uname, self.current_title,
                                   {"file": os.path.basename(self.current_save_path) if self.current_save_path else ""})
                self.stop_recording(reset_time=True)
                time.sleep(20)
