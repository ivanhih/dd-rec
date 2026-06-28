"""
ffmpeg 剪切执行器 —— 把 Clip 列表从原 MP4 里切出来,落到『录播所在目录/高能剪切/』。

剪切的两种模式:
1. -c copy 优先:不重编码,秒级完成;但要求 keyframe 落在 start 附近(否则会有
   几百 ms 的偏移)。ffmpeg 的 -ss 在 -i 之前是『fast seek』(跳到最近 keyframe),
   偏移对片段剪切一般可接受。
2. 失败回退:-c:v libx264 -crf <Q> 重编码;按需触发(用户设置里可关)。

任务调度:串行执行(避免多 ffmpeg 抢磁盘),每完成一个 clip 通过 Signal 上报。
"""
from __future__ import annotations
import os
import re
import logging
import subprocess
from dataclasses import dataclass
from typing import List

from PySide6.QtCore import QThread, Signal

from .analyzer import Clip

logger = logging.getLogger(__name__)

OUTPUT_SUBDIR = "高能剪切"
# 默认输出命名模板: <原 stem>__片段<idx>_<HH-MM-SS>_<HH-MM-SS>.<ext>
DEFAULT_NAME_TEMPLATE = "{stem}__片段{idx}_{start}_{end}.{ext}"


@dataclass
class CutConfig:
    ffmpeg_cmd: str = "ffmpeg"
    output_dir: str = ""           # 空字符串 = 默认(原 MP4 所在目录/高能剪切)
    name_template: str = DEFAULT_NAME_TEMPLATE
    use_copy_first: bool = True    # -c copy 失败时是否降级到 libx264
    crf: int = 23                  # 18-28,数字越大质量越低
    preset: str = "veryfast"       # libx264 preset


@dataclass
class CutTask:
    """一个待剪切的片段 + 其输出路径。"""
    clip: Clip
    src_mp4: str
    output_path: str
    mode: str = "copy"      # "copy" | "reencode"


# ---------------- 单片段剪切 ----------------
def cut_one(task: CutTask, cfg: CutConfig) -> tuple:
    """同步切一个片段。返回 (ok: bool, error_msg: str, used_mode: str)。"""
    out = task.output_path
    os.makedirs(os.path.dirname(out), exist_ok=True)

    duration = max(0.1, task.clip.duration)
    start = max(0.0, task.clip.start)

    # ---- 1) 尝试 -c copy ----
    if cfg.use_copy_first:
        cmd_copy = [
            cfg.ffmpeg_cmd, "-y",
            "-ss", f"{start:.3f}",
            "-i", task.src_mp4,
            "-t", f"{duration:.3f}",
            "-c", "copy",
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            out,
        ]
        ok, err = _run(cmd_copy)
        if ok:
            return True, "", "copy"
        logger.info(f"-c copy 失败,尝试 libx264 回退: {err[:120]}")

    # ---- 2) 回退:libx264 ----
    cmd_re = [
        cfg.ffmpeg_cmd, "-y",
        "-ss", f"{start:.3f}",
        "-i", task.src_mp4,
        "-t", f"{duration:.3f}",
        "-c:v", "libx264",
        "-preset", cfg.preset,
        "-crf", str(cfg.crf),
        "-c:a", "aac",
        "-b:a", "128k",
        "-movflags", "+faststart",
        "-avoid_negative_ts", "make_zero",
        out,
    ]
    ok, err = _run(cmd_re)
    if ok:
        return True, "", "reencode"
    return False, err, "reencode"


def _run(cmd: list) -> tuple:
    """同步跑一次 ffmpeg,返回 (ok, stderr_or_msg)。"""
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) or 0,
        )
        if proc.returncode == 0 and os.path.exists(cmd[-1]) and os.path.getsize(cmd[-1]) > 0:
            return True, ""
        err = (proc.stderr or b"").decode("utf-8", errors="replace")[-500:]
        return False, err or f"ffmpeg 返回码 {proc.returncode}"
    except FileNotFoundError as e:
        return False, f"ffmpeg 不存在: {e}"
    except Exception as e:
        return False, f"ffmpeg 调用异常: {e}"


# ---------------- 命名 + 路径 ----------------
_INVALID_FN_RE = re.compile(r'[\\/:*?"<>|]')


def _sanitize_filename(name: str) -> str:
    return _INVALID_FN_RE.sub("_", name)[:80]


def _fmt_time(seconds: float) -> str:
    s = max(0, int(seconds))
    h, rem = divmod(s, 3600)
    m, ss = divmod(rem, 60)
    return f"{h:02d}-{m:02d}-{ss:02d}"


def build_output_path(src_mp4: str, clip: Clip, idx: int, cfg: CutConfig) -> str:
    """按命名模板 + 输出目录生成单片段输出路径。"""
    out_dir = cfg.output_dir.strip() or os.path.join(os.path.dirname(src_mp4), OUTPUT_SUBDIR)
    os.makedirs(out_dir, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src_mp4))[0]
    stem = _sanitize_filename(stem)
    ext = "mp4"
    name = cfg.name_template.format(
        stem=stem,
        idx=idx,
        start=_fmt_time(clip.start),
        end=_fmt_time(clip.end),
        ext=ext,
    )
    name = _sanitize_filename(name)
    return os.path.join(out_dir, name)


# ---------------- QThread 串行执行 ----------------
class CutBatchWorker(QThread):
    """按顺序切多个片段,串行避免 ffmpeg 抢磁盘。"""
    started_clip = Signal(int, int)            # (index, total)
    clip_done = Signal(int, int, str, bool)    # (index, total, output_path, ok)
    finished = Signal(int, int)                # (success_count, total)
    failed = Signal(str)

    def __init__(self, tasks: List[CutTask], cfg: CutConfig, parent=None):
        super().__init__(parent)
        self._tasks = tasks
        self._cfg = cfg
        self._cancel = False

    def cancel(self) -> None:
        self._cancel = True

    def run(self) -> None:
        total = len(self._tasks)
        success = 0
        try:
            for i, task in enumerate(self._tasks, start=1):
                if self._cancel:
                    break
                self.started_clip.emit(i, total)
                ok, err, mode = cut_one(task, self._cfg)
                if ok:
                    success += 1
                self.clip_done.emit(i, total, task.output_path, ok)
                if not ok:
                    logger.error(f"剪切片段 {i}/{total} 失败: {err[:200]}")
            self.finished.emit(success, total)
        except Exception as e:
            logger.exception("批量剪切失败")
            self.failed.emit(str(e))
