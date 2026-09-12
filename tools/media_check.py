#!/usr/bin/env python3
"""媒体健康检查：ffprobe + 严格解码 + 关键帧 + 损坏关键词。

用法:
  python tools/media_check.py INPUT.mp4
  python tools/media_check.py DIR --json-out report.json

退出码:
  0 = green / yellow (可探测)
  2 = red (致命损坏或缺流)
  1 = 工具/参数错误
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

# 项目内 ffmpeg 优先
_ROOT = Path(__file__).resolve().parents[1]
_LOCAL_FFMPEG = _ROOT / "ffmpeg" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
_LOCAL_FFPROBE = _ROOT / "ffmpeg" / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.media_validation import assess_aac_integrity

FATAL_RE = re.compile(
    r"Invalid NAL unit size|missing picture in access unit|"
    r"Error splitting the input into NAL units|"
    r"Invalid data found when processing input|"
    r"error while decoding|corrupt|Invalid .* packet",
    re.I,
)
WARN_RE = re.compile(
    r"co located POCs unavailable|illegal short term buffer state|"
    r"non monotonically increasing dts|DTS .* out of order|"
    r"Application provided invalid, non monotonically increasing",
    re.I,
)


def _tool(name: str, local: Path) -> str:
    if local.exists():
        return str(local)
    found = shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"找不到 {name}，请安装或放到 {_ROOT / 'ffmpeg'}")


def run_cmd(cmd: list[str], timeout: int = 3600) -> tuple[int, str, str]:
    try:
        p = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        return p.returncode, p.stdout or "", p.stderr or ""
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", (e.stderr or "") + "\nTIMEOUT"


def probe(path: str, ffprobe: str) -> dict[str, Any]:
    code, out, err = run_cmd(
        [
            ffprobe,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            path,
        ],
        timeout=120,
    )
    if code != 0:
        return {"ok": False, "error": err.strip() or f"ffprobe exit {code}", "raw": {}}
    try:
        data = json.loads(out)
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"json parse: {e}", "raw": {}}
    return {"ok": True, "error": "", "raw": data}


def first_keyframe_time(path: str, ffprobe: str) -> float | None:
    code, out, err = run_cmd(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-skip_frame",
            "nokey",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time,pts_time,key_frame",
            "-of",
            "csv=p=0",
            path,
        ],
        timeout=180,
    )
    if code != 0 or not out.strip():
        return None
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        for p in parts:
            if not p or p in ("1", "0"):
                continue
            try:
                return float(p)
            except ValueError:
                continue
    return None


def audio_timing_frames(path: str, ffprobe: str, sample_seconds: float = 10.0) -> tuple[list[dict], str]:
    code, out, err = run_cmd(
        [
            ffprobe,
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
        return [], err.strip() or f"ffprobe exit {code}"
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        return [], str(exc)
    return list(data.get("frames") or []), ""


def strict_decode(path: str, ffmpeg: str, stream: str) -> dict[str, Any]:
    """stream: 'v' | 'a' | 'both'"""
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-v", "error", "-xerror", "-i", path]
    if stream == "v":
        cmd += ["-map", "0:v:0", "-f", "null", "-"]
    elif stream == "a":
        cmd += ["-map", "0:a:0", "-f", "null", "-"]
    else:
        cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-"]
    code, _out, err = run_cmd(cmd, timeout=7200)
    fatal = bool(FATAL_RE.search(err))
    warn = bool(WARN_RE.search(err))
    return {
        "exit_code": code,
        "fatal_match": fatal,
        "warn_match": warn,
        "stderr_tail": "\n".join(err.splitlines()[-40:]),
    }


def classify(path: str, ffmpeg: str, ffprobe: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": path,
        "basename": os.path.basename(path),
        "size": os.path.getsize(path) if os.path.exists(path) else 0,
        "status": "red",
        "reasons": [],
        "probe": {},
        "video": {},
        "audio": {},
        "first_keyframe_time": None,
        "decode": {},
    }
    if not os.path.exists(path):
        result["reasons"].append("file_missing")
        return result

    pr = probe(path, ffprobe)
    result["probe"] = {
        "ok": pr["ok"],
        "error": pr.get("error", ""),
        "format_name": (pr.get("raw") or {}).get("format", {}).get("format_name"),
        "duration": (pr.get("raw") or {}).get("format", {}).get("duration"),
        "bit_rate": (pr.get("raw") or {}).get("format", {}).get("bit_rate"),
    }
    if not pr["ok"]:
        result["reasons"].append(f"probe_failed:{pr.get('error')}")
        return result

    streams = (pr.get("raw") or {}).get("streams") or []
    vstreams = [s for s in streams if s.get("codec_type") == "video"]
    astreams = [s for s in streams if s.get("codec_type") == "audio"]
    audio_integrity_warning = False
    if not vstreams:
        result["reasons"].append("no_video_stream")
    if not astreams:
        result["reasons"].append("no_audio_stream")

    if vstreams:
        v = vstreams[0]
        result["video"] = {
            "codec": v.get("codec_name"),
            "profile": v.get("profile"),
            "width": v.get("width"),
            "height": v.get("height"),
            "time_base": v.get("time_base"),
            "start_time": v.get("start_time"),
            "avg_frame_rate": v.get("avg_frame_rate"),
            "extradata_size": v.get("extradata_size"),
        }
    if astreams:
        a = astreams[0]
        result["audio"] = {
            "codec": a.get("codec_name"),
            "profile": a.get("profile"),
            "sample_rate": a.get("sample_rate"),
            "channels": a.get("channels"),
            "channel_layout": a.get("channel_layout"),
            "time_base": a.get("time_base"),
            "start_time": a.get("start_time"),
            "extradata_size": a.get("extradata_size"),
        }
        if str(a.get("codec_name") or "").lower() == "aac":
            frames, timing_error = audio_timing_frames(path, ffprobe)
        else:
            frames, timing_error = [], ""
        audio_report = assess_aac_integrity(a, frames)
        result["audio"].update(
            {
                "observed_sample_rate": audio_report.observed_sample_rate,
                "timing_frame_count": audio_report.frame_count,
                "integrity_fatal_reasons": list(audio_report.fatal_reasons),
                "integrity_warnings": list(audio_report.warnings),
            }
        )
        if audio_report.fatal_reasons:
            result["reasons"].extend(audio_report.fatal_reasons)
            result["status"] = "red"
            return result
        if timing_error:
            result["reasons"].append(f"audio_timing_probe_warn:{timing_error[:200]}")
            audio_integrity_warning = True
        if audio_report.warnings:
            result["reasons"].extend(audio_report.warnings)
            audio_integrity_warning = True

    result["first_keyframe_time"] = first_keyframe_time(path, ffprobe)

    dec_v = strict_decode(path, ffmpeg, "v") if vstreams else {"exit_code": -1, "fatal_match": True}
    dec_a = strict_decode(path, ffmpeg, "a") if astreams else {"exit_code": -1, "fatal_match": True}
    result["decode"] = {"video": dec_v, "audio": dec_a}

    if not vstreams or not astreams:
        result["status"] = "red"
        return result

    if dec_v.get("fatal_match") or dec_v.get("exit_code", 0) != 0:
        result["reasons"].append("video_decode_fatal")
        result["status"] = "red"
        return result
    if dec_a.get("fatal_match") or dec_a.get("exit_code", 0) != 0:
        result["reasons"].append("audio_decode_fatal")
        result["status"] = "red"
        return result

    if dec_v.get("warn_match") or dec_a.get("warn_match"):
        result["reasons"].append("timestamp_or_ref_warning")
        result["status"] = "yellow"
        return result

    if audio_integrity_warning:
        result["status"] = "yellow"
        return result

    fk = result["first_keyframe_time"]
    if fk is not None and fk > 1.0:
        result["reasons"].append(f"late_first_keyframe:{fk:.3f}s")
        result["status"] = "yellow"
        return result

    result["status"] = "green"
    result["reasons"].append("ok")
    return result


def iter_media(path: Path) -> list[Path]:
    exts = {".mp4", ".flv", ".mkv", ".ts", ".m4v", ".mov"}
    if path.is_file():
        return [path]
    files = []
    for p in sorted(path.rglob("*")):
        if p.is_file() and p.suffix.lower() in exts:
            files.append(p)
    return files


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="录播媒体健康检查")
    ap.add_argument("path", help="文件或目录")
    ap.add_argument("--json-out", default="", help="写出 JSON 报告路径")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    try:
        ffmpeg = _tool("ffmpeg", _LOCAL_FFMPEG)
        ffprobe = _tool("ffprobe", _LOCAL_FFPROBE)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1

    target = Path(args.path)
    if not target.exists():
        print(f"路径不存在: {target}", file=sys.stderr)
        return 1

    reports = []
    worst = "green"
    rank = {"green": 0, "yellow": 1, "red": 2}
    for f in iter_media(target):
        r = classify(str(f), ffmpeg, ffprobe)
        reports.append(r)
        if rank[r["status"]] > rank[worst]:
            worst = r["status"]
        if not args.quiet:
            print(
                f"[{r['status'].upper():6}] {r['basename']}"
                f"  reasons={','.join(r['reasons'])}"
                f"  v={r.get('video', {}).get('codec')}"
                f"  a={r.get('audio', {}).get('codec')}"
                f"  key@{r.get('first_keyframe_time')}"
            )

    if args.json_out:
        outp = Path(args.json_out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(reports, ensure_ascii=False, indent=2), encoding="utf-8")
        if not args.quiet:
            print(f"wrote {outp}")

    return 2 if worst == "red" else 0


if __name__ == "__main__":
    sys.exit(main())
