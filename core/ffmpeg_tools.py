"""项目内 ffmpeg/ffprobe 路径解析。"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys

from core.config import RESOURCE_DIR


def _project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_ffmpeg_path() -> str:
    """获取 ffmpeg 可执行路径。"""
    name = "ffmpeg.exe" if platform.system() == "Windows" else "ffmpeg"
    candidates = []

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        parent_dir = os.path.dirname(exe_dir)
        candidates.extend(
            [
                os.path.join(parent_dir, "ffmpeg", name),
                os.path.join(RESOURCE_DIR, "ffmpeg", name),
                os.path.join(exe_dir, "ffmpeg", name),
            ]
        )
    else:
        root = _project_root()
        candidates.append(os.path.join(root, "ffmpeg", name))

    for p in candidates:
        if os.path.exists(p):
            return p

    found = shutil.which("ffmpeg")
    return found or "ffmpeg"


def get_ffprobe_path() -> str:
    """获取 ffprobe 可执行路径。"""
    name = "ffprobe.exe" if platform.system() == "Windows" else "ffprobe"
    ffmpeg = get_ffmpeg_path()
    # 与 ffmpeg 同目录
    sibling = os.path.join(os.path.dirname(ffmpeg), name)
    if os.path.exists(sibling):
        return sibling

    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        parent_dir = os.path.dirname(exe_dir)
        for p in (
            os.path.join(parent_dir, "ffmpeg", name),
            os.path.join(RESOURCE_DIR, "ffmpeg", name),
            os.path.join(exe_dir, "ffmpeg", name),
        ):
            if os.path.exists(p):
                return p
    else:
        root = _project_root()
        p = os.path.join(root, "ffmpeg", name)
        if os.path.exists(p):
            return p

    found = shutil.which("ffprobe")
    return found or "ffprobe"


def hidden_subprocess_kwargs(*, new_process_group: bool = False) -> dict:
    """Return subprocess options that never create a visible Windows console."""
    if platform.system() != "Windows":
        return {}

    flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if new_process_group:
        flags |= int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    kwargs = {"creationflags": flags}

    startupinfo_type = getattr(subprocess, "STARTUPINFO", None)
    if startupinfo_type is not None:
        startupinfo = startupinfo_type()
        startupinfo.dwFlags |= int(getattr(subprocess, "STARTF_USESHOWWINDOW", 0))
        startupinfo.wShowWindow = int(getattr(subprocess, "SW_HIDE", 0))
        kwargs["startupinfo"] = startupinfo
    return kwargs


FFMPEG_CMD = get_ffmpeg_path()
FFPROBE_CMD = get_ffprobe_path()
