"""关闭文件后处理：验证 → 无损 remux → 复验 → 原子发布。"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Callable, List, Optional

from core.ffmpeg_tools import FFMPEG_CMD, FFPROBE_CMD, hidden_subprocess_kwargs
from core.media_validation import AudioIntegrityReport, assess_aac_integrity

logger = logging.getLogger(__name__)

FATAL_RE = re.compile(
    r"Invalid NAL unit size|missing picture in access unit|"
    r"Error splitting the input into NAL units|"
    r"Invalid data found when processing input|"
    r"error while decoding|corrupt",
    re.I,
)


class ArtifactStatus(str, Enum):
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"


@dataclass
class ArtifactResult:
    status: ArtifactStatus
    source_path: str
    final_path: str = ""
    session_id: str = ""
    room_id: str = ""
    close_reason: str = ""
    duration: float = 0.0
    size: int = 0
    first_keyframe_time: Optional[float] = None
    warnings: List[str] = field(default_factory=list)
    error: str = ""
    health: str = ""
    probe: dict = field(default_factory=dict)
    continuity: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["status"] = self.status.value
        return d


def write_artifact_sidecar(result: ArtifactResult, path: str) -> None:
    if not path:
        return
    try:
        with open(path + ".artifact.json", "w", encoding="utf-8") as handle:
            json.dump(result.to_dict(), handle, ensure_ascii=False, indent=2)
    except Exception:
        logger.debug("failed to write artifact sidecar", exc_info=True)


def _safe_unlink(path: str) -> None:
    try:
        if path and os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def publish_staged_artifact(staged: str, final: str) -> tuple[bool, str]:
    try:
        os.makedirs(os.path.dirname(final) or ".", exist_ok=True)
        os.replace(staged, final)
        return True, ""
    except OSError as exc:
        return False, str(exc)


def make_staging_path(final: str) -> str:
    """Return a short sibling path so long recording titles do not grow again."""
    directory = os.path.dirname(final) or "."
    return os.path.join(directory, f".ddrec-{uuid.uuid4().hex[:12]}.validating.mp4")


def _run(cmd: list[str], timeout: int = 3600) -> tuple[int, str, str]:
    try:
        run_kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        run_kwargs.update(hidden_subprocess_kwargs())
        p = subprocess.run(cmd, **run_kwargs)
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", (e.stderr or "") + "\nTIMEOUT"
    except Exception as e:
        return 1, "", str(e)


def probe_file(path: str) -> dict:
    code, out, err = _run(
        [FFPROBE_CMD, "-v", "error", "-show_format", "-show_streams", "-of", "json", path],
        timeout=120,
    )
    if code != 0:
        return {"ok": False, "error": err.strip() or f"exit {code}"}
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": str(e)}
    streams = data.get("streams") or []
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    a = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    return {
        "ok": True,
        "format_name": fmt.get("format_name"),
        "duration": float(fmt.get("duration") or 0),
        "size": int(fmt.get("size") or 0),
        "has_video": v is not None,
        "has_audio": a is not None,
        "video_codec": (v or {}).get("codec_name"),
        "audio_codec": (a or {}).get("codec_name"),
        "video_start": float((v or {}).get("start_time") or 0),
        "audio_start": float((a or {}).get("start_time") or 0),
        "raw": data,
    }


def probe_audio_frames(path: str, sample_seconds: float = 10.0) -> tuple[list[dict], str]:
    """Read a bounded set of decoded audio-frame timestamps for clock validation."""
    code, out, err = _run(
        [
            FFPROBE_CMD,
            "-v",
            "error",
            "-read_intervals",
            f"%+{max(1.0, float(sample_seconds)):.3f}",
            "-select_streams",
            "a:0",
            "-show_frames",
            "-show_entries",
            "frame=pts_time,best_effort_timestamp_time,nb_samples",
            "-of",
            "json",
            path,
        ],
        timeout=120,
    )
    if code != 0:
        return [], err.strip() or f"exit {code}"
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        return [], str(exc)
    return list(data.get("frames") or []), ""


def inspect_audio_integrity(path: str, probe_result: dict) -> tuple[AudioIntegrityReport, str]:
    streams = (probe_result.get("raw") or {}).get("streams") or []
    audio = next((stream for stream in streams if stream.get("codec_type") == "audio"), {})
    if str(audio.get("codec_name") or "").lower() != "aac":
        return AudioIntegrityReport(), ""
    frames, error = probe_audio_frames(path)
    return assess_aac_integrity(audio, frames), error


def strict_decode(path: str) -> tuple[bool, str]:
    code, _o, err = _run(
        [
            FFMPEG_CMD,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
            "-i",
            path,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-f",
            "null",
            "-",
        ],
        timeout=7200,
    )
    if FATAL_RE.search(err):
        return False, err[-2000:]
    if code != 0:
        return False, err[-2000:] or f"exit {code}"
    return True, ""


def sampled_decode(path: str, duration: float = 0.0, sample_seconds: float = 3.0) -> tuple[bool, str]:
    """Decode short windows at the start and end instead of the full recording."""
    window = max(0.5, float(sample_seconds))
    starts = [0.0]
    if duration > window * 2:
        starts.append(max(0.0, duration - window))

    errors = []
    for start in starts:
        cmd = [
            FFMPEG_CMD,
            "-hide_banner",
            "-nostdin",
            "-v",
            "error",
            "-xerror",
        ]
        if start > 0:
            cmd.extend(["-ss", f"{start:.3f}"])
        cmd.extend(
            [
                "-i",
                path,
                "-t",
                f"{window:.3f}",
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-f",
                "null",
                "-",
            ]
        )
        code, _out, err = _run(cmd, timeout=300)
        if code != 0 or FATAL_RE.search(err):
            detail = err[-1500:] or f"exit {code}"
            errors.append(f"start={start:.3f}: {detail}")
    if errors:
        return False, "\n".join(errors)
    return True, ""


def remux_copy(src: str, dst: str) -> tuple[bool, str]:
    """无损转封装为 MP4（+faststart）。"""
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + f".{uuid.uuid4().hex[:8]}.processing.mp4"
    cmd = [
        FFMPEG_CMD,
        "-y",
        "-fflags",
        "+genpts",
        "-i",
        src,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c",
        "copy",
        "-avoid_negative_ts",
        "make_zero",
        "-movflags",
        "+faststart",
        tmp,
    ]
    code, _o, err = _run(cmd, timeout=7200)
    if code != 0 or FATAL_RE.search(err) or not os.path.exists(tmp):
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, err[-2000:] or f"remux exit {code}"
    # 原子发布
    try:
        if os.path.exists(dst):
            os.remove(dst)
        os.replace(tmp, dst)
    except OSError as e:
        return False, str(e)
    return True, ""


class MediaPipeline:
    """串行后处理队列。

    on_result 路由：
      - 不再使用单一共享 on_result（旧 recorder 销毁后新 recorder 覆盖会打到已删除对象）
      - 改为 {room_id: callback} 注册表，submit_closed_part 带 room_id，
        worker 完成后按 result.room_id 找回对应 recorder 的回调，查不到则跳过。
    """

    def __init__(
        self,
        *,
        on_result: Optional[Callable[[ArtifactResult], None]] = None,
        keep_source: bool = True,
        strict: bool = True,
    ):
        # 兼容旧签名：单回调模式（非单例路径仍可用）
        self.on_result = on_result or (lambda r: None)
        self.keep_source = keep_source
        self.strict = strict
        self._q: List[dict] = []
        self._cv = threading.Condition()
        self._stop = False
        # room_id -> callback 路由表（默认 pipeline 专用）
        self._result_handlers: dict = {}
        self._handlers_lock = threading.Lock()
        self._thread = threading.Thread(target=self._worker, name="media-pipeline", daemon=True)
        self._thread.start()

    def register_result_handler(
        self,
        room_id: str,
        callback: Callable,
        token: Optional[str] = None,
    ):
        """recorder 注册产物回调；token 用于防止旧实例竞态。"""
        with self._handlers_lock:
            self._result_handlers[str(room_id)] = (callback, token)

    def unregister_result_handler(self, room_id: str, token: Optional[str] = None):
        """recorder 销毁时注销。

        若传入 token，仅当仍是同一注册时才移除（避免误删新 recorder 的 handler）。
        """
        rid = str(room_id)
        with self._handlers_lock:
            entry = self._result_handlers.get(rid)
            if entry is None:
                return
            _cb, cur_token = entry if isinstance(entry, tuple) else (entry, None)
            if token is not None and cur_token is not None and cur_token != token:
                return
            self._result_handlers.pop(rid, None)

    def _dispatch_result(self, result: ArtifactResult):
        rid = str(result.room_id or "")
        with self._handlers_lock:
            entry = self._result_handlers.get(rid)
        if entry is None:
            # 旧 recorder 已销毁，job 结果无人认领，直接跳过
            logger.debug(f"pipeline: room_id={rid} 无注册回调，跳过产物结果")
            return
        if isinstance(entry, tuple):
            handler, token = entry
        else:
            # 兼容旧单回调注册
            handler, token = entry, None
        try:
            # 优先把 token 传给回调，便于回调侧二次校验
            handler(result, token)
        except TypeError:
            # 兼容只接收 result 的旧回调
            handler(result)

    def submit_closed_part(
        self,
        source_path: str,
        *,
        session_id: str = "",
        room_id: str = "",
        close_reason: str = "",
        health_hint: str = "",
        preferred_final: str = "",
    ):
        job = {
            "source_path": source_path,
            "session_id": session_id,
            "room_id": room_id,
            "close_reason": close_reason,
            "health_hint": health_hint,
            "preferred_final": preferred_final,
        }
        with self._cv:
            self._q.append(job)
            self._cv.notify()

    def shutdown(self, drain: bool = True, timeout: float = 120.0):
        with self._cv:
            self._stop = True
            self._cv.notify_all()
        if drain:
            self._thread.join(timeout=timeout)

    def _worker(self):
        while True:
            with self._cv:
                while not self._q and not self._stop:
                    self._cv.wait(timeout=1.0)
                if not self._q:
                    if self._stop:
                        return
                    continue
                job = self._q.pop(0)
            try:
                result = self._process(job)
            except Exception as e:
                logger.exception("media pipeline job failed")
                result = ArtifactResult(
                    status=ArtifactStatus.FAILED,
                    source_path=job.get("source_path", ""),
                    session_id=job.get("session_id", ""),
                    room_id=job.get("room_id", ""),
                    error=str(e),
                )
            try:
                self._dispatch_result(result)
            except Exception as e:
                logger.warning(f"on_result error: {e}")

    def _process(self, job: dict) -> ArtifactResult:
        src = job["source_path"]
        result = ArtifactResult(
            status=ArtifactStatus.FAILED,
            source_path=src,
            session_id=job.get("session_id", ""),
            room_id=job.get("room_id", ""),
            close_reason=job.get("close_reason", ""),
            health=job.get("health_hint", ""),
        )
        try:
            with open(src + ".media.json", "r", encoding="utf-8") as handle:
                media_manifest = json.load(handle)
            result.continuity = {key: media_manifest[key] for key in (
                "diagnostics_version", "wall_duration_ms", "media_progress_gap_ms", "health_intervals",
                "transport_gaps", "recovery_events", "continuity_log_path"
            ) if key in media_manifest}
            if media_manifest.get("health") in ("degraded", "failed"):
                result.health = media_manifest["health"]
        except (OSError, ValueError, TypeError):
            pass  # Legacy and non-native recordings do not have this manifest.
        if not src or not os.path.exists(src):
            result.error = "source_missing"
            return result

        # 等待文件大小稳定
        # submit_closed_part() only accepts files whose writer/process has
        # already closed. Waiting for three identical size polls added about
        # 1.2 seconds to every cut without increasing safety.

        pr = probe_file(src)
        result.probe = {k: v for k, v in pr.items() if k != "raw"}
        if not pr.get("ok"):
            result.error = f"probe_failed:{pr.get('error')}"
            result.health = "failed"
            return result
        if not pr.get("has_video"):
            result.error = "no_video"
            result.health = "failed"
            return result

        # 目标路径
        preferred = job.get("preferred_final") or ""
        if preferred:
            final = preferred
        else:
            base, _ext = os.path.splitext(src)
            # 去掉 .capture 后缀
            if base.endswith(".capture"):
                base = base[: -len(".capture")]
            final = base + ".mp4"

        if os.path.normcase(os.path.abspath(final)) == os.path.normcase(os.path.abspath(src)):
            final = src + ".deliver.mp4"

        staged = make_staging_path(final)
        ok, err = remux_copy(src, staged)
        if not ok:
            _safe_unlink(staged)
            result.error = f"remux_failed:{err[:500]}"
            result.status = ArtifactStatus.FAILED
            write_artifact_sidecar(result, final)
            return result

        # 复验
        pr2 = probe_file(staged)
        result.probe = {k: v for k, v in pr2.items() if k != "raw"}
        if not pr2.get("ok") or not pr2.get("has_video"):
            _safe_unlink(staged)
            result.error = "final_probe_failed"
            result.status = ArtifactStatus.FAILED
            write_artifact_sidecar(result, final)
            return result
        if not pr2.get("has_audio"):
            if pr.get("has_audio"):
                _safe_unlink(staged)
                result.error = "audio_track_lost_during_remux"
                result.status = ArtifactStatus.FAILED
                result.health = "failed"
                write_artifact_sidecar(result, final)
                return result
            result.warnings.append("final_no_audio")
            result.status = ArtifactStatus.WARNING
            result.duration = float(pr2.get("duration") or 0)
            result.size = int(pr2.get("size") or 0)
            published, publish_error = publish_staged_artifact(staged, final)
            if not published:
                _safe_unlink(staged)
                result.error = f"publish_failed:{publish_error}"
                result.status = ArtifactStatus.FAILED
                write_artifact_sidecar(result, final)
                return result
            result.final_path = final
            write_artifact_sidecar(result, final)
            # 无损优先策略：无音频不作为 success 交付
            return result

        audio_report, audio_probe_error = inspect_audio_integrity(staged, pr2)
        result.probe.update(
            {
                "audio_declared_sample_rate": audio_report.declared_sample_rate,
                "audio_observed_sample_rate": audio_report.observed_sample_rate,
                "audio_timing_frame_count": audio_report.frame_count,
                "audio_extradata_size": audio_report.extradata_size,
                "audio_profile": audio_report.profile,
                "audio_integrity_fatal_reasons": list(audio_report.fatal_reasons),
                "audio_integrity_warnings": list(audio_report.warnings),
            }
        )
        if audio_report.fatal_reasons:
            result.error = "audio_integrity_failed:" + ",".join(audio_report.fatal_reasons)
            result.status = ArtifactStatus.FAILED
            result.health = "failed"
            _safe_unlink(staged)
            write_artifact_sidecar(result, final)
            return result
        if self.strict and audio_probe_error:
            result.error = f"audio_timing_probe_failed:{audio_probe_error[:500]}"
            result.status = ArtifactStatus.FAILED
            result.health = "failed"
            _safe_unlink(staged)
            write_artifact_sidecar(result, final)
            return result
        if self.strict and audio_report.warnings:
            result.error = "audio_integrity_inconclusive:" + ",".join(audio_report.warnings)
            result.status = ArtifactStatus.FAILED
            result.health = "failed"
            _safe_unlink(staged)
            write_artifact_sidecar(result, final)
            return result
        if audio_probe_error:
            result.warnings.append(f"audio_timing_probe_warn:{audio_probe_error[:200]}")
        result.warnings.extend(f"audio_integrity_warn:{reason}" for reason in audio_report.warnings)

        ok2, err2 = sampled_decode(staged, duration=float(pr2.get("duration") or 0))
        if not ok2 and self.strict:
            _safe_unlink(staged)
            result.error = f"final_sample_decode_failed:{err2[:500]}"
            result.status = ArtifactStatus.FAILED
            write_artifact_sidecar(result, final)
            return result
        if not ok2:
            result.warnings.append(f"sample_decode_warn:{err2[:200]}")
            result.health = result.health or "degraded"

        result.status = ArtifactStatus.SUCCESS if not result.warnings else ArtifactStatus.WARNING
        result.final_path = final
        result.duration = float(pr2.get("duration") or 0)
        result.size = int(pr2.get("size") or 0)
        result.health = result.health or "healthy"

        published, publish_error = publish_staged_artifact(staged, final)
        if not published:
            _safe_unlink(staged)
            result.error = f"publish_failed:{publish_error}"
            result.status = ArtifactStatus.FAILED
            result.final_path = ""
            result.health = "failed"
            write_artifact_sidecar(result, final)
            return result

        if not self.keep_source and result.status == ArtifactStatus.SUCCESS:
            # 首期默认 keep；仅显式关闭时删除
            try:
                os.remove(src)
            except OSError as e:
                result.warnings.append(f"delete_source_failed:{e}")

        # 写结果 sidecar
        write_artifact_sidecar(result, final)
        return result

    @staticmethod
    def _wait_stable(path: str, rounds: int = 3, delay: float = 0.4):
        last = -1
        stable = 0
        for _ in range(20):
            try:
                sz = os.path.getsize(path)
            except OSError:
                return
            if sz == last:
                stable += 1
                if stable >= rounds:
                    return
            else:
                stable = 0
                last = sz
            time.sleep(delay)


# 进程级默认 pipeline（懒创建）
_default_pipeline: Optional[MediaPipeline] = None
_default_lock = threading.Lock()


def resolve_keep_source() -> bool:
    """解析是否保留源文件。

    优先 artifact_keep_source；若仅有遗留 convert_delete_source，则取反映射。
    """
    try:
        from core.config import get_global_setting, CONFIG

        gs = CONFIG.get("global_settings") or {}
        if "artifact_keep_source" in gs:
            return bool(gs.get("artifact_keep_source"))
        # 遗留：convert_delete_source=True 表示删源
        if "convert_delete_source" in gs:
            logging.info(
                "⚙️ 使用遗留 convert_delete_source 映射 artifact_keep_source="
                f"{not bool(gs.get('convert_delete_source'))}"
            )
            return not bool(gs.get("convert_delete_source"))
        return bool(get_global_setting("artifact_keep_source"))
    except Exception:
        return True


def get_default_pipeline(
    on_result: Optional[Callable[[ArtifactResult], None]] = None,
) -> MediaPipeline:
    """获取进程级默认 pipeline。

    注意：on_result 参数仅为兼容旧签名而保留，实际回调请通过
    pipeline.register_result_handler(room_id, cb) 注册，避免共享回调被覆盖。
    """
    global _default_pipeline
    with _default_lock:
        if _default_pipeline is None:
            keep = resolve_keep_source()
            strict = True
            try:
                from core.config import get_global_setting

                strict = bool(get_global_setting("artifact_strict_decode_validation"))
            except Exception:
                pass
            _default_pipeline = MediaPipeline(on_result=on_result, keep_source=keep, strict=strict)
        return _default_pipeline
